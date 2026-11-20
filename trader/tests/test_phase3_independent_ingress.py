"""Phase 3 independent tester acceptance: the closed-bar ingress validator.

The second parent return showed two bypasses of the frozen closed-bar boundary:

* a TICKER in the warm-up history satisfied a stateful plugin's window and the
  next completed bar could trade;
* a KLINE carrying only ``open_time_ms`` was consumed before its contract close,
  using the observation timestamp as the reference time.

Contract 13.15 requires one rule at both the live and the history ingress:

* only ``MarketDataType.KLINE``;
* an explicit ``open_time_ms`` plus a supported interval, so
  ``close_time_ms = open_time_ms + interval_ms``;
* every supplied close/availability field equals that close, and an explicit
  positive ``kline_close`` is required;
* ``timestamp`` and ``data.price`` never substitute for the missing fields;
* the whole warm-up window is validated before the plugin sees any element, and
  a rejected window leaves the plugin, the counters and the account untouched.

Every expected value below is hand-computed from BAR_V1 (equality is visible) or
read from the frozen contract; none is copied from the engineer's assertions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.core.domain.models.order import OrderType
from trader.core.domain.models.signal import Signal, SignalType
from trader.tests.test_trading_runtime import runtime, snapshot, start_runtime
from trader.tests.test_trading_service import TEST_BOOK, Clock
from trader.trading import books as book_ledger
from trader.trading.models import NotReady, TradingError
from trader.trading.order_spec import PRICE_SOURCE_MARKET_CLOSED_BAR, PRICE_SOURCE_SIGNAL_LIMIT
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


def bar(
    open_ms,
    *,
    close="104.25",
    market_price=None,
    complete=True,
    with_open=True,
    interval="1m",
    with_close_fields=True,
    metadata_close_offset=0,
    attr_available_offset=0,
    with_kline_close=True,
    timestamp_at="close",
):
    """A KLINE that is complete unless a test deliberately removes a field.

    ``metadata_close_offset`` / ``attr_available_offset`` let a test make the
    two independently supplied close values disagree with each other or with
    ``open_time_ms + interval``.
    """
    close_ms = open_ms + MINUTE
    price = Decimal(close if market_price is None else market_price)
    metadata = {
        "event_id": f"bar:{open_ms}",
        "bar_id": f"bar:{SYMBOL}:1m:{open_ms}",
        "complete": complete,
        "is_closed": complete,
    }
    if with_open:
        metadata["open_time_ms"] = open_ms
    if interval is not None:
        metadata["interval"] = interval
    shown_at = open_ms if timestamp_at == "open" else close_ms
    fields = {
        "symbol": SYMBOL,
        "data_type": MarketDataType.KLINE,
        "price": price,
        "volume": Decimal("3"),
        "timestamp": datetime.fromtimestamp(shown_at / 1000, tz=timezone.utc),
        "kline_open": Decimal("100"),
        "kline_high": Decimal("106"),
        "kline_low": Decimal("99"),
        "kline_interval": interval,
        "metadata": metadata,
    }
    if with_close_fields:
        if metadata_close_offset is not None:
            metadata["close_time_ms"] = close_ms + metadata_close_offset
        if attr_available_offset is not None:
            fields["available_at_ms"] = close_ms + attr_available_offset
            metadata["available_at_ms"] = close_ms + attr_available_offset
    if with_kline_close:
        fields["kline_close"] = Decimal(close)
    return MarketData(**fields)


def non_bar(data_type):
    return MarketData(
        symbol=SYMBOL,
        data_type=data_type,
        price=Decimal("104.25"),
        volume=Decimal("1"),
        timestamp=datetime.fromtimestamp(NOW / 1000, tz=timezone.utc),
    )


class WarmPlugin:
    """Stateful probe: a window of ``events`` bars must be replayed first."""

    stateful_indicators = True

    def __init__(self, events=1):
        self.warmup_events = events
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


class GatePlugin:
    """Stateless probe: one MARKET or LIMIT signal with a deliberately odd price."""

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


def make_runner(rt, plugin, history=None):
    async def source():
        return list(history or [])

    return UnifiedStrategyRunner(
        rt,
        deployment_id="p3i-ingress",
        plugin=plugin,
        instruments=INSTRUMENTS,
        book_id=TEST_BOOK,
        warmup_events=0,
        warmup_source=source if history is not None else None,
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


async def assert_no_effects(rt, plugin):
    assert await counts(rt, plugin) == {
        "plugin": 0,
        "exchange": 0,
        "orders": 0,
        "commands": 0,
        "holds": 0,
    }


async def prepare_blocked(history, events=1, code="", prefix=""):
    """Build a runner over ``history``, prepare it, and check the blocked state."""
    plugin = WarmPlugin(events=events)
    rt = runtime(clock=Clock(NOW), snap=snapshot(checked=NOW))
    assert await start_runtime(rt)
    runner = make_runner(rt, plugin, history)
    rt.strategy_runner = runner
    try:
        prepared = await runner.prepare_warmup()
        assert prepared is False
        assert runner.warmup_state.value == "BLOCKED"
        if code:
            assert runner.warmup_reason == code
        if prefix:
            assert runner.warmup_reason.startswith(prefix)
        assert plugin.calls == 0
        assert runner.instrument_warmup[SYMBOL] == {
            "state": "BLOCKED",
            "observations": 0,
            "required": events,
        }
        await assert_no_effects(rt, plugin)
        # A blocked runner refuses a perfectly valid bar instead of trading late.
        with pytest.raises(NotReady):
            await runner.on_market_data(bar(NOW - MINUTE), event_id="blocked-follow-up")
        assert plugin.calls == 0
        await assert_no_effects(rt, plugin)
        return runner
    finally:
        await rt.stop()


# ------------------------------------------------ history: non-bar and window --


@pytest.mark.asyncio
@pytest.mark.parametrize("data_type", NON_BAR_TYPES, ids=lambda item: item.value)
async def test_p3i_warmup_history_refuses_every_non_bar_type(data_type):
    await prepare_blocked([non_bar(data_type)], events=1, code="STRATEGY_WARMUP_NON_BAR_DATA")


@pytest.mark.asyncio
async def test_p3i_warmup_valid_prefix_then_non_bar_is_not_partially_fed():
    # One valid bar would already satisfy ``events=1``; a validator that fed
    # incrementally would warm and then count the ticker.
    history = [bar(0), non_bar(MarketDataType.TICKER)]
    await prepare_blocked(history, events=1, code="STRATEGY_WARMUP_NON_BAR_DATA")


@pytest.mark.asyncio
async def test_p3i_warmup_valid_prefix_then_invalid_bar_is_not_partially_fed():
    history = [bar(0), bar(MINUTE, with_kline_close=False)]
    await prepare_blocked(
        history,
        events=1,
        code="STRATEGY_WARMUP_INVALID_BAR:BAR_CLOSE_PRICE_REQUIRED",
    )


MALFORMED_HISTORY = (
    ("no_open", {"with_open": False}, "BAR_OPEN_TIME_REQUIRED"),
    ("no_interval", {"interval": None}, "BAR_INTERVAL_REQUIRED"),
    ("unsupported_interval", {"interval": "3m"}, "BAR_INTERVAL_REQUIRED"),
    ("no_close_fields", {"with_close_fields": False}, "BAR_CLOSE_TIME_REQUIRED"),
    ("close_contradiction", {"metadata_close_offset": MINUTE}, "BAR_TIMESTAMP_INCONSISTENT"),
    ("no_close_price", {"with_kline_close": False}, "BAR_CLOSE_PRICE_REQUIRED"),
    ("zero_close", {"close": "0", "market_price": "104.25"}, "INVALID_BAR_CLOSE"),
    ("negative_close", {"close": "-1", "market_price": "104.25"}, "INVALID_BAR_CLOSE"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "code"),
    [(row[1], row[2]) for row in MALFORMED_HISTORY],
    ids=[row[0] for row in MALFORMED_HISTORY],
)
async def test_p3i_warmup_history_refuses_malformed_bar_fields(mutation, code):
    await prepare_blocked(
        [bar(0, **mutation)],
        events=1,
        code=f"STRATEGY_WARMUP_INVALID_BAR:{code}",
    )


@pytest.mark.asyncio
async def test_p3i_warmup_history_refuses_a_future_bar():
    # open NOW -> contract close NOW + 60s, after the decision clock NOW.
    await prepare_blocked(
        [bar(NOW)],
        events=1,
        code="STRATEGY_WARMUP_BAR_NOT_AVAILABLE",
    )


@pytest.mark.asyncio
async def test_p3i_warmup_history_refuses_an_explicitly_unfinished_bar():
    await prepare_blocked(
        [bar(0, complete=False)],
        events=1,
        code="STRATEGY_WARMUP_UNFINISHED_BAR",
    )


@pytest.mark.asyncio
async def test_p3i_warmup_history_duplicate_entries_do_not_satisfy_the_window():
    # Replayed duplicates are one bar; two copies must not warm a two-event
    # plugin and must not feed the plugin at all.
    await prepare_blocked(
        [bar(0), bar(0)],
        events=2,
        prefix="STRATEGY_WARMUP_HISTORY_INCOMPLETE",
    )


@pytest.mark.asyncio
async def test_p3i_warmup_history_short_window_is_incomplete():
    await prepare_blocked(
        [bar(0)],
        events=2,
        prefix="STRATEGY_WARMUP_HISTORY_INCOMPLETE",
    )


# ------------------------------------------------------- history: valid control --


@pytest.mark.asyncio
async def test_p3i_warmup_valid_history_warms_without_trading_then_trades_once():
    plugin = WarmPlugin(events=2)
    rt = runtime(clock=Clock(NOW), snap=snapshot(checked=NOW))
    assert await start_runtime(rt)
    runner = make_runner(rt, plugin, [bar(0), bar(MINUTE)])
    rt.strategy_runner = runner
    try:
        assert await runner.prepare_warmup() is True
        assert runner.warmup_state.value == "WARM"
        assert plugin.calls == 2
        assert [item.metadata["open_time_ms"] for item in plugin.seen] == [0, MINUTE]
        assert runner.instrument_warmup[SYMBOL] == {
            "state": "WARM",
            "observations": 2,
            "required": 2,
        }
        # Replaying history rebuilds indicators only; it must not trade.
        assert await counts(rt, plugin) == {
            "plugin": 2,
            "exchange": 0,
            "orders": 0,
            "commands": 0,
            "holds": 0,
        }

        observation = bar(1_140_000, close="100")
        receipt = await runner.on_market_data(
            observation,
            event_id=observation.metadata["bar_id"],
        )
        assert receipt is not None
        assert plugin.calls == 3
        assert len(rt.exchange.placed) == 1
        command, _cl_ord_id = rt.exchange.placed[0]
        assert command.price == Decimal("100")
        assert command.price_source == PRICE_SOURCE_MARKET_CLOSED_BAR
        assert command.price_at_ms == NOW
        assert command.price_reference_id == "bar:BTCUSDT:1m:1140000"
        state = await rt.store.read(rt.account_id)
        assert len(state.orders) == 1
        assert len(state.commands) == 1
        assert len(state.holds) == 1
        # 1 * 100 * (1 + 0.001) on both reservation layers.
        assert state.holds[receipt.cl_ord_id].amount == Decimal("100.1")
        assert state.books[TEST_BOOK].holds[receipt.cl_ord_id].amount == Decimal("100.1")
        assert book_ledger.conservation_mismatch(state) == {}

        # The same bar replayed is the same decision, not a second trade.
        again = await runner.on_market_data(
            observation,
            event_id=observation.metadata["bar_id"],
        )
        assert again is not None
        assert again.cl_ord_id == receipt.cl_ord_id
        assert plugin.calls == 3
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


# ----------------------------------------------------------- live: field rules ---


@pytest.mark.asyncio
async def test_p3i_live_parent_counterexample_missing_close_at_659500():
    # Exactly the parent's second counterexample: timestamp at the open, no
    # available/close/kline_close fields, decision before the contract close.
    plugin = GatePlugin()
    rt = runtime(clock=Clock(659_500), snap=snapshot(checked=659_500))
    assert await start_runtime(rt)
    runner = make_runner(rt, plugin)
    rt.strategy_runner = runner
    try:
        broken = bar(
            600_000,
            with_close_fields=False,
            with_kline_close=False,
            timestamp_at="open",
        )
        with pytest.raises(TradingError, match="BAR_CLOSE_TIME_REQUIRED"):
            await runner.on_market_data(broken, event_id="parent-missing-close")
        await assert_no_effects(rt, plugin)
        # The timestamp alone is never a substitute, even when it names the close.
        broken_at_close = bar(
            600_000,
            with_close_fields=False,
            with_kline_close=False,
            timestamp_at="close",
        )
        with pytest.raises(TradingError, match="BAR_CLOSE_TIME_REQUIRED"):
            await runner.on_market_data(broken_at_close, event_id="timestamp-close")
        await assert_no_effects(rt, plugin)
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_p3i_live_missing_close_price_and_contradiction_are_refused():
    plugin = GatePlugin()
    rt = runtime(clock=Clock(659_500), snap=snapshot(checked=659_500))
    assert await start_runtime(rt)
    runner = make_runner(rt, plugin)
    rt.strategy_runner = runner
    try:
        no_price = bar(600_000, with_kline_close=False)
        with pytest.raises(TradingError, match="BAR_CLOSE_PRICE_REQUIRED"):
            await runner.on_market_data(no_price, event_id="no-close-price")
        contradiction = bar(600_000, metadata_close_offset=MINUTE)
        with pytest.raises(TradingError, match="BAR_TIMESTAMP_INCONSISTENT"):
            await runner.on_market_data(contradiction, event_id="contradiction")
        unsupported = bar(600_000, interval="3m")
        with pytest.raises(TradingError, match="BAR_INTERVAL_REQUIRED"):
            await runner.on_market_data(unsupported, event_id="unsupported-interval")
        await assert_no_effects(rt, plugin)
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_p3i_live_exact_boundary_provenance_and_replay():
    plugin = GatePlugin(signal_price="999", quantity="2")
    rt = runtime(clock=Clock(NOW), snap=snapshot(checked=NOW))
    assert await start_runtime(rt)
    runner = make_runner(rt, plugin)
    rt.strategy_runner = runner
    try:
        # open 1_140_001 + 1m = 1_200_001, one millisecond after the decision
        # clock; the account snapshot is fresh for this clock, so only bar
        # availability can refuse it.
        early = bar(1_140_001, close="100")
        with pytest.raises(TradingError, match="BAR_NOT_AVAILABLE_YET"):
            await runner.on_market_data(early, event_id="one-ms-early")
        await assert_no_effects(rt, plugin)

        # open 1_140_000 + 1m = 1_200_000: equality is visible.
        observation = bar(1_140_000, close="100")
        receipt = await runner.on_market_data(
            observation,
            event_id=observation.metadata["bar_id"],
        )
        assert receipt is not None
        command, _cl_ord_id = rt.exchange.placed[0]
        assert command.price == Decimal("100")
        assert command.price_source == PRICE_SOURCE_MARKET_CLOSED_BAR
        assert command.price_at_ms == NOW
        assert command.price_reference_id == "bar:BTCUSDT:1m:1140000"
        state = await rt.store.read(rt.account_id)
        assert len(state.orders) == 1
        assert len(state.commands) == 1
        assert len(state.holds) == 1
        assert state.holds[receipt.cl_ord_id].amount == Decimal("200.2")
        assert state.books[TEST_BOOK].holds[receipt.cl_ord_id].amount == Decimal("200.2")
        assert book_ledger.conservation_mismatch(state) == {}

        again = await runner.on_market_data(
            observation,
            event_id=observation.metadata["bar_id"],
        )
        assert again is not None
        assert again.cl_ord_id == receipt.cl_ord_id
        assert plugin.calls == 1
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_p3i_live_limit_keeps_the_declared_price():
    plugin = GatePlugin(order_type=OrderType.LIMIT, signal_price="99.5", quantity="1")
    rt = runtime(clock=Clock(NOW), snap=snapshot(checked=NOW))
    assert await start_runtime(rt)
    runner = make_runner(rt, plugin)
    rt.strategy_runner = runner
    try:
        observation = bar(600_000, close="104.25")
        receipt = await runner.on_market_data(
            observation,
            event_id=observation.metadata["bar_id"],
        )
        assert receipt is not None
        command, _cl_ord_id = rt.exchange.placed[0]
        assert command.order_type == "LIMIT"
        assert command.price == Decimal("99.5")
        assert command.price_source == PRICE_SOURCE_SIGNAL_LIMIT
        assert command.price_reference_id == "bar:BTCUSDT:1m:600000"
        assert command.price_at_ms == NOW
        assert command.price != Decimal("104.25")
    finally:
        await rt.stop()
