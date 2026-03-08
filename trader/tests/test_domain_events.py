"""Domain Events Tests - 领域事件回归测试"""
from dataclasses import dataclass, field
from decimal import Decimal
from trader.core.domain.models.events import (
    DomainEvent,
    EventType,
    create_order_created_event,
    create_order_filled_event,
    create_position_updated_event,
)


class SimpleEnum:
    """简单枚举模拟"""
    def __init__(self, value):
        self.value = value
    def __repr__(self):
        return f"SimpleEnum({self.value})"


@dataclass
class MockOrder:
    """模拟订单"""
    order_id: str = "order_123"
    client_order_id: str = "client_001"
    symbol: str = "BTCUSDT"
    side: SimpleEnum = field(default_factory=lambda: SimpleEnum("BUY"))
    order_type: SimpleEnum = field(default_factory=lambda: SimpleEnum("LIMIT"))
    quantity: Decimal = field(default_factory=lambda: Decimal("1.0"))
    price: Decimal = field(default_factory=lambda: Decimal("50000.0"))
    strategy_name: str = "test_strategy"
    filled_quantity: Decimal = field(default_factory=lambda: Decimal("1.0"))
    average_price: Decimal = field(default_factory=lambda: Decimal("50000.0"))


@dataclass
class MockPosition:
    """模拟持仓"""
    position_id: str = "pos_123"
    symbol: str = "BTCUSDT"
    quantity: Decimal = field(default_factory=lambda: Decimal("1.0"))
    avg_price: Decimal = field(default_factory=lambda: Decimal("50000.0"))
    current_price: Decimal = field(default_factory=lambda: Decimal("51000.0"))
    unrealized_pnl: Decimal = field(default_factory=lambda: Decimal("1000.0"))


def test_event_type_position_updated_exists():
    """验证 EventType.POSITION_UPDATED 存在"""
    assert EventType.POSITION_UPDATED is not None
    assert EventType.POSITION_UPDATED.value == "POSITION_UPDATED"


def test_create_order_created_event():
    """验证创建订单创建事件"""
    order = MockOrder()
    event = create_order_created_event(order)
    
    assert event.event_type == EventType.ORDER_CREATED
    assert event.aggregate_id == "order_123"
    assert event.aggregate_type == "Order"


def test_create_order_filled_event():
    """验证创建订单成交事件"""
    order = MockOrder()
    event = create_order_filled_event(order)
    
    assert event.event_type == EventType.ORDER_FILLED
    assert event.aggregate_id == "order_123"


def test_create_position_updated_event():
    """验证创建持仓更新事件 - 回归测试"""
    position = MockPosition()
    realized_pnl = Decimal("100.0")
    event = create_position_updated_event(position, realized_pnl)
    
    assert event.event_type == EventType.POSITION_UPDATED
    assert event.aggregate_id == "pos_123"
    assert event.aggregate_type == "Position"
    assert event.data["unrealized_pnl"] == Decimal("1000.0")
    assert event.data["realized_pnl"] == realized_pnl


def test_create_position_updated_event_without_realized_pnl():
    """验证创建持仓更新事件（无已实现盈亏）"""
    position = MockPosition()
    event = create_position_updated_event(position)
    
    assert event.event_type == EventType.POSITION_UPDATED
    assert event.data["realized_pnl"] is None


def test_domain_event_to_json():
    """验证事件序列化"""
    order = MockOrder()
    event = create_order_created_event(order)
    json_str = event.to_json()
    
    assert "ORDER_CREATED" in json_str
    assert "order_123" in json_str


def test_domain_event_from_json():
    """验证事件反序列化"""
    order = MockOrder()
    event = create_order_created_event(order)
    json_str = event.to_json()
    
    restored = DomainEvent.from_json(json_str)
    assert restored.event_type == EventType.ORDER_CREATED
    assert restored.aggregate_id == "order_123"
