"""Acceptance tests for immutable datasets and the event-driven spot runtime."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from trader.trading.backtest import EventBacktestRuntime, StrategySignal, validate_dataset_set
from trader.trading.datasets import INTERVAL_MS, freeze_dataset
from trader.trading.jobs import BacktestApplication, MemoryResearchStore, ResearchWorker


def identity(**overrides):
    values = dict(
        venue="binance",
        market_type="SPOT",
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        interval="1m",
        source="fixture",
        version="1",
    )
    values.update(overrides)
    return values


def bars(count=4, *, start=0, open_price="100", close_price="100", interval_ms=60_000):
    result = []
    for index in range(count):
        opened = start + index * interval_ms
        result.append(
            {
                "open_time_ms": opened,
                "close_time_ms": opened + interval_ms,
                "available_at_ms": opened + interval_ms,
                "open": open_price,
                "high": str(max(Decimal(open_price), Decimal(close_price))),
                "low": str(min(Decimal(open_price), Decimal(close_price))),
                "close": close_price,
                "volume": "10",
            }
        )
    return result


def dataset(bar_list=None, **overrides):
    interval = overrides.get("interval", "1m")
    return freeze_dataset(
        identity(**overrides),
        bar_list or bars(interval_ms=INTERVAL_MS.get(interval, 60_000)),
    )


def signal(side="BUY", qty="1", order_type="MARKET", limit_price=None):
    return StrategySignal(
        symbol="BTCUSDT",
        side=side,
        qty=Decimal(qty),
        order_type=order_type,
        limit_price=Decimal(limit_price) if limit_price is not None else None,
    )


def run(dataset_data, strategy, initial_cash="1000", fee_bps="10", slippage_bps="0"):
    return EventBacktestRuntime(
        datasets=[dataset_data],
        strategy=strategy,
        initial_cash=Decimal(initial_cash),
        fee_bps=Decimal(fee_bps),
        slippage_bps=Decimal(slippage_bps),
    ).run()


def test_dataset_identity_is_immutable_and_content_addressed():
    first = dataset()
    second = dataset()
    changed = dataset(bars(close_price="101"))
    different_version = dataset(version="2")
    different_interval = dataset(interval="5m")

    assert first["dataset_id"] == second["dataset_id"]
    assert first["dataset_id"] != changed["dataset_id"]
    assert first["dataset_id"] != different_version["dataset_id"]
    assert first["dataset_id"] != different_interval["dataset_id"]
    assert first["identity"]["market_type"] == "SPOT"
    assert first["identity"]["version"] == "1"
    assert first["quality"]["bar_count"] == 4


def test_dataset_rejects_unfinished_or_mixed_bars():
    unfinished = bars(2)
    unfinished[1]["available_at_ms"] = unfinished[1]["close_time_ms"] - 1
    with pytest.raises(ValueError):
        dataset(unfinished)

    mixed = bars(3)
    mixed[2]["open_time_ms"] += 60_000
    mixed[2]["close_time_ms"] += 60_000
    mixed[2]["available_at_ms"] += 60_000
    with pytest.raises(ValueError):
        dataset(mixed)

    with pytest.raises(ValueError):
        dataset(interval="2m")


def test_dataset_set_rejects_mixed_calendars_intervals_and_markets():
    first = dataset()
    other_symbol = dataset(symbol="ETHUSDT", base_asset="ETH")
    validate_dataset_set([first, other_symbol])

    other_interval = dataset(interval="5m")
    with pytest.raises(ValueError):
        validate_dataset_set([first, other_interval])

    shifted = dataset(bars(4, start=60_000))
    with pytest.raises(ValueError):
        validate_dataset_set([first, shifted])

    with pytest.raises(ValueError):
        validate_dataset_set(
            [first, dict(first, identity=dict(first["identity"], market_type="FUTURES"))]
        )


def test_buy_fee_and_mark_to_market_match_acceptance_numbers():
    data = dataset(
        [
            {
                "open_time_ms": 0,
                "close_time_ms": 60_000,
                "available_at_ms": 60_000,
                "open": "100",
                "high": "100",
                "low": "100",
                "close": "100",
                "volume": "1",
            },
            {
                "open_time_ms": 60_000,
                "close_time_ms": 120_000,
                "available_at_ms": 120_000,
                "open": "100",
                "high": "100",
                "low": "100",
                "close": "100",
                "volume": "1",
            },
            {
                "open_time_ms": 120_000,
                "close_time_ms": 180_000,
                "available_at_ms": 180_000,
                "open": "110",
                "high": "110",
                "low": "110",
                "close": "110",
                "volume": "1",
            },
        ]
    )

    def strategy(context, bar):
        if bar["open_time_ms"] == 0:
            return [signal("BUY", "1")]
        if bar["open_time_ms"] == 60_000 and context.position("BTCUSDT") == 1:
            return [signal("SELL", "1")]
        return []

    result = run(data, strategy)

    assert result.initial_cash == Decimal("1000")
    assert result.cash == Decimal("1009.79")
    assert result.positions["BTCUSDT"] == Decimal("0")
    assert result.fees == Decimal("0.21")
    assert result.total_fees == Decimal("0.21")
    assert result.equity_curve[1] == Decimal("999.9")
    assert result.equity_curve[2] == Decimal("1009.79")


def test_signal_at_close_fills_at_next_open_with_slippage():
    data = dataset(
        [
            {
                "open_time_ms": 0,
                "close_time_ms": 60_000,
                "available_at_ms": 60_000,
                "open": "100",
                "high": "100",
                "low": "100",
                "close": "100",
                "volume": "1",
            },
            {
                "open_time_ms": 60_000,
                "close_time_ms": 120_000,
                "available_at_ms": 120_000,
                "open": "102",
                "high": "102",
                "low": "102",
                "close": "102",
                "volume": "1",
            },
        ]
    )

    def strategy(context, bar):
        return [signal("BUY", "1")] if bar["open_time_ms"] == 0 else []

    no_slippage = run(data, strategy, slippage_bps="0")
    slippage = run(data, strategy, slippage_bps="10")

    assert no_slippage.fills[0]["price"] == Decimal("102")
    assert slippage.fills[0]["price"] == Decimal("102.102")


def test_limit_orders_never_break_their_limit_price():
    data = dataset(
        [
            {
                "open_time_ms": 0,
                "close_time_ms": 60_000,
                "available_at_ms": 60_000,
                "open": "100",
                "high": "100",
                "low": "100",
                "close": "100",
                "volume": "1",
            },
            {
                "open_time_ms": 60_000,
                "close_time_ms": 120_000,
                "available_at_ms": 120_000,
                "open": "99",
                "high": "103",
                "low": "98",
                "close": "102",
                "volume": "1",
            },
            {
                "open_time_ms": 120_000,
                "close_time_ms": 180_000,
                "available_at_ms": 180_000,
                "open": "104",
                "high": "105",
                "low": "103",
                "close": "104",
                "volume": "1",
            },
        ]
    )

    def strategy(context, bar):
        if bar["open_time_ms"] == 0:
            return [signal("BUY", "1", "LIMIT", "101")]
        return []

    result = run(data, strategy)
    assert len(result.fills) == 1
    assert result.fills[0]["price"] == Decimal("99")
    assert result.fills[0]["price"] <= Decimal("101")

    def untouched(context, bar):
        if bar["open_time_ms"] == 0:
            return [signal("BUY", "1", "LIMIT", "97")]
        return []

    assert run(data, untouched).fills == []


def test_estimated_equity_is_produced_without_any_fill():
    data = dataset(bars(3, close_price="100"))
    prices = iter(["100", "110", "90"])
    data = dataset(
        [
            {
                "open_time_ms": index * 60_000,
                "close_time_ms": (index + 1) * 60_000,
                "available_at_ms": (index + 1) * 60_000,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": "1",
            }
            for index, price in enumerate(prices)
        ]
    )
    result = run(data, lambda context, bar: [], initial_cash="500")
    assert result.fills == []
    assert len(result.equity_curve) == 3
    assert result.equity_curve == [Decimal("500"), Decimal("500"), Decimal("500")]


def test_short_and_oversell_are_rejected_not_converted():
    data = dataset(bars(2))

    def short(context, bar):
        return [signal("SHORT", "1")] if bar["open_time_ms"] == 0 else []

    result = run(data, short)
    assert result.fills == []
    assert result.rejections == {"UNSUPPORTED_SPOT_INTENT": 1}

    def oversell(context, bar):
        return [signal("SELL", "1")] if bar["open_time_ms"] == 0 else []

    result = run(data, oversell)
    assert result.fills == []
    assert result.rejections == {"INSUFFICIENT_POSITION": 1}


def test_risk_sees_position_after_the_first_fill():
    data = dataset(bars(4))

    def strategy(context, bar):
        if bar["open_time_ms"] == 0:
            return [signal("BUY", "2")]
        if bar["open_time_ms"] == 60_000:
            # First fill happened at this bar's open, so risk may approve two sells.
            return [signal("SELL", "2")]
        return []

    result = run(data, strategy)
    assert result.risk["approved"] == 2
    assert result.risk["rejected"] == 0
    assert result.positions["BTCUSDT"] == Decimal("0")
    assert result.fills[-1]["qty"] == Decimal("2")


def test_results_are_deterministic_and_do_not_use_wall_clock():
    data = dataset(bars(3))

    def strategy(context, bar):
        return [signal("BUY", "0.5")] if bar["open_time_ms"] == 0 else []

    first = run(data, strategy)
    second = run(data, strategy)
    assert first.equity_curve == second.equity_curve
    assert first.fills == second.fills
    assert first.manifest_hash == second.manifest_hash


@pytest.mark.asyncio
async def test_worker_claims_executes_and_publishes_once():
    store = MemoryResearchStore()
    job = await store.enqueue(
        {"dataset_ids": ["ds_x"], "strategy": {"module_path": "trader.strategies.ema_cross_btc"}}
    )
    worker = ResearchWorker(store, owner="worker-1")
    calls = []

    async def execute(record):
        calls.append(record["run_id"])
        return {"net_value": "1000"}

    finished = await worker.run_once(execute)
    assert finished["status"] == "SUCCEEDED"
    assert finished["result"] == {"net_value": "1000"}
    assert calls == [job["run_id"]]
    assert await worker.run_once(execute) is None


@pytest.mark.asyncio
async def test_worker_failure_is_recorded_and_retry_can_succeed():
    store = MemoryResearchStore()
    job = await store.enqueue({"dataset_ids": ["ds_x"]})
    worker = ResearchWorker(store, owner="worker-1")

    async def boom(record):
        raise RuntimeError("engine exploded")

    failed = await worker.run_once(boom)
    assert failed["status"] == "FAILED"
    assert "engine exploded" in failed["error"]

    retry = ResearchWorker(store, owner="worker-2")
    published = []

    async def ok(record):
        published.append(record["run_id"])
        return {"ok": True}

    finished = await retry.run_once(ok)
    assert finished["status"] == "SUCCEEDED"
    assert published == [job["run_id"]]


@pytest.mark.asyncio
async def test_stale_worker_cannot_publish_results():
    store = MemoryResearchStore()
    job = await store.enqueue({"dataset_ids": ["ds_x"]})
    first = await store.claim("worker-1", lease_seconds=0)
    assert first["run_id"] == job["run_id"]

    second = await store.claim("worker-2", lease_seconds=30)
    assert second["run_id"] == job["run_id"]
    assert second["fencing_token"] > first["fencing_token"]

    with pytest.raises(RuntimeError):
        await store.finish(
            job["run_id"], "worker-1", first["fencing_token"], result={"stale": True}
        )
    published = await store.finish(
        job["run_id"], "worker-2", second["fencing_token"], result={"ok": True}
    )
    assert published["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_cancel_requested_is_honoured_at_heartbeat():
    store = MemoryResearchStore()
    job = await store.enqueue({"dataset_ids": ["ds_x"]})
    claimed = await store.claim("worker-1", lease_seconds=30)
    await store.cancel(job["run_id"])
    assert await store.heartbeat(job["run_id"], "worker-1", claimed["fencing_token"]) is True
    await store.finish(job["run_id"], "worker-1", claimed["fencing_token"], cancelled=True)
    assert (await store.get(job["run_id"]))["status"] == "CANCELLED"


def test_vectorbt_cannot_produce_admission_evidence():
    import asyncio as _asyncio

    from trader.trading.jobs import BacktestApplication

    store = MemoryResearchStore()
    application = BacktestApplication(store)

    async def submit():
        await store.register_dataset(identity(), bars())
        data = await store.list_datasets()
        manifest = {
            "dataset_ids": [data[0]["dataset_id"]],
            "strategy": {"module_path": "trader.strategies.ema_cross_btc", "params": {}},
            "engine": "vectorbt",
            "purpose": "admission_evidence",
            "initial_cash": "1000",
            "fee_bps": "10",
            "slippage_bps": "5",
        }
        return await application.submit(manifest)

    with pytest.raises(ValueError):
        _asyncio.run(submit())


@pytest.mark.asyncio
async def test_research_executor_runs_frozen_manifest_with_installed_plugin():
    from trader.trading.jobs import ResearchWorker
    from trader.trading.research import ResearchExecutor

    store = MemoryResearchStore()
    # Enough completed bars for the EMA plugin's slow period.
    prices = [str(100 + index) for index in range(40)] + [str(140 - index) for index in range(20)]
    bar_list = [
        {
            "open_time_ms": index * 60_000,
            "close_time_ms": (index + 1) * 60_000,
            "available_at_ms": (index + 1) * 60_000,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": "1",
        }
        for index, price in enumerate(prices)
    ]
    data = await store.register_dataset(identity(), bar_list)
    application = BacktestApplication(store)
    job = await application.submit(
        {
            "dataset_ids": [data["dataset_id"]],
            "strategy": {"module_path": "trader.strategies.ema_cross_btc", "params": {}},
            "initial_cash": "1000",
            "fee_bps": "10",
            "slippage_bps": "5",
            "engine": "event_runtime",
            "purpose": "research_screening",
        }
    )
    worker = ResearchWorker(store, owner="research-1")
    finished = await worker.run_once(ResearchExecutor(store).execute)
    assert finished["status"] == "SUCCEEDED", finished
    result = finished["result"]
    assert result["engine"] == "event_runtime"
    assert result["admissible"] is False  # screening is not admission evidence
    assert isinstance(result["net_value"], str)
    assert len(result["equity_curve"]) == len(bar_list)
    assert result["manifest_hash"]


@pytest.mark.asyncio
async def test_research_executor_refuses_non_event_engines():
    from trader.trading.research import ResearchExecutor

    store = MemoryResearchStore()
    executor = ResearchExecutor(store)
    with pytest.raises(ValueError):
        await executor.execute(
            {
                "manifest": {
                    "dataset_ids": ["ds_missing"],
                    "strategy": {"module_path": "trader.strategies.ema_cross_btc"},
                    "engine": "vectorbt",
                    "purpose": "admission_evidence",
                }
            }
        )


@pytest.mark.asyncio
async def test_reports_without_event_runtime_evidence_are_marked_legacy():
    store = MemoryResearchStore()
    job = await store.enqueue({"dataset_ids": ["ds_x"]})
    await store.claim("worker-1", lease_seconds=30)
    await store.finish(job["run_id"], "worker-1", 1, result={"net_value": "1000"})
    legacy = await store.get(job["run_id"])
    assert legacy["model"] == "legacy"

    job2 = await store.enqueue({"dataset_ids": ["ds_y"], "runtime_version": "spot_bar_v2_shared"})
    await store.claim("worker-2", lease_seconds=30)
    await store.finish(
        job2["run_id"],
        "worker-2",
        1,
        result={
            "engine": "event_runtime",
            "runtime_version": "spot_bar_v2_shared",
            "net_value": "1000",
        },
    )
    current = await store.get(job2["run_id"])
    assert current["model"] == "event_runtime"


def test_results_do_not_depend_on_the_machine_clock(monkeypatch):
    import time

    data = dataset(bars(3))

    def strategy(context, bar):
        return [signal("BUY", "0.5")] if bar["open_time_ms"] == 0 else []

    first = run(data, strategy)
    monkeypatch.setattr(time, "time", lambda: 1_900_000_000)
    second = run(data, strategy)
    assert first.equity_curve == second.equity_curve
    assert first.fills == second.fills
    assert first.manifest_hash == second.manifest_hash


def test_reports_separate_amount_and_percentage_drawdown():
    from trader.trading.backtest import EventBacktestRuntime

    data = dataset(
        [
            {
                "open_time_ms": 0,
                "close_time_ms": 60_000,
                "available_at_ms": 60_000,
                "open": "100",
                "high": "100",
                "low": "100",
                "close": "100",
                "volume": "1",
            },
            {
                "open_time_ms": 60_000,
                "close_time_ms": 120_000,
                "available_at_ms": 120_000,
                "open": "100",
                "high": "100",
                "low": "100",
                "close": "100",
                "volume": "1",
            },
            {
                "open_time_ms": 120_000,
                "close_time_ms": 180_000,
                "available_at_ms": 180_000,
                "open": "80",
                "high": "80",
                "low": "80",
                "close": "80",
                "volume": "1",
            },
        ]
    )

    def strategy(context, bar):
        return [signal("BUY", "1")] if bar["open_time_ms"] == 0 else []

    result = EventBacktestRuntime(
        datasets=[data], strategy=strategy, initial_cash=Decimal("1000"), fee_bps=Decimal("0")
    ).run()
    assert result.max_drawdown_amount > 0
    assert result.max_drawdown_pct > 0
    assert result.max_drawdown_pct < 1
