"""PG-first performance accounting repository."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from decimal import Decimal
from typing import Any, AsyncIterator

from trader.adapters.persistence.postgres import PostgreSQLStorage, check_postgres_connection
from trader.core.domain.models.performance import (
    AccountingLedgerEntry,
    AccountPortfolioNAV,
    AssetClassification,
    AttributionItem,
    AttributionResult,
    BenchmarkHolding,
    CashFlowEvent,
    FactorExposureFact,
    FactorReturnFact,
    MarkPriceSnapshot,
    NAVPointV2,
    PerformanceSnapshot,
    PortfolioHoldingFact,
    RiskBudgetDecisionTrace,
    StrategyRun,
)

logger = logging.getLogger(__name__)


class PerformancePostgresRequiredError(RuntimeError):
    """Raised when audit-grade performance storage requires PostgreSQL."""


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    if isinstance(value, dict):
        return value
    return dict(value)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or "0"))


class PerformanceRepository:
    """Persist strategy runs, ledger rows, NAV v2, and attribution results."""

    def __init__(self, *, pg_required: bool | None = None) -> None:
        self._pg_required = (
            _env_flag("PERFORMANCE_PG_REQUIRED") if pg_required is None else bool(pg_required)
        )
        self._postgres_storage: PostgreSQLStorage | None = None
        self._use_postgres = False
        self._init_lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        self._runs: dict[str, StrategyRun] = {}
        self._ledger_by_key: dict[str, AccountingLedgerEntry] = {}
        self._ledger: list[AccountingLedgerEntry] = []
        self._cash_flows: dict[str, CashFlowEvent] = {}
        self._nav_points: list[NAVPointV2] = []
        self._account_nav_points: list[AccountPortfolioNAV] = []
        self._mark_snapshots: list[MarkPriceSnapshot] = []
        self._snapshots: list[PerformanceSnapshot] = []
        self._attribution_results: list[AttributionResult] = []
        self._asset_classifications: list[AssetClassification] = []
        self._benchmark_holdings: list[BenchmarkHolding] = []
        self._portfolio_holding_facts: list[PortfolioHoldingFact] = []
        self._factor_exposures: list[FactorExposureFact] = []
        self._factor_returns: list[FactorReturnFact] = []
        self._risk_budget_traces: list[RiskBudgetDecisionTrace] = []
        self._projection_cursors: dict[str, dict[str, Any]] = {}
        self._projection_audits: list[dict[str, Any]] = []
        self._projection_connection: ContextVar[Any | None] = ContextVar(
            "performance_projection_connection",
            default=None,
        )

    @asynccontextmanager
    async def run_projection_lock(self, run_id: str) -> AsyncIterator[None]:
        """Serialize one run projector across processes when PostgreSQL is available."""

        if not await self._ensure_postgres():
            yield
            return

        assert self._postgres_storage is not None
        pool = self._postgres_storage._pool
        assert pool is not None
        lock_key = int.from_bytes(
            hashlib.sha256(run_id.encode("utf-8")).digest()[:8],
            byteorder="big",
            signed=True,
        )
        async with pool.acquire() as conn:
            await conn.execute("SELECT pg_advisory_lock($1)", lock_key)
            token = self._projection_connection.set(conn)
            try:
                yield
            finally:
                self._projection_connection.reset(token)
                await conn.execute("SELECT pg_advisory_unlock($1)", lock_key)

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[Any]:
        current = self._projection_connection.get()
        if current is not None:
            yield current
            return

        assert self._postgres_storage is not None
        pool = self._postgres_storage._pool
        assert pool is not None
        async with pool.acquire() as conn:
            yield conn

    def _clear_postgres_state(self) -> None:
        self._postgres_storage = None
        self._use_postgres = False
        self._init_lock = None
        self._loop = None

    async def _reset_postgres_connection(self) -> None:
        if self._postgres_storage is not None:
            try:
                await self._postgres_storage.disconnect()
            except Exception:
                pool = getattr(self._postgres_storage, "_pool", None)
                if pool is not None:
                    pool.terminate()
        self._clear_postgres_state()

    def _terminate_postgres_connection(self) -> None:
        if self._postgres_storage is not None:
            pool = getattr(self._postgres_storage, "_pool", None)
            if pool is not None:
                pool.terminate()
            self._postgres_storage._pool = None
            self._postgres_storage._connected = False
        self._clear_postgres_state()

    async def _require_postgres_for_write(self) -> None:
        if self._pg_required:
            await self._ensure_postgres()

    async def _ensure_postgres(self) -> bool:
        current_loop = asyncio.get_running_loop()
        if self._loop is not current_loop:
            await self._reset_postgres_connection()
            self._loop = current_loop
            self._init_lock = asyncio.Lock()
        if self._use_postgres and self._postgres_storage is not None:
            return True
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        async with self._init_lock:
            if self._use_postgres and self._postgres_storage is not None:
                return True
            ok, msg = await check_postgres_connection(timeout=2.0)
            if not ok:
                logger.debug("PostgreSQL unavailable for performance accounting: %s", msg)
                if self._pg_required:
                    raise PerformancePostgresRequiredError(
                        "PostgreSQL unavailable for performance accounting while "
                        f"PERFORMANCE_PG_REQUIRED is enabled: {msg}"
                    )
                return False
            try:
                self._postgres_storage = PostgreSQLStorage()
                await self._postgres_storage.connect()
                await self._ensure_tables()
                self._use_postgres = True
                return True
            except Exception as exc:
                logger.warning("Failed to connect PostgreSQL for performance accounting: %s", exc)
                self._clear_postgres_state()
                if self._pg_required:
                    raise PerformancePostgresRequiredError(
                        "PostgreSQL connection failed for performance accounting while "
                        "PERFORMANCE_PG_REQUIRED is enabled"
                    ) from exc
                return False

    async def _ensure_tables(self) -> None:
        assert self._postgres_storage is not None
        async with self._connection() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_runs (
                    run_id TEXT PRIMARY KEY,
                    deployment_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'paper',
                    code_version TEXT,
                    params_hash TEXT,
                    initial_capital NUMERIC(36,18) NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL DEFAULT 'USDT',
                    status TEXT NOT NULL DEFAULT 'RUNNING',
                    started_at_ms BIGINT NOT NULL,
                    stopped_at_ms BIGINT,
                    start_reason TEXT,
                    stop_reason TEXT,
                    quality TEXT NOT NULL DEFAULT 'complete',
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_strategy_runs_deployment ON strategy_runs(deployment_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_strategy_runs_strategy ON strategy_runs(strategy_id)"
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pnl_ledger_entries (
                    ledger_entry_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    deployment_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    ts_ms BIGINT NOT NULL,
                    symbol TEXT,
                    side TEXT,
                    qty NUMERIC(36,18) NOT NULL DEFAULT 0,
                    price NUMERIC(36,18) NOT NULL DEFAULT 0,
                    trade_notional NUMERIC(36,18) NOT NULL DEFAULT 0,
                    gross_pnl NUMERIC(36,18) NOT NULL DEFAULT 0,
                    realized_pnl NUMERIC(36,18) NOT NULL DEFAULT 0,
                    unrealized_pnl NUMERIC(36,18) NOT NULL DEFAULT 0,
                    fee NUMERIC(36,18) NOT NULL DEFAULT 0,
                    fee_currency TEXT,
                    slippage NUMERIC(36,18) NOT NULL DEFAULT 0,
                    funding NUMERIC(36,18) NOT NULL DEFAULT 0,
                    cash_flow NUMERIC(36,18) NOT NULL DEFAULT 0,
                    cl_ord_id TEXT,
                    exec_id TEXT,
                    source TEXT NOT NULL DEFAULT 'performance_service',
                    quality TEXT NOT NULL DEFAULT 'complete',
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pnl_ledger_run_ts ON pnl_ledger_entries(run_id, ts_ms)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pnl_ledger_deployment_ts ON pnl_ledger_entries(deployment_id, ts_ms)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pnl_ledger_strategy_symbol ON pnl_ledger_entries(strategy_id, symbol)"
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cash_flows (
                    cash_flow_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    deployment_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    amount NUMERIC(36,18) NOT NULL,
                    currency TEXT NOT NULL,
                    flow_type TEXT NOT NULL,
                    ts_ms BIGINT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual',
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cash_flows_run_ts ON cash_flows(run_id, ts_ms)"
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nav_points_v2 (
                    nav_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    deployment_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    timestamp_ms BIGINT NOT NULL,
                    equity NUMERIC(36,18) NOT NULL,
                    cash NUMERIC(36,18) NOT NULL,
                    position_value NUMERIC(36,18) NOT NULL DEFAULT 0,
                    realized_pnl NUMERIC(36,18) NOT NULL DEFAULT 0,
                    unrealized_pnl NUMERIC(36,18) NOT NULL DEFAULT 0,
                    fee NUMERIC(36,18) NOT NULL DEFAULT 0,
                    slippage NUMERIC(36,18) NOT NULL DEFAULT 0,
                    funding NUMERIC(36,18) NOT NULL DEFAULT 0,
                    cash_flow NUMERIC(36,18) NOT NULL DEFAULT 0,
                    total_pnl NUMERIC(36,18) NOT NULL DEFAULT 0,
                    net_return NUMERIC(36,18) NOT NULL DEFAULT 0,
                    gross_return NUMERIC(36,18) NOT NULL DEFAULT 0,
                    twr NUMERIC(36,18) NOT NULL DEFAULT 0,
                    mwr NUMERIC(36,18),
                    quality TEXT NOT NULL DEFAULT 'complete',
                    source TEXT NOT NULL DEFAULT 'performance_ledger',
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(run_id, timestamp_ms)
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_nav_v2_run_ts ON nav_points_v2(run_id, timestamp_ms)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_nav_v2_deployment_ts ON nav_points_v2(deployment_id, timestamp_ms)"
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS account_portfolio_nav (
                    account_nav_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    timestamp_ms BIGINT NOT NULL,
                    currency TEXT NOT NULL,
                    equity NUMERIC(36,18) NOT NULL,
                    cash NUMERIC(36,18) NOT NULL,
                    position_value NUMERIC(36,18) NOT NULL DEFAULT 0,
                    liabilities NUMERIC(36,18) NOT NULL DEFAULT 0,
                    initial_capital NUMERIC(36,18) NOT NULL,
                    cumulative_cash_flow NUMERIC(36,18) NOT NULL DEFAULT 0,
                    total_pnl NUMERIC(36,18) NOT NULL DEFAULT 0,
                    run_equity NUMERIC(36,18) NOT NULL DEFAULT 0,
                    run_total_pnl NUMERIC(36,18) NOT NULL DEFAULT 0,
                    unallocated_cash NUMERIC(36,18) NOT NULL DEFAULT 0,
                    reconciliation_residual NUMERIC(36,18) NOT NULL DEFAULT 0,
                    quality TEXT NOT NULL DEFAULT 'complete',
                    source TEXT NOT NULL DEFAULT 'account_snapshot',
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(account_id, venue, timestamp_ms)
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_account_portfolio_nav_account_ts
                ON account_portfolio_nav(account_id, venue, timestamp_ms DESC)
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_mark_snapshots (
                    mark_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    deployment_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timestamp_ms BIGINT NOT NULL,
                    mark_price NUMERIC(36,18) NOT NULL,
                    source TEXT NOT NULL DEFAULT 'performance_service',
                    quality TEXT NOT NULL DEFAULT 'complete',
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(run_id, symbol, timestamp_ms)
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_performance_marks_run_symbol_ts
                ON performance_mark_snapshots(run_id, symbol, timestamp_ms DESC)
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    deployment_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    timestamp_ms BIGINT NOT NULL,
                    equity NUMERIC(36,18) NOT NULL,
                    total_pnl NUMERIC(36,18) NOT NULL DEFAULT 0,
                    net_return NUMERIC(36,18) NOT NULL DEFAULT 0,
                    twr NUMERIC(36,18) NOT NULL DEFAULT 0,
                    mwr NUMERIC(36,18),
                    max_drawdown NUMERIC(36,18) NOT NULL DEFAULT 0,
                    sharpe NUMERIC(36,18),
                    sortino NUMERIC(36,18),
                    calmar NUMERIC(36,18),
                    turnover NUMERIC(36,18) NOT NULL DEFAULT 0,
                    exposure NUMERIC(36,18) NOT NULL DEFAULT 0,
                    fees NUMERIC(36,18) NOT NULL DEFAULT 0,
                    slippage NUMERIC(36,18) NOT NULL DEFAULT 0,
                    funding NUMERIC(36,18) NOT NULL DEFAULT 0,
                    realized_pnl NUMERIC(36,18) NOT NULL DEFAULT 0,
                    unrealized_pnl NUMERIC(36,18) NOT NULL DEFAULT 0,
                    win_rate NUMERIC(36,18),
                    profit_factor NUMERIC(36,18),
                    avg_win NUMERIC(36,18),
                    avg_loss NUMERIC(36,18),
                    payoff_ratio NUMERIC(36,18),
                    var_95 NUMERIC(36,18),
                    cvar_95 NUMERIC(36,18),
                    quality TEXT NOT NULL DEFAULT 'complete',
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_perf_snapshots_run_ts ON performance_snapshots(run_id, timestamp_ms DESC)"
            )
            await conn.execute(
                """
                ALTER TABLE performance_snapshots
                    ADD COLUMN IF NOT EXISTS avg_win NUMERIC(36,18),
                    ADD COLUMN IF NOT EXISTS avg_loss NUMERIC(36,18),
                    ADD COLUMN IF NOT EXISTS payoff_ratio NUMERIC(36,18),
                    ADD COLUMN IF NOT EXISTS var_95 NUMERIC(36,18),
                    ADD COLUMN IF NOT EXISTS cvar_95 NUMERIC(36,18)
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS attribution_results (
                    attribution_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    group_by TEXT NOT NULL,
                    timestamp_ms BIGINT NOT NULL,
                    account_id TEXT,
                    venue TEXT,
                    run_id TEXT,
                    deployment_id TEXT,
                    strategy_id TEXT,
                    total_pnl NUMERIC(36,18) NOT NULL,
                    explained_pnl NUMERIC(36,18) NOT NULL,
                    residual_pnl NUMERIC(36,18) NOT NULL,
                    items JSONB NOT NULL DEFAULT '[]',
                    quality TEXT NOT NULL DEFAULT 'complete',
                    method TEXT NOT NULL DEFAULT 'absolute_contribution_v1',
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_projection_cursors (
                    projector_name TEXT PRIMARY KEY,
                    last_ts_ms BIGINT NOT NULL,
                    last_execution_id TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_asset_classifications (
                    classification_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    effective_from_ms BIGINT NOT NULL,
                    effective_to_ms BIGINT,
                    source TEXT NOT NULL DEFAULT 'manual',
                    quality TEXT NOT NULL DEFAULT 'complete',
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_perf_asset_class_symbol_effective
                ON performance_asset_classifications(symbol, effective_from_ms DESC)
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_benchmark_holdings (
                    holding_id TEXT PRIMARY KEY,
                    benchmark_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    period_start_ms BIGINT NOT NULL,
                    period_end_ms BIGINT NOT NULL,
                    weight NUMERIC(36,18) NOT NULL,
                    period_return NUMERIC(36,18) NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual',
                    quality TEXT NOT NULL DEFAULT 'complete',
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(benchmark_id, symbol, period_start_ms, period_end_ms)
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_perf_benchmark_holdings_period
                ON performance_benchmark_holdings(benchmark_id, period_start_ms, period_end_ms)
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_portfolio_holding_facts (
                    holding_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    period_start_ms BIGINT NOT NULL,
                    period_end_ms BIGINT NOT NULL,
                    weight NUMERIC(36,18) NOT NULL,
                    period_return NUMERIC(36,18) NOT NULL,
                    source TEXT NOT NULL DEFAULT 'performance_service',
                    quality TEXT NOT NULL DEFAULT 'complete',
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(run_id, symbol, period_start_ms, period_end_ms)
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_perf_portfolio_holdings_period
                ON performance_portfolio_holding_facts(run_id, period_start_ms, period_end_ms)
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_factor_exposures (
                    exposure_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    factor_name TEXT NOT NULL,
                    period_start_ms BIGINT NOT NULL,
                    period_end_ms BIGINT NOT NULL,
                    exposure NUMERIC(36,18) NOT NULL,
                    weight NUMERIC(36,18) NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual',
                    quality TEXT NOT NULL DEFAULT 'complete',
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(run_id, symbol, factor_name, period_start_ms, period_end_ms)
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_perf_factor_exposures_period
                ON performance_factor_exposures(run_id, period_start_ms, period_end_ms)
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_factor_returns (
                    return_id TEXT PRIMARY KEY,
                    factor_name TEXT NOT NULL,
                    period_start_ms BIGINT NOT NULL,
                    period_end_ms BIGINT NOT NULL,
                    period_return NUMERIC(36,18) NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual',
                    quality TEXT NOT NULL DEFAULT 'complete',
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(factor_name, period_start_ms, period_end_ms)
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_perf_factor_returns_period
                ON performance_factor_returns(period_start_ms, period_end_ms)
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_risk_budget_traces (
                    trace_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    period_start_ms BIGINT NOT NULL,
                    period_end_ms BIGINT NOT NULL,
                    constraint_type TEXT NOT NULL,
                    requested_notional NUMERIC(36,18) NOT NULL,
                    allowed_notional NUMERIC(36,18) NOT NULL,
                    final_notional NUMERIC(36,18) NOT NULL,
                    decision TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'risk_sizing_engine',
                    quality TEXT NOT NULL DEFAULT 'complete',
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_perf_risk_budget_traces_period
                ON performance_risk_budget_traces(run_id, period_start_ms, period_end_ms)
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_projection_audit (
                    audit_id TEXT PRIMARY KEY,
                    projector_name TEXT NOT NULL,
                    execution_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 1,
                    last_error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_performance_projection_audit_execution
                ON performance_projection_audit(projector_name, execution_id, created_at)
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_attribution_scope_ts ON attribution_results(scope, group_by, timestamp_ms DESC)"
            )

    async def get_projection_cursor(self, projector_name: str) -> dict[str, Any] | None:
        if await self._ensure_postgres():
            async with self._connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT projector_name, last_ts_ms, last_execution_id
                    FROM performance_projection_cursors
                    WHERE projector_name=$1
                    """,
                    projector_name,
                )
            return dict(row) if row else None
        cursor = self._projection_cursors.get(projector_name)
        return dict(cursor) if cursor else None

    async def save_projection_cursor(
        self,
        *,
        projector_name: str,
        last_ts_ms: int,
        last_execution_id: str,
    ) -> None:
        await self._require_postgres_for_write()
        cursor = {
            "projector_name": projector_name,
            "last_ts_ms": last_ts_ms,
            "last_execution_id": last_execution_id,
        }
        self._projection_cursors[projector_name] = cursor
        if await self._ensure_postgres():
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO performance_projection_cursors
                    (projector_name, last_ts_ms, last_execution_id)
                    VALUES ($1,$2,$3)
                    ON CONFLICT (projector_name) DO UPDATE SET
                        last_ts_ms=EXCLUDED.last_ts_ms,
                        last_execution_id=EXCLUDED.last_execution_id,
                        updated_at=NOW()
                    """,
                    projector_name,
                    last_ts_ms,
                    last_execution_id,
                )

    async def reset_projection_cursor(self, projector_name: str) -> None:
        await self._require_postgres_for_write()
        self._projection_cursors.pop(projector_name, None)
        if await self._ensure_postgres():
            async with self._connection() as conn:
                await conn.execute(
                    "DELETE FROM performance_projection_cursors WHERE projector_name=$1",
                    projector_name,
                )

    async def save_projection_audit(self, audit: dict[str, Any]) -> None:
        await self._require_postgres_for_write()
        self._projection_audits.append(dict(audit))
        if await self._ensure_postgres():
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO performance_projection_audit
                    (audit_id, projector_name, execution_id, run_id, status,
                     attempt_count, last_error)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    """,
                    audit["audit_id"],
                    audit["projector_name"],
                    audit["execution_id"],
                    audit["run_id"],
                    audit["status"],
                    audit["attempt_count"],
                    audit.get("last_error"),
                )

    async def list_projection_audits(
        self,
        *,
        projector_name: str,
        execution_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if await self._ensure_postgres():
            query = """
                SELECT audit_id, projector_name, execution_id, run_id, status,
                       attempt_count, last_error
                FROM performance_projection_audit
                WHERE projector_name=$1
            """
            params: list[Any] = [projector_name]
            if execution_id:
                params.append(execution_id)
                query += " AND execution_id=$2"
            query += " ORDER BY created_at ASC, audit_id ASC"
            async with self._connection() as conn:
                rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]
        return [
            dict(audit)
            for audit in self._projection_audits
            if audit["projector_name"] == projector_name
            and (execution_id is None or audit["execution_id"] == execution_id)
        ]

    async def save_run(self, run: StrategyRun) -> StrategyRun:
        await self._require_postgres_for_write()
        self._runs[run.run_id] = run
        if await self._ensure_postgres():
            assert self._postgres_storage is not None
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO strategy_runs
                    (run_id, deployment_id, strategy_id, account_id, venue, mode,
                     code_version, params_hash, initial_capital, currency, status,
                     started_at_ms, stopped_at_ms, start_reason, stop_reason, quality, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::jsonb)
                    ON CONFLICT (run_id) DO UPDATE SET
                        deployment_id=EXCLUDED.deployment_id,
                        strategy_id=EXCLUDED.strategy_id,
                        account_id=EXCLUDED.account_id,
                        venue=EXCLUDED.venue,
                        mode=EXCLUDED.mode,
                        code_version=EXCLUDED.code_version,
                        params_hash=EXCLUDED.params_hash,
                        initial_capital=EXCLUDED.initial_capital,
                        currency=EXCLUDED.currency,
                        status=EXCLUDED.status,
                        stopped_at_ms=EXCLUDED.stopped_at_ms,
                        stop_reason=EXCLUDED.stop_reason,
                        quality=EXCLUDED.quality,
                        metadata=EXCLUDED.metadata,
                        updated_at=NOW()
                    """,
                    run.run_id,
                    run.deployment_id,
                    run.strategy_id,
                    run.account_id,
                    run.venue,
                    run.mode,
                    run.code_version,
                    run.params_hash,
                    str(run.initial_capital),
                    run.currency,
                    run.status,
                    run.started_at_ms,
                    run.stopped_at_ms,
                    run.start_reason,
                    run.stop_reason,
                    run.quality,
                    json.dumps(run.metadata, default=_json_default),
                )
        return run

    async def get_run(self, run_id: str) -> StrategyRun | None:
        if await self._ensure_postgres():
            assert self._postgres_storage is not None
            async with self._connection() as conn:
                row = await conn.fetchrow("SELECT * FROM strategy_runs WHERE run_id=$1", run_id)
            if row:
                return self._row_to_run(dict(row))
        return self._runs.get(run_id)

    async def list_runs(
        self,
        *,
        deployment_id: str | None = None,
        strategy_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[StrategyRun]:
        if await self._ensure_postgres():
            assert self._postgres_storage is not None
            query = "SELECT * FROM strategy_runs WHERE 1=1"
            params: list[Any] = []
            if deployment_id:
                params.append(deployment_id)
                query += f" AND deployment_id=${len(params)}"
            if strategy_id:
                params.append(strategy_id)
                query += f" AND strategy_id=${len(params)}"
            if status:
                params.append(status)
                query += f" AND status=${len(params)}"
            params.append(limit)
            query += f" ORDER BY started_at_ms DESC LIMIT ${len(params)}"
            async with self._connection() as conn:
                rows = await conn.fetch(query, *params)
            return [self._row_to_run(dict(row)) for row in rows]
        runs = list(self._runs.values())
        if deployment_id:
            runs = [run for run in runs if run.deployment_id == deployment_id]
        if strategy_id:
            runs = [run for run in runs if run.strategy_id == strategy_id]
        if status:
            runs = [run for run in runs if run.status == status]
        return sorted(runs, key=lambda run: run.started_at_ms, reverse=True)[:limit]

    async def save_ledger_entry(self, entry: AccountingLedgerEntry) -> tuple[str, bool]:
        await self._require_postgres_for_write()
        existing = self._ledger_by_key.get(entry.idempotency_key)
        if existing is not None:
            return existing.ledger_entry_id, False
        self._ledger_by_key[entry.idempotency_key] = entry
        self._ledger.append(entry)
        if await self._ensure_postgres():
            assert self._postgres_storage is not None
            async with self._connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO pnl_ledger_entries
                    (ledger_entry_id, idempotency_key, run_id, deployment_id, strategy_id,
                     account_id, venue, event_type, ts_ms, symbol, side, qty, price,
                     trade_notional, gross_pnl, realized_pnl, unrealized_pnl, fee,
                     fee_currency, slippage, funding, cash_flow, cl_ord_id, exec_id,
                     source, quality, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
                            $18,$19,$20,$21,$22,$23,$24,$25,$26,$27::jsonb)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING ledger_entry_id
                    """,
                    entry.ledger_entry_id,
                    entry.idempotency_key,
                    entry.run_id,
                    entry.deployment_id,
                    entry.strategy_id,
                    entry.account_id,
                    entry.venue,
                    entry.event_type,
                    entry.ts_ms,
                    entry.symbol,
                    entry.side,
                    str(entry.qty),
                    str(entry.price),
                    str(entry.trade_notional),
                    str(entry.gross_pnl),
                    str(entry.realized_pnl),
                    str(entry.unrealized_pnl),
                    str(entry.fee),
                    entry.fee_currency,
                    str(entry.slippage),
                    str(entry.funding),
                    str(entry.cash_flow),
                    entry.cl_ord_id,
                    entry.exec_id,
                    entry.source,
                    entry.quality,
                    json.dumps(entry.metadata, default=_json_default),
                )
            if row is None:
                return entry.ledger_entry_id, False
        return entry.ledger_entry_id, True

    async def list_ledger_entries(
        self,
        *,
        run_id: str | None = None,
        deployment_id: str | None = None,
        strategy_id: str | None = None,
        account_id: str | None = None,
        venue: str | None = None,
        since_ms: int | None = None,
        limit: int = 1000,
    ) -> list[AccountingLedgerEntry]:
        return await self._query_ledger_entries(
            run_id=run_id,
            deployment_id=deployment_id,
            strategy_id=strategy_id,
            account_id=account_id,
            venue=venue,
            since_ms=since_ms,
            limit=limit,
        )

    async def list_all_ledger_entries(
        self,
        *,
        run_id: str | None = None,
        deployment_id: str | None = None,
        strategy_id: str | None = None,
        account_id: str | None = None,
        venue: str | None = None,
        since_ms: int | None = None,
    ) -> list[AccountingLedgerEntry]:
        """Return complete ledger history for deterministic replay."""

        return await self._query_ledger_entries(
            run_id=run_id,
            deployment_id=deployment_id,
            strategy_id=strategy_id,
            account_id=account_id,
            venue=venue,
            since_ms=since_ms,
            limit=None,
        )

    async def _query_ledger_entries(
        self,
        *,
        run_id: str | None,
        deployment_id: str | None,
        strategy_id: str | None,
        account_id: str | None,
        venue: str | None,
        since_ms: int | None,
        limit: int | None,
    ) -> list[AccountingLedgerEntry]:
        if await self._ensure_postgres():
            assert self._postgres_storage is not None
            query = "SELECT * FROM pnl_ledger_entries WHERE 1=1"
            params: list[Any] = []
            for field, value in [
                ("run_id", run_id),
                ("deployment_id", deployment_id),
                ("strategy_id", strategy_id),
                ("account_id", account_id),
                ("venue", venue),
            ]:
                if value:
                    params.append(value)
                    query += f" AND {field}=${len(params)}"
            if since_ms is not None:
                params.append(since_ms)
                query += f" AND ts_ms>=${len(params)}"
            query += " ORDER BY ts_ms ASC, ledger_entry_id ASC"
            if limit is not None:
                params.append(limit)
                query += f" LIMIT ${len(params)}"
            async with self._connection() as conn:
                rows = await conn.fetch(query, *params)
            return [self._row_to_ledger(dict(row)) for row in rows]

        entries = list(self._ledger)
        if run_id:
            entries = [entry for entry in entries if entry.run_id == run_id]
        if deployment_id:
            entries = [entry for entry in entries if entry.deployment_id == deployment_id]
        if strategy_id:
            entries = [entry for entry in entries if entry.strategy_id == strategy_id]
        if account_id:
            entries = [entry for entry in entries if entry.account_id == account_id]
        if venue:
            entries = [entry for entry in entries if entry.venue == venue]
        if since_ms is not None:
            entries = [entry for entry in entries if entry.ts_ms >= since_ms]
        ordered = sorted(entries, key=lambda entry: (entry.ts_ms, entry.ledger_entry_id))
        return ordered[:limit] if limit is not None else ordered

    async def save_cash_flow(self, flow: CashFlowEvent) -> CashFlowEvent:
        await self._require_postgres_for_write()
        self._cash_flows[flow.cash_flow_id] = flow
        if await self._ensure_postgres():
            assert self._postgres_storage is not None
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO cash_flows
                    (cash_flow_id, run_id, deployment_id, strategy_id, account_id, venue,
                     amount, currency, flow_type, ts_ms, source, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
                    ON CONFLICT (cash_flow_id) DO NOTHING
                    """,
                    flow.cash_flow_id,
                    flow.run_id,
                    flow.deployment_id,
                    flow.strategy_id,
                    flow.account_id,
                    flow.venue,
                    str(flow.amount),
                    flow.currency,
                    flow.flow_type,
                    flow.ts_ms,
                    flow.source,
                    json.dumps(flow.metadata, default=_json_default),
                )
        return flow

    async def list_cash_flows(self, run_id: str) -> list[CashFlowEvent]:
        if await self._ensure_postgres():
            assert self._postgres_storage is not None
            async with self._connection() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM cash_flows WHERE run_id=$1 ORDER BY ts_ms ASC", run_id
                )
            return [self._row_to_cash_flow(dict(row)) for row in rows]
        return sorted(
            [flow for flow in self._cash_flows.values() if flow.run_id == run_id],
            key=lambda flow: flow.ts_ms,
        )

    async def save_nav_point(self, point: NAVPointV2) -> NAVPointV2:
        await self._require_postgres_for_write()
        self._nav_points = [
            existing
            for existing in self._nav_points
            if not (existing.run_id == point.run_id and existing.timestamp_ms == point.timestamp_ms)
        ]
        self._nav_points.append(point)
        if await self._ensure_postgres():
            assert self._postgres_storage is not None
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO nav_points_v2
                    (nav_id, run_id, deployment_id, strategy_id, account_id, venue,
                     timestamp_ms, equity, cash, position_value, realized_pnl,
                     unrealized_pnl, fee, slippage, funding, cash_flow, total_pnl,
                     net_return, gross_return, twr, mwr, quality, source, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                            $17,$18,$19,$20,$21,$22,$23,$24::jsonb)
                    ON CONFLICT (run_id, timestamp_ms) DO UPDATE SET
                        equity=EXCLUDED.equity,
                        cash=EXCLUDED.cash,
                        position_value=EXCLUDED.position_value,
                        realized_pnl=EXCLUDED.realized_pnl,
                        unrealized_pnl=EXCLUDED.unrealized_pnl,
                        fee=EXCLUDED.fee,
                        slippage=EXCLUDED.slippage,
                        funding=EXCLUDED.funding,
                        cash_flow=EXCLUDED.cash_flow,
                        total_pnl=EXCLUDED.total_pnl,
                        net_return=EXCLUDED.net_return,
                        gross_return=EXCLUDED.gross_return,
                        twr=EXCLUDED.twr,
                        mwr=EXCLUDED.mwr,
                        quality=EXCLUDED.quality,
                        source=EXCLUDED.source,
                        metadata=EXCLUDED.metadata
                    """,
                    point.nav_id,
                    point.run_id,
                    point.deployment_id,
                    point.strategy_id,
                    point.account_id,
                    point.venue,
                    point.timestamp_ms,
                    str(point.equity),
                    str(point.cash),
                    str(point.position_value),
                    str(point.realized_pnl),
                    str(point.unrealized_pnl),
                    str(point.fee),
                    str(point.slippage),
                    str(point.funding),
                    str(point.cash_flow),
                    str(point.total_pnl),
                    str(point.net_return),
                    str(point.gross_return),
                    str(point.twr),
                    str(point.mwr) if point.mwr is not None else None,
                    point.quality,
                    point.source,
                    json.dumps(point.metadata, default=_json_default),
                )
        return point

    async def list_nav_points(
        self,
        *,
        run_id: str | None = None,
        deployment_id: str | None = None,
        strategy_id: str | None = None,
        account_id: str | None = None,
        venue: str | None = None,
        since_ms: int | None = None,
        limit: int = 500,
    ) -> list[NAVPointV2]:
        return await self._query_nav_points(
            run_id=run_id,
            deployment_id=deployment_id,
            strategy_id=strategy_id,
            account_id=account_id,
            venue=venue,
            since_ms=since_ms,
            limit=limit,
        )

    async def list_all_nav_points(
        self,
        *,
        run_id: str | None = None,
        deployment_id: str | None = None,
        strategy_id: str | None = None,
        account_id: str | None = None,
        venue: str | None = None,
        since_ms: int | None = None,
    ) -> list[NAVPointV2]:
        """Return complete NAV history for metric and report recomputation."""

        return await self._query_nav_points(
            run_id=run_id,
            deployment_id=deployment_id,
            strategy_id=strategy_id,
            account_id=account_id,
            venue=venue,
            since_ms=since_ms,
            limit=None,
        )

    async def _query_nav_points(
        self,
        *,
        run_id: str | None,
        deployment_id: str | None,
        strategy_id: str | None,
        account_id: str | None,
        venue: str | None,
        since_ms: int | None,
        limit: int | None,
    ) -> list[NAVPointV2]:
        if await self._ensure_postgres():
            assert self._postgres_storage is not None
            query = "SELECT * FROM nav_points_v2 WHERE 1=1"
            params: list[Any] = []
            for field, value in [
                ("run_id", run_id),
                ("deployment_id", deployment_id),
                ("strategy_id", strategy_id),
                ("account_id", account_id),
                ("venue", venue),
            ]:
                if value:
                    params.append(value)
                    query += f" AND {field}=${len(params)}"
            if since_ms is not None:
                params.append(since_ms)
                query += f" AND timestamp_ms>=${len(params)}"
            query += " ORDER BY timestamp_ms ASC, nav_id ASC"
            if limit is not None:
                params.append(limit)
                query += f" LIMIT ${len(params)}"
            async with self._connection() as conn:
                rows = await conn.fetch(query, *params)
            return [self._row_to_nav(dict(row)) for row in rows]
        points = list(self._nav_points)
        if run_id:
            points = [point for point in points if point.run_id == run_id]
        if deployment_id:
            points = [point for point in points if point.deployment_id == deployment_id]
        if strategy_id:
            points = [point for point in points if point.strategy_id == strategy_id]
        if account_id:
            points = [point for point in points if point.account_id == account_id]
        if venue:
            points = [point for point in points if point.venue == venue]
        if since_ms is not None:
            points = [point for point in points if point.timestamp_ms >= since_ms]
        ordered = sorted(points, key=lambda point: (point.timestamp_ms, point.nav_id))
        return ordered[:limit] if limit is not None else ordered

    async def save_account_nav_point(self, point: AccountPortfolioNAV) -> AccountPortfolioNAV:
        await self._require_postgres_for_write()
        self._account_nav_points = [
            existing
            for existing in self._account_nav_points
            if not (
                existing.account_id == point.account_id
                and existing.venue == point.venue
                and existing.timestamp_ms == point.timestamp_ms
            )
        ]
        self._account_nav_points.append(point)
        if await self._ensure_postgres():
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO account_portfolio_nav
                    (account_nav_id, account_id, venue, timestamp_ms, currency, equity,
                     cash, position_value, liabilities, initial_capital,
                     cumulative_cash_flow, total_pnl, run_equity, run_total_pnl,
                     unallocated_cash, reconciliation_residual, quality, source, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                            $17,$18,$19::jsonb)
                    ON CONFLICT (account_id, venue, timestamp_ms) DO UPDATE SET
                        currency=EXCLUDED.currency,
                        equity=EXCLUDED.equity,
                        cash=EXCLUDED.cash,
                        position_value=EXCLUDED.position_value,
                        liabilities=EXCLUDED.liabilities,
                        initial_capital=EXCLUDED.initial_capital,
                        cumulative_cash_flow=EXCLUDED.cumulative_cash_flow,
                        total_pnl=EXCLUDED.total_pnl,
                        run_equity=EXCLUDED.run_equity,
                        run_total_pnl=EXCLUDED.run_total_pnl,
                        unallocated_cash=EXCLUDED.unallocated_cash,
                        reconciliation_residual=EXCLUDED.reconciliation_residual,
                        quality=EXCLUDED.quality,
                        source=EXCLUDED.source,
                        metadata=EXCLUDED.metadata
                    """,
                    point.account_nav_id,
                    point.account_id,
                    point.venue,
                    point.timestamp_ms,
                    point.currency,
                    str(point.equity),
                    str(point.cash),
                    str(point.position_value),
                    str(point.liabilities),
                    str(point.initial_capital),
                    str(point.cumulative_cash_flow),
                    str(point.total_pnl),
                    str(point.run_equity),
                    str(point.run_total_pnl),
                    str(point.unallocated_cash),
                    str(point.reconciliation_residual),
                    point.quality,
                    point.source,
                    json.dumps(point.metadata, default=_json_default),
                )
        return point

    async def list_account_nav_points(
        self,
        *,
        account_id: str,
        venue: str | None = None,
        since_ms: int | None = None,
        limit: int = 500,
    ) -> list[AccountPortfolioNAV]:
        if await self._ensure_postgres():
            query = "SELECT * FROM account_portfolio_nav WHERE account_id=$1"
            params: list[Any] = [account_id]
            if venue:
                params.append(venue)
                query += f" AND venue=${len(params)}"
            if since_ms is not None:
                params.append(since_ms)
                query += f" AND timestamp_ms>=${len(params)}"
            params.append(limit)
            query += f" ORDER BY timestamp_ms ASC, account_nav_id ASC LIMIT ${len(params)}"
            async with self._connection() as conn:
                rows = await conn.fetch(query, *params)
            return [self._row_to_account_nav(dict(row)) for row in rows]
        points = [point for point in self._account_nav_points if point.account_id == account_id]
        if venue:
            points = [point for point in points if point.venue == venue]
        if since_ms is not None:
            points = [point for point in points if point.timestamp_ms >= since_ms]
        return sorted(points, key=lambda point: (point.timestamp_ms, point.account_nav_id))[:limit]

    async def get_latest_account_nav(
        self,
        *,
        account_id: str,
        venue: str | None = None,
    ) -> AccountPortfolioNAV | None:
        if await self._ensure_postgres():
            query = "SELECT * FROM account_portfolio_nav WHERE account_id=$1"
            params: list[Any] = [account_id]
            if venue:
                params.append(venue)
                query += f" AND venue=${len(params)}"
            query += " ORDER BY timestamp_ms DESC, account_nav_id DESC LIMIT 1"
            async with self._connection() as conn:
                row = await conn.fetchrow(query, *params)
            return self._row_to_account_nav(dict(row)) if row else None
        points = [point for point in self._account_nav_points if point.account_id == account_id]
        if venue:
            points = [point for point in points if point.venue == venue]
        return max(points, key=lambda point: point.timestamp_ms) if points else None

    async def save_mark_snapshot(self, snapshot: MarkPriceSnapshot) -> MarkPriceSnapshot:
        await self._require_postgres_for_write()
        self._mark_snapshots = [
            existing
            for existing in self._mark_snapshots
            if not (
                existing.run_id == snapshot.run_id
                and existing.symbol == snapshot.symbol
                and existing.timestamp_ms == snapshot.timestamp_ms
            )
        ]
        self._mark_snapshots.append(snapshot)
        if await self._ensure_postgres():
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO performance_mark_snapshots
                    (mark_id, run_id, deployment_id, strategy_id, account_id, venue,
                     symbol, timestamp_ms, mark_price, source, quality, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
                    ON CONFLICT (run_id, symbol, timestamp_ms) DO UPDATE SET
                        mark_price=EXCLUDED.mark_price,
                        source=EXCLUDED.source,
                        quality=EXCLUDED.quality,
                        metadata=EXCLUDED.metadata
                    """,
                    snapshot.mark_id,
                    snapshot.run_id,
                    snapshot.deployment_id,
                    snapshot.strategy_id,
                    snapshot.account_id,
                    snapshot.venue,
                    snapshot.symbol,
                    snapshot.timestamp_ms,
                    str(snapshot.mark_price),
                    snapshot.source,
                    snapshot.quality,
                    json.dumps(snapshot.metadata, default=_json_default),
                )
        return snapshot

    async def list_mark_snapshots(
        self,
        *,
        run_id: str,
        symbol: str | None = None,
        since_ms: int | None = None,
        limit: int = 500,
    ) -> list[MarkPriceSnapshot]:
        if await self._ensure_postgres():
            query = "SELECT * FROM performance_mark_snapshots WHERE run_id=$1"
            params: list[Any] = [run_id]
            if symbol:
                params.append(symbol)
                query += f" AND symbol=${len(params)}"
            if since_ms is not None:
                params.append(since_ms)
                query += f" AND timestamp_ms>=${len(params)}"
            params.append(limit)
            query += f" ORDER BY timestamp_ms ASC, mark_id ASC LIMIT ${len(params)}"
            async with self._connection() as conn:
                rows = await conn.fetch(query, *params)
            return [self._row_to_mark_snapshot(dict(row)) for row in rows]
        points = [point for point in self._mark_snapshots if point.run_id == run_id]
        if symbol:
            points = [point for point in points if point.symbol == symbol]
        if since_ms is not None:
            points = [point for point in points if point.timestamp_ms >= since_ms]
        return sorted(points, key=lambda point: (point.timestamp_ms, point.mark_id))[:limit]

    async def list_latest_mark_snapshots(
        self,
        *,
        run_id: str,
        as_of_ms: int | None = None,
    ) -> list[MarkPriceSnapshot]:
        if await self._ensure_postgres():
            params: list[Any] = [run_id]
            time_filter = ""
            if as_of_ms is not None:
                params.append(as_of_ms)
                time_filter = " AND timestamp_ms<=$2"
            async with self._connection() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT DISTINCT ON (symbol) *
                    FROM performance_mark_snapshots
                    WHERE run_id=$1{time_filter}
                    ORDER BY symbol, timestamp_ms DESC, mark_id DESC
                    """,
                    *params,
                )
            return [self._row_to_mark_snapshot(dict(row)) for row in rows]
        latest: dict[str, MarkPriceSnapshot] = {}
        for point in self._mark_snapshots:
            if point.run_id != run_id or (as_of_ms is not None and point.timestamp_ms > as_of_ms):
                continue
            current = latest.get(point.symbol)
            if current is None or point.timestamp_ms > current.timestamp_ms:
                latest[point.symbol] = point
        return list(latest.values())

    async def save_performance_snapshot(self, snapshot: PerformanceSnapshot) -> PerformanceSnapshot:
        await self._require_postgres_for_write()
        self._snapshots.append(snapshot)
        if await self._ensure_postgres():
            assert self._postgres_storage is not None
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO performance_snapshots
                    (snapshot_id, run_id, deployment_id, strategy_id, account_id, venue,
                     timestamp_ms, equity, total_pnl, net_return, twr, mwr,
                     max_drawdown, sharpe, sortino, calmar, turnover, exposure, fees,
                     slippage, funding, realized_pnl, unrealized_pnl, win_rate,
                     profit_factor, avg_win, avg_loss, payoff_ratio, var_95, cvar_95,
                     quality, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                            $17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,
                            $32::jsonb)
                    """,
                    snapshot.snapshot_id,
                    snapshot.run_id,
                    snapshot.deployment_id,
                    snapshot.strategy_id,
                    snapshot.account_id,
                    snapshot.venue,
                    snapshot.timestamp_ms,
                    str(snapshot.equity),
                    str(snapshot.total_pnl),
                    str(snapshot.net_return),
                    str(snapshot.twr),
                    str(snapshot.mwr) if snapshot.mwr is not None else None,
                    str(snapshot.max_drawdown),
                    str(snapshot.sharpe) if snapshot.sharpe is not None else None,
                    str(snapshot.sortino) if snapshot.sortino is not None else None,
                    str(snapshot.calmar) if snapshot.calmar is not None else None,
                    str(snapshot.turnover),
                    str(snapshot.exposure),
                    str(snapshot.fees),
                    str(snapshot.slippage),
                    str(snapshot.funding),
                    str(snapshot.realized_pnl),
                    str(snapshot.unrealized_pnl),
                    str(snapshot.win_rate) if snapshot.win_rate is not None else None,
                    str(snapshot.profit_factor) if snapshot.profit_factor is not None else None,
                    str(snapshot.avg_win) if snapshot.avg_win is not None else None,
                    str(snapshot.avg_loss) if snapshot.avg_loss is not None else None,
                    str(snapshot.payoff_ratio) if snapshot.payoff_ratio is not None else None,
                    str(snapshot.var_95) if snapshot.var_95 is not None else None,
                    str(snapshot.cvar_95) if snapshot.cvar_95 is not None else None,
                    snapshot.quality,
                    json.dumps(snapshot.metadata, default=_json_default),
                )
        return snapshot

    async def get_latest_snapshot(self, run_id: str) -> PerformanceSnapshot | None:
        if await self._ensure_postgres():
            assert self._postgres_storage is not None
            async with self._connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM performance_snapshots
                    WHERE run_id=$1
                    ORDER BY timestamp_ms DESC
                    LIMIT 1
                    """,
                    run_id,
                )
            if row:
                return self._row_to_snapshot(dict(row))
        matches = [snapshot for snapshot in self._snapshots if snapshot.run_id == run_id]
        return max(matches, key=lambda snapshot: snapshot.timestamp_ms) if matches else None

    async def save_asset_classification(
        self, classification: AssetClassification
    ) -> AssetClassification:
        await self._require_postgres_for_write()
        self._asset_classifications = [
            item
            for item in self._asset_classifications
            if item.classification_id != classification.classification_id
        ]
        self._asset_classifications.append(classification)
        if await self._ensure_postgres():
            assert self._postgres_storage is not None
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO performance_asset_classifications
                    (classification_id, symbol, asset_class, effective_from_ms, effective_to_ms,
                     source, quality, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
                    ON CONFLICT (classification_id) DO UPDATE SET
                        symbol=EXCLUDED.symbol,
                        asset_class=EXCLUDED.asset_class,
                        effective_from_ms=EXCLUDED.effective_from_ms,
                        effective_to_ms=EXCLUDED.effective_to_ms,
                        source=EXCLUDED.source,
                        quality=EXCLUDED.quality,
                        metadata=EXCLUDED.metadata
                    """,
                    classification.classification_id,
                    classification.symbol,
                    classification.asset_class,
                    classification.effective_from_ms,
                    classification.effective_to_ms,
                    classification.source,
                    classification.quality,
                    json.dumps(classification.metadata, default=_json_default),
                )
        return classification

    async def list_asset_classifications(
        self, *, as_of_ms: int | None = None
    ) -> list[AssetClassification]:
        if await self._ensure_postgres():
            query = "SELECT * FROM performance_asset_classifications WHERE 1=1"
            params: list[Any] = []
            if as_of_ms is not None:
                params.append(as_of_ms)
                query += f" AND effective_from_ms<=${len(params)}"
                params.append(as_of_ms)
                query += f" AND (effective_to_ms IS NULL OR effective_to_ms>${len(params)})"
            query += " ORDER BY symbol ASC, effective_from_ms DESC"
            async with self._connection() as conn:
                rows = await conn.fetch(query, *params)
            return [self._row_to_asset_classification(dict(row)) for row in rows]
        items = list(self._asset_classifications)
        if as_of_ms is not None:
            items = [
                item
                for item in items
                if item.effective_from_ms <= as_of_ms
                and (item.effective_to_ms is None or item.effective_to_ms > as_of_ms)
            ]
        return sorted(items, key=lambda item: (item.symbol, -item.effective_from_ms))

    async def save_benchmark_holding(self, holding: BenchmarkHolding) -> BenchmarkHolding:
        await self._require_postgres_for_write()
        self._benchmark_holdings = [
            item
            for item in self._benchmark_holdings
            if item.holding_id != holding.holding_id
            and not (
                item.benchmark_id == holding.benchmark_id
                and item.symbol == holding.symbol
                and item.period_start_ms == holding.period_start_ms
                and item.period_end_ms == holding.period_end_ms
            )
        ]
        self._benchmark_holdings.append(holding)
        if await self._ensure_postgres():
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO performance_benchmark_holdings
                    (holding_id, benchmark_id, symbol, period_start_ms, period_end_ms,
                     weight, period_return, source, quality, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
                    ON CONFLICT (benchmark_id, symbol, period_start_ms, period_end_ms)
                    DO UPDATE SET
                        holding_id=EXCLUDED.holding_id,
                        weight=EXCLUDED.weight,
                        period_return=EXCLUDED.period_return,
                        source=EXCLUDED.source,
                        quality=EXCLUDED.quality,
                        metadata=EXCLUDED.metadata
                    """,
                    holding.holding_id,
                    holding.benchmark_id,
                    holding.symbol,
                    holding.period_start_ms,
                    holding.period_end_ms,
                    str(holding.weight),
                    str(holding.period_return),
                    holding.source,
                    holding.quality,
                    json.dumps(holding.metadata, default=_json_default),
                )
        return holding

    async def list_benchmark_holdings(
        self, *, benchmark_id: str, period_start_ms: int, period_end_ms: int
    ) -> list[BenchmarkHolding]:
        if await self._ensure_postgres():
            async with self._connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM performance_benchmark_holdings
                    WHERE benchmark_id=$1 AND period_start_ms=$2 AND period_end_ms=$3
                    ORDER BY symbol ASC
                    """,
                    benchmark_id,
                    period_start_ms,
                    period_end_ms,
                )
            return [self._row_to_benchmark_holding(dict(row)) for row in rows]
        return sorted(
            [
                item
                for item in self._benchmark_holdings
                if item.benchmark_id == benchmark_id
                and item.period_start_ms == period_start_ms
                and item.period_end_ms == period_end_ms
            ],
            key=lambda item: item.symbol,
        )

    async def save_portfolio_holding_fact(
        self, holding: PortfolioHoldingFact
    ) -> PortfolioHoldingFact:
        await self._require_postgres_for_write()
        self._portfolio_holding_facts = [
            item
            for item in self._portfolio_holding_facts
            if item.holding_id != holding.holding_id
            and not (
                item.run_id == holding.run_id
                and item.symbol == holding.symbol
                and item.period_start_ms == holding.period_start_ms
                and item.period_end_ms == holding.period_end_ms
            )
        ]
        self._portfolio_holding_facts.append(holding)
        if await self._ensure_postgres():
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO performance_portfolio_holding_facts
                    (holding_id, run_id, symbol, period_start_ms, period_end_ms,
                     weight, period_return, source, quality, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
                    ON CONFLICT (run_id, symbol, period_start_ms, period_end_ms)
                    DO UPDATE SET
                        holding_id=EXCLUDED.holding_id,
                        weight=EXCLUDED.weight,
                        period_return=EXCLUDED.period_return,
                        source=EXCLUDED.source,
                        quality=EXCLUDED.quality,
                        metadata=EXCLUDED.metadata
                    """,
                    holding.holding_id,
                    holding.run_id,
                    holding.symbol,
                    holding.period_start_ms,
                    holding.period_end_ms,
                    str(holding.weight),
                    str(holding.period_return),
                    holding.source,
                    holding.quality,
                    json.dumps(holding.metadata, default=_json_default),
                )
        return holding

    async def list_portfolio_holding_facts(
        self, *, run_id: str, period_start_ms: int, period_end_ms: int
    ) -> list[PortfolioHoldingFact]:
        if await self._ensure_postgres():
            async with self._connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM performance_portfolio_holding_facts
                    WHERE run_id=$1 AND period_start_ms=$2 AND period_end_ms=$3
                    ORDER BY symbol ASC
                    """,
                    run_id,
                    period_start_ms,
                    period_end_ms,
                )
            return [self._row_to_portfolio_holding_fact(dict(row)) for row in rows]
        return sorted(
            [
                item
                for item in self._portfolio_holding_facts
                if item.run_id == run_id
                and item.period_start_ms == period_start_ms
                and item.period_end_ms == period_end_ms
            ],
            key=lambda item: item.symbol,
        )

    async def save_factor_exposure(self, exposure: FactorExposureFact) -> FactorExposureFact:
        await self._require_postgres_for_write()
        self._factor_exposures = [
            item
            for item in self._factor_exposures
            if item.exposure_id != exposure.exposure_id
            and not (
                item.run_id == exposure.run_id
                and item.symbol == exposure.symbol
                and item.factor_name == exposure.factor_name
                and item.period_start_ms == exposure.period_start_ms
                and item.period_end_ms == exposure.period_end_ms
            )
        ]
        self._factor_exposures.append(exposure)
        if await self._ensure_postgres():
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO performance_factor_exposures
                    (exposure_id, run_id, symbol, factor_name, period_start_ms, period_end_ms,
                     exposure, weight, source, quality, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb)
                    ON CONFLICT (run_id, symbol, factor_name, period_start_ms, period_end_ms)
                    DO UPDATE SET
                        exposure_id=EXCLUDED.exposure_id,
                        exposure=EXCLUDED.exposure,
                        weight=EXCLUDED.weight,
                        source=EXCLUDED.source,
                        quality=EXCLUDED.quality,
                        metadata=EXCLUDED.metadata
                    """,
                    exposure.exposure_id,
                    exposure.run_id,
                    exposure.symbol,
                    exposure.factor_name,
                    exposure.period_start_ms,
                    exposure.period_end_ms,
                    str(exposure.exposure),
                    str(exposure.weight),
                    exposure.source,
                    exposure.quality,
                    json.dumps(exposure.metadata, default=_json_default),
                )
        return exposure

    async def list_factor_exposures(
        self, *, run_id: str, period_start_ms: int, period_end_ms: int
    ) -> list[FactorExposureFact]:
        if await self._ensure_postgres():
            async with self._connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM performance_factor_exposures
                    WHERE run_id=$1 AND period_start_ms=$2 AND period_end_ms=$3
                    ORDER BY factor_name ASC, symbol ASC
                    """,
                    run_id,
                    period_start_ms,
                    period_end_ms,
                )
            return [self._row_to_factor_exposure(dict(row)) for row in rows]
        return sorted(
            [
                item
                for item in self._factor_exposures
                if item.run_id == run_id
                and item.period_start_ms == period_start_ms
                and item.period_end_ms == period_end_ms
            ],
            key=lambda item: (item.factor_name, item.symbol),
        )

    async def save_factor_return(self, factor_return: FactorReturnFact) -> FactorReturnFact:
        await self._require_postgres_for_write()
        self._factor_returns = [
            item
            for item in self._factor_returns
            if item.return_id != factor_return.return_id
            and not (
                item.factor_name == factor_return.factor_name
                and item.period_start_ms == factor_return.period_start_ms
                and item.period_end_ms == factor_return.period_end_ms
            )
        ]
        self._factor_returns.append(factor_return)
        if await self._ensure_postgres():
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO performance_factor_returns
                    (return_id, factor_name, period_start_ms, period_end_ms, period_return,
                     source, quality, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
                    ON CONFLICT (factor_name, period_start_ms, period_end_ms)
                    DO UPDATE SET
                        return_id=EXCLUDED.return_id,
                        period_return=EXCLUDED.period_return,
                        source=EXCLUDED.source,
                        quality=EXCLUDED.quality,
                        metadata=EXCLUDED.metadata
                    """,
                    factor_return.return_id,
                    factor_return.factor_name,
                    factor_return.period_start_ms,
                    factor_return.period_end_ms,
                    str(factor_return.period_return),
                    factor_return.source,
                    factor_return.quality,
                    json.dumps(factor_return.metadata, default=_json_default),
                )
        return factor_return

    async def list_factor_returns(
        self, *, period_start_ms: int, period_end_ms: int
    ) -> list[FactorReturnFact]:
        if await self._ensure_postgres():
            async with self._connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM performance_factor_returns
                    WHERE period_start_ms=$1 AND period_end_ms=$2
                    ORDER BY factor_name ASC
                    """,
                    period_start_ms,
                    period_end_ms,
                )
            return [self._row_to_factor_return(dict(row)) for row in rows]
        return sorted(
            [
                item
                for item in self._factor_returns
                if item.period_start_ms == period_start_ms and item.period_end_ms == period_end_ms
            ],
            key=lambda item: item.factor_name,
        )

    async def save_risk_budget_trace(
        self, trace: RiskBudgetDecisionTrace
    ) -> RiskBudgetDecisionTrace:
        await self._require_postgres_for_write()
        self._risk_budget_traces = [
            item for item in self._risk_budget_traces if item.trace_id != trace.trace_id
        ]
        self._risk_budget_traces.append(trace)
        if await self._ensure_postgres():
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO performance_risk_budget_traces
                    (trace_id, run_id, period_start_ms, period_end_ms, constraint_type,
                     requested_notional, allowed_notional, final_notional, decision,
                     source, quality, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
                    ON CONFLICT (trace_id) DO UPDATE SET
                        run_id=EXCLUDED.run_id,
                        period_start_ms=EXCLUDED.period_start_ms,
                        period_end_ms=EXCLUDED.period_end_ms,
                        constraint_type=EXCLUDED.constraint_type,
                        requested_notional=EXCLUDED.requested_notional,
                        allowed_notional=EXCLUDED.allowed_notional,
                        final_notional=EXCLUDED.final_notional,
                        decision=EXCLUDED.decision,
                        source=EXCLUDED.source,
                        quality=EXCLUDED.quality,
                        metadata=EXCLUDED.metadata
                    """,
                    trace.trace_id,
                    trace.run_id,
                    trace.period_start_ms,
                    trace.period_end_ms,
                    trace.constraint_type,
                    str(trace.requested_notional),
                    str(trace.allowed_notional),
                    str(trace.final_notional),
                    trace.decision,
                    trace.source,
                    trace.quality,
                    json.dumps(trace.metadata, default=_json_default),
                )
        return trace

    async def list_risk_budget_traces(
        self, *, run_id: str, period_start_ms: int, period_end_ms: int
    ) -> list[RiskBudgetDecisionTrace]:
        if await self._ensure_postgres():
            async with self._connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM performance_risk_budget_traces
                    WHERE run_id=$1 AND period_start_ms=$2 AND period_end_ms=$3
                    ORDER BY constraint_type ASC, trace_id ASC
                    """,
                    run_id,
                    period_start_ms,
                    period_end_ms,
                )
            return [self._row_to_risk_budget_trace(dict(row)) for row in rows]
        return sorted(
            [
                item
                for item in self._risk_budget_traces
                if item.run_id == run_id
                and item.period_start_ms == period_start_ms
                and item.period_end_ms == period_end_ms
            ],
            key=lambda item: (item.constraint_type, item.trace_id),
        )

    async def save_attribution_result(self, result: AttributionResult) -> AttributionResult:
        await self._require_postgres_for_write()
        self._attribution_results.append(result)
        if await self._ensure_postgres():
            assert self._postgres_storage is not None
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO attribution_results
                    (attribution_id, scope, group_by, timestamp_ms, account_id, venue,
                     run_id, deployment_id, strategy_id, total_pnl, explained_pnl,
                     residual_pnl, items, quality, method, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14,$15,$16::jsonb)
                    """,
                    result.attribution_id,
                    result.scope,
                    result.group_by,
                    result.timestamp_ms,
                    result.account_id,
                    result.venue,
                    result.run_id,
                    result.deployment_id,
                    result.strategy_id,
                    str(result.total_pnl),
                    str(result.explained_pnl),
                    str(result.residual_pnl),
                    json.dumps([item.to_dict() for item in result.items], default=_json_default),
                    result.quality,
                    result.method,
                    json.dumps(result.metadata, default=_json_default),
                )
        return result

    async def get_latest_attribution(
        self,
        *,
        scope: str,
        group_by: str,
        run_id: str | None = None,
        account_id: str | None = None,
    ) -> AttributionResult | None:
        if await self._ensure_postgres():
            assert self._postgres_storage is not None
            query = "SELECT * FROM attribution_results WHERE scope=$1 AND group_by=$2"
            params: list[Any] = [scope, group_by]
            if run_id:
                params.append(run_id)
                query += f" AND run_id=${len(params)}"
            if account_id:
                params.append(account_id)
                query += f" AND account_id=${len(params)}"
            query += " ORDER BY timestamp_ms DESC LIMIT 1"
            async with self._connection() as conn:
                row = await conn.fetchrow(query, *params)
            if row:
                return self._row_to_attribution(dict(row))
        matches = [
            result
            for result in self._attribution_results
            if result.scope == scope
            and result.group_by == group_by
            and (run_id is None or result.run_id == run_id)
            and (account_id is None or result.account_id == account_id)
        ]
        return max(matches, key=lambda result: result.timestamp_ms) if matches else None

    def _row_to_run(self, row: dict[str, Any]) -> StrategyRun:
        return StrategyRun(
            run_id=row["run_id"],
            deployment_id=row["deployment_id"],
            strategy_id=row["strategy_id"],
            account_id=row["account_id"],
            venue=row["venue"],
            mode=row.get("mode") or "paper",
            code_version=row.get("code_version"),
            params_hash=row.get("params_hash"),
            initial_capital=_decimal(row.get("initial_capital")),
            currency=row.get("currency") or "USDT",
            status=row.get("status") or "RUNNING",
            started_at_ms=int(row.get("started_at_ms") or 0),
            stopped_at_ms=row.get("stopped_at_ms"),
            start_reason=row.get("start_reason"),
            stop_reason=row.get("stop_reason"),
            quality=row.get("quality") or "complete",
            metadata=_metadata(row.get("metadata")),
        )

    def _row_to_ledger(self, row: dict[str, Any]) -> AccountingLedgerEntry:
        return AccountingLedgerEntry(
            ledger_entry_id=row["ledger_entry_id"],
            idempotency_key=row["idempotency_key"],
            run_id=row["run_id"],
            deployment_id=row["deployment_id"],
            strategy_id=row["strategy_id"],
            account_id=row["account_id"],
            venue=row["venue"],
            event_type=row["event_type"],
            ts_ms=int(row["ts_ms"]),
            symbol=row.get("symbol"),
            side=row.get("side"),
            qty=_decimal(row.get("qty")),
            price=_decimal(row.get("price")),
            trade_notional=_decimal(row.get("trade_notional")),
            gross_pnl=_decimal(row.get("gross_pnl")),
            realized_pnl=_decimal(row.get("realized_pnl")),
            unrealized_pnl=_decimal(row.get("unrealized_pnl")),
            fee=_decimal(row.get("fee")),
            fee_currency=row.get("fee_currency"),
            slippage=_decimal(row.get("slippage")),
            funding=_decimal(row.get("funding")),
            cash_flow=_decimal(row.get("cash_flow")),
            cl_ord_id=row.get("cl_ord_id"),
            exec_id=row.get("exec_id"),
            source=row.get("source") or "performance_service",
            quality=row.get("quality") or "complete",
            metadata=_metadata(row.get("metadata")),
        )

    def _row_to_cash_flow(self, row: dict[str, Any]) -> CashFlowEvent:
        return CashFlowEvent(
            cash_flow_id=row["cash_flow_id"],
            run_id=row["run_id"],
            deployment_id=row["deployment_id"],
            strategy_id=row["strategy_id"],
            account_id=row["account_id"],
            venue=row["venue"],
            amount=_decimal(row["amount"]),
            currency=row["currency"],
            flow_type=row["flow_type"],
            ts_ms=int(row["ts_ms"]),
            source=row.get("source") or "manual",
            metadata=_metadata(row.get("metadata")),
        )

    def _row_to_nav(self, row: dict[str, Any]) -> NAVPointV2:
        return NAVPointV2(
            nav_id=row["nav_id"],
            run_id=row["run_id"],
            deployment_id=row["deployment_id"],
            strategy_id=row["strategy_id"],
            account_id=row["account_id"],
            venue=row["venue"],
            timestamp_ms=int(row["timestamp_ms"]),
            equity=_decimal(row["equity"]),
            cash=_decimal(row["cash"]),
            position_value=_decimal(row["position_value"]),
            realized_pnl=_decimal(row["realized_pnl"]),
            unrealized_pnl=_decimal(row["unrealized_pnl"]),
            fee=_decimal(row["fee"]),
            slippage=_decimal(row["slippage"]),
            funding=_decimal(row["funding"]),
            cash_flow=_decimal(row["cash_flow"]),
            total_pnl=_decimal(row["total_pnl"]),
            net_return=_decimal(row["net_return"]),
            gross_return=_decimal(row["gross_return"]),
            twr=_decimal(row["twr"]),
            mwr=_decimal(row["mwr"]) if row.get("mwr") is not None else None,
            quality=row.get("quality") or "complete",
            source=row.get("source") or "performance_ledger",
            metadata=_metadata(row.get("metadata")),
        )

    def _row_to_account_nav(self, row: dict[str, Any]) -> AccountPortfolioNAV:
        return AccountPortfolioNAV(
            account_nav_id=row["account_nav_id"],
            account_id=row["account_id"],
            venue=row["venue"],
            timestamp_ms=int(row["timestamp_ms"]),
            currency=row["currency"],
            equity=_decimal(row["equity"]),
            cash=_decimal(row["cash"]),
            position_value=_decimal(row["position_value"]),
            liabilities=_decimal(row["liabilities"]),
            initial_capital=_decimal(row["initial_capital"]),
            cumulative_cash_flow=_decimal(row["cumulative_cash_flow"]),
            total_pnl=_decimal(row["total_pnl"]),
            run_equity=_decimal(row["run_equity"]),
            run_total_pnl=_decimal(row["run_total_pnl"]),
            unallocated_cash=_decimal(row["unallocated_cash"]),
            reconciliation_residual=_decimal(row["reconciliation_residual"]),
            quality=row.get("quality") or "complete",
            source=row.get("source") or "account_snapshot",
            metadata=_metadata(row.get("metadata")),
        )

    def _row_to_mark_snapshot(self, row: dict[str, Any]) -> MarkPriceSnapshot:
        return MarkPriceSnapshot(
            mark_id=row["mark_id"],
            run_id=row["run_id"],
            deployment_id=row["deployment_id"],
            strategy_id=row["strategy_id"],
            account_id=row["account_id"],
            venue=row["venue"],
            symbol=row["symbol"],
            timestamp_ms=int(row["timestamp_ms"]),
            mark_price=_decimal(row["mark_price"]),
            source=row.get("source") or "performance_service",
            quality=row.get("quality") or "complete",
            metadata=_metadata(row.get("metadata")),
        )

    def _row_to_snapshot(self, row: dict[str, Any]) -> PerformanceSnapshot:
        return PerformanceSnapshot(
            snapshot_id=row["snapshot_id"],
            run_id=row["run_id"],
            deployment_id=row["deployment_id"],
            strategy_id=row["strategy_id"],
            account_id=row["account_id"],
            venue=row["venue"],
            timestamp_ms=int(row["timestamp_ms"]),
            equity=_decimal(row["equity"]),
            total_pnl=_decimal(row["total_pnl"]),
            net_return=_decimal(row["net_return"]),
            twr=_decimal(row["twr"]),
            mwr=_decimal(row["mwr"]) if row.get("mwr") is not None else None,
            max_drawdown=_decimal(row["max_drawdown"]),
            sharpe=_decimal(row["sharpe"]) if row.get("sharpe") is not None else None,
            sortino=_decimal(row["sortino"]) if row.get("sortino") is not None else None,
            calmar=_decimal(row["calmar"]) if row.get("calmar") is not None else None,
            turnover=_decimal(row["turnover"]),
            exposure=_decimal(row["exposure"]),
            fees=_decimal(row["fees"]),
            slippage=_decimal(row["slippage"]),
            funding=_decimal(row["funding"]),
            realized_pnl=_decimal(row["realized_pnl"]),
            unrealized_pnl=_decimal(row["unrealized_pnl"]),
            win_rate=_decimal(row["win_rate"]) if row.get("win_rate") is not None else None,
            profit_factor=(
                _decimal(row["profit_factor"]) if row.get("profit_factor") is not None else None
            ),
            avg_win=_decimal(row["avg_win"]) if row.get("avg_win") is not None else None,
            avg_loss=_decimal(row["avg_loss"]) if row.get("avg_loss") is not None else None,
            payoff_ratio=(
                _decimal(row["payoff_ratio"]) if row.get("payoff_ratio") is not None else None
            ),
            var_95=_decimal(row["var_95"]) if row.get("var_95") is not None else None,
            cvar_95=_decimal(row["cvar_95"]) if row.get("cvar_95") is not None else None,
            quality=row.get("quality") or "complete",
            metadata=_metadata(row.get("metadata")),
        )

    def _row_to_asset_classification(self, row: dict[str, Any]) -> AssetClassification:
        return AssetClassification(
            classification_id=row["classification_id"],
            symbol=row["symbol"],
            asset_class=row["asset_class"],
            effective_from_ms=int(row["effective_from_ms"]),
            effective_to_ms=(
                int(row["effective_to_ms"]) if row.get("effective_to_ms") is not None else None
            ),
            source=row.get("source") or "manual",
            quality=row.get("quality") or "complete",
            metadata=_metadata(row.get("metadata")),
        )

    def _row_to_benchmark_holding(self, row: dict[str, Any]) -> BenchmarkHolding:
        return BenchmarkHolding(
            holding_id=row["holding_id"],
            benchmark_id=row["benchmark_id"],
            symbol=row["symbol"],
            period_start_ms=int(row["period_start_ms"]),
            period_end_ms=int(row["period_end_ms"]),
            weight=_decimal(row.get("weight")),
            period_return=_decimal(row.get("period_return")),
            source=row.get("source") or "manual",
            quality=row.get("quality") or "complete",
            metadata=_metadata(row.get("metadata")),
        )

    def _row_to_portfolio_holding_fact(self, row: dict[str, Any]) -> PortfolioHoldingFact:
        return PortfolioHoldingFact(
            holding_id=row["holding_id"],
            run_id=row["run_id"],
            symbol=row["symbol"],
            period_start_ms=int(row["period_start_ms"]),
            period_end_ms=int(row["period_end_ms"]),
            weight=_decimal(row.get("weight")),
            period_return=_decimal(row.get("period_return")),
            source=row.get("source") or "performance_service",
            quality=row.get("quality") or "complete",
            metadata=_metadata(row.get("metadata")),
        )

    def _row_to_factor_exposure(self, row: dict[str, Any]) -> FactorExposureFact:
        return FactorExposureFact(
            exposure_id=row["exposure_id"],
            run_id=row["run_id"],
            symbol=row["symbol"],
            factor_name=row["factor_name"],
            period_start_ms=int(row["period_start_ms"]),
            period_end_ms=int(row["period_end_ms"]),
            exposure=_decimal(row.get("exposure")),
            weight=_decimal(row.get("weight")),
            source=row.get("source") or "manual",
            quality=row.get("quality") or "complete",
            metadata=_metadata(row.get("metadata")),
        )

    def _row_to_factor_return(self, row: dict[str, Any]) -> FactorReturnFact:
        return FactorReturnFact(
            return_id=row["return_id"],
            factor_name=row["factor_name"],
            period_start_ms=int(row["period_start_ms"]),
            period_end_ms=int(row["period_end_ms"]),
            period_return=_decimal(row.get("period_return")),
            source=row.get("source") or "manual",
            quality=row.get("quality") or "complete",
            metadata=_metadata(row.get("metadata")),
        )

    def _row_to_risk_budget_trace(self, row: dict[str, Any]) -> RiskBudgetDecisionTrace:
        return RiskBudgetDecisionTrace(
            trace_id=row["trace_id"],
            run_id=row["run_id"],
            period_start_ms=int(row["period_start_ms"]),
            period_end_ms=int(row["period_end_ms"]),
            constraint_type=row["constraint_type"],
            requested_notional=_decimal(row.get("requested_notional")),
            allowed_notional=_decimal(row.get("allowed_notional")),
            final_notional=_decimal(row.get("final_notional")),
            decision=row["decision"],
            source=row.get("source") or "risk_sizing_engine",
            quality=row.get("quality") or "complete",
            metadata=_metadata(row.get("metadata")),
        )

    def _row_to_attribution(self, row: dict[str, Any]) -> AttributionResult:
        raw_items = row.get("items") or []
        if isinstance(raw_items, str):
            raw_items = json.loads(raw_items)
        items = [
            AttributionItem(
                key=item["key"],
                group_by=item["group_by"],
                pnl=_decimal(item.get("pnl")),
                gross_pnl=_decimal(item.get("gross_pnl")),
                realized_pnl=_decimal(item.get("realized_pnl")),
                unrealized_pnl=_decimal(item.get("unrealized_pnl")),
                fees=_decimal(item.get("fees")),
                slippage=_decimal(item.get("slippage")),
                funding=_decimal(item.get("funding")),
                contribution_return=_decimal(item.get("contribution_return")),
                contribution_percent=(
                    _decimal(item["contribution_percent"])
                    if item.get("contribution_percent") is not None
                    else None
                ),
                metadata=_metadata(item.get("metadata")),
            )
            for item in raw_items
        ]
        return AttributionResult(
            attribution_id=row["attribution_id"],
            scope=row["scope"],
            group_by=row["group_by"],
            timestamp_ms=int(row["timestamp_ms"]),
            account_id=row.get("account_id"),
            venue=row.get("venue"),
            run_id=row.get("run_id"),
            deployment_id=row.get("deployment_id"),
            strategy_id=row.get("strategy_id"),
            total_pnl=_decimal(row["total_pnl"]),
            explained_pnl=_decimal(row["explained_pnl"]),
            residual_pnl=_decimal(row["residual_pnl"]),
            items=items,
            quality=row.get("quality") or "complete",
            method=row.get("method") or "absolute_contribution_v1",
            metadata=_metadata(row.get("metadata")),
        )


_repository_instance: PerformanceRepository | None = None


def get_performance_repository() -> PerformanceRepository:
    global _repository_instance
    if _repository_instance is None:
        _repository_instance = PerformanceRepository()
    return _repository_instance


def reset_performance_repository() -> None:
    global _repository_instance
    if _repository_instance is None:
        return
    repo = _repository_instance
    if repo._postgres_storage is not None:
        try:
            asyncio.get_running_loop()
            repo._terminate_postgres_connection()
        except RuntimeError:
            try:
                asyncio.run(repo._reset_postgres_connection())
            except RuntimeError:
                repo._terminate_postgres_connection()
    _repository_instance = None
