from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Protocol

from trader.core.domain.models.order import OrderSide


class InstrumentSpecLike(Protocol):
    symbol: str
    price_tick: Decimal
    qty_step: Decimal
    min_qty: Decimal
    min_notional: Decimal
    max_qty: Decimal | None
    max_notional: Decimal | None


@dataclass(frozen=True, slots=True)
class ExchangeRuleCheckResult:
    ok: bool
    normalized_qty: Decimal
    normalized_price: Decimal
    notional: Decimal
    rejection_reason: str | None = None
    message: str = ""


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    units = (value / step).to_integral_value(rounding=ROUND_FLOOR)
    return units * step


class ExchangeRuleGuard:
    def check_order(
        self,
        spec: InstrumentSpecLike,
        side: OrderSide,
        qty: Decimal,
        price: Decimal,
    ) -> ExchangeRuleCheckResult:
        if spec.price_tick <= 0 or spec.qty_step <= 0:
            return self._reject("INVALID_INSTRUMENT_SPEC", qty, price)
        if qty <= 0:
            return self._reject("INVALID_QTY", Decimal("0"), price)
        if price <= 0:
            return self._reject("INVALID_PRICE", qty, Decimal("0"))

        normalized_qty = _floor_to_step(qty, spec.qty_step)
        normalized_price = _floor_to_step(price, spec.price_tick)
        notional = normalized_qty * normalized_price

        if normalized_qty <= 0:
            return self._reject("INVALID_QTY", normalized_qty, normalized_price)
        if normalized_qty < spec.min_qty:
            return self._reject("MIN_QTY", normalized_qty, normalized_price)
        if spec.max_qty is not None and normalized_qty > spec.max_qty:
            return self._reject("MAX_QTY", normalized_qty, normalized_price)
        if notional < spec.min_notional:
            return self._reject("MIN_NOTIONAL", normalized_qty, normalized_price)
        if spec.max_notional is not None and notional > spec.max_notional:
            return self._reject("MAX_NOTIONAL", normalized_qty, normalized_price)

        return ExchangeRuleCheckResult(
            ok=True,
            normalized_qty=normalized_qty,
            normalized_price=normalized_price,
            notional=notional,
            message=f"{side.value} {spec.symbol} satisfies exchange rules",
        )

    def _reject(
        self,
        reason: str,
        qty: Decimal,
        price: Decimal,
    ) -> ExchangeRuleCheckResult:
        normalized_qty = max(Decimal("0"), qty)
        normalized_price = max(Decimal("0"), price)
        return ExchangeRuleCheckResult(
            ok=False,
            normalized_qty=normalized_qty,
            normalized_price=normalized_price,
            notional=normalized_qty * normalized_price,
            rejection_reason=reason,
            message=reason,
        )
