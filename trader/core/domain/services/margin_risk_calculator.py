from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trader.core.domain.models.crypto_risk import (
    CryptoAccountRisk,
    CryptoPositionRisk,
    LeverageBracket,
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


class MarginRiskCalculator:
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
