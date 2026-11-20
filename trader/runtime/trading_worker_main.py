"""Execution-only process. PostgreSQL is the sole Control API coordination channel."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import time
from datetime import datetime, timezone
from decimal import Decimal

from trader.adapters.broker.binance_spot_demo_broker import (
    BinanceSpotDemoBroker,
    BinanceSpotDemoBrokerConfig,
)
from trader.adapters.broker.spot_coverage import BinanceSpotCoverageProver
from trader.adapters.broker.spot_execution_adapter import (
    BinanceSpotExchangePort,
    BinanceSpotSnapshotProvider,
)
from trader.ops.dr_evidence import dr_gate_from_env
from trader.runtime.credentials import CredentialBoundaryError, require_execution_credentials
from trader.services.alerting import alert_monitor_from_env
from trader.trading.models import NotReady
from trader.trading.pg import create_pool
from trader.trading.postgres import PostgresTradingStore
from trader.trading.runtime import TradingRuntime

logger = logging.getLogger(__name__)


class VenueClock:
    """Decision clock anchored to a venue sample and monotonic elapsed time.

    A venue bar is visible only after the *venue* has closed it. A fixed offset
    from local wall time is not sufficient: a subsequent wall-clock step would
    advance the decision clock even though the venue did not. This clock stores
    the sampled venue timestamp and advances only by a separately injectable
    monotonic elapsed-time source.

    ``local_clock`` is accepted only for backward-compatible construction; it is
    never used to advance the decision clock. ``sync`` samples the venue clock
    and anchors elapsed time after the response, so the decision clock starts at
    (or behind) the sampled venue time and cannot be moved by a local wall-clock
    jump.
    """

    def __init__(self, server_time, local_clock=None, elapsed_clock=None):
        self._server_time = server_time
        # Kept for call compatibility; wall time must never drive visibility.
        self._local_clock = local_clock
        self._elapsed_clock = elapsed_clock or (lambda: time.monotonic_ns() // 1_000_000)
        self._venue_anchor_ms = None
        self._elapsed_anchor_ms = None

    @property
    def synced(self) -> bool:
        return self._venue_anchor_ms is not None

    async def sync(self) -> int:
        elapsed_before = self._elapsed_clock()
        venue_now = int(await self._server_time())
        elapsed_after = self._elapsed_clock()
        if elapsed_after < elapsed_before:
            # A monotonic source stepping backwards is not a safe domain.
            raise NotReady("VENUE_CLOCK_UNSTABLE")
        self._venue_anchor_ms = venue_now
        self._elapsed_anchor_ms = elapsed_after
        return self()

    def __call__(self) -> int:
        if self._venue_anchor_ms is None:
            raise NotReady("VENUE_CLOCK_NOT_SYNCED")
        elapsed = self._elapsed_clock()
        if elapsed < self._elapsed_anchor_ms:
            raise NotReady("VENUE_CLOCK_UNSTABLE")
        return self._venue_anchor_ms + (elapsed - self._elapsed_anchor_ms)


def interval_ms(interval: str) -> int:
    """Fixed duration of a supported strategy interval."""
    from trader.trading.datasets import INTERVAL_MS

    value = INTERVAL_MS.get(str(interval or "").strip())
    if value is None:
        raise ValueError("UNSUPPORTED_MARKET_INTERVAL:" + str(interval))
    return int(value)


def _kline_observation(symbol: str, row, interval: str):
    """Convert one Binance kline row into a BAR_V1 closed-bar observation.

    Binance reports ``closeTime`` as the inclusive last millisecond of the bar
    (``openTime + interval - 1``); BAR_V1 defines the half-open interval and
    ``close_time_ms = open_time_ms + interval_ms``. Normalisation happens here,
    at the adapter boundary, without changing the frozen bar contract.
    """
    from trader.core.application.strategy_protocol import MarketData, MarketDataType

    duration = interval_ms(interval)
    open_time = int(row[0])
    venue_close_time = int(row[6])
    close_time = open_time + duration
    if venue_close_time not in (close_time - 1, close_time):
        raise ValueError(
            "KLINE_TIMESTAMP_CONTRACT_MISMATCH:"
            f"{symbol}:open={open_time}:venue_close={venue_close_time}:expected={close_time}"
        )
    close_price = Decimal(str(row[4]))
    bar_id = f"bar:{symbol}:{interval}:{open_time}"
    return MarketData(
        symbol=symbol,
        data_type=MarketDataType.KLINE,
        price=close_price,
        volume=Decimal(str(row[5])),
        timestamp=datetime.fromtimestamp(close_time / 1000, tz=timezone.utc),
        available_at_ms=close_time,
        kline_open=Decimal(str(row[1])),
        kline_high=Decimal(str(row[2])),
        kline_low=Decimal(str(row[3])),
        kline_close=close_price,
        kline_interval=interval,
        metadata={
            "event_id": bar_id,
            "bar_id": bar_id,
            "open_time_ms": open_time,
            "close_time_ms": close_time,
            "available_at_ms": close_time,
            "interval": interval,
            "complete": True,
            "is_closed": True,
            "source": "binance_kline",
        },
    )


def build_runtime(
    *,
    store,
    exchange,
    account_id,
    snapshot_provider,
    clock,
    private_session_factory=None,
    strategy_plugin=None,
    instruments=None,
    deployment_id="",
    strategy_id="",
    strategy_warmup_events=0,
    warmup_source=None,
    **options,
):
    """Dependency-injected production assembly; tests supply fake venue and stream ports."""
    runtime = TradingRuntime(
        store=store,
        exchange=exchange,
        account_id=account_id,
        snapshot_provider=snapshot_provider,
        clock=clock,
        private_session_factory=private_session_factory,
        require_private_session=True,
        **options,
    )
    if strategy_plugin is not None:
        from trader.trading.strategy import UnifiedStrategyRunner

        if not deployment_id or not instruments:
            raise ValueError("Strategy deployment_id and explicit instruments are required")
        runtime.strategy_runner = UnifiedStrategyRunner(
            runtime,
            deployment_id=deployment_id,
            plugin=strategy_plugin,
            instruments=instruments,
            strategy_id=strategy_id,
            warmup_events=strategy_warmup_events,
            warmup_source=warmup_source,
            on_block=runtime.block,
        )
    return runtime


def require_instruments_within_symbols(instruments, symbols) -> None:
    """Coverage proof only scans configured symbols; trading outside them is unsafe."""
    missing = sorted(
        {str(symbol).upper() for symbol in instruments} - {str(s).upper() for s in symbols}
    )
    if missing:
        raise ValueError("STRATEGY_INSTRUMENTS_NOT_IN_QTS_SYMBOLS:" + ",".join(missing))


async def enforce_strategy_warmup(runtime) -> bool:
    """Rebuild indicator state and block the worker when the warm-up fails.

    Plugin indicator state is never persisted, so every process start warms up
    again. A warm-up fault is an assembly fault: the worker must report it
    instead of pretending to be ready.
    """
    runner = runtime.strategy_runner
    if runner is None:
        return True
    try:
        await runner.prepare_warmup()
    except NotReady as exc:
        logger.warning("Strategy warm-up deferred: %s", exc)
        return False
    if runner.warmup_state.value == "BLOCKED":
        await runtime.block(runner.warmup_reason or "STRATEGY_WARMUP_BLOCKED")
        return False
    logger.info(
        "Strategy warm-up state=%s reason=%s remaining=%s",
        runner.warmup_state.value,
        runner.warmup_reason,
        runner.warmup_remaining,
    )
    return True


async def feed_market_once(runtime, broker, instruments, *, interval="1m", limit=2):
    """Feed completed bars only; a ticker snapshot is never a decision input.

    The venue kline endpoint is the sole market-data source of the new-stack
    execution path. A row whose availability moment is still in the future is
    skipped before it can reach the strategy runner.
    """
    if not runtime.strategy_start_allowed or runtime.strategy_runner is None:
        return
    state = getattr(runtime.strategy_runner.warmup_state, "value", "")
    if state == "BLOCKED":
        # A blocked warm-up must not keep poking the plugin.
        return
    get_klines = getattr(broker, "get_klines", None)
    if get_klines is None:
        # Refuse to fall back to a REST ticker snapshot: there is no bar
        # identity or availability moment to audit.
        raise RuntimeError("CLOSED_BAR_SOURCE_REQUIRED")
    now = runtime.clock()
    for symbol in sorted(instruments):
        rows = await get_klines(symbol, interval=interval, limit=max(int(limit), 2))
        for row in sorted(rows or [], key=lambda item: int(item[0])):
            observation = _kline_observation(symbol, row, interval)
            if observation.available_at_ms > now:
                # The bar is still forming; unfinished data is invisible.
                continue
            await runtime.on_market_data(observation, event_id=observation.metadata["event_id"])


async def _market_loop(runtime, broker, instruments, interval, poll_seconds):
    while True:
        try:
            clock = runtime.clock
            sync = getattr(clock, "sync", None)
            if callable(sync):
                # Recalibrate before every feed; a failed venue time source
                # blocks new risk instead of falling back to local wall time.
                await sync()
            runner = runtime.strategy_runner
            if runner is not None and not runner.warm:
                state = getattr(runner.warmup_state, "value", "")
                if state != "BLOCKED":
                    # A delayed start (lease/recovery) must still replay the
                    # recollected window before live bars may trade.
                    await enforce_strategy_warmup(runtime)
            await feed_market_once(runtime, broker, instruments, interval=interval)
        except NotReady as exc:
            # A readiness fence (recovery/alignment/lease) is a normal,
            # bounded transition: new risk is already closed by the runtime
            # and will reopen after the fence. Converting it into a durable
            # account block would turn every periodic alignment into an outage.
            logger.info("Market input paused by readiness fence: %s", exc)
        except Exception:
            logger.exception("Strategy or market input failed; blocking new risk")
            await runtime.block("STRATEGY_OR_MARKET_DATA_FAILED")
        await asyncio.sleep(poll_seconds)


async def run():
    credentials = require_execution_credentials()
    api_key = credentials.api_key
    secret = credentials.secret_key
    pool = await create_pool()
    broker = BinanceSpotDemoBroker(
        BinanceSpotDemoBrokerConfig.for_env(
            api_key,
            secret,
            env=credentials.environment,
            proxy_url=os.getenv("BINANCE_PROXY_URL") or None,
        )
    )
    runtime = None
    market_task = None
    alert_monitor = None
    alert_task = None
    health_task = None
    try:
        await broker.connect()
        account_id = os.getenv("QTS_ACCOUNT_ID", "binance_spot")
        # Every venue-time comparison (bar visibility, marks, reconciliation)
        # uses the venue-calibrated clock. A local clock ahead of the venue
        # must never make a forming bar visible.
        clock = VenueClock(broker.get_server_time)
        await clock.sync()
        reconcile_lookback_ms = int(os.getenv("QTS_RECONCILE_LOOKBACK_MS", "86400000"))
        coverage_lookback_ms = int(os.getenv("QTS_COVERAGE_LOOKBACK_MS", "86400000"))
        if coverage_lookback_ms < reconcile_lookback_ms:
            # The proven window must cover the snapshot lookback; a shorter
            # coverage window cannot prove the executions the snapshot applies.
            raise ValueError("COVERAGE_LOOKBACK_SHORTER_THAN_RECONCILE")
        provider = BinanceSpotSnapshotProvider(
            broker,
            account_id=account_id,
            symbols=os.getenv("QTS_SYMBOLS", "BTCUSDT").split(","),
            # Snapshot timestamps must live in the venue clock domain: the
            # coverage proof compares them with the stream connection time.
            clock=broker.get_server_time,
            lookback_ms=reconcile_lookback_ms,
        )
        coverage = BinanceSpotCoverageProver(
            rest=provider,
            symbols=provider.symbols,
            exchange_clock=broker.get_server_time,
            lookback_ms=coverage_lookback_ms,
            settle_ms=int(os.getenv("QTS_COVERAGE_SETTLE_MS", "250")),
        )

        from trader.adapters.binance.private_stream import (
            BinanceCredentials,
            PrivateStreamConfig,
            PrivateStreamManager,
        )
        from trader.api.env_config import get_binance_env_config
        from trader.trading.private_session import PrivateTradingSession

        endpoints = get_binance_env_config()

        def stream_factory():
            return PrivateStreamManager(
                BinanceCredentials(api_key, secret, testnet=True),
                config=PrivateStreamConfig(
                    base_url=endpoints["private_ws_base"],
                    rest_url=endpoints["rest_base"],
                    proxy_url=os.getenv("BINANCE_PROXY_URL") or None,
                    # Demo/testnet legacy listenKey is 410 Gone; use the
                    # official ws-api signature subscription explicitly.
                    mode=os.getenv("QTS_PRIVATE_STREAM_MODE", "auto"),
                ),
            )

        def session_factory(rt):
            # Coverage is proven from a complete REST backfill (pagination,
            # trade-to-order mapping, repeated passes). There is no bypass flag:
            # an unproven window keeps the session blocked with a precise reason.
            return PrivateTradingSession(
                service=rt.service,
                account_id=account_id,
                stream_factory=stream_factory,
                snapshot_provider=provider.build,
                coverage_provider=coverage,
                exchange_clock=broker.get_server_time,
                on_block=rt.block,
                on_ready=rt.mark_stream_ready,
            )

        async def reconnect():
            await broker.disconnect()
            await broker.connect()

        instruments = {
            symbol: tuple(assets)
            for symbol, assets in json.loads(os.getenv("QTS_INSTRUMENTS", "{}")).items()
        }
        require_instruments_within_symbols(instruments, provider.symbols)

        warmup_interval = os.getenv("QTS_STRATEGY_WARMUP_INTERVAL", "1m")

        async def warmup_source():
            """Re-collect closed klines so the plugin rebuilds indicator state.

            Plugin indicator state is never persisted: a restart always replays
            a fresh window from the venue. Unfinished bars are dropped.
            """
            runner = runtime.strategy_runner
            required = max(1, runner.warmup_events)
            observations = []
            for symbol in instruments:
                rows = await broker.get_klines(symbol, interval=warmup_interval, limit=required + 2)
                now_ms = await broker.get_server_time()
                for row in sorted(rows or [], key=lambda item: int(item[0])):
                    observation = _kline_observation(symbol, row, warmup_interval)
                    if observation.available_at_ms > now_ms:
                        # The bar is still forming; unfinished data is invisible.
                        continue
                    observations.append(observation)
            return observations

        plugin = None
        module = os.getenv("QTS_STRATEGY_MODULE", "")
        if module:
            from trader.trading.research import load_plugin

            config = json.loads(os.getenv("QTS_STRATEGY_CONFIG", "{}"))
            plugin = load_plugin(module, config)
            await plugin.initialize(config)

        from trader.trading.risk import risk_policy_from_env

        runtime = build_runtime(
            store=PostgresTradingStore(pool),
            exchange=BinanceSpotExchangePort(broker),
            account_id=account_id,
            snapshot_provider=provider.build,
            clock=clock,
            policy=risk_policy_from_env(),
            owner=os.getenv("QTS_RUNTIME_OWNER", f"{socket.gethostname()}:{os.getpid()}"),
            reconnect_callback=reconnect,
            reconcile_interval_seconds=float(os.getenv("QTS_RECONCILE_INTERVAL_SECONDS", "30")),
            private_session_factory=session_factory,
            allowed_symbols=set(provider.symbols),
            strategy_plugin=plugin,
            instruments=instruments,
            deployment_id=os.getenv("QTS_DEPLOYMENT_ID", ""),
            strategy_id=module,
            strategy_warmup_events=int(os.getenv("QTS_STRATEGY_WARMUP_EVENTS", "0")),
            warmup_source=warmup_source if plugin is not None else None,
            dr_gate=dr_gate_from_env(),
        )
        started = await runtime.start()
        health_path = os.getenv("QTS_EXECUTION_HEALTH_FILE", "")
        if health_path:
            from trader.runtime.healthcheck import execution_health_loop

            health_task = asyncio.create_task(
                execution_health_loop(
                    runtime,
                    health_path,
                    float(os.getenv("QTS_HEALTH_INTERVAL_SECONDS", "5")),
                )
            )
        alert_monitor = alert_monitor_from_env(
            source="execution",
            account_id=account_id,
            readiness=runtime.readiness,
        )
        if alert_monitor is not None:
            alert_task = asyncio.create_task(alert_monitor.run_forever())
        logger.info(
            "Execution startup ready=%s stages=%s reasons=%s",
            started,
            runtime.stages,
            runtime.last_reasons,
        )
        if not started and "EXECUTION_INSTANCE_CONFLICT" in runtime.last_reasons:
            raise RuntimeError("EXECUTION_INSTANCE_CONFLICT")
        if runtime.strategy_runner is not None and started:
            # Rebuild indicator state before the strategy may produce output.
            await enforce_strategy_warmup(runtime)
        if plugin is not None:
            market_interval = os.getenv("QTS_STRATEGY_MARKET_INTERVAL", warmup_interval)
            market_task = asyncio.create_task(
                _market_loop(
                    runtime,
                    broker,
                    instruments,
                    market_interval,
                    float(os.getenv("QTS_MARKET_POLL_SECONDS", "1")),
                )
            )
        # A graceful stop must release the execution lease, otherwise a
        # restart drill waits for the lease to expire and the new instance
        # fails with EXECUTION_INSTANCE_CONFLICT.
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signal_name in ("SIGINT", "SIGTERM"):
            signal_number = getattr(signal, signal_name, None)
            if signal_number is None:
                continue
            try:
                loop.add_signal_handler(signal_number, stop_event.set)
            except (NotImplementedError, RuntimeError):
                # Windows consoles cannot install signal handlers; the
                # operator then stops the process and the lease expires.
                logger.info("Signal handler for %s is unavailable", signal_name)
        await stop_event.wait()
        logger.info("Execution shutdown requested; releasing the lease")
    finally:
        if market_task:
            market_task.cancel()
            import contextlib

            with contextlib.suppress(asyncio.CancelledError):
                await market_task
        if health_task:
            health_task.cancel()
            import contextlib

            with contextlib.suppress(asyncio.CancelledError):
                await health_task
        if alert_task:
            alert_task.cancel()
            import contextlib

            with contextlib.suppress(asyncio.CancelledError):
                await alert_task
        if alert_monitor is not None:
            import contextlib

            with contextlib.suppress(Exception):
                await alert_monitor.close()
        if runtime:
            await runtime.stop()
        await broker.disconnect()
        await pool.close()


def main():
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run())
    except CredentialBoundaryError as exc:
        # A refusal is a startup contract, not a transient failure: exit with a
        # stable reason code so Compose/operators can stop retrying blindly.
        logger.error("EXECUTION_STARTUP_REFUSED reason=%s", exc.reason_code)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
