"""
Events - 领域事件模型
====================
事件是审计和状态重建的基础。

关键原则：
1. 所有状态变化都通过事件记录
2. 事件是不可变的（追加写）
3. 事件可以重放以重建状态
4. 事件链可用于审计追溯

事件流示例：
    OrderCreated -> OrderSubmitted -> OrderPartiallyFilled -> OrderFilled
                    -> PositionUpdated
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional, Dict, Any, List
import uuid
import json


class EventType(Enum):
    """领域事件类型"""

    # 订单事件
    ORDER_CREATED = "ORDER_CREATED"                 # 订单创建
    ORDER_SUBMITTED = "ORDER_SUBMITTED"             # 订单提交到券商
    ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"  # 部分成交
    ORDER_FILLED = "ORDER_FILLED"                 # 完全成交
    ORDER_CANCELLED = "ORDER_CANCELLED"           # 订单撤销
    ORDER_REJECTED = "ORDER_REJECTED"             # 订单拒绝

# 持仓事件
    POSITION_OPENED = "POSITION_OPENED"           # 开仓
    POSITION_INCREASED = "POSITION_INCREASED"     # 加仓
    POSITION_DECREASED = "POSITION_DECREASED"     # 减仓
    POSITION_CLOSED = "POSITION_CLOSED"           # 平仓
    POSITION_UPDATED = "POSITION_UPDATED"         # 持仓更新

    # Lot 级事件（Batch 1 新增）
    POSITION_LOT_OPENED = "POSITION_LOT_OPENED"   # 新批次开仓
    POSITION_LOT_REDUCED = "POSITION_LOT_REDUCED"  # 批次被部分平仓
    POSITION_LOT_CLOSED = "POSITION_LOT_CLOSED"   # 批次完全平仓

    # 策略持仓汇总事件（Batch 1 新增）
    STRATEGY_POSITION_UPDATED = "STRATEGY_POSITION_UPDATED"  # 策略持仓变更

    # 账户持仓事件（Batch 1 新增）
    ACCOUNT_POSITION_UPDATED = "ACCOUNT_POSITION_UPDATED"  # 账户总持仓变更

    # 历史持仓事件（Batch 1 新增）
    HISTORICAL_POSITION_DISCOVERED = "HISTORICAL_POSITION_DISCOVERED"  # 启动时发现历史持仓
    POSITION_COST_ALIGNED = "POSITION_COST_ALIGNED"  # 历史持仓成本已对齐

    # 对账事件（Batch 1 新增）
    RECONCILIATION_PERFORMED = "RECONCILIATION_PERFORMED"  # 对账执行
    RECONCILIATION_DISCREPANCY = "RECONCILIATION_DISCREPANCY"  # 对账发现

    # 风控事件
    RISK_CHECK_PASSED = "RISK_CHECK_PASSED"      # 风控通过
    RISK_CHECK_FAILED = "RISK_CHECK_FAILED"        # 风控拒绝

    # 信号事件
    SIGNAL_GENERATED = "SIGNAL_GENERATED"         # 信号生成
    SIGNAL_PROCESSED = "SIGNAL_PROCESSED"         # 信号处理完成

    # 账户事件
    ACCOUNT_UPDATED = "ACCOUNT_UPDATED"           # 账户更新

    # 系统事件
    SYSTEM_STARTED = "SYSTEM_STARTED"
    SYSTEM_STOPPED = "SYSTEM_STOPPED"


@dataclass
class DomainEvent:
    """
    领域事件基类

    所有业务事件都继承自这个基类。
    事件包含足够的信息用于：
    1. 状态重建（重放事件流）
    2. 审计追溯
    3. 调试分析
    """
    event_id: str = ""                    # 事件唯一ID
    event_type: EventType = EventType.ORDER_CREATED  # 事件类型

    # 聚合根信息
    aggregate_id: str = ""               # 聚合根ID（如订单ID）
    aggregate_type: str = ""              # 聚合根类型（如Order）
    aggregate_version: int = 1            # 聚合根版本（乐观锁）

    # 时间
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # 事件数据（具体内容）
    data: Dict[str, Any] = field(default_factory=dict)

    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.event_id:
            object.__setattr__(self, 'event_id', str(uuid.uuid4()))

    def to_json(self) -> str:
        """序列化为JSON（用于持久化）"""
        return json.dumps({
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "aggregate_id": self.aggregate_id,
            "aggregate_type": self.aggregate_type,
            "aggregate_version": self.aggregate_version,
            "timestamp": self.timestamp.isoformat(),
            "data": self._serialize_data(),
            "metadata": self.metadata,
        }, ensure_ascii=False, default=str)

    def _serialize_data(self) -> Dict[str, Any]:
        """序列化事件数据（处理Decimal等特殊类型）"""
        result = {}
        for key, value in self.data.items():
            if isinstance(value, Decimal):
                result[key] = str(value)
            elif isinstance(value, datetime):
                result[key] = value.isoformat()
            elif isinstance(value, Enum):
                result[key] = value.value
            else:
                result[key] = value
        return result

    @classmethod
    def from_json(cls, json_str: str) -> "DomainEvent":
        data = json.loads(json_str)
        raw_data = data.get("data", {})
        deserialized_data = cls._deserialize_data(raw_data)
        return cls(
            event_id=data["event_id"],
            event_type=EventType(data["event_type"]),
            aggregate_id=data["aggregate_id"],
            aggregate_type=data["aggregate_type"],
            aggregate_version=data.get("aggregate_version", 1),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            data=deserialized_data,
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def _deserialize_data(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                try:
                    candidate = Decimal(value)
                    if "." in value or candidate != int(candidate):
                        result[key] = candidate
                    else:
                        result[key] = value
                except Exception:
                    result[key] = value
            else:
                result[key] = value
        return result

    def __repr__(self) -> str:
        return f"DomainEvent({self.event_type.value}, {self.aggregate_id}, v{self.aggregate_version})"


# ==================== 便捷构造函数 ====================

def create_order_created_event(order) -> DomainEvent:
    """创建订单创建事件"""
    return DomainEvent(
        event_type=EventType.ORDER_CREATED,
        aggregate_id=order.order_id,
        aggregate_type="Order",
        data={
            "client_order_id": order.client_order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "order_type": order.order_type.value,
            "quantity": order.quantity,
            "price": order.price,
            "strategy_name": order.strategy_name,
        }
    )


def create_order_filled_event(order) -> DomainEvent:
    """创建订单成交事件"""
    return DomainEvent(
        event_type=EventType.ORDER_FILLED,
        aggregate_id=order.order_id,
        aggregate_type="Order",
        data={
            "client_order_id": order.client_order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "filled_quantity": order.filled_quantity,
            "average_price": order.average_price,
        }
    )


def create_position_updated_event(position, realized_pnl: Decimal | None = None) -> DomainEvent:
    """创建持仓更新事件"""
    return DomainEvent(
        event_type=EventType.POSITION_UPDATED,
        aggregate_id=position.position_id,
        aggregate_type="Position",
        data={
            "symbol": position.symbol,
            "quantity": position.quantity,
            "avg_price": position.avg_price,
            "current_price": position.current_price,
            "unrealized_pnl": position.unrealized_pnl,
            "realized_pnl": realized_pnl,
        }
    )


# ==================== Lot 级事件构造函数（Batch 1 新增） ====================

def create_lot_opened_event(
    lot_id: str,
    strategy_id: str,
    symbol: str,
    quantity: Decimal,
    fill_price: Decimal,
    fee_qty: Decimal | None = None,
) -> DomainEvent:
    """创建批次开仓事件"""
    return DomainEvent(
        event_type=EventType.POSITION_LOT_OPENED,
        aggregate_id=lot_id,
        aggregate_type="PositionLot",
        data={
            "lot_id": lot_id,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "original_qty": quantity,
            "remaining_qty": quantity,
            "fill_price": fill_price,
            "fee_qty": fee_qty,
        }
    )


def create_lot_reduced_event(
    lot_id: str,
    strategy_id: str,
    symbol: str,
    reduce_qty: Decimal,
    reduce_price: Decimal,
    remaining_qty: Decimal,
    realized_pnl: Decimal,
) -> DomainEvent:
    """创建批次减仓事件"""
    return DomainEvent(
        event_type=EventType.POSITION_LOT_REDUCED,
        aggregate_id=lot_id,
        aggregate_type="PositionLot",
        data={
            "lot_id": lot_id,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "reduce_qty": reduce_qty,
            "reduce_price": reduce_price,
            "remaining_qty": remaining_qty,
            "realized_pnl": realized_pnl,
        }
    )


def create_lot_closed_event(
    lot_id: str,
    strategy_id: str,
    symbol: str,
    close_price: Decimal,
    total_realized_pnl: Decimal,
) -> DomainEvent:
    """创建批次完全平仓事件"""
    return DomainEvent(
        event_type=EventType.POSITION_LOT_CLOSED,
        aggregate_id=lot_id,
        aggregate_type="PositionLot",
        data={
            "lot_id": lot_id,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "close_price": close_price,
            "total_realized_pnl": total_realized_pnl,
        }
    )


def create_strategy_position_updated_event(
    strategy_id: str,
    symbol: str,
    total_qty: Decimal,
    avg_cost: Decimal,
    realized_pnl: Decimal,
    unrealized_pnl: Decimal,
) -> DomainEvent:
    """创建策略持仓变更事件"""
    return DomainEvent(
        event_type=EventType.STRATEGY_POSITION_UPDATED,
        aggregate_id=f"{strategy_id}:{symbol}",
        aggregate_type="StrategyPosition",
        data={
            "strategy_id": strategy_id,
            "symbol": symbol,
            "total_qty": total_qty,
            "avg_cost": avg_cost,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
        }
    )


def create_historical_position_discovered_event(
    symbol: str,
    quantity: Decimal,
    broker_avg_price: Decimal | None = None,
    source: str = "broker_api",
) -> DomainEvent:
    """创建历史持仓发现事件"""
    return DomainEvent(
        event_type=EventType.HISTORICAL_POSITION_DISCOVERED,
        aggregate_id=symbol,
        aggregate_type="AccountPosition",
        data={
            "symbol": symbol,
            "quantity": quantity,
            "broker_avg_price": broker_avg_price,
            "source": source,
        }
    )


def create_reconciliation_discrepancy_event(
    symbol: str,
    broker_qty: Decimal,
    oms_qty: Decimal,
    difference: Decimal,
    status: str,
    tolerance: Decimal,
) -> DomainEvent:
    """创建对账差异事件"""
    return DomainEvent(
        event_type=EventType.RECONCILIATION_DISCREPANCY,
        aggregate_id=symbol,
        aggregate_type="Reconciliation",
        data={
            "symbol": symbol,
            "broker_qty": broker_qty,
            "oms_qty": oms_qty,
            "difference": difference,
            "status": status,
            "tolerance": tolerance,
        }
    )
