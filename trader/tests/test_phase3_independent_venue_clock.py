"""Phase 3 independent tester regression: venue-anchored decision clock.

The reviewer's counterexample (``P3-R1-3.1-CLOCK``) is that a local wall clock
running ahead of the venue lets a still-forming bar reach the strategy. The
first fix attempt kept a fixed venue-minus-local offset, which a later wall step
could still defeat; the accepted boundary is a *venue sample anchor* advanced
only by an independently injectable **monotonic elapsed** source. This file
derives its own oracle from ``docs/INTERFACE_CONTRACTS.md`` 13.15 and BAR_V1,
with hand-picked values that differ from the engineer's regression:

* BAR_V1: ``available_at_ms = close_time_ms = open_time_ms + interval_ms``; a bar
  is visible iff ``available_at_ms <= decision clock``.
* 13.15: the execution decision clock samples the venue, then advances with
  monotonic elapsed time. Local wall clock steps (forward or backward) must not
  move visibility; ``local_clock`` is accepted for construction compatibility
  and never participates. A failing time source or a backwards monotonic source
  blocks new risk. Strategy visibility, feed and warm-up share that domain.

So with local = venue + 1000 ms and a bar whose ``available_at_ms`` equals the
local clock, the venue has not closed the bar: the feed must skip it, the
strategy must raise ``BAR_NOT_AVAILABLE_YET``, and no order, command or hold may
exist. A wall jump between calibration and the feed must not change that. Once
the *elapsed* source reaches ``available_at_ms`` the same bar must become usable
exactly once, including the exact-equality boundary.

Every expected number below is hand-computed; nothing is copied from the
engineer's assertions or read out of the implementation.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.core.domain.models.order import OrderType
from trader.core.domain.models.signal import Signal, SignalType
from trader.runtime.trading_worker_main import (
    VenueClock,
    _market_loop,
    build_runtime,
    feed_market_once,
    run,
)
from trader.tests.test_trading_runtime import runtime, snapshot, start_runtime
from trader.tests.test_trading_service import TEST_BOOK, Clock, FakeExchange
from trader.trading.models import NotReady, TradingError
from trader.trading.order_spec import PRICE_SOURCE_MARKET_CLOSED_BAR
from trader.trading.store import MemoryTradingStore
from trader.trading.strategy import UnifiedStrategyRunner

SYMBOL = "BTCUSDT"
MINUTE = 60_000
INSTRUMENTS = {SYMBOL: ("BTC", "USDT")}
VENUE_LAG_MS = 1_000


class Elapsed:
    """Injectable monotonic elapsed source, in milliseconds."""

    def __init__(self, now=700_000):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, ms):
        self.now += ms


def bar(open_ms, *, o="100", h="106", l="99", c="104.25", v="7"):
    """BAR_V1 observation closing exactly ``open_ms + MINUTE``."""
    close_ms = open_ms + MINUTE
    bar_id = f"bar:{SYMBOL}:1m:{open_ms}"
    return MarketData(
        symbol=SYMBOL,
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
            "complete": True,
            "is_closed": True,
        },
    )


class ProbePlugin:
    """Records every decision input; the signal price is never a bar price.

    Declared stateless with a zero-length warm-up: ``describe_plugin_warmup``
    treats an undeclared plugin as stateful (silence must not mean "safe to
    trade cold"), which would block the runner and make every feed a no-op.
    """

    stateful_indicators = False
    warmup_events = 0

    def __init__(self, *, signal_price="999", quantity="1", on_call=None):
        self.signal_price = Decimal(signal_price)
        self.quantity = Decimal(quantity)
        self.on_call = on_call
        self.calls = 0
        self.seen: list[MarketData] = []

    async def on_market_data(self, data):
        self.calls += 1
        self.seen.append(data)
        if self.on_call is not None:
            self.on_call()
        return Signal(
            signal_type=SignalType.BUY,
            order_type=OrderType.MARKET,
            symbol=data.symbol,
            price=self.signal_price,
            quantity=self.quantity,
        )


class RowsBroker:
    """Venue double: public klines only, ticker reads are a test failure."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0
        self.ticker_reads = 0

    async def get_klines(self, symbol, *, interval="1m", limit=2):
        self.calls += 1
        return [list(row) for row in self.rows]

    async def get_ticker_prices(self, symbols):
        self.ticker_reads += 1
        raise AssertionError("a ticker snapshot is not a decision input")


def runner_for(rt, plugin):
    return UnifiedStrategyRunner(
        rt,
        deployment_id="p3i-venue-clock",
        plugin=plugin,
        instruments=INSTRUMENTS,
        book_id=TEST_BOOK,
        warmup_events=0,
        on_block=rt.block,
    )


async def wait_until(predicate, *, timeout=3.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition was not reached before timeout")
        await asyncio.sleep(0.005)


# ------------------------------------------------ venue clock arithmetic ---


@pytest.mark.asyncio
async def test_p3i_venue_clock_advances_only_by_monotonic_elapsed():
    local = Clock(5_000_000)
    venue = {"now": 5_000_000 - VENUE_LAG_MS}
    elapsed = Elapsed(700_000)

    async def server_time():
        return venue["now"]

    clock = VenueClock(server_time, local_clock=local, elapsed_clock=elapsed)
    assert clock.synced is False
    await clock.sync()
    assert clock.synced is True
    # Local leads by one second; the decision clock must read the venue second.
    assert local() == 5_000_000
    assert clock() == 4_999_000
    assert clock() < local()

    # A forward wall step alone must not move the decision clock.
    local.now = 6_000_000
    assert clock() == 4_999_000
    # Nor may a backward wall step: wall time is not the domain at all.
    local.now = 2_000_000
    assert clock() == 4_999_000

    # Only the monotonic elapsed source advances visibility.
    elapsed.advance(250)
    assert clock() == 4_999_250
    elapsed.advance(749)
    assert clock() == 4_999_999

    # Resampling anchors a new venue sample; skipped local drift is discarded.
    venue["now"] = 5_000_000
    local.now = 9_000_000
    elapsed.advance(1)
    await clock.sync()
    assert clock() == 5_000_000
    elapsed.advance(100)
    assert clock() == 5_000_100

    # A monotonic source that steps backwards is not a safe domain.
    elapsed.now = 700_000
    with pytest.raises(NotReady, match="VENUE_CLOCK_UNSTABLE"):
        clock()

    # A source that steps back *during* a sync is refused as well.
    async def jumping_time():
        elapsed.now = 650_000
        return 5_000_500

    unstable = VenueClock(jumping_time, local_clock=local, elapsed_clock=elapsed)
    with pytest.raises(NotReady, match="VENUE_CLOCK_UNSTABLE"):
        await unstable.sync()
    assert unstable.synced is False


@pytest.mark.asyncio
async def test_p3i_unsynced_clock_refuses_the_feed_instead_of_using_local_time():
    rt = runtime(clock=Clock(1_500_000), snap=snapshot(checked=1_500_000))
    assert await start_runtime(rt)
    plugin = ProbePlugin()
    rt.strategy_runner = runner_for(rt, plugin)
    broker = RowsBroker([[1_440_000, "100", "101", "99", "100.5", "2", 1_499_999]])
    try:
        # A time source that never produced an anchor must fail closed; a
        # fallback to local wall time would reintroduce the skew defect.
        rt.clock = VenueClock(
            lambda: 1_500_000,
            local_clock=lambda: 1_500_000,
            elapsed_clock=Elapsed(0),
        )
        with pytest.raises(NotReady, match="VENUE_CLOCK_NOT_SYNCED"):
            await feed_market_once(rt, broker, INSTRUMENTS)
        assert broker.calls == 0
        assert plugin.calls == 0
        assert rt.exchange.placed == []
        state = await rt.store.read(rt.account_id)
        assert len(state.orders) == 0
        assert len(state.commands) == 0
        assert len(state.holds) == 0
    finally:
        rt.clock = Clock(1_500_000)
        await rt.stop()


# ------------------------------------- reviewer counterexample, own values ---


@pytest.mark.asyncio
async def test_p3i_3_1_wall_jump_cannot_admit_a_forming_bar():
    local = Clock(1_200_000)
    venue = {"now": 1_200_000 - VENUE_LAG_MS}
    elapsed = Elapsed(1_000_000)

    async def server_time():
        return venue["now"]

    clock = VenueClock(server_time, local_clock=local, elapsed_clock=elapsed)
    await clock.sync()
    assert clock() == 1_199_000

    rt = runtime(clock=clock, snap=snapshot(checked=1_199_000))
    assert await start_runtime(rt)
    plugin = ProbePlugin(signal_price="999", quantity="3")
    rt.strategy_runner = runner_for(rt, plugin)
    # open 1_140_000 + 60_000 = available 1_200_000 = local now, venue -1 s.
    broker = RowsBroker([[1_140_000, "100", "106", "99", "104.25", "7", 1_199_999]])
    try:
        # The parent counterexample: the wall clock steps forward by exactly the
        # skew after calibration. Neither the venue nor elapsed time moved, so
        # the decision clock must stay on the venue second.
        local.now += VENUE_LAG_MS
        assert local() == 1_201_000
        assert clock() == 1_199_000

        await feed_market_once(rt, broker, INSTRUMENTS)
        assert plugin.calls == 0
        assert broker.ticker_reads == 0
        assert rt.exchange.placed == []
        state = await rt.store.read(rt.account_id)
        assert len(state.orders) == 0
        assert len(state.commands) == 0
        assert len(state.holds) == 0

        forming = bar(1_140_000)
        with pytest.raises(TradingError, match="BAR_NOT_AVAILABLE_YET"):
            await rt.strategy_runner.on_market_data(forming, event_id=forming.metadata["event_id"])
        assert plugin.calls == 0
        assert rt.exchange.placed == []

        # The venue actually closes the bar: elapsed crosses the 1 s gap, so the
        # exact-equality boundary is reached without any wall-clock movement.
        elapsed.advance(VENUE_LAG_MS)
        assert clock() == 1_200_000
        assert clock() == local() - VENUE_LAG_MS

        await feed_market_once(rt, broker, INSTRUMENTS)
        assert plugin.calls == 1
        assert plugin.seen[0].available_at_ms == 1_200_000
        assert len(rt.exchange.placed) == 1
        command, _cl_ord_id = rt.exchange.placed[0]
        assert command.price == Decimal("104.25")
        assert command.price_at_ms == 1_200_000
        assert command.price_reference_id == "bar:BTCUSDT:1m:1140000"
        assert command.price_source == PRICE_SOURCE_MARKET_CLOSED_BAR
        # The plugin's own signal price must never become a MARKET reference.
        assert command.price != plugin.signal_price

        # A replayed identical bar must not trade twice, even at the boundary.
        await feed_market_once(rt, broker, INSTRUMENTS)
        assert plugin.calls == 1
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


# ------------------------------------------------ feed loop recalibration ---


@pytest.mark.asyncio
async def test_p3i_market_loop_recalibrates_before_every_feed():
    local = Clock(1_200_000)
    venue = {"now": 1_199_000}
    elapsed = Elapsed(2_000_000)

    async def server_time():
        return venue["now"]

    class CountingVenueClock(VenueClock):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.syncs = 0

        async def sync(self):
            self.syncs += 1
            return await super().sync()

    class DriftingBroker:
        """Venue closes the bar between the first and second poll."""

        def __init__(self):
            self.calls = 0
            self.decision_ms: list[int] = []

        async def get_klines(self, symbol, *, interval="1m", limit=2):
            self.calls += 1
            self.decision_ms.append(clock())
            if self.calls == 1:
                venue["now"] = 1_200_000
                elapsed.advance(VENUE_LAG_MS)
            return [[1_140_000, "100", "106", "99", "104.25", "7", 1_199_999]]

    clock = CountingVenueClock(server_time, local_clock=local, elapsed_clock=elapsed)
    await clock.sync()
    assert clock.syncs == 1

    rt = runtime(clock=clock, snap=snapshot(checked=1_199_000))
    assert await start_runtime(rt)
    plugin = ProbePlugin(signal_price="999", quantity="2")
    rt.strategy_runner = runner_for(rt, plugin)
    broker = DriftingBroker()
    task = asyncio.create_task(_market_loop(rt, broker, INSTRUMENTS, "1m", 0.02))
    try:
        await wait_until(
            lambda: plugin.calls == 1 and len(rt.exchange.placed) == 1 and clock.syncs >= 4
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await rt.stop()

    # First poll ran on the venue second (bar still forming); every later poll
    # ran after the loop resampled the venue and saw the closed bar.
    assert broker.decision_ms[0] == 1_199_000
    assert all(value == 1_200_000 for value in broker.decision_ms[1:])
    assert clock.syncs >= 4
    assert plugin.calls == 1
    assert plugin.seen[0].available_at_ms == 1_200_000
    assert len(rt.exchange.placed) == 1
    assert rt.exchange.placed[0][0].price == Decimal("104.25")


@pytest.mark.asyncio
async def test_p3i_market_loop_blocks_risk_when_the_venue_time_source_fails():
    local = Clock(1_300_000)
    venue = {"now": 1_300_000, "fail": False}

    async def server_time():
        if venue["fail"]:
            raise RuntimeError("venue time unavailable")
        return venue["now"]

    clock = VenueClock(server_time, local_clock=local, elapsed_clock=Elapsed(3_000_000))
    await clock.sync()

    rt = runtime(clock=clock, snap=snapshot(checked=1_300_000))
    assert await start_runtime(rt)
    plugin = ProbePlugin()
    rt.strategy_runner = runner_for(rt, plugin)
    broker = RowsBroker([[1_240_000, "100", "101", "99", "100.5", "2", 1_299_999]])

    venue["fail"] = True
    task = asyncio.create_task(_market_loop(rt, broker, INSTRUMENTS, "1m", 0.02))
    try:
        await wait_until(lambda: "STRATEGY_OR_MARKET_DATA_FAILED" in rt.last_reasons)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await rt.stop()

    # The calibration failure precedes the feed: no bar was read, no decision
    # was made, and the runtime is not ready for new risk.
    assert broker.calls == 0
    assert plugin.calls == 0
    assert rt.exchange.placed == []
    assert rt.ready is False


# ------------------------------------------ production assembly / input gate ---


@pytest.mark.asyncio
async def test_p3i_production_assembly_has_no_ticker_opt_in():
    async def snapshot_provider(account_id):
        return snapshot(checked=2_000_000)

    plugin = ProbePlugin()
    rt = build_runtime(
        store=MemoryTradingStore(),
        exchange=FakeExchange(),
        account_id="acct",
        snapshot_provider=snapshot_provider,
        clock=Clock(2_000_000),
        strategy_plugin=plugin,
        instruments=INSTRUMENTS,
        deployment_id="p3i-venue-clock",
        strategy_id="strategy-1",
    )
    runner = rt.strategy_runner
    assert runner is not None
    # There is no stored opt-in at all: the gate is unconditional, so neither a
    # production assembly nor a direct caller can carry a ticker flag.
    assert not hasattr(runner, "allow_ticker_input")

    ticker = MarketData(
        symbol=SYMBOL,
        data_type=MarketDataType.TICKER,
        price=Decimal("100.5"),
        volume=Decimal("0"),
        timestamp=datetime.fromtimestamp(2_000_000 / 1000, tz=timezone.utc),
    )
    with pytest.raises(TradingError, match="NON_BAR_MARKET_DATA"):
        await runner.on_market_data(ticker, event_id="ticker-2-000-000")
    assert plugin.calls == 0
    assert rt.exchange.placed == []

    # The legacy keyword is a construction no-op for ``False`` and refuses to
    # enable anything for ``True``.
    with pytest.raises(TradingError, match="TICKER_INPUT_IS_NOT_CONFIGURABLE"):
        UnifiedStrategyRunner(
            rt,
            deployment_id="p3i-legacy-direct",
            plugin=plugin,
            instruments=INSTRUMENTS,
            book_id=TEST_BOOK,
            warmup_events=0,
            on_block=rt.block,
            allow_ticker_input=True,
        )

    # Wiring cannot inject an uncalibrated local clock: the argument is
    # mandatory and has no default.
    clock_parameter = inspect.signature(build_runtime).parameters["clock"]
    assert clock_parameter.default is inspect.Parameter.empty

    # ``run()`` must calibrate before assembling and start the feed loop last.
    source = inspect.getsource(run)
    calibrated = source.index("VenueClock(broker.get_server_time)")
    synced = source.index("await clock.sync()")
    assembled = source.index("build_runtime(")
    market_loop = source.index("_market_loop(")
    assert calibrated < synced < assembled < market_loop
