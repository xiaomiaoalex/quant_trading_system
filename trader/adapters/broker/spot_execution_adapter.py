"""Binance spot implementations of the unified ExchangePort and snapshot port.

This adapter only translates venue fields into internal DTOs. Order state,
holds and balances remain owned by the trading service.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

import aiohttp

from trader.core.application.ports import BrokerBusinessError, BrokerNetworkError, BrokerOrder
from trader.core.domain.models.order import OrderSide, OrderStatus, OrderType
from trader.trading.models import (
    ExchangeOrderResult,
    ExchangeOutcome,
    Execution,
    OrderObservation,
    PlaceOrderCommand,
    ReconcileSnapshot,
)

_STATUS_MAP = {
    OrderStatus.PENDING: "OPEN",
    OrderStatus.SUBMITTED: "OPEN",
    OrderStatus.CANCEL_PENDING: "OPEN",
    OrderStatus.PARTIALLY_FILLED: "PARTIALLY_FILLED",
    OrderStatus.FILLED: "FILLED",
    OrderStatus.CANCELLED: "CANCELLED",
    OrderStatus.REJECTED: "REJECTED",
}


def _status(value: Any) -> str:
    if isinstance(value, OrderStatus):
        return _STATUS_MAP[value]
    text = str(getattr(value, "value", value) or "").upper()
    return {
        "NEW": "OPEN",
        "SUBMITTED": "OPEN",
        "PENDING": "OPEN",
        "CANCEL_PENDING": "OPEN",
        "EXPIRED": "EXPIRED",
        "PARTIALLY_FILLED": "PARTIALLY_FILLED",
        "FILLED": "FILLED",
        "CANCELED": "CANCELLED",
        "CANCELLED": "CANCELLED",
        "REJECTED": "REJECTED",
    }.get(text, "OPEN")


def _network_failure(exc: Exception) -> bool:
    return isinstance(
        exc,
        (BrokerNetworkError, asyncio.TimeoutError, ConnectionError, aiohttp.ClientError),
    )


@dataclass(frozen=True)
class SymbolRules:
    """Exchange rules needed to normalise an order before it is sent."""

    step_size: Decimal = Decimal("0")
    tick_size: Decimal = Decimal("0")
    min_notional: Decimal = Decimal("0")
    quote_precision: int = 8

    def quantize_qty(self, qty: Decimal) -> Decimal:
        if self.step_size <= 0:
            return qty
        steps = int(qty / self.step_size)
        return Decimal(steps) * self.step_size

    def quantize_price(self, price: Decimal) -> Decimal:
        if self.tick_size <= 0:
            return price
        ticks = int(price / self.tick_size)
        return Decimal(ticks) * self.tick_size

    def quantize_quote(self, amount: Decimal) -> Decimal:
        return amount.quantize(Decimal(1).scaleb(-self.quote_precision))


class BinanceSpotExchangePort:
    """ExchangePort over the existing signing/HTTP broker adapter."""

    def __init__(self, broker):
        self.broker = broker
        self._rules: dict[str, SymbolRules] = {}

    async def symbol_rules(self, symbol: str) -> SymbolRules:
        cached = self._rules.get(symbol)
        if cached is not None:
            return cached
        info = await self.broker.get_exchange_info(symbol=symbol)
        symbols = info.get("symbols") or []
        data = symbols[0] if symbols else {}
        filters = {
            item.get("filterType"): item
            for item in data.get("filters", [])
            if isinstance(item, dict)
        }
        rules = SymbolRules(
            step_size=Decimal(str(filters.get("LOT_SIZE", {}).get("stepSize", "0") or "0")),
            tick_size=Decimal(str(filters.get("PRICE_FILTER", {}).get("tickSize", "0") or "0")),
            min_notional=Decimal(str(filters.get("NOTIONAL", {}).get("minNotional", "0") or "0")),
            quote_precision=int(data.get("quoteAssetPrecision") or data.get("quotePrecision") or 8),
        )
        self._rules[symbol] = rules
        return rules

    async def place_order(self, command: PlaceOrderCommand, cl_ord_id: str) -> ExchangeOrderResult:
        try:
            rules = await self.symbol_rules(command.symbol)
        except Exception as exc:
            if _network_failure(exc):
                return ExchangeOrderResult(ExchangeOutcome.UNKNOWN, reason=str(exc))
            return ExchangeOrderResult(ExchangeOutcome.UNAVAILABLE, reason=str(exc))

        qty = rules.quantize_qty(command.qty)
        price = rules.quantize_price(command.price)
        if qty <= 0:
            return ExchangeOrderResult(
                ExchangeOutcome.REJECTED,
                reason="QUANTITY_BELOW_STEP_SIZE",
            )
        quote_amount = None
        if command.order_type == "MARKET" and command.side == "BUY":
            quote_amount = rules.quantize_quote(qty * command.price)
            if quote_amount <= 0:
                return ExchangeOrderResult(
                    ExchangeOutcome.REJECTED,
                    reason="QUOTE_AMOUNT_BELOW_MINIMUM",
                )
        elif rules.min_notional > 0 and qty * price < rules.min_notional:
            return ExchangeOrderResult(
                ExchangeOutcome.REJECTED,
                reason=f"NOTIONAL_BELOW_MINIMUM:{qty * price}<{rules.min_notional}",
            )

        try:
            order = await self.broker.place_order(
                symbol=command.symbol,
                side=OrderSide(command.side),
                order_type=OrderType(command.order_type),
                qty=qty,
                price=price,
                cl_ord_id=cl_ord_id,
                quote_qty=quote_amount,
            )
        except BrokerBusinessError as exc:
            return ExchangeOrderResult(ExchangeOutcome.REJECTED, reason=str(exc))
        except Exception as exc:
            if _network_failure(exc):
                return ExchangeOrderResult(ExchangeOutcome.UNKNOWN, reason=str(exc))
            return ExchangeOrderResult(ExchangeOutcome.UNAVAILABLE, reason=str(exc))
        return ExchangeOrderResult(
            ExchangeOutcome.ACCEPTED,
            broker_order_id=order.broker_order_id,
            status=_status(order.status),
            cumulative_qty=order.filled_qty,
        )

    async def cancel_order(
        self, account_id: str, cl_ord_id: str, command_id: str, symbol: str
    ) -> ExchangeOrderResult:
        try:
            await self.broker.cancel_order(cl_ord_id=cl_ord_id, symbol=symbol)
        except BrokerBusinessError as exc:
            message = str(exc)
            if "Unknown order" in message or "unknown order" in message.lower():
                return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND, reason=message)
            return ExchangeOrderResult(ExchangeOutcome.REJECTED, reason=message)
        except Exception as exc:
            if _network_failure(exc):
                return ExchangeOrderResult(ExchangeOutcome.UNKNOWN, reason=str(exc))
            return ExchangeOrderResult(ExchangeOutcome.UNAVAILABLE, reason=str(exc))
        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, status="OPEN")

    async def query_order(
        self, account_id: str, cl_ord_id: str, symbol: str
    ) -> ExchangeOrderResult:
        try:
            order = await self.broker.get_order(cl_ord_id=cl_ord_id, symbol=symbol)
        except Exception as exc:
            if _network_failure(exc):
                return ExchangeOrderResult(ExchangeOutcome.UNKNOWN, reason=str(exc))
            return ExchangeOrderResult(ExchangeOutcome.UNAVAILABLE, reason=str(exc))
        if order is None:
            return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)
        return ExchangeOrderResult(
            ExchangeOutcome.ACCEPTED,
            broker_order_id=order.broker_order_id,
            status=_status(order.status),
            cumulative_qty=order.filled_qty,
        )


class BinanceSpotSnapshotProvider:
    """Builds a complete account snapshot or fails closed.

    ``locked`` balances on spot are usually our own open orders, so they are not
    reported as external locks. Explicit external locks come from configuration.
    Pagination truncation or an unmapped order/trade marks the snapshot
    incomplete, and the runtime will refuse to enable new risk.
    """

    def __init__(
        self,
        broker,
        *,
        account_id: str,
        symbols: list[str],
        clock: Callable[[], int],
        lookback_ms: int = 86_400_000,
        external_locked: dict[str, Decimal] | None = None,
        page_limit: int = 1000,
        max_pages: int = 10,
        order_prefix: str = "qts-",
    ):
        self.broker = broker
        self.account_id = account_id
        self.symbols = [symbol.upper() for symbol in symbols]
        self.clock = clock
        self.lookback_ms = lookback_ms
        self.external_locked = dict(external_locked or {})
        self.page_limit = page_limit
        self.max_pages = max_pages
        self.order_prefix = order_prefix
        self._generation = 0
        self._last_checked_ms = 0

    async def build(self, account_id: str | None = None) -> ReconcileSnapshot:
        account_id = account_id or self.account_id
        now = max(self.clock(), self._last_checked_ms + 1)
        self._last_checked_ms = now
        complete = True

        account = await self.broker._fetch_account()
        balances: dict[str, Decimal] = {}
        for item in account.get("balances", []):
            asset = str(item.get("asset", ""))
            if not asset:
                continue
            free = Decimal(str(item.get("free", "0")))
            locked = Decimal(str(item.get("locked", "0")))
            total = free + locked
            if total != 0:
                balances[asset] = total

        open_orders = await self.broker.get_open_orders()
        observations: list[OrderObservation] = []
        for order in open_orders:
            observations.append(
                OrderObservation(
                    account_id=account_id,
                    cl_ord_id=order.cl_ord_id,
                    status=_status(order.status),
                    cumulative_qty=order.filled_qty,
                    broker_order_id=order.broker_order_id,
                    observed_at_ms=now,
                    source="REST",
                )
            )

        symbols = set(self.symbols)
        symbols.update(order.symbol for order in open_orders if order.symbol)
        open_cl_ord_ids = {order.cl_ord_id for order in open_orders}

        marks: dict[str, dict[str, Any]] = {}
        if symbols:
            prices = await self.broker.get_ticker_prices(sorted(symbols))
            for symbol, price in prices.items():
                if price is not None and Decimal(price) > 0:
                    marks[symbol] = {"price": Decimal(price), "at_ms": now}

        executions: list[Execution] = []
        for symbol in sorted(symbols):
            orders = await self.broker.get_all_orders(
                symbol, start_time_ms=now - self.lookback_ms, limit=self.page_limit
            )
            if len(orders) >= self.page_limit:
                complete = False
            mapping = {order.broker_order_id: order.cl_ord_id for order in orders}
            # Closed orders disappear from openOrders; report our own terminal
            # orders so cancellations and fills are observed instead of guessed.
            for order in orders:
                if (
                    order.cl_ord_id.startswith(self.order_prefix)
                    and order.cl_ord_id not in open_cl_ord_ids
                ):
                    observations.append(
                        OrderObservation(
                            account_id=account_id,
                            cl_ord_id=order.cl_ord_id,
                            status=_status(order.status),
                            cumulative_qty=order.filled_qty,
                            broker_order_id=order.broker_order_id,
                            observed_at_ms=now,
                            source="REST",
                        )
                    )
            trades, trades_complete = await self._trades(symbol, now)
            complete = complete and trades_complete
            for trade in trades:
                order_id = str(trade.get("orderId", ""))
                cl_ord_id = mapping.get(order_id)
                if not cl_ord_id:
                    complete = False
                    continue
                executions.append(
                    Execution(
                        account_id=account_id,
                        cl_ord_id=cl_ord_id,
                        symbol=symbol,
                        exec_id=str(trade.get("id", "")),
                        qty=Decimal(str(trade.get("qty", "0"))),
                        price=Decimal(str(trade.get("price", "0"))),
                        fee=Decimal(str(trade.get("commission", "0"))),
                        fee_asset=str(trade.get("commissionAsset", "")),
                        occurred_at_ms=int(trade.get("time", now)),
                        venue="binance_spot",
                    )
                )

        self._generation = max(now, self._generation + 1)
        return ReconcileSnapshot(
            account_id=account_id,
            checked_at_ms=now,
            generation=self._generation,
            balances=balances,
            external_locked=self.external_locked,
            open_orders=observations,
            executions=executions,
            marks=marks,
            stream_healthy=True,
            complete=complete,
        )

    async def _trades(self, symbol: str, now: int) -> tuple[list[dict], bool]:
        trades: list[dict] = []
        from_id = None
        for _ in range(self.max_pages):
            page = await self.broker.get_my_trades(
                symbol,
                start_time_ms=now - self.lookback_ms,
                from_id=from_id,
                limit=self.page_limit,
            )
            trades.extend(page)
            if len(page) < self.page_limit:
                return trades, True
            from_id = int(page[-1].get("id", 0)) + 1
        return trades, False


def observation_from_broker_order(
    order: BrokerOrder, now: int, account_id: str
) -> OrderObservation:
    return OrderObservation(
        account_id=account_id,
        cl_ord_id=order.cl_ord_id,
        status=_status(order.status),
        cumulative_qty=order.filled_qty,
        broker_order_id=order.broker_order_id,
        observed_at_ms=now,
        source="REST",
    )
