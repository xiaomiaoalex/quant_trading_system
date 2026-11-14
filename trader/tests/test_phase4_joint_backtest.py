"""Phase 4 engineer tests: joint backtests, frozen manifests and consistency."""

from __future__ import annotations

from decimal import Decimal

import pytest

from trader.tests.fakes.research_eligibility import always_eligible, trust_memory_dataset
from trader.trading import core as trading_core
from trader.trading.backtest import EventBacktestRuntime, StrategySignal
from trader.trading.datasets import digest, freeze_dataset
from trader.trading.jobs import (
    BacktestApplication,
    MemoryResearchStore,
    ResearchWorker,
    normalize_backtest_manifest,
)
from trader.trading.models import SETTLEMENT_BASE
from trader.trading.order_spec import (
    PRICE_SOURCE_BACKTEST_CLOSED_BAR,
    PRICE_SOURCE_MARKET_CLOSED_BAR,
    build_order_spec,
)
from trader.trading.research import ResearchExecutor
from trader.trading.version import SPOT_RUNTIME_VERSION


def identity(symbol="BTCUSDT", base_asset="BTC"):
    return {
        "venue": "binance",
        "market_type": "SPOT",
        "symbol": symbol,
        "base_asset": base_asset,
        "quote_asset": "USDT",
        "interval": "1m",
        "source": "fixture",
        "version": "1",
    }


def bars(prices):
    rows = []
    for index, price in enumerate(prices):
        opened = index * 60_000
        rows.append(
            {
                "open_time_ms": opened,
                "close_time_ms": opened + 60_000,
                "available_at_ms": opened + 60_000,
                "open": str(price),
                "high": str(price),
                "low": str(price),
                "close": str(price),
                "volume": "1",
            }
        )
    return rows


# Enough observations for the EMA plugin (slow_period=26) to cross upwards once.
CROSSING_PRICES = [100] * 26 + [101, 102, 103, 104, 105]


async def register(store, prices=None, symbol="BTCUSDT", base_asset="BTC"):
    data = await store.register_dataset(
        identity(symbol, base_asset), bars(prices or CROSSING_PRICES)
    )
    link = trust_memory_dataset(store, data["dataset_id"], symbol=symbol)
    return data, link


def joint_manifest(dataset_id, *, allocations=None, order_sizes=("2", "1")):
    allocations = allocations or {"book:alpha": "100", "book:beta": "900"}
    return {
        "dataset_ids": [dataset_id],
        "strategies": [
            {
                "strategy_id": "beta",
                "module_path": "trader.strategies.ema_cross_btc",
                "params": {"order_size": order_sizes[1]},
            },
            {
                "strategy_id": "alpha",
                "module_path": "trader.strategies.ema_cross_btc",
                "params": {"order_size": order_sizes[0]},
            },
        ],
        "book_allocations": allocations,
        "initial_cash": "1000",
        "fee_bps": "10",
        "slippage_bps": "0",
        "engine": "event_runtime",
        "purpose": "research_screening",
    }


async def submit_and_run(store, manifest):
    application = BacktestApplication(store, eligibility=always_eligible)
    job = await application.submit(manifest)
    worker = ResearchWorker(store, owner="phase4-worker")
    finished = await worker.run_once(ResearchExecutor(store).execute)
    assert finished["run_id"] == job["run_id"]
    assert finished["status"] == "SUCCEEDED", finished.get("error")
    return job, finished["result"]


def books_by_id(result):
    return {item["book_id"]: item for item in result["books"]}


@pytest.mark.asyncio
async def test_a_joint_manifest_is_frozen_with_books_costs_runtime_and_datasets():
    """4.3 / 4.10: the frozen document carries every input needed to rerun."""
    store = MemoryResearchStore()
    data, link = await register(store)
    manifest = joint_manifest(data["dataset_id"])
    application = BacktestApplication(store, eligibility=always_eligible)

    job = await application.submit(manifest)
    frozen = job["manifest"]

    assert frozen["dataset_ids"] == [data["dataset_id"]]
    assert frozen["runtime_version"] == SPOT_RUNTIME_VERSION
    assert frozen["initial_cash"] == "1000"
    assert frozen["fee_bps"] == "10"
    assert frozen["slippage_bps"] == "0"
    assert frozen["book_allocations"] == {"book:alpha": "100", "book:beta": "900"}
    # Strategies are sorted by their stable id, not by caller order.
    assert [item["strategy_id"] for item in frozen["strategies"]] == ["alpha", "beta"]
    assert [item["book_id"] for item in frozen["strategies"]] == [
        "book:alpha",
        "book:beta",
    ]
    assert frozen["provenance"] == {data["dataset_id"]: link["provenance_hash"]}
    assert job["manifest_hash"] == digest(frozen)

    # Mutating the caller's nested input after submission cannot change the
    # stored manifest or its hash.
    manifest["strategies"][0]["params"]["order_size"] = "999"
    manifest["book_allocations"]["book:alpha"] = "1"
    manifest["dataset_ids"].append("ds_injected")
    stored = store.jobs[job["run_id"]]["manifest"]
    assert stored == frozen
    assert stored["strategies"][0]["strategy_id"] == "alpha"
    assert stored["strategies"][0]["params"]["order_size"] == "2"
    assert stored["book_allocations"] == {"book:alpha": "100", "book:beta": "900"}


@pytest.mark.asyncio
async def test_a_joint_backtest_isolates_books_and_exports_equity_budgets_and_fees():
    """4.1 / 4.2 / 4.7: shared account, isolated books, explicit refusal."""
    store = MemoryResearchStore()
    data, _ = await register(store)
    _, result = await submit_and_run(store, joint_manifest(data["dataset_id"]))

    books = books_by_id(result)
    assert set(books) == {"UNALLOCATED", "book:alpha", "book:beta"}

    alpha = books["book:alpha"]
    beta = books["book:beta"]
    # alpha may not spend beta's money: its 2-unit request costs about 202
    # against a 100 unit book. It is refused and keeps its whole budget.
    assert result["rejections"].get("BOOK_INSUFFICIENT_AVAILABLE_BALANCE") == 1
    assert alpha["balances"]["USDT"] == "100"
    assert alpha["positions"] == {}
    assert Decimal(alpha["equity"]) == Decimal("100")
    assert alpha["fees"] == "0"
    assert alpha["allocation"] == "100"
    assert alpha["held"]["USDT"] == "0"
    assert alpha["available"]["USDT"] == "100"

    # beta can still trade: 900 - 1 * 102 - 0.102 fee = 797.898 quote + 1 BTC.
    assert beta["balances"]["USDT"] == "797.898"
    assert beta["positions"] == {"BTCUSDT": "1"}
    assert Decimal(beta["equity"]) == Decimal("902.898")
    assert beta["fees"] == "0.102"
    assert beta["allocation"] == "900"

    assert result["fills"] == [
        {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "qty": "1",
            "price": "102",
            "fee": "0.102",
            "bar_index": 27,
            "book_id": "book:beta",
            "strategy_id": "beta",
            "cl_ord_id": "bar-26-BTCUSDT-2",
        }
    ]
    assert result["rejected_intents"] == [
        {
            "strategy_id": "alpha",
            "book_id": "book:alpha",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "qty": "2",
            "order_type": "MARKET",
            "code": "BOOK_INSUFFICIENT_AVAILABLE_BALANCE",
        }
    ]

    assert Decimal(result["total_fees"]) == Decimal("0.102")
    assert result["book_fees"] == {
        "UNALLOCATED": "0",
        "book:alpha": "0",
        "book:beta": "0.102",
    }
    assert sum(Decimal(value) for value in result["book_fees"].values()) == Decimal(
        result["total_fees"]
    )
    account_equity = Decimal(result["cash"]) + Decimal("1") * Decimal("105")
    assert sum(Decimal(item["equity"]) for item in result["books"]) == account_equity


@pytest.mark.asyncio
async def test_equal_allocations_let_both_books_trade_with_independent_positions():
    """4.1: both strategies trade the same account, each inside its own book."""
    store = MemoryResearchStore()
    data, _ = await register(store)
    _, result = await submit_and_run(
        store,
        joint_manifest(
            data["dataset_id"],
            allocations={"book:alpha": "500", "book:beta": "500"},
            order_sizes=("1", "1"),
        ),
    )

    books = books_by_id(result)
    for book_id in ("book:alpha", "book:beta"):
        book = books[book_id]
        assert book["positions"] == {"BTCUSDT": "1"}
        assert book["balances"]["USDT"] == "397.898"
        assert book["fees"] == "0.102"
        assert Decimal(book["equity"]) == Decimal("502.898")
    assert Decimal(result["positions"]["BTCUSDT"]) == Decimal("2")
    assert sum(Decimal(value) for value in result["book_fees"].values()) == Decimal(
        result["total_fees"]
    )


@pytest.mark.asyncio
async def test_the_same_frozen_manifest_is_deterministic_in_fresh_runtimes():
    """4.4: identical frozen input, identical hashes and identical results."""
    first_store = MemoryResearchStore()
    first_data, _ = await register(first_store)
    first_job, first_result = await submit_and_run(
        first_store, joint_manifest(first_data["dataset_id"])
    )

    second_store = MemoryResearchStore()
    second_data, _ = await register(second_store)
    second_job, second_result = await submit_and_run(
        second_store, joint_manifest(second_data["dataset_id"])
    )

    assert first_data["dataset_id"] == second_data["dataset_id"]
    assert first_job["manifest_hash"] == second_job["manifest_hash"]
    assert first_job["manifest"] == second_job["manifest"]
    assert first_result == second_result
    assert first_result["manifest_hash"] == first_job["manifest_hash"]
    assert first_result["runtime_manifest_hash"] == second_result["runtime_manifest_hash"]

    changed_store = MemoryResearchStore()
    changed_data, _ = await register(changed_store)
    changed_job = await BacktestApplication(changed_store, eligibility=always_eligible).submit(
        joint_manifest(
            changed_data["dataset_id"],
            allocations={"book:alpha": "400", "book:beta": "600"},
        )
    )
    assert changed_job["manifest_hash"] != first_job["manifest_hash"]


def test_budget_occupancy_is_exported_while_an_order_is_still_pending():
    """4.2: held vs available budget is visible per book before a fill."""
    data = freeze_dataset(identity(), bars([100, 100, 100]))

    def strategy(context, bar):
        if context.bar_index != 2:
            return []
        return [
            StrategySignal(
                symbol="BTCUSDT",
                side="BUY",
                qty=Decimal("1"),
                order_type="MARKET",
                strategy_id="alpha",
            )
        ]

    result = EventBacktestRuntime(
        datasets=[data],
        strategy=strategy,
        initial_cash=Decimal("1000"),
        fee_bps=Decimal("0"),
        strategy_books={"alpha": "book:alpha", "beta": "book:beta"},
        initial_allocations={"book:alpha": Decimal("400"), "book:beta": Decimal("600")},
    ).run()
    books = {item["book_id"]: item for item in result.books}
    alpha = books["book:alpha"]
    assert alpha["held"]["USDT"] == "100"
    assert alpha["available"]["USDT"] == "300"
    assert alpha["balances"]["USDT"] == "400"
    assert Decimal(alpha["equity"]) == Decimal("400")
    assert books["book:beta"]["held"]["USDT"] == "0"
    assert books["book:beta"]["available"]["USDT"] == "600"


@pytest.mark.asyncio
async def test_frozen_manifest_mutation_and_conflicting_inputs_are_refused():
    """4.3 / 4.4: no in-place edit, no silent manifest rewrite on retry."""
    store = MemoryResearchStore()
    data, link = await register(store)
    application = BacktestApplication(store, eligibility=always_eligible)
    job = await application.submit(joint_manifest(data["dataset_id"]))
    run_id = job["run_id"]

    # A tampered nested allocation is not retried under the old hash. The
    # mutation keeps the allocation sum valid, so only the frozen hash check
    # can notice it.
    store.jobs[run_id]["status"] = "FAILED"
    store.jobs[run_id]["manifest"]["book_allocations"]["book:alpha"] = "101"
    store.jobs[run_id]["manifest"]["book_allocations"]["book:beta"] = "899"
    with pytest.raises(ValueError, match="Frozen manifest hash mismatch"):
        await application.retry(run_id)
    assert store.jobs[run_id]["manifest"]["book_allocations"]["book:alpha"] == "101"
    assert store.jobs[run_id]["status"] == "FAILED"

    # A stale provenance link is refused by retry and by execution.
    store.jobs[run_id]["manifest"] = job["manifest"]
    store.register_dataset_link(
        data["dataset_id"], dict(store.links[data["dataset_id"]], provenance_hash="c" * 64)
    )
    with pytest.raises(ValueError, match="DATASET_PROVENANCE_MISMATCH"):
        await application.retry(run_id)
    with pytest.raises(ValueError, match="Frozen provenance mismatch"):
        await ResearchExecutor(store).execute(
            {"manifest": job["manifest"], "manifest_hash": job["manifest_hash"]}
        )
    assert link["provenance_hash"] != "c" * 64

    # Conflicting inputs never reach the queue.
    conflicting = joint_manifest(data["dataset_id"])
    conflicting["provenance"] = {data["dataset_id"]: "d" * 64}
    with pytest.raises(ValueError, match="DATASET_PROVENANCE_MISMATCH"):
        await application.submit(conflicting)
    unbalanced = joint_manifest(
        data["dataset_id"], allocations={"book:alpha": "100", "book:beta": "800"}
    )
    with pytest.raises(ValueError, match="sum to the initial cash"):
        await application.submit(unbalanced)
    duplicate = joint_manifest(
        data["dataset_id"], allocations={"book:alpha": "500", "book:beta": "500"}
    )
    duplicate["strategies"][0]["strategy_id"] = "alpha"
    with pytest.raises(ValueError, match="Duplicate strategy_id"):
        await application.submit(duplicate)
    unallocated = joint_manifest(data["dataset_id"])
    unallocated["book_allocations"] = {"book:alpha": "1000"}
    with pytest.raises(ValueError, match="Book allocations must match"):
        await application.submit(unallocated)


@pytest.mark.asyncio
async def test_a_valid_retry_keeps_the_frozen_manifest_and_hash():
    store = MemoryResearchStore()
    data, _ = await register(store)
    application = BacktestApplication(store, eligibility=always_eligible)
    job = await application.submit(joint_manifest(data["dataset_id"]))
    store.jobs[job["run_id"]]["status"] = "FAILED"

    requeued = await application.retry(job["run_id"])
    assert requeued["status"] == "QUEUED"
    assert requeued["manifest"] == job["manifest"]
    assert requeued["manifest_hash"] == job["manifest_hash"]


def test_build_order_spec_and_the_simulator_share_live_command_semantics(monkeypatch):
    """4.5 / 4.6: manifest orders keep live symbol/side/qty/price semantics."""
    from trader.trading import backtest as backtest_module

    commands = []
    executions = []
    real_reserve = trading_core.reserve_order
    real_book_execution = trading_core.book_execution

    def spy_reserve(state, command, cl_ord_id, policy, now_ms):
        commands.append(command)
        return real_reserve(state, command, cl_ord_id, policy, now_ms)

    def spy_book_execution(state, execution, policy):
        executions.append(execution)
        return real_book_execution(state, execution, policy)

    monkeypatch.setattr(backtest_module.trading_core, "reserve_order", spy_reserve)
    monkeypatch.setattr(backtest_module.trading_core, "book_execution", spy_book_execution)

    data = freeze_dataset(
        identity(),
        [
            {
                "open_time_ms": 0,
                "close_time_ms": 60_000,
                "available_at_ms": 60_000,
                "open": "100",
                "high": "100",
                "low": "95",
                "close": "100",
                "volume": "1",
            },
            {
                "open_time_ms": 60_000,
                "close_time_ms": 120_000,
                "available_at_ms": 120_000,
                "open": "100",
                "high": "100",
                "low": "90",
                "close": "100",
                "volume": "1",
            },
        ],
    )

    def strategy(context, bar):
        if context.bar_index != 0:
            return []
        return [
            StrategySignal(symbol="BTCUSDT", side="BUY", qty=Decimal("1"), order_type="MARKET"),
            StrategySignal(
                symbol="BTCUSDT",
                side="BUY",
                qty=Decimal("1"),
                order_type="LIMIT",
                limit_price=Decimal("99"),
            ),
        ]

    result = EventBacktestRuntime(
        datasets=[data], strategy=strategy, initial_cash=Decimal("1000"), fee_bps=Decimal("0")
    ).run()
    assert len(commands) == 2
    assert len(executions) == 2 == len(result.fills)

    market, limit = commands
    live_market = build_order_spec(
        symbol="BTCUSDT",
        side="BUY",
        order_type="MARKET",
        qty=Decimal("1"),
        limit_price=None,
        reference_price=Decimal("100"),
        reference_price_source=PRICE_SOURCE_MARKET_CLOSED_BAR,
        reference_at_ms=60_000,
        decision_at_ms=0,
        time_in_force="GTC",
        book_id="book:backtest",
        price_source=PRICE_SOURCE_MARKET_CLOSED_BAR,
        settlement=SETTLEMENT_BASE,
    )
    assert (market.symbol, market.side, market.qty, market.price) == (
        live_market.symbol,
        live_market.side,
        live_market.qty,
        live_market.price,
    )
    live_limit = build_order_spec(
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        qty=Decimal("1"),
        limit_price=Decimal("99"),
        reference_price=None,
        reference_price_source=PRICE_SOURCE_BACKTEST_CLOSED_BAR,
        reference_at_ms=60_000,
        decision_at_ms=0,
        time_in_force="GTC",
        book_id="book:backtest",
        settlement=SETTLEMENT_BASE,
    )
    assert (limit.symbol, limit.side, limit.qty, limit.price) == (
        live_limit.symbol,
        live_limit.side,
        live_limit.qty,
        live_limit.price,
    )
    assert result.fills[0]["price"] == Decimal("100")
    assert result.fills[1]["price"] == Decimal("99")


@pytest.mark.asyncio
async def test_a_joint_manifest_limit_signal_keeps_its_declared_price(monkeypatch):
    """4.5: the manifest-driven plugin path carries the LIMIT price unchanged."""
    from trader.core.domain.models.order import OrderType
    from trader.core.domain.models.signal import Signal, SignalType
    from trader.trading import research as research_module

    class LimitPlugin:
        def __init__(self, params):
            self.emit = bool(params.get("emit"))
            self.sent = False

        async def initialize(self, params):
            return None

        async def on_market_data(self, market):
            if not self.emit or self.sent:
                return None
            self.sent = True
            return Signal(
                signal_type=SignalType.BUY,
                order_type=OrderType.LIMIT,
                symbol=market.symbol,
                price=Decimal("99"),
                quantity=Decimal("1"),
            )

    monkeypatch.setattr(
        research_module, "load_plugin", lambda module_path, params: LimitPlugin(params)
    )

    store = MemoryResearchStore()
    data = await store.register_dataset(
        identity(),
        [
            {
                "open_time_ms": 0,
                "close_time_ms": 60_000,
                "available_at_ms": 60_000,
                "open": "100",
                "high": "100",
                "low": "95",
                "close": "100",
                "volume": "1",
            },
            {
                "open_time_ms": 60_000,
                "close_time_ms": 120_000,
                "available_at_ms": 120_000,
                "open": "100",
                "high": "100",
                "low": "90",
                "close": "100",
                "volume": "1",
            },
        ],
    )
    trust_memory_dataset(store, data["dataset_id"])
    manifest = joint_manifest(
        data["dataset_id"],
        allocations={"book:alpha": "500", "book:beta": "500"},
        order_sizes=("1", "1"),
    )
    # joint_manifest lists beta first and alpha second.
    manifest["strategies"][0]["params"]["emit"] = False
    manifest["strategies"][1]["params"]["emit"] = True

    _, result = await submit_and_run(store, manifest)
    assert result["fills"] == [
        {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "qty": "1",
            "price": "99",
            "fee": "0.099",
            "bar_index": 1,
            "book_id": "book:alpha",
            "strategy_id": "alpha",
            "cl_ord_id": "bar-0-BTCUSDT-1",
        }
    ]


@pytest.mark.asyncio
async def test_the_executor_needs_only_research_state_and_shared_core():
    """4.6 / 4.9: no live account/order state is read by the research path."""
    store = MemoryResearchStore()
    data, _ = await register(store)
    application = BacktestApplication(store, eligibility=always_eligible)
    job = await application.submit(joint_manifest(data["dataset_id"]))

    class ResearchOnlyStore:
        def __init__(self, inner):
            self._inner = inner

        async def get_dataset(self, dataset_id):
            return await self._inner.get_dataset(dataset_id)

        async def get_dataset_link(self, dataset_id):
            return await self._inner.get_dataset_link(dataset_id)

    result = await ResearchExecutor(ResearchOnlyStore(store)).execute(
        {"manifest": job["manifest"], "manifest_hash": job["manifest_hash"]}
    )
    assert result["engine"] == "event_runtime"
    assert result["runtime_version"] == SPOT_RUNTIME_VERSION
    assert result["dataset_ids"] == job["manifest"]["dataset_ids"]
    assert result["dataset_provenance"] == job["manifest"]["provenance"]
    assert result["report"]["provenance"] == job["manifest"]["provenance"]
    assert {
        dataset["dataset_id"]: dataset["provenance_hash"]
        for dataset in result["report"]["datasets"]
    } == job["manifest"]["provenance"]


def test_an_execution_above_the_book_budget_is_refused_and_attributed():
    """4.7: a fill that no longer fits its book is refused with attribution."""
    data = freeze_dataset(identity(), bars(["100", "110", "110"]))

    def strategy(context, bar):
        if context.bar_index != 0:
            return []
        return [
            StrategySignal(
                symbol="BTCUSDT",
                side="BUY",
                qty=Decimal("0.95"),
                order_type="MARKET",
                strategy_id="alpha",
            )
        ]

    result = EventBacktestRuntime(
        datasets=[data],
        strategy=strategy,
        initial_cash=Decimal("1000"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("1000"),
        strategy_books={"alpha": "book:alpha", "beta": "book:beta"},
        initial_allocations={"book:alpha": Decimal("100"), "book:beta": Decimal("900")},
    ).run()

    books = {item["book_id"]: item for item in result.books}
    # The close-based reservation is 95.095, but the slipped fill costs
    # 0.95 * 110 * 1.1 + fee = 115.06495: A's own book cannot fund it.
    assert result.fills == []
    assert result.execution_rejections == {"BOOK_INSUFFICIENT_AVAILABLE_BALANCE": 1}
    assert result.rejected_intents == [
        {
            "strategy_id": "alpha",
            "book_id": "book:alpha",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "qty": Decimal("0.95"),
            "order_type": "MARKET",
            "code": "BOOK_INSUFFICIENT_AVAILABLE_BALANCE",
        }
    ]
    assert books["book:alpha"]["balances"]["USDT"] == "100"
    assert books["book:alpha"]["available"]["USDT"] == "100"
    assert books["book:alpha"]["positions"] == {}
    assert books["book:beta"]["balances"]["USDT"] == "900"
    assert Decimal(result.total_fees) == Decimal("0")


def test_manifest_normalization_is_shared_by_submit_and_execution():
    """4.3 / 4.4: one normalizer, not a submission-only convenience helper."""
    manifest = {
        "dataset_ids": ["ds_" + "a" * 64],
        "strategies": [
            {
                "strategy_id": "beta",
                "module_path": "trader.strategies.ema_cross_btc",
                "params": {"order_size": "1"},
            },
            {
                "strategy_id": "alpha",
                "module_path": "trader.strategies.ema_cross_btc",
                "params": {"order_size": "1"},
            },
        ],
        "book_allocations": {"book:beta": "500", "book:alpha": "500"},
        "initial_cash": "1000",
        "fee_bps": "0",
        "slippage_bps": "0",
    }
    first = normalize_backtest_manifest(manifest)
    manifest["strategies"].reverse()
    manifest["book_allocations"] = {"book:alpha": "500", "book:beta": "500"}
    second = normalize_backtest_manifest(manifest)
    assert first == second
    assert digest(first) == digest(second)
    assert [item["strategy_id"] for item in first["strategies"]] == ["alpha", "beta"]
