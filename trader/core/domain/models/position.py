"""
Position - 持仓领域模型
=======================
持仓模型区分"券商真实持仓"与"内部账本持仓"，为对账机制提供基础。

关键概念：
- Position: 内部账本持仓（从成交事件累积）
- BrokerPosition: 券商真实持仓（来自券商API查询）
- 两者的差异就是需要对齐的地方
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any
import uuid

from trader.core.domain.models.money import Money


@dataclass
class Position:
    """
    持仓领域模型

    记录策略/账户对某个标的的持仓情况。
    通过成交事件累积计算，而不是直接存储。
    """
    position_id: str = ""                    # 持仓唯一ID
    symbol: str = ""                        # 交易标的
    quantity: Decimal = Decimal("0")         # 持仓数量
    avg_price: Decimal = Decimal("0")        # 平均持仓成本

    # 实时行情（由外部更新）
    current_price: Decimal = Decimal("0")

    # 盈亏计算
    realized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))   # 已实现盈亏
    unrealized_pnl: Decimal = field(default_factory=lambda: Decimal("0")) # 未实现盈亏

    # 时间戳
    opened_at: Optional[datetime] = None    # 建仓时间
    updated_at: datetime = field(default_factory=datetime.utcnow)

    # 扩展
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.position_id:
            object.__setattr__(self, 'position_id', str(uuid.uuid4()))

        # 确保Decimal类型
        for attr in ['quantity', 'avg_price', 'current_price', 'realized_pnl', 'unrealized_pnl']:
            val = getattr(self, attr)
            if isinstance(val, (int, float, str)):
                object.__setattr__(self, attr, Decimal(str(val)))

    # ==================== 核心属性 ====================

    @property
    def market_value(self) -> Decimal:
        """市值 = 数量 * 当前价格"""
        return self.quantity * self.current_price

    @property
    def cost_basis(self) -> Decimal:
        """成本基础 = 数量 * 平均价格"""
        return self.quantity * self.avg_price

    @property
    def is_long(self) -> bool:
        """是否多头"""
        return self.quantity > 0

    @property
    def is_empty(self) -> bool:
        """是否空仓"""
        return self.quantity == 0

    # ==================== 持仓操作 ====================

    def update_price(self, current_price: Decimal) -> None:
        """更新当前价格，计算未实现盈亏"""
        self.current_price = current_price
        if self.quantity > 0:
            self.unrealized_pnl = (current_price - self.avg_price) * self.quantity
        else:
            self.unrealized_pnl = Decimal("0")
        self.updated_at = datetime.utcnow()

    def open(self, quantity: Decimal, price: Decimal) -> None:
        """
        开仓

        Args:
            quantity: 开仓数量
            price: 开仓价格
        """
        if quantity <= 0:
            raise ValueError("开仓数量必须大于0")

        if self.quantity == 0:
            # 新开仓
            self.quantity = quantity
            self.avg_price = price
            self.opened_at = datetime.utcnow()
        else:
            # 加仓
            self.add(quantity, price)

        self.update_price(price)
        self.updated_at = datetime.utcnow()

    def add(self, add_quantity: Decimal, add_price: Decimal) -> None:
        """加仓"""
        if add_quantity <= 0:
            raise ValueError("加仓数量必须大于0")

        # 计算新的加权平均价
        total_cost = self.cost_basis + (add_quantity * add_price)
        self.quantity += add_quantity
        self.avg_price = total_cost / self.quantity if self.quantity > 0 else Decimal("0")
        self.updated_at = datetime.utcnow()

    def reduce(self, reduce_quantity: Decimal, reduce_price: Decimal) -> Decimal:
        """
        减仓

        Args:
            reduce_quantity: 减仓数量
            reduce_price: 减仓价格

        Returns:
            实现的盈亏
        """
        if reduce_quantity <= 0:
            raise ValueError("减仓数量必须大于0")

        # 限制减仓数量不能超过持仓
        actual_reduce = min(reduce_quantity, self.quantity)

        # 计算实现的盈亏
        cost = actual_reduce * self.avg_price
        proceeds = actual_reduce * reduce_price
        realized = proceeds - cost

        self.realized_pnl += realized
        self.quantity -= actual_reduce

        # 更新均价（如果是全平，均价归零）
        if self.quantity > 0:
            # 持仓数量减少，但平均成本不变
            pass
        else:
            self.avg_price = Decimal("0")
            self.unrealized_pnl = Decimal("0")

        self.updated_at = datetime.utcnow()
        return realized

    def close(self, price: Decimal) -> Decimal:
        """平仓"""
        return self.reduce(self.quantity, price)

    def __repr__(self) -> str:
        return (f"Position({self.symbol}, qty={self.quantity}, "
                f"avg={self.avg_price}, cur={self.current_price}, "
                f"pnl={self.realized_pnl + self.unrealized_pnl})")


@dataclass
class BrokerPosition:
    """
    券商真实持仓

    来自券商API的实时持仓数据，作为对账的source of truth。
    """
    symbol: str = ""
    quantity: Decimal = Decimal("0")          # 持仓数量
    avg_price: Decimal = Decimal("0")        # 持仓成本
    unrealized_pnl: Decimal = Decimal("0")  # 未实现盈亏
    frozen_quantity: Decimal = Decimal("0")   # 冻结数量（如用于申购）

    # A股特有
    yesterday_quantity: Decimal = Decimal("0")  # 昨仓（用于T+1）

    def __post_init__(self):
        for attr in ['quantity', 'avg_price', 'unrealized_pnl', 'frozen_quantity', 'yesterday_quantity']:
            val = getattr(self, attr)
            if isinstance(val, (int, float, str)):
                object.__setattr__(self, attr, Decimal(str(val)))

    @property
    def available_quantity(self) -> Decimal:
        """可用数量 = 持仓 - 冻结"""
        return self.quantity - self.frozen_quantity


@dataclass
class PositionReconciliation:
    """
    持仓对账结果

    比较券商持仓与内部账本持仓的差异。
    """
    symbol: str = ""
    broker_quantity: Decimal = Decimal("0")  # 券商持仓
    ledger_quantity: Decimal = Decimal("0") # 账本持仓
    difference: Decimal = Decimal("0")      # 差异
    status: str = "CONSISTENT"              # CONSISTENT / DISCREPANCY
    action: Optional[str] = None             # 修复建议
