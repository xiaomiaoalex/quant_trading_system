from __future__ import annotations

from decimal import Decimal
from typing import Any

from trader.core.domain.models.crypto_risk import (
    CryptoAccountRisk,
    CryptoInstrumentSpec,
    CryptoMarketType,
    CryptoPositionRisk,
    LeverageBracket,
    MarginMode,
    OpenOrderRisk,
)
from trader.core.domain.models.order import OrderSide


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _symbol(value: Any) -> str:
    return str(value or "").upper().replace("-", "").replace("/", "")


def _filters_by_type(symbol_info: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("filterType", "")): item
        for item in symbol_info.get("filters", [])
        if isinstance(item, dict)
    }


def map_exchange_info(
    data: dict[str, Any],
    *,
    market_type: CryptoMarketType = CryptoMarketType.USD_M_FUTURES,
    symbols: set[str] | None = None,
) -> dict[str, CryptoInstrumentSpec]:
    requested = {_symbol(symbol) for symbol in symbols} if symbols else None
    specs: dict[str, CryptoInstrumentSpec] = {}

    for item in data.get("symbols", []):
        symbol = _symbol(item.get("symbol"))
        if not symbol:
            continue
        if requested is not None and symbol not in requested:
            continue

        filters = _filters_by_type(item)
        price_filter = filters.get("PRICE_FILTER", {})
        lot_size = filters.get("LOT_SIZE", {})
        notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
        min_notional = (
            notional.get("minNotional") or notional.get("notionalMin") or notional.get("notional")
        )

        specs[symbol] = CryptoInstrumentSpec(
            symbol=symbol,
            market_type=market_type,
            price_tick=_decimal(price_filter.get("tickSize")),
            qty_step=_decimal(lot_size.get("stepSize")),
            min_qty=_decimal(lot_size.get("minQty")),
            max_qty=(
                _decimal(lot_size.get("maxQty")) if lot_size.get("maxQty") is not None else None
            ),
            min_notional=_decimal(min_notional),
            max_notional=(
                _decimal(notional.get("maxNotional") or notional.get("notionalMax"))
                if (notional.get("maxNotional") or notional.get("notionalMax")) is not None
                else None
            ),
            base_asset=str(item.get("baseAsset", "")),
            quote_asset=str(item.get("quoteAsset", "USDT")),
        )

    return specs


def map_leverage_brackets(
    data: list[dict[str, Any]] | dict[str, Any]
) -> dict[str, list[LeverageBracket]]:
    items = data if isinstance(data, list) else [data]
    result: dict[str, list[LeverageBracket]] = {}

    for item in items:
        symbol = _symbol(item.get("symbol"))
        if not symbol:
            continue
        brackets: list[LeverageBracket] = []
        for bracket in item.get("brackets", []):
            brackets.append(
                LeverageBracket(
                    symbol=symbol,
                    notional_floor=_decimal(bracket.get("notionalFloor")),
                    notional_cap=_decimal(bracket.get("notionalCap")),
                    initial_leverage=_decimal(bracket.get("initialLeverage"), Decimal("1")),
                    maint_margin_ratio=_decimal(bracket.get("maintMarginRatio")),
                    maint_amount=_decimal(bracket.get("maintAmount") or bracket.get("cum")),
                )
            )
        result[symbol] = brackets

    return result


def map_account_risk(data: dict[str, Any]) -> CryptoAccountRisk:
    wallet_balance = _decimal(data.get("totalWalletBalance"))
    margin_balance = _decimal(data.get("totalMarginBalance"), wallet_balance)

    return CryptoAccountRisk(
        equity=margin_balance,
        available_balance=_decimal(data.get("availableBalance")),
        wallet_balance=wallet_balance,
        margin_balance=margin_balance,
        total_initial_margin=_decimal(data.get("totalInitialMargin")),
        total_maintenance_margin=_decimal(data.get("totalMaintMargin")),
    )


def _order_side(value: Any) -> OrderSide:
    raw = str(value or "").upper()
    if raw == "BUY":
        return OrderSide.BUY
    if raw == "SELL":
        return OrderSide.SELL
    raise ValueError(f"Unsupported Binance order side: {value}")


def map_open_orders(data: list[dict[str, Any]] | dict[str, Any]) -> list[OpenOrderRisk]:
    items = data if isinstance(data, list) else [data]
    result: list[OpenOrderRisk] = []

    for item in items:
        symbol = _symbol(item.get("symbol"))
        cl_ord_id = str(item.get("clientOrderId") or item.get("origClientOrderId") or "")
        if not symbol or not cl_ord_id:
            continue
        result.append(
            OpenOrderRisk(
                cl_ord_id=cl_ord_id,
                symbol=symbol,
                side=_order_side(item.get("side")),
                qty=_decimal(item.get("origQty")),
                filled_qty=_decimal(item.get("executedQty")),
                price=_decimal(item.get("price") or item.get("stopPrice")),
                reduce_only=bool(item.get("reduceOnly", False)),
                status=str(item.get("status") or "OPEN"),
            )
        )

    return result


def _margin_mode(value: Any) -> MarginMode:
    raw = str(value or "").lower()
    if raw == "isolated":
        return MarginMode.ISOLATED
    return MarginMode.CROSS


def map_positions(data: list[dict[str, Any]] | dict[str, Any]) -> list[CryptoPositionRisk]:
    items = data if isinstance(data, list) else [data]
    result: list[CryptoPositionRisk] = []

    for item in items:
        symbol = _symbol(item.get("symbol"))
        if not symbol:
            continue
        qty = _decimal(item.get("positionAmt"))
        result.append(
            CryptoPositionRisk(
                symbol=symbol,
                qty=qty,
                entry_price=_decimal(item.get("entryPrice")),
                mark_price=_decimal(item.get("markPrice")),
                leverage=_decimal(item.get("leverage"), Decimal("1")),
                margin_mode=_margin_mode(item.get("marginType")),
                liquidation_price=(
                    None
                    if item.get("liquidationPrice") in (None, "")
                    else _decimal(item.get("liquidationPrice"))
                ),
            )
        )

    return result


def map_mark_prices(data: list[dict[str, Any]] | dict[str, Any]) -> dict[str, Decimal]:
    items = data if isinstance(data, list) else [data]
    result: dict[str, Decimal] = {}

    for item in items:
        symbol = _symbol(item.get("symbol"))
        if not symbol:
            continue
        price = _decimal(item.get("markPrice") or item.get("price"))
        if price > 0:
            result[symbol] = price

    return result
