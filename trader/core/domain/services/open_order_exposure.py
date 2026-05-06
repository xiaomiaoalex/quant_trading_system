from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, Sequence

_ACTIVE_ORDER_STATUSES = {
    "OPEN",
    "NEW",
    "PENDING",
    "SUBMITTED",
    "PARTIALLY_FILLED",
    "CANCEL_PENDING",
}


class PositionRiskLike(Protocol):
    symbol: str
    qty: Decimal


class OpenOrderRiskLike(Protocol):
    symbol: str
    reduce_only: bool
    status: str

    @property
    def signed_remaining_qty(self) -> Decimal: ...

    @property
    def notional(self) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class SymbolOpenOrderExposure:
    symbol: str
    current_qty: Decimal
    current_notional: Decimal
    pending_open_qty: Decimal
    pending_open_notional: Decimal
    risk_qty_after_open_orders: Decimal
    total_risk_notional: Decimal


class OpenOrderExposureCalculator:
    def calculate_symbol_exposure(
        self,
        symbol: str,
        positions: Sequence[PositionRiskLike],
        open_orders: Sequence[OpenOrderRiskLike],
        mark_price: Decimal,
    ) -> SymbolOpenOrderExposure:
        current_qty = sum((p.qty for p in positions if p.symbol == symbol), Decimal("0"))
        current_notional = abs(current_qty) * mark_price

        pending_open_qty = Decimal("0")
        pending_open_notional = Decimal("0")
        for order in open_orders:
            if order.symbol != symbol:
                continue
            if order.status not in _ACTIVE_ORDER_STATUSES:
                continue
            if order.reduce_only:
                continue
            pending_open_qty += order.signed_remaining_qty
            pending_open_notional += order.notional

        risk_qty_after_open_orders = current_qty + pending_open_qty
        total_risk_notional = current_notional + pending_open_notional

        return SymbolOpenOrderExposure(
            symbol=symbol,
            current_qty=current_qty,
            current_notional=current_notional,
            pending_open_qty=pending_open_qty,
            pending_open_notional=pending_open_notional,
            risk_qty_after_open_orders=risk_qty_after_open_orders,
            total_risk_notional=total_risk_notional,
        )

    def calculate_total_risk_notional(
        self,
        positions: Sequence[PositionRiskLike],
        open_orders: Sequence[OpenOrderRiskLike],
        mark_prices: dict[str, Decimal],
    ) -> Decimal:
        symbols = {position.symbol for position in positions}
        symbols.update(order.symbol for order in open_orders)

        total = Decimal("0")
        for symbol in symbols:
            mark_price = mark_prices.get(symbol)
            if mark_price is None or mark_price <= 0:
                continue
            total += self.calculate_symbol_exposure(
                symbol=symbol,
                positions=positions,
                open_orders=open_orders,
                mark_price=mark_price,
            ).total_risk_notional
        return total
