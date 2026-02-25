"""
InMemoryEventStore - 内存事件存储
=================================
用于开发和测试的内存事件存储。

特点：
- 完全内存操作，速度快
- 支持事件回放
- 可用于验证事件溯源逻辑
"""
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime
from dataclasses import dataclass, field
from collections import defaultdict

from trader.core.application.ports import StoragePort


@dataclass
class StoredEvent:
    """存储的事件"""
    event_id: str
    event_type: str
    aggregate_id: str
    aggregate_type: str
    timestamp: datetime
    data: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)


class InMemoryEventStore:
    """
    内存事件存储

    用于开发和测试。
    生产环境应使用持久化存储（如PostgreSQL）。
    """

    def __init__(self):
        self._events: List[StoredEvent] = []
        self._index: Dict[str, List[int]] = defaultdict(list)  # aggregate_id -> event indices

    async def append(self, event) -> str:
        """追加事件"""
        stored = StoredEvent(
            event_id=event.event_id,
            event_type=event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type),
            aggregate_id=event.aggregate_id,
            aggregate_type=event.aggregate_type,
            timestamp=event.timestamp,
            data=event.data,
            metadata=event.metadata,
        )

        self._events.append(stored)
        self._index[event.aggregate_id].append(len(self._events) - 1)

        return event.event_id

    async def get_events(
        self,
        aggregate_id: Optional[str] = None,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 1000
    ) -> List[StoredEvent]:
        """查询事件"""
        results = self._events

        # 按聚合ID过滤
        if aggregate_id:
            indices = self._index.get(aggregate_id, [])
            results = [self._events[i] for i in indices]
        else:
            # 按时间排序
            results = sorted(self._events, key=lambda e: e.timestamp)

        # 按事件类型过滤
        if event_type:
            results = [e for e in results if e.event_type == event_type]

        # 按时间过滤
        if since:
            results = [e for e in results if e.timestamp >= since]

        # 限制数量
        return results[-limit:]

    async def get_all_events(self) -> List[StoredEvent]:
        """获取所有事件"""
        return list(self._events)

    def clear(self) -> None:
        """清空所有事件（用于测试）"""
        self._events.clear()
        self._index.clear()


class InMemoryStorage(StoragePort):
    """
    内存存储实现

    用于开发和测试。
    实现完整的StoragePort接口。
    """

    def __init__(self):
        self._event_store = InMemoryEventStore()
        self._orders: Dict[str, Any] = {}
        self._positions: Dict[str, Any] = {}
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    # ==================== 事件 ====================

    async def save_event(self, event) -> str:
        return await self._event_store.append(event)

    async def get_events(
        self,
        aggregate_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 1000
    ) -> List:
        return await self._event_store.get_events(
            aggregate_id=aggregate_id,
            event_type=event_type,
            limit=limit
        )

    # ==================== 订单 ====================

    async def save_order(self, order) -> None:
        self._orders[order.order_id] = order

    async def get_order(self, order_id: str):
        return self._orders.get(order_id)

    async def get_orders(
        self,
        symbol: Optional[str] = None,
        status: Optional[Any] = None,
        limit: int = 100
    ) -> List:
        results = list(self._orders.values())

        if symbol:
            results = [o for o in results if o.symbol == symbol]

        if status:
            results = [o for o in results if o.status == status]

        return results[:limit]

    # ==================== 持仓 ====================

    async def save_position(self, position) -> None:
        self._positions[position.symbol] = position

    async def get_position(self, symbol: str):
        return self._positions.get(symbol)

    async def get_all_positions(self) -> List:
        return list(self._positions.values())
