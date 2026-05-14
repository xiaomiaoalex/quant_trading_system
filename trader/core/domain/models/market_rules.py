"""
market_rules.py - 市场无关规则接口定义
=======================================
P9 核心：市场无关层只定义 pre-trade rule contract、插件接口、结果格式和 fail-closed 语义。
A 股规则、Crypto 规则由各自 specialization plugin 实现，不污染通用层。

参考: docs/INTERFACE_CONTRACTS.md 8.11 P9 市场规则与 EventDrivenRiskReplay 契约冻结
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, TypeAlias

from trader.core.domain.models.market_risk import AssetClass, MarketRiskSnapshot
from trader.core.domain.models.order import OrderSide as _OrderSide
from trader.core.domain.models.order import OrderType as _OrderType

OrderSide: TypeAlias = _OrderSide
OrderType: TypeAlias = _OrderType


@dataclass(slots=True)
class MarketRuleIntent:
    """
    规则检查输入 - 市场无关抽象

    不包含任何 A 股或交易所专属固定字段。
    市场专属字段统一放在 metadata 中，由 specialization plugin 读取。

    参考: docs/INTERFACE_CONTRACTS.md 8.11.1 MarketRuleIntent
    """

    symbol: str
    venue: str
    asset_class: AssetClass
    side: OrderSide
    qty: Decimal
    price: Decimal
    order_type: OrderType = OrderType.MARKET
    timestamp_ms: int = 0
    account_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbol = self.symbol.upper().strip()
        self.venue = self.venue.lower().strip()
        if isinstance(self.qty, (int, float)):
            object.__setattr__(self, "qty", Decimal(str(self.qty)))
        if isinstance(self.price, (int, float)):
            object.__setattr__(self, "price", Decimal(str(self.price)))


@dataclass(frozen=True, slots=True)
class MarketRuleViolation:
    """
    规则违规项

    参考: docs/INTERFACE_CONTRACTS.md 8.11.2 MarketRuleCheckResult
    """

    code: str
    message: str
    field: str = ""
    expected: str = ""
    actual: str = ""


@dataclass(frozen=True, slots=True)
class MarketRuleCheckResult:
    """
    规则检查结果

    插件异常或缺少必要 snapshot 时必须返回 passed=false，不得 fail-open。

    参考: docs/INTERFACE_CONTRACTS.md 8.11.2 MarketRuleCheckResult
    """

    passed: bool
    violations: tuple[MarketRuleViolation, ...] = field(default_factory=tuple)
    normalized_qty: Decimal = Decimal("0")
    normalized_price: Decimal = Decimal("0")
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def approve(
        cls,
        normalized_qty: Decimal,
        normalized_price: Decimal,
        details: dict[str, Any] | None = None,
    ) -> MarketRuleCheckResult:
        return cls(
            passed=True,
            violations=(),
            normalized_qty=normalized_qty,
            normalized_price=normalized_price,
            details=details or {},
        )

    @classmethod
    def reject(
        cls,
        violations: list[MarketRuleViolation] | tuple[MarketRuleViolation, ...],
        normalized_qty: Decimal = Decimal("0"),
        normalized_price: Decimal = Decimal("0"),
        details: dict[str, Any] | None = None,
    ) -> MarketRuleCheckResult:
        if isinstance(violations, list):
            violations = tuple(violations)
        return cls(
            passed=False,
            violations=violations,
            normalized_qty=normalized_qty,
            normalized_price=normalized_price,
            details=details or {},
        )

    @classmethod
    def fail_closed(
        cls,
        reason: str,
        normalized_qty: Decimal = Decimal("0"),
        normalized_price: Decimal = Decimal("0"),
    ) -> MarketRuleCheckResult:
        return cls(
            passed=False,
            violations=(
                MarketRuleViolation(
                    code="FAIL_CLOSED",
                    message=reason,
                    field="system",
                    expected="plugin execution success",
                    actual=f"exception or missing data: {reason}",
                ),
            ),
            normalized_qty=normalized_qty,
            normalized_price=normalized_price,
            details={"fail_closed": True, "reason": reason},
        )


class MarketRulePlugin(Protocol):
    """
    市场规则插件接口

    市场无关 engine 只负责插件选择、结果聚合和 fail-closed 包装，
    不得硬编码 T+1、100 股、涨跌停、停牌、午休或 Binance filter 字段。

    参考: docs/INTERFACE_CONTRACTS.md 8.11.3 MarketRulePlugin
    """

    def supports(self, asset_class: AssetClass, venue: str) -> bool:
        """
        判断该插件是否支持指定的市场/资产类别

        Args:
            asset_class: 资产类别
            venue: 交易场所

        Returns:
            True 如果插件支持该市场
        """
        ...

    def check(
        self,
        intent: MarketRuleIntent,
        snapshot: MarketRiskSnapshot,
    ) -> MarketRuleCheckResult:
        """
        执行市场规则检查

        Args:
            intent: 规则检查输入
            snapshot: 市场风险快照

        Returns:
            MarketRuleCheckResult: 通过/拒绝/裁剪结果
        """
        ...
