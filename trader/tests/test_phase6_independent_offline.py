"""Phase-6 independent acceptance tests - offline half.

The expectations here are derived from the phase-6 acceptance items
(docs/FOUNDATION_REFACTOR_PHASE0.md 6.1-6.12), contract 13.17 and
docs/PROJECT_ARCHITECTURE.md 8.5, then checked against the implementation.
They do not import the engineer's phase-6 tests and do not reuse their
fixtures.

Scope covered without a database or the venue:

* single-stack static constraints (retired modules/imports/routes/writers);
* Control compatibility views over a real ``TradingService`` (orders,
  executions, monitor, killswitch durability, portfolio, strategy,
  deployment events, reconciler report, 410/503 refusal codes, no venue IO);
* the two frozen execution defects: ``EXPIRED_IN_MATCH`` terminal mapping and
  a queued ``ALLOCATE`` before the first authoritative snapshot;
* the readiness fence in ``_market_loop`` and the lookback guard;
* observation JSONL field completeness/durability/transition/auth handling;
* supervisor window verdict over synthetic journals;
* clean-start preflight over a synthetic venue account.

No skip/xfail: these are ordinary assertions. A failure is a finding.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import functools
import importlib.util
import json
import re
import sys
import types
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trader.adapters.broker.binance_spot_demo_broker import (
    BinanceSpotDemoBroker,
    BinanceSpotDemoBrokerConfig,
)
from trader.api.trading_app import create_trading_app
from trader.runtime.trading_worker_main import _market_loop
from trader.trading import core as trading_core
from trader.trading.models import Execution, NotReady, ReconcileSnapshot
from trader.trading.order_spec import build_order_spec
from trader.trading.risk import RiskPolicy
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore

ROOT = Path(__file__).resolve().parents[2]
TRADER = ROOT / "trader"

ACCOUNT = "p6ind_acct"
T0 = 1_000_000_000_000
DAY_MS = 86_400_000
BOOK = "book:phase6-probe"
DEPLOYMENT = "dep-p6ind"
STRATEGY = "trader.strategies.phase6_probe"
_COVERAGE_AT_SAMPLE = object()


# --------------------------------------------------------------------- helpers
def _production_files() -> list[Path]:
    return sorted(
        path
        for path in TRADER.rglob("*.py")
        if "tests" not in path.parts and "__pycache__" not in path.parts
    )


_EXCLUDED_REPO_PARTS = {
    "tests",
    "__pycache__",
    "artifacts",
    ".worktrees",
    ".kilo",
    ".workbuddy",
    "workflow",
    ".venv",
    "node_modules",
    "Frontend",
}


def _repo_python_files() -> list[Path]:
    """Every runnable repo module except tests, tool-owned trees and backups."""
    return sorted(
        path for path in ROOT.rglob("*.py") if not (_EXCLUDED_REPO_PARTS & set(path.parts))
    )


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            modules.add(base)
            for alias in node.names:
                modules.add(f"{base}.{alias.name}" if base else alias.name)
    return modules


def _defined_names(path: Path, kind: type) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.name for node in ast.walk(tree) if isinstance(node, kind)]


@functools.lru_cache(maxsize=None)
def _script_module(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"phase6_independent_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _place_command(command_id: str, *, qty: str = "1", price: str = "200"):
    """A hand-computed order: 1 BTC at 200 = 200 USDT notional."""
    spec = build_order_spec(
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        qty=Decimal(qty),
        limit_price=Decimal(price),
        reference_price=Decimal(price),
        reference_price_source="SIGNAL_LIMIT",
        reference_at_ms=T0,
        decision_at_ms=T0,
        time_in_force="GTC",
        book_id=BOOK,
        price_source="SIGNAL_LIMIT",
    )
    return spec.to_command(
        command_id=command_id,
        account_id=ACCOUNT,
        base_asset="BTC",
        quote_asset="USDT",
        deployment_id=DEPLOYMENT,
        decision_id=f"decision-{command_id}",
    )


def _record_fill(
    service: TradingService,
    cl_ord_id: str,
    exec_id: str,
    qty: str,
    price: str,
    *,
    occurred_at_ms: int = T0 + 1_000,
) -> None:
    """Book a real execution fact through the same service method as the venue path."""
    applied = asyncio.run(
        service.record_execution(
            Execution(
                account_id=ACCOUNT,
                cl_ord_id=cl_ord_id,
                symbol="BTCUSDT",
                exec_id=exec_id,
                qty=Decimal(qty),
                price=Decimal(price),
                fee=Decimal("0"),
                fee_asset="",
                occurred_at_ms=occurred_at_ms,
            )
        )
    )
    assert applied is True


def _snapshot(
    *,
    at_ms: int = T0,
    balances: dict[str, Decimal] | None = None,
    generation: int = 1,
) -> ReconcileSnapshot:
    return ReconcileSnapshot(
        account_id=ACCOUNT,
        checked_at_ms=at_ms,
        generation=generation,
        balances=balances or {"USDT": Decimal("1000"), "BTC": Decimal("0.5")},
        marks={"BTCUSDT": {"price": Decimal("200"), "at_ms": at_ms}},
    )


class _ForbiddenVenue:
    """Any venue call is a contract breach in the read/safety surfaces."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def _record(self, kind: str, key: str) -> None:
        self.calls.append((kind, key))
        raise AssertionError("control surface must never reach the venue")

    async def place_order(self, command, cl_ord_id):
        self._record("place", cl_ord_id)

    async def cancel_order(self, account_id, cl_ord_id, command_id, symbol):
        self._record("cancel", cl_ord_id)

    async def query_order(self, account_id, cl_ord_id, symbol):
        self._record("query", cl_ord_id)


class _AcceptingVenue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def place_order(self, command, cl_ord_id):
        from trader.trading.models import ExchangeOrderResult, ExchangeOutcome

        self.calls.append(("place", cl_ord_id))
        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "broker-p6ind", "OPEN")

    async def cancel_order(self, account_id, cl_ord_id, command_id, symbol):
        from trader.trading.models import ExchangeOrderResult, ExchangeOutcome

        self.calls.append(("cancel", cl_ord_id))
        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "broker-p6ind", "CANCELLED")

    async def query_order(self, account_id, cl_ord_id, symbol):
        from trader.trading.models import ExchangeOrderResult, ExchangeOutcome

        self.calls.append(("query", cl_ord_id))
        return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)


async def _ready_service(venue) -> TradingService:
    store = MemoryTradingStore()
    service = TradingService(store, venue, RiskPolicy(), clock=lambda: T0)
    await service.recover(_snapshot())
    # A strategy book must exist before a BUY can reserve against it; moving
    # the quote asset out of UNALLOCATED keeps the account total unchanged.
    await service.allocate(
        "alloc-p6ind-bootstrap", ACCOUNT, BOOK, "USDT", Decimal("400"), reason="fixture"
    )
    async with store.transaction(ACCOUNT) as state:
        state.control["runtime"] = {
            "owner": "p6ind-owner",
            "token": 1,
            "ready": True,
            "at_ms": T0,
            "reasons": [],
            "strategy": {
                "deployment_id": DEPLOYMENT,
                "strategy_id": STRATEGY,
                "book_id": BOOK,
                "version": "7",
                "symbols": ["BTCUSDT"],
                "warmup_events": 12,
                "warmup_state": "WARM",
                "warmup_reason": "",
                "warmup_remaining": 0,
                "strategy_ready": True,
            },
        }
    return service


def _client(monkeypatch, venue) -> TestClient:
    monkeypatch.setenv("QTS_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("QTS_INSTRUMENTS", '{"BTCUSDT":["BTC","USDT"]}')
    service = asyncio.run(_ready_service(venue))
    return TestClient(create_trading_app(trading_service=service))


# ===================================================== 6.3-6.8, 6.11: single stack
RETIRED_MODULES = (
    "trader.api.main",
    "trader.api.connections",
    "trader.api.routes.orders",
    "trader.api.routes.monitor",
    "trader.api.routes.killswitch",
    "trader.api.routes.reconciler",
    "trader.api.routes.strategies",
    "trader.api.routes.deployments",
    "trader.api.routes.backtests",
    "trader.api.routes.allocations",
    "trader.api.routes.brokers",
    "trader.api.routes.portfolio",
    "trader.api.routes.events",
    "trader.core.application.oms",
    "trader.services.order",
    "trader.services.execution_budget",
    "trader.services.strategy_runner",
    "trader.services.oms_callback",
    "trader.services.account_state",
    "trader.services.account_stream_bridge",
    "trader.services.deployment",
    "trader.services.strategy_candidate",
    "trader.services.strategy_auto_pause",
    "trader.services.strategy_runtime_orchestrator",
    "trader.services.monitor_service",
    "trader.services.reconciler_service",
    "trader.services.killswitch",
    "trader.services.broker",
    "trader.services.event",
    "trader.services.portfolio",
    "trader.services.portfolio_autopilot",
    "trader.services.capital_allocator",
    "trader.services.nav_service",
    "trader.services.heartbeat",
)

RETIRED_FILES = (
    "trader/api/main.py",
    "trader/api/connections.py",
    "trader/core/application/oms.py",
    "trader/services/order.py",
    "trader/services/execution_budget.py",
    "trader/services/strategy_runner.py",
    "trader/services/oms_callback.py",
    "trader/services/account_state.py",
    "trader/services/account_stream_bridge.py",
    "trader/adapters/persistence/account_state_repository.py",
    "trader/adapters/persistence/budget_reservation_repository.py",
)


def test_retired_modules_cannot_be_imported() -> None:
    importable: list[tuple[str, str]] = []
    for name in RETIRED_MODULES:
        try:
            spec = importlib.util.find_spec(name)
        except ModuleNotFoundError:
            continue
        if spec is not None and spec.loader is not None:
            importable.append((name, spec.origin or "?"))
    assert importable == []


def test_retired_files_are_deleted() -> None:
    missing = [relative for relative in RETIRED_FILES if (ROOT / relative).exists()]
    assert missing == []


def test_production_does_not_reference_retired_modules_by_import_or_text() -> None:
    import_offenders: list[str] = []
    text_offenders: list[str] = []
    for path in _production_files():
        imported = _imported_modules(path)
        for name in RETIRED_MODULES:
            if name in imported or any(item.startswith(name + ".") for item in imported):
                import_offenders.append(f"{path.relative_to(ROOT).as_posix()} -> {name}")
        text = path.read_text(encoding="utf-8")
        for name in RETIRED_MODULES:
            # Exact dotted path only: trader.services.portfolio must not match
            # trader.services.portfolio_research_workflow.
            if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w.])", text):
                text_offenders.append(f"{path.relative_to(ROOT).as_posix()} mentions {name}")
    assert import_offenders == []
    assert text_offenders == []


def test_no_runnable_repo_module_imports_the_deleted_root_services_or_retired_modules() -> None:
    """Scripts and top-level tooling are in scope too: a leftover import there
    would resurrect the legacy stack outside the trader package (the final
    implementation freeze removed exactly one such script)."""
    offenders: list[str] = []
    for path in _repo_python_files():
        imported = _imported_modules(path)
        for name in RETIRED_MODULES:
            if name in imported or any(item.startswith(name + ".") for item in imported):
                offenders.append(f"{path.relative_to(ROOT).as_posix()} -> {name}")
        if any(name == "services" or name.startswith("services.") for name in imported):
            offenders.append(f"{path.relative_to(ROOT).as_posix()} -> root services package")
    assert offenders == []


def test_there_is_exactly_one_http_assembly_and_no_second_entrypoint() -> None:
    definitions = [
        path.relative_to(ROOT).as_posix()
        for path in _production_files()
        if "create_trading_app" in _defined_names(path, ast.FunctionDef)
    ]
    assert definitions == ["trader/api/trading_app.py"]
    assert not (ROOT / "trader/api/main.py").exists()
    compose = (ROOT / "compose.yml").read_text(encoding="utf-8")
    assert "trader.api.main" not in compose


def test_control_route_table_exposes_no_fact_write_entry() -> None:
    app = create_trading_app()
    entries = {
        (getattr(route, "path", ""), method)
        for route in app.routes
        for method in (getattr(route, "methods", set()) or set())
    }
    paths = {path for path, _method in entries}
    for forbidden in (
        "/v1/trading/executions",
        "/v1/trading/observations",
        "/v1/trading/reconcile",
        "/v1/trading/resolve/{code}",
    ):
        assert forbidden not in paths
    # The compatibility surface that remains must be mounted exactly once.
    for required in (
        "/v1/monitor/snapshot",
        "/v1/orders",
        "/v1/executions",
        "/v1/killswitch",
        "/v1/reconciler/report",
        "/v1/strategies/loaded",
    ):
        assert required in paths


def test_only_the_account_uow_writes_executions_and_ledger_rows() -> None:
    execution_writers: list[str] = []
    ledger_writers: list[str] = []
    for path in _production_files():
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT).as_posix()
        if "INSERT INTO trading_executions" in text:
            execution_writers.append(relative)
        if "INSERT INTO trading_ledger" in text:
            ledger_writers.append(relative)
    assert execution_writers == ["trader/trading/postgres.py"]
    assert ledger_writers == ["trader/trading/postgres.py"]


def test_only_one_execution_booking_implementation_exists() -> None:
    booking_definitions: list[str] = []
    for path in _production_files():
        names = set(_defined_names(path, ast.AsyncFunctionDef)) | set(
            _defined_names(path, ast.FunctionDef)
        )
        if names & {"_book_execution", "record_execution"}:
            booking_definitions.append(path.relative_to(ROOT).as_posix())
    # service.py owns the booking rules; private_session only forwards to it.
    assert booking_definitions == [
        "trader/trading/private_session.py",
        "trader/trading/service.py",
    ]
    session = (TRADER / "trading/private_session.py").read_text(encoding="utf-8")
    assert "service.record_execution" in session


LEGACY_WRITE_METHODS = {
    "save_balances",
    "save_lot",
    "update_lot_on_reduce",
    "save_position_projection",
    "save_reconciliation",
    "save_strategy_runtime_state",
    "save_killswitch_state",
    "save_budget_reservation",
}


def test_no_production_calls_a_legacy_account_or_projection_writer() -> None:
    offenders: list[str] = []
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in LEGACY_WRITE_METHODS:
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{name}")
    assert offenders == []


def test_only_unified_strategy_runner_produces_risk_commands() -> None:
    legacy_runner_definitions: list[str] = []
    unified_definitions: list[str] = []
    for path in _production_files():
        names = set(_defined_names(path, ast.ClassDef))
        if "StrategyRunner" in names:
            legacy_runner_definitions.append(path.relative_to(ROOT).as_posix())
        if "UnifiedStrategyRunner" in names:
            unified_definitions.append(path.relative_to(ROOT).as_posix())
    assert legacy_runner_definitions == []
    assert len(unified_definitions) == 1


def test_backtest_application_requires_an_eligibility_gate() -> None:
    from trader.trading.jobs import BacktestApplication

    with pytest.raises(ValueError, match="DATASET_ELIGIBILITY_GATE_UNAVAILABLE"):
        BacktestApplication(object())
    offenders = []
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "BacktestApplication" and not any(
                keyword.arg == "eligibility" for keyword in node.keywords
            ):
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


# ============================================== 13.17: control compatibility views
def test_control_views_project_the_unified_account(monkeypatch) -> None:
    venue = _ForbiddenVenue()
    client = _client(monkeypatch, venue)

    snapshot = client.get("/v1/monitor/snapshot")
    assert snapshot.status_code == 200
    body = snapshot.json()
    # Hand calculation: 0.5 BTC marked at 200 = 100 USDT exposure.
    assert body["snapshot_source"] == "new_stack_account_state"
    assert body["total_positions"] == 0
    assert body["total_exposure"] == "0"
    assert body["killswitch_level"] == 0
    assert body["adapters"]["execution"]["status"] == "HEALTHY"
    assert body["freshness"].endswith("Z")

    assert client.get("/v1/orders").json() == []
    assert client.get("/v1/executions").json() == []
    brokers = client.get("/v1/brokers").json()
    assert len(brokers) == 1 and brokers[0]["account_id"] == ACCOUNT
    assert brokers[0]["status"] == "READY"
    assert brokers[0]["capabilities"]["symbols"] == ["BTCUSDT"]
    pairs = client.get("/v1/exchange/trading-pairs").json()
    assert pairs["source"] == "QTS_INSTRUMENTS"
    assert len(pairs["pairs"]) == 1
    pair = pairs["pairs"][0]
    assert pair["symbol"] == "BTCUSDT"
    assert pair["base_asset"] == "BTC"
    assert pair["quote_asset"] == "USDT"
    # The compatibility response field names the market state, not the config source.
    assert pair["status"] == "TRADING"

    # Honest empty answers for state the new stack does not keep.
    assert client.get("/v1/portfolio/positions").json() == []
    lots = client.get(f"/v1/portfolio/positions/BTCUSDT/lots?strategy_id={BOOK}")
    assert lots.status_code == 200 and lots.json() == []

    assert venue.calls == [], "read views must not construct or call a venue client"


def test_killswitch_is_a_durable_control_command_not_a_local_flag(monkeypatch) -> None:
    venue = _ForbiddenVenue()
    client = _client(monkeypatch, venue)
    service = client.app.state.trading_service

    blocked = client.post(
        "/v1/killswitch",
        json={
            "scope": "GLOBAL",
            "level": 2,
            "reason": "independent drill",
            "updated_by": "tester",
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["level"] == 1
    assert blocked.json()["reason"] == "independent drill"
    command_id = blocked.json()["command_id"]

    state = asyncio.run(service.store.read(ACCOUNT))
    assert state.control["new_risk_blocked"] is True
    record = state.commands[command_id]
    assert record.kind == "CONTROL" and record.status == "CONFIRMED"
    requested = [e for e in state.events if e["event_type"] == "control.requested"]
    assert requested and requested[-1]["payload"]["action"] == "BLOCK_NEW_RISK"

    gate = client.get("/v1/safety-gate/status").json()
    assert gate["live_trading_enabled"] is False and gate["killswitch_level"] == 1

    resumed = client.post(
        "/v1/killswitch",
        json={"scope": "GLOBAL", "level": 0, "reason": "resume", "updated_by": "tester"},
    )
    assert resumed.status_code == 200 and resumed.json()["level"] == 0
    state = asyncio.run(service.store.read(ACCOUNT))
    assert state.control["new_risk_blocked"] is False
    assert venue.calls == []


def test_safety_gate_requires_confirmation_to_resume(monkeypatch) -> None:
    client = _client(monkeypatch, _ForbiddenVenue())
    refused = client.post("/v1/safety-gate/enable", json={"enabled": True, "confirmed": False})
    assert refused.status_code == 400
    enabled = client.post("/v1/safety-gate/enable", json={"enabled": True, "confirmed": True})
    assert enabled.status_code == 200 and enabled.json()["live_trading_enabled"] is True


def test_orders_executions_strategy_and_deployment_events(monkeypatch) -> None:
    venue = _AcceptingVenue()
    client = _client(monkeypatch, venue)
    service = client.app.state.trading_service
    receipt = asyncio.run(service.place(_place_command("cmd-p6ind-1")))
    assert receipt.status == "CONFIRMED"
    cl_ord_id = receipt.cl_ord_id

    orders = client.get("/v1/orders").json()
    assert len(orders) == 1
    order = orders[0]
    assert order["cl_ord_id"] == cl_ord_id
    assert order["instrument"] == "BTCUSDT"
    assert Decimal(str(order["qty"])) == Decimal("1")
    assert order["deployment_id"] == DEPLOYMENT
    assert order["strategy_id"] == BOOK

    assert client.get("/v1/executions").json() == []

    loaded = client.get("/v1/strategies/loaded").json()
    assert len(loaded) == 1
    assert loaded[0]["deployment_id"] == DEPLOYMENT
    assert loaded[0]["strategy_id"] == STRATEGY
    assert loaded[0]["strategy_ready"] is True
    assert loaded[0]["warmup_state"] == "WARM"
    status = client.get(f"/v1/deployments/{DEPLOYMENT}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "running"
    assert client.get("/v1/deployments/other-dep/status").status_code == 404

    events = client.get(f"/v1/deployments/{DEPLOYMENT}/events").json()
    # The generic feed is chronological order evidence: the queue record and the
    # venue acceptance are both real facts. The signals view is the narrow one.
    assert [row["event_type"] for row in events] == ["order.queued", "order.accepted"]
    assert events[0]["trace_id"] == "decision-cmd-p6ind-1"
    signals = client.get(f"/v1/deployments/{DEPLOYMENT}/events/signals").json()
    assert [row["event_type"] for row in signals] == ["order.queued"]
    assert client.get(f"/v1/deployments/{DEPLOYMENT}/events/fills").json() == []
    assert client.get("/v1/deployments/other-dep/events").json() == []
    assert venue.calls == [("place", cl_ord_id)]


def test_control_views_use_booked_fills_for_avg_price_and_exposure(monkeypatch) -> None:
    """A real service fill must drive both the order average and the monitor exposure.

    Hand calculation: LIMIT 1 BTC @200, booked fill 0.25 BTC @180.
    - order avg_price = 180 (not the 200 order price);
    - monitor position = BTCUSDT 0.25 marked at 200 = 50.00 USDT exposure.
    A second fill 0.25 @220 must make the weighted average (45 + 55) / 0.5 = 200.
    """
    venue = _AcceptingVenue()
    client = _client(monkeypatch, venue)
    service = client.app.state.trading_service
    receipt = asyncio.run(service.place(_place_command("cmd-p6ind-fill-1")))
    assert receipt.status == "CONFIRMED"
    cl_ord_id = receipt.cl_ord_id

    order = client.get("/v1/orders").json()[0]
    assert order["cl_ord_id"] == cl_ord_id
    assert order["avg_price"] is None

    _record_fill(service, cl_ord_id, "fill-p6ind-1", "0.25", "180")
    order = client.get("/v1/orders").json()[0]
    assert Decimal(str(order["avg_price"])) == Decimal("180")

    snapshot = client.get("/v1/monitor/snapshot").json()
    positions = [row for row in snapshot["positions"] if row["symbol"] == "BTCUSDT"]
    assert len(positions) == 1
    position = positions[0]
    assert Decimal(str(position["quantity"])) == Decimal("0.25")
    assert Decimal(str(position["current_price"])) == Decimal("200")
    assert Decimal(str(position["exposure"])) == Decimal("50.00")
    assert Decimal(str(snapshot["total_exposure"])) == Decimal("50.00")

    _record_fill(service, cl_ord_id, "fill-p6ind-2", "0.25", "220")
    order = client.get("/v1/orders").json()[0]
    assert Decimal(str(order["avg_price"])) == Decimal("200")

    snapshot = client.get("/v1/monitor/snapshot").json()
    position = [row for row in snapshot["positions"] if row["symbol"] == "BTCUSDT"][0]
    assert Decimal(str(position["quantity"])) == Decimal("0.5")
    assert Decimal(str(position["exposure"])) == Decimal("100.00")
    assert Decimal(str(snapshot["total_exposure"])) == Decimal("100.00")
    assert venue.calls == [("place", cl_ord_id)]


def test_monitor_snapshot_reports_unknown_mark_as_null_exposure(monkeypatch) -> None:
    """An unmarkable position must not be summed into a zero exposure."""
    venue = _AcceptingVenue()
    client = _client(monkeypatch, venue)
    service = client.app.state.trading_service
    receipt = asyncio.run(service.place(_place_command("cmd-p6ind-fill-nomark")))
    assert receipt.status == "CONFIRMED"
    _record_fill(service, receipt.cl_ord_id, "fill-p6ind-nomark", "0.25", "180")

    async def drop_mark() -> None:
        async with service.store.transaction(ACCOUNT) as state:
            state.reconciliation["marks"].pop("BTCUSDT", None)

    asyncio.run(drop_mark())
    snapshot = client.get("/v1/monitor/snapshot").json()
    positions = [row for row in snapshot["positions"] if row["symbol"] == "BTCUSDT"]
    assert len(positions) == 1
    position = positions[0]
    assert position["current_price"] is None
    assert position["exposure"] is None
    assert snapshot["total_exposure"] is None


def test_reconciler_report_is_honest_about_absent_and_present_facts(monkeypatch) -> None:
    service = asyncio.run(_ready_service(_ForbiddenVenue()))
    store = service.store
    empty_account = "p6ind_unreconciled"
    monkeypatch.setenv("QTS_ACCOUNT_ID", empty_account)
    empty_client = TestClient(create_trading_app(trading_service=service))
    assert empty_client.get("/v1/reconciler/report").status_code == 404

    async def seed() -> None:
        async with store.transaction(ACCOUNT) as state:
            state.reconciliation.setdefault("unresolved", []).append(
                {"code": "BALANCE_MISMATCH", "detail": {"asset": "USDT"}, "at_ms": T0}
            )

    asyncio.run(seed())
    report = empty_client.get(f"/v1/reconciler/report?account_id={ACCOUNT}").json()
    assert report["diverged_count"] == 1
    assert report["drifts"][0]["local_status"] == "BALANCE_MISMATCH"
    assert report["total_orders_checked"] == 0


def test_retired_writes_refuse_with_stable_codes(monkeypatch) -> None:
    client = _client(monkeypatch, _ForbiddenVenue())
    retired = (
        ("post", f"/v1/strategies/{STRATEGY}/load", 410, "LEGACY_STRATEGY_LIFECYCLE_RETIRED"),
        ("post", f"/v1/deployments/{DEPLOYMENT}/start", 410, "LEGACY_STRATEGY_LIFECYCLE_RETIRED"),
        ("post", f"/v1/deployments/{DEPLOYMENT}/stop", 410, "LEGACY_STRATEGY_LIFECYCLE_RETIRED"),
        ("post", f"/v1/deployments/{DEPLOYMENT}/pause", 410, "LEGACY_STRATEGY_LIFECYCLE_RETIRED"),
        ("post", "/v1/replay", 410, "LEGACY_REPLAY_RETIRED"),
        ("get", "/v1/snapshots/latest", 410, "LEGACY_REPLAY_RETIRED"),
        ("post", "/v1/reconciler/trigger", 503, "RECONCILIATION_IS_EXECUTION_OWNED"),
        ("post", "/v1/orders/does-not-exist/cancel", 503, "LEGACY_CANCEL_RETIRED"),
        ("put", f"/v1/allocations/{DEPLOYMENT}", 503, "LEGACY_ALLOCATION_RETIRED"),
    )
    for method, path, status, code in retired:
        response = getattr(client, method)(path)
        assert response.status_code == status, (method, path, response.text)
        assert code in str(response.json().get("detail", ""))


# ============================== 6.2/frozen defects: terminal map and allocation fence
def test_expired_in_match_is_terminal_cancelled_for_the_recovery_read() -> None:
    broker = BinanceSpotDemoBroker(
        BinanceSpotDemoBrokerConfig.for_demo(api_key="key", secret_key="secret")
    )
    # The broker owns its domain enum; compare the semantic value, not object
    # identity with trader.trading.models.OrderStatus.
    assert broker._parse_order_status("EXPIRED_IN_MATCH").value == "CANCELLED"
    payload = {
        "orderId": "7001",
        "clientOrderId": "qts-p6ind-stp",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "origQty": "1.00000000",
        "executedQty": "0.00000000",
        "avgPrice": "0.00000000",
        "status": "EXPIRED_IN_MATCH",
        "updateTime": T0,
    }
    order = broker._parse_order_response(payload)
    assert order.status.value == "CANCELLED"
    assert order.broker_order_id == "7001" and order.cl_ord_id == "qts-p6ind-stp"
    # Documented Binance spot statuses must all map; unknown values must still fail.
    for raw, expected in {
        "NEW": "SUBMITTED",
        "PARTIALLY_FILLED": "PARTIALLY_FILLED",
        "FILLED": "FILLED",
        "CANCELED": "CANCELLED",
        "PENDING_CANCEL": "CANCEL_PENDING",
        "REJECTED": "REJECTED",
        "EXPIRED": "CANCELLED",
    }.items():
        assert broker._parse_order_status(raw).value == expected
    with pytest.raises(ValueError, match="Unsupported order status"):
        broker._parse_order_status("SOME_FUTURE_STATUS")


def test_queued_allocate_waits_for_the_first_snapshot_then_moves_assets() -> None:
    async def run() -> None:
        store = MemoryTradingStore()
        # The control process records the intent; only the execution owner drains it.
        control = TradingService(
            store, _ForbiddenVenue(), RiskPolicy(), clock=lambda: T0, defer_send=True
        )
        executor = TradingService(store, _ForbiddenVenue(), RiskPolicy(), clock=lambda: T0)
        # Without reconciliation, booking the allocation would permanently fail:
        # there is no UNALLOCATED balance yet.
        fresh = await store.read(ACCOUNT)
        with pytest.raises(Exception, match="ALLOCATION_INSUFFICIENT_UNALLOCATED"):
            trading_core.allocate_to_book(fresh, BOOK, "USDT", Decimal("400"))

        receipt = await control.allocate(
            "alloc-p6ind", ACCOUNT, BOOK, "USDT", Decimal("400"), reason="drill"
        )
        assert receipt.status == "QUEUED"
        assert receipt.error == ""

        before = await executor.resume_queued(ACCOUNT)
        allocation_receipt = [item for item in before if item.kind == "ALLOCATE"]
        assert allocation_receipt and allocation_receipt[0].status == "QUEUED"
        assert allocation_receipt[0].error == ""
        state = await store.read(ACCOUNT)
        assert state.commands["alloc-p6ind"].status == "QUEUED"
        assert BOOK not in state.books
        assert state.reconciliation.get("checked_at_ms") is None

        await executor.recover(_snapshot())
        after = await executor.resume_queued(ACCOUNT)
        allocation_receipt = [item for item in after if item.kind == "ALLOCATE"]
        assert allocation_receipt and allocation_receipt[0].status == "CONFIRMED"
        state = await store.read(ACCOUNT)
        assert state.commands["alloc-p6ind"].status == "CONFIRMED"
        assert state.books[BOOK].balances["USDT"] == Decimal("400")
        unallocated = state.books["UNALLOCATED"].balances["USDT"]
        assert unallocated == Decimal("600")
        assert state.balances["USDT"] == Decimal("1000")
        total = state.books[BOOK].balances["USDT"] + state.books["UNALLOCATED"].balances["USDT"]
        assert state.balances["USDT"] == total

        # A second drain is idempotent: confirmed commands are not applied twice.
        again = await executor.resume_queued(ACCOUNT)
        assert [item for item in again if item.kind == "ALLOCATE"] == []
        state = await store.read(ACCOUNT)
        assert state.books[BOOK].balances["USDT"] == Decimal("400")

    asyncio.run(run())


# ==================================================== readiness fence in the market loop
class _Warmup:
    value = "WARM"


class _Runner:
    warm = True
    warmup_state = _Warmup()


class _Clock:
    def __call__(self) -> int:
        return T0

    async def sync(self) -> int:
        raise NotReady("VENUE_CLOCK_NOT_SYNCED")


class _KlineBroker:
    def __init__(self, rows=None, error: Exception | None = None) -> None:
        self.rows = rows if rows is not None else [[0, "1", "1", "1", "200", "1", 59_999, 0, 0, 0]]
        self.error = error
        self.calls = 0

    async def get_klines(self, symbol, interval, limit):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.rows


class _LoopRuntime:
    strategy_start_allowed = True
    strategy_runner = _Runner()

    def __init__(self, *, not_ready=False, explode=False, clock=None) -> None:
        self.clock = clock or (lambda: T0)
        self.blocks: list[str] = []
        self.ticks = 0
        if not_ready:
            self._not_ready = True
        if explode:
            self._explode = True

    async def on_market_data(self, data, event_id=None):
        self.ticks += 1
        if getattr(self, "_not_ready", False):
            raise NotReady("TRADING_RUNTIME_NOT_READY")
        if getattr(self, "_explode", False):
            raise RuntimeError("strategy exploded")

    async def block(self, reason):
        self.blocks.append(reason)


async def _drive_loop(runtime, broker, *, seconds: float = 0.2) -> None:
    task = asyncio.create_task(
        _market_loop(
            runtime,
            broker,
            {"BTCUSDT": ("BTC", "USDT")},
            interval="1m",
            poll_seconds=0.01,
        )
    )
    await asyncio.sleep(seconds)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_readiness_fence_from_clock_or_runtime_never_blocks_the_account() -> None:
    clock_runtime = _LoopRuntime(clock=_Clock())
    clock_broker = _KlineBroker()
    await _drive_loop(clock_runtime, clock_broker)
    assert clock_runtime.blocks == []
    assert clock_broker.calls == 0

    fence_runtime = _LoopRuntime(not_ready=True)
    fence_broker = _KlineBroker()
    await _drive_loop(fence_runtime, fence_broker)
    assert fence_runtime.blocks == []
    assert fence_runtime.ticks >= 2, "the loop must keep polling after a fence"
    assert fence_broker.calls >= 2


@pytest.mark.asyncio
async def test_real_market_or_strategy_fault_blocks_new_risk_exactly_once() -> None:
    feed_runtime = _LoopRuntime()
    feed_broker = _KlineBroker(error=RuntimeError("feed down"))
    await _drive_loop(feed_runtime, feed_broker)
    assert set(feed_runtime.blocks) == {"STRATEGY_OR_MARKET_DATA_FAILED"}

    strategy_runtime = _LoopRuntime(explode=True)
    await _drive_loop(strategy_runtime, _KlineBroker())
    assert set(strategy_runtime.blocks) == {"STRATEGY_OR_MARKET_DATA_FAILED"}


def test_worker_rejects_a_reconcile_lookback_longer_than_coverage() -> None:
    path = TRADER / "runtime" / "trading_worker_main.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    guards = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        test = node.test
        names = {child.id for child in ast.walk(test) if isinstance(child, ast.Name)}
        if {"coverage_lookback_ms", "reconcile_lookback_ms"} <= names:
            raised = [
                child
                for child in ast.walk(node)
                if isinstance(child, ast.Raise) and child.exc is not None
            ]
            guards.append(raised)
    assert guards, "the worker must compare the two configurable lookbacks"
    assert "COVERAGE_LOOKBACK_SHORTER_THAN_RECONCILE" in source
    assert "QTS_RECONCILE_LOOKBACK_MS" in source and "QTS_COVERAGE_LOOKBACK_MS" in source
    assert source.count('os.getenv("QTS_RECONCILE_LOOKBACK_MS", "86400000")') == 1
    assert source.count('os.getenv("QTS_COVERAGE_LOOKBACK_MS", "86400000")') == 1


# =============================================================== observation JSONL
class _Response:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            request = httpx.Request("POST", "http://fake/v1/auth/login")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"{self.status_code} auth rejected", request=request, response=response
            )


class _MetricsClient:
    def __init__(self, *, login_status: int = 200) -> None:
        self.login_status = login_status
        self.posts = 0
        self.gets: list[str] = []

    async def post(self, path, json=None):
        self.posts += 1
        assert path == "/v1/auth/login"
        return _Response(self.login_status, {"csrf_token": "csrf-token"})

    async def get(self, path, params=None):
        self.gets.append(path)
        if path == "/health/trading":
            return _Response(200, {"status": "READY", "reasons": []})
        if path == "/v1/trading/metrics":
            return _Response(
                200,
                {
                    "status": "READY",
                    "unknown_commands": 0,
                    "unbooked_fills": 0,
                    "balance_anomalies": [],
                    "active_holds": 1,
                    "reconciliation_age_ms": 1200,
                    "outbox_backlog": 0,
                    "unresolved": [],
                    "unresolved_entries": [],
                    "balance_differences": {},
                    "evidence": {
                        "executions": {"count": 3, "keys_sha256": "e" * 64},
                        "ledger": {"count": 6, "entry_ids_sha256": "l" * 64},
                        "events": {"count": 9, "event_ids_sha256": "v" * 64},
                        "commands_by_status": {"CONFIRMED": 1},
                        "orders_by_status": {"FILLED": 1},
                        "send_outcomes": {"repeated_send_commands": []},
                        "last_execution_at_ms": T0,
                        "last_event_at_ms": T0,
                    },
                    "coverage": {"contiguous": True, "proven_at_ms": T0},
                    "runtime": {"ready": True, "reasons": []},
                    "strategy": {"strategy_id": STRATEGY, "warmup_state": "WARM"},
                    "book_count": 1,
                    "books": [],
                },
            )
        if path == f"/v1/trading/accounts/{ACCOUNT}/risk":
            return _Response(
                200,
                {
                    "status": "READY",
                    "reasons": [],
                    "generation": 1,
                    "checked_at_ms": T0,
                    "account_version": 4,
                    "valuation_complete": True,
                    "positions": {"BTCUSDT": "0.5"},
                    "balance_differences": {},
                },
            )
        raise AssertionError(f"unexpected GET {path}")


class _ReloginMetricsClient:
    """Control client whose 12h session expires on exactly one response.

    The first request to the configured surface answers 401 without raising,
    matching an expired control session. A successful login clears the
    expiration; the retried sample must then carry the normal evidence body.
    """

    def __init__(self, expire_on: str) -> None:
        assert expire_on in {"metrics", "risk"}
        self.expire_on = expire_on
        self.login_count = 0
        self.expired_once = False
        self.calls: list[tuple[str, int]] = []

    async def post(self, path, json=None):
        assert path == "/v1/auth/login"
        self.login_count += 1
        return _Response(200, {"csrf_token": f"csrf-{self.login_count}"})

    async def get(self, path, params=None):
        if path == "/health/trading":
            self.calls.append((path, 200))
            return _Response(200, {"status": "READY", "reasons": []})
        if path == "/v1/trading/metrics":
            if self.expire_on == "metrics" and not self.expired_once:
                self.expired_once = True
                self.calls.append((path, 401))
                return _Response(401, {"detail": "session expired"})
            self.calls.append((path, 200))
            return _Response(
                200,
                {
                    "status": "READY",
                    "unknown_commands": 0,
                    "unbooked_fills": 0,
                    "balance_anomalies": [],
                    "active_holds": 0,
                    "reconciliation_age_ms": 900,
                    "outbox_backlog": 0,
                    "unresolved": [],
                    "unresolved_entries": [],
                    "balance_differences": {},
                    "evidence": {
                        "executions": {"count": 7, "keys_sha256": "e" * 64},
                        "ledger": {"count": 11, "entry_ids_sha256": "l" * 64},
                        "events": {"count": 13, "event_ids_sha256": "v" * 64},
                        "commands_by_status": {"CONFIRMED": 2},
                        "orders_by_status": {"FILLED": 1},
                        "send_outcomes": {"repeated_send_commands": []},
                        "last_execution_at_ms": T0,
                        "last_event_at_ms": T0,
                    },
                    "coverage": {"contiguous": True, "recorded_at_ms": T0},
                    "runtime": {"ready": True, "reasons": []},
                    "strategy": {"strategy_id": STRATEGY, "warmup_state": "WARM"},
                    "book_count": 1,
                    "books": [],
                },
            )
        if path == f"/v1/trading/accounts/{ACCOUNT}/risk":
            if self.expire_on == "risk" and not self.expired_once:
                self.expired_once = True
                self.calls.append((path, 401))
                return _Response(401, {"detail": "session expired"})
            self.calls.append((path, 200))
            return _Response(
                200,
                {
                    "status": "READY",
                    "reasons": [],
                    "generation": 3,
                    "checked_at_ms": T0,
                    "account_version": 9,
                    "valuation_complete": True,
                    "positions": {"BTCUSDT": "0.5"},
                    "balance_differences": {},
                },
            )
        raise AssertionError(f"unexpected GET {path}")


def _sample_args(tmp_path: Path, *, retries: int = 2):
    return SimpleNamespace(
        retries=retries,
        account_id=ACCOUNT,
        username="owner",
        base_url="http://fake",
        out_path=tmp_path / "observation.jsonl",
    )


@pytest.mark.asyncio
async def test_observation_sample_carries_every_contract_field(tmp_path) -> None:
    observer = _script_module("observe_trading_runtime")
    password = "P6-Independent-Secret-9f3"
    client = _MetricsClient()
    state = {"csrf": "", "last_ready": None, "last_monotonic_ms": None}
    line = await observer._sample(client, _sample_args(tmp_path), password, state, 1)
    observer._append(_sample_args(tmp_path).out_path, line)

    for key in (
        "ts_ms",
        "monotonic_ms",
        "seq",
        "sample_gap_ms",
        "health",
        "metrics",
        "risk",
        "ready",
    ):
        assert key in line
    assert line["health"]["status_code"] == 200
    assert line["metrics"]["evidence"]["executions"]["count"] == 3
    assert line["metrics"]["evidence"]["ledger"]["count"] == 6
    assert line["metrics"]["evidence"]["events"]["count"] == 9
    assert "repeated_send_commands" in line["metrics"]["evidence"]["send_outcomes"]
    assert line["metrics"]["unresolved_entries"] == []
    assert line["metrics"]["coverage"]["contiguous"] is True
    assert line["risk"]["status"] == "READY" and line["risk"]["reasons"] == []
    assert line["ready"] is True

    payload = _sample_args(tmp_path).out_path.read_text(encoding="utf-8")
    assert password not in payload
    assert "csrf-token" not in payload
    assert json.loads(payload.splitlines()[0])["seq"] == 1


@pytest.mark.asyncio
async def test_observation_append_fsyncs_each_durable_line(tmp_path, monkeypatch) -> None:
    observer = _script_module("observe_trading_runtime")
    synced: list[int] = []
    monkeypatch.setattr(observer.os, "fsync", lambda fd: synced.append(fd))
    path = tmp_path / "nested" / "durable.jsonl"
    observer._append(path, {"kind": "sample", "seq": 1})
    observer._append(path, {"kind": "sample", "seq": 2})
    assert len(synced) == 2
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["seq"] for row in rows] == [1, 2]


@pytest.mark.asyncio
async def test_observation_writes_explicit_ready_transitions(tmp_path) -> None:
    observer = _script_module("observe_trading_runtime")

    class _FlappingClient(_MetricsClient):
        def __init__(self) -> None:
            super().__init__()
            self.health_codes = [200, 503, 200]

        async def get(self, path, params=None):
            if path == "/health/trading":
                code = self.health_codes.pop(0)
                body = {"status": "READY" if code == 200 else "BLOCKED", "reasons": []}
                if code != 200:
                    body["reasons"] = ["ACCOUNT_NOT_RECONCILED"]
                return _Response(code, body)
            return await super().get(path, params=params)

    args = _sample_args(tmp_path)
    state = {"csrf": "", "last_ready": None, "last_monotonic_ms": None}
    client = _FlappingClient()
    for seq in (1, 2, 3):
        line = await observer._sample(client, args, "pw", state, seq)
        observer._append(args.out_path, line)

    rows = [json.loads(line) for line in args.out_path.read_text(encoding="utf-8").splitlines()]
    transitions = [row for row in rows if row["kind"] == "transition"]
    assert [(row["from_ready"], row["to_ready"]) for row in transitions] == [
        (True, False),
        (False, True),
    ]
    assert transitions[0]["reasons"] == ["ACCOUNT_NOT_RECONCILED"]


@pytest.mark.asyncio
async def test_observation_retries_auth_failure_without_leaking_the_secret(
    tmp_path, monkeypatch
) -> None:
    observer = _script_module("observe_trading_runtime")
    calls = {"sleep": 0}

    async def _no_sleep(_seconds):
        calls["sleep"] += 1

    monkeypatch.setattr(observer.asyncio, "sleep", _no_sleep)
    password = "P6-Independent-Secret-9f3"
    args = _sample_args(tmp_path, retries=2)
    state = {"csrf": "", "last_ready": None, "last_monotonic_ms": None}
    client = _MetricsClient(login_status=401)
    line = await observer._sample(client, args, password, state, 1)
    observer._append(args.out_path, line)

    assert client.posts == 2, "a 401 must be retried, not silently accepted"
    assert len(line["attempts"]) == 2
    assert all(attempt["ok"] is False for attempt in line["attempts"])
    assert "401" in line["error"]
    assert state["csrf"] == ""
    assert calls["sleep"] == 1
    payload = args.out_path.read_text(encoding="utf-8")
    assert password not in payload
    assert "P6-Independent" not in payload


@pytest.mark.asyncio
async def test_observer_metrics_401_relogins_and_keeps_the_same_sample_evidence(
    tmp_path, monkeypatch
) -> None:
    """The metrics response path (not an exception) must renew the session.

    The sample starts with the stale token left by the previous 12h session.
    Its first metrics GET returns 401; the observer drops the token, logs in
    once, and retries inside sample #1, which must end with the full metrics
    and risk bodies.
    """
    observer = _script_module("observe_trading_runtime")
    sleeps: list[float] = []

    async def _no_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(observer.asyncio, "sleep", _no_sleep)
    args = _sample_args(tmp_path, retries=2)
    state = {"csrf": "stale-12h-token", "last_ready": None, "last_monotonic_ms": None}
    client = _ReloginMetricsClient("metrics")

    line = await observer._sample(client, args, "P6-Independent-Secret-9f3", state, 1)

    assert line["seq"] == 1
    assert [entry["ok"] for entry in line["attempts"]] == [False, True]
    assert line["attempts"][0]["error"] == "HTTP 401"
    assert client.login_count == 1
    assert state["csrf"] == "csrf-1"
    assert len(sleeps) == 1
    assert line["metrics_status_code"] == 200
    assert line["metrics"]["evidence"]["executions"]["count"] == 7
    assert line["metrics"]["coverage"] == {"contiguous": True, "recorded_at_ms": T0}
    assert line["risk_status_code"] == 200
    assert line["risk"]["status"] == "READY" and line["risk"]["reasons"] == []
    assert client.calls.count(("/v1/trading/metrics", 401)) == 1
    assert client.calls.count(("/v1/trading/metrics", 200)) == 1
    assert client.calls.count((f"/v1/trading/accounts/{ACCOUNT}/risk", 200)) == 1


@pytest.mark.asyncio
async def test_observer_risk_401_relogins_and_keeps_the_same_sample_evidence(
    tmp_path, monkeypatch
) -> None:
    """The risk response path must renew the session independently of metrics.

    The first metrics GET succeeds, then the risk GET returns 401. Sample #1
    must drop the stale token, log in once, retry both reads, and retain the
    metrics evidence plus a READY risk view.
    """
    observer = _script_module("observe_trading_runtime")
    sleeps: list[float] = []

    async def _no_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(observer.asyncio, "sleep", _no_sleep)
    args = _sample_args(tmp_path, retries=2)
    state = {"csrf": "stale-12h-token", "last_ready": None, "last_monotonic_ms": None}
    client = _ReloginMetricsClient("risk")

    line = await observer._sample(client, args, "P6-Independent-Secret-9f3", state, 1)

    assert line["seq"] == 1
    assert [entry["ok"] for entry in line["attempts"]] == [False, True]
    assert line["attempts"][0]["error"] == "HTTP 401"
    assert client.login_count == 1
    assert state["csrf"] == "csrf-1"
    assert len(sleeps) == 1
    assert line["metrics_status_code"] == 200
    assert line["metrics"]["evidence"]["executions"]["count"] == 7
    assert line["risk_status_code"] == 200
    assert line["risk"]["status"] == "READY" and line["risk"]["reasons"] == []
    assert client.calls.count(("/v1/trading/metrics", 200)) == 2
    assert client.calls.count((f"/v1/trading/accounts/{ACCOUNT}/risk", 401)) == 1
    assert client.calls.count((f"/v1/trading/accounts/{ACCOUNT}/risk", 200)) == 1


def test_observer_credentials_come_from_a_file_never_from_argv(tmp_path, monkeypatch) -> None:
    observer = _script_module("observe_trading_runtime")
    secret_file = tmp_path / "operator_password"
    secret_file.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.delenv("QTS_E2E_PASSWORD", raising=False)
    assert observer._read_password(str(secret_file), "owner") == "file-secret"

    tree = ast.parse((ROOT / "scripts/observe_trading_runtime.py").read_text(encoding="utf-8"))
    options = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            options.add(node.args[0].value)
    assert "--password-file" in options
    assert "--password" not in options
    assert "--dsn" not in options


@pytest.mark.asyncio
async def test_observer_refuses_to_start_without_an_operator_password(
    tmp_path, monkeypatch
) -> None:
    observer = _script_module("observe_trading_runtime")
    monkeypatch.delenv("QTS_E2E_PASSWORD", raising=False)
    monkeypatch.delenv("QTS_OPERATOR_PASSWORD_FILE", raising=False)
    monkeypatch.setattr(observer.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(observer.sys, "argv", ["observe", "--out", str(tmp_path / "missing.jsonl")])
    assert await observer.main() == 2
    assert not (tmp_path / "missing.jsonl").exists()


# ============================================================= supervisor verdict
def _sample(
    ts_ms: int,
    *,
    ready: bool = True,
    reasons=(),
    unresolved=(),
    unresolved_entries=(),
    count: int = 0,
    coverage: bool = True,
    coverage_recorded_at_ms: int | None | object = _COVERAGE_AT_SAMPLE,
    metrics_present: bool = True,
    metrics_status_code: int = 200,
    repeated=(),
    gap: int = 30_000,
) -> dict:
    row = {
        "kind": "sample",
        "ts_ms": ts_ms,
        "sample_gap_ms": gap,
        "ready": ready,
        "health": {
            "status_code": 200 if ready else 503,
            "body": {"status": "READY" if ready else "BLOCKED", "reasons": list(reasons)},
        },
    }
    if not metrics_present:
        # A 401/exception leaves the response without an evidence body. The
        # supervisor must treat this as an evidence gap, not as "nothing seen".
        row["metrics_status_code"] = metrics_status_code
        return row
    if coverage_recorded_at_ms is _COVERAGE_AT_SAMPLE:
        # Healthy fixtures carry a coverage proof timestamped at the sample;
        # tests that need the fail-closed missing-field path pass None.
        coverage_recorded_at_ms = ts_ms
    coverage_row = {"contiguous": coverage}
    if coverage_recorded_at_ms is not None:
        coverage_row["recorded_at_ms"] = int(coverage_recorded_at_ms)
    row["metrics"] = {
        "unresolved": list(unresolved),
        "unresolved_entries": list(unresolved_entries),
        "evidence": {
            "executions": {"count": count},
            "send_outcomes": {"repeated_send_commands": list(repeated)},
        },
        "coverage": coverage_row,
    }
    return row


def _write_window(path: Path, *, start_ms: int, end_ms: int, samples: list[dict]) -> None:
    rows = [{"kind": "marker", "ts_ms": start_ms, "marker": "observer_started"}]
    rows.extend(samples)
    rows.append(
        {"kind": "marker", "ts_ms": end_ms, "marker": "observer_finished", "samples": len(samples)}
    )
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _write_backups(path: Path, completions_ms: list[int]) -> None:
    path.write_text(
        "\n".join(
            json.dumps({"status": "succeeded", "backup": {"completed_at_ms": ms}})
            for ms in completions_ms
        )
        + "\n",
        encoding="utf-8",
    )


def _window_backups(path: Path) -> None:
    """Backups that satisfy the window-level RPO, hand-computed: one fresh at
    T0-120_000 (head age 120_000ms <= 300_000ms), one every 180_000ms through
    T0+DAY_MS (gaps 180_000ms), so the last one is 1_000ms stale at the window
    end T0+DAY_MS+1_000."""
    _write_backups(
        path,
        [T0 - 120_000] + list(range(T0, T0 + DAY_MS + 1, 180_000)),
    )


def _supervisor(tmp_path: Path, *, aborted_reason: str = "", dr_successes: int = 2):
    module = _script_module("sandbox_supervisor")
    supervisor = object.__new__(module.SandboxSupervisor)
    supervisor.observation_path = tmp_path / "observation.jsonl"
    supervisor.backup_dir = tmp_path / "backup"
    supervisor.backup_dir.mkdir(parents=True, exist_ok=True)
    supervisor.dr_evidence_path = tmp_path / "dr" / "dr-evidence.json"
    supervisor.dr_journal_path = tmp_path / "dr" / "dr-cycles.jsonl"
    supervisor.feeder = SimpleNamespace(
        state={"cycles": 40, "successes": dr_successes, "failures": 0}
    )
    supervisor.start_ms = T0
    supervisor.aborted_reason = aborted_reason
    supervisor.run_id = "p6ind"
    supervisor.project = "qts_p6ind"
    supervisor.args = SimpleNamespace(duration_hours=24, sample_seconds=30.0, reconcile_seconds=300)
    return supervisor


def _dense_samples(*, start_ms: int = T0, end_ms: int = T0 + DAY_MS + 1_000, overrides=None):
    """One ready sample every 30s so a marker-bounded window is fully covered."""
    overrides = dict(overrides or {})
    samples = []
    index = 0
    ts = start_ms + 1_000
    while ts <= end_ms - 1_000:
        samples.append(overrides.get(index) or _sample(ts, count=index))
        index += 1
        ts += 30_000
    return samples


def _clean_window(tmp_path: Path, **overrides):
    supervisor = _supervisor(tmp_path, **overrides)
    _write_window(
        supervisor.observation_path,
        start_ms=T0,
        end_ms=T0 + DAY_MS + 1_000,
        samples=_dense_samples(),
    )
    _window_backups(supervisor.backup_dir / "backup-journal.jsonl")
    return supervisor


def test_clean_window_passes_using_observer_markers(tmp_path) -> None:
    summary = _clean_window(tmp_path)._summarize()
    assert all(summary["criteria"].values()), summary["criteria"]
    assert summary["passed"] is True
    assert summary["started_at_ms"] == T0
    assert summary["ended_at_ms"] == T0 + DAY_MS + 1_000
    assert summary["duration_seconds"] == DAY_MS // 1000 + 1
    assert summary["samples"] == 2880
    assert summary["expected_samples"] == 2880
    assert summary["min_samples"] == 2592
    assert summary["sample_gap_limit_ms"] == 180_000
    assert summary["sample_gap_max_ms"] == 30_000
    assert summary["execution_counts"] == list(range(2880))


def _thirty_second_samples(*, evidenced_until: int) -> list[dict]:
    """2880 samples at exactly 30s spacing across [T0, T0+24h).

    ``evidenced_until=1440`` means the first 1440 scheduled samples (12h of
    samples) carry a full metrics body and every later one is a 401 response
    with no body. The expectation is derived only from these timestamps.
    """
    samples = []
    for index, ts_ms in enumerate(range(T0, T0 + DAY_MS, 30_000)):
        present = index < evidenced_until
        samples.append(
            _sample(
                ts_ms,
                count=index,
                metrics_present=present,
                metrics_status_code=200 if present else 401,
                coverage_recorded_at_ms=ts_ms if present else None,
            )
        )
    return samples


def _thirty_second_samples_ending_at_end(*, last_coverage_age_ms: int | None) -> list[dict]:
    """2880 samples over (T0, T0+24h], with the last sample exactly at the end.

    Every sample carries full metrics. The last sample's coverage proof is
    deliberately backdated by ``last_coverage_age_ms`` (``None`` omits the
    field). Hand calculation for the default reconcile cadence: the freshness
    bound is 2*300_000 + 180_000 = 780_000ms.
    """
    end_ms = T0 + DAY_MS
    samples = []
    for index, ts_ms in enumerate(range(T0 + 30_000, end_ms + 1, 30_000)):
        if ts_ms == end_ms and last_coverage_age_ms is not None:
            recorded_at_ms = end_ms - last_coverage_age_ms
        elif ts_ms == end_ms:
            recorded_at_ms = None
        else:
            recorded_at_ms = ts_ms
        samples.append(_sample(ts_ms, count=index, coverage_recorded_at_ms=recorded_at_ms))
    return samples


def _summarize_end_coverage(tmp_path: Path, *, last_coverage_age_ms: int | None):
    supervisor = _supervisor(tmp_path)
    _write_window(
        supervisor.observation_path,
        start_ms=T0,
        end_ms=T0 + DAY_MS,
        samples=_thirty_second_samples_ending_at_end(last_coverage_age_ms=last_coverage_age_ms),
    )
    _window_backups(supervisor.backup_dir / "backup-journal.jsonl")
    return supervisor._summarize()


def test_metrics_evidence_stopping_at_half_the_window_cannot_pass(tmp_path) -> None:
    """24h dense window: 1440 evidenced samples then 1440 samples without a
    metrics body. Hand calculations:

    * expected = 86_400_000 / 30_000 = 2880; min = 2592;
    * present = 1440 < 2592, missing = 1440;
    * last evidence at T0 + 1439*30_000 = T0+43_170_000, so the tail gap is
      86_400_000 - 43_170_000 = 43_230_000ms > 180_000ms.
    """
    supervisor = _supervisor(tmp_path)
    _write_window(
        supervisor.observation_path,
        start_ms=T0,
        end_ms=T0 + DAY_MS,
        samples=_thirty_second_samples(evidenced_until=1440),
    )
    _window_backups(supervisor.backup_dir / "backup-journal.jsonl")

    summary = supervisor._summarize()
    criteria = summary["criteria"]
    assert summary["duration_seconds"] == 86_400
    assert summary["expected_samples"] == 2880
    assert summary["min_samples"] == 2592
    assert summary["samples"] == 2880
    assert summary["metrics_samples"] == 1440
    assert summary["metrics_missing"] == 1440
    assert summary["metrics_gap_max_ms"] == 43_230_000
    assert criteria["samples_cover_window"] is True
    assert criteria["metrics_samples_present"] is False
    assert criteria["metrics_gap_within_bound"] is False
    assert criteria["coverage_fresh_at_end"] is False
    assert criteria["unresolved_cleared_at_end"] is False
    assert summary["passed"] is False


def test_a_fully_evidenced_24h_window_passes(tmp_path) -> None:
    """The positive control: every one of the 2880 samples carries a complete
    metrics body and a coverage proof recorded at that sample. Hand
    calculations: present=2880, missing=0, largest gap=max(30_000, 30_000)
    =30_000ms, tail coverage age=30_000ms."""
    supervisor = _supervisor(tmp_path)
    _write_window(
        supervisor.observation_path,
        start_ms=T0,
        end_ms=T0 + DAY_MS,
        samples=_thirty_second_samples(evidenced_until=2880),
    )
    _window_backups(supervisor.backup_dir / "backup-journal.jsonl")

    summary = supervisor._summarize()
    criteria = summary["criteria"]
    assert summary["metrics_samples"] == 2880
    assert summary["metrics_missing"] == 0
    assert summary["metrics_gap_max_ms"] == 30_000
    assert criteria["metrics_samples_present"] is True
    assert criteria["metrics_gap_within_bound"] is True
    assert criteria["coverage_fresh_at_end"] is True
    assert criteria["unresolved_cleared_at_end"] is True
    assert all(criteria.values()), criteria
    assert summary["passed"] is True


def test_coverage_freshness_margin_allows_reconcile_phase_and_fails_closed(tmp_path) -> None:
    """Hand calculation for reconcile=300s and sample slack=180s:

    limit = 2*300_000 + 180_000 = 780_000ms. Coverage refreshed on a 300s
    reconcile cadence can be 0..300s old at the window end; every such phase
    must pass. Exactly 780_000ms still passes, 780_001ms fails, and a missing
    ``recorded_at_ms`` fails closed rather than falling back to sample time.
    """
    for offset_seconds in (0, 60, 120, 180, 240, 300):
        summary = _summarize_end_coverage(
            tmp_path / f"phase-{offset_seconds}",
            last_coverage_age_ms=offset_seconds * 1_000,
        )
        assert summary["coverage_fresh_limit_ms"] == 780_000
        assert summary["criteria"]["coverage_fresh_at_end"] is True
        assert summary["passed"] is True

    boundary = _summarize_end_coverage(tmp_path / "boundary", last_coverage_age_ms=780_000)
    assert boundary["coverage_fresh_limit_ms"] == 780_000
    assert boundary["criteria"]["coverage_fresh_at_end"] is True
    assert boundary["passed"] is True

    over = _summarize_end_coverage(tmp_path / "over", last_coverage_age_ms=780_001)
    assert over["coverage_fresh_limit_ms"] == 780_000
    assert over["criteria"]["coverage_fresh_at_end"] is False
    assert over["passed"] is False

    missing = _summarize_end_coverage(tmp_path / "missing", last_coverage_age_ms=None)
    assert missing["criteria"]["coverage_fresh_at_end"] is False
    assert missing["passed"] is False


def test_sparse_samples_cannot_pass_a_24h_window(tmp_path) -> None:
    supervisor = _supervisor(tmp_path)
    _write_window(
        supervisor.observation_path,
        start_ms=T0,
        end_ms=T0 + DAY_MS + 1_000,
        samples=[
            _sample(T0 + 1_000, count=0),
            _sample(T0 + DAY_MS // 2, count=0),
            _sample(T0 + DAY_MS - 1_000, count=0),
        ],
    )
    _window_backups(supervisor.backup_dir / "backup-journal.jsonl")
    summary = supervisor._summarize()
    assert summary["samples"] < summary["min_samples"]
    assert summary["criteria"]["samples_cover_window"] is False
    assert summary["passed"] is False


def test_oversized_sample_gaps_fail_the_window(tmp_path) -> None:
    supervisor = _supervisor(tmp_path)
    samples = _dense_samples()
    # Drop 41 consecutive samples: still above min_samples, but the resuming
    # sample must report its real (over-limit) gap since the last kept sample.
    samples = samples[:100] + samples[141:]
    samples[100] = _sample(T0 + 1_000 + 141 * 30_000, count=141, gap=42 * 30_000)
    _write_window(
        supervisor.observation_path, start_ms=T0, end_ms=T0 + DAY_MS + 1_000, samples=samples
    )
    _window_backups(supervisor.backup_dir / "backup-journal.jsonl")
    summary = supervisor._summarize()
    assert summary["samples"] == 2839
    assert summary["criteria"]["samples_cover_window"] is True
    assert summary["sample_gap_max_ms"] == 1_260_000
    assert summary["sample_gap_max_ms"] > summary["sample_gap_limit_ms"]
    assert summary["criteria"]["sample_gap_within_bound"] is False
    assert summary["passed"] is False


def test_configured_duration_cannot_replace_the_observed_window(tmp_path) -> None:
    supervisor = _supervisor(tmp_path)
    supervisor.args = SimpleNamespace(duration_hours=24)
    _write_window(
        supervisor.observation_path,
        start_ms=T0,
        end_ms=T0 + 3_600_000,
        samples=[_sample(T0 + 1_000, count=0), _sample(T0 + 3_500_000, count=0)],
    )
    _window_backups(supervisor.backup_dir / "backup-journal.jsonl")
    summary = supervisor._summarize()
    assert summary["duration_seconds"] < 24 * 3600
    assert summary["criteria"]["duration_at_least_24h"] is False
    assert summary["passed"] is False


def test_persistent_unresolved_difference_fails_the_window(tmp_path) -> None:
    """Time-based bound, hand-computed against the acceptance recovery window:
    a non-reconciliation unresolved code must clear within 2 minutes
    (120_000 ms). A difference first seen 1 minute earlier (60_000 ms) is an
    explained transient; one still unresolved 3 minutes later (180_000 ms)
    exceeds the bound and fails the window."""

    def build(at_ms: int, sample_ts: int):
        supervisor = _supervisor(tmp_path / f"d{sample_ts - at_ms}")
        index = (sample_ts - (T0 + 1_000)) // 30_000
        _write_window(
            supervisor.observation_path,
            start_ms=T0,
            end_ms=T0 + DAY_MS + 1_000,
            samples=_dense_samples(
                overrides={
                    index: _sample(
                        sample_ts,
                        unresolved=["BOOK_ATTRIBUTION_UNKNOWN"],
                        unresolved_entries=[{"code": "BOOK_ATTRIBUTION_UNKNOWN", "at_ms": at_ms}],
                        count=index,
                    )
                }
            ),
        )
        _window_backups(supervisor.backup_dir / "backup-journal.jsonl")
        summary = supervisor._summarize()
        # 60_000 ms and 180_000 ms are computed from the two timestamps above,
        # never from the implementation.
        assert summary["unresolved_max_duration_ms"] == {
            "BOOK_ATTRIBUTION_UNKNOWN": sample_ts - at_ms
        }
        return summary

    explained = build(T0 + 1_000, T0 + 61_000)
    assert explained["criteria"]["no_persistent_unresolved"] is True
    assert explained["criteria"]["unresolved_cleared_at_end"] is True
    assert explained["passed"] is True

    persistent = build(T0 + 1_000, T0 + 181_000)
    assert persistent["criteria"]["no_persistent_unresolved"] is False
    assert persistent["criteria"]["unresolved_cleared_at_end"] is True
    assert persistent["passed"] is False


def test_transient_unresolved_difference_that_clears_is_explained(tmp_path) -> None:
    supervisor = _supervisor(tmp_path)
    _write_window(
        supervisor.observation_path,
        start_ms=T0,
        end_ms=T0 + DAY_MS + 1_000,
        samples=_dense_samples(
            overrides={
                0: _sample(T0 + 1_000, unresolved=["BALANCE_MISMATCH"], count=0),
                1: _sample(T0 + 31_000, unresolved=["BALANCE_MISMATCH"], count=1),
            }
        ),
    )
    _window_backups(supervisor.backup_dir / "backup-journal.jsonl")
    summary = supervisor._summarize()
    assert summary["criteria"]["no_persistent_unresolved"] is True
    assert summary["criteria"]["unresolved_cleared_at_end"] is True
    assert summary["passed"] is True


def test_unresolved_must_be_clear_at_the_end_even_when_transient(tmp_path) -> None:
    supervisor = _supervisor(tmp_path)
    _write_window(
        supervisor.observation_path,
        start_ms=T0,
        end_ms=T0 + DAY_MS + 1_000,
        samples=[
            _sample(T0 + 1_000, count=0),
            _sample(T0 + DAY_MS - 1_000, unresolved=["BALANCE_MISMATCH"], count=0),
        ],
    )
    _window_backups(supervisor.backup_dir / "backup-journal.jsonl")
    summary = supervisor._summarize()
    assert summary["unresolved_max_streak"]["BALANCE_MISMATCH"] == 1
    assert summary["criteria"]["no_persistent_unresolved"] is True
    assert summary["criteria"]["unresolved_cleared_at_end"] is False
    assert summary["passed"] is False


def test_balance_mismatch_is_bounded_by_thirty_minute_recovery(tmp_path) -> None:
    """Time-based bound, hand-computed against the acceptance recovery window:
    reconciliation/balance-affecting codes (BALANCE_MISMATCH and peers) may
    stay unresolved for at most 30 minutes (1_800_000 ms) — a real elapsed
    time that covers one Demo reconciliation cycle after an outage, not a
    sample count. A difference first seen 29 minutes earlier (1_740_000 ms)
    is within the bound; one still unresolved after 31 minutes (1_860_000 ms)
    exceeds it and fails the window."""

    def build(duration_ms: int):
        at_ms = T0 + 1_000
        sample_ts = at_ms + duration_ms
        supervisor = _supervisor(tmp_path / f"d{duration_ms}")
        index = (sample_ts - (T0 + 1_000)) // 30_000
        _write_window(
            supervisor.observation_path,
            start_ms=T0,
            end_ms=T0 + DAY_MS + 1_000,
            samples=_dense_samples(
                overrides={
                    index: _sample(
                        sample_ts,
                        unresolved=["BALANCE_MISMATCH"],
                        unresolved_entries=[{"code": "BALANCE_MISMATCH", "at_ms": at_ms}],
                        count=index,
                    )
                }
            ),
        )
        _window_backups(supervisor.backup_dir / "backup-journal.jsonl")
        summary = supervisor._summarize()
        # 1_740_000 ms and 1_860_000 ms are computed from the two timestamps
        # above, never from the implementation.
        assert summary["unresolved_max_duration_ms"] == {"BALANCE_MISMATCH": duration_ms}
        return summary

    within_bound = build(1_740_000)
    assert within_bound["criteria"]["samples_cover_window"] is True
    assert within_bound["criteria"]["no_persistent_unresolved"] is True
    assert within_bound["criteria"]["unresolved_cleared_at_end"] is True
    assert within_bound["passed"] is True

    over_bound = build(1_860_000)
    assert over_bound["criteria"]["no_persistent_unresolved"] is False
    assert over_bound["criteria"]["unresolved_cleared_at_end"] is True
    assert over_bound["passed"] is False


def test_repeated_send_fails_the_window(tmp_path) -> None:
    supervisor = _supervisor(tmp_path)
    _write_window(
        supervisor.observation_path,
        start_ms=T0,
        end_ms=T0 + DAY_MS + 1_000,
        samples=_dense_samples(
            overrides={0: _sample(T0 + 1_000, count=0, repeated=["cmd-repeated"])}
        ),
    )
    _window_backups(supervisor.backup_dir / "backup-journal.jsonl")
    summary = supervisor._summarize()
    assert summary["repeated_send_commands"] == ["cmd-repeated"]
    assert summary["criteria"]["no_repeated_sends"] is False
    assert summary["passed"] is False


def test_coverage_proof_is_required_and_unproven_coverage_fails(tmp_path) -> None:
    supervisor = _supervisor(tmp_path)
    _write_window(
        supervisor.observation_path,
        start_ms=T0,
        end_ms=T0 + DAY_MS + 1_000,
        samples=[
            _sample(T0 + 1_000, coverage=False),
            _sample(T0 + DAY_MS - 1_000, coverage=False),
        ],
    )
    _window_backups(supervisor.backup_dir / "backup-journal.jsonl")
    missing = supervisor._summarize()
    assert missing["criteria"]["coverage_proof_recorded"] is False
    assert missing["passed"] is False

    _write_window(
        supervisor.observation_path,
        start_ms=T0,
        end_ms=T0 + DAY_MS + 1_000,
        samples=[
            _sample(T0 + 1_000, reasons=["PRIVATE_STREAM_COVERAGE_UNPROVEN"]),
            _sample(T0 + DAY_MS - 1_000),
        ],
    )
    unproven = supervisor._summarize()
    assert unproven["criteria"]["no_private_stream_coverage_unproven"] is False
    assert unproven["passed"] is False


def test_container_restart_or_unverifiable_state_fails_the_window(tmp_path) -> None:
    for reason in ("CONTAINER_RESTART_DETECTED", "CONTAINER_STATE_UNVERIFIABLE"):
        supervisor = _clean_window(tmp_path / reason)
        supervisor.aborted_reason = reason
        summary = supervisor._summarize()
        assert summary["criteria"]["no_container_restart"] is False, reason
        assert summary["passed"] is False, reason


def test_supervisor_error_must_not_be_reported_as_a_passed_window(tmp_path) -> None:
    aborted = _clean_window(tmp_path / "aborted")
    aborted.aborted_reason = "SUPERVISOR_ERROR:TimeoutError"
    assert aborted._summarize()["passed"] is False

    errored = _clean_window(tmp_path / "errored")
    errored.abort_error = "RuntimeError: supervisor loop crashed"
    summary = errored._summarize()
    assert summary["criteria"]["no_supervisor_error"] is False
    assert summary["passed"] is False


def test_stale_or_missing_dr_evidence_fails_the_window(tmp_path) -> None:
    stale = _clean_window(tmp_path / "stale")
    _write_backups(stale.backup_dir / "backup-journal.jsonl", [T0, T0 + 900_000])
    assert stale._summarize()["criteria"]["dr_backups_fresh"] is False

    # A journal that only ever covered the opening minutes cannot keep the
    # window tail fresh: head covered, tail uncovered, window fails.
    head_only = _clean_window(tmp_path / "head-only")
    _write_backups(head_only.backup_dir / "backup-journal.jsonl", [T0 - 120_000])
    head_summary = head_only._summarize()
    assert head_summary["criteria"]["dr_backups_fresh"] is False
    assert head_summary["passed"] is False

    missing = _clean_window(tmp_path / "missing", dr_successes=0)
    (missing.backup_dir / "backup-journal.jsonl").unlink()
    summary = missing._summarize()
    assert summary["criteria"]["dr_cycles_succeeded"] is False
    assert summary["criteria"]["dr_backups_fresh"] is False
    assert summary["passed"] is False


def _rpo_journal(tmp_path: Path, completions_ms: list[int]):
    from trader.ops.backup import summarize_rpo

    journal = tmp_path / "backup-journal.jsonl"
    _write_backups(journal, completions_ms)
    return summarize_rpo


def test_window_rpo_requires_a_fresh_backup_at_the_start(tmp_path) -> None:
    """Hand-computed: window [T0, T0+600_000], no backup at or before T0. The
    T0+900_000 completion lies after the window and must not cover the missing
    head; rpo_satisfied is False even though gaps inside stay small."""
    summarize_rpo = _rpo_journal(tmp_path, [T0 + 180_000, T0 + 600_000, T0 + 900_000])
    rpo = summarize_rpo(
        tmp_path / "backup-journal.jsonl",
        300,
        window_start_ms=T0,
        window_end_ms=T0 + 600_000,
    )
    assert rpo["successful_backups"] == 3
    assert rpo["head_covered"] is False
    assert rpo["head_age_ms"] is None
    assert rpo["tail_covered"] is True
    assert rpo["max_gap_ms"] == 420_000
    assert rpo["rpo_satisfied"] is False


def test_window_rpo_requires_a_still_fresh_backup_at_the_end(tmp_path) -> None:
    """Hand-computed: head at T0-120_000 (age 120_000ms, covered), one backup
    at T0+180_000 (gap 300_000ms, at the bound), window ends T0+600_000 so the
    tail age is 420_000ms > 300_000ms and the window fails."""
    summarize_rpo = _rpo_journal(tmp_path, [T0 - 120_000, T0 + 180_000])
    rpo = summarize_rpo(
        tmp_path / "backup-journal.jsonl",
        300,
        window_start_ms=T0,
        window_end_ms=T0 + 600_000,
    )
    assert rpo["head_covered"] is True
    assert rpo["gaps_ms"] == [300_000]
    assert rpo["tail_age_ms"] == 420_000
    assert rpo["tail_covered"] is False
    assert rpo["rpo_satisfied"] is False


def test_window_rpo_flags_internal_gaps_over_the_bound(tmp_path) -> None:
    """Hand-computed: head T0-60_000, backups at T0+180_000 and T0+900_000,
    window [T0, T0+900_000]. Internal gap 900_000-180_000 = 720_000ms breaks
    the RPO even though head and tail ages are covered."""
    summarize_rpo = _rpo_journal(tmp_path, [T0 - 60_000, T0 + 180_000, T0 + 900_000])
    rpo = summarize_rpo(
        tmp_path / "backup-journal.jsonl",
        300,
        window_start_ms=T0,
        window_end_ms=T0 + 900_000,
    )
    assert rpo["head_covered"] is True
    assert rpo["tail_covered"] is True
    assert rpo["gaps_ms"] == [240_000, 720_000]
    assert rpo["max_gap_ms"] == 720_000
    assert rpo["rpo_satisfied"] is False


def test_window_rpo_bound_is_millisecond_exact(tmp_path) -> None:
    """Hand-computed: a single backup exactly at the window start leaves a
    tail age of exactly 300_000ms (satisfied); one millisecond more
    (window ends at T0+300_001) exceeds the bound and fails."""
    summarize_rpo = _rpo_journal(tmp_path, [T0])
    satisfied = summarize_rpo(
        tmp_path / "backup-journal.jsonl",
        300,
        window_start_ms=T0,
        window_end_ms=T0 + 300_000,
    )
    assert satisfied["tail_age_ms"] == 300_000
    assert satisfied["rpo_satisfied"] is True

    exceeded = summarize_rpo(
        tmp_path / "backup-journal.jsonl",
        300,
        window_start_ms=T0,
        window_end_ms=T0 + 300_001,
    )
    assert exceeded["tail_age_ms"] == 300_001
    assert exceeded["rpo_satisfied"] is False


def test_window_rpo_full_coverage_passes(tmp_path) -> None:
    """Hand-computed: fresh head, 300_000ms and 180_000ms internal gaps, and a
    backup exactly at the window end — the only fully covered shape here."""
    summarize_rpo = _rpo_journal(tmp_path, [T0 - 120_000, T0 + 180_000, T0 + 360_000])
    rpo = summarize_rpo(
        tmp_path / "backup-journal.jsonl",
        300,
        window_start_ms=T0,
        window_end_ms=T0 + 360_000,
    )
    assert rpo["head_age_ms"] == 120_000
    assert rpo["gaps_ms"] == [300_000, 180_000]
    assert rpo["tail_age_ms"] == 0
    assert rpo["rpo_satisfied"] is True


def test_recovery_generation_is_recovery_bounded_but_dr_stale_stays_strict(tmp_path) -> None:
    """Hand-computed: three minutes (180_000ms) of a reason is over the strict
    2-minute bound. RECOVERY_GENERATION_OR_OVERFLOW sits in the 30-minute
    recovery class so the window still passes; DR_BACKUP_STALE does not, and
    the same duration fails the window (dr_backups_fresh itself is unchanged)."""

    def build(reason: str):
        supervisor = _supervisor(tmp_path / reason.lower())
        _write_window(
            supervisor.observation_path,
            start_ms=T0,
            end_ms=T0 + DAY_MS + 1_000,
            samples=_dense_samples(
                overrides={
                    index: _sample(
                        T0 + 1_000 + index * 30_000,
                        ready=False,
                        reasons=[reason],
                        count=index,
                    )
                    for index in range(7)
                }
            ),
        )
        _window_backups(supervisor.backup_dir / "backup-journal.jsonl")
        summary = supervisor._summarize()
        # 180_000 ms is 6 * 30_000 ms of samples after the first, hand-computed.
        assert summary["reason_max_duration_ms"] == {reason: 180_000}
        return summary

    recovery = build("RECOVERY_GENERATION_OR_OVERFLOW")
    assert recovery["criteria"]["no_persistent_reasons"] is True
    assert recovery["criteria"]["reasons_cleared_at_end"] is True
    assert recovery["unexplained_reasons"] == {}
    assert recovery["criteria"]["dr_backups_fresh"] is True
    assert recovery["passed"] is True

    stale = build("DR_BACKUP_STALE")
    assert stale["criteria"]["no_persistent_reasons"] is False
    assert stale["criteria"]["reasons_cleared_at_end"] is True
    assert stale["unexplained_reasons"] == {"DR_BACKUP_STALE": 180_000}
    assert stale["criteria"]["dr_backups_fresh"] is True
    assert stale["passed"] is False


def test_execution_count_cannot_regress(tmp_path) -> None:
    supervisor = _supervisor(tmp_path)
    _write_window(
        supervisor.observation_path,
        start_ms=T0,
        end_ms=T0 + DAY_MS + 1_000,
        samples=[
            _sample(T0 + 1_000, count=2),
            _sample(T0 + 2_000, count=1),
            _sample(T0 + DAY_MS - 1_000, count=1),
        ],
    )
    _window_backups(supervisor.backup_dir / "backup-journal.jsonl")
    summary = supervisor._summarize()
    assert summary["criteria"]["execution_count_monotonic"] is False
    assert summary["passed"] is False


def test_health_reasons_must_clear_and_not_persist(tmp_path) -> None:
    transient = _supervisor(tmp_path / "transient")
    _write_window(
        transient.observation_path,
        start_ms=T0,
        end_ms=T0 + DAY_MS + 1_000,
        samples=_dense_samples(
            overrides={
                index: _sample(
                    T0 + 1_000 + index * 30_000,
                    ready=False,
                    reasons=["PRIVATE_STREAM_UNHEALTHY"],
                    count=index,
                )
                for index in range(3)
            }
        ),
    )
    _window_backups(transient.backup_dir / "backup-journal.jsonl")
    transient_summary = transient._summarize()
    assert transient_summary["reason_max_duration_ms"] == {"PRIVATE_STREAM_UNHEALTHY": 60_000}
    assert transient_summary["passed"] is True

    persistent = _supervisor(tmp_path / "persistent")
    _write_window(
        persistent.observation_path,
        start_ms=T0,
        end_ms=T0 + DAY_MS + 1_000,
        samples=_dense_samples(
            overrides={
                index: _sample(
                    T0 + 1_000 + index * 30_000,
                    ready=False,
                    reasons=["OPERATOR_BLOCK_PENDING"],
                    count=index,
                )
                for index in range(2880)
            }
        ),
    )
    _write_backups(persistent.backup_dir / "backup-journal.jsonl", [T0, T0 + 120_000, T0 + 240_000])
    summary = persistent._summarize()
    assert summary["reason_max_duration_ms"] == {"OPERATOR_BLOCK_PENDING": 86_370_000}
    assert summary["criteria"]["no_persistent_reasons"] is False
    assert summary["criteria"]["reasons_cleared_at_end"] is False
    assert summary["unexplained_reasons"] == {"OPERATOR_BLOCK_PENDING": 86_370_000}
    assert summary["passed"] is False


def test_network_reasons_get_a_thirty_minute_recovery_bound(tmp_path) -> None:
    def build(count: int):
        supervisor = _supervisor(tmp_path / f"n{count}")
        _write_window(
            supervisor.observation_path,
            start_ms=T0,
            end_ms=T0 + DAY_MS + 1_000,
            samples=_dense_samples(
                overrides={
                    index: _sample(
                        T0 + 1_000 + index * 30_000,
                        ready=False,
                        reasons=["PRIVATE_STREAM_UNHEALTHY"],
                        count=index,
                    )
                    for index in range(count)
                }
            ),
        )
        _window_backups(supervisor.backup_dir / "backup-journal.jsonl")
        return supervisor._summarize()

    recovered = build(59)
    assert recovered["reason_max_duration_ms"] == {"PRIVATE_STREAM_UNHEALTHY": 1_740_000}
    assert recovered["criteria"]["no_persistent_reasons"] is True
    assert recovered["criteria"]["reasons_cleared_at_end"] is True
    assert recovered["unexplained_reasons"] == {}
    assert recovered["passed"] is True

    stuck = build(63)
    assert stuck["reason_max_duration_ms"] == {"PRIVATE_STREAM_UNHEALTHY": 1_860_000}
    assert stuck["criteria"]["no_persistent_reasons"] is False
    assert stuck["criteria"]["reasons_cleared_at_end"] is True
    assert stuck["unexplained_reasons"] == {"PRIVATE_STREAM_UNHEALTHY": 1_860_000}
    assert stuck["passed"] is False


def test_warmup_reasons_are_tracked_but_never_called_unexplained(tmp_path) -> None:
    def build(count: int, subdir: str):
        supervisor = _supervisor(tmp_path / subdir)
        _write_window(
            supervisor.observation_path,
            start_ms=T0,
            end_ms=T0 + DAY_MS + 1_000,
            samples=_dense_samples(
                overrides={
                    index: _sample(
                        T0 + 1_000 + index * 30_000,
                        ready=False,
                        reasons=["STRATEGY_WARMUP_PENDING"],
                        count=index,
                    )
                    for index in range(count)
                }
            ),
        )
        _window_backups(supervisor.backup_dir / "backup-journal.jsonl")
        return supervisor._summarize()

    short = build(3, "short")
    assert short["reason_max_streak"]["STRATEGY_WARMUP_PENDING"] == 3
    assert short["reason_max_duration_ms"] == {"STRATEGY_WARMUP_PENDING": 60_000}
    assert short["unexplained_reasons"] == {}
    assert short["criteria"]["no_persistent_reasons"] is True
    assert short["passed"] is True

    long = build(2880, "long")
    assert long["reason_max_duration_ms"] == {"STRATEGY_WARMUP_PENDING": 86_370_000}
    assert long["unexplained_reasons"] == {}
    assert long["criteria"]["no_persistent_reasons"] is False
    assert long["passed"] is False


# ====================================================== object-store idempotency
class _BucketAlreadyOwnedByYou(Exception):
    pass


class _BucketAlreadyExists(Exception):
    pass


def _task_s3_config(module, **overrides):
    values = {
        "endpoint_url": "http://127.0.0.1:9000",
        "bucket": "qts-p6ind",
        "prefix": "phase6/p6ind",
        "access_key": "ak-test",
        "secret_key": "sk-test-secret",
    }
    values.update(overrides)
    return module.S3Config(**values)


def _fake_s3_client(monkeypatch, module, *, fail_create=None, fail_head=None):
    calls = {"create_bucket": [], "head_bucket": []}

    class _FakeExceptions:
        BucketAlreadyOwnedByYou = _BucketAlreadyOwnedByYou
        BucketAlreadyExists = _BucketAlreadyExists

    class _FakeClient:
        exceptions = _FakeExceptions

        def create_bucket(self, **kwargs):
            calls["create_bucket"].append(kwargs.get("Bucket"))
            if fail_create is not None:
                raise fail_create

        def head_bucket(self, **kwargs):
            calls["head_bucket"].append(kwargs.get("Bucket"))
            if fail_head is not None:
                raise fail_head

    monkeypatch.setattr(module, "_s3_client", lambda config: _FakeClient())
    return calls


def test_create_bucket_accepts_a_verified_task_bucket_on_reconnect(monkeypatch) -> None:
    module = _script_module("sandbox_supervisor")
    config = _task_s3_config(module)
    calls = _fake_s3_client(monkeypatch, module, fail_create=_BucketAlreadyExists("already there"))

    module._create_bucket(config)

    assert calls["create_bucket"] == ["qts-p6ind"]
    assert calls["head_bucket"] == ["qts-p6ind"]


def test_create_bucket_rejects_an_existing_bucket_it_cannot_reach(monkeypatch) -> None:
    module = _script_module("sandbox_supervisor")
    config = _task_s3_config(module)
    calls = _fake_s3_client(
        monkeypatch,
        module,
        fail_create=_BucketAlreadyExists("already there"),
        fail_head=Exception("403 AccessDenied: authorization failure"),
    )

    with pytest.raises(RuntimeError, match="BUCKET_EXISTING_BUT_NOT_ACCESSIBLE") as excinfo:
        module._create_bucket(config)

    message = str(excinfo.value)
    assert "AccessDenied" in message
    assert "sk-test-secret" not in message
    assert calls["create_bucket"] == ["qts-p6ind"]
    assert calls["head_bucket"] == ["qts-p6ind"]


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("endpoint_url", "http://s3.example.com:9000", "S3_ENDPOINT_NOT_TASK_LOOPBACK"),
        ("bucket", "other-bucket", "S3_BUCKET_NOT_TASK_TARGET"),
        ("prefix", "other/prefix", "S3_PREFIX_NOT_TASK_TARGET"),
    ],
)
def test_create_bucket_refuses_targets_outside_the_task(
    monkeypatch, field, value, expected_code
) -> None:
    module = _script_module("sandbox_supervisor")
    config = _task_s3_config(module, **{field: value})
    calls = _fake_s3_client(monkeypatch, module, fail_create=_BucketAlreadyExists("already there"))

    with pytest.raises(RuntimeError, match=expected_code):
        module._create_bucket(config)

    # The unverified target is never probed for access.
    assert calls["head_bucket"] == []


def _tunnel_supervisor(tmp_path, module):
    supervisor = object.__new__(module.SandboxSupervisor)
    supervisor.tunnel = SimpleNamespace(
        poll=lambda: None,
        terminate=lambda: None,
        wait=lambda timeout=5: 0,
    )
    supervisor.run_id = "p6ind"
    supervisor.supervisor_journal = tmp_path / "supervisor.jsonl"
    supervisor.context = {
        "s3_config": _task_s3_config(module, endpoint_url="http://127.0.0.1:19001"),
        "ssh_destination": "dr@127.0.0.1",
        "remote_port": 9000,
        "bucket": "qts-p6ind",
        "access_key": "ak-test",
        "secret_key": "sk-test-secret",
        "local_port": 19001,
    }
    return supervisor


def _journal_rows(tmp_path):
    journal = tmp_path / "supervisor.jsonl"
    if not journal.exists():
        return []
    return [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_ensure_tunnel_reuses_a_live_tunnel_that_passes_the_probe(
    tmp_path, monkeypatch
) -> None:
    module = _script_module("sandbox_supervisor")
    supervisor = _tunnel_supervisor(tmp_path, module)
    probes = []

    def record_probe(config):
        probes.append(config.endpoint_url)

    monkeypatch.setattr(module, "_probe_bucket", record_probe)
    started = []
    monkeypatch.setattr(
        module, "_start_tunnel", lambda destination, port: started.append(destination)
    )

    await supervisor._ensure_tunnel()

    # The verified live tunnel is reused as-is: no rebuild, no re-create.
    assert probes == ["http://127.0.0.1:19001"]
    assert started == []
    assert supervisor.context["local_port"] == 19001
    assert _journal_rows(tmp_path) == []


@pytest.mark.asyncio
async def test_ensure_tunnel_rebuilds_and_readbacks_after_a_failed_probe(
    tmp_path, monkeypatch
) -> None:
    module = _script_module("sandbox_supervisor")
    supervisor = _tunnel_supervisor(tmp_path, module)
    probes = []

    def fake_probe(config):
        probes.append(config.endpoint_url)
        if len(probes) == 1:
            raise RuntimeError("S3_READBACK_FAILED: connection reset by peer")

    monkeypatch.setattr(module, "_probe_bucket", fake_probe)
    started = []

    def fake_start(destination, remote_port):
        started.append((destination, remote_port))
        return (
            SimpleNamespace(terminate=lambda: None, wait=lambda timeout=5: 0),
            19000,
        )

    monkeypatch.setattr(module, "_start_tunnel", fake_start)
    created = []
    monkeypatch.setattr(
        module, "_create_bucket", lambda config: created.append(config.endpoint_url)
    )

    await supervisor._ensure_tunnel()

    # Probe fails on the stale tunnel -> discard, rebuild, create, readback.
    assert probes == ["http://127.0.0.1:19001", "http://127.0.0.1:19000"]
    assert started == [("dr@127.0.0.1", 9000)]
    assert created == ["http://127.0.0.1:19000"]
    assert supervisor.context["local_port"] == 19000
    rebuilt = supervisor.context["s3_config"]
    assert rebuilt.endpoint_url == "http://127.0.0.1:19000"
    assert rebuilt.bucket == "qts-p6ind"
    assert rebuilt.prefix == "phase6/p6ind"
    rows = _journal_rows(tmp_path)
    assert [row["kind"] for row in rows] == ["tunnel_discarded", "tunnel"]
    assert rows[0]["reason"] == "APPLICATION_PROBE_FAILED"
    assert rows[1]["local_port"] == 19000
    assert rows[1]["strict_host_key"] is True


# ====================================================== clean-start sandbox preflight
def _broker_class(*, orders=(), trades=(), account=None):
    class _FakeBroker:
        def __init__(self, config=None) -> None:
            self.config = config

        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def get_server_time(self):
            return T0

        async def _fetch_account(self):
            return account

        async def get_open_orders(self):
            return []

        async def get_all_orders(self, symbol):
            return list(orders)

        async def get_my_trades(self, symbol):
            return list(trades)

    return _FakeBroker


class _FakeBrokerConfig:
    """Class, not SimpleNamespace: classmethod must be assignable."""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    @classmethod
    def for_env(cls, *args, **kwargs):
        return cls(**kwargs)


def _fake_broker_module(broker_cls):
    module = types.ModuleType("trader.adapters.broker.binance_spot_demo_broker")
    module.BinanceSpotDemoBroker = broker_cls
    module.BinanceSpotDemoBrokerConfig = _FakeBrokerConfig
    return module


def _preflight_supervisor(tmp_path: Path):
    supervisor = object.__new__(_script_module("sandbox_supervisor").SandboxSupervisor)
    supervisor.args = SimpleNamespace(lookback_ms=300_000, symbols="BTCUSDT")
    supervisor.run_id = "p6ind"
    return supervisor


_SPOT_ACCOUNT = {
    "accountType": "SPOT",
    "canTrade": True,
    "permissions": ["SPOT"],
    "balances": [{"asset": "USDT", "free": "1000", "locked": "0"}],
}


@pytest.mark.asyncio
async def test_preflight_accepts_a_clean_dedicated_sandbox_account(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "trader.adapters.broker.binance_spot_demo_broker",
        _fake_broker_module(_broker_class(account=_SPOT_ACCOUNT)),
    )
    env = {"BINANCE_API_KEY": "key", "BINANCE_SECRET_KEY": "secret", "BINANCE_ENV": "demo"}
    evidence = await _preflight_supervisor(tmp_path)._preflight(env)
    assert evidence["account_type"] == "SPOT" and evidence["can_trade"] is True
    assert evidence["symbols"]["BTCUSDT"] == {
        "recent_trades": 0,
        "foreign_recent_trades": 0,
        "own_recent_trades": 0,
    }
    assert evidence["non_zero_assets"] == ["USDT"]
    assert evidence["endpoint_identity_source"].startswith("BINANCE_ENV")


@pytest.mark.asyncio
async def test_preflight_rejects_prior_qts_trade_inside_the_recovery_lookback(
    tmp_path, monkeypatch
) -> None:
    order = SimpleNamespace(broker_order_id="9001", cl_ord_id="qts-previous-run")
    trade = {"orderId": "9001", "time": T0 - 60_000}
    monkeypatch.setitem(
        sys.modules,
        "trader.adapters.broker.binance_spot_demo_broker",
        _fake_broker_module(_broker_class(account=_SPOT_ACCOUNT, orders=[order], trades=[trade])),
    )
    env = {"BINANCE_API_KEY": "key", "BINANCE_SECRET_KEY": "secret", "BINANCE_ENV": "demo"}
    with pytest.raises(RuntimeError, match="SANDBOX_LOOKBACK_NOT_CLEAN"):
        await _preflight_supervisor(tmp_path)._preflight(env)


@pytest.mark.asyncio
async def test_preflight_rejects_foreign_or_non_tradable_accounts(tmp_path, monkeypatch) -> None:
    foreign_trade = {"orderId": "7777", "time": T0 - 1_000}
    monkeypatch.setitem(
        sys.modules,
        "trader.adapters.broker.binance_spot_demo_broker",
        _fake_broker_module(_broker_class(account=_SPOT_ACCOUNT, trades=[foreign_trade])),
    )
    env = {"BINANCE_API_KEY": "key", "BINANCE_SECRET_KEY": "secret", "BINANCE_ENV": "demo"}
    with pytest.raises(RuntimeError, match="SANDBOX_ACCOUNT_NOT_DEDICATED"):
        await _preflight_supervisor(tmp_path)._preflight(env)

    monkeypatch.setitem(
        sys.modules,
        "trader.adapters.broker.binance_spot_demo_broker",
        _fake_broker_module(_broker_class(account={**_SPOT_ACCOUNT, "accountType": "MARGIN"})),
    )
    with pytest.raises(RuntimeError, match="SANDBOX_ACCOUNT_NOT_TRADABLE"):
        await _preflight_supervisor(tmp_path)._preflight(env)
