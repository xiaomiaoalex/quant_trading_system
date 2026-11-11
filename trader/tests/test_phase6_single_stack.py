"""Phase-6 single-stack guards.

These are static checks over the production package. They fail closed when a
retired execution/accounting module or entry point reappears, when the Control
API exposes a fact-write route, or when a legacy account/projection table gains
a production writer.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TRADER = ROOT / "trader"

FORBIDDEN_MODULES = {
    "trader.api.main",
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
    "trader.api.connections",
    "trader.api.routes.orders",
    "trader.api.routes.monitor",
    "trader.api.routes.killswitch",
    "trader.api.routes.reconciler",
    "trader.api.routes.strategies",
}

FORBIDDEN_FILE_MARKERS = (
    "trader/api/main.py",
    "trader/core/application/oms.py",
    "trader/services/order.py",
    "trader/services/execution_budget.py",
    "trader/services/strategy_runner.py",
    "trader/services/oms_callback.py",
)

FACT_WRITE_ROUTES = (
    "/v1/trading/executions",
    "/v1/trading/observations",
    "/v1/trading/reconcile",
    "/v1/trading/resolve/{code}",
)

LEGACY_WRITE_CALLS = {
    "save_balances",
    "save_lot",
    "update_lot_on_reduce",
    "save_position_projection",
    "save_reconciliation",
    "save_strategy_runtime_state",
}


def _production_files() -> list[Path]:
    return sorted(
        path
        for path in TRADER.rglob("*.py")
        if "tests" not in path.parts and "__pycache__" not in path.parts
    )


def _imports(path: Path) -> set[str]:
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


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_retired_modules_and_files_are_gone() -> None:
    for relative in FORBIDDEN_FILE_MARKERS:
        assert not (ROOT / relative).exists(), f"retired file still present: {relative}"


def test_production_code_does_not_import_retired_modules() -> None:
    offenders = []
    for path in _production_files():
        for module in _imports(path):
            if module in FORBIDDEN_MODULES or any(
                module.startswith(f"{forbidden}.") for forbidden in FORBIDDEN_MODULES
            ):
                offenders.append(f"{path.relative_to(ROOT)} imports {module}")
    assert offenders == []


def test_no_legacy_oms_submit_symbol_remains() -> None:
    offenders = []
    for path in _production_files():
        text = path.read_text(encoding="utf-8")
        if "submit_order" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_control_app_has_no_fact_write_route() -> None:
    from trader.api.trading_app import create_trading_app

    app = create_trading_app()
    paths = {getattr(route, "path", "") for route in app.routes}
    for path in FACT_WRITE_ROUTES:
        assert path not in paths
    # The single trade write surface and its read/control companions stay.
    for required in (
        "/v1/trading/orders",
        "/v1/trading/control",
        "/v1/trading/orders/{cl_ord_id}/cancel",
        "/health/trading",
    ):
        assert required in paths


def test_only_the_unified_service_books_executions() -> None:
    definitions = []
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
                "record_execution",
                "_book_execution",
            }:
                definitions.append(path.relative_to(ROOT).as_posix())
    # ``private_session._GenerationSink.record_execution`` only forwards to the
    # recovery coordinator; the accounting implementation itself lives once.
    assert definitions.count("trader/trading/service.py") == 2
    assert set(definitions) == {
        "trader/trading/service.py",
        "trader/trading/private_session.py",
    }


def test_trading_execution_table_is_written_only_by_the_store() -> None:
    writers = []
    for path in _production_files():
        text = path.read_text(encoding="utf-8")
        if "INSERT INTO trading_executions" in text:
            writers.append(path.relative_to(ROOT).as_posix())
    assert writers == ["trader/trading/postgres.py"]


def test_legacy_account_projection_tables_have_no_production_writer() -> None:
    offenders = []
    for path in _production_files():
        if path.name in {"position_repository.py", "execution_repository.py"}:
            # Read-only retention: the adapters may still define legacy SQL, but
            # no production module may call their write methods.
            continue
        calls = _called_names(path)
        hit = sorted(calls & LEGACY_WRITE_CALLS)
        if hit:
            offenders.append(f"{path.relative_to(ROOT)} calls {hit}")
    assert offenders == []


def test_backtest_submission_requires_an_eligibility_gate() -> None:
    from trader.trading.jobs import BacktestApplication

    with pytest.raises(ValueError, match="DATASET_ELIGIBILITY_GATE_UNAVAILABLE"):
        BacktestApplication(object())


def test_production_never_constructs_a_gateless_backtest_application() -> None:
    offenders = []
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name != "BacktestApplication":
                continue
            if not any(keyword.arg == "eligibility" for keyword in node.keywords):
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []
