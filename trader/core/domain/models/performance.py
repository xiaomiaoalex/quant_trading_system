"""Performance accounting domain models.

These models are pure data contracts. They do not perform IO and are shared by
the performance service, persistence layer, and API mapping code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class StrategyRun:
    """One strategy runtime instance used as the performance accounting unit."""

    run_id: str
    deployment_id: str
    strategy_id: str
    account_id: str
    venue: str
    mode: str = "paper"
    code_version: str | None = None
    params_hash: str | None = None
    initial_capital: Decimal = Decimal("0")
    currency: str = "USDT"
    status: str = "RUNNING"
    started_at_ms: int = 0
    stopped_at_ms: int | None = None
    start_reason: str | None = None
    stop_reason: str | None = None
    quality: str = "complete"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AccountingLedgerEntry:
    """Append-only performance ledger row.

    A fill is keyed by ``cl_ord_id + exec_id`` so repeated WS/REST messages do
    not create duplicate PnL. Non-fill rows use their own idempotency key.
    """

    ledger_entry_id: str
    idempotency_key: str
    run_id: str
    deployment_id: str
    strategy_id: str
    account_id: str
    venue: str
    event_type: str
    ts_ms: int
    symbol: str | None = None
    side: str | None = None
    qty: Decimal = Decimal("0")
    price: Decimal = Decimal("0")
    trade_notional: Decimal = Decimal("0")
    gross_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")
    fee_currency: str | None = None
    slippage: Decimal = Decimal("0")
    funding: Decimal = Decimal("0")
    cash_flow: Decimal = Decimal("0")
    cl_ord_id: str | None = None
    exec_id: str | None = None
    source: str = "performance_service"
    quality: str = "complete"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CashFlowEvent:
    """External capital movement attached to a strategy run."""

    cash_flow_id: str
    run_id: str
    deployment_id: str
    strategy_id: str
    account_id: str
    venue: str
    amount: Decimal
    currency: str
    flow_type: str
    ts_ms: int
    source: str = "manual"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NAVPointV2:
    """Audit-grade NAV snapshot derived from the performance ledger."""

    nav_id: str
    run_id: str
    deployment_id: str
    strategy_id: str
    account_id: str
    venue: str
    timestamp_ms: int
    equity: Decimal
    cash: Decimal
    position_value: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    fee: Decimal
    slippage: Decimal
    funding: Decimal
    cash_flow: Decimal
    total_pnl: Decimal
    net_return: Decimal
    gross_return: Decimal
    twr: Decimal
    mwr: Decimal | None = None
    quality: str = "complete"
    source: str = "performance_ledger"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AccountPortfolioNAV:
    """True account-level NAV plus reconciliation to strategy run projections."""

    account_nav_id: str
    account_id: str
    venue: str
    timestamp_ms: int
    currency: str
    equity: Decimal
    cash: Decimal
    position_value: Decimal
    liabilities: Decimal
    initial_capital: Decimal
    cumulative_cash_flow: Decimal
    total_pnl: Decimal
    run_equity: Decimal
    run_total_pnl: Decimal
    unallocated_cash: Decimal
    reconciliation_residual: Decimal
    quality: str = "complete"
    source: str = "account_snapshot"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MarkPriceSnapshot:
    """Canonical run-level mark price used for mark-to-market NAV."""

    mark_id: str
    run_id: str
    deployment_id: str
    strategy_id: str
    account_id: str
    venue: str
    symbol: str
    timestamp_ms: int
    mark_price: Decimal
    source: str = "performance_service"
    quality: str = "complete"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PerformanceSnapshot:
    """Latest performance metrics for a run."""

    snapshot_id: str
    run_id: str
    deployment_id: str
    strategy_id: str
    account_id: str
    venue: str
    timestamp_ms: int
    equity: Decimal
    total_pnl: Decimal
    net_return: Decimal
    twr: Decimal
    mwr: Decimal | None
    max_drawdown: Decimal
    sharpe: Decimal | None
    sortino: Decimal | None
    calmar: Decimal | None
    turnover: Decimal
    exposure: Decimal
    fees: Decimal
    slippage: Decimal
    funding: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    win_rate: Decimal | None
    profit_factor: Decimal | None
    avg_win: Decimal | None = None
    avg_loss: Decimal | None = None
    payoff_ratio: Decimal | None = None
    var_95: Decimal | None = None
    cvar_95: Decimal | None = None
    quality: str = "complete"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AttributionItem:
    """One contribution bucket in an attribution result."""

    key: str
    group_by: str
    pnl: Decimal
    gross_pnl: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    fees: Decimal
    slippage: Decimal
    funding: Decimal
    contribution_return: Decimal
    contribution_percent: Decimal | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AttributionResult:
    """Portfolio attribution output for an account/run slice."""

    attribution_id: str
    scope: str
    group_by: str
    timestamp_ms: int
    account_id: str | None
    venue: str | None
    run_id: str | None
    deployment_id: str | None
    strategy_id: str | None
    total_pnl: Decimal
    explained_pnl: Decimal
    residual_pnl: Decimal
    items: list[AttributionItem] = field(default_factory=list)
    quality: str = "complete"
    method: str = "absolute_contribution_v1"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["items"] = [item.to_dict() for item in self.items]
        return data


@dataclass(slots=True)
class AssetClassification:
    """Versioned symbol classification used by benchmark-relative attribution."""

    classification_id: str
    symbol: str
    asset_class: str
    effective_from_ms: int
    effective_to_ms: int | None = None
    source: str = "manual"
    quality: str = "complete"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BenchmarkHolding:
    """Benchmark symbol weight and return for one attribution period."""

    holding_id: str
    benchmark_id: str
    symbol: str
    period_start_ms: int
    period_end_ms: int
    weight: Decimal
    period_return: Decimal
    source: str = "manual"
    quality: str = "complete"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PortfolioHoldingFact:
    """Portfolio symbol weight and return for one attribution period."""

    holding_id: str
    run_id: str
    symbol: str
    period_start_ms: int
    period_end_ms: int
    weight: Decimal
    period_return: Decimal
    source: str = "performance_service"
    quality: str = "complete"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FactorExposureFact:
    """Versioned factor exposure for one run/symbol attribution period."""

    exposure_id: str
    run_id: str
    symbol: str
    factor_name: str
    period_start_ms: int
    period_end_ms: int
    exposure: Decimal
    weight: Decimal
    source: str = "manual"
    quality: str = "complete"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FactorReturnFact:
    """Factor return for one attribution period."""

    return_id: str
    factor_name: str
    period_start_ms: int
    period_end_ms: int
    period_return: Decimal
    source: str = "manual"
    quality: str = "complete"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RiskBudgetDecisionTrace:
    """Risk budget decision fact used for risk-budget attribution."""

    trace_id: str
    run_id: str
    period_start_ms: int
    period_end_ms: int
    constraint_type: str
    requested_notional: Decimal
    allowed_notional: Decimal
    final_notional: Decimal
    decision: str
    source: str = "risk_sizing_engine"
    quality: str = "complete"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
