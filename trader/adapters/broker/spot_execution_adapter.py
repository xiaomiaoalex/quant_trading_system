"""Binance spot implementations of the unified ExchangePort and snapshot port.

This adapter only translates venue fields into internal DTOs. Order state,
holds and balances remain owned by the trading service.
"""

from __future__ import annotations

import asyncio
import inspect
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
    VenueOrder,
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
        self._assets: dict[str, tuple[str, str]] = {}

    async def _now(self) -> int:
        """Read the snapshot timestamp in the venue clock domain.

        The clock may be async (exchange server time). Snapshot timestamps must
        not mix the local clock with venue time: the coverage proof compares
        them against the stream connection boundary.
        """
        value = self.clock()
        if inspect.isawaitable(value):
            value = await value
        return int(value)

    async def _symbol_assets(self, symbol: str) -> tuple[str, str]:
        """Base and quote asset of a symbol, taken from the venue's own metadata."""
        cached = self._assets.get(symbol)
        if cached is not None:
            return cached
        base, quote = "", ""
        try:
            info = await self.broker.get_exchange_info(symbol)
            for item in (info or {}).get("symbols", []):
                if str(item.get("symbol", "")).upper() == symbol:
                    base = str(item.get("baseAsset", ""))
                    quote = str(item.get("quoteAsset", ""))
                    break
        except Exception:
            base, quote = "", ""
        self._assets[symbol] = (base, quote)
        return base, quote

    async def build(self, account_id: str | None = None) -> ReconcileSnapshot:
        account_id = account_id or self.account_id
        now = max(await self._now(), self._last_checked_ms + 1)
        self._last_checked_ms = now
        complete = True

        account, open_orders = await asyncio.gather(
            self.broker._fetch_account(), self.broker.get_open_orders()
        )
        initial_balances = self._balance_fingerprint(account)
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

        initial_orders = self._order_fingerprint(open_orders)
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

        async def _fetch_symbol(symbol: str):
            (orders, orders_complete), (trades, trades_complete) = await asyncio.gather(
                self._orders(symbol, now), self._trades(symbol, now)
            )
            return symbol, orders, orders_complete, trades, trades_complete

        ordered_symbols = sorted(symbols)
        # Symbols are independent reads; fetching them concurrently only
        # shortens one snapshot. Assembly below stays in sorted symbol order.
        fetched = await asyncio.gather(*(_fetch_symbol(symbol) for symbol in ordered_symbols))
        assets = dict(
            zip(
                ordered_symbols,
                await asyncio.gather(*(self._symbol_assets(symbol) for symbol in ordered_symbols)),
            )
        )

        executions: list[Execution] = []
        venue_orders: list[VenueOrder] = []
        for symbol, orders, orders_complete, trades, trades_complete in fetched:
            complete = complete and orders_complete
            mapping = {order.broker_order_id: order.cl_ord_id for order in orders}
            # Closed orders disappear from openOrders; report our own terminal
            # orders so cancellations and fills are observed instead of guessed.
            for order in orders:
                if (
                    order.cl_ord_id.startswith(self.order_prefix)
                    and order.cl_ord_id not in open_cl_ord_ids
                ):
                    base_asset, quote_asset = assets[symbol]
                    venue_orders.append(
                        VenueOrder(
                            cl_ord_id=order.cl_ord_id,
                            broker_order_id=order.broker_order_id,
                            symbol=symbol,
                            side=str(getattr(order.side, "value", order.side)).upper(),
                            order_type=str(
                                getattr(order.order_type, "value", order.order_type)
                            ).upper(),
                            qty=Decimal(str(order.qty)),
                            price=Decimal(str(order.average_price or 0)),
                            filled_qty=Decimal(str(order.filled_qty)),
                            status=_status(order.status),
                            created_at_ms=int(order.created_at.timestamp() * 1000),
                            base_asset=base_asset,
                            quote_asset=quote_asset,
                        )
                    )
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

        # REST calls are not an atomic exchange snapshot. A change during the
        # collection invalidates this generation instead of enabling risk with
        # balances from before a fill and executions from after it.
        final_account, final_orders = await asyncio.gather(
            self.broker._fetch_account(), self.broker.get_open_orders()
        )
        complete = (
            complete
            and initial_balances == self._balance_fingerprint(final_account)
            and initial_orders == self._order_fingerprint(final_orders)
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
            venue_orders=venue_orders,
            marks=marks,
            stream_healthy=True,
            complete=complete,
        )

    @staticmethod
    def _balance_fingerprint(account: dict) -> tuple:
        return tuple(
            sorted(
                (
                    str(item["asset"]),
                    Decimal(str(item.get("free", "0"))),
                    Decimal(str(item.get("locked", "0"))),
                )
                for item in account.get("balances", [])
            )
        )

    @staticmethod
    def _order_fingerprint(orders: list) -> tuple:
        return tuple(
            sorted(
                (
                    order.symbol,
                    order.cl_ord_id,
                    order.broker_order_id,
                    _status(order.status),
                    order.qty,
                    order.filled_qty,
                )
                for order in orders
            )
        )

    async def _trades(self, symbol: str, now: int) -> tuple[list[dict], bool]:
        return await self.fetch_trades(symbol, now - self.lookback_ms)

    async def fetch_trades(self, symbol: str, start_time_ms: int) -> tuple[list[dict], bool]:
        """Complete paginated trade history; False means coverage is unproven."""
        trades: list[dict] = []
        from_id = None
        for _ in range(self.max_pages):
            page = await self.broker.get_my_trades(
                symbol,
                start_time_ms=start_time_ms,
                from_id=from_id,
                limit=self.page_limit,
            )
            trades.extend(page)
            if len(page) < self.page_limit:
                return trades, True
            from_id = int(page[-1].get("id", 0)) + 1
        return trades, False

    async def _orders(self, symbol: str, now: int) -> tuple[list, bool]:
        return await self.fetch_orders(symbol, now - self.lookback_ms)

    async def fetch_orders(self, symbol: str, start_time_ms: int) -> tuple[list, bool]:
        """Complete paginated order history; False means coverage is unproven."""
        orders = []
        seen = set()
        cursor = None
        for _ in range(self.max_pages):
            kwargs = {"start_time_ms": start_time_ms, "limit": self.page_limit}
            if cursor is not None:
                kwargs["from_order_id"] = cursor
            try:
                page = await self.broker.get_all_orders(symbol, **kwargs)
            except TypeError:
                # A legacy port without cursor support cannot prove completion.
                if cursor is None:
                    raise
                return orders, False
            for order in page:
                if order.broker_order_id in seen:
                    return orders, False
                seen.add(order.broker_order_id)
                orders.append(order)
            if len(page) < self.page_limit:
                return orders, True
            try:
                next_cursor = int(page[-1].broker_order_id) + 1
            except (ValueError, TypeError):
                return orders, False
            if cursor is not None and next_cursor <= cursor:
                return orders, False
            cursor = next_cursor
        return orders, False


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
