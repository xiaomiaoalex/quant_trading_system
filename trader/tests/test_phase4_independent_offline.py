"""Independent phase-4 acceptance tests for joint backtests and frozen manifests.

The expectations in this module are hand-computed from the phase-4 contract and
the book/settlement contract. They do not call the implementation to derive an
expected balance, fee, hash or order field. Test-local plugins and fault
injections are explicit; no production file is modified.
"""

from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal

import pytest

from trader.trading import books as book_ledger
from trader.trading import core as trading_core
from trader.trading.backtest import EventBacktestRuntime, StrategySignal
from trader.trading.jobs import (
    BacktestApplication,
    MemoryResearchStore,
    ResearchWorker,
    normalize_backtest_manifest,
)
from trader.trading.models import SETTLEMENT_BASE, AccountState, Execution, TradingError
from trader.trading.order_spec import (
    PRICE_SOURCE_BACKTEST_CLOSED_BAR,
    PRICE_SOURCE_MARKET_CLOSED_BAR,
    PRICE_SOURCE_SIGNAL_LIMIT,
    build_order_spec,
)
from trader.trading.research import ResearchExecutor
from trader.trading.risk import RiskPolicy
from trader.trading.version import SPOT_RUNTIME_VERSION


def identity(symbol: str = "BTCUSDT", base_asset: str = "BTC") -> dict:
    return {
        "venue": "binance",
        "market_type": "SPOT",
        "symbol": symbol,
        "base_asset": base_asset,
        "quote_asset": "USDT",
        "interval": "1m",
        "source": "independent-phase4-fixture",
        "version": "1",
    }


def bars(opens: list[str], closes: list[str] | None = None) -> list[dict]:
    closes = closes or opens
    rows = []
    for index, (opened, closed) in enumerate(zip(opens, closes, strict=True)):
        open_ms = index * 60_000
        high = max(Decimal(opened), Decimal(closed))
        low = min(Decimal(opened), Decimal(closed))
        rows.append(
            {
                "open_time_ms": open_ms,
                "close_time_ms": open_ms + 60_000,
                "available_at_ms": open_ms + 60_000,
                "open": opened,
                "high": str(high),
                "low": str(low),
                "close": closed,
                "volume": "1",
            }
        )
    return rows


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class ScriptedSignal:
    def __init__(self, side: str, qty: str, order_type: str = "MARKET", price=None):
        self.signal_type = side
        self.quantity = Decimal(qty)
        self.order_type = order_type
        self.price = None if price is None else Decimal(price)
        self.reason = "independent-scripted-signal"
        self.time_in_force = "GTC"


class ScriptedPlugin:
    """One deterministic signal per strategy, emitted on the first closed bar."""

    def __init__(self, params: dict):
        self.params = params
        self.sent = False

    async def on_market_data(self, market):
        if self.sent or not self.params.get("emit", True):
            return None
        self.sent = True
        return ScriptedSignal(
            str(self.params.get("side", "BUY")),
            str(self.params["qty"]),
            str(self.params.get("order_type", "MARKET")),
            self.params.get("limit_price"),
        )


def install_scripted_plugins(monkeypatch):
    def loader(module_path, params):
        return ScriptedPlugin(params)

    monkeypatch.setattr("trader.trading.research.load_plugin", loader)


def trust_memory(store, dataset_id: str, provenance_hash: str = "a" * 64) -> dict:
    link = {
        "research_dataset_id": dataset_id,
        "history_dataset_id": "hds_independent_phase4",
        "official_eligible": True,
        "provenance_hash": provenance_hash,
        "symbol": "BTCUSDT",
        "interval": "1m",
        "month": "2026-11",
        "conversion_version": "independent_test_v1",
        "bar_count": 4,
    }
    store.register_dataset_link(dataset_id, link)
    return link


async def eligible(store):
    async def check(dataset_id: str) -> bool:
        link = await store.get_dataset_link(dataset_id)
        return bool(link and link.get("official_eligible") and link.get("provenance_hash"))

    return check


def joint_manifest(
    dataset_id: str,
    *,
    allocations: dict[str, str] | None = None,
    sizes: tuple[str, str] = ("2", "1"),
    runtime_version: str | None = SPOT_RUNTIME_VERSION,
    provenance_hash: str = "a" * 64,
) -> dict:
    manifest = {
        "dataset_ids": [dataset_id],
        "strategies": [
            {
                "strategy_id": "beta",
                "module_path": "trader.strategies.ema_cross_btc",
                "params": {
                    "emit": True,
                    "side": "BUY",
                    "qty": sizes[1],
                    "order_type": "MARKET",
                },
            },
            {
                "strategy_id": "alpha",
                "module_path": "trader.strategies.ema_cross_btc",
                "params": {
                    "emit": True,
                    "side": "BUY",
                    "qty": sizes[0],
                    "order_type": "MARKET",
                },
            },
        ],
        "book_allocations": allocations or {"book:beta": "900", "book:alpha": "100"},
        "initial_cash": "1000",
        "fee_bps": "10",
        "slippage_bps": "0",
        "engine": "event_runtime",
        "purpose": "research_screening",
        "provenance": {dataset_id: provenance_hash},
    }
    if runtime_version is not None:
        manifest["runtime_version"] = runtime_version
    return manifest


async def submit_and_run(store, manifest: dict):
    application = BacktestApplication(store, eligibility=await eligible(store))
    job = await application.submit(manifest)
    worker = ResearchWorker(store, owner="independent-phase4", lease_seconds=30)
    finished = await worker.run_once(ResearchExecutor(store).execute)
    assert finished is not None
    assert finished["run_id"] == job["run_id"]
    assert finished["status"] == "SUCCEEDED", finished.get("error")
    return job, finished["result"]


async def one_dataset(store, prices: list[str] | None = None):
    prices = prices or ["100", "100", "100", "100"]
    data = await store.register_dataset(identity(), bars(prices))
    link = trust_memory(store, data["dataset_id"])
    return data, link


def books_by_id(result: dict) -> dict[str, dict]:
    return {item["book_id"]: item for item in result["books"]}


# ---------------------------------------------------------------------------
# Manifest freezing, exact book mapping and independent hash


async def test_allocation_sum_book_ids_and_manifest_hash_are_exact():
    store = MemoryResearchStore()
    data, _link = await one_dataset(store)
    manifest = joint_manifest(data["dataset_id"])

    job, _result = await submit_and_run(store, manifest)
    frozen = job["manifest"]

    assert frozen["book_allocations"] == {"book:alpha": "100", "book:beta": "900"}
    assert sum(Decimal(value) for value in frozen["book_allocations"].values()) == Decimal("1000")
    assert [item["strategy_id"] for item in frozen["strategies"]] == ["alpha", "beta"]
    assert {item["book_id"] for item in frozen["strategies"]} == {"book:alpha", "book:beta"}
    assert all(item["code_hash"] for item in frozen["strategies"])
    assert frozen["runtime_version"] == SPOT_RUNTIME_VERSION
    assert frozen["provenance"] == {data["dataset_id"]: "a" * 64}
    assert frozen["risk_config"] == {}

    # The canonical hash is recomputed here from the frozen document only.
    assert job["manifest_hash"] == sha(frozen)

    # A fresh normalizer invocation with reordered caller input freezes the same
    # public content. Sorting is performed on dataset ids and strategy ids.
    reordered = copy.deepcopy(manifest)
    reordered["strategies"].reverse()
    reordered["book_allocations"] = {"book:alpha": "100", "book:beta": "900"}
    assert sha(normalize_backtest_manifest(reordered)) == job["manifest_hash"]


def test_manifest_rejects_impossible_allocations_and_stale_runtime():
    dataset_id = "ds_" + "1" * 64
    with pytest.raises(ValueError, match="sum to the initial cash"):
        normalize_backtest_manifest(
            joint_manifest(dataset_id, allocations={"book:alpha": "100", "book:beta": "800"})
        )
    with pytest.raises(ValueError, match="match the configured strategy books"):
        normalize_backtest_manifest(
            joint_manifest(dataset_id, allocations={"book:alpha": "500", "book:other": "500"})
        )
    with pytest.raises(ValueError, match="Legacy runtime version"):
        normalize_backtest_manifest(joint_manifest(dataset_id, runtime_version="spot_bar_v1"))

    two_ids = joint_manifest(dataset_id)
    two_ids["dataset_ids"] = ["ds_" + "b" * 64, "ds_" + "a" * 64]
    # A different dataset set cannot keep the one-dataset provenance mapping.
    with pytest.raises(ValueError, match="DATASET_PROVENANCE_MISMATCH"):
        normalize_backtest_manifest(two_ids)


async def test_manifest_nested_mutation_is_refused_and_recovers(monkeypatch):
    install_scripted_plugins(monkeypatch)
    store = MemoryResearchStore()
    data, _link = await one_dataset(store)
    application = BacktestApplication(store, eligibility=await eligible(store))
    job = await application.submit(joint_manifest(data["dataset_id"]))
    original_manifest = copy.deepcopy(store.jobs[job["run_id"]]["manifest"])
    original_hash = store.jobs[job["run_id"]]["manifest_hash"]

    # Caller-side mutation after submission cannot reach the stored document.
    caller = joint_manifest(data["dataset_id"])
    caller["strategies"][0]["params"]["qty"] = "999"
    caller["book_allocations"]["book:alpha"] = "999"
    caller["book_allocations"]["book:beta"] = "1"
    assert store.jobs[job["run_id"]]["manifest"] == original_manifest

    # A mutated stored document is refused by both explicit retry and execution.
    store.jobs[job["run_id"]]["manifest"]["strategies"][0]["params"]["qty"] = "999"
    store.jobs[job["run_id"]]["status"] = "FAILED"
    with pytest.raises(ValueError, match="Frozen manifest hash mismatch"):
        await application.retry(job["run_id"])
    assert store.jobs[job["run_id"]]["manifest"]["strategies"][0]["params"]["qty"] == "999"
    assert store.jobs[job["run_id"]]["manifest_hash"] == original_hash

    with pytest.raises(ValueError, match="Frozen manifest hash mismatch"):
        await ResearchExecutor(store).execute(store.jobs[job["run_id"]])

    # Removing the injected mutation restores the normal retry path.
    store.jobs[job["run_id"]]["manifest"] = copy.deepcopy(original_manifest)
    store.jobs[job["run_id"]]["status"] = "FAILED"
    retried = await application.retry(job["run_id"])
    assert retried["status"] == "QUEUED"
    finished = await ResearchWorker(store, owner="independent-recovery").run_once(
        ResearchExecutor(store).execute
    )
    assert finished is not None
    assert finished["status"] == "SUCCEEDED", finished.get("error")


async def test_same_frozen_manifest_is_deterministic_in_fresh_runtimes(monkeypatch):
    install_scripted_plugins(monkeypatch)
    outputs = []
    for _index in range(2):
        store = MemoryResearchStore()
        data, _link = await one_dataset(store)
        manifest = joint_manifest(
            data["dataset_id"],
            allocations={"book:alpha": "500", "book:beta": "500"},
            sizes=("1", "1"),
        )
        job, result = await submit_and_run(store, manifest)
        outputs.append((job["manifest_hash"], result))

    first_hash, first_result = outputs[0]
    second_hash, second_result = outputs[1]
    assert first_hash == second_hash
    assert canonical(first_result) == canonical(second_result)
    assert first_result["manifest_hash"] == first_hash
    assert first_result["runtime_manifest_hash"] == second_result["runtime_manifest_hash"]
    assert first_result["equity_curve"] == second_result["equity_curve"]


# ---------------------------------------------------------------------------
# Book isolation, budget refusal and per-book accounting


def test_unequal_books_refuse_a_and_leave_b_trading():
    data = {
        "dataset_id": "ds_independent_unequal",
        "identity": identity(),
        "bars": bars(["100", "100", "100", "100"]),
    }

    def strategy(context, bar):
        if context.bar_index != 0:
            return []
        return [
            StrategySignal(
                "BTCUSDT",
                "BUY",
                Decimal("2"),
                order_type="MARKET",
                strategy_id="alpha",
            ),
            StrategySignal(
                "BTCUSDT",
                "BUY",
                Decimal("1"),
                order_type="MARKET",
                strategy_id="beta",
            ),
        ]

    result = EventBacktestRuntime(
        datasets=[data],
        strategy=strategy,
        initial_cash=Decimal("1000"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("0"),
        strategy_books={"alpha": "book:alpha", "beta": "book:beta"},
        initial_allocations={"book:alpha": Decimal("100"), "book:beta": Decimal("900")},
    ).run()

    by_book = {item["book_id"]: item for item in result.books}
    assert result.rejections == {"BOOK_INSUFFICIENT_AVAILABLE_BALANCE": 1}
    assert result.rejected_intents[0]["strategy_id"] == "alpha"
    assert result.rejected_intents[0]["book_id"] == "book:alpha"
    assert len(result.fills) == 1
    assert result.fills[0]["book_id"] == "book:beta"
    assert result.fills[0]["strategy_id"] == "beta"
    assert Decimal(result.fills[0]["fee"]) == Decimal("0.1")

    assert Decimal(by_book["book:alpha"]["balances"]["USDT"]) == Decimal("100")
    assert by_book["book:alpha"]["positions"] == {}
    assert Decimal(by_book["book:alpha"]["allocation"]) == Decimal("100")
    assert Decimal(by_book["book:alpha"]["fees"]) == Decimal("0")
    assert Decimal(by_book["book:beta"]["balances"]["USDT"]) == Decimal("799.9")
    assert by_book["book:beta"]["positions"] == {"BTCUSDT": "1"}
    assert Decimal(by_book["book:beta"]["equity"]) == Decimal("899.9")
    assert Decimal(by_book["book:beta"]["allocation"]) == Decimal("900")
    assert Decimal(by_book["book:beta"]["fees"]) == Decimal("0.1")

    assert result.cash == Decimal("899.9")
    assert result.positions == {"BTCUSDT": Decimal("1")}
    assert result.net_value == Decimal("999.9")
    assert result.total_fees == Decimal("0.1")
    assert result.book_fees["book:alpha"] == Decimal("0")
    assert result.book_fees["book:beta"] == Decimal("0.1")
    assert sum(result.book_fees.values(), Decimal(0)) == Decimal("0.1")
    assert Decimal(by_book["book:alpha"]["available"]["USDT"]) == Decimal("100")
    assert Decimal(by_book["book:beta"]["available"]["USDT"]) == Decimal("799.9")


def test_fill_above_a_books_budget_must_not_borrow_from_the_account():
    # A=100, B=900; A reserves 0.95 * 100 * 1.001 = 95.095 at the close, but
    # the next open is 110 with 1000 bps adverse slippage: 110 * 1.10 = 121.
    # The actual cost is 0.95 * 121 = 114.95 plus 0.11495 fee = 115.06495,
    # which is greater than A's 100 allocation.
    data = {
        "dataset_id": "ds_independent_overbudget",
        "identity": identity(),
        "bars": bars(["100", "110", "110"], closes=["100", "110", "110"]),
    }

    def strategy(context, bar):
        if context.bar_index == 0:
            return [
                StrategySignal(
                    "BTCUSDT",
                    "BUY",
                    Decimal("0.95"),
                    order_type="MARKET",
                    strategy_id="alpha",
                )
            ]
        return []

    result = EventBacktestRuntime(
        datasets=[data],
        strategy=strategy,
        initial_cash=Decimal("1000"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("1000"),
        strategy_books={"alpha": "book:alpha", "beta": "book:beta"},
        initial_allocations={"book:alpha": Decimal("100"), "book:beta": Decimal("900")},
    ).run()

    by_book = {item["book_id"]: item for item in result.books}
    # The execution-stage rejection must be explicit and attributed; a silent
    # no-op is not enough, because it would hide an accepted intent.
    assert result.fills == []
    assert result.execution_rejections == {"BOOK_INSUFFICIENT_AVAILABLE_BALANCE": 1}
    assert len(result.rejected_intents) == 1
    assert result.rejected_intents[0] == {
        "strategy_id": "alpha",
        "book_id": "book:alpha",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "qty": Decimal("0.95"),
        "order_type": "MARKET",
        "code": "BOOK_INSUFFICIENT_AVAILABLE_BALANCE",
    }
    assert Decimal(by_book["book:alpha"]["balances"]["USDT"]) == Decimal("100")
    assert Decimal(by_book["book:alpha"]["available"]["USDT"]) == Decimal("100")
    assert by_book["book:alpha"]["positions"] == {}
    assert Decimal(by_book["book:beta"]["balances"]["USDT"]) == Decimal("900")
    assert Decimal(by_book["book:beta"]["available"]["USDT"]) == Decimal("900")
    assert by_book["book:beta"]["positions"] == {}
    assert result.fees == Decimal("0")
    assert sum(result.book_fees.values(), Decimal(0)) == Decimal("0")


def _core_state() -> AccountState:
    state = AccountState(
        account_id="acct",
        status="READY",
        balances={"USDT": Decimal("1000"), "BTC": Decimal("3")},
        positions={"BTCUSDT": Decimal("3")},
    )
    state.reconciliation = {
        "checked_at_ms": 0,
        "marks": {"BTCUSDT": {"price": "100", "at_ms": 0}},
        "stream_healthy": True,
        "unresolved": [],
    }
    book_ledger.migrate_book_ledger(state)
    book_ledger.allocate(state, "book:alpha", "USDT", Decimal("500"))
    book_ledger.allocate(state, "book:beta", "USDT", Decimal("500"))
    return state


def _command(book_id: str, side: str, qty: str, price: str = "100"):
    spec = build_order_spec(
        symbol="BTCUSDT",
        side=side,
        order_type="MARKET",
        qty=Decimal(qty),
        limit_price=None,
        reference_price=Decimal(price),
        reference_price_source=PRICE_SOURCE_BACKTEST_CLOSED_BAR,
        reference_at_ms=0,
        decision_at_ms=0,
        time_in_force="GTC",
        book_id=book_id,
        price_source=PRICE_SOURCE_BACKTEST_CLOSED_BAR,
        settlement=SETTLEMENT_BASE,
    )
    return spec.to_command(
        command_id=f"cmd-{book_id}-{side}-{qty}",
        account_id="acct",
        base_asset="BTC",
        quote_asset="USDT",
        deployment_id="independent",
        decision_id=f"decision-{book_id}-{side}",
    )


def test_partial_fill_actual_fee_currency_and_per_book_reconciliation():
    state = _core_state()
    policy = RiskPolicy(fee_rate=Decimal("0.001"))

    alpha = trading_core.reserve_order(
        state, _command("book:alpha", "BUY", "2"), "alpha-buy", policy, 0
    )
    beta = trading_core.reserve_order(
        state, _command("book:beta", "BUY", "1"), "beta-buy", policy, 0
    )
    assert alpha.cl_ord_id == "alpha-buy"
    assert beta.cl_ord_id == "beta-buy"

    # A's two executions total 2 base at 100 for 0.20 USDT fee.
    assert trading_core.book_execution(
        state,
        Execution(
            "acct",
            "alpha-buy",
            "BTCUSDT",
            "a-1",
            Decimal("0.5"),
            Decimal("100"),
            Decimal("0.05"),
            "USDT",
        ),
        policy,
    )
    assert trading_core.book_execution(
        state,
        Execution(
            "acct",
            "alpha-buy",
            "BTCUSDT",
            "a-2",
            Decimal("1.5"),
            Decimal("100"),
            Decimal("0.15"),
            "USDT",
        ),
        policy,
    )
    # B's one execution is 1 base at 100 for 0.10 USDT fee.
    assert trading_core.book_execution(
        state,
        Execution(
            "acct",
            "beta-buy",
            "BTCUSDT",
            "b-1",
            Decimal("1"),
            Decimal("100"),
            Decimal("0.10"),
            "USDT",
        ),
        policy,
    )

    assert state.balances["USDT"] == Decimal("699.7")
    assert state.balances["BTC"] == Decimal("6")
    assert state.positions["BTCUSDT"] == Decimal("6")
    assert state.books["book:alpha"].balances["USDT"] == Decimal("299.8")
    assert state.books["book:alpha"].balances["BTC"] == Decimal("2")
    assert state.books["book:alpha"].positions["BTCUSDT"] == Decimal("2")
    assert state.books["book:beta"].balances["USDT"] == Decimal("399.9")
    assert state.books["book:beta"].balances["BTC"] == Decimal("1")
    assert state.books["book:beta"].positions["BTCUSDT"] == Decimal("1")

    fees = {"book:alpha": Decimal("0.20"), "book:beta": Decimal("0.10")}
    assert sum(fees.values()) == Decimal("0.30")
    fee_entries = [
        (item["book_id"], item["asset"], Decimal(item["delta"]))
        for item in state.ledger
        if item["kind"] == "fee"
    ]
    assert sorted(fee_entries) == [
        ("book:alpha", "USDT", Decimal("-0.15")),
        ("book:alpha", "USDT", Decimal("-0.05")),
        ("book:beta", "USDT", Decimal("-0.10")),
    ]
    assert state.holds == {}
    assert all(not book.holds for book in state.books.values())
    assert book_ledger.conservation_mismatch(state) == {}

    snapshots = {
        book_id: book_ledger.book_snapshot(
            state,
            state.books[book_id],
            marks={"BTCUSDT": {"price": "100", "at_ms": 0}},
            now_ms=0,
            max_mark_age_ms=60_000,
        )
        for book_id in ("book:alpha", "book:beta", book_ledger.UNALLOCATED)
    }
    assert Decimal(snapshots["book:alpha"]["equity"]) == Decimal("499.8")
    assert Decimal(snapshots["book:beta"]["equity"]) == Decimal("499.9")
    assert Decimal(snapshots[book_ledger.UNALLOCATED]["equity"]) == Decimal("300")
    assert sum(Decimal(item["equity"]) for item in snapshots.values()) == Decimal("1299.7")


def test_base_fee_is_booked_in_base_currency_once():
    state = AccountState(
        account_id="acct",
        status="READY",
        balances={"USDT": Decimal("1000"), "BTC": Decimal("0")},
        positions={"BTCUSDT": Decimal("0")},
    )
    state.reconciliation = {
        "checked_at_ms": 0,
        "marks": {"BTCUSDT": {"price": "100", "at_ms": 0}},
        "stream_healthy": True,
        "unresolved": [],
    }
    book_ledger.migrate_book_ledger(state)
    book_ledger.allocate(state, "book:alpha", "USDT", Decimal("500"))
    policy = RiskPolicy(fee_rate=Decimal("0.001"))

    trading_core.reserve_order(state, _command("book:alpha", "BUY", "1"), "base-fee", policy, 0)
    assert trading_core.book_execution(
        state,
        Execution(
            "acct",
            "base-fee",
            "BTCUSDT",
            "bf-1",
            Decimal("1"),
            Decimal("100"),
            Decimal("0.001"),
            "BTC",
        ),
        policy,
    )

    assert state.books["book:alpha"].balances["USDT"] == Decimal("400")
    assert state.books["book:alpha"].balances["BTC"] == Decimal("0.999")
    assert state.books["book:alpha"].positions["BTCUSDT"] == Decimal("0.999")
    assert state.balances["USDT"] == Decimal("900")
    assert state.balances["BTC"] == Decimal("0.999")
    fee_entry = next(item for item in state.ledger if item["kind"] == "fee")
    assert (fee_entry["book_id"], fee_entry["asset"], fee_entry["delta"]) == (
        "book:alpha",
        "BTC",
        "-0.001",
    )
    assert book_ledger.conservation_mismatch(state) == {}


def test_mutation_book_borrowing_is_detected_and_restored(monkeypatch):
    state = AccountState(
        account_id="acct",
        status="READY",
        balances={"USDT": Decimal("1000")},
    )
    state.reconciliation = {
        "checked_at_ms": 0,
        "marks": {"BTCUSDT": {"price": "100", "at_ms": 0}},
        "stream_healthy": True,
        "unresolved": [],
    }
    book_ledger.migrate_book_ledger(state)
    book_ledger.allocate(state, "book:alpha", "USDT", Decimal("100"))
    book_ledger.allocate(state, "book:beta", "USDT", Decimal("900"))
    policy = RiskPolicy(fee_rate=Decimal("0.001"))
    command = _command("book:alpha", "BUY", "2")

    with pytest.raises(TradingError, match="BOOK_INSUFFICIENT_AVAILABLE_BALANCE"):
        trading_core.reserve_order(state, command, "normal", policy, 0)

    import trader.trading.risk as risk_module

    def permissive_book_available(book, asset):
        return Decimal("1000000")

    with monkeypatch.context() as patch:
        patch.setattr(risk_module.book_ledger, "book_available", permissive_book_available)
        order = trading_core.reserve_order(state, command, "mutated", policy, 0)
        assert order.cl_ord_id == "mutated"
        # This is the independent audit that a real book check must satisfy.
        alpha = state.books["book:alpha"]
        alpha_holds = book_ledger.book_hold_amount(alpha, "USDT")
        assert alpha.balances["USDT"] - alpha_holds < 0

    with pytest.raises(TradingError, match="BOOK_INSUFFICIENT_AVAILABLE_BALANCE"):
        trading_core.reserve_order(state, command, "restored", policy, 0)


# ---------------------------------------------------------------------------
# Order semantics and the shared economic path


def test_live_and_research_order_intent_match(monkeypatch):
    import trader.trading.backtest as backtest_module

    captured = []
    real_build = backtest_module.build_order_spec

    def spy(**kwargs):
        spec = real_build(**kwargs)
        captured.append(spec)
        return spec

    monkeypatch.setattr(backtest_module, "build_order_spec", spy)
    data = {
        "dataset_id": "ds_independent_orders",
        "identity": identity(),
        "bars": bars(["100", "100", "100"], closes=["100", "100", "100"]),
    }
    data["bars"][1]["low"] = "90"

    def strategy(context, bar):
        if context.bar_index != 0:
            return []
        return [
            StrategySignal("BTCUSDT", "BUY", Decimal("0.25"), order_type="MARKET"),
            StrategySignal(
                "BTCUSDT",
                "BUY",
                Decimal("0.5"),
                order_type="LIMIT",
                limit_price=Decimal("99"),
            ),
        ]

    result = EventBacktestRuntime(
        datasets=[data],
        strategy=strategy,
        initial_cash=Decimal("1000"),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    ).run()

    assert len(captured) == 2
    market, limit = captured
    assert (market.symbol, market.side, market.qty, market.price) == (
        "BTCUSDT",
        "BUY",
        Decimal("0.25"),
        Decimal("100"),
    )
    assert market.order_type == "MARKET"
    assert market.price_source == PRICE_SOURCE_BACKTEST_CLOSED_BAR
    assert market.price_at_ms == 60_000
    assert (limit.symbol, limit.side, limit.qty, limit.price) == (
        "BTCUSDT",
        "BUY",
        Decimal("0.5"),
        Decimal("99"),
    )
    assert limit.order_type == "LIMIT"
    assert limit.price_source == PRICE_SOURCE_SIGNAL_LIMIT

    live_market = build_order_spec(
        symbol="BTCUSDT",
        side="BUY",
        order_type="MARKET",
        qty=Decimal("0.25"),
        limit_price=None,
        reference_price=Decimal("100"),
        reference_price_source=PRICE_SOURCE_MARKET_CLOSED_BAR,
        reference_at_ms=60_000,
        decision_at_ms=0,
        time_in_force="GTC",
        book_id="book:orders",
        price_source=PRICE_SOURCE_MARKET_CLOSED_BAR,
    )
    live_limit = build_order_spec(
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        qty=Decimal("0.5"),
        limit_price=Decimal("99"),
        reference_price=None,
        reference_price_source=PRICE_SOURCE_SIGNAL_LIMIT,
        reference_at_ms=60_000,
        decision_at_ms=0,
        time_in_force="GTC",
        book_id="book:orders",
        price_source=PRICE_SOURCE_SIGNAL_LIMIT,
    )
    assert (live_market.symbol, live_market.side, live_market.qty, live_market.price) == (
        market.symbol,
        market.side,
        market.qty,
        market.price,
    )
    assert (live_limit.symbol, live_limit.side, live_limit.qty, live_limit.price) == (
        limit.symbol,
        limit.side,
        limit.qty,
        limit.price,
    )
    assert [(fill["side"], fill["qty"], fill["price"]) for fill in result.fills] == [
        ("BUY", Decimal("0.25"), Decimal("100")),
        ("BUY", Decimal("0.5"), Decimal("99")),
    ]


async def test_research_executor_rechecks_trusted_link_after_queue(monkeypatch):
    install_scripted_plugins(monkeypatch)
    store = MemoryResearchStore()
    data, link = await one_dataset(store)
    application = BacktestApplication(store, eligibility=await eligible(store))
    job = await application.submit(joint_manifest(data["dataset_id"]))

    stale = copy.deepcopy(link)
    stale["official_eligible"] = False
    store.register_dataset_link(data["dataset_id"], stale)
    worker = ResearchWorker(store, owner="independent-stale-link")
    finished = await worker.run_once(ResearchExecutor(store).execute)

    assert finished is not None
    assert finished["status"] == "FAILED", finished
    assert "provenance" in str(finished.get("error", "")).lower()
    assert store.jobs[job["run_id"]]["manifest"] == job["manifest"]


async def test_executor_rejects_changed_dataset_content_and_recovers(monkeypatch):
    install_scripted_plugins(monkeypatch)
    store = MemoryResearchStore()
    data, _link = await one_dataset(store)
    application = BacktestApplication(store, eligibility=await eligible(store))
    job = await application.submit(joint_manifest(data["dataset_id"]))
    original = copy.deepcopy(store.datasets[data["dataset_id"]])

    store.datasets[data["dataset_id"]]["bars"][0]["close"] = "101"
    worker = ResearchWorker(store, owner="independent-mutated-dataset")
    failed = await worker.run_once(ResearchExecutor(store).execute)
    assert failed is not None
    assert failed["status"] == "FAILED"
    assert "content hash" in str(failed.get("error", "")).lower()

    store.datasets[data["dataset_id"]] = original
    retried = await application.retry(job["run_id"])
    assert retried["status"] == "QUEUED"
    finished = await ResearchWorker(store, owner="independent-dataset-recovery").run_once(
        ResearchExecutor(store).execute
    )
    assert finished is not None
    assert finished["status"] == "SUCCEEDED", finished.get("error")


async def test_research_executor_only_needs_research_store(monkeypatch):
    install_scripted_plugins(monkeypatch)
    store = MemoryResearchStore()
    data, _link = await one_dataset(store)
    application = BacktestApplication(store, eligibility=await eligible(store))
    job = await application.submit(joint_manifest(data["dataset_id"]))

    class ResearchOnly:
        def __init__(self, inner):
            self.inner = inner

        async def get_dataset(self, dataset_id):
            return await self.inner.get_dataset(dataset_id)

        async def get_dataset_link(self, dataset_id):
            return await self.inner.get_dataset_link(dataset_id)

        def __getattr__(self, name):
            raise AssertionError(f"research path touched non-research store member: {name}")

    result = await ResearchExecutor(ResearchOnly(store)).execute(store.jobs[job["run_id"]])
    assert result["engine"] == "event_runtime"
    assert result["runtime_version"] == SPOT_RUNTIME_VERSION
    assert result["manifest_hash"] == job["manifest_hash"]
    assert result["book_fees"]["book:alpha"] == "0"
    assert result["book_fees"]["book:beta"] == "0.1"
    assert sum(Decimal(value) for value in result["book_fees"].values()) == Decimal("0.1")
    assert result["dataset_provenance"] == {data["dataset_id"]: "a" * 64}
    assert result["dataset_ids"] == [data["dataset_id"]]
