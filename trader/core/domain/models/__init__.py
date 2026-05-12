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

from trader.core.domain.models.events import (
    DomainEvent,
    EventType,
    create_order_created_event,
    create_order_filled_event,
    create_position_updated_event,
)
from trader.core.domain.models.market_risk import (
    AssetClass,
    MarketAccountRisk,
    MarketInstrumentSpec,
    MarketOpenOrderRisk,
    MarketPositionRisk,
    MarketRiskAuditEvent,
    MarketRiskBudget,
    MarketRiskSnapshot,
)
from trader.core.domain.models.money import Money
from trader.core.domain.models.order import (
    Order,
    OrderSide,
    OrderStatus,
    OrderTimeInForce,
    OrderType,
)
from trader.core.domain.models.orderbook import DepthCheckResult, OrderBook, OrderBookLevel
from trader.core.domain.models.position import BrokerPosition, Position, PositionReconciliation
from trader.core.domain.models.risk_decision import (
    ConstraintResult,
    RiskSizingDecision,
    RiskSizingDecisionType,
)
from trader.core.domain.models.signal import Signal, SignalType

__all__ = [
    # Money
    "Money",
    # Market Risk
    "AssetClass",
    "MarketAccountRisk",
    "MarketInstrumentSpec",
    "MarketOpenOrderRisk",
    "MarketPositionRisk",
    "MarketRiskAuditEvent",
    "MarketRiskBudget",
    "MarketRiskSnapshot",
    # Order
    "Order",
    "OrderStatus",
    "OrderSide",
    "OrderType",
    "OrderTimeInForce",
    # Position
    "Position",
    "BrokerPosition",
    "PositionReconciliation",
    # Signal
    "Signal",
    "SignalType",
    # Events
    "DomainEvent",
    "EventType",
    "create_order_created_event",
    "create_order_filled_event",
    "create_position_updated_event",
    # OrderBook
    "OrderBook",
    "OrderBookLevel",
    "DepthCheckResult",
    # Risk Sizing
    "ConstraintResult",
    "RiskSizingDecision",
    "RiskSizingDecisionType",
]
