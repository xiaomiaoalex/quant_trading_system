"""Strategies must execute through the recovered account runtime only."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.core.domain.models.signal import Signal, SignalType
from trader.tests.test_trading_runtime import runtime, snapshot
from trader.trading.models import Conflict, Execution, NotReady
from trader.trading.strategy import UnifiedStrategyRunner


class Plugin:
    def __init__(self, side=SignalType.BUY, qty="1", stateful=False):
        self.side, self.qty = side, Decimal(qty)
        # Stateless plugins hold no indicator window, so they trade at once.
        self.stateful_indicators = stateful
        self.warmup_events = 0
        self.calls = 0
        self.views = []

    async def on_account_view(self, view):
        self.views.append(view)

    async def on_market_data(self, data):
        self.calls += 1
        return Signal(
            signal_type=self.side, symbol=data.symbol, price=data.price, quantity=self.qty
        )


def market(price="100"):
    return MarketData(
        symbol="BTCUSDT",
        data_type=MarketDataType.TICKER,
        price=Decimal(price),
        timestamp=datetime.fromtimestamp(1000, tz=timezone.utc),
    )


def runner(rt, plugin=None, warmup_events=0):
    return UnifiedStrategyRunner(
        rt,
        deployment_id="deployment-1",
        plugin=plugin or Plugin(),
        instruments={"BTCUSDT": ("BTC", "USDT")},
        warmup_events=warmup_events,
    )


@pytest.mark.asyncio
async def test_runner_buy_fill_sell_and_reentry_share_the_account_risk_and_inventory():
    rt = runtime(snap=snapshot())
    await rt.start()
    plugin = Plugin()
    strategy = runner(rt, plugin)
    try:
        buy = await strategy.on_market_data(market(), event_id="tick-1")
        await rt.service.record_execution(
            Execution(
                "acct",
                buy.cl_ord_id,
                "BTCUSDT",
                "buy",
                Decimal("1"),
                Decimal("100"),
                Decimal("0.1"),
                "USDT",
            )
        )
        assert (await strategy.account_view()).positions["BTCUSDT"] == Decimal("1")
        plugin.side = SignalType.SELL
        sell = await strategy.on_market_data(market(), event_id="tick-2")
        assert plugin.views[-1].positions["BTCUSDT"] == Decimal("1")
        await rt.service.record_execution(
            Execution(
                "acct",
                sell.cl_ord_id,
                "BTCUSDT",
                "sell",
                Decimal("1"),
                Decimal("100"),
                Decimal("0.1"),
                "USDT",
            )
        )
        plugin.side = SignalType.BUY
        again = await strategy.on_market_data(market(), event_id="tick-3")
        assert again.status == "CONFIRMED"
        assert len(rt.exchange.placed) == 3
        state = await rt.store.read("acct")
        assert state.positions["BTCUSDT"] == Decimal("0")
        assert state.balances["USDT"] == Decimal("999.8")
        assert all(order.command.deployment_id == "deployment-1" for order in state.orders.values())
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_runner_retry_and_restart_reuse_event_command_without_rerunning_plugin():
    rt = runtime(snap=snapshot())
    await rt.start()
    plugin = Plugin(qty="0.123")
    strategy = runner(rt, plugin)
    try:
        receipts = await asyncio.gather(
            *[strategy.on_market_data(market(), event_id="same-event") for _ in range(100)]
        )
        replacement_plugin = Plugin(qty="0.123")
        after_restart = await runner(rt, replacement_plugin).on_market_data(
            market(), event_id="same-event"
        )
        assert {receipt.command_id for receipt in receipts} == {after_restart.command_id}
        assert plugin.calls == 1
        assert replacement_plugin.calls == 0
        assert len(rt.exchange.placed) == 1
        assert rt.exchange.placed[0][0].qty == Decimal("0.123")
        with pytest.raises(Conflict):
            await strategy.on_market_data(market("101"), event_id="same-event")
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_runner_never_invokes_plugin_before_account_recovery():
    rt = runtime(snap=None)
    await rt.start()
    plugin = Plugin()
    try:
        with pytest.raises(NotReady):
            await runner(rt, plugin).on_market_data(market(), event_id="not-ready")
        assert plugin.calls == 0
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("side", [SignalType.SHORT, SignalType.CLOSE_SHORT])
async def test_runner_rejects_short_intents_without_converting_to_sell(side):
    rt = runtime(snap=snapshot())
    await rt.start()
    try:
        with pytest.raises(ValueError, match="UNSUPPORTED_SPOT_INTENT"):
            await runner(rt, Plugin(side)).on_market_data(market(), event_id="short")
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_signal_retry_preserves_command_id_and_rejects_changed_quantity():
    rt = runtime(snap=snapshot())
    await rt.start()
    strategy = runner(rt)
    try:
        signal = Signal(signal_type=SignalType.BUY, symbol="BTCUSDT", quantity=Decimal("1"))
        first = await strategy.submit_signal(
            signal, decision_id="decision-1", reference_price=Decimal("100")
        )
        signal.signal_id = "new-uuid-from-plugin-retry"
        second = await strategy.submit_signal(
            signal, decision_id="decision-1", reference_price=Decimal("100")
        )
        assert first.command_id == second.command_id
        assert len(rt.exchange.placed) == 1
        signal.quantity = Decimal("2")
        with pytest.raises(Conflict):
            await strategy.submit_signal(
                signal, decision_id="decision-1", reference_price=Decimal("100")
            )
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_only_real_fill_creates_sellable_inventory_and_base_fees_reduce_it():
    rt = runtime(snap=snapshot())
    await rt.start()
    plugin = Plugin()
    strategy = runner(rt, plugin)
    try:
        buy = await strategy.on_market_data(market(), event_id="buy")
        plugin.side = SignalType.SELL
        with pytest.raises(ValueError, match="INSUFFICIENT_AVAILABLE_BALANCE"):
            await strategy.on_market_data(market(), event_id="sell-before-fill")
        await rt.service.record_execution(
            Execution(
                "acct",
                buy.cl_ord_id,
                "BTCUSDT",
                "real-fill",
                Decimal("1"),
                Decimal("100"),
                Decimal("0.001"),
                "BTC",
            )
        )
        with pytest.raises(ValueError, match="INSUFFICIENT_AVAILABLE_BALANCE"):
            await strategy.on_market_data(market(), event_id="oversell-fee")
        plugin.qty = Decimal("0.999")
        sell = await strategy.on_market_data(market(), event_id="sell-after-fill")
        assert sell.status == "CONFIRMED"
        assert rt.exchange.placed[-1][0].qty == Decimal("0.999")
        assert (await strategy.account_view()).positions["BTCUSDT"] == Decimal("0.999")
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_warmup_observations_feed_the_plugin_without_trading():
    rt = runtime(snap=snapshot())
    await rt.start()
    plugin = Plugin(stateful=True)
    strategy = runner(rt, plugin, warmup_events=2)
    rt.strategy_runner = strategy
    try:
        assert strategy.warmup_remaining == 2
        assert await strategy.on_market_data(market(), event_id="warm-1") is None
        assert await strategy.on_market_data(market(), event_id="warm-2") is None
        assert plugin.calls == 2
        assert rt.exchange.placed == []
        assert strategy.warmup_remaining == 0
        assert (await rt.readiness())["strategy_warmup_remaining"] == 0
        receipt = await strategy.on_market_data(market(), event_id="tradable")
        assert receipt is not None and receipt.status == "CONFIRMED"
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_restart_starts_a_fresh_warmup_period():
    rt = runtime(snap=snapshot())
    await rt.start()
    try:
        first = runner(rt, Plugin(stateful=True), warmup_events=1)
        assert await first.on_market_data(market(), event_id="before-restart") is None

        restarted_plugin = Plugin(stateful=True)
        restarted = runner(rt, restarted_plugin, warmup_events=1)
        assert await restarted.on_market_data(market(), event_id="after-restart") is None
        assert restarted_plugin.calls == 1
        assert rt.exchange.placed == []

        receipt = await restarted.on_market_data(market(), event_id="after-warmup")
        assert receipt is not None
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_warmup_never_bypasses_account_recovery():
    rt = runtime(snap=None)
    await rt.start()
    plugin = Plugin()
    try:
        with pytest.raises(NotReady):
            await runner(rt, plugin, warmup_events=1).on_market_data(market(), event_id="blocked")
        assert plugin.calls == 0
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_strategy_rejects_conditional_or_unfinished_intents():
    rt = runtime(snap=snapshot())
    await rt.start()
    strategy = runner(rt)
    try:
        signal = Signal(
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            quantity=Decimal("1"),
            stop_loss=Decimal("90"),
        )
        with pytest.raises(ValueError, match="UNSUPPORTED_SPOT_CONDITIONS"):
            await strategy.submit_signal(signal, decision_id="stop", reference_price=Decimal("100"))
        incomplete = market()
        incomplete.data_type = MarketDataType.KLINE
        incomplete.metadata["complete"] = False
        with pytest.raises(ValueError, match="UNFINISHED_BAR"):
            await strategy.on_market_data(incomplete, event_id="unfinished")
        assert rt.exchange.placed == []
    finally:
        await rt.stop()
