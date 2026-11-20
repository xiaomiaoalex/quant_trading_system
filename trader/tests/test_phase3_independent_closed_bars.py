"""Phase 3 independent tester acceptance: closed bars, warm-up and the feed.

Every expected value below comes from the frozen contracts
(docs/INTERFACE_CONTRACTS.md 13.6 / 13.15 / 14.1 and the phase-3 acceptance
table) or from hand-computed BAR_V1 arithmetic. Nothing in this file is derived
from the implementation under test or from the engineer's own assertions.

Shared pre-existing test doubles (``Clock``, ``FakeExchange`` and the runtime
assembly fixtures) are reused as transport/clock fakes only; every asserted
value is an independent oracle.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.core.domain.models.order import OrderType
from trader.core.domain.models.signal import Signal, SignalType
from trader.runtime.trading_worker_main import _kline_observation, feed_market_once
from trader.tests.test_trading_runtime import runtime, snapshot, start_runtime
from trader.tests.test_trading_service import TEST_BOOK, Clock
from trader.trading.models import NotReady, TradingError
from trader.trading.order_spec import PRICE_SOURCE_MARKET_CLOSED_BAR, PRICE_SOURCE_SIGNAL_LIMIT
from trader.trading.strategy import UnifiedStrategyRunner, WarmupState

SYMBOL = "BTCUSDT"
MINUTE = 60_000

# Every market-data type the new-stack runner must refuse (contract 13.15).
NON_BAR_TYPES = (
    MarketDataType.TICKER,
    MarketDataType.TRADE,
    MarketDataType.DEPTH,
    MarketDataType.FUNDING,
    MarketDataType.INDEX_PRICE,
    MarketDataType.MARK_PRICE,
)


def closed_bar(
    symbol=SYMBOL,
    open_ms=0,
    *,
    o="100",
    h="110",
    l="90",
    c="100.5",
    v="3",
    complete=True,
):
    """A BAR_V1 observation: close/available at ``open_ms + interval``."""
    close_ms = open_ms + MINUTE
    bar_id = f"bar:{symbol}:1m:{open_ms}"
    return MarketData(
        symbol=symbol,
        data_type=MarketDataType.KLINE,
        price=Decimal(c),
        volume=Decimal(v),
        timestamp=datetime.fromtimestamp(close_ms / 1000, tz=timezone.utc),
        available_at_ms=close_ms,
        kline_open=Decimal(o),
        kline_high=Decimal(h),
        kline_low=Decimal(l),
        kline_close=Decimal(c),
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


class RecordingPlugin:
    """Minimal plugin double: signals are configurable, inputs are recorded."""

    def __init__(
        self,
        *,
        stateful=None,
        warmup_events=None,
        order_type=OrderType.MARKET,
        signal_price="999",
        quantity="2",
    ):
        if stateful is not None:
            self.stateful_indicators = stateful
        if warmup_events is not None:
            self.warmup_events = warmup_events
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
            # Deliberately unlike every bar close: a MARKET reference price that
            # equals this value would prove the wrong source was used.
            price=self.signal_price,
            quantity=self.quantity,
        )


class KlineBroker:
    """Venue double that only serves the public kline endpoint."""

    def __init__(self, rows):
        self.rows = dict(rows)
        self.kline_calls: list[tuple[str, str, int]] = []
        self.ticker_calls = 0

    async def get_klines(self, symbol, *, interval="1m", limit=2):
        self.kline_calls.append((symbol, interval, limit))
        return list(self.rows.get(symbol, []))

    async def get_ticker_prices(self, symbols):
        self.ticker_calls += 1
        return {symbol: Decimal("1") for symbol in symbols}


class TickerOnlyBroker:
    """Venue double without any kline endpoint."""

    def __init__(self):
        self.ticker_calls = 0

    async def get_ticker_prices(self, symbols):
        self.ticker_calls += 1
        return {symbol: Decimal("1") for symbol in symbols}


def make_runner(
    rt,
    plugin,
    *,
    warmup_events=0,
    warmup_source=None,
    instruments=None,
    allow_ticker_input=None,
):
    return UnifiedStrategyRunner(
        rt,
        deployment_id="phase3-tester",
        plugin=plugin,
        instruments=instruments or {SYMBOL: ("BTC", "USDT")},
        book_id=TEST_BOOK,
        warmup_events=warmup_events,
        warmup_source=warmup_source,
        on_block=rt.block,
        allow_ticker_input=allow_ticker_input,
    )


async def started_runtime(*, now=1_000_000):
    rt = runtime(clock=Clock(now), snap=snapshot(checked=now))
    assert await start_runtime(rt)
    return rt


async def feed(strategy, bar):
    return await strategy.on_market_data(bar, event_id=bar.metadata["event_id"])


# ---------------------------------------------------------------- 3.1 -------


@pytest.mark.asyncio
async def test_p3i_3_1_closed_bar_is_invisible_before_and_visible_at_and_after():
    now = 1_200_000
    rt = runtime(clock=Clock(now), snap=snapshot(checked=now))
    assert await start_runtime(rt)
    plugin = RecordingPlugin(stateful=False, warmup_events=0)
    strategy = make_runner(rt, plugin)
    rt.strategy_runner = strategy
    try:
        forming = closed_bar(open_ms=1_140_001)  # available at 1_200_001: one ms late
        with pytest.raises(TradingError, match="BAR_NOT_AVAILABLE_YET"):
            await strategy.on_market_data(forming, event_id=forming.metadata["event_id"])
        assert plugin.calls == 0
        assert rt.exchange.placed == []
        state = await rt.store.read("acct")
        assert len(state.orders) == 0
        assert len(state.commands) == 0
        assert len(state.holds) == 0

        after = closed_bar(open_ms=1_100_000, c="102")  # closed 40 s before now
        at_boundary = closed_bar(open_ms=1_140_000, c="101")  # available exactly at now
        first = await feed(strategy, after)
        second = await feed(strategy, at_boundary)
        assert first is not None and second is not None
        assert plugin.calls == 2
        assert [item[0].price for item in rt.exchange.placed] == [
            Decimal("102"),
            Decimal("101"),
        ]
    finally:
        await rt.stop()


def test_p3i_3_1_venue_tail_close_time_is_normalised_to_bar_v1():
    # Binance reports the inclusive last millisecond: open + interval - 1.
    row = [600_000, "10", "12", "9", "11", "5", 659_999]
    observation = _kline_observation(SYMBOL, row, "1m")
    assert observation.available_at_ms == 660_000
    assert observation.metadata["close_time_ms"] == 660_000
    assert observation.metadata["open_time_ms"] == 600_000
    assert observation.metadata["available_at_ms"] == 660_000
    assert observation.metadata["bar_id"] == "bar:BTCUSDT:1m:600000"
    assert observation.kline_close == Decimal("11")
    assert observation.price == Decimal("11")
    assert observation.timestamp == datetime.fromtimestamp(660_000 / 1000, tz=timezone.utc)
    assert observation.metadata["complete"] is True

    # A venue that already reports the exclusive boundary is accepted unchanged.
    same = _kline_observation(SYMBOL, [600_000, "10", "12", "9", "11", "5", 660_000], "1m")
    assert same.available_at_ms == 660_000

    with pytest.raises(ValueError, match="KLINE_TIMESTAMP_CONTRACT_MISMATCH"):
        _kline_observation(SYMBOL, [600_000, "10", "12", "9", "11", "5", 630_000], "1m")

    with pytest.raises(ValueError, match="UNSUPPORTED_MARKET_INTERVAL"):
        _kline_observation(SYMBOL, [600_000, "10", "12", "9", "11", "5", 659_999], "7m")


@pytest.mark.asyncio
async def test_p3i_3_1_and_3_3_feed_skips_unfinished_bars_and_never_reads_ticker():
    now = 1_000_000
    rt = runtime(clock=Clock(now), snap=snapshot(checked=now))
    assert await start_runtime(rt)
    plugin = RecordingPlugin(stateful=False, warmup_events=0, quantity="1")
    rt.strategy_runner = make_runner(rt, plugin)
    broker = KlineBroker(
        {
            SYMBOL: [
                # open, open, high, low, close, volume, venue closeTime (inclusive)
                [820_000, "100", "101", "99", "101", "5", 879_999],
                [880_000, "101", "102", "100", "102", "5", 939_999],
                [940_000, "102", "103", "101", "103", "5", 999_999],
                # Still forming: closes at 1_060_000, one minute after the clock.
                [1_000_000, "103", "999", "102", "999", "5", 1_059_999],
            ],
        }
    )
    try:
        await feed_market_once(rt, broker, {SYMBOL: ("BTC", "USDT")}, interval="1m", limit=4)
        assert [data.metadata["bar_id"] for data in plugin.seen] == [
            "bar:BTCUSDT:1m:820000",
            "bar:BTCUSDT:1m:880000",
            "bar:BTCUSDT:1m:940000",
        ]
        # The ticker endpoint is never a decision input of the execution feed.
        assert broker.ticker_calls == 0
        assert broker.kline_calls == [(SYMBOL, "1m", 4)]
        assert len(rt.exchange.placed) == 3
        for command, _cl_ord_id in rt.exchange.placed:
            assert command.price_source == PRICE_SOURCE_MARKET_CLOSED_BAR
            assert command.price in {Decimal("101"), Decimal("102"), Decimal("103")}
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_p3i_3_3_feed_without_a_kline_port_refuses_instead_of_falling_back():
    now = 1_000_000
    rt = runtime(clock=Clock(now), snap=snapshot(checked=now))
    assert await start_runtime(rt)
    plugin = RecordingPlugin(stateful=False, warmup_events=0)
    rt.strategy_runner = make_runner(rt, plugin)
    broker = TickerOnlyBroker()
    try:
        with pytest.raises(RuntimeError, match="CLOSED_BAR_SOURCE_REQUIRED"):
            await feed_market_once(rt, broker, {SYMBOL: ("BTC", "USDT")})
        assert broker.ticker_calls == 0
        assert plugin.calls == 0
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("data_type", NON_BAR_TYPES, ids=lambda item: item.value)
async def test_p3i_3_3_every_non_bar_type_is_refused_before_side_effects(data_type):
    now = 1_000_000
    rt = runtime(clock=Clock(now), snap=snapshot(checked=now))
    assert await start_runtime(rt)
    plugin = RecordingPlugin(stateful=False, warmup_events=0, quantity="1")
    strategy = make_runner(rt, plugin)
    rt.strategy_runner = strategy
    try:
        # The observation is timestamped at the decision moment, so "freshness"
        # cannot be the reason it is refused: its type is.
        observation = MarketData(
            symbol=SYMBOL,
            data_type=data_type,
            price=Decimal("55.5"),
            volume=Decimal("1"),
            timestamp=datetime.fromtimestamp(now / 1000, tz=timezone.utc),
        )
        with pytest.raises(TradingError, match="NON_BAR_MARKET_DATA"):
            await strategy.on_market_data(observation, event_id=f"non-bar-{data_type.value}")
        assert plugin.calls == 0
        assert rt.exchange.placed == []
        state = await rt.store.read("acct")
        assert len(state.orders) == 0
        assert len(state.commands) == 0
        assert len(state.holds) == 0
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_p3i_3_3_the_legacy_ticker_switch_cannot_enable_ticker_decisions():
    now = 1_000_000
    rt = runtime(clock=Clock(now), snap=snapshot(checked=now))
    assert await start_runtime(rt)
    plugin = RecordingPlugin(stateful=False, warmup_events=0)
    # The old keyword survives only as a construction no-op for callers that
    # still pass ``False``; it is not stored and cannot opt a runner in.
    runner = make_runner(rt, plugin, allow_ticker_input=False)
    assert not hasattr(runner, "allow_ticker_input")
    with pytest.raises(TradingError, match="TICKER_INPUT_IS_NOT_CONFIGURABLE"):
        make_runner(rt, plugin, allow_ticker_input=True)
    rt.strategy_runner = runner
    try:
        ticker = MarketData(
            symbol=SYMBOL,
            data_type=MarketDataType.TICKER,
            price=Decimal("55.5"),
            timestamp=datetime.fromtimestamp(now / 1000, tz=timezone.utc),
        )
        with pytest.raises(TradingError, match="NON_BAR_MARKET_DATA"):
            await runner.on_market_data(ticker, event_id="ticker-p3i-1")
        assert plugin.calls == 0
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


# ---------------------------------------------------------------- 3.2 -------


@pytest.mark.asyncio
async def test_p3i_3_2_market_reference_price_is_the_triggering_bar_close():
    now = 1_000_000
    rt = runtime(clock=Clock(now), snap=snapshot(checked=now))
    assert await start_runtime(rt)
    plugin = RecordingPlugin(stateful=False, warmup_events=0, signal_price="999", quantity="2")
    strategy = make_runner(rt, plugin)
    rt.strategy_runner = strategy
    try:
        # Four distinct OHLC values: only the close can satisfy the assertions.
        bar = closed_bar(open_ms=940_000, o="90", h="120", l="80", c="105.5")
        receipt = await feed(strategy, bar)
        assert receipt is not None
        sent = rt.exchange.placed[0][0]
        assert sent.order_type == "MARKET"
        assert sent.price == Decimal("105.5")
        assert sent.price != plugin.signal_price
        assert sent.price_source == PRICE_SOURCE_MARKET_CLOSED_BAR
        assert sent.price_at_ms == 1_000_000  # open 940_000 + 60_000
        assert sent.price_reference_id == "bar:BTCUSDT:1m:940000"
        assert sent.qty == Decimal("2")
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_p3i_3_2_limit_orders_keep_the_declared_signal_price():
    now = 1_000_000
    rt = runtime(clock=Clock(now), snap=snapshot(checked=now))
    assert await start_runtime(rt)
    plugin = RecordingPlugin(
        stateful=False,
        warmup_events=0,
        order_type=OrderType.LIMIT,
        signal_price="77.25",
        quantity="1",
    )
    strategy = make_runner(rt, plugin)
    rt.strategy_runner = strategy
    try:
        bar = closed_bar(open_ms=940_000, c="105.5")
        receipt = await feed(strategy, bar)
        assert receipt is not None
        sent = rt.exchange.placed[0][0]
        # LIMIT keeps the declared signal price; the reference time is the
        # decision clock, not the bar close.
        assert sent.order_type == "LIMIT"
        assert sent.price == Decimal("77.25")
        assert sent.price != bar.kline_close
        assert sent.price_source == PRICE_SOURCE_SIGNAL_LIMIT
        assert sent.price_at_ms == now
    finally:
        await rt.stop()


# ---------------------------------------------------------------- 3.4 -------


@pytest.mark.asyncio
async def test_p3i_3_4_deployment_maximum_wins_over_a_smaller_plugin_declaration():
    rt = await started_runtime()
    plugin = RecordingPlugin(stateful=True, warmup_events=3)
    strategy = make_runner(rt, plugin, warmup_events=5)
    rt.strategy_runner = strategy
    try:
        assert strategy.instrument_warmup[SYMBOL]["required"] == 5
        for index in range(4):
            assert await feed(strategy, closed_bar(open_ms=index * MINUTE)) is None
        assert strategy.warmup_state is WarmupState.WARMING_UP
        # The observation that completes warm-up rebuilds state only.
        assert await feed(strategy, closed_bar(open_ms=4 * MINUTE, c="106")) is None
        assert strategy.warmup_state is WarmupState.WARM
        assert rt.exchange.placed == []
        receipt = await feed(strategy, closed_bar(open_ms=5 * MINUTE, c="107"))
        assert receipt is not None
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_p3i_3_4_plugin_declaration_is_a_lower_bound():
    rt = await started_runtime()
    plugin = RecordingPlugin(stateful=True, warmup_events=5)
    strategy = make_runner(rt, plugin, warmup_events=3)
    rt.strategy_runner = strategy
    try:
        assert strategy.instrument_warmup[SYMBOL]["required"] == 5
        for index in range(3):
            assert await feed(strategy, closed_bar(open_ms=index * MINUTE)) is None
        assert strategy.warmup_state is WarmupState.WARMING_UP
        for index in range(3, 5):
            assert await feed(strategy, closed_bar(open_ms=index * MINUTE)) is None
        assert strategy.warmup_state is WarmupState.WARM
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_p3i_3_4_stateless_plugin_is_warm_with_zero_warmup():
    rt = await started_runtime()
    plugin = RecordingPlugin(stateful=False, warmup_events=0)
    strategy = make_runner(rt, plugin, warmup_events=7)
    rt.strategy_runner = strategy
    try:
        assert strategy.warmup_state is WarmupState.WARM
        assert strategy.warmup_remaining == 0
        assert strategy.instrument_warmup[SYMBOL] == {
            "state": "WARM",
            "observations": 0,
            "required": 0,
        }
        receipt = await feed(strategy, closed_bar(open_ms=940_000))
        assert receipt is not None
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


# ---------------------------------------------------------------- 3.5 -------


@pytest.mark.asyncio
async def test_p3i_3_5_warmup_is_counted_per_instrument_and_gates_the_whole_runner():
    rt = await started_runtime()
    instruments = {"BTCUSDT": ("BTC", "USDT"), "ETHUSDT": ("ETH", "USDT")}
    plugin = RecordingPlugin(stateful=True, warmup_events=2)
    strategy = make_runner(rt, plugin, warmup_events=2, instruments=instruments)
    rt.strategy_runner = strategy
    try:
        assert strategy.instrument_warmup == {
            "BTCUSDT": {"state": "PENDING", "observations": 0, "required": 2},
            "ETHUSDT": {"state": "PENDING", "observations": 0, "required": 2},
        }
        assert await feed(strategy, closed_bar("BTCUSDT", 0)) is None
        # A replayed warm-up bar must not inflate the per-instrument count.
        assert await feed(strategy, closed_bar("BTCUSDT", 0)) is None
        assert strategy.instrument_warmup["BTCUSDT"] == {
            "state": "WARMING_UP",
            "observations": 1,
            "required": 2,
        }
        assert strategy.instrument_warmup["ETHUSDT"]["state"] == "PENDING"
        assert strategy.warmup_state is WarmupState.WARMING_UP
        assert await feed(strategy, closed_bar("BTCUSDT", MINUTE)) is None
        assert strategy.instrument_warmup["BTCUSDT"]["state"] == "WARM"
        # BTC is warm, ETH is not: the whole runner stays closed.
        assert strategy.warmup_state is WarmupState.WARMING_UP
        assert strategy.warmup_remaining == 2
        assert await feed(strategy, closed_bar("ETHUSDT", 0)) is None
        assert strategy.warmup_remaining == 1
        assert await feed(strategy, closed_bar("ETHUSDT", MINUTE)) is None
        assert strategy.warmup_state is WarmupState.WARM
        assert rt.exchange.placed == []  # no warm-up observation ever traded
        receipt = await feed(strategy, closed_bar(open_ms=2 * MINUTE, c="107"))
        assert receipt is not None
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


# ---------------------------------------------------------------- 3.6 -------


class CountingSource:
    """Warm-up source double that records every (re)collection."""

    def __init__(self, bars):
        self.bars = list(bars)
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        return list(self.bars)


@pytest.mark.asyncio
async def test_p3i_3_6_restart_recollects_the_window_and_warmup_signals_never_trade():
    rt = await started_runtime()
    window = [closed_bar(open_ms=index * MINUTE) for index in range(3)]
    source = CountingSource(window)
    plugin = RecordingPlugin(stateful=True, warmup_events=3)
    strategy = make_runner(rt, plugin, warmup_events=3, warmup_source=source)
    rt.strategy_runner = strategy
    try:
        assert await strategy.prepare_warmup() is True
        assert plugin.calls == 3
        assert rt.exchange.placed == []  # warm-up signals never place orders
        assert strategy.warmup_state is WarmupState.WARM

        # A restart means a fresh plugin with empty state: the window is
        # collected again, never restored from persisted plugin state.
        restarted = RecordingPlugin(stateful=True, warmup_events=3)
        fresh = make_runner(rt, restarted, warmup_events=3, warmup_source=source)
        assert await fresh.prepare_warmup() is True
        assert source.calls == 2
        assert restarted.calls == 3
        assert rt.exchange.placed == []
        assert fresh.warmup_state is WarmupState.WARM
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_p3i_3_6_replayed_and_out_of_order_bars_do_not_duplicate_decisions():
    rt = await started_runtime()
    plugin = RecordingPlugin(stateful=False, warmup_events=0)
    strategy = make_runner(rt, plugin)
    rt.strategy_runner = strategy
    try:
        first = await feed(strategy, closed_bar(open_ms=5 * MINUTE, c="106"))
        assert first is not None
        assert len(rt.exchange.placed) == 1

        # The same completed bar replayed: the original decision is returned,
        # the plugin is not consulted again and no second order is sent.
        replay = await feed(strategy, closed_bar(open_ms=5 * MINUTE, c="106"))
        assert replay is not None
        assert replay.command_id == first.command_id
        assert plugin.calls == 1
        assert len(rt.exchange.placed) == 1
        assert strategy.instrument_warmup[SYMBOL]["observations"] == 1

        # An older out-of-order bar is stale and is dropped silently.
        assert await feed(strategy, closed_bar(open_ms=4 * MINUTE, c="105")) is None
        assert plugin.calls == 1
        assert len(rt.exchange.placed) == 1
        assert strategy.instrument_warmup[SYMBOL]["observations"] == 1

        second = await feed(strategy, closed_bar(open_ms=6 * MINUTE, c="107"))
        assert second is not None
        assert second.command_id != first.command_id
        assert plugin.calls == 2
        assert len(rt.exchange.placed) == 2
    finally:
        await rt.stop()


def _history_cases():
    async def failing_source():
        raise RuntimeError("kline endpoint down")

    now = 1_000_000

    def bars(count, **kwargs):
        return [closed_bar(open_ms=index * MINUTE, **kwargs) for index in range(count)]

    return [
        ("STRATEGY_WARMUP_HISTORY_FAILED", failing_source),
        ("STRATEGY_WARMUP_HISTORY_EMPTY", CountingSource([])),
        # Only one of the two required BTC observations is available.
        ("STRATEGY_WARMUP_HISTORY_INCOMPLETE", CountingSource(bars(1))),
        ("STRATEGY_WARMUP_UNFINISHED_BAR", CountingSource(bars(2, complete=False))),
        (
            "STRATEGY_WARMUP_BAR_NOT_AVAILABLE",
            CountingSource(bars(1) + [closed_bar(open_ms=now)]),
        ),
    ]


@pytest.mark.parametrize("reason_prefix,source", _history_cases())
@pytest.mark.asyncio
async def test_p3i_3_6_warmup_failure_reason_codes_block_new_risk_without_side_effects(
    reason_prefix,
    source,
):
    rt = await started_runtime()
    plugin = RecordingPlugin(stateful=True, warmup_events=2)
    strategy = make_runner(rt, plugin, warmup_events=2, warmup_source=source)
    rt.strategy_runner = strategy
    try:
        assert await strategy.prepare_warmup() is False
        assert strategy.warmup_state is WarmupState.BLOCKED
        assert strategy.warmup_reason.startswith(reason_prefix)
        readiness = await rt.readiness()
        assert readiness["ready"] is False
        assert readiness["strategy_warmup_state"] == "BLOCKED"
        assert readiness["strategy_warmup_reason"].startswith(reason_prefix)
        assert any(item.startswith(reason_prefix) for item in readiness["reasons"])
        # Blocked warm-up refuses further input and leaves no order side effects.
        with pytest.raises(NotReady):
            await feed(strategy, closed_bar(open_ms=940_000))
        state = await rt.store.read("acct")
        assert len(state.orders) == 0
        assert len(state.commands) == 0
        assert len(state.holds) == 0
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_p3i_3_6_stateful_plugin_without_a_warmup_length_is_blocked():
    rt = await started_runtime()
    plugin = RecordingPlugin()  # undeclared: silence is not "safe to cold start"
    strategy = make_runner(rt, plugin)
    rt.strategy_runner = strategy
    try:
        assert strategy.warmup_state is WarmupState.BLOCKED
        assert strategy.warmup_reason == "STRATEGY_WARMUP_NOT_CONFIGURED"
        assert await strategy.prepare_warmup() is False
        with pytest.raises(NotReady, match="STRATEGY_WARMUP_NOT_CONFIGURED"):
            await feed(strategy, closed_bar(open_ms=940_000))
        assert plugin.calls == 0
        assert rt.exchange.placed == []
        readiness = await rt.readiness()
        assert readiness["strategy_warmup_state"] == "BLOCKED"
        assert readiness["ready"] is False
    finally:
        await rt.stop()
