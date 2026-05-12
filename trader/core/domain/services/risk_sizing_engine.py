from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trader.core.domain.models.crypto_risk import CryptoRiskSnapshot
from trader.core.domain.models.order import OrderSide
from trader.core.domain.models.risk_decision import (
    ConstraintResult,
    RiskSizingDecision,
    RiskSizingDecisionType,
)
from trader.core.domain.models.signal import Signal, SignalType
from trader.core.domain.services.exchange_rule_guard import ExchangeRuleGuard
from trader.core.domain.services.open_order_exposure import OpenOrderExposureCalculator
from trader.core.domain.services.portfolio_exposure_aggregator import PortfolioExposureAggregator


def _safe_divide(
    numerator: Decimal, denominator: Decimal, default: Decimal = Decimal("0")
) -> Decimal:
    if denominator <= 0:
        return default
    return numerator / denominator


@dataclass(frozen=True, slots=True)
class SymbolExposureInput:
    current_qty: Decimal
    current_notional: Decimal
    pending_open_notional: Decimal
    total_risk_notional: Decimal


@dataclass(frozen=True, slots=True)
class ClusterExposureInput:
    cluster: str
    current_notional: Decimal
    pending_open_notional: Decimal
    total_risk_notional: Decimal


@dataclass(frozen=True, slots=True)
class ConstraintInput:
    symbol: str
    side: OrderSide
    qty: Decimal
    normalized_qty: Decimal
    normalized_price: Decimal
    mark_price: Decimal
    reduce_only: bool


class RiskSizingEngine:
    def __init__(self) -> None:
        self._exchange_guard = ExchangeRuleGuard()
        self._exposure_calc = OpenOrderExposureCalculator()
        self._portfolio_aggregator = PortfolioExposureAggregator()

    def calculate(
        self,
        signal: Signal,
        snapshot: CryptoRiskSnapshot,
        trace_id: str = "",
    ) -> RiskSizingDecision:
        constraints: list[ConstraintResult] = []
        symbol = signal.symbol
        mark_price = snapshot.mark_prices.get(symbol)
        price = mark_price if mark_price is not None and mark_price > 0 else Decimal("0")

        if price <= 0:
            return self._reject_all(
                signal.quantity,
                Decimal("0"),
                "NO_MARK_PRICE",
                constraints,
                trace_id,
            )

        side = self._resolve_side(signal)
        reduce_only = signal.is_close_signal()

        spec = snapshot.instrument_specs.get(symbol)
        if spec is None:
            return self._reject_all(
                signal.quantity,
                Decimal("0"),
                "EXCHANGE_RULE_INVALID_INSTRUMENT_SPEC",
                constraints,
                trace_id,
            )

        exchange_result = self._exchange_guard.check_order(
            spec=spec,
            side=side,
            qty=signal.quantity,
            price=price,
        )

        exchange_cap_result = self._calc_exchange_cap_constraint(
            symbol=symbol,
            spec=spec,
            price=price,
            requested_qty=signal.quantity,
        )
        constraints.append(exchange_cap_result)

        normalized_qty = exchange_result.normalized_qty
        normalized_price = exchange_result.normalized_price

        if not exchange_result.ok:
            if normalized_qty > 0 and exchange_cap_result.max_qty > 0:
                return self._build_rejection(
                    signal.quantity,
                    normalized_qty,
                    exchange_cap_result,
                    tuple(constraints),
                    f"EXCHANGE_RULE_{exchange_result.rejection_reason}",
                    trace_id,
                )
            return self._reject_all(
                signal.quantity,
                normalized_qty,
                f"EXCHANGE_RULE_{exchange_result.rejection_reason}",
                constraints,
                trace_id,
            )

        if normalized_qty <= 0:
            return self._reject_all(
                signal.quantity,
                normalized_qty,
                "INVALID_NORMALIZED_QTY",
                constraints,
                trace_id,
            )

        proposed_order_notional = normalized_qty * price
        proposed_symbol_exposure = self._calc_symbol_exposure(
            symbol=symbol,
            snapshot=snapshot,
            proposed_notional=proposed_order_notional,
            reduce_only=reduce_only,
        )

        proposed_order = ConstraintInput(
            symbol=symbol,
            side=side,
            qty=signal.quantity,
            normalized_qty=normalized_qty,
            normalized_price=normalized_price,
            mark_price=price,
            reduce_only=reduce_only,
        )

        symbol_cap_result = self._calc_symbol_cap_constraint(
            symbol=symbol,
            cap=snapshot.risk_budget.symbol_notional_caps.get(symbol),
            proposed_exposure=proposed_symbol_exposure,
            proposed_notional=proposed_order_notional,
            price=price,
        )
        constraints.append(symbol_cap_result)

        total_cap_result = self._calc_total_cap_constraint(
            snapshot=snapshot,
            proposed_notional=proposed_order_notional,
            price=price,
        )
        constraints.append(total_cap_result)

        cluster_cap_result = self._calc_cluster_cap_constraint(
            signal=signal,
            snapshot=snapshot,
            proposed_order=proposed_order,
            proposed_notional=proposed_order_notional,
            price=price,
        )
        if cluster_cap_result is not None:
            constraints.append(cluster_cap_result)

        margin_cap_result = self._calc_margin_cap_constraint(
            signal=signal,
            snapshot=snapshot,
            proposed_order=proposed_order,
        )
        if margin_cap_result is not None:
            constraints.append(margin_cap_result)

        limiting_result = min(
            (c for c in constraints if c.constraint_type != "exchange_rule"),
            key=lambda c: c.max_qty,
            default=None,
        )

        if limiting_result is None or limiting_result.max_qty <= 0:
            return self._build_rejection(
                signal.quantity,
                normalized_qty,
                limiting_result,
                tuple(constraints),
                "ALL_CONSTRAINTS_BLOCKED",
                trace_id,
            )

        if limiting_result.max_qty >= normalized_qty:
            return RiskSizingDecision(
                requested_qty=signal.quantity,
                normalized_qty=normalized_qty,
                max_allowed_qty=normalized_qty,
                final_qty=normalized_qty,
                decision=RiskSizingDecisionType.APPROVE,
                reason="APPROVED",
                limiting_factor=limiting_result.constraint_type,
                constraints=tuple(constraints),
                trace_id=trace_id,
            )

        return self._build_rejection(
            signal.quantity,
            normalized_qty,
            limiting_result,
            tuple(constraints),
            limiting_result.constraint_type.upper(),
            trace_id,
        )

    def _resolve_side(self, signal: Signal) -> OrderSide:
        if signal.signal_type in {SignalType.BUY, SignalType.LONG, SignalType.CLOSE_SHORT}:
            return OrderSide.BUY
        return OrderSide.SELL

    def _calc_symbol_exposure(
        self,
        symbol: str,
        snapshot: CryptoRiskSnapshot,
        proposed_notional: Decimal,
        reduce_only: bool,
    ) -> SymbolExposureInput:
        mark_price = snapshot.mark_prices.get(symbol, Decimal("0"))
        current_qty = sum(
            (p.qty for p in snapshot.positions if p.symbol == symbol),
            Decimal("0"),
        )
        current_notional = abs(current_qty) * mark_price

        pending_open_notional = Decimal("0")
        for order in snapshot.open_orders:
            if order.symbol != symbol:
                continue
            if order.status not in {"OPEN", "NEW", "PENDING", "SUBMITTED", "PARTIALLY_FILLED"}:
                continue
            if order.reduce_only:
                continue
            pending_open_notional += order.notional

        if reduce_only:
            total_risk_notional = current_notional + pending_open_notional
        else:
            total_risk_notional = current_notional + pending_open_notional + proposed_notional

        return SymbolExposureInput(
            current_qty=current_qty,
            current_notional=current_notional,
            pending_open_notional=pending_open_notional,
            total_risk_notional=total_risk_notional,
        )

    def _calc_symbol_cap_constraint(
        self,
        symbol: str,
        cap: Decimal | None,
        proposed_exposure: SymbolExposureInput,
        proposed_notional: Decimal,
        price: Decimal,
    ) -> ConstraintResult:
        if cap is None or cap <= 0:
            max_qty = Decimal("999999999999")
            return ConstraintResult(
                constraint_type="symbol_cap",
                max_qty=max_qty,
                current_value=proposed_exposure.total_risk_notional - proposed_notional,
                limit_value=cap or Decimal("0"),
                passed=True,
            )

        total_after_proposed = proposed_exposure.total_risk_notional
        max_qty = _safe_divide(cap, price)
        if proposed_exposure.current_notional > Decimal(
            "0"
        ) or proposed_exposure.pending_open_notional > Decimal("0"):
            current_exposure = (
                proposed_exposure.current_notional + proposed_exposure.pending_open_notional
            )
            available = max(Decimal("0"), cap - current_exposure)
            max_qty = _safe_divide(available, price)
        passed = total_after_proposed <= cap

        return ConstraintResult(
            constraint_type="symbol_cap",
            max_qty=max_qty,
            current_value=proposed_exposure.total_risk_notional - proposed_notional,
            limit_value=cap,
            passed=passed,
        )

    def _calc_total_cap_constraint(
        self,
        snapshot: CryptoRiskSnapshot,
        proposed_notional: Decimal,
        price: Decimal,
    ) -> ConstraintResult:
        cap = snapshot.risk_budget.total_notional_cap
        if cap <= 0:
            max_qty = Decimal("999999999999")
            current_total = self._exposure_calc.calculate_total_risk_notional(
                positions=snapshot.positions,
                open_orders=snapshot.open_orders,
                mark_prices=snapshot.mark_prices,
            )
            return ConstraintResult(
                constraint_type="total_cap",
                max_qty=max_qty,
                current_value=current_total,
                limit_value=cap,
                passed=True,
            )

        current_total = self._exposure_calc.calculate_total_risk_notional(
            positions=snapshot.positions,
            open_orders=snapshot.open_orders,
            mark_prices=snapshot.mark_prices,
        )
        available_notional = max(Decimal("0"), cap - current_total)
        max_qty = _safe_divide(available_notional, price)
        passed = available_notional >= proposed_notional

        return ConstraintResult(
            constraint_type="total_cap",
            max_qty=max_qty,
            current_value=current_total,
            limit_value=cap,
            passed=passed,
        )

    def _calc_cluster_cap_constraint(
        self,
        signal: Signal,
        snapshot: CryptoRiskSnapshot,
        proposed_order: ConstraintInput,
        proposed_notional: Decimal,
        price: Decimal,
    ) -> ConstraintResult | None:
        cluster_caps = snapshot.risk_budget.cluster_notional_caps
        if not cluster_caps:
            return None

        if proposed_order.reduce_only:
            return ConstraintResult(
                constraint_type="cluster_cap",
                max_qty=Decimal("999999999999"),
                current_value=Decimal("0"),
                limit_value=Decimal("0"),
                passed=True,
            )

        symbol_clusters = snapshot.risk_budget.symbol_clusters
        if signal.symbol not in symbol_clusters:
            return ConstraintResult(
                constraint_type="cluster_cap",
                max_qty=Decimal("0"),
                current_value=Decimal("0"),
                limit_value=Decimal("0"),
                passed=False,
            )

        cluster = symbol_clusters[signal.symbol]
        cap = cluster_caps.get(cluster)
        if cap is None or cap <= 0:
            return ConstraintResult(
                constraint_type="cluster_cap",
                max_qty=Decimal("999999999999"),
                current_value=Decimal("0"),
                limit_value=Decimal("0"),
                passed=True,
            )

        cluster_exposures = self._portfolio_aggregator.calculate_cluster_exposures(
            positions=snapshot.positions,
            open_orders=snapshot.open_orders,
            mark_prices=snapshot.mark_prices,
            symbol_clusters=symbol_clusters,
        )

        cluster_exposure = cluster_exposures.get(cluster)
        if cluster_exposure is None:
            total_after_proposed = proposed_notional
            max_qty = _safe_divide(cap, price)
            passed = proposed_notional <= cap
            return ConstraintResult(
                constraint_type="cluster_cap",
                max_qty=max_qty,
                current_value=Decimal("0"),
                limit_value=cap,
                passed=passed,
            )

        total_after_proposed = cluster_exposure.total_risk_notional + proposed_notional
        current_exposure = cluster_exposure.total_risk_notional
        available = max(Decimal("0"), cap - current_exposure)
        max_qty = _safe_divide(available, price)
        passed = total_after_proposed <= cap

        return ConstraintResult(
            constraint_type="cluster_cap",
            max_qty=max_qty,
            current_value=current_exposure,
            limit_value=cap,
            passed=passed,
        )

    def _calc_margin_cap_constraint(
        self,
        signal: Signal,
        snapshot: CryptoRiskSnapshot,
        proposed_order: ConstraintInput,
    ) -> ConstraintResult | None:
        max_margin_ratio = snapshot.risk_budget.max_margin_ratio
        if max_margin_ratio <= 0:
            return None

        if proposed_order.reduce_only:
            return ConstraintResult(
                constraint_type="margin_limit",
                max_qty=Decimal("999999999999"),
                current_value=Decimal("0"),
                limit_value=max_margin_ratio,
                passed=True,
            )

        available_balance = snapshot.account.available_balance
        if available_balance <= 0:
            return ConstraintResult(
                constraint_type="margin_limit",
                max_qty=Decimal("0"),
                current_value=Decimal("0"),
                limit_value=max_margin_ratio,
                passed=False,
            )

        max_maintenance_margin = available_balance * max_margin_ratio
        max_notional_for_margin = max_maintenance_margin * Decimal("100")

        return ConstraintResult(
            constraint_type="margin_limit",
            max_qty=_safe_divide(max_notional_for_margin, proposed_order.mark_price),
            current_value=Decimal("0"),
            limit_value=max_margin_ratio,
            passed=True,
        )

    def _calc_exchange_cap_constraint(
        self,
        symbol: str,
        spec,
        price: Decimal,
        requested_qty: Decimal = Decimal("0"),
    ) -> ConstraintResult:
        if spec is None:
            return ConstraintResult(
                constraint_type="exchange_rule",
                max_qty=Decimal("0"),
                current_value=Decimal("0"),
                limit_value=Decimal("0"),
                passed=False,
            )

        max_qty = spec.max_qty if spec.max_qty is not None else Decimal("999999999999")
        passed = requested_qty <= max_qty
        return ConstraintResult(
            constraint_type="exchange_rule",
            max_qty=max_qty,
            current_value=requested_qty,
            limit_value=max_qty,
            passed=passed,
        )

    def _reject_all(
        self,
        requested_qty: Decimal,
        normalized_qty: Decimal,
        reason: str,
        constraints: list[ConstraintResult],
        trace_id: str,
    ) -> RiskSizingDecision:
        return RiskSizingDecision(
            requested_qty=requested_qty,
            normalized_qty=normalized_qty,
            max_allowed_qty=Decimal("0"),
            final_qty=Decimal("0"),
            decision=RiskSizingDecisionType.REJECT,
            reason=reason,
            limiting_factor=None,
            constraints=tuple(constraints),
            trace_id=trace_id,
        )

    def _build_rejection(
        self,
        requested_qty: Decimal,
        normalized_qty: Decimal,
        limiting: ConstraintResult | None,
        constraints: tuple[ConstraintResult, ...],
        reason: str,
        trace_id: str,
    ) -> RiskSizingDecision:
        decision = (
            RiskSizingDecisionType.CLIP
            if limiting and limiting.max_qty > 0
            else RiskSizingDecisionType.REJECT
        )
        max_allowed = limiting.max_qty if limiting else Decimal("0")
        limiting_factor = limiting.constraint_type if limiting else None

        return RiskSizingDecision(
            requested_qty=requested_qty,
            normalized_qty=normalized_qty,
            max_allowed_qty=max_allowed,
            final_qty=max_allowed,
            decision=decision,
            reason=reason,
            limiting_factor=limiting_factor,
            constraints=constraints,
            trace_id=trace_id,
        )
