"""
Order - 订单领域模型
=====================
订单是交易系统的核心实体。订单状态机确保订单生命周期中的状态转换是正确和可追溯的。

订单状态机（OMS核心）：
    PENDING -> SUBMITTED -> PARTIALLY_FILLED -> FILLED
         |          |              |
         v          v              v
      REJECTED  CANCELLED     CANCELLED

关键概念：
- client_order_id: 客户端生成的唯一ID，用于幂等性保证
- broker_order_id: 券商分配的订单ID
- 状态转换必须通过事件记录，确保可审计和回放
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional, Dict, Any, List
import uuid


class OrderStatus(Enum):
    """订单状态枚举"""
    PENDING = "PENDING"           # 待提交（刚创建，还未发送到券商）
    SUBMITTED = "SUBMITTED"       # 已提交（已发送到券商，等待成交）
    PARTIALLY_FILLED = "PARTIALLY_FILLED"  # 部分成交
    FILLED = "FILLED"            # 完全成交
    CANCELLED = "CANCELLED"       # 已撤销
    REJECTED = "REJECTED"        # 已拒绝（被券商或风控拒绝）
    CANCEL_PENDING = "CANCEL_PENDING"  # 撤销待确认


class OrderSide(Enum):
    """订单方向"""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """订单类型"""
    MARKET = "MARKET"     # 市价单
    LIMIT = "LIMIT"       # 限价单


class OrderTimeInForce(Enum):
    """订单时效"""
    GTC = "GTC"          # Good Till Cancel - 取消前有效
    IOC = "IOC"          # Immediate Or Cancel - 立即成交否则取消
    FOK = "FOK"          # Fill Or Kill - 全部成交否则取消


@dataclass
class Order:
    """
    订单领域模型

    这是交易系统的核心实体。所有订单都必须通过 OMS 管理，
    策略不能直接调用券商API下单。
    """
    # 核心标识
    order_id: str                         # 系统生成的唯一订单ID
    client_order_id: str                  # 客户端订单ID（用于幂等）
    broker_order_id: Optional[str] = None  # 券商订单ID

    # 订单内容
    symbol: str = ""                      # 交易标的，如 BTCUSDT
    side: OrderSide = OrderSide.BUY       # 买卖方向
    order_type: OrderType = OrderType.MARKET  # 订单类型
    time_in_force: OrderTimeInForce = OrderTimeInForce.GTC  # 时效

    # 价格和数量
    quantity: Decimal = Decimal("0")       # 委托数量
    price: Optional[Decimal] = None       # 委托价格（限价单）
    filled_quantity: Decimal = field(default_factory=lambda: Decimal("0"))   # 已成交数量
    average_price: Decimal = field(default_factory=lambda: Decimal("0"))    # 成交均价

    # 状态
    status: OrderStatus = OrderStatus.PENDING
    strategy_name: str = ""               # 产生该订单的策略名

    # 元数据
    stop_loss: Optional[Decimal] = None    # 止损价格
    take_profit: Optional[Decimal] = None # 止盈价格
    error_message: Optional[str] = None    # 错误信息（如被拒绝）

    # 时间戳
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None

    # 扩展信息
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """初始化后的处理"""
        # 生成唯一ID
        if not self.order_id:
            object.__setattr__(self, 'order_id', str(uuid.uuid4()))

        # 生成客户端订单ID（如果未提供）
        if not self.client_order_id:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
            object.__setattr__(self, 'client_order_id',
                            f"{self.strategy_name}_{timestamp}_{uuid.uuid4().hex[:8]}")

        # 确保Decimal类型
        if isinstance(self.quantity, (int, float)):
            object.__setattr__(self, 'quantity', Decimal(str(self.quantity)))
        if self.price and isinstance(self.price, (int, float)):
            object.__setattr__(self, 'price', Decimal(str(self.price)))

    # ==================== 状态判断 ====================

    def is_terminal(self) -> bool:
        """是否为终态（不可再变化）"""
        return self.status in [
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED
        ]

    def can_modify(self) -> bool:
        """是否可以修改（改价）"""
        return self.status in [
            OrderStatus.PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED
        ]

    def can_cancel(self) -> bool:
        """是否可以撤销"""
        return self.status in [
            OrderStatus.PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED
        ]

    def is_buy(self) -> bool:
        """是否为买入订单"""
        return self.side == OrderSide.BUY

    def is_sell(self) -> bool:
        """是否为卖出订单"""
        return self.side == OrderSide.SELL

    def get_remaining_quantity(self) -> Decimal:
        """获取剩余未成交数量"""
        return self.quantity - self.filled_quantity

    def get_order_value(self) -> Decimal:
        """获取订单名义金额"""
        price = self.average_price if self.average_price > 0 else (self.price or Decimal("0"))
        return self.filled_quantity * price

    # ==================== 状态转换方法 ====================

    def submit(self) -> None:
        """提交订单（发送到券商）"""
        if self.status != OrderStatus.PENDING:
            raise ValueError(f"订单状态错误：{self.status}")
        self.status = OrderStatus.SUBMITTED
        self.submitted_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)

    def fill(self, fill_quantity: Decimal, fill_price: Decimal) -> None:
        """
        处理成交回报

        Args:
            fill_quantity: 本次成交数量
            fill_price: 本次成交价格

        Raises:
            ValueError: 如果订单状态不允许成交
        """
        # 状态检查：只有 SUBMITTED 或 PARTIALLY_FILLED 状态可以成交
        if self.status not in (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED):
            raise ValueError(f"订单状态不允许成交: 当前状态 {self.status}")

        # 更新成交数量
        self.filled_quantity += fill_quantity

        # 计算加权平均价格
        if self.average_price == 0:
            self.average_price = fill_price
        else:
            total_value = (self.average_price * self.filled_quantity) + (fill_price * fill_quantity)
            self.average_price = total_value / self.filled_quantity

        # 更新状态
        if self.filled_quantity >= self.quantity:
            self.status = OrderStatus.FILLED
            self.filled_at = datetime.now(timezone.utc)
        else:
            self.status = OrderStatus.PARTIALLY_FILLED

        self.updated_at = datetime.now(timezone.utc)

    def reject(self, reason: str) -> None:
        """拒绝订单"""
        if self.is_terminal():
            raise ValueError(f"订单已终态，无法拒绝: {self.status}")
        self.status = OrderStatus.REJECTED
        self.error_message = reason
        self.updated_at = datetime.now(timezone.utc)

    def cancel(self) -> None:
        """撤销订单"""
        if self.is_terminal():
            raise ValueError(f"订单已终态，无法撤销: {self.status}")
        self.status = OrderStatus.CANCELLED
        self.updated_at = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return (f"Order({self.client_order_id}, {self.symbol}, "
                f"{self.side.value}, {self.quantity}@{self.price or 'MARKET'}, "
                f"status={self.status.value})")
