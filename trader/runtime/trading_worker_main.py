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
from trader.trading.models import NotReady
from trader.trading.pg import create_pool
from trader.trading.postgres import PostgresTradingStore
from trader.trading.runtime import TradingRuntime

logger = logging.getLogger(__name__)


def _kline_observation(symbol: str, row, interval: str):
    """Convert one Binance kline row into a closed-bar observation."""
    from trader.core.application.strategy_protocol import MarketData, MarketDataType

    open_time = int(row[0])
    close_time = int(row[6])
    close_price = Decimal(str(row[4]))
    return MarketData(
        symbol=symbol,
        data_type=MarketDataType.KLINE,
        price=close_price,
        volume=Decimal(str(row[5])),
        timestamp=datetime.fromtimestamp(open_time / 1000, tz=timezone.utc),
        kline_open=Decimal(str(row[1])),
        kline_high=Decimal(str(row[2])),
        kline_low=Decimal(str(row[3])),
        kline_close=close_price,
        kline_interval=interval,
        metadata={
            "event_id": f"warmup:{symbol}:{open_time}",
            "complete": True,
            "is_closed": True,
            "close_time_ms": close_time,
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


async def feed_market_once(runtime, broker, instruments):
    if not runtime.strategy_start_allowed or runtime.strategy_runner is None:
        return
    state = getattr(runtime.strategy_runner.warmup_state, "value", "")
    if state == "BLOCKED":
        # A blocked warm-up must not keep poking the plugin.
        return
    from trader.core.application.strategy_protocol import MarketData, MarketDataType

    prices = await broker.get_ticker_prices(sorted(instruments))
    now = runtime.clock()
    for symbol, price in prices.items():
        if symbol not in instruments:
            continue
        event_id = f"ticker:{symbol}:{now}"
        data = MarketData(
            symbol=symbol,
            data_type=MarketDataType.TICKER,
            price=Decimal(str(price)),
            timestamp=datetime.fromtimestamp(now / 1000, tz=timezone.utc),
            metadata={"event_id": event_id, "source": "rest_ticker"},
        )
        await runtime.on_market_data(data, event_id=event_id)


async def _market_loop(runtime, broker, instruments, interval):
    while True:
        try:
            await feed_market_once(runtime, broker, instruments)
        except Exception:
            logger.exception("Strategy or market input failed; blocking new risk")
            await runtime.block("STRATEGY_OR_MARKET_DATA_FAILED")
        await asyncio.sleep(interval)


async def run():
    if os.getenv("BINANCE_ENV", "demo") not in {"demo", "testnet", "test"}:
        raise RuntimeError("Only Binance sandbox environments are supported")
    api_key = os.getenv("BINANCE_API_KEY", "")
    secret = os.getenv("BINANCE_SECRET_KEY", "")
    if not api_key or not secret:
        raise RuntimeError("BINANCE_CREDENTIALS_MISSING")
    pool = await create_pool()
    broker = BinanceSpotDemoBroker(
        BinanceSpotDemoBrokerConfig.for_env(
            api_key,
            secret,
            env=os.getenv("BINANCE_ENV", "demo"),
            proxy_url=os.getenv("BINANCE_PROXY_URL") or None,
        )
    )
    runtime = None
    market_task = None
    try:
        await broker.connect()
        account_id = os.getenv("QTS_ACCOUNT_ID", "binance_spot")
        clock = lambda: int(time.time() * 1000)
        provider = BinanceSpotSnapshotProvider(
            broker,
            account_id=account_id,
            symbols=os.getenv("QTS_SYMBOLS", "BTCUSDT").split(","),
            # Snapshot timestamps must live in the venue clock domain: the
            # coverage proof compares them with the stream connection time.
            clock=broker.get_server_time,
        )
        coverage = BinanceSpotCoverageProver(
            rest=provider,
            symbols=provider.symbols,
            exchange_clock=broker.get_server_time,
            lookback_ms=int(os.getenv("QTS_COVERAGE_LOOKBACK_MS", "86400000")),
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
                rows = await broker.get_klines(symbol, interval=warmup_interval, limit=required + 1)
                now_ms = await broker.get_server_time()
                for row in rows:
                    close_time = int(row[6])
                    if close_time > now_ms:
                        # The bar is still forming; unfinished data is invisible.
                        continue
                    observations.append(_kline_observation(symbol, row, warmup_interval))
            return observations

        plugin = None
        module = os.getenv("QTS_STRATEGY_MODULE", "")
        if module:
            from trader.trading.research import load_plugin

            config = json.loads(os.getenv("QTS_STRATEGY_CONFIG", "{}"))
            plugin = load_plugin(module, config)
            await plugin.initialize(config)

        runtime = build_runtime(
            store=PostgresTradingStore(pool),
            exchange=BinanceSpotExchangePort(broker),
            account_id=account_id,
            snapshot_provider=provider.build,
            clock=clock,
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
        )
        started = await runtime.start()
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
            market_task = asyncio.create_task(
                _market_loop(
                    runtime,
                    broker,
                    instruments,
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
        if runtime:
            await runtime.stop()
        await broker.disconnect()
        await pool.close()


def main():
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
