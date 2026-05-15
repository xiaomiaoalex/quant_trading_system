"""
market_cost_model_port.py - P9.5 市场成本模型端口
==================================================
Service 层市场成本模型接口，用于回测和模拟环境。

核心协议：
- MarketCostModelPort: 市场成本模型接口
- ChinaStockCostModel: A 股成本模型（配置化纯计算）

成本组成：
- 买入佣金
- 卖出佣金
- 卖出印花税
- 最低佣金
- 滑点

不接入 Tushare、akshare、券商 API。

参考: docs/INTERFACE_CONTRACTS.md P9.5 MarketCostModelPort 契约
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """成本分解"""

    commission_buy: Decimal = Decimal("0")
    commission_sell: Decimal = Decimal("0")
    stamp_tax: Decimal = Decimal("0")
    minimum_commission: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class CostCalculationRequest:
    """成本计算请求"""

    symbol: str
    side: str
    price: Decimal
    quantity: Decimal
    asset_class: str = "CRYPTO"


@dataclass(frozen=True, slots=True)
class CostCalculationResult:
    """成本计算结果"""

    request: CostCalculationRequest
    breakdown: CostBreakdown
    effective_cost: Decimal
    effective_price: Decimal


class MarketCostModelPort(Protocol):
    """
    市场成本模型端口

    定义市场成本计算的接口。

    实现要求：
    1. calculate_costs: 计算交易成本
    2. get_effective_price: 计算滑点后的有效价格

    示例：
        class ChinaStockCostModel:
            async def calculate_costs(
                self, request: CostCalculationRequest
            ) -> CostCalculationResult:
                ...

            async def get_effective_price(
                self, symbol: str, side: str, price: Decimal
            ) -> Decimal:
                ...
    """

    async def calculate_costs(self, request: CostCalculationRequest) -> CostCalculationResult:
        """计算交易成本"""
        ...

    async def get_effective_price(self, symbol: str, side: str, price: Decimal) -> Decimal:
        """计算滑点后的有效价格"""
        ...


class NoOpCostModel(MarketCostModelPort):
    """无成本模型（用于 Crypto 测试）"""

    async def calculate_costs(self, request: CostCalculationRequest) -> CostCalculationResult:
        return CostCalculationResult(
            request=request,
            breakdown=CostBreakdown(),
            effective_cost=Decimal("0"),
            effective_price=request.price,
        )

    async def get_effective_price(self, symbol: str, side: str, price: Decimal) -> Decimal:
        return price


@dataclass
class ChinaStockCostModelConfig:
    """A 股成本模型配置"""

    buy_commission_rate: Decimal = Decimal("0.0003")
    sell_commission_rate: Decimal = Decimal("0.0003")
    stamp_tax_rate: Decimal = Decimal("0.001")
    minimum_commission: Decimal = Decimal("5")
    slippage_rate: Decimal = Decimal("0.0005")

    def __post_init__(self) -> None:
        if isinstance(self.buy_commission_rate, (int, float)):
            object.__setattr__(
                self,
                "buy_commission_rate",
                Decimal(str(self.buy_commission_rate)),
            )
        if isinstance(self.sell_commission_rate, (int, float)):
            object.__setattr__(
                self,
                "sell_commission_rate",
                Decimal(str(self.sell_commission_rate)),
            )
        if isinstance(self.stamp_tax_rate, (int, float)):
            object.__setattr__(self, "stamp_tax_rate", Decimal(str(self.stamp_tax_rate)))
        if isinstance(self.minimum_commission, (int, float)):
            object.__setattr__(self, "minimum_commission", Decimal(str(self.minimum_commission)))
        if isinstance(self.slippage_rate, (int, float)):
            object.__setattr__(self, "slippage_rate", Decimal(str(self.slippage_rate)))


class ChinaStockCostModel(MarketCostModelPort):
    """A 股成本模型（配置化纯计算）"""

    def __init__(self, config: ChinaStockCostModelConfig | None = None) -> None:
        self._config = config or ChinaStockCostModelConfig()

    async def calculate_costs(self, request: CostCalculationRequest) -> CostCalculationResult:
        notional = request.price * request.quantity
        slippage = notional * self._config.slippage_rate

        is_buy = request.side.upper() in ("BUY", "LONG", "OPEN_LONG")
        is_sell = request.side.upper() in ("SELL", "SHORT", "CLOSE_LONG", "CLOSE_SHORT")

        if is_buy:
            commission = notional * self._config.buy_commission_rate
            stamp_tax = Decimal("0")
        elif is_sell:
            commission = notional * self._config.sell_commission_rate
            stamp_tax = notional * self._config.stamp_tax_rate
        else:
            commission = Decimal("0")
            stamp_tax = Decimal("0")

        if commission < self._config.minimum_commission:
            commission = self._config.minimum_commission

        effective_cost = commission + stamp_tax + slippage
        effective_price = request.price
        if is_buy:
            effective_price = request.price * (Decimal("1") + self._config.slippage_rate)
        elif is_sell:
            effective_price = request.price * (Decimal("1") - self._config.slippage_rate)

        return CostCalculationResult(
            request=request,
            breakdown=CostBreakdown(
                commission_buy=commission if is_buy else Decimal("0"),
                commission_sell=commission if is_sell else Decimal("0"),
                stamp_tax=stamp_tax,
                minimum_commission=(
                    self._config.minimum_commission
                    if commission <= self._config.minimum_commission
                    else Decimal("0")
                ),
                slippage=slippage,
                total_cost=effective_cost,
            ),
            effective_cost=effective_cost,
            effective_price=effective_price,
        )

    async def get_effective_price(self, symbol: str, side: str, price: Decimal) -> Decimal:
        request = CostCalculationRequest(
            symbol=symbol, side=side, price=price, quantity=Decimal("1")
        )
        result = await self.calculate_costs(request)
        return result.effective_price
