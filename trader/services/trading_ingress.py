"""Translate Binance private-stream events into unified trading facts.

The ingress never mutates order or account state itself. It forwards raw
executions and order observations to the trading service, which owns
idempotency, accounting and reconciliation evidence.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from trader.trading.models import Conflict, Execution, OrderObservation
from trader.trading.service import TradingService

logger = logging.getLogger(__name__)

_STATUS = {
    "NEW": "OPEN",
    "SUBMITTED": "OPEN",
    "PENDING": "OPEN",
    "CANCEL_PENDING": "OPEN",
    "PARTIALLY_FILLED": "PARTIALLY_FILLED",
    "FILLED": "FILLED",
    "CANCELED": "CANCELLED",
    "CANCELLED": "CANCELLED",
    "EXPIRED": "EXPIRED",
    "REJECTED": "REJECTED",
}


class ExecutionIngress:
    def __init__(self, service: TradingService, *, account_id: str, venue: str = "binance_spot"):
        self.service = service
        self.account_id = account_id
        self.venue = venue

    async def on_fill(self, update: Any) -> bool:
        cl_ord_id = getattr(update, "cl_ord_id", None)
        exec_id = getattr(update, "exec_id", None) or getattr(update, "trade_id", None)
        if not cl_ord_id or not exec_id:
            logger.warning("Dropping fill without cl_ord_id/exec_id")
            return False
        order = await self.service.order(self.account_id, cl_ord_id)
        symbol = getattr(update, "symbol", None) or (order["symbol"] if order else "")
        quote_asset = order["quote_asset"] if order else ""
        commission = Decimal(str(getattr(update, "commission", 0) or 0))
        fee_asset = getattr(update, "commission_asset", None) or ""
        if commission > 0 and not fee_asset:
            fee_asset = quote_asset
        execution = Execution(
            account_id=self.account_id,
            cl_ord_id=str(cl_ord_id),
            symbol=str(symbol),
            exec_id=str(exec_id),
            qty=Decimal(str(getattr(update, "qty", 0))),
            price=Decimal(str(getattr(update, "price", 0))),
            fee=commission,
            fee_asset=fee_asset,
            occurred_at_ms=int(getattr(update, "exchange_ts_ms", 0) or 0),
            venue=self.venue,
        )
        try:
            return await self.service.record_execution(execution)
        except Conflict:
            # The service persisted conflict evidence and blocked new risk.
            logger.error(
                "Execution key conflict recorded: cl_ord_id=%s exec_id=%s",
                cl_ord_id,
                exec_id,
            )
            return False

    async def on_order_update(self, update: Any) -> bool:
        cl_ord_id = getattr(update, "cl_ord_id", None)
        if not cl_ord_id:
            return False
        status = _STATUS.get(str(getattr(update, "status", "")).upper(), "OPEN")
        observation = OrderObservation(
            account_id=self.account_id,
            cl_ord_id=str(cl_ord_id),
            status=status,
            cumulative_qty=Decimal(str(getattr(update, "filled_qty", 0) or 0)),
            broker_order_id=str(getattr(update, "broker_order_id", "") or ""),
            observed_at_ms=int(getattr(update, "exchange_ts_ms", 0) or 0),
            source=str(getattr(update, "source", "WS") or "WS"),
        )
        return await self.service.observe(observation)
