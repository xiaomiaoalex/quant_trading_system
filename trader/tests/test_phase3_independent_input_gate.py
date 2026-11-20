"""Phase 3 independent tester regression: the non-bar market-data gate.

Contract 13.15 says the new-stack ``UnifiedStrategyRunner`` accepts completed
``MarketDataType.KLINE`` only; every other type (TICKER / TRADE / DEPTH /
FUNDING / INDEX_PRICE / MARK_PRICE) is refused with ``NON_BAR_MARKET_DATA``
*before* the plugin or any account side effect. There is no production ticker
opt-in, and the legacy ``allow_ticker_input`` keyword can only be ``False`` (a
construction no-op) or ``None``.

The parent probe (``parent-final-boundary-probes.py``) showed that the default
runner used to trade a TICKER. This file proves the same boundary behaviour with
its own fixtures and its own numbers, for both a default-constructed runner and
the production ``build_runtime`` assembly:

* a refused observation leaves plugin calls, exchange placements, orders,
  commands and holds at zero, and never advances warm-up;
* a closed bar is admitted exactly once at the ``available_at_ms`` boundary and
  prices a MARKET order at its close (``price_source`` / ``price_at_ms`` /
  ``price_reference_id`` follow the bar);
* a LIMIT order keeps the signal's declared price;
* unfinished and future bars are refused.

Expected numbers are hand-computed from BAR_V1 arithmetic and contract 13.15;
they are not copied from the engineer's assertions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.core.domain.models.order import OrderType
from trader.core.domain.models.signal import Signal, SignalType
from trader.runtime.trading_worker_main import build_runtime
from trader.tests.test_trading_runtime import runtime, snapshot, start_runtime
from trader.tests.test_trading_service import TEST_BOOK, Clock, FakeExchange
from trader.trading import books as book_ledger
from trader.trading.models import TradingError
from trader.trading.order_spec import PRICE_SOURCE_MARKET_CLOSED_BAR, PRICE_SOURCE_SIGNAL_LIMIT
from trader.trading.store import MemoryTradingStore
from trader.trading.strategy import UnifiedStrategyRunner

SYMBOL = "BTCUSDT"
MINUTE = 60_000
INSTRUMENTS = {SYMBOL: ("BTC", "USDT")}
NOW = 1_200_000

NON_BAR_TYPES = (
    MarketDataType.TICKER,
    MarketDataType.TRADE,
    MarketDataType.DEPTH,
    MarketDataType.FUNDING,
    MarketDataType.INDEX_PRICE,
    MarketDataType.MARK_PRICE,
)


def closed_bar(open_ms, *, close="104.25", complete=True):
    """BAR_V1 observation: visible exactly at ``open_ms + MINUTE``."""
    close_ms = open_ms + MINUTE
    bar_id = f"bar:{SYMBOL}:1m:{open_ms}"
    return MarketData(
        symbol=SYMBOL,
        data_type=MarketDataType.KLINE,
        price=Decimal(close),
        volume=Decimal("3"),
        timestamp=datetime.fromtimestamp(close_ms / 1000, tz=timezone.utc),
        available_at_ms=close_ms,
        kline_open=Decimal("100"),
        kline_high=Decimal("106"),
        kline_low=Decimal("99"),
        kline_close=Decimal(close),
        kline_interval="1m",
        metadata={
            "event_id": bar_id,
            "bar_id": bar_id,
            "open_time_ms": open_ms,
            "close_time_ms": close_ms,
            "available_at_ms": close_ms,
            "interval": "1m",
            "complete": complete,
            "is_closed": complete,
        },
    )


class GatePlugin:
    """Stateless probe: records inputs, signals a MARKET BUY at a fake price."""

    stateful_indicators = False
    warmup_events = 0

    def __init__(self, *, order_type=OrderType.MARKET, signal_price="999", quantity="2"):
        self.order_type = order_type
        self.signal_price = Decimal(signal_price)
        self.quantity = Decimal(quantity)
        self.calls = 0
        self.seen: list[MarketData] = []

    async def on_market_data(self, data):
        self.calls += 1
        self.seen.append(data)
        return Signal(
            signal_type=SignalType.BUY,
            order_type=self.order_type,
            symbol=data.symbol,
            price=self.signal_price,
            quantity=self.quantity,
        )


class WarmPlugin:
    """Stateful probe: two replayed bars must warm it before it may trade."""

    stateful_indicators = True
    warmup_events = 2

    def __init__(self):
        self.calls = 0
        self.seen: list[MarketData] = []

    async def on_market_data(self, data):
        self.calls += 1
        self.seen.append(data)
        return Signal(
            signal_type=SignalType.BUY,
            order_type=OrderType.MARKET,
            symbol=data.symbol,
            price=Decimal("999"),
            quantity=Decimal("1"),
        )


def make_runner(rt, plugin, *, warmup_source=None):
    return UnifiedStrategyRunner(
        rt,
        deployment_id="p3i-input-gate",
        plugin=plugin,
        instruments=INSTRUMENTS,
        book_id=TEST_BOOK,
        warmup_events=getattr(plugin, "warmup_events", 0),
        warmup_source=warmup_source,
        on_block=rt.block,
    )


def non_bar(data_type):
    """A fresh, correctly priced observation of a forbidden type."""
    return MarketData(
        symbol=SYMBOL,
        data_type=data_type,
        price=Decimal("104.25"),
        volume=Decimal("1"),
        timestamp=datetime.fromtimestamp(NOW / 1000, tz=timezone.utc),
    )


async def counts(rt, plugin):
    state = await rt.store.read(rt.account_id)
    return {
        "plugin": plugin.calls,
        "exchange": len(rt.exchange.placed),
        "orders": len(state.orders),
        "commands": len(state.commands),
        "holds": len(state.holds),
    }


# ---------------------------------------------- non-bar gate, both runners ---


@pytest.mark.asyncio
@pytest.mark.parametrize("data_type", NON_BAR_TYPES, ids=lambda item: item.value)
async def test_p3i_non_bar_type_is_refused_by_default_and_production_runners(data_type):
    # 1) Default-constructed runner: no legacy keyword at all.
    plugin = GatePlugin()
    rt = runtime(clock=Clock(NOW), snap=snapshot(checked=NOW))
    assert await start_runtime(rt)
    runner = make_runner(rt, plugin)
    rt.strategy_runner = runner
    try:
        assert not hasattr(runner, "allow_ticker_input")
        with pytest.raises(TradingError, match="NON_BAR_MARKET_DATA"):
            await runner.on_market_data(non_bar(data_type), event_id=f"gate-{data_type.value}")
        assert await counts(rt, plugin) == {
            "plugin": 0,
            "exchange": 0,
            "orders": 0,
            "commands": 0,
            "holds": 0,
        }
    finally:
        await rt.stop()

    # 2) Production assembly: ``build_runtime`` must not expose an opt-in.
    async def snapshot_provider(account_id):
        return snapshot(checked=NOW)

    production_plugin = GatePlugin()
    assembled = build_runtime(
        store=MemoryTradingStore(),
        exchange=FakeExchange(),
        account_id="acct",
        snapshot_provider=snapshot_provider,
        clock=Clock(NOW),
        strategy_plugin=production_plugin,
        instruments=INSTRUMENTS,
        deployment_id="p3i-input-gate",
        strategy_id="strategy-1",
    )
    production_runner = assembled.strategy_runner
    assert production_runner is not None
    assert not hasattr(production_runner, "allow_ticker_input")
    with pytest.raises(TradingError, match="NON_BAR_MARKET_DATA"):
        await production_runner.on_market_data(
            non_bar(data_type), event_id=f"prod-{data_type.value}"
        )
    state = await assembled.store.read(assembled.account_id)
    assert production_plugin.calls == 0
    assert assembled.exchange.placed == []
    assert len(state.orders) == 0
    assert len(state.commands) == 0
    assert len(state.holds) == 0


@pytest.mark.asyncio
async def test_p3i_legacy_ticker_switch_is_a_no_op_or_a_refusal():
    plugin = GatePlugin()
    rt = runtime(clock=Clock(NOW), snap=snapshot(checked=NOW))
    assert await start_runtime(rt)
    try:
        # ``False`` is accepted only so old callers keep constructing; it is not
        # stored anywhere the runner could consult.
        runner = UnifiedStrategyRunner(
            rt,
            deployment_id="p3i-input-gate",
            plugin=plugin,
            instruments=INSTRUMENTS,
            book_id=TEST_BOOK,
            warmup_events=0,
            on_block=rt.block,
            allow_ticker_input=False,
        )
        assert not hasattr(runner, "allow_ticker_input")
        # ``True`` cannot opt in: the boundary itself is unconfigurable.
        with pytest.raises(TradingError, match="TICKER_INPUT_IS_NOT_CONFIGURABLE"):
            UnifiedStrategyRunner(
                rt,
                deployment_id="p3i-input-gate",
                plugin=plugin,
                instruments=INSTRUMENTS,
                book_id=TEST_BOOK,
                warmup_events=0,
                on_block=rt.block,
                allow_ticker_input=True,
            )
        assert plugin.calls == 0
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


# --------------------------------------------------- closed-bar positive -------


@pytest.mark.asyncio
async def test_p3i_closed_bar_is_admitted_exactly_once_at_the_boundary():
    plugin = GatePlugin(signal_price="999", quantity="2")
    rt = runtime(clock=Clock(NOW), snap=snapshot(checked=NOW))
    assert await start_runtime(rt)
    runner = make_runner(rt, plugin)
    rt.strategy_runner = runner
    try:
        # open 1_140_000 + 60_000 = 1_200_000 = decision clock: equality is visible.
        observation = closed_bar(1_140_000, close="100")
        receipt = await runner.on_market_data(
            observation,
            event_id=observation.metadata["bar_id"],
        )
        assert receipt is not None
        assert receipt.status == "CONFIRMED"
        assert plugin.calls == 1
        assert len(rt.exchange.placed) == 1
        command, _cl_ord_id = rt.exchange.placed[0]
        assert command.order_type == "MARKET"
        assert command.price == Decimal("100")
        assert command.price_source == PRICE_SOURCE_MARKET_CLOSED_BAR
        assert command.price_at_ms == 1_200_000
        assert command.price_reference_id == "bar:BTCUSDT:1m:1140000"
        assert command.qty == Decimal("2")

        # Phase-2 accounting did not regress: 2 * 100 * (1 + 0.001) = 200.2 on
        # both reservation layers, one order, and conservation intact.
        state = await rt.store.read(rt.account_id)
        assert len(state.orders) == 1
        assert len(state.commands) == 1
        assert len(state.holds) == 1
        assert state.holds[receipt.cl_ord_id].amount == Decimal("200.2")
        assert state.books[TEST_BOOK].holds[receipt.cl_ord_id].amount == Decimal("200.2")
        assert state.orders[receipt.cl_ord_id].remaining_qty == Decimal("2")
        assert book_ledger.conservation_mismatch(state) == {}

        # A replay of the same bar is the same decision, not a second trade.
        again = await runner.on_market_data(
            observation,
            event_id=observation.metadata["bar_id"],
        )
        assert again is not None
        assert again.cl_ord_id == receipt.cl_ord_id
        assert plugin.calls == 1
        assert len(rt.exchange.placed) == 1
        state = await rt.store.read(rt.account_id)
        assert len(state.orders) == 1
        assert len(state.holds) == 1
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_p3i_limit_signal_keeps_the_declared_price_and_bar_identity():
    plugin = GatePlugin(order_type=OrderType.LIMIT, signal_price="99.5", quantity="1")
    rt = runtime(clock=Clock(NOW), snap=snapshot(checked=NOW))
    assert await start_runtime(rt)
    runner = make_runner(rt, plugin)
    rt.strategy_runner = runner
    try:
        observation = closed_bar(1_140_000, close="104.25")
        receipt = await runner.on_market_data(
            observation,
            event_id=observation.metadata["bar_id"],
        )
        assert receipt is not None
        command, _cl_ord_id = rt.exchange.placed[0]
        assert command.order_type == "LIMIT"
        # LIMIT keeps the signal price; only the reference/audit fields name the
        # closed bar that produced the decision.
        assert command.price == Decimal("99.5")
        assert command.price_source == PRICE_SOURCE_SIGNAL_LIMIT
        assert command.price_reference_id == "bar:BTCUSDT:1m:1140000"
        assert command.price_at_ms == 1_200_000
        assert command.price != Decimal("104.25")
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_p3i_unfinished_and_future_bars_are_refused():
    plugin = GatePlugin()
    rt = runtime(clock=Clock(NOW), snap=snapshot(checked=NOW))
    assert await start_runtime(rt)
    runner = make_runner(rt, plugin)
    rt.strategy_runner = runner
    try:
        unfinished = closed_bar(1_140_000, complete=False)
        with pytest.raises(TradingError, match="UNFINISHED_BAR"):
            await runner.on_market_data(unfinished, event_id="unfinished")
        # available_at_ms = 1_200_001, one millisecond after the decision clock.
        future = closed_bar(1_140_001)
        with pytest.raises(TradingError, match="BAR_NOT_AVAILABLE_YET"):
            await runner.on_market_data(future, event_id="future")
        assert plugin.calls == 0
        assert rt.exchange.placed == []
        state = await rt.store.read(rt.account_id)
        assert len(state.orders) == 0
        assert len(state.commands) == 0
        assert len(state.holds) == 0

        # The same bar one millisecond later is admitted: only availability
        # decided the refusal.
        rt.clock.now = 1_200_001
        receipt = await runner.on_market_data(future, event_id="future")
        assert receipt is not None
        assert plugin.calls == 1
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


# --------------------------------------------------- warm-up interaction ------


@pytest.mark.asyncio
async def test_p3i_refused_inputs_never_advance_warmup():
    plugin = WarmPlugin()

    async def warmup_source():
        return [closed_bar(880_000), closed_bar(940_000)]

    rt = runtime(clock=Clock(NOW), snap=snapshot(checked=NOW))
    assert await start_runtime(rt)
    runner = make_runner(rt, plugin, warmup_source=warmup_source)
    rt.strategy_runner = runner
    try:
        assert runner.warmup_state.value == "PENDING"
        assert runner.warmup_remaining == 2
        for data_type in NON_BAR_TYPES:
            with pytest.raises(TradingError, match="NON_BAR_MARKET_DATA"):
                await runner.on_market_data(non_bar(data_type), event_id=f"warm-{data_type.value}")
        # No warm-up credit, no plugin call, no ledger movement.
        assert plugin.calls == 0
        assert runner.warmup_remaining == 2
        assert runner.instrument_warmup[SYMBOL] == {
            "state": "PENDING",
            "observations": 0,
            "required": 2,
        }
        assert rt.exchange.placed == []

        # The declared history warms the plugin exactly twice, then a closed bar
        # trades once.
        assert await runner.prepare_warmup() is True
        assert plugin.calls == 2
        assert runner.warmup_state.value == "WARM"
        assert runner.warmup_remaining == 0
        observation = closed_bar(1_140_000)
        receipt = await runner.on_market_data(
            observation,
            event_id=observation.metadata["bar_id"],
        )
        assert receipt is not None
        assert plugin.calls == 3
        assert len(rt.exchange.placed) == 1
        # A refused type after warm-up still cannot consume a decision slot.
        with pytest.raises(TradingError, match="NON_BAR_MARKET_DATA"):
            await runner.on_market_data(non_bar(MarketDataType.TICKER), event_id="post-warm")
        assert plugin.calls == 3
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()
