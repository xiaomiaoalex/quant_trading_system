from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from trader.core.domain.models.crypto_risk import (
    CryptoAccountRisk,
    CryptoPositionRisk,
    LeverageBracket,
    MarginMode,
)


@dataclass(frozen=True, slots=True)
class MarginRiskResult:
    ok: bool
    notional: Decimal
    initial_margin: Decimal
    maintenance_margin: Decimal
    margin_ratio: Decimal
    bracket: LeverageBracket | None = None
    rejection_reason: str | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class LiquidationPriceResult:
    ok: bool
    liquidation_price: Optional[Decimal]
    buffer_ratio: Optional[Decimal]
    effective_initial_margin: Decimal = Decimal("0")
    effective_maintenance_margin: Decimal = Decimal("0")
    funding_fee_estimate: Decimal = Decimal("0")
    taker_fee_estimate: Decimal = Decimal("0")
    slippage_estimate: Decimal = Decimal("0")
    rejection_reason: Optional[str] = None


@dataclass(frozen=True, slots=True)
class FeeBufferConfig:
    funding_rate: Decimal = Decimal("0.0001")
    taker_fee_rate: Decimal = Decimal("0.0004")
    slippage_bps: Decimal = Decimal("5")
    funding_interval_hours: int = 8


class MarginRiskCalculator:
    def __init__(self, fee_config: Optional[FeeBufferConfig] = None) -> None:
        self._fee_config = fee_config or FeeBufferConfig()

    def evaluate_position(
        self,
        account: CryptoAccountRisk,
        position: CryptoPositionRisk,
        brackets: list[LeverageBracket],
    ) -> MarginRiskResult:
        if account.margin_balance <= 0:
            return self._reject("INVALID_MARGIN_BALANCE", position.notional)
        if position.mark_price <= 0 or position.leverage <= 0:
            return self._reject("INVALID_POSITION_INPUT", position.notional)

        notional = position.notional
        if notional == 0:
            return MarginRiskResult(
                ok=True,
                notional=Decimal("0"),
                initial_margin=Decimal("0"),
                maintenance_margin=Decimal("0"),
                margin_ratio=Decimal("0"),
                message="empty position",
            )

        bracket = self.select_bracket(notional, brackets)
        if bracket is None:
            return self._reject("MISSING_LEVERAGE_BRACKET", notional)

        effective_leverage = min(position.leverage, bracket.initial_leverage)
        initial_margin = notional / effective_leverage
        maintenance_margin = max(
            Decimal("0"),
            notional * bracket.maint_margin_ratio - bracket.maint_amount,
        )
        margin_ratio = maintenance_margin / account.margin_balance

        return MarginRiskResult(
            ok=True,
            notional=notional,
            initial_margin=initial_margin,
            maintenance_margin=maintenance_margin,
            margin_ratio=margin_ratio,
            bracket=bracket,
            message="margin check passed",
        )

    def calculate_liquidation_price(
        self,
        account: CryptoAccountRisk,
        position: CryptoPositionRisk,
        brackets: list[LeverageBracket],
        margin_mode: MarginMode = MarginMode.CROSS,
    ) -> LiquidationPriceResult:
        if account.margin_balance <= 0:
            return LiquidationPriceResult(
                ok=False,
                liquidation_price=None,
                buffer_ratio=None,
                rejection_reason="INVALID_MARGIN_BALANCE",
            )
        if position.mark_price <= 0 or position.leverage <= 0:
            return LiquidationPriceResult(
                ok=False,
                liquidation_price=None,
                buffer_ratio=None,
                rejection_reason="INVALID_POSITION_INPUT",
            )

        notional = position.notional
        if notional == 0:
            return LiquidationPriceResult(
                ok=True,
                liquidation_price=Decimal("0"),
                buffer_ratio=None,
            )

        bracket = self.select_bracket(notional, brackets)
        if bracket is None:
            return LiquidationPriceResult(
                ok=False,
                liquidation_price=None,
                buffer_ratio=None,
                rejection_reason="MISSING_LEVERAGE_BRACKET",
            )

        effective_leverage = min(position.leverage, bracket.initial_leverage)
        if effective_leverage <= 0:
            return LiquidationPriceResult(
                ok=False,
                liquidation_price=None,
                buffer_ratio=None,
                rejection_reason="INVALID_POSITION_INPUT",
            )
        entry_price = position.entry_price
        qty = position.qty

        fee_estimates = self._estimate_fees(
            position=position,
            is_reducing=False,
        )

        abs_qty = abs(qty)
        entry_notional = abs_qty * entry_price
        effective_initial_margin = entry_notional / effective_leverage
        total_fees = fee_estimates["funding"] + fee_estimates["taker"] + fee_estimates["slippage"]
        maintenance_margin = max(
            Decimal("0"),
            notional * bracket.maint_margin_ratio - bracket.maint_amount,
        )
        effective_maintenance_margin = maintenance_margin + total_fees

        if qty > 0:
            denominator = abs_qty * (Decimal("1") - bracket.maint_margin_ratio)
            numerator = (
                entry_notional - effective_initial_margin - bracket.maint_amount + total_fees
            )
        else:
            denominator = abs_qty * (Decimal("1") + bracket.maint_margin_ratio)
            numerator = (
                entry_notional + effective_initial_margin + bracket.maint_amount - total_fees
            )

        if denominator <= 0:
            return LiquidationPriceResult(
                ok=False,
                liquidation_price=None,
                buffer_ratio=None,
                rejection_reason="INVALID_POSITION_INPUT",
            )

        liquidation_price = max(Decimal("0"), numerator / denominator)

        if position.mark_price > 0 and liquidation_price > 0:
            if qty > 0:
                buffer = (position.mark_price - liquidation_price) / position.mark_price
            else:
                buffer = (liquidation_price - position.mark_price) / position.mark_price
        else:
            buffer = None

        return LiquidationPriceResult(
            ok=True,
            liquidation_price=liquidation_price,
            buffer_ratio=buffer,
            effective_initial_margin=effective_initial_margin,
            effective_maintenance_margin=effective_maintenance_margin,
            funding_fee_estimate=fee_estimates["funding"],
            taker_fee_estimate=fee_estimates["taker"],
            slippage_estimate=fee_estimates["slippage"],
        )

    def calculate_risk_adjusted_margin(
        self,
        account: CryptoAccountRisk,
        position: CryptoPositionRisk,
        brackets: list[LeverageBracket],
        is_reducing: bool = False,
    ) -> tuple[Decimal, Decimal, Decimal]:
        if not self._fee_config or account.margin_balance <= 0:
            result = self.evaluate_position(account, position, brackets)
            return result.initial_margin, result.maintenance_margin, result.margin_ratio

        notional = position.notional
        if notional == 0:
            return Decimal("0"), Decimal("0"), Decimal("0")

        bracket = self.select_bracket(notional, brackets)
        if bracket is None:
            return Decimal("0"), Decimal("0"), Decimal("0")

        effective_leverage = min(position.leverage, bracket.initial_leverage)
        initial_margin = notional / effective_leverage

        fee_estimates = self._estimate_fees(position, is_reducing)
        total_fees = fee_estimates["funding"] + fee_estimates["taker"] + fee_estimates["slippage"]

        maintenance_margin = max(
            Decimal("0"),
            notional * bracket.maint_margin_ratio - bracket.maint_amount,
        )
        adjusted_maintenance = maintenance_margin + total_fees
        adjusted_ratio = adjusted_maintenance / account.margin_balance

        return initial_margin, adjusted_maintenance, adjusted_ratio

    def _estimate_fees(
        self,
        position: CryptoPositionRisk,
        is_reducing: bool,
    ) -> dict[str, Decimal]:
        if not self._fee_config:
            return {"funding": Decimal("0"), "taker": Decimal("0"), "slippage": Decimal("0")}

        notional = position.notional
        funding = (
            notional
            * self._fee_config.funding_rate
            * (Decimal(self._fee_config.funding_interval_hours) / Decimal("24"))
        )
        taker = notional * self._fee_config.taker_fee_rate if not is_reducing else Decimal("0")
        slippage = notional * (self._fee_config.slippage_bps / Decimal("10000"))

        return {
            "funding": funding,
            "taker": taker,
            "slippage": slippage,
        }

    def select_bracket(
        self,
        notional: Decimal,
        brackets: list[LeverageBracket],
    ) -> LeverageBracket | None:
        for bracket in sorted(brackets, key=lambda item: item.notional_floor):
            if bracket.contains(notional):
                return bracket
        return None

    def _reject(self, reason: str, notional: Decimal) -> MarginRiskResult:
        return MarginRiskResult(
            ok=False,
            notional=notional,
            initial_margin=Decimal("0"),
            maintenance_margin=Decimal("0"),
            margin_ratio=Decimal("0"),
            rejection_reason=reason,
            message=reason,
        )
