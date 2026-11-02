"""Single explicit order specification for strategy, manual and simulated flow.

Every order path produces one ``PlaceOrderCommand`` through this contract:
the strategy runner builds an ``OrderSpec`` here, the manual API validates the
same fields, and the historical simulator uses the same command. The adapter
only translates the resulting command into venue parameters; it never decides
an order type, a price source or a book.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException
from typing import Any

from trader.trading.books import validate_strategy_book_id
from trader.trading.models import (
    SETTLEMENT_BASE,
    SETTLEMENT_QUOTE,
    SETTLEMENTS,
    PlaceOrderCommand,
    TradingError,
)

PRICE_SOURCE_SIGNAL_LIMIT = "SIGNAL_LIMIT"
PRICE_SOURCE_MANUAL_LIMIT = "MANUAL_LIMIT"
PRICE_SOURCE_MARKET_TICKER = "MARKET_REFERENCE_TICKER"
PRICE_SOURCE_MARKET_CLOSED_BAR = "MARKET_REFERENCE_CLOSED_BAR"
PRICE_SOURCE_MANUAL_REFERENCE = "MANUAL_REFERENCE"
PRICE_SOURCE_BACKTEST_CLOSED_BAR = "BACKTEST_CLOSED_BAR"

LIMIT_PRICE_SOURCES = frozenset({PRICE_SOURCE_SIGNAL_LIMIT, PRICE_SOURCE_MANUAL_LIMIT})
MARKET_PRICE_SOURCES = frozenset(
    {
        PRICE_SOURCE_MARKET_TICKER,
        PRICE_SOURCE_MARKET_CLOSED_BAR,
        PRICE_SOURCE_MANUAL_REFERENCE,
        PRICE_SOURCE_BACKTEST_CLOSED_BAR,
    }
)
SUPPORTED_ORDER_TYPES = frozenset({"LIMIT", "MARKET"})
SUPPORTED_SETTLEMENTS = frozenset(SETTLEMENTS)
SUPPORTED_TIME_IN_FORCE = frozenset({"GTC"})


def order_type_value(value: Any) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise TradingError("ORDER_TYPE_REQUIRED")
    text = str(getattr(value, "value", value)).strip().upper()
    if text not in SUPPORTED_ORDER_TYPES:
        raise TradingError("UNSUPPORTED_ORDER_TYPE")
    return text


def price_source_for_order_type(order_type: str, source: Any) -> str:
    text = str(source or "").strip()
    if not text:
        raise TradingError("ORDER_PRICE_SOURCE_REQUIRED")
    allowed = LIMIT_PRICE_SOURCES if order_type == "LIMIT" else MARKET_PRICE_SOURCES
    if text not in allowed:
        raise TradingError("INVALID_ORDER_PRICE_SOURCE")
    return text


def _positive_decimal(value: Any, code: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (DecimalException, TypeError, ValueError):
        raise TradingError(code) from None
    if not amount.is_finite() or amount <= 0:
        raise TradingError(code)
    return amount


@dataclass(frozen=True)
class OrderSpec:
    symbol: str
    side: str
    order_type: str
    qty: Decimal
    price: Decimal
    price_source: str
    price_at_ms: int
    time_in_force: str
    book_id: str
    settlement: str = SETTLEMENT_QUOTE

    def to_command(
        self,
        *,
        command_id: str,
        account_id: str,
        base_asset: str,
        quote_asset: str,
        deployment_id: str,
        decision_id: str,
    ) -> PlaceOrderCommand:
        return PlaceOrderCommand(
            command_id=command_id,
            account_id=account_id,
            symbol=self.symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            side=self.side,
            qty=self.qty,
            price=self.price,
            deployment_id=deployment_id,
            order_type=self.order_type,
            time_in_force=self.time_in_force,
            decision_id=decision_id,
            book_id=self.book_id,
            price_source=self.price_source,
            price_at_ms=self.price_at_ms,
            settlement=self.settlement,
        )


def build_order_spec(
    *,
    symbol: str,
    side: str,
    order_type: Any,
    qty: Any,
    limit_price: Any,
    reference_price: Any,
    reference_price_source: str,
    reference_at_ms: int,
    decision_at_ms: int,
    time_in_force: str,
    book_id: str,
    price_source: str = "",
    settlement: str = "",
) -> OrderSpec:
    """The single constructor for an order specification.

    Missing fields are refused, never defaulted. ``price_source`` names the
    provenance of the price for the resolved order type; when it is empty the
    caller-specific default applies (``SIGNAL_LIMIT`` for a limit order,
    ``reference_price_source`` for a market order).
    """
    kind = order_type_value(order_type)
    if side not in {"BUY", "SELL"}:
        raise TradingError("UNSUPPORTED_SPOT_INTENT")
    if not isinstance(symbol, str) or not symbol.strip():
        raise TradingError("ORDER_SYMBOL_REQUIRED")
    quantity = _positive_decimal(qty, "INVALID_ORDER_AMOUNT")
    validate_strategy_book_id(book_id)
    if time_in_force not in SUPPORTED_TIME_IN_FORCE:
        raise TradingError("UNSUPPORTED_TIME_IN_FORCE")
    settlement_mode = str(settlement or SETTLEMENT_QUOTE).strip().upper()
    if settlement_mode not in SUPPORTED_SETTLEMENTS:
        raise TradingError("UNSUPPORTED_SETTLEMENT")
    if kind == "LIMIT":
        price = _positive_decimal(limit_price, "LIMIT_PRICE_REQUIRED")
        source = price_source_for_order_type(kind, price_source or PRICE_SOURCE_SIGNAL_LIMIT)
        at_ms = int(decision_at_ms if decision_at_ms is not None else -1)
    else:
        price = _positive_decimal(reference_price, "MARKET_REFERENCE_PRICE_REQUIRED")
        source = price_source_for_order_type(kind, price_source or reference_price_source)
        at_ms = int(reference_at_ms if reference_at_ms is not None else -1)
    if at_ms < 0:
        raise TradingError("ORDER_PRICE_TIME_REQUIRED")
    return OrderSpec(
        symbol=symbol,
        side=side,
        order_type=kind,
        qty=quantity,
        price=price,
        price_source=source,
        price_at_ms=at_ms,
        time_in_force=time_in_force,
        book_id=book_id,
        settlement=settlement_mode,
    )


def adopted_venue_command(
    *,
    command_id: str,
    account_id: str,
    symbol: str,
    base_asset: str,
    quote_asset: str,
    side: str,
    qty: Any,
    price: Any,
    order_type: str,
) -> PlaceOrderCommand:
    """Rebuild an order intent from venue evidence during recovery.

    This is not an admission: the venue told us the order exists, so the
    intent is reconstructed with no book and no price source. Both stay empty
    on purpose -- the strategy attribution is unknown until an operator
    settles it, and an unknown attribution must never be guessed.
    """
    return PlaceOrderCommand(
        command_id=command_id,
        account_id=account_id,
        symbol=symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
        side=side,
        qty=Decimal(str(qty)),
        price=Decimal(str(price)),
        deployment_id="recovered",
        order_type=str(order_type or ""),
        time_in_force="GTC",
        decision_id=command_id,
        book_id="",
        price_source="",
        price_at_ms=-1,
        settlement=SETTLEMENT_QUOTE,
    )


def validate_order_command(command: PlaceOrderCommand) -> None:
    """Validate the unified contract at every admission boundary."""
    kind = order_type_value(command.order_type)
    if command.side not in {"BUY", "SELL"}:
        raise TradingError("UNSUPPORTED_SPOT_INTENT")
    if not command.symbol or not command.base_asset or not command.quote_asset:
        raise TradingError("INVALID_INSTRUMENT")
    if command.base_asset == command.quote_asset:
        raise TradingError("INVALID_INSTRUMENT")
    _positive_decimal(command.qty, "INVALID_ORDER_AMOUNT")
    _positive_decimal(command.price, "INVALID_ORDER_AMOUNT")
    if command.time_in_force not in SUPPORTED_TIME_IN_FORCE:
        raise TradingError("UNSUPPORTED_TIME_IN_FORCE")
    validate_strategy_book_id(command.book_id)
    price_source_for_order_type(kind, command.price_source)
    if int(command.price_at_ms) < 0:
        raise TradingError("ORDER_PRICE_TIME_REQUIRED")
    if str(command.settlement or "").strip().upper() not in SUPPORTED_SETTLEMENTS:
        raise TradingError("UNSUPPORTED_SETTLEMENT")
