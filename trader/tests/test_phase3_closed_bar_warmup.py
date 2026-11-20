"""Phase 3 engineer acceptance: closed bars and stateful warm-up.

Every expected value comes from the frozen contracts: BAR_V1 visibility at
``close_time_ms``, MARKET price provenance from the triggering bar, per-symbol
warm-up counts and a restart that replays a freshly collected window.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trader.core.application.strategy_protocol import MarketData, MarketDataType
from trader.core.domain.models.order import OrderType
from trader.core.domain.models.signal import Signal, SignalType
from trader.runtime.trading_worker_main import VenueClock, _kline_observation, feed_market_once
from trader.tests.test_trading_runtime import runtime, snapshot, start_runtime
from trader.tests.test_trading_service import Clock
from trader.trading.models import NotReady, TradingError
from trader.trading.strategy import UnifiedStrategyRunner, WarmupState

SYMBOL = "BTCUSDT"
BOOK = "book:test"
INSTRUMENTS = {SYMBOL: ("BTC", "USDT")}


class Plugin:
    stateful_indicators = False
    warmup_events = 0

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
            quantity=Decimal("1"),
        )


def bar(symbol=SYMBOL, open_ms=0, price="100", interval="1m"):
    close_ms = open_ms + 60_000
    return MarketData(
        symbol=symbol,
        data_type=MarketDataType.KLINE,
        price=Decimal(price),
        volume=Decimal("1"),
        timestamp=datetime.fromtimestamp(close_ms / 1000, tz=timezone.utc),
        available_at_ms=close_ms,
        kline_open=Decimal("100"),
        kline_high=Decimal("101"),
        kline_low=Decimal("99"),
        kline_close=Decimal(price),
        kline_interval=interval,
        metadata={
            "bar_id": f"bar:{symbol}:{interval}:{open_ms}",
            "event_id": f"bar:{symbol}:{interval}:{open_ms}",
            "open_time_ms": open_ms,
            "close_time_ms": close_ms,
            "available_at_ms": close_ms,
            "interval": interval,
            "complete": True,
            "is_closed": True,
        },
    )


def runner(rt, plugin, *, warmup_events=0, warmup_source=None, instruments=None):
    return UnifiedStrategyRunner(
        rt,
        deployment_id="phase3",
        plugin=plugin,
        instruments=instruments or INSTRUMENTS,
        book_id=BOOK,
        warmup_events=warmup_events,
        warmup_source=warmup_source,
        on_block=rt.block,
    )


async def started_runtime(*, now=1_000_000):
    rt = runtime(clock=Clock(now), snap=snapshot(checked=now))
    assert await start_runtime(rt)
    return rt


@pytest.mark.asyncio
async def test_bar_is_invisible_before_available_at_ms_and_visible_at_and_after():
    rt = await started_runtime(now=1_030_000)
    plugin = Plugin()
    strategy = runner(rt, plugin)
    try:
        future = bar(open_ms=1_000_000, price="111")  # closes 1_060_000
        with pytest.raises(TradingError, match="BAR_NOT_AVAILABLE_YET"):
            await strategy.on_market_data(future, event_id=future.metadata["event_id"])
        assert plugin.calls == 0
        assert rt.exchange.placed == []

        after = bar(open_ms=960_000, price="101")  # closed at 1_020_000
        at = bar(open_ms=970_000, price="102")  # closes exactly at 1_030_000
        first = await strategy.on_market_data(after, event_id=after.metadata["event_id"])
        second = await strategy.on_market_data(at, event_id=at.metadata["event_id"])
        assert first is not None and second is not None
        assert plugin.calls == 2
        sent = rt.exchange.placed[0][0]
        assert sent.price == Decimal("101"), "MARKET price is the completed bar close"
        assert sent.price_source == "MARKET_REFERENCE_CLOSED_BAR"
        assert sent.price_at_ms == 1_020_000
        assert sent.price_reference_id == after.metadata["bar_id"]
        assert after.metadata["bar_id"] in sent.decision_id
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_feed_market_once_only_consumes_closed_klines_and_never_tickers():
    rt = await started_runtime(now=1_030_000)
    plugin = Plugin()
    rt.strategy_runner = runner(rt, plugin)

    class Broker:
        def __init__(self):
            self.rows = [
                [960_000, "100", "102", "99", "101", "3", 1_019_999],
                [1_020_000, "101", "103", "100", "999", "4", 1_079_999],
            ]

        async def get_klines(self, symbol, interval="1m", limit=200):
            assert symbol == SYMBOL
            assert interval == "1m"
            return list(self.rows)

        async def get_ticker_prices(self, symbols):
            raise AssertionError("ticker snapshots are not a decision input")

    try:
        await feed_market_once(rt, Broker(), INSTRUMENTS, interval="1m", limit=2)
        assert plugin.calls == 1
        observation = plugin.seen[0]
        assert observation.data_type is MarketDataType.KLINE
        assert observation.metadata["bar_id"] == "bar:BTCUSDT:1m:960000"
        assert observation.available_at_ms == 1_020_000
        sent = rt.exchange.placed[0][0]
        assert sent.price == Decimal("101"), "the forming bar close must be ignored"
        assert sent.price_at_ms == 1_020_000

        # The polling feed replays the same closed bar; no new decision occurs.
        await feed_market_once(rt, Broker(), INSTRUMENTS, interval="1m", limit=2)
        assert plugin.calls == 1
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_venue_clock_wall_jump_cannot_advance_decision_time():
    wall = Clock(1_060_000)
    elapsed = Clock(0)
    venue = {"now": 1_059_000}

    async def server_time():
        return venue["now"]

    clock = VenueClock(server_time, local_clock=wall, elapsed_clock=elapsed)
    await clock.sync()
    assert clock() == 1_059_000

    # A local wall-clock step is irrelevant to venue visibility.
    wall.now += 1_000
    assert clock() == 1_059_000
    wall.now += 60_000
    assert clock() == 1_059_000

    # Only the injectable monotonic elapsed source advances the clock.
    elapsed.now += 1_000
    assert clock() == 1_060_000


@pytest.mark.asyncio
async def test_venue_clock_rejects_a_forming_bar_when_local_time_leads():
    wall = Clock(1_060_000)
    elapsed = Clock(0)
    venue = {"now": 1_059_000}

    async def server_time():
        return venue["now"]

    clock = VenueClock(server_time, local_clock=wall, elapsed_clock=elapsed)
    await clock.sync()

    rt = runtime(clock=clock, snap=snapshot(checked=1_059_000))
    assert await start_runtime(rt)
    plugin = Plugin()
    rt.strategy_runner = runner(rt, plugin)

    class Broker:
        async def get_klines(self, symbol, interval="1m", limit=200):
            # Binance inclusive close 1_059_999 => BAR_V1 available 1_060_000.
            return [[1_000_000, "100", "110", "90", "105", "2", 1_059_999]]

    try:
        await feed_market_once(rt, Broker(), INSTRUMENTS)
        assert plugin.calls == 0, "a venue-forming bar must not reach the plugin"
        assert rt.exchange.placed == []

        # A wall-clock jump between calibration and decision must not reveal it.
        wall.now += 1_000
        await feed_market_once(rt, Broker(), INSTRUMENTS)
        assert plugin.calls == 0
        assert rt.exchange.placed == []

        # The venue closes the bar; the monotonic elapsed source advances too.
        venue["now"] = 1_060_000
        elapsed.now += 1_000
        await clock.sync()
        assert clock() == 1_060_000
        await feed_market_once(rt, Broker(), INSTRUMENTS)
        assert plugin.calls == 1
        assert rt.exchange.placed[0][0].price == Decimal("105")
    finally:
        await rt.stop()


def test_execution_entry_uses_the_venue_calibrated_clock():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "runtime" / "trading_worker_main.py").read_text(
        encoding="utf-8"
    )
    assert "VenueClock(broker.get_server_time)" in source
    assert "await clock.sync()" in source
    assert "clock = lambda: int(time.time() * 1000)" not in source
    assert "time.monotonic_ns()" in source
    assert "self._venue_anchor_ms + (elapsed - self._elapsed_anchor_ms)" in source


def test_binance_inclusive_close_time_is_normalized_to_bar_v1():
    observation = _kline_observation(SYMBOL, [0, "100", "101", "99", "100.5", "2", 59_999], "1m")
    assert observation.metadata["open_time_ms"] == 0
    assert observation.metadata["close_time_ms"] == 60_000
    assert observation.metadata["available_at_ms"] == 60_000
    assert observation.available_at_ms == 60_000
    assert observation.metadata["bar_id"] == "bar:BTCUSDT:1m:0"
    assert observation.timestamp == datetime.fromtimestamp(60, tz=timezone.utc)

    with pytest.raises(ValueError, match="KLINE_TIMESTAMP_CONTRACT_MISMATCH"):
        _kline_observation(SYMBOL, [0, "100", "101", "99", "100.5", "2", 12_345], "1m")


@pytest.mark.asyncio
async def test_duplicate_and_out_of_order_bars_do_not_inflate_warmup():
    rt = await started_runtime(now=1_000_000)
    plugin = Plugin()
    plugin.stateful_indicators = True
    plugin.warmup_events = 2
    strategy = runner(rt, plugin, warmup_events=2)
    try:
        newest = bar(open_ms=600_000)
        older = bar(open_ms=540_000)
        later = bar(open_ms=660_000)
        assert await strategy.on_market_data(newest, event_id=newest.metadata["event_id"]) is None
        assert await strategy.on_market_data(newest, event_id=newest.metadata["event_id"]) is None
        assert await strategy.on_market_data(older, event_id=older.metadata["event_id"]) is None
        assert plugin.calls == 1
        assert strategy.warmup_state is WarmupState.WARMING_UP
        assert strategy.instrument_warmup[SYMBOL]["observations"] == 1
        assert await strategy.on_market_data(later, event_id=later.metadata["event_id"]) is None
        assert plugin.calls == 2
        assert strategy.warmup_state is WarmupState.WARM
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_warmup_replay_deduplicates_the_window_before_counting():
    rt = await started_runtime(now=1_000_000)
    plugin = Plugin()
    plugin.stateful_indicators = True
    plugin.warmup_events = 2
    history = [bar(open_ms=600_000), bar(open_ms=600_000), bar(open_ms=660_000)]

    async def source():
        return list(history)

    strategy = runner(rt, plugin, warmup_events=2, warmup_source=source)
    try:
        assert await strategy.prepare_warmup() is True
        assert plugin.calls == 2
        assert strategy.warmup_state is WarmupState.WARM
        assert strategy.instrument_warmup[SYMBOL]["observations"] == 2
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_warmup_replay_refuses_an_unfinished_bar():
    rt = await started_runtime(now=1_000_000)
    plugin = Plugin()
    plugin.stateful_indicators = True
    plugin.warmup_events = 1
    future = bar(open_ms=1_000_000)  # closes at 1_060_000, after the clock

    async def source():
        return [future]

    strategy = runner(rt, plugin, warmup_events=1, warmup_source=source)
    try:
        assert await strategy.prepare_warmup() is False
        assert strategy.warmup_state is WarmupState.BLOCKED
        assert strategy.warmup_reason == "STRATEGY_WARMUP_BAR_NOT_AVAILABLE"
        assert plugin.calls == 0
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_warmup_failure_blocks_new_risk_with_a_specific_reason():
    rt = await started_runtime(now=1_000_000)
    plugin = Plugin()
    plugin.stateful_indicators = True
    plugin.warmup_events = 2

    async def broken_source():
        raise ConnectionError("venue history unavailable")

    strategy = runner(rt, plugin, warmup_events=2, warmup_source=broken_source)
    rt.strategy_runner = strategy
    try:
        assert await strategy.prepare_warmup() is False
        assert strategy.warmup_state is WarmupState.BLOCKED
        assert strategy.warmup_reason.startswith("STRATEGY_WARMUP_HISTORY_FAILED")
        assert rt.strategy_start_allowed is False
        with pytest.raises(NotReady):
            await strategy.on_market_data(bar(open_ms=600_000), event_id="cold")
        assert plugin.calls == 0
        assert rt.exchange.placed == []
        readiness = await rt.readiness()
        assert readiness["strategy_warmup_state"] == "BLOCKED"
        assert readiness["strategy_ready"] is False
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_restart_replays_a_fresh_window_for_every_instrument():
    rt = await started_runtime(now=1_000_000)
    first_plugin = Plugin()
    first_plugin.stateful_indicators = True
    first_plugin.warmup_events = 2
    history = [bar(open_ms=600_000), bar(open_ms=660_000)]

    async def source():
        return list(history)

    first = runner(rt, first_plugin, warmup_events=2, warmup_source=source)
    try:
        assert await first.prepare_warmup() is True
        second_plugin = Plugin()
        second_plugin.stateful_indicators = True
        second_plugin.warmup_events = 2
        second = runner(rt, second_plugin, warmup_events=2, warmup_source=source)
        assert second.warmup_state is WarmupState.PENDING
        assert await second.prepare_warmup() is True
        assert second_plugin.calls == 2
        assert first_plugin.calls == 2
        assert rt.exchange.placed == []
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_every_instrument_must_reach_its_count_before_warm():
    rt = await started_runtime(now=1_000_000)
    plugin = Plugin()
    plugin.stateful_indicators = True
    plugin.warmup_events = 2
    instruments = {SYMBOL: ("BTC", "USDT"), "ETHUSDT": ("ETH", "USDT")}
    strategy = runner(rt, plugin, warmup_events=2, instruments=instruments)
    rt.strategy_runner = strategy
    try:
        assert await strategy.on_market_data(bar(open_ms=600_000), event_id="btc-1") is None
        assert await strategy.on_market_data(bar(open_ms=660_000), event_id="btc-2") is None
        assert strategy.warmup_state is WarmupState.WARMING_UP
        states = strategy.instrument_warmup
        assert states[SYMBOL] == {"state": "WARM", "observations": 2, "required": 2}
        assert states["ETHUSDT"] == {"state": "PENDING", "observations": 0, "required": 2}
        readiness = await rt.readiness()
        assert readiness["strategy_warmup_instruments"] == states

        assert (
            await strategy.on_market_data(bar(symbol="ETHUSDT", open_ms=600_000), event_id="eth-1")
            is None
        )
        assert (
            await strategy.on_market_data(bar(symbol="ETHUSDT", open_ms=660_000), event_id="eth-2")
            is None
        )
        assert strategy.warmup_state is WarmupState.WARM
        assert all(entry["state"] == "WARM" for entry in strategy.instrument_warmup.values())
        assert rt.exchange.placed == []

        receipt = await strategy.on_market_data(bar(open_ms=720_000), event_id="btc-3")
        assert receipt is not None
        assert len(rt.exchange.placed) == 1
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_plugin_declaration_is_the_lower_bound_of_the_warmup_length():
    rt = await started_runtime(now=1_000_000)
    plugin = Plugin()
    plugin.stateful_indicators = True
    plugin.warmup_events = 3
    strategy = runner(rt, plugin, warmup_events=1)
    try:
        assert strategy.warmup_events == 3
        assert strategy.warmup_remaining == 3
        for index in range(3):
            assert (
                await strategy.on_market_data(
                    bar(open_ms=600_000 + index * 60_000), event_id=f"w{index}"
                )
                is None
            )
        assert strategy.warmup_state is WarmupState.WARM
        assert rt.exchange.placed == []
    finally:
        await rt.stop()
