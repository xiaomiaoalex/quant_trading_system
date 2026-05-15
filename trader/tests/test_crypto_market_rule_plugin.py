"""
test_crypto_market_rule_plugin.py - P9.3 Crypto 市场规则插件单元测试
========================================================================
测试 CryptoMarketRulePlugin 的各项规则：
1. price_tick/qty_step 归一化
2. min_qty 检查
3. max_qty 检查
4. min_notional 检查
5. max_notional 检查
6. 缺失市场状态时 fail-closed
7. 不读取 A 股字段

参考: docs/INTERFACE_CONTRACTS.md 8.11.5 CryptoMarketRulePlugin 边界
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
from trader.core.domain.services.crypto_market_rule_plugin import (
    CryptoMarketRulePlugin,
    CryptoMarketRulePluginConfig,
)


def _default_metadata() -> dict:
    """默认 Crypto metadata"""
    return {
        "price_tick": "0.01",
        "qty_step": "0.001",
        "min_qty": "0.01",
        "min_notional": "10",
    }


def _make_snapshot(metadata: dict | None = None) -> MarketRiskSnapshot:
    return MarketRiskSnapshot(
        account=MarketAccountRisk(
            equity=Decimal("10000"),
            available_cash=Decimal("5000"),
            venue="binance",
            asset_class=AssetClass.CRYPTO,
            account_id="crypto_account",
        ),
        instrument_specs={
            "BTCUSDT": MarketInstrumentSpec(
                symbol="BTCUSDT",
                venue="binance",
                asset_class=AssetClass.CRYPTO,
                price_tick=Decimal("0.01"),
                qty_step=Decimal("0.001"),
                min_qty=Decimal("0.01"),
                min_notional=Decimal("10"),
            ),
        },
        positions=[],
        open_orders=[],
        risk_prices={"BTCUSDT": Decimal("50000.00")},
        metadata=metadata or {},
    )


def _make_intent(
    qty: Decimal = Decimal("0.1"),
    price: Decimal = Decimal("50000.00"),
    side: OrderSide = OrderSide.BUY,
    metadata: dict | None = None,
) -> MarketRuleIntent:
    """创建 intent，自动注入默认 metadata"""
    merged_metadata = _default_metadata()
    if metadata is not None:
        merged_metadata.update(metadata)
    return MarketRuleIntent(
        symbol="BTCUSDT",
        venue="binance",
        asset_class=AssetClass.CRYPTO,
        side=side,
        qty=qty,
        price=price,
        timestamp_ms=1234567890000,
        account_id="crypto_account",
        metadata=merged_metadata,
    )


class TestSupports:
    def test_supports_binance_crypto(self):
        plugin = CryptoMarketRulePlugin()
        assert plugin.supports(AssetClass.CRYPTO, "binance") is True

    def test_supports_okx_crypto(self):
        plugin = CryptoMarketRulePlugin()
        assert plugin.supports(AssetClass.CRYPTO, "okx") is True

    def test_supports_bybit_crypto(self):
        plugin = CryptoMarketRulePlugin()
        assert plugin.supports(AssetClass.CRYPTO, "bybit") is True

    def test_supports_cn_stock_rejected(self):
        plugin = CryptoMarketRulePlugin()
        assert plugin.supports(AssetClass.CN_STOCK, "sse") is False

    def test_supports_unknown_venue_rejected(self):
        plugin = CryptoMarketRulePlugin()
        assert plugin.supports(AssetClass.CRYPTO, "unknown") is False


class TestQtyStepNormalization:
    def test_qty_multiple_of_step_approved(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("0.100"))
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True
        assert result.normalized_qty == Decimal("0.100")

    def test_qty_not_multiple_of_step_adjusted(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("0.1005"))
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True
        assert result.normalized_qty == Decimal("0.100")

    def test_price_multiple_of_tick_approved(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("0.100"), price=Decimal("50000.00"))
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True
        assert result.normalized_price == Decimal("50000.00")

    def test_price_not_multiple_of_tick_adjusted(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("0.100"), price=Decimal("50000.005"))
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True
        assert result.normalized_price == Decimal("50000.00")


class TestMinQty:
    def test_qty_above_min_approved(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("0.1"), metadata={"min_qty": "0.01"})
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_qty_below_min_rejected(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("0.005"), metadata={"min_qty": "0.01"})
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "MIN_QTY" for v in result.violations)

    def test_qty_at_min_approved(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("0.01"), metadata={"min_qty": "0.01"})
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True


class TestMaxQty:
    def test_qty_below_max_approved(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("1"), metadata={"max_qty": "10"})
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_qty_above_max_rejected(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("15"), metadata={"max_qty": "10"})
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "MAX_QTY" for v in result.violations)

    def test_qty_at_max_approved(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("10"), metadata={"max_qty": "10"})
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True


class TestMinNotional:
    def test_notional_above_min_approved(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("0.01"),
            price=Decimal("50000"),
            metadata={"min_qty": "0.001", "min_notional": "10"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_notional_below_min_rejected(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("0.001"),
            price=Decimal("0.01"),
            metadata={"min_qty": "0.001", "min_notional": "10"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "MIN_NOTIONAL" for v in result.violations)

    def test_notional_at_min_approved(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("0.01"),
            price=Decimal("1000"),
            metadata={"min_qty": "0.001", "min_notional": "10"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True


class TestMaxNotional:
    def test_notional_below_max_approved(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("0.1"),
            price=Decimal("50000"),
            metadata={"max_notional": "1000000"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_notional_above_max_rejected(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("1"),
            price=Decimal("50000"),
            metadata={"max_notional": "10000"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "MAX_NOTIONAL" for v in result.violations)


class TestInvalidInput:
    def test_negative_qty_rejected(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("-0.1"))
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "INVALID_QTY" for v in result.violations)

    def test_negative_price_rejected(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("0.1"), price=Decimal("-50000"))
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "INVALID_PRICE" for v in result.violations)

    def test_zero_price_tick_rejected(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("0.1"), metadata={"price_tick": "0", "qty_step": "0.001"})
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "INVALID_INSTRUMENT_SPEC" for v in result.violations)

    def test_zero_qty_step_rejected(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("0.1"), metadata={"price_tick": "0.01", "qty_step": "0"})
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "INVALID_INSTRUMENT_SPEC" for v in result.violations)


class TestMissingMarketState:
    def test_missing_price_tick_fail_closed(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("0.1"))
        del intent.metadata["price_tick"]
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "MARKET_STATE_MISSING" for v in result.violations)

    def test_missing_qty_step_fail_closed(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("0.1"))
        del intent.metadata["qty_step"]
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "MARKET_STATE_MISSING" for v in result.violations)

    def test_require_market_state_false_uses_default(self):
        plugin = CryptoMarketRulePlugin(
            config=CryptoMarketRulePluginConfig(require_market_state=False)
        )
        intent = _make_intent(qty=Decimal("0.1"))
        del intent.metadata["price_tick"]
        del intent.metadata["qty_step"]
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True


class TestNoAShareFields:
    def test_sellable_qty_not_read(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("0.1"),
            side=OrderSide.SELL,
            metadata={"sellable_qty": "0"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_limit_up_not_read(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("0.1"),
            metadata={"limit_up": "999999"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_trading_phase_not_read(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(
            qty=Decimal("0.1"),
            metadata={"trading_phase": "CLOSED"},
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True


class TestOrderSideCompatibility:
    def test_order_side_from_market_rules(self):
        plugin = CryptoMarketRulePlugin()
        intent = _make_intent(qty=Decimal("0.1"), side=OrderSide.BUY)
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_order_side_from_order_module(self):
        plugin = CryptoMarketRulePlugin()
        intent = MarketRuleIntent(
            symbol="BTCUSDT",
            venue="binance",
            asset_class=AssetClass.CRYPTO,
            side=OrderSideFromOrder.SELL,
            qty=Decimal("0.1"),
            price=Decimal("50000"),
            timestamp_ms=1234567890000,
            account_id="crypto_account",
            metadata={
                "price_tick": "0.01",
                "qty_step": "0.001",
                "min_qty": "0.01",
                "min_notional": "10",
            },
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is True

    def test_unknown_side_fail_closed(self):
        from dataclasses import dataclass

        @dataclass
        class UnknownSide:
            value: str = "INVALID_SIDE"

        plugin = CryptoMarketRulePlugin()
        unknown_side = UnknownSide()
        intent = MarketRuleIntent(
            symbol="BTCUSDT",
            venue="binance",
            asset_class=AssetClass.CRYPTO,
            side=unknown_side,
            qty=Decimal("0.1"),
            price=Decimal("50000"),
            timestamp_ms=1234567890000,
            account_id="crypto_account",
            metadata={
                "price_tick": "0.01",
                "qty_step": "0.001",
                "min_qty": "0.01",
                "min_notional": "10",
            },
        )
        snapshot = _make_snapshot()

        result = plugin.check(intent, snapshot)

        assert result.passed is False
        assert any(v.code == "INVALID_SIDE" for v in result.violations)
