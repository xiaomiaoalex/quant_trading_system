from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from trader.core.application.risk_engine import (
    RejectionReason,
    RiskCheckResult,
    RiskLevel,
    RiskMetrics,
)
from trader.core.domain.models.crypto_risk import (
    CryptoPositionRisk,
    CryptoRiskSnapshot,
    OpenOrderRisk,
)
from trader.core.domain.models.order import OrderSide
from trader.core.domain.models.signal import Signal, SignalType
from trader.core.domain.services.exchange_rule_guard import ExchangeRuleGuard
from trader.core.domain.services.margin_risk_calculator import MarginRiskCalculator
from trader.core.domain.services.open_order_exposure import OpenOrderExposureCalculator
from trader.core.domain.services.portfolio_exposure_aggregator import PortfolioExposureAggregator

if TYPE_CHECKING:
    from trader.core.application.risk_engine import RiskEngine


class CryptoRiskSnapshotProvider(Protocol):
    async def build(self, signal: Signal) -> CryptoRiskSnapshot: ...


@dataclass(frozen=True, slots=True)
class CryptoPreTradeRiskConfig:
    fail_closed: bool = True


class CryptoPreTradeRiskPlugin:
    def __init__(
        self,
        snapshot_provider: CryptoRiskSnapshotProvider,
        config: CryptoPreTradeRiskConfig | None = None,
    ) -> None:
        self._snapshot_provider = snapshot_provider
        self._config = config or CryptoPreTradeRiskConfig()
        self._rule_guard = ExchangeRuleGuard()
        self._open_order_exposure = OpenOrderExposureCalculator()
        self._portfolio_exposure = PortfolioExposureAggregator()
        self._margin = MarginRiskCalculator()

    async def check(
        self,
        signal: Signal,
        metrics: RiskMetrics,
        engine: "RiskEngine | None",
    ) -> RiskCheckResult | None:
        del metrics, engine
        try:
            snapshot = await self._snapshot_provider.build(signal)
        except Exception as exc:
            return self._reject(
                level=RiskLevel.CRITICAL,
                reason=RejectionReason.RISK_SYSTEM_ERROR,
                message="Crypto risk snapshot unavailable, fail-closed",
                details={"error": str(exc), "symbol": signal.symbol},
            )

        validation_error = self._validate_snapshot(signal, snapshot)
        if validation_error is not None:
            return validation_error

        spec = snapshot.instrument_specs[signal.symbol]
        mark_price = snapshot.mark_prices[signal.symbol]
        side = self._order_side(signal)
        price = signal.price if signal.price > 0 else mark_price

        rule_result = self._rule_guard.check_order(
            spec=spec,
            side=side,
            qty=signal.quantity,
            price=price,
        )
        if not rule_result.ok:
            return self._reject(
                level=RiskLevel.HIGH,
                reason=RejectionReason.CRYPTO_EXCHANGE_RULE,
                message=f"Exchange rule rejected {signal.symbol}: {rule_result.rejection_reason}",
                details={
                    "rule_reason": rule_result.rejection_reason,
                    "normalized_qty": str(rule_result.normalized_qty),
                    "normalized_price": str(rule_result.normalized_price),
                    "notional": str(rule_result.notional),
                },
            )

        proposed_order = OpenOrderRisk(
            cl_ord_id=signal.signal_id,
            symbol=signal.symbol,
            side=side,
            qty=rule_result.normalized_qty,
            filled_qty=Decimal("0"),
            price=rule_result.normalized_price,
            reduce_only=signal.is_close_signal(),
        )

        exposure_result = self._open_order_exposure.calculate_symbol_exposure(
            symbol=signal.symbol,
            positions=snapshot.positions,
            open_orders=[*snapshot.open_orders, proposed_order],
            mark_price=mark_price,
        )
        symbol_cap = snapshot.risk_budget.symbol_notional_caps.get(signal.symbol)
        if symbol_cap is not None and symbol_cap > 0:
            if exposure_result.total_risk_notional > symbol_cap:
                return self._reject(
                    level=RiskLevel.HIGH,
                    reason=RejectionReason.CRYPTO_OPEN_ORDER_EXPOSURE,
                    message=f"{signal.symbol} risk notional exceeds cap",
                    details={
                        "symbol_cap": str(symbol_cap),
                        "total_risk_notional": str(exposure_result.total_risk_notional),
                        "pending_open_notional": str(exposure_result.pending_open_notional),
                    },
                )

        total_cap = snapshot.risk_budget.total_notional_cap
        if total_cap > 0:
            total_risk = self._open_order_exposure.calculate_total_risk_notional(
                positions=snapshot.positions,
                open_orders=[*snapshot.open_orders, proposed_order],
                mark_prices=snapshot.mark_prices,
            )
            if total_risk > total_cap:
                return self._reject(
                    level=RiskLevel.HIGH,
                    reason=RejectionReason.CRYPTO_OPEN_ORDER_EXPOSURE,
                    message="Total crypto risk notional exceeds cap",
                    details={"total_cap": str(total_cap), "total_risk_notional": str(total_risk)},
                )

        cluster_result = self._evaluate_cluster_exposure(
            signal=signal,
            snapshot=snapshot,
            proposed_order=proposed_order,
        )
        if cluster_result is not None:
            return cluster_result

        margin_result = self._evaluate_projected_margin(
            signal=signal,
            snapshot=snapshot,
            proposed_order=proposed_order,
            mark_price=mark_price,
        )
        if margin_result is not None:
            return margin_result

        return None

    def _validate_snapshot(
        self,
        signal: Signal,
        snapshot: CryptoRiskSnapshot,
    ) -> RiskCheckResult | None:
        if signal.symbol not in snapshot.instrument_specs:
            return self._reject(
                level=RiskLevel.CRITICAL,
                reason=RejectionReason.RISK_SYSTEM_ERROR,
                message="Missing instrument spec, fail-closed",
                details={"symbol": signal.symbol},
            )
        mark_price = snapshot.mark_prices.get(signal.symbol)
        if mark_price is None or mark_price <= 0:
            return self._reject(
                level=RiskLevel.CRITICAL,
                reason=RejectionReason.RISK_SYSTEM_ERROR,
                message="Missing mark price, fail-closed",
                details={"symbol": signal.symbol},
            )
        if signal.quantity <= 0:
            return self._reject(
                level=RiskLevel.HIGH,
                reason=RejectionReason.CRYPTO_EXCHANGE_RULE,
                message="Signal quantity must be positive",
                details={"symbol": signal.symbol, "qty": str(signal.quantity)},
            )
        if signal.signal_type == SignalType.NONE:
            return self._reject(
                level=RiskLevel.HIGH,
                reason=RejectionReason.CRYPTO_EXCHANGE_RULE,
                message="Signal type NONE is not a trade intent",
                details={"symbol": signal.symbol, "signal_type": signal.signal_type.value},
            )
        return None

    def _evaluate_cluster_exposure(
        self,
        signal: Signal,
        snapshot: CryptoRiskSnapshot,
        proposed_order: OpenOrderRisk,
    ) -> RiskCheckResult | None:
        cluster_caps = snapshot.risk_budget.cluster_notional_caps
        if not cluster_caps:
            return None
        if proposed_order.reduce_only:
            return None

        symbol_clusters = snapshot.risk_budget.symbol_clusters
        if signal.symbol not in symbol_clusters:
            return self._reject(
                level=RiskLevel.CRITICAL,
                reason=RejectionReason.RISK_SYSTEM_ERROR,
                message="Missing crypto cluster mapping while cluster budget is enabled",
                details={"symbol": signal.symbol},
            )

        cluster_exposures = self._portfolio_exposure.calculate_cluster_exposures(
            positions=snapshot.positions,
            open_orders=[*snapshot.open_orders, proposed_order],
            mark_prices=snapshot.mark_prices,
            symbol_clusters=symbol_clusters,
        )
        for cluster, cap in sorted(cluster_caps.items()):
            if cap <= 0:
                continue
            exposure = cluster_exposures.get(cluster)
            if exposure is None:
                continue
            if exposure.total_risk_notional > cap:
                return self._reject(
                    level=RiskLevel.HIGH,
                    reason=RejectionReason.CRYPTO_CLUSTER_EXPOSURE,
                    message=f"{cluster} crypto cluster risk notional exceeds cap",
                    details={
                        "cluster": cluster,
                        "cluster_cap": str(cap),
                        "cluster_risk_notional": str(exposure.total_risk_notional),
                        "symbols": list(exposure.symbols),
                    },
                )
        return None

    def _evaluate_projected_margin(
        self,
        signal: Signal,
        snapshot: CryptoRiskSnapshot,
        proposed_order: OpenOrderRisk,
        mark_price: Decimal,
    ) -> RiskCheckResult | None:
        brackets = snapshot.leverage_brackets.get(signal.symbol, [])
        current_qty = sum(
            (position.qty for position in snapshot.positions if position.symbol == signal.symbol),
            Decimal("0"),
        )
        projected_qty = current_qty
        if not proposed_order.reduce_only:
            projected_qty += proposed_order.signed_remaining_qty

        projected_position = CryptoPositionRisk(
            symbol=signal.symbol,
            qty=projected_qty,
            entry_price=signal.price if signal.price > 0 else mark_price,
            mark_price=mark_price,
            leverage=self._resolve_leverage(signal, snapshot),
            liquidation_price=self._resolve_liquidation_price(signal),
        )
        margin_result = self._margin.evaluate_position(
            account=snapshot.account,
            position=projected_position,
            brackets=brackets,
        )
        if not margin_result.ok:
            return self._reject(
                level=RiskLevel.CRITICAL,
                reason=RejectionReason.RISK_SYSTEM_ERROR,
                message=f"Margin calculation failed: {margin_result.rejection_reason}",
                details={"margin_reason": margin_result.rejection_reason, "symbol": signal.symbol},
            )
        if margin_result.initial_margin > snapshot.account.available_balance:
            return self._reject(
                level=RiskLevel.HIGH,
                reason=RejectionReason.CRYPTO_MARGIN_LIMIT,
                message="Projected initial margin exceeds available balance",
                details={
                    "initial_margin": str(margin_result.initial_margin),
                    "available_balance": str(snapshot.account.available_balance),
                },
            )
        if margin_result.margin_ratio > snapshot.risk_budget.max_margin_ratio:
            return self._reject(
                level=RiskLevel.CRITICAL,
                reason=RejectionReason.CRYPTO_MARGIN_LIMIT,
                message="Projected margin ratio exceeds risk budget",
                details={
                    "margin_ratio": str(margin_result.margin_ratio),
                    "max_margin_ratio": str(snapshot.risk_budget.max_margin_ratio),
                },
            )

        min_buffer = snapshot.risk_budget.min_liquidation_buffer_ratio
        if min_buffer > 0:
            buffer_ratio = projected_position.liquidation_buffer_ratio
            if buffer_ratio is None:
                return self._reject(
                    level=RiskLevel.CRITICAL,
                    reason=RejectionReason.CRYPTO_LIQUIDATION_BUFFER,
                    message="Liquidation price missing while buffer budget is enabled",
                    details={"min_liquidation_buffer_ratio": str(min_buffer)},
                )
            if buffer_ratio < min_buffer:
                return self._reject(
                    level=RiskLevel.CRITICAL,
                    reason=RejectionReason.CRYPTO_LIQUIDATION_BUFFER,
                    message="Liquidation buffer below risk budget",
                    details={
                        "liquidation_buffer_ratio": str(buffer_ratio),
                        "min_liquidation_buffer_ratio": str(min_buffer),
                    },
                )
        return None

    def _resolve_leverage(self, signal: Signal, snapshot: CryptoRiskSnapshot) -> Decimal:
        leverage = signal.metadata.get("leverage")
        if leverage is not None:
            return Decimal(str(leverage))
        for position in snapshot.positions:
            if position.symbol == signal.symbol and position.leverage > 0:
                return position.leverage
        brackets = snapshot.leverage_brackets.get(signal.symbol, [])
        if brackets:
            return brackets[0].initial_leverage
        return Decimal("1")

    def _resolve_liquidation_price(self, signal: Signal) -> Decimal | None:
        value = signal.metadata.get("liquidation_price")
        if value is None:
            return None
        return Decimal(str(value))

    def _order_side(self, signal: Signal) -> OrderSide:
        if signal.signal_type in {SignalType.BUY, SignalType.LONG, SignalType.CLOSE_SHORT}:
            return OrderSide.BUY
        return OrderSide.SELL

    def _reject(
        self,
        level: RiskLevel,
        reason: RejectionReason,
        message: str,
        details: dict[str, object],
    ) -> RiskCheckResult:
        return RiskCheckResult(
            passed=False,
            risk_level=level,
            rejection_reason=reason,
            message=message,
            details=details,
        )
