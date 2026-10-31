"""Execution-entrypoint wiring with fake streams, clocks and strategies; no network."""

from dataclasses import replace

import pytest

from trader.runtime.trading_worker_main import build_runtime
from trader.tests.test_trading_runtime import snapshot
from trader.tests.test_trading_service import Clock, FakeExchange, command
from trader.trading.models import NotReady
from trader.trading.store import MemoryTradingStore


def build(**overrides):
    async def provider(account_id):
        return snapshot()

    args = dict(
        store=MemoryTradingStore(),
        exchange=FakeExchange(),
        account_id="acct",
        snapshot_provider=provider,
        clock=Clock(),
        command_interval_seconds=0,
        reconcile_interval_seconds=0,
    )
    args.update(overrides)
    return build_runtime(**args)


class CoveredSession:
    def __init__(self, rt):
        self.rt = rt
        self.ready = False
        self.stopped = False

    async def start(self):
        assert self.rt.service is not None
        assert self.rt.lease_token is not None
        assert not self.rt.strategy_start_allowed
        await self.rt.block("PRIVATE_STREAM_RECOVERING")
        await self.rt.service.recover(replace(snapshot(), stream_healthy=False))
        await self.rt.mark_stream_ready()
        self.ready = True
        return True

    align = start
    reconnect = start

    async def stop(self):
        self.stopped = True


def test_strategy_instruments_must_be_inside_the_coverage_symbols():
    from trader.runtime.trading_worker_main import require_instruments_within_symbols

    require_instruments_within_symbols({"BTCUSDT": ("BTC", "USDT")}, ["btcusdt"])
    with pytest.raises(ValueError, match="STRATEGY_INSTRUMENTS_NOT_IN_QTS_SYMBOLS:ETHUSDT"):
        require_instruments_within_symbols(
            {"BTCUSDT": ("BTC", "USDT"), "ETHUSDT": ("ETH", "USDT")},
            ["BTCUSDT"],
        )


@pytest.mark.asyncio
async def test_worker_without_private_session_stays_explicitly_blocked():
    rt = build()
    try:
        assert not await rt.start()
        assert "PRIVATE_SESSION_NOT_ASSEMBLED" in (await rt.readiness())["reasons"]
        with pytest.raises(NotReady):
            await rt.place(command())
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_worker_constructs_session_after_lease_and_waits_for_recovery():
    rt = build(private_session_factory=CoveredSession)
    assert await rt.start()
    assert rt.ready
    session = rt.private_session
    await rt.place(command())
    assert len(rt.exchange.placed) == 1
    await rt.stop()
    assert session.stopped


@pytest.mark.asyncio
async def test_session_block_closes_live_command_and_strategy_gate():
    rt = build(private_session_factory=CoveredSession)
    assert await rt.start()
    try:
        await rt.block("PRIVATE_STREAM_DISCONNECTED")
        assert not rt.strategy_start_allowed
        assert "PRIVATE_STREAM_DISCONNECTED" in (await rt.readiness())["reasons"]
        with pytest.raises(NotReady):
            await rt.place(command())
        state = await rt.store.read("acct")
        assert not state.control["runtime"]["ready"]
        assert not state.reconciliation["stream_healthy"]
    finally:
        await rt.stop()


def session_factory(*, covered):
    from trader.tests.test_private_trading_session import FakeStream
    from trader.trading.private_session import PrivateTradingSession
    from trader.trading.recovery import RecoveryCoverage

    def factory(rt):
        async def coverage(generation, first, last, connected_from_ms, buffered_through_ms):
            return RecoveryCoverage(generation, "fixture_cursor", 1, 2_000_000, 2_000_000, True)

        return PrivateTradingSession(
            service=rt.service,
            account_id="acct",
            stream_factory=FakeStream,
            snapshot_provider=rt.snapshot_provider,
            exchange_clock=lambda: 1_000_000,
            on_block=rt.block,
            on_ready=rt.mark_stream_ready,
            coverage_provider=coverage if covered else None,
        )

    return factory


@pytest.mark.asyncio
async def test_worker_real_session_without_coverage_reports_precise_blocker():
    rt = build(private_session_factory=session_factory(covered=False))
    try:
        assert not await rt.start()
        assert "PRIVATE_STREAM_COVERAGE_UNPROVEN" in (await rt.readiness())["reasons"]
        assert not (await rt.store.read("acct")).control["runtime"]["ready"]
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_worker_strategy_to_ws_fill_uses_one_ledger_and_disconnect_blocks_ticks():
    from decimal import Decimal
    from types import SimpleNamespace

    from trader.runtime.trading_worker_main import feed_market_once
    from trader.tests.test_unified_strategy_runner import Plugin

    plugin = Plugin(qty="0.1")
    rt = build(
        private_session_factory=session_factory(covered=True),
        strategy_plugin=plugin,
        instruments={"BTCUSDT": ("BTC", "USDT")},
        deployment_id="worker-strategy",
    )

    class Market:
        async def get_ticker_prices(self, symbols):
            return {"BTCUSDT": Decimal("100")}

    try:
        assert await rt.start()
        await feed_market_once(rt, Market(), {"BTCUSDT": ("BTC", "USDT")})
        assert len(rt.exchange.placed) == 1
        order_id = rt.exchange.placed[0][1]
        stream = rt.private_session.stream
        fill = SimpleNamespace(
            cl_ord_id=order_id,
            exec_id="1",
            trade_id=1,
            symbol="BTCUSDT",
            qty=Decimal("0.1"),
            price=Decimal("100"),
            commission=Decimal("0.01"),
            commission_asset="USDT",
            exchange_ts_ms=1_000_000,
        )
        await stream.emit(stream.fills, fill)
        await stream.emit(stream.fills, fill)
        state = await rt.store.read("acct")
        assert state.balances["USDT"] == Decimal("989.99")
        assert state.balances["BTC"] == Decimal("0.1")
        assert state.positions["BTCUSDT"] == Decimal("0.1")
        assert len(state.executions) == 1
        assert not state.holds
        await stream.disconnect()
        assert not rt.strategy_start_allowed
        await feed_market_once(rt, Market(), {"BTCUSDT": ("BTC", "USDT")})
        assert plugin.calls == 1
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_worker_blocks_when_the_strategy_warmup_cannot_be_satisfied():
    from trader.runtime.trading_worker_main import enforce_strategy_warmup

    # An undeclared plugin is treated as stateful: without a warm-up length the
    # worker must block instead of trading cold indicators.
    class UndeclaredPlugin:
        async def on_market_data(self, data):
            return None

    plugin = UndeclaredPlugin()
    rt = build(
        private_session_factory=session_factory(covered=True),
        strategy_plugin=plugin,
        instruments={"BTCUSDT": ("BTC", "USDT")},
        deployment_id="worker-warmup",
    )
    try:
        assert await rt.start()
        assert await enforce_strategy_warmup(rt) is False
        readiness = await rt.readiness()
        assert "STRATEGY_WARMUP_NOT_CONFIGURED" in readiness["reasons"]
        assert readiness["strategy_warmup_state"] == "BLOCKED"
        assert readiness["strategy_ready"] is False
        assert not rt.strategy_start_allowed
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_worker_warms_a_declared_plugin_from_the_recollected_window():
    from trader.runtime.trading_worker_main import enforce_strategy_warmup
    from trader.tests.test_unified_strategy_runner import Plugin
    from trader.trading.strategy import WarmupState

    plugin = Plugin(stateful=True)
    plugin.warmup_events = 2
    history = []

    async def source():
        return list(history)

    rt = build(
        private_session_factory=session_factory(covered=True),
        strategy_plugin=plugin,
        instruments={"BTCUSDT": ("BTC", "USDT")},
        deployment_id="worker-warmup-history",
    )
    rt.strategy_runner.warmup_source = source
    try:
        assert await rt.start()
        from datetime import datetime, timezone
        from decimal import Decimal

        from trader.core.application.strategy_protocol import MarketData, MarketDataType

        history.extend(
            MarketData(
                symbol="BTCUSDT",
                data_type=MarketDataType.KLINE,
                price=Decimal("100"),
                timestamp=datetime.fromtimestamp(1000, tz=timezone.utc),
            )
            for _ in range(2)
        )
        assert await enforce_strategy_warmup(rt) is True
        assert rt.strategy_runner.warmup_state is WarmupState.WARM
        assert rt.strategy_runner.warmup_remaining == 0
        assert plugin.calls == 2
        assert rt.exchange.placed == []
    finally:
        await rt.stop()
