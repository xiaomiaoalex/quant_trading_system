"""
Strategy Event Service - 策略事件服务
=====================================

负责发布和查询策略运行时事件（信号、订单、错误等）。

事件类型:
- strategy.signal: 策略生成信号
- strategy.order.submitted: 订单提交
- strategy.order.filled: 订单成交
- strategy.order.cancelled: 订单取消
- strategy.error: 策略错误

stream_key 格式: strategy.{strategy_id}.{event_type}
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from trader.api.models.schemas import EventEnvelope


class StrategyEventType(str, Enum):
    SIGNAL_GENERATED = "strategy.signal"
    ORDER_SUBMITTED = "strategy.order.submitted"
    ORDER_FILLED = "strategy.order.filled"
    ORDER_CANCELLED = "strategy.order.cancelled"
    ORDER_REJECTED = "strategy.order.rejected"
    ERROR = "strategy.error"
    TICK = "strategy.tick"


@dataclass
class StrategyEvent:
    """策略事件"""
    strategy_id: str
    event_type: StrategyEventType
    trace_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    ts_ms: Optional[int] = None
    
    def __post_init__(self):
        if self.ts_ms is None:
            self.ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if self.trace_id is None:
            self.trace_id = str(uuid.uuid4())
    
    def to_envelope(self) -> EventEnvelope:
        return EventEnvelope(
            stream_key=f"strategy:{self.strategy_id}",
            event_type=self.event_type.value,
            trace_id=self.trace_id,
            ts_ms=self.ts_ms,
            payload={
                "strategy_id": self.strategy_id,
                **self.payload,
            },
        )


class StrategyEventService:
    """策略事件服务（内存存储）"""
    
    def __init__(self, max_events_per_strategy: int = 1000):
        self._events: List[EventEnvelope] = []
        self._lock = asyncio.Lock()
        self._max_events = max_events_per_strategy
    
    async def publish(self, event: StrategyEvent) -> EventEnvelope:
        """发布策略事件"""
        envelope = event.to_envelope()
        async with self._lock:
            self._events.append(envelope)
            # 保持事件数量限制
            if len(self._events) > self._max_events * 10:
                self._events = self._events[-self._max_events * 5:]
        return envelope
    
    async def publish_signal(
        self,
        strategy_id: str,
        signal_data: Dict[str, Any],
        trace_id: Optional[str] = None,
    ) -> EventEnvelope:
        """发布信号事件"""
        event = StrategyEvent(
            strategy_id=strategy_id,
            event_type=StrategyEventType.SIGNAL_GENERATED,
            trace_id=trace_id,
            payload={"signal": signal_data},
        )
        return await self.publish(event)
    
    async def publish_order_event(
        self,
        strategy_id: str,
        order_data: Dict[str, Any],
        event_type: StrategyEventType,
        trace_id: Optional[str] = None,
    ) -> EventEnvelope:
        """发布订单事件"""
        event = StrategyEvent(
            strategy_id=strategy_id,
            event_type=event_type,
            trace_id=trace_id,
            payload={"order": order_data},
        )
        return await self.publish(event)
    
    async def publish_error(
        self,
        strategy_id: str,
        error_message: str,
        error_details: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> EventEnvelope:
        """发布错误事件"""
        event = StrategyEvent(
            strategy_id=strategy_id,
            event_type=StrategyEventType.ERROR,
            trace_id=trace_id,
            payload={
                "error_message": error_message,
                "error_details": error_details or {},
            },
        )
        return await self.publish(event)
    
    async def list_events(
        self,
        strategy_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[EventEnvelope]:
        """查询策略事件"""
        async with self._lock:
            events = self._events
        
        # 过滤
        if strategy_id:
            events = [e for e in events if e.stream_key == f"strategy:{strategy_id}"]
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        
        # 返回最新的
        return events[-limit:]
    
    async def get_recent_signals(self, strategy_id: str, limit: int = 20) -> List[EventEnvelope]:
        """获取最近信号事件"""
        return await self.list_events(
            strategy_id=strategy_id,
            event_type=StrategyEventType.SIGNAL_GENERATED.value,
            limit=limit,
        )
    
    async def get_recent_orders(self, strategy_id: str, limit: int = 50) -> List[EventEnvelope]:
        """获取最近订单事件"""
        events = await self.list_events(strategy_id=strategy_id, limit=limit * 2)
        order_events = [
            e for e in events 
            if e.event_type.startswith("strategy.order.")
        ]
        return order_events[-limit:]
    
    async def get_recent_errors(self, strategy_id: str, limit: int = 20) -> List[EventEnvelope]:
        """获取最近错误事件"""
        return await self.list_events(
            strategy_id=strategy_id,
            event_type=StrategyEventType.ERROR.value,
            limit=limit,
        )
    
    async def clear_events(self, strategy_id: Optional[str] = None) -> int:
        """清除事件，返回清除数量"""
        async with self._lock:
            if strategy_id:
                before = len(self._events)
                self._events = [
                    e for e in self._events 
                    if f"strategy.{strategy_id}." not in e.stream_key
                ]
                return before - len(self._events)
            else:
                count = len(self._events)
                self._events = []
                return count


# 全局单例
_strategy_event_service: Optional[StrategyEventService] = None


def get_strategy_event_service() -> StrategyEventService:
    """获取策略事件服务单例"""
    global _strategy_event_service
    if _strategy_event_service is None:
        _strategy_event_service = StrategyEventService()
    return _strategy_event_service
