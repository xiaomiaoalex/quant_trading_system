"""
test_china_stock_market_rule_plugin.py - P9.2 A 股市场规则插件单元测试
========================================================================
测试 ChinaStockMarketRulePlugin 的各项规则：
1. 100 股手数：100 通过，101 拒绝
2. T+1：sellable_qty=0 时卖出拒绝
3. 涨跌停：超出上下限拒绝
4. 停牌拒绝
5. 不允许做空时卖出超过可卖数量拒绝
6. 非交易阶段拒绝
7. 关键市场状态缺失时 fail-closed

参考: docs/INTERFACE_CONTRACTS.md 8.11.4 ChinaStockMarketRulePlugin 边界
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trader.core.domain.models.market_risk import (
    AssetClass,
    MarketAccountRisk,
    MarketInstrumentSpec,
    MarketRiskSnapshot,
)
from trader.core.domain.models.market_rules import MarketRuleIntent, OrderSide
from trader.core.domain.models.order import OrderSide as OrderSideFromOrder
from trader.core.domain.services.china_stock_market_rule_plugin import (
    ChinaStockMarketRulePlugin,
    ChinaStockMarketRulePluginConfig,
    ChinaStockTradingPhase,
)


def _default_buy_metadata() -> dict:
    """默认买入 metadata（包含涨跌停等必填字段）"""
    return {
        "limit_up": "15.00",
        "limit_down": "5.00",
        "trading_phase": ChinaStockTradingPhase.CONTINUOUS_AUCTION,
        "is_suspended": False,
        "allow_short": False,
    }


def _make_snapshot(metadata: dict | None = None) -> MarketRiskSnapshot:
    return MarketRiskSnapshot(
        account=MarketAccountRisk(
            equity=Decimal("100000"),
            available_cash=Decimal("50000"),
            venue="sse",
            asset_class=AssetClass.CN_STOCK,
            account_id="cn_account",
        ),
        instrument_specs={
            "600000": MarketInstrumentSpec(
                symbol="600000",
                venue="sse",
                asset_class=AssetClass.CN_STOCK,
                price_tick=Decimal("0.01"),
                qty_step=Decimal("100"),
                min_qty=Decimal("100"),
                min_notional=Decimal("100"),
            ),
        },
        positions=[],
        open_orders=[],
        risk_prices={"600000": Decimal("10.00")},
        metadata=metadata or {},
    )


def _make_intent(
    qty: Decimal = Decimal("100"),
    price: Decimal = Decimal("10.00"),
    side: OrderSide = OrderSide.BUY,
    metadata: dict | None = None,
) -> MarketRuleIntent:
    """创建 intent，自动注入默认买入 metadata"""
    merged_metadata = _default_buy_metadata()
    if metadata is not None:
        merged_metadata.update(metadata)
    return MarketRuleIntent(
        symbol="600000",
        venue="sse",
        asset_class=AssetClass.CN_STOCK,
        side=side,
        qty=qty,
        price=price,
        timestamp_ms=1234567890000,
        account_id="cn_account",
        metadata=merged_metadata,
    )


class TestLotSize:
    def test_lot_size_100_approved(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(qty=Decimal("100"))
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True
        assert result.normalized_qty == Decimal("100")

    def test_lot_size_200_approved(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(qty=Decimal("200"))
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True
        assert result.normalized_qty == Decimal("200")

    def test_lot_size_101_rejected(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(qty=Decimal("101"))
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert len(result.violations) >= 1
        assert any(v.code == "LOT_SIZE" for v in result.violations)

    def test_lot_size_50_rejected(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(qty=Decimal("50"))
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "LOT_SIZE" for v in result.violations)


class TestT1SellLimit:
    def test_t1_sell_with_sellable_qty_approved(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            side=OrderSide.SELL,
            metadata={"sellable_qty": "1000"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_t1_sell_without_sellable_qty_rejected(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            side=OrderSide.SELL,
            metadata={"sellable_qty": "0"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "T1_SELL_LIMIT" for v in result.violations)

    def test_t1_sell_exceeds_sellable_qty_rejected(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("500"),
            side=OrderSide.SELL,
            metadata={"sellable_qty": "300"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "T1_SELL_LIMIT" for v in result.violations)

    def test_t1_sell_with_sellable_qty_exact_approved(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("300"),
            side=OrderSide.SELL,
            metadata={"sellable_qty": "300"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True


class TestPriceLimit:
    def test_price_within_limit_approved(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            price=Decimal("10.00"),
            metadata={"limit_up": "12.00", "limit_down": "8.00"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_price_at_limit_up_approved(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            price=Decimal("12.00"),
            metadata={"limit_up": "12.00", "limit_down": "8.00"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_price_at_limit_down_approved(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            price=Decimal("8.00"),
            metadata={"limit_up": "12.00", "limit_down": "8.00"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_price_above_limit_up_rejected(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            price=Decimal("12.50"),
            metadata={"limit_up": "12.00", "limit_down": "8.00"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "PRICE_LIMIT_UP" for v in result.violations)

    def test_price_below_limit_down_rejected(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            price=Decimal("7.50"),
            metadata={"limit_up": "12.00", "limit_down": "8.00"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "PRICE_LIMIT_DOWN" for v in result.violations)


class TestSuspension:
    def test_suspended_rejected(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(qty=Decimal("100"), metadata={"is_suspended": True})
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "SUSPENDED" for v in result.violations)

    def test_not_suspended_approved(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(qty=Decimal("100"), metadata={"is_suspended": False})
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True


class TestNoShort:
    def test_no_short_sell_without_shares_rejected(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            side=OrderSide.SELL,
            metadata={"sellable_qty": "0", "allow_short": False},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "NO_SHORT" for v in result.violations)

    def test_allow_short_with_shares_approved(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            side=OrderSide.SELL,
            metadata={"sellable_qty": "100", "allow_short": False},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_allow_short_false_string_parsed_correctly(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            side=OrderSide.SELL,
            metadata={"sellable_qty": "0", "allow_short": "False"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "NO_SHORT" for v in result.violations)


class TestTradingPhase:
    def test_continuous_auction_approved(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            metadata={"trading_phase": ChinaStockTradingPhase.CONTINUOUS_AUCTION},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_call_auction_close_approved(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            metadata={"trading_phase": ChinaStockTradingPhase.CALL_AUCTION_CLOSE},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_closed_rejected(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            metadata={"trading_phase": ChinaStockTradingPhase.CLOSED},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "TRADING_PHASE" for v in result.violations)

    def test_suspended_phase_rejected(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            metadata={"trading_phase": ChinaStockTradingPhase.SUSPENDED},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "TRADING_PHASE" for v in result.violations)

    def test_call_auction_open_rejected(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            metadata={"trading_phase": ChinaStockTradingPhase.CALL_AUCTION_OPEN},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "TRADING_PHASE" for v in result.violations)


class TestSupports:
    def test_supports_sse_cn_stock(self):
        plugin = ChinaStockMarketRulePlugin()
        assert plugin.supports(AssetClass.CN_STOCK, "sse") is True

    def test_supports_szse_cn_stock(self):
        plugin = ChinaStockMarketRulePlugin()
        assert plugin.supports(AssetClass.CN_STOCK, "szse") is True

    def test_supports_bjse_cn_stock(self):
        plugin = ChinaStockMarketRulePlugin()
        assert plugin.supports(AssetClass.CN_STOCK, "bjse") is True

    def test_supports_crypto_rejected(self):
        plugin = ChinaStockMarketRulePlugin()
        assert plugin.supports(AssetClass.CRYPTO, "binance") is False

    def test_supports_unknown_venue_rejected(self):
        plugin = ChinaStockMarketRulePlugin()
        assert plugin.supports(AssetClass.CN_STOCK, "unknown") is False


class TestOrderSideCompatibility:
    def test_order_side_from_market_rules(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            side=OrderSide.SELL,
            metadata={"sellable_qty": "1000"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_order_side_from_order_module(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = MarketRuleIntent(
            symbol="600000",
            venue="sse",
            asset_class=AssetClass.CN_STOCK,
            side=OrderSideFromOrder.SELL,
            qty=Decimal("100"),
            price=Decimal("10.00"),
            timestamp_ms=1234567890000,
            account_id="cn_account",
            metadata={
                "sellable_qty": "1000",
                "limit_up": "15.00",
                "limit_down": "5.00",
                "trading_phase": ChinaStockTradingPhase.CONTINUOUS_AUCTION,
                "is_suspended": False,
            },
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True


class TestMissingMarketStateFailClosed:
    def test_missing_limit_up_fail_closed(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(qty=Decimal("100"), metadata={"limit_down": "5.00"})
        del intent.metadata["limit_up"]
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "MARKET_STATE_MISSING" for v in result.violations)

    def test_missing_limit_down_fail_closed(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(qty=Decimal("100"), metadata={"limit_up": "15.00"})
        del intent.metadata["limit_down"]
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "MARKET_STATE_MISSING" for v in result.violations)

    def test_missing_trading_phase_fail_closed(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(qty=Decimal("100"))
        del intent.metadata["trading_phase"]
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "MARKET_STATE_MISSING" for v in result.violations)

    def test_allow_short_false_string_not_parsed_as_true(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = MarketRuleIntent(
            symbol="600000",
            venue="sse",
            asset_class=AssetClass.CN_STOCK,
            side=OrderSide.SELL,
            qty=Decimal("100"),
            price=Decimal("10.00"),
            timestamp_ms=1234567890000,
            account_id="cn_account",
            metadata={
                "sellable_qty": "0",
                "allow_short": "False",
                "limit_up": "15.00",
                "limit_down": "5.00",
                "trading_phase": ChinaStockTradingPhase.CONTINUOUS_AUCTION,
                "is_suspended": False,
            },
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "NO_SHORT" for v in result.violations)

    def test_unknown_side_fail_closed(self):
        from dataclasses import dataclass

        @dataclass
        class UnknownSide:
            value: str = "INVALID_SIDE"

        plugin = ChinaStockMarketRulePlugin()
        unknown_side = UnknownSide()
        intent = MarketRuleIntent(
            symbol="600000",
            venue="sse",
            asset_class=AssetClass.CN_STOCK,
            side=unknown_side,
            qty=Decimal("100"),
            price=Decimal("10.00"),
            timestamp_ms=1234567890000,
            account_id="cn_account",
            metadata={
                "limit_up": "15.00",
                "limit_down": "5.00",
                "sellable_qty": "0",
                "trading_phase": ChinaStockTradingPhase.CONTINUOUS_AUCTION,
                "is_suspended": False,
            },
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "INVALID_SIDE" for v in result.violations)


class TestRequiredBoolFields:
    def test_missing_is_suspended_fail_closed(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(qty=Decimal("100"))
        del intent.metadata["is_suspended"]
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "MARKET_STATE_MISSING" for v in result.violations)

    def test_invalid_is_suspended_string_fail_closed(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(qty=Decimal("100"), metadata={"is_suspended": "maybe"})
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "INVALID_BOOL" for v in result.violations)

    def test_invalid_is_suspended_numeric_fail_closed(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(qty=Decimal("100"), metadata={"is_suspended": 1.5})
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "INVALID_BOOL" for v in result.violations)

    def test_invalid_allow_short_fail_closed(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("100"),
            side=OrderSide.SELL,
            metadata={"sellable_qty": "0", "allow_short": "unknown"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "INVALID_BOOL" for v in result.violations)


class TestNormalizedQtyInViolation:
    def test_lot_size_violation_returns_normalized_qty(self):
        plugin = ChinaStockMarketRulePlugin()
        intent = _make_intent(qty=Decimal("101"))
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "LOT_SIZE" for v in result.violations)
        assert result.normalized_qty == Decimal("100")


class TestDefaultAllowShort:
    def test_default_allow_short_true_approved(self):
        plugin = ChinaStockMarketRulePlugin(
            config=ChinaStockMarketRulePluginConfig(default_allow_short=True)
        )
        intent = MarketRuleIntent(
            symbol="600000",
            venue="sse",
            asset_class=AssetClass.CN_STOCK,
            side=OrderSide.SELL,
            qty=Decimal("100"),
            price=Decimal("10.00"),
            timestamp_ms=1234567890000,
            account_id="cn_account",
            metadata={
                "sellable_qty": "0",
                "limit_up": "15.00",
                "limit_down": "5.00",
                "trading_phase": ChinaStockTradingPhase.CONTINUOUS_AUCTION,
                "is_suspended": False,
            },
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_default_allow_short_false_rejected(self):
        plugin = ChinaStockMarketRulePlugin(
            config=ChinaStockMarketRulePluginConfig(default_allow_short=False)
        )
        intent = MarketRuleIntent(
            symbol="600000",
            venue="sse",
            asset_class=AssetClass.CN_STOCK,
            side=OrderSide.SELL,
            qty=Decimal("100"),
            price=Decimal("10.00"),
            timestamp_ms=1234567890000,
            account_id="cn_account",
            metadata={
                "sellable_qty": "0",
                "limit_up": "15.00",
                "limit_down": "5.00",
                "trading_phase": ChinaStockTradingPhase.CONTINUOUS_AUCTION,
                "is_suspended": False,
            },
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "NO_SHORT" for v in result.violations)


class TestRequireMarketStateFalse:
    def test_missing_trading_phase_not_rejected(self):
        plugin = ChinaStockMarketRulePlugin(
            config=ChinaStockMarketRulePluginConfig(require_market_state=False)
        )
        intent = _make_intent(qty=Decimal("100"))
        del intent.metadata["trading_phase"]
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_missing_is_suspended_not_rejected(self):
        plugin = ChinaStockMarketRulePlugin(
            config=ChinaStockMarketRulePluginConfig(require_market_state=False)
        )
        intent = _make_intent(qty=Decimal("100"))
        del intent.metadata["is_suspended"]
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True
