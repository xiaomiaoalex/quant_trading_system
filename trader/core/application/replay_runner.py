"""
Replay Runner - 事件重放执行器
================================
从Event Store重放历史事件序列，重建订单/持仓状态。

核心功能：
1. 从Event Store按seq顺序读取事件
2. 通过DeterministicApplier重放事件
3. 重建订单/持仓状态
4. 支持场景复现和回归测试

架构约束：
- Core Plane禁止IO（不得有网络/DB/文件IO）
- 纯计算逻辑，放在core/application/

使用方式：
    # 定义适配器实现EventStoreProviderPort
    class MyEventStoreAdapter(EventStoreProviderPort):
        async def read_stream(self, stream_key: str, from_seq: int = 0, limit: int = 1000) -> List[StreamEvent]:
            ...  # 从数据库或内存读取
        async def get_latest_seq(self, stream_key: str) -> int:
            ...  # 获取最新序列号
    
    # 创建重放器并执行重放
    runner = ReplayRunner(event_store=my_adapter)
    result = await runner.replay_stream(ReplayOptions(stream_key="order-123"))
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, List, Optional, Dict, Any, runtime_checkable

from trader.core.application.deterministic_layer import (
    DeterministicApplier,
    ShadowState,
    ShadowOrder,
    RawOrderUpdate,
    RawFillUpdate,
    OrderStatus,
)
from trader.core.domain.models.order import OrderSide, OrderType


# ==================== 事件存储端口 ====================

@runtime_checkable
class EventStoreProviderPort(Protocol):
    """
    事件存储端口
    
    定义从事件存储读取事件的接口。
    由适配器实现（如PostgresEventStoreAdapter、InMemoryEventStoreAdapter）。
    """

    async def read_stream(
        self,
        stream_key: str,
        from_seq: int = 0,
        limit: int = 1000,
    ) -> List["StreamEvent"]:
        """
        按seq升序读取事件流
        
        Args:
            stream_key: 事件流键
            from_seq: 起始序列号（不包含）
            limit: 最大事件数
            
        Returns:
            List[StreamEvent]: 事件列表，按seq升序排列
        """
        ...

    async def get_latest_seq(self, stream_key: str) -> int:
        """
        获取流中最新的序列号
        
        Args:
            stream_key: 事件流键
            
        Returns:
            int: 最新序列号，如果流为空则返回-1
        """
        ...


@dataclass
class StreamEvent:
    """
    事件流中的事件
    
    与trader.adapters.persistence.postgres.event_store.StreamEvent保持一致。
    """
    event_id: str
    stream_key: str
    seq: int
    event_type: str
    aggregate_id: str
    aggregate_type: str
    timestamp: Any  # datetime
    ts_ms: int
    data: Dict[str, Any]
    metadata: Dict[str, Any]
    schema_version: int = 1


# ==================== 重放选项 ====================

@dataclass
class ReplayOptions:
    """
    重放选项
    
    控制事件重放的行为和范围。
    """
    stream_key: str                          # 事件流键
    from_seq: int = 0                        # 起始序列号（不包含）
    to_seq: Optional[int] = None             # 结束序列号（包含）
    event_types: Optional[List[str]] = None  # 事件类型过滤器（None表示全部）
    limit: int = 10000                       # 最大重放事件数
    record_states: bool = True              # 是否记录中间状态


# ==================== 重放结果 ====================

@dataclass
class ReplayResult:
    """
    重放结果
    
    包含重放统计和最终状态。
    """
    events_replayed: int                     # 重放的事件数
    final_seq: int                           # 最终序列号
    final_state: ShadowState                 # 最终状态
    states: List[ShadowState] = field(default_factory=list)  # 中间状态序列
    errors: List[str] = field(default_factory=list)           # 错误记录


# ==================== 事件类型映射 ====================

# 领域事件类型字符串映射
EVENT_TYPE_TO_ORDER_STATUS: Dict[str, OrderStatus] = {
    "ORDER_CREATED": OrderStatus.PENDING,
    "ORDER_SUBMITTED": OrderStatus.SUBMITTED,
    "ORDER_PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "ORDER_FILLED": OrderStatus.FILLED,
    "ORDER_CANCELLED": OrderStatus.CANCELLED,
    "ORDER_REJECTED": OrderStatus.REJECTED,
}


def convert_stream_event_to_raw_update(event: StreamEvent) -> Optional[RawOrderUpdate]:
    """
    将StreamEvent转换为RawOrderUpdate
    
    Args:
        event: 流事件
        
    Returns:
        Optional[RawOrderUpdate]: 转换后的订单更新，如果无法转换则返回None
    """
    data = event.data
    
    # 解析订单状态
    event_type_str = event.event_type
    if event_type_str in EVENT_TYPE_TO_ORDER_STATUS:
        status = EVENT_TYPE_TO_ORDER_STATUS[event_type_str].value
    else:
        # 无法识别的event_type，返回None让调用方跳过该事件
        return None
    
    # 解析数量
    filled_qty = None
    if "filled_qty" in data:
        filled_qty_val = data["filled_qty"]
        if isinstance(filled_qty_val, str):
            filled_qty = Decimal(filled_qty_val)
        elif isinstance(filled_qty_val, (int, float)):
            filled_qty = Decimal(str(filled_qty_val))
        elif filled_qty_val is not None:
            filled_qty = filled_qty_val
    
    # 解析价格
    avg_price = None
    if "avg_price" in data and data["avg_price"] is not None:
        price_val = data["avg_price"]
        if isinstance(price_val, str):
            avg_price = Decimal(price_val)
        elif isinstance(price_val, (int, float)):
            avg_price = Decimal(str(price_val))
        elif isinstance(price_val, Decimal):
            avg_price = price_val
    
    # 解析cl_ord_id
    cl_ord_id = data.get("client_order_id") or event.aggregate_id
    
    # 解析broker_order_id
    broker_order_id = data.get("broker_order_id")
    
    return RawOrderUpdate(
        cl_ord_id=cl_ord_id,
        broker_order_id=broker_order_id,
        status=status,
        filled_qty=filled_qty,
        avg_price=avg_price,
        exchange_event_ts_ms=event.ts_ms,
        local_receive_ts_ms=event.ts_ms,
        source="REPLAY",
        update_id=None,
        seq=event.seq,
    )


# ==================== 重放执行器 ====================

class ReplayRunner:
    """
    重放执行器
    
    负责从Event Store重放历史事件序列，重建订单状态。
    
    设计原则：
    1. 确定性重放：相同的事件序列必然产生相同的状态
    2. 幂等性：多次重放同一事件序列结果一致
    3. 可中断性：支持分页重放，可从任意点恢复
    """

    def __init__(
        self,
        event_store: EventStoreProviderPort,
        deterministic_applier: Optional[DeterministicApplier] = None,
    ):
        """
        初始化重放执行器
        
        Args:
            event_store: 事件存储适配器
            deterministic_applier: 确定性应用器（可选，默认创建新实例）
        """
        self._event_store = event_store
        self._applier = deterministic_applier or DeterministicApplier()

    def _create_snapshot(self, shadow: ShadowState) -> ShadowState:
        """
        创建ShadowState的深拷贝快照
        
        Args:
            shadow: 原始影子状态
            
        Returns:
            ShadowState: 深拷贝的影子状态
        """
        new_state = ShadowState()
        for cl_ord_id, order in shadow.orders_by_cl.items():
            new_order = ShadowOrder(
                cl_ord_id=order.cl_ord_id,
                broker_order_id=order.broker_order_id,
                status=order.status,
                filled_qty=order.filled_qty,
                avg_price=order.avg_price,
                last_exchange_ts_ms=order.last_exchange_ts_ms,
            )
            new_state.orders_by_cl[cl_ord_id] = new_order
        for broker_id, cl_ord_id in shadow.orders_by_broker_id.items():
            new_state.orders_by_broker_id[broker_id] = cl_ord_id
        return new_state

    async def replay_stream(
        self,
        options: ReplayOptions,
    ) -> ReplayResult:
        """
        重放事件流
        
        从Event Store读取事件，按seq顺序重放，重建最终状态。
        
        Args:
            options: 重放选项
            
        Returns:
            ReplayResult: 重放结果，包含最终状态和统计信息
            
        Raises:
            无（错误记录在result.errors中）
        """
        errors: List[str] = []
        states: List[ShadowState] = []
        events_replayed = 0
        current_seq = options.from_seq
        
        # 重置确定性应用器状态
        self._applier.reset()
        
        # 批量读取事件
        batch_limit = min(options.limit, 1000)  # 每批最多1000个事件
        
        while events_replayed < options.limit:
            # 检查是否到达目标序列
            if options.to_seq is not None and current_seq >= options.to_seq:
                break
            
            # 读取下一批事件
            remaining_limit = options.limit - events_replayed
            read_limit = min(batch_limit, remaining_limit)
            
            try:
                events = await self._event_store.read_stream(
                    stream_key=options.stream_key,
                    from_seq=current_seq,
                    limit=read_limit,
                )
            except Exception as e:
                errors.append(f"Error reading stream at seq {current_seq}: {str(e)}")
                break
            
            if not events:
                # 没有更多事件
                break
            
            # 处理每个事件
            for event in events:
                # 检查是否到达目标序列（to_seq是inclusive的）
                if options.to_seq is not None and event.seq > options.to_seq:
                    break
                
                # 检查事件类型过滤器
                if options.event_types is not None:
                    if event.event_type not in options.event_types:
                        continue
                
                # 转换并应用事件
                try:
                    raw_update = convert_stream_event_to_raw_update(event)
                    if raw_update is not None:
                        await self._applier.apply_order_update(raw_update)
                        events_replayed += 1
                except Exception as e:
                    errors.append(
                        f"Error applying event seq {event.seq} ({event.event_type}): {str(e)}"
                    )
                
                # 记录中间状态
                if options.record_states:
                    snapshot = self._create_snapshot(self._applier._shadow)
                    states.append(snapshot)
                
                current_seq = event.seq
            
            # 如果已处理完所有事件或到达目标序列
            if len(events) < read_limit:
                break
            if options.to_seq is not None and current_seq >= options.to_seq:
                break
        
        # 获取最终状态
        final_state = self._create_snapshot(self._applier._shadow)
        final_seq = current_seq
        
        return ReplayResult(
            events_replayed=events_replayed,
            final_seq=final_seq,
            final_state=final_state,
            states=states if options.record_states else [],
            errors=errors,
        )

    async def replay_to_point(
        self,
        stream_key: str,
        target_seq: int,
        event_types: Optional[List[str]] = None,
    ) -> ReplayResult:
        """
        重放事件到指定序列号
        
        便捷方法，重放到指定序列号为止。
        
        Args:
            stream_key: 事件流键
            target_seq: 目标序列号（包含）
            event_types: 事件类型过滤器
            
        Returns:
            ReplayResult: 重放结果
        """
        options = ReplayOptions(
            stream_key=stream_key,
            from_seq=0,
            to_seq=target_seq,
            event_types=event_types,
            limit=100000,  # 足够大的限制
            record_states=False,  # 不需要中间状态
        )
        return await self.replay_stream(options)


# ==================== 便捷函数 ====================

async def replay_stream(
    event_store: EventStoreProviderPort,
    stream_key: str,
    from_seq: int = 0,
    to_seq: Optional[int] = None,
    event_types: Optional[List[str]] = None,
    limit: int = 10000,
) -> ReplayResult:
    """
    重放事件流的便捷函数
    
    Args:
        event_store: 事件存储适配器
        stream_key: 事件流键
        from_seq: 起始序列号
        to_seq: 结束序列号（包含）
        event_types: 事件类型过滤器
        limit: 最大事件数
        
    Returns:
        ReplayResult: 重放结果
    """
    runner = ReplayRunner(event_store=event_store)
    options = ReplayOptions(
        stream_key=stream_key,
        from_seq=from_seq,
        to_seq=to_seq,
        event_types=event_types,
        limit=limit,
        record_states=True,
    )
    return await runner.replay_stream(options)
