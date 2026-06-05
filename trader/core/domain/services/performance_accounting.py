"""Pure performance accounting calculations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import ceil, sqrt
from statistics import mean, pstdev
from typing import Iterable

from trader.core.domain.models.performance import (
    AccountingLedgerEntry,
    AccountPortfolioNAV,
    AssetClassification,
    BenchmarkHolding,
    FactorExposureFact,
    FactorReturnFact,
    NAVPointV2,
    PortfolioHoldingFact,
    RiskBudgetDecisionTrace,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")


def to_decimal(value: object, default: Decimal = _ZERO) -> Decimal:
    """Convert loose service/API values into Decimal without raising."""

    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default


@dataclass(slots=True)
class PositionState:
    qty: Decimal = Decimal("0")
    avg_cost: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")


@dataclass(slots=True)
class LedgerState:
    cash: Decimal
    realized_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    funding: Decimal = Decimal("0")
    cash_flow: Decimal = Decimal("0")
    positions: dict[str, PositionState] | None = None

    def __post_init__(self) -> None:
        if self.positions is None:
            self.positions = {}


def apply_fill_to_state(
    state: LedgerState,
    *,
    symbol: str,
    side: str,
    qty: Decimal,
    price: Decimal,
    fee: Decimal = Decimal("0"),
) -> tuple[Decimal, Decimal]:
    """Apply a long-only spot fill and return gross and realized PnL.

    Short and derivative-aware handling can be layered later; v1 matches the
    current spot demo execution path.
    """

    normalized_side = side.upper()
    pos = state.positions.setdefault(symbol, PositionState())  # type: ignore[union-attr]
    notional = qty * price
    gross_pnl = Decimal("0")
    realized_pnl = Decimal("0")

    if normalized_side == "BUY":
        new_qty = pos.qty + qty
        if new_qty > 0:
            pos.avg_cost = ((pos.avg_cost * pos.qty) + notional) / new_qty
        pos.qty = new_qty
        state.cash -= notional + fee
    elif normalized_side == "SELL":
        matched_qty = min(qty, pos.qty)
        gross_pnl = (price - pos.avg_cost) * matched_qty
        realized_pnl = gross_pnl
        pos.qty -= matched_qty
        if pos.qty <= 0:
            pos.qty = Decimal("0")
            pos.avg_cost = Decimal("0")
        pos.realized_pnl += realized_pnl
        state.realized_pnl += realized_pnl
        state.cash += notional - fee
    else:
        state.cash -= fee

    pos.fees += fee
    state.fees += fee
    return gross_pnl, realized_pnl


def mark_unrealized_pnl(
    positions: dict[str, PositionState],
    mark_prices: dict[str, Decimal],
) -> tuple[Decimal, Decimal]:
    """Return total market value and unrealized PnL for open positions."""

    market_value = Decimal("0")
    unrealized = Decimal("0")
    for symbol, pos in positions.items():
        if pos.qty <= 0:
            continue
        mark = mark_prices.get(symbol, pos.avg_cost)
        market_value += pos.qty * mark
        unrealized += (mark - pos.avg_cost) * pos.qty
    return market_value, unrealized


def build_account_portfolio_nav(
    *,
    account_nav_id: str,
    account_id: str,
    venue: str,
    timestamp_ms: int,
    currency: str,
    equity: Decimal,
    cash: Decimal,
    position_value: Decimal,
    liabilities: Decimal,
    initial_capital: Decimal,
    cumulative_cash_flow: Decimal,
    latest_run_nav: Iterable[NAVPointV2],
    quality: str = "complete",
    source: str = "account_snapshot",
    metadata: dict[str, object] | None = None,
) -> AccountPortfolioNAV:
    """Build account NAV from a true account snapshot and reconcile run projections."""

    run_points = list(latest_run_nav)
    run_equity = sum((point.equity for point in run_points), _ZERO)
    run_cash = sum((point.cash for point in run_points), _ZERO)
    run_total_pnl = sum((point.total_pnl for point in run_points), _ZERO)
    total_pnl = equity - initial_capital - cumulative_cash_flow
    return AccountPortfolioNAV(
        account_nav_id=account_nav_id,
        account_id=account_id,
        venue=venue,
        timestamp_ms=timestamp_ms,
        currency=currency,
        equity=equity,
        cash=cash,
        position_value=position_value,
        liabilities=liabilities,
        initial_capital=initial_capital,
        cumulative_cash_flow=cumulative_cash_flow,
        total_pnl=total_pnl,
        run_equity=run_equity,
        run_total_pnl=run_total_pnl,
        unallocated_cash=cash - run_cash,
        reconciliation_residual=total_pnl - run_total_pnl,
        quality="partial" if initial_capital <= 0 else quality,
        source=source,
        metadata=dict(metadata or {}),
    )


def calculate_twr(nav_points: Iterable[NAVPointV2]) -> Decimal:
    """Calculate time-weighted return, neutralizing external cash flow."""

    ordered = sorted(nav_points, key=lambda p: p.timestamp_ms)
    if len(ordered) < 2:
        return Decimal("0")

    cumulative = Decimal("1")
    prev_equity = ordered[0].equity
    for point in ordered[1:]:
        if prev_equity == 0:
            prev_equity = point.equity
            continue
        period_return = (point.equity - point.cash_flow - prev_equity) / prev_equity
        cumulative *= Decimal("1") + period_return
        prev_equity = point.equity
    return cumulative - Decimal("1")


def calculate_max_drawdown(nav_points: Iterable[NAVPointV2]) -> Decimal:
    ordered = sorted(nav_points, key=lambda p: p.timestamp_ms)
    peak: Decimal | None = None
    max_dd = Decimal("0")
    for point in ordered:
        if peak is None or point.equity > peak:
            peak = point.equity
        if peak and peak > 0:
            dd = (point.equity - peak) / peak
            if dd < max_dd:
                max_dd = dd
    return max_dd


def calculate_period_returns(nav_points: Iterable[NAVPointV2]) -> list[Decimal]:
    ordered = sorted(nav_points, key=lambda p: p.timestamp_ms)
    returns: list[Decimal] = []
    if len(ordered) < 2:
        return returns
    prev_equity = ordered[0].equity
    for point in ordered[1:]:
        if prev_equity != 0:
            returns.append((point.equity - point.cash_flow - prev_equity) / prev_equity)
        prev_equity = point.equity
    return returns


def calculate_daily_returns(nav_points: Iterable[NAVPointV2]) -> list[Decimal]:
    """Return UTC day-end cash-flow-neutralized returns."""

    day_ms = 24 * 60 * 60 * 1000
    daily: dict[int, tuple[Decimal, Decimal]] = {}
    for point in sorted(nav_points, key=lambda p: p.timestamp_ms):
        day = point.timestamp_ms // day_ms
        _, cash_flow = daily.get(day, (point.equity, Decimal("0")))
        daily[day] = (point.equity, cash_flow + point.cash_flow)

    returns: list[Decimal] = []
    previous_equity: Decimal | None = None
    for equity, cash_flow in daily.values():
        if previous_equity is not None and previous_equity != 0:
            returns.append((equity - cash_flow - previous_equity) / previous_equity)
        previous_equity = equity
    return returns


def calculate_empyrical_compatible_metrics(
    period_returns: Iterable[Decimal],
    periods_per_year: int = 365,
) -> dict[str, Decimal | int | str | None]:
    """Return common Empyrical-style metrics from audited period returns."""

    returns = list(period_returns)
    sample_count = len(returns)
    cumulative = Decimal("1")
    for value in returns:
        cumulative *= Decimal("1") + value
    cumulative_return = cumulative - Decimal("1")

    values = [float(value) for value in returns]
    annual_return: Decimal | None = None
    annual_volatility: Decimal | None = None
    if sample_count > 0:
        annual_return = Decimal(str((float(cumulative) ** (periods_per_year / sample_count)) - 1.0))
    if sample_count > 1:
        annual_volatility = Decimal(str(pstdev(values) * sqrt(periods_per_year)))

    max_drawdown = _max_drawdown_from_returns(returns)
    var_95, cvar_95 = calculate_historical_var_cvar(returns)
    return {
        "engine": "internal_empyrical_compatible",
        "sample_count": sample_count,
        "periods_per_year": periods_per_year,
        "cumulative_return": cumulative_return,
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe_ratio": annualized_sharpe(returns, periods_per_year=periods_per_year),
        "sortino_ratio": annualized_sortino(returns, periods_per_year=periods_per_year),
        "max_drawdown": max_drawdown,
        "calmar_ratio": (
            annual_return / abs(max_drawdown)
            if annual_return is not None and max_drawdown < 0
            else None
        ),
        "var_95": var_95,
        "cvar_95": cvar_95,
    }


def _max_drawdown_from_returns(period_returns: Iterable[Decimal]) -> Decimal:
    equity = Decimal("1")
    peak = Decimal("1")
    max_drawdown = Decimal("0")
    for value in period_returns:
        equity *= Decimal("1") + value
        if equity > peak:
            peak = equity
        if peak > 0:
            drawdown = (equity - peak) / peak
            if drawdown < max_drawdown:
                max_drawdown = drawdown
    return max_drawdown


def calculate_brinson_attribution(
    *,
    portfolio_holdings: Iterable[PortfolioHoldingFact],
    benchmark_holdings: Iterable[BenchmarkHolding],
    classifications: Iterable[AssetClassification],
) -> dict[str, object]:
    """Calculate single-period Brinson attribution from explicit facts."""

    portfolio_holdings = list(portfolio_holdings)
    benchmark_holdings = list(benchmark_holdings)
    classifications = list(classifications)
    classification_by_symbol = {
        item.symbol: item.asset_class
        for item in sorted(classifications, key=lambda item: item.effective_from_ms)
    }
    portfolio_buckets = _aggregate_brinson_buckets(
        portfolio_holdings,
        classification_by_symbol=classification_by_symbol,
    )
    benchmark_buckets = _aggregate_brinson_buckets(
        benchmark_holdings,
        classification_by_symbol=classification_by_symbol,
    )
    keys = sorted(set(portfolio_buckets) | set(benchmark_buckets))
    items: list[dict[str, Decimal | str]] = []
    quality = "complete"
    for key in keys:
        portfolio = portfolio_buckets.get(key, {"weight": Decimal("0"), "return": Decimal("0")})
        benchmark = benchmark_buckets.get(key, {"weight": Decimal("0"), "return": Decimal("0")})
        if key == "unclassified":
            quality = "partial"
        allocation = (portfolio["weight"] - benchmark["weight"]) * benchmark["return"]
        selection = benchmark["weight"] * (portfolio["return"] - benchmark["return"])
        interaction = (portfolio["weight"] - benchmark["weight"]) * (
            portfolio["return"] - benchmark["return"]
        )
        active = allocation + selection + interaction
        items.append(
            {
                "asset_class": key,
                "portfolio_weight": portfolio["weight"],
                "benchmark_weight": benchmark["weight"],
                "portfolio_return": portfolio["return"],
                "benchmark_return": benchmark["return"],
                "allocation_effect": allocation,
                "selection_effect": selection,
                "interaction_effect": interaction,
                "active_return": active,
            }
        )

    total_active = sum((item["active_return"] for item in items), Decimal("0"))
    portfolio_return = sum(
        (holding.weight * holding.period_return for holding in portfolio_holdings),
        Decimal("0"),
    )
    benchmark_return = sum(
        (holding.weight * holding.period_return for holding in benchmark_holdings),
        Decimal("0"),
    )
    return {
        "method": "brinson_single_period_v2",
        "quality": quality,
        "items": items,
        "total_active_return": total_active,
        "portfolio_return": portfolio_return,
        "benchmark_return": benchmark_return,
        "allocation_effect": sum((item["allocation_effect"] for item in items), Decimal("0")),
        "selection_effect": sum((item["selection_effect"] for item in items), Decimal("0")),
        "interaction_effect": sum((item["interaction_effect"] for item in items), Decimal("0")),
    }


def calculate_brinson_carino_linking(
    period_results: Iterable[dict[str, object]],
) -> dict[str, object]:
    """Link single-period Brinson effects with the Carino multi-period method."""

    periods = list(period_results)
    if not periods:
        return {
            "method": "brinson_carino_linked_v2",
            "quality": "partial",
            "items": [],
            "total_active_return": Decimal("0"),
            "portfolio_total_return": Decimal("0"),
            "benchmark_total_return": Decimal("0"),
            "allocation_effect": Decimal("0"),
            "selection_effect": Decimal("0"),
            "interaction_effect": Decimal("0"),
        }

    portfolio_total = _compound_returns(
        [Decimal(str(period.get("portfolio_return", "0"))) for period in periods]
    )
    benchmark_total = _compound_returns(
        [Decimal(str(period.get("benchmark_return", "0"))) for period in periods]
    )
    total_active = portfolio_total - benchmark_total
    total_k = _carino_coefficient(portfolio_total, benchmark_total)
    if total_k == 0:
        total_k = Decimal("1")

    buckets: dict[str, dict[str, Decimal | int]] = {}
    quality = "complete"
    for period in periods:
        if period.get("quality") != "complete":
            quality = "partial"
        period_portfolio_return = Decimal(str(period.get("portfolio_return", "0")))
        period_benchmark_return = Decimal(str(period.get("benchmark_return", "0")))
        period_k = _carino_coefficient(period_portfolio_return, period_benchmark_return)
        link_weight = period_k / total_k
        for raw_item in period.get("items", []):
            item = dict(raw_item)  # type: ignore[arg-type]
            bucket = buckets.setdefault(
                str(item["asset_class"]),
                {
                    "linked_allocation_effect": Decimal("0"),
                    "linked_selection_effect": Decimal("0"),
                    "linked_interaction_effect": Decimal("0"),
                    "linked_active_return": Decimal("0"),
                    "period_count": 0,
                },
            )
            allocation = Decimal(str(item.get("allocation_effect", "0"))) * link_weight
            selection = Decimal(str(item.get("selection_effect", "0"))) * link_weight
            interaction = Decimal(str(item.get("interaction_effect", "0"))) * link_weight
            bucket["linked_allocation_effect"] += allocation  # type: ignore[operator]
            bucket["linked_selection_effect"] += selection  # type: ignore[operator]
            bucket["linked_interaction_effect"] += interaction  # type: ignore[operator]
            bucket["linked_active_return"] += allocation + selection + interaction  # type: ignore[operator]
            bucket["period_count"] += 1  # type: ignore[operator]

    items = [
        {
            "asset_class": asset_class,
            "linked_allocation_effect": bucket["linked_allocation_effect"],
            "linked_selection_effect": bucket["linked_selection_effect"],
            "linked_interaction_effect": bucket["linked_interaction_effect"],
            "linked_active_return": bucket["linked_active_return"],
            "period_count": bucket["period_count"],
        }
        for asset_class, bucket in sorted(buckets.items())
    ]
    return {
        "method": "brinson_carino_linked_v2",
        "quality": quality,
        "items": items,
        "total_active_return": total_active,
        "portfolio_total_return": portfolio_total,
        "benchmark_total_return": benchmark_total,
        "allocation_effect": sum(
            (Decimal(str(item["linked_allocation_effect"])) for item in items),
            Decimal("0"),
        ),
        "selection_effect": sum(
            (Decimal(str(item["linked_selection_effect"])) for item in items),
            Decimal("0"),
        ),
        "interaction_effect": sum(
            (Decimal(str(item["linked_interaction_effect"])) for item in items),
            Decimal("0"),
        ),
    }


def _compound_returns(period_returns: Iterable[Decimal]) -> Decimal:
    total = Decimal("1")
    for value in period_returns:
        total *= Decimal("1") + value
    return total - Decimal("1")


def _carino_coefficient(portfolio_return: Decimal, benchmark_return: Decimal) -> Decimal:
    if portfolio_return == benchmark_return:
        base = Decimal("1") + portfolio_return
        return Decimal("1") / base if base != 0 else Decimal("1")
    if portfolio_return <= Decimal("-1") or benchmark_return <= Decimal("-1"):
        return Decimal("1")
    return ((Decimal("1") + portfolio_return).ln() - (Decimal("1") + benchmark_return).ln()) / (
        portfolio_return - benchmark_return
    )


def _aggregate_brinson_buckets(
    holdings: Iterable[PortfolioHoldingFact] | Iterable[BenchmarkHolding],
    *,
    classification_by_symbol: dict[str, str],
) -> dict[str, dict[str, Decimal]]:
    buckets: dict[str, dict[str, Decimal]] = {}
    for holding in holdings:
        key = classification_by_symbol.get(holding.symbol, "unclassified")
        bucket = buckets.setdefault(
            key,
            {
                "weight": Decimal("0"),
                "weighted_return": Decimal("0"),
                "return": Decimal("0"),
            },
        )
        bucket["weight"] += holding.weight
        bucket["weighted_return"] += holding.weight * holding.period_return
    for bucket in buckets.values():
        bucket["return"] = _safe_div_decimal(bucket["weighted_return"], bucket["weight"])
    return buckets


def _safe_div_decimal(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return numerator / denominator


def calculate_factor_attribution(
    *,
    exposures: Iterable[FactorExposureFact],
    factor_returns: Iterable[FactorReturnFact],
) -> dict[str, object]:
    """Calculate factor contribution from explicit exposure and return facts."""

    returns_by_factor = {item.factor_name: item.period_return for item in factor_returns}
    buckets: dict[str, dict[str, Decimal]] = {}
    quality = "complete"
    missing_returns: set[str] = set()
    for exposure in exposures:
        bucket = buckets.setdefault(
            exposure.factor_name,
            {
                "weighted_exposure": Decimal("0"),
                "contribution_return": Decimal("0"),
                "weight": Decimal("0"),
                "sample_count": Decimal("0"),
            },
        )
        bucket["weighted_exposure"] += exposure.weight * exposure.exposure
        bucket["weight"] += exposure.weight
        bucket["sample_count"] += Decimal("1")
        factor_return = returns_by_factor.get(exposure.factor_name)
        if factor_return is None:
            quality = "partial"
            missing_returns.add(exposure.factor_name)
            continue
        bucket["contribution_return"] += exposure.weight * exposure.exposure * factor_return

    items = [
        {
            "factor_name": factor_name,
            "weighted_exposure": bucket["weighted_exposure"],
            "factor_return": returns_by_factor.get(factor_name),
            "contribution_return": bucket["contribution_return"],
            "weight": bucket["weight"],
            "sample_count": bucket["sample_count"],
        }
        for factor_name, bucket in sorted(buckets.items())
    ]
    total = sum((item["contribution_return"] for item in items), Decimal("0"))
    return {
        "method": "factor_return_attribution_v3",
        "quality": quality,
        "items": items,
        "total_factor_return": total,
        "missing_factor_returns": sorted(missing_returns),
    }


def calculate_risk_budget_attribution(
    traces: Iterable[RiskBudgetDecisionTrace],
) -> dict[str, object]:
    """Aggregate risk-budget decision traces by limiting constraint."""

    buckets: dict[str, dict[str, Decimal]] = {}
    for trace in traces:
        bucket = buckets.setdefault(
            trace.constraint_type,
            {
                "requested_notional": Decimal("0"),
                "allowed_notional": Decimal("0"),
                "final_notional": Decimal("0"),
                "avoided_notional": Decimal("0"),
                "decision_count": Decimal("0"),
            },
        )
        bucket["requested_notional"] += trace.requested_notional
        bucket["allowed_notional"] += trace.allowed_notional
        bucket["final_notional"] += trace.final_notional
        bucket["avoided_notional"] += max(
            trace.requested_notional - trace.final_notional,
            Decimal("0"),
        )
        bucket["decision_count"] += Decimal("1")

    total_avoided = sum((bucket["avoided_notional"] for bucket in buckets.values()), Decimal("0"))
    items = [
        {
            "constraint_type": constraint_type,
            "requested_notional": bucket["requested_notional"],
            "allowed_notional": bucket["allowed_notional"],
            "final_notional": bucket["final_notional"],
            "avoided_notional": bucket["avoided_notional"],
            "decision_count": bucket["decision_count"],
            "contribution_percent": (
                _safe_div_decimal(
                    bucket["avoided_notional"],
                    total_avoided,
                )
                if total_avoided
                else None
            ),
        }
        for constraint_type, bucket in sorted(buckets.items())
    ]
    return {
        "method": "risk_budget_trace_attribution_v3",
        "quality": "complete" if items else "partial",
        "items": items,
        "total_avoided_notional": total_avoided,
    }


def annualized_sharpe(
    period_returns: Iterable[Decimal], periods_per_year: int = 365
) -> Decimal | None:
    values = [float(v) for v in period_returns]
    if len(values) < 2:
        return None
    vol = pstdev(values)
    if vol == 0:
        return None
    return Decimal(str((mean(values) / vol) * sqrt(periods_per_year)))


def annualized_sortino(
    period_returns: Iterable[Decimal], periods_per_year: int = 365
) -> Decimal | None:
    values = [float(v) for v in period_returns]
    downside = [v for v in values if v < 0]
    if len(values) < 2 or not downside:
        return None
    downside_vol = pstdev(downside)
    if downside_vol == 0:
        return None
    return Decimal(str((mean(values) / downside_vol) * sqrt(periods_per_year)))


def calculate_turnover(
    ledger_entries: Iterable[AccountingLedgerEntry],
    nav_points: Iterable[NAVPointV2],
) -> Decimal:
    """Return absolute fill notional divided by average positive equity."""

    total_notional = sum(
        (abs(entry.trade_notional) for entry in ledger_entries if entry.event_type == "FILL"),
        Decimal("0"),
    )
    equities = [point.equity for point in nav_points if point.equity > 0]
    if not equities:
        return Decimal("0")
    average_equity = sum(equities, Decimal("0")) / Decimal(len(equities))
    return total_notional / average_equity if average_equity else Decimal("0")


def calculate_trade_statistics(
    ledger_entries: Iterable[AccountingLedgerEntry],
) -> dict[str, Decimal | None]:
    """Calculate statistics from realized PnL-bearing ledger rows."""

    realized = [
        entry.realized_pnl
        for entry in ledger_entries
        if entry.event_type == "FILL" and entry.realized_pnl != 0
    ]
    wins = [value for value in realized if value > 0]
    losses = [abs(value) for value in realized if value < 0]
    trade_count = len(realized)
    avg_win = sum(wins, Decimal("0")) / Decimal(len(wins)) if wins else None
    avg_loss = sum(losses, Decimal("0")) / Decimal(len(losses)) if losses else None
    return {
        "win_rate": Decimal(len(wins)) / Decimal(trade_count) if trade_count else None,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": avg_win / avg_loss if avg_win is not None and avg_loss else None,
        "profit_factor": (
            sum(wins, Decimal("0")) / sum(losses, Decimal("0")) if wins and losses else None
        ),
    }


def calculate_historical_var_cvar(
    period_returns: Iterable[Decimal],
    confidence: Decimal = Decimal("0.95"),
) -> tuple[Decimal | None, Decimal | None]:
    """Return positive historical loss VaR and CVaR."""

    losses = sorted((-value for value in period_returns))
    if len(losses) < 2:
        return None, None
    index = max(0, min(len(losses) - 1, ceil(float(confidence) * len(losses)) - 1))
    var = max(losses[index], Decimal("0"))
    tail = [loss for loss in losses if loss >= var]
    cvar = sum(tail, Decimal("0")) / Decimal(len(tail)) if tail else var
    return var, cvar


def simple_mwr(
    initial_capital: Decimal,
    nav_points: Iterable[NAVPointV2],
    cash_flows: Iterable[tuple[int, Decimal]],
) -> Decimal | None:
    """Approximate money-weighted return with Newton XIRR.

    Deposits are negative investor cash flows; withdrawals are positive. This
    lightweight implementation avoids a runtime dependency for the core path.
    """

    ordered_nav = sorted(nav_points, key=lambda p: p.timestamp_ms)
    if not ordered_nav:
        return None
    start_ts = ordered_nav[0].timestamp_ms
    end = ordered_nav[-1]
    flows = [(start_ts, -initial_capital)]
    flows.extend((ts, -amount) for ts, amount in cash_flows)
    flows.append((end.timestamp_ms, end.equity))

    def npv(rate: float) -> float:
        total = 0.0
        for ts, amount in flows:
            years = max(0.0, (ts - start_ts) / (365.0 * 24 * 60 * 60 * 1000))
            total += float(amount) / ((1.0 + rate) ** years)
        return total

    rate = 0.0
    for _ in range(50):
        value = npv(rate)
        bump = 1e-6
        derivative = (npv(rate + bump) - value) / bump
        if derivative == 0:
            return None
        next_rate = rate - value / derivative
        if next_rate <= -0.999999:
            return None
        if abs(next_rate - rate) < 1e-8:
            return Decimal(str(next_rate))
        rate = next_rate
    return Decimal(str(rate))


def explain_pnl(
    ledger_entries: Iterable[AccountingLedgerEntry],
    latest_nav: NAVPointV2 | None,
) -> dict[str, Decimal]:
    entries = list(ledger_entries)
    realized = sum((entry.realized_pnl for entry in entries), Decimal("0"))
    fees = sum((entry.fee for entry in entries), Decimal("0"))
    slippage = sum((entry.slippage for entry in entries), Decimal("0"))
    funding = sum((entry.funding for entry in entries), Decimal("0"))
    cash_flow = sum((entry.cash_flow for entry in entries), Decimal("0"))
    gross = sum((entry.gross_pnl for entry in entries), Decimal("0"))
    unrealized = latest_nav.unrealized_pnl if latest_nav else Decimal("0")
    total = latest_nav.total_pnl if latest_nav else realized + unrealized
    explained = realized + unrealized + funding - fees - slippage
    return {
        "gross_pnl": gross,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "fees": fees,
        "slippage": slippage,
        "funding": funding,
        "cash_flow": cash_flow,
        "total_pnl": total,
        "unattributed_pnl": total - explained,
    }
