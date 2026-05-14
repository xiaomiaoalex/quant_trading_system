"""
test_market_rule_engine.py - P9.1 市场无关规则引擎单元测试
===========================================================
测试 MarketRuleEngine 的 fail-closed 行为：
1. 无插件默认 fail-closed
2. supports() 抛异常 fail-closed
3. check() 抛异常 fail-closed
4. 多插件中一个 reject 则整体 reject
5. MarketRuleIntent 能兼容既有 OrderSide

参考: docs/INTERFACE_CONTRACTS.md 8.11 P9 市场规则与 EventDrivenRiskReplay 契约冻结
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

from trader.core.domain.models.market_risk import (
    AssetClass,
    MarketAccountRisk,
    MarketInstrumentSpec,
    MarketRiskSnapshot,
)
from trader.core.domain.models.market_rules import (
    MarketRuleCheckResult,
    MarketRuleIntent,
    MarketRulePlugin,
    MarketRuleViolation,
    OrderSide,
)
from trader.core.domain.models.order import OrderSide as OrderSideFromOrder
from trader.core.domain.services.market_rule_engine import MarketRuleEngine, MarketRuleEngineConfig


@dataclass(frozen=True, slots=True)
class MockPlugin:
    name: str
    supported_asset_class: AssetClass
    supported_venue: str
    result: MarketRuleCheckResult
    supports_raises: Exception | None = None
    check_raises: Exception | None = None

    def supports(self, asset_class: AssetClass, venue: str) -> bool:
        if self.supports_raises:
            raise self.supports_raises
        return asset_class == self.supported_asset_class and venue == self.supported_venue

    def check(
        self,
        intent: MarketRuleIntent,
        snapshot: MarketRiskSnapshot,
    ) -> MarketRuleCheckResult:
        if self.check_raises:
            raise self.check_raises
        return self.result


def _make_snapshot() -> MarketRiskSnapshot:
    return MarketRiskSnapshot(
        account=MarketAccountRisk(
            equity=Decimal("10000"),
            available_cash=Decimal("5000"),
            venue="test",
            asset_class=AssetClass.CRYPTO,
            account_id="test_account",
        ),
        instrument_specs={
            "BTCUSDT": MarketInstrumentSpec(
                symbol="BTCUSDT",
                venue="binance",
                asset_class=AssetClass.CRYPTO,
                price_tick=Decimal("0.01"),
                qty_step=Decimal("0.00001"),
                min_qty=Decimal("0.00001"),
                min_notional=Decimal("10"),
            ),
        },
        positions=[],
        open_orders=[],
        risk_prices={"BTCUSDT": Decimal("50000")},
    )


def _make_intent(
    asset_class: AssetClass = AssetClass.CRYPTO, side: OrderSide = OrderSide.BUY
) -> MarketRuleIntent:
    return MarketRuleIntent(
        symbol="BTCUSDT",
        venue="binance",
        asset_class=asset_class,
        side=side,
        qty=Decimal("0.1"),
        price=Decimal("50000"),
        timestamp_ms=1234567890000,
        account_id="test_account",
    )


class TestNoPlugin:
    def test_no_plugins_fail_closed(self):
        engine = MarketRuleEngine(plugins=[], config=MarketRuleEngineConfig())
        intent = _make_intent()
        snapshot = _make_snapshot()

        result = engine.check(intent, snapshot)

        assert result.passed is False
        assert len(result.violations) == 1
        assert "FAIL_CLOSED" in result.violations[0].code
        assert "No plugin" in result.violations[0].message

    def test_no_plugins_with_fail_closed_false(self):
        engine = MarketRuleEngine(
            plugins=[],
            config=MarketRuleEngineConfig(fail_closed_on_no_plugin=False),
        )
        intent = _make_intent()
        snapshot = _make_snapshot()

        result = engine.check(intent, snapshot)

        assert result.passed is True
        assert result.details.get("no_plugin") is True


class TestSupportsException:
    def test_supports_exception_fail_closed(self):
        plugin = MockPlugin(
            name="TestPlugin",
            supported_asset_class=AssetClass.CRYPTO,
            supported_venue="binance",
            result=MarketRuleCheckResult.approve(Decimal("0.1"), Decimal("50000")),
            supports_raises=RuntimeError("supports broken"),
        )
        engine = MarketRuleEngine(plugins=[plugin], config=MarketRuleEngineConfig())
        intent = _make_intent()
        snapshot = _make_snapshot()

        result = engine.check(intent, snapshot)

        assert result.passed is False
        assert len(result.violations) == 1
        assert "supports" in result.violations[0].message.lower()
        assert result.violations[0].code == "FAIL_CLOSED"


class TestCheckException:
    def test_check_exception_fail_closed(self):
        plugin = MockPlugin(
            name="TestPlugin",
            supported_asset_class=AssetClass.CRYPTO,
            supported_venue="binance",
            result=MarketRuleCheckResult.approve(Decimal("0.1"), Decimal("50000")),
            check_raises=RuntimeError("check broken"),
        )
        engine = MarketRuleEngine(plugins=[plugin], config=MarketRuleEngineConfig())
        intent = _make_intent()
        snapshot = _make_snapshot()

        result = engine.check(intent, snapshot)

        assert result.passed is False
        assert "check" in result.violations[0].message.lower()
        assert result.details.get("fail_closed") is True


class TestOneRejectBlocksAll:
    def test_one_plugin_reject_blocks(self):
        reject_plugin = MockPlugin(
            name="RejectPlugin",
            supported_asset_class=AssetClass.CRYPTO,
            supported_venue="binance",
            result=MarketRuleCheckResult.reject(
                violations=[
                    MarketRuleViolation(
                        code="REJECT_RULE",
                        message="rejected by reject plugin",
                        field="qty",
                        expected="< 10",
                        actual="100",
                    )
                ],
            ),
        )
        approve_plugin = MockPlugin(
            name="ApprovePlugin",
            supported_asset_class=AssetClass.CRYPTO,
            supported_venue="binance",
            result=MarketRuleCheckResult.approve(Decimal("0.1"), Decimal("50000")),
        )
        engine = MarketRuleEngine(
            plugins=[approve_plugin, reject_plugin],
            config=MarketRuleEngineConfig(),
        )
        intent = _make_intent()
        snapshot = _make_snapshot()

        result = engine.check(intent, snapshot)

        assert result.passed is False
        assert result.violations[0].code == "REJECT_RULE"

    def test_all_plugins_approve(self):
        plugins = [
            MockPlugin(
                name=f"Plugin{i}",
                supported_asset_class=AssetClass.CRYPTO,
                supported_venue="binance",
                result=MarketRuleCheckResult.approve(
                    normalized_qty=Decimal(str(0.1 - i * 0.01)),
                    normalized_price=Decimal(str(50000 - i * 100)),
                ),
            )
            for i in range(3)
        ]
        engine = MarketRuleEngine(plugins=plugins, config=MarketRuleEngineConfig())
        intent = _make_intent()
        snapshot = _make_snapshot()

        result = engine.check(intent, snapshot)

        assert result.passed is True
        assert result.normalized_qty < Decimal("0.1")
        assert result.normalized_price < Decimal("50000")


class TestOrderSideCompatibility:
    def test_order_side_from_order_module(self):
        intent = MarketRuleIntent(
            symbol="BTCUSDT",
            venue="binance",
            asset_class=AssetClass.CRYPTO,
            side=OrderSideFromOrder.SELL,
            qty=Decimal("0.1"),
            price=Decimal("50000"),
        )

        assert intent.side is OrderSide.SELL

    def test_order_side_from_market_rules(self):
        intent = MarketRuleIntent(
            symbol="BTCUSDT",
            venue="binance",
            asset_class=AssetClass.CRYPTO,
            side=OrderSide.SELL,
            qty=Decimal("0.1"),
            price=Decimal("50000"),
        )

        assert intent.side is OrderSide.SELL
        assert intent.side == OrderSideFromOrder.SELL


class TestNormalizedQuantityClipping:
    def test_normalized_qty_takes_minimum(self):
        plugin1 = MockPlugin(
            name="Plugin1",
            supported_asset_class=AssetClass.CRYPTO,
            supported_venue="binance",
            result=MarketRuleCheckResult.approve(
                normalized_qty=Decimal("0.1"),
                normalized_price=Decimal("50000"),
            ),
        )
        plugin2 = MockPlugin(
            name="Plugin2",
            supported_asset_class=AssetClass.CRYPTO,
            supported_venue="binance",
            result=MarketRuleCheckResult.approve(
                normalized_qty=Decimal("0.05"),
                normalized_price=Decimal("49000"),
            ),
        )
        engine = MarketRuleEngine(
            plugins=[plugin1, plugin2],
            config=MarketRuleEngineConfig(),
        )
        intent = _make_intent()
        snapshot = _make_snapshot()

        result = engine.check(intent, snapshot)

        assert result.passed is True
        assert result.normalized_qty == Decimal("0.05")
        assert result.normalized_price == Decimal("49000")


class TestPluginSelection:
    def test_venue_mismatch_no_matching(self):
        plugin = MockPlugin(
            name="BinancePlugin",
            supported_asset_class=AssetClass.CRYPTO,
            supported_venue="binance",
            result=MarketRuleCheckResult.approve(Decimal("0.1"), Decimal("50000")),
        )
        engine = MarketRuleEngine(
            plugins=[plugin],
            config=MarketRuleEngineConfig(),
        )
        intent = MarketRuleIntent(
            symbol="BTCUSDT",
            venue="sse",
            asset_class=AssetClass.CRYPTO,
            side=OrderSide.BUY,
            qty=Decimal("0.1"),
            price=Decimal("50000"),
        )
        snapshot = _make_snapshot()

        result = engine.check(intent, snapshot)

        assert result.passed is False
        assert "No plugin" in result.violations[0].message

    def test_asset_class_mismatch_no_matching(self):
        plugin = MockPlugin(
            name="CryptoPlugin",
            supported_asset_class=AssetClass.CRYPTO,
            supported_venue="binance",
            result=MarketRuleCheckResult.approve(Decimal("0.1"), Decimal("50000")),
        )
        engine = MarketRuleEngine(
            plugins=[plugin],
            config=MarketRuleEngineConfig(),
        )
        intent = _make_intent(asset_class=AssetClass.CN_STOCK)
        snapshot = _make_snapshot()

        result = engine.check(intent, snapshot)

        assert result.passed is False
        assert "No plugin" in result.violations[0].message
