"""Indicator warm-up: a restarted plugin may never trade on cold state."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trader.core.application.strategy_protocol import (
    MarketData,
    MarketDataType,
    describe_plugin_warmup,
)
from trader.core.domain.models.signal import Signal, SignalType
from trader.tests.test_trading_runtime import runtime, snapshot
from trader.trading.models import NotReady
from trader.trading.strategy import UnifiedStrategyRunner, WarmupState


class Plugin:
    """Test plugin; the declarations mirror the installed strategy contract."""

    def __init__(self, *, stateful=None, warmup_events=None, fail_on=None):
        if stateful is not None:
            self.stateful_indicators = stateful
        if warmup_events is not None:
            self.warmup_events = warmup_events
        self.fail_on = fail_on
        self.calls = 0
        self.seen: list[str] = []

    async def on_market_data(self, data):
        self.calls += 1
        self.seen.append(data.symbol)
        if self.fail_on is not None and self.calls == self.fail_on:
            raise RuntimeError("indicator backend unavailable")
        return Signal(
            signal_type=SignalType.BUY,
            symbol=data.symbol,
            price=data.price,
            quantity=Decimal("1"),
        )


def market(symbol="BTCUSDT", price="100"):
    return MarketData(
        symbol=symbol,
        data_type=MarketDataType.KLINE,
        price=Decimal(price),
        timestamp=datetime.fromtimestamp(1000, tz=timezone.utc),
    )


def runner(rt, plugin=None, *, warmup_events=0, warmup_source=None, instruments=None):
    return UnifiedStrategyRunner(
        rt,
        deployment_id="deployment-warmup",
        plugin=plugin if plugin is not None else Plugin(stateful=False),
        instruments=instruments or {"BTCUSDT": ("BTC", "USDT")},
        warmup_events=warmup_events,
        warmup_source=warmup_source,
        on_block=rt.block,
    )


def test_warmup_spec_treats_undeclared_plugins_as_stateful():
    undeclared = describe_plugin_warmup(Plugin())
    assert undeclared.stateful is True
    assert undeclared.declared is False
    stateless = describe_plugin_warmup(Plugin(stateful=False, warmup_events=0))
    assert stateless.stateful is False
    declared = describe_plugin_warmup(Plugin(stateful=True, warmup_events=7))
    assert (declared.stateful, declared.events, declared.declared) == (True, 7, True)


@pytest.mark.asyncio
async def test_undeclared_stateful_plugin_blocks_new_risk():
    rt = runtime(snap=snapshot())
    await rt.start()
    plugin = Plugin()
    strategy = runner(rt, plugin)
    rt.strategy_runner = strategy
    try:
        assert strategy.warmup_state is WarmupState.BLOCKED
        assert strategy.warmup_reason == "STRATEGY_WARMUP_NOT_CONFIGURED"
        with pytest.raises(NotReady):
            await strategy.on_market_data(market(), event_id="cold")
        assert plugin.calls == 0
        assert rt.exchange.placed == []
        readiness = await rt.readiness()
        assert readiness["strategy_warmup_state"] == "BLOCKED"
        assert readiness["strategy_warmup_reason"] == "STRATEGY_WARMUP_NOT_CONFIGURED"
        assert readiness["strategy_ready"] is False
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_stateless_plugin_is_warm_without_observations():
    rt = runtime(snap=snapshot())
    await rt.start()
    plugin = Plugin(stateful=False, warmup_events=0)
    strategy = runner(rt, plugin)
    try:
        assert strategy.warmup_state is WarmupState.WARM
        assert strategy.strategy_ready is True
        receipt = await strategy.on_market_data(market(), event_id="first")
        assert receipt is not None and receipt.status == "CONFIRMED"
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_warmup_requires_every_instrument_before_trading():
    rt = runtime(snap=snapshot())
    await rt.start()
    plugin = Plugin(stateful=True, warmup_events=2)
    strategy = runner(
        rt,
        plugin,
        warmup_events=2,
        instruments={"BTCUSDT": ("BTC", "USDT"), "ETHUSDT": ("ETH", "USDT")},
    )
    try:
        assert strategy.warmup_state is WarmupState.PENDING
        assert await strategy.on_market_data(market("BTCUSDT"), event_id="b1") is None
        assert strategy.warmup_state is WarmupState.WARMING_UP
        assert await strategy.on_market_data(market("BTCUSDT"), event_id="b2") is None
        # BTC is warm, ETH is not: the runner must still refuse to trade.
        assert strategy.warmup_state is WarmupState.WARMING_UP
        assert strategy.warmup_remaining == 2
        assert await strategy.on_market_data(market("ETHUSDT"), event_id="e1") is None
        assert strategy.warmup_state is WarmupState.WARMING_UP
        assert await strategy.on_market_data(market("ETHUSDT"), event_id="e2") is None
        assert strategy.warmup_state is WarmupState.WARM
        assert strategy.warmup_remaining == 0
        assert rt.exchange.placed == []
        receipt = await strategy.on_market_data(market("BTCUSDT"), event_id="trade")
        assert receipt is not None
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_restart_requires_a_fresh_warmup_before_trading():
    rt = runtime(snap=snapshot())
    await rt.start()
    try:
        first = runner(rt, Plugin(stateful=True, warmup_events=1), warmup_events=1)
        assert await first.on_market_data(market(), event_id="warm-1") is None
        assert first.warmup_state is WarmupState.WARM

        # A new process rebuilds the plugin: the old warm state is not reused.
        restarted = runner(rt, Plugin(stateful=True, warmup_events=1), warmup_events=1)
        assert restarted.warmup_state is WarmupState.PENDING
        assert await restarted.on_market_data(market(), event_id="restart-1") is None
        assert rt.exchange.placed == []
        receipt = await restarted.on_market_data(market(), event_id="restart-2")
        assert receipt is not None
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_history_replay_warms_the_plugin_without_live_events():
    rt = runtime(snap=snapshot())
    await rt.start()
    plugin = Plugin(stateful=True, warmup_events=3)
    history = [market(price=str(100 + index)) for index in range(3)]

    async def source():
        return list(history)

    strategy = runner(rt, plugin, warmup_events=3, warmup_source=source)
    try:
        assert await strategy.prepare_warmup() is True
        assert strategy.warmup_state is WarmupState.WARM
        assert plugin.calls == 3
        assert rt.exchange.placed == []
        receipt = await strategy.on_market_data(market(), event_id="after-warmup")
        assert receipt is not None
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_incomplete_history_blocks_with_a_precise_reason():
    rt = runtime(snap=snapshot())
    await rt.start()
    plugin = Plugin(stateful=True, warmup_events=3)

    async def source():
        return [market(price="100"), market(price="101")]

    strategy = runner(rt, plugin, warmup_events=3, warmup_source=source)
    try:
        assert await strategy.prepare_warmup() is False
        assert strategy.warmup_state is WarmupState.BLOCKED
        assert "STRATEGY_WARMUP_HISTORY_INCOMPLETE" in strategy.warmup_reason
        assert plugin.calls == 0
        assert rt.exchange.placed == []
        reasons = (await rt.readiness())["reasons"]
        assert any(r.startswith("STRATEGY_WARMUP_HISTORY_INCOMPLETE") for r in reasons)
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_history_source_failure_blocks_instead_of_trading_cold():
    rt = runtime(snap=snapshot())
    await rt.start()
    plugin = Plugin(stateful=True, warmup_events=2)

    async def source():
        raise ConnectionError("venue unavailable")

    strategy = runner(rt, plugin, warmup_events=2, warmup_source=source)
    try:
        assert await strategy.prepare_warmup() is False
        assert strategy.warmup_state is WarmupState.BLOCKED
        assert "STRATEGY_WARMUP_HISTORY_FAILED" in strategy.warmup_reason
        with pytest.raises(NotReady):
            await strategy.on_market_data(market(), event_id="cold")
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_plugin_error_during_warmup_blocks_new_risk():
    rt = runtime(snap=snapshot())
    await rt.start()
    plugin = Plugin(stateful=True, warmup_events=2, fail_on=1)
    strategy = runner(rt, plugin, warmup_events=2)
    try:
        with pytest.raises(RuntimeError):
            await strategy.on_market_data(market(), event_id="broken")
        assert strategy.warmup_state is WarmupState.BLOCKED
        assert "STRATEGY_WARMUP_FAILED" in strategy.warmup_reason
        assert rt.exchange.placed == []
        reasons = (await rt.readiness())["reasons"]
        assert any(r.startswith("STRATEGY_WARMUP_FAILED") for r in reasons)
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_plugin_declaration_is_the_lower_bound_for_the_warmup_length():
    rt = runtime(snap=snapshot())
    await rt.start()
    plugin = Plugin(stateful=True, warmup_events=5)
    strategy = runner(rt, plugin, warmup_events=2)
    try:
        assert strategy.warmup_events == 5
        assert strategy.warmup_remaining == 5
        for index in range(4):
            assert await strategy.on_market_data(market(), event_id=f"w{index}") is None
        assert strategy.warmup_state is WarmupState.WARMING_UP
        assert await strategy.on_market_data(market(), event_id="w4") is None
        assert strategy.warmup_state is WarmupState.WARM
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_warmup_never_bypasses_account_recovery():
    rt = runtime(snap=None)
    await rt.start()
    plugin = Plugin(stateful=True, warmup_events=1)
    strategy = runner(rt, plugin, warmup_events=1)
    try:
        with pytest.raises(NotReady):
            await strategy.prepare_warmup()
        with pytest.raises(NotReady):
            await strategy.on_market_data(market(), event_id="blocked")
        assert plugin.calls == 0
        assert rt.exchange.placed == []
    finally:
        await rt.stop()
