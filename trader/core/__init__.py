# Core Domain Models - 核心领域模型
#
# 本模块定义交易系统的核心领域模型
# 重要原则：
#   1. 使用decimal.Decimal处理所有金额/数量，避免float精度问题
#   2. 统一使用epoch_ms作为时间戳
#   3. 模型应该是纯Python对象，不依赖外部库

from trader.core.domain.models.money import Money
from trader.core.domain.models.order import Order, OrderStatus, OrderSide, OrderType
from trader.core.domain.models.position import Position, BrokerPosition
from trader.core.domain.models.signal import Signal, SignalType
from trader.core.domain.models.events import DomainEvent, EventType

__all__ = [
    "Money",
    "Order", "OrderStatus", "OrderSide", "OrderType",
    "Position", "BrokerPosition",
    "Signal", "SignalType",
    "DomainEvent", "EventType",
]
