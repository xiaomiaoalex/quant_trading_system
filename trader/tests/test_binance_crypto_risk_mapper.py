from decimal import Decimal

from trader.adapters.binance.crypto_risk_mapper import (
    map_account_risk,
    map_exchange_info,
    map_leverage_brackets,
    map_mark_prices,
    map_open_orders,
    map_positions,
)
from trader.core.domain.models.crypto_risk import CryptoMarketType, MarginMode
from trader.core.domain.models.order import OrderSide


def test_map_exchange_info_symbol_filters_to_internal_spec() -> None:
    specs = map_exchange_info(
        {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {
                            "filterType": "LOT_SIZE",
                            "minQty": "0.001",
                            "maxQty": "100",
                            "stepSize": "0.001",
                        },
                        {
                            "filterType": "NOTIONAL",
                            "minNotional": "10",
                            "maxNotional": "1000000",
                        },
                    ],
                }
            ]
        },
        market_type=CryptoMarketType.USD_M_FUTURES,
    )

    spec = specs["BTCUSDT"]
    assert spec.symbol == "BTCUSDT"
    assert spec.base_asset == "BTC"
    assert spec.quote_asset == "USDT"
    assert spec.price_tick == Decimal("0.10")
    assert spec.qty_step == Decimal("0.001")
    assert spec.min_qty == Decimal("0.001")
    assert spec.max_qty == Decimal("100")
    assert spec.min_notional == Decimal("10")
    assert spec.max_notional == Decimal("1000000")


def test_map_leverage_brackets_uses_notional_and_maintenance_fields() -> None:
    brackets = map_leverage_brackets(
        [
            {
                "symbol": "BTCUSDT",
                "brackets": [
                    {
                        "notionalFloor": "0",
                        "notionalCap": "50000",
                        "initialLeverage": 20,
                        "maintMarginRatio": "0.004",
                        "cum": "0",
                    }
                ],
            }
        ]
    )

    bracket = brackets["BTCUSDT"][0]
    assert bracket.notional_floor == Decimal("0")
    assert bracket.notional_cap == Decimal("50000")
    assert bracket.initial_leverage == Decimal("20")
    assert bracket.maint_margin_ratio == Decimal("0.004")
    assert bracket.maint_amount == Decimal("0")


def test_map_open_orders_converts_binance_order_fields_to_internal_names() -> None:
    orders = map_open_orders(
        [
            {
                "clientOrderId": "cli-1",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "origQty": "0.5",
                "executedQty": "0.1",
                "price": "20000",
                "reduceOnly": False,
                "status": "NEW",
            }
        ]
    )

    order = orders[0]
    assert order.cl_ord_id == "cli-1"
    assert order.side == OrderSide.BUY
    assert order.qty == Decimal("0.5")
    assert order.filled_qty == Decimal("0.1")
    assert order.price == Decimal("20000")
    assert order.reduce_only is False
    assert not hasattr(order, "clientOrderId")
    assert not hasattr(order, "origQty")


def test_map_positions_and_mark_prices_to_internal_risk_dtos() -> None:
    positions = map_positions(
        [
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.25",
                "entryPrice": "19000",
                "markPrice": "20000",
                "leverage": "10",
                "marginType": "isolated",
                "liquidationPrice": "15000",
            }
        ]
    )
    marks = map_mark_prices(
        [
            {
                "symbol": "BTCUSDT",
                "markPrice": "20000",
            }
        ]
    )

    position = positions[0]
    assert position.qty == Decimal("0.25")
    assert position.mark_price == Decimal("20000")
    assert position.leverage == Decimal("10")
    assert position.margin_mode == MarginMode.ISOLATED
    assert position.liquidation_price == Decimal("15000")
    assert marks["BTCUSDT"] == Decimal("20000")


def test_map_account_risk_uses_futures_margin_balances() -> None:
    account = map_account_risk(
        {
            "totalWalletBalance": "1000",
            "availableBalance": "800",
            "totalMarginBalance": "1050",
            "totalInitialMargin": "120",
            "totalMaintMargin": "20",
        }
    )

    assert account.wallet_balance == Decimal("1000")
    assert account.available_balance == Decimal("800")
    assert account.margin_balance == Decimal("1050")
    assert account.equity == Decimal("1050")
    assert account.total_initial_margin == Decimal("120")
    assert account.total_maintenance_margin == Decimal("20")
