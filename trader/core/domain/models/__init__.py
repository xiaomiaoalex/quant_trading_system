# Domain Models - 领域模型包
"""
本包包含交易系统的核心领域模型。

核心概念：
- Money: 金额值对象（使用Decimal避免精度问题）
- Order: 订单实体（含完整状态机）
- Position: 持仓实体
- Signal: 交易信号
- DomainEvent: 领域事件（用于审计和回放）

重要原则：
1. 所有金额/数量使用Decimal
2. 所有时间使用datetime
3. 模型应该是纯Python对象，不依赖外部库
"""

from trader.core.domain.models.money import Money
from trader.core.domain.models.order import (
    Order, OrderStatus, OrderSide, OrderType, OrderTimeInForce
)
from trader.core.domain.models.position import Position, BrokerPosition, PositionReconciliation
from trader.core.domain.models.signal import Signal, SignalType
from trader.core.domain.models.events import (
    DomainEvent, EventType,
    create_order_created_event, create_order_filled_event, create_position_updated_event
)

__all__ = [
    # Money
    "Money",

    # Order
    "Order", "OrderStatus", "OrderSide", "OrderType", "OrderTimeInForce",

    # Position
    "Position", "BrokerPosition", "PositionReconciliation",

    # Signal
    "Signal", "SignalType",

    # Events
    "DomainEvent", "EventType",
    "create_order_created_event", "create_order_filled_event", "create_position_updated_event",
]
