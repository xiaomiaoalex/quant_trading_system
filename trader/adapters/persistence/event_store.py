"""
Event Store with Fallback - 事件存储自动降级
============================================
提供Event Store功能，当PostgreSQL不可用时自动降级到内存存储。

职责：
- 优先使用PostgreSQL EventStore
- 当PG不可用时自动切换到内存存储
- 保持幂等性
"""
import asyncio
import logging
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from trader.adapters.persistence.memory.event_store import InMemoryEventStore
from trader.adapters.persistence.postgres.event_store import PostgresEventStore, StreamEvent


logger = logging.getLogger(__name__)


class EventStoreWithFallback:
    """
    Event Store with automatic PostgreSQL to Memory fallback
    
    当PostgreSQL不可用时，自动降级到内存存储。
    适用于需要高可用的场景。
    
    注意：
    - 内存存储在进程重启后会丢失
    - 生产环境应监控PG健康状态并及时告警
    """

    def __init__(
        self,
        pg_event_store: Optional[PostgresEventStore] = None,
        memory_event_store: Optional[InMemoryEventStore] = None,
    ):
        self._pg = pg_event_store
        self._memory = memory_event_store or InMemoryEventStore()
        self._use_pg = pg_event_store is not None
        self._fallback_lock = asyncio.Lock()  # Lock to protect fallback path seq calculation

    @property
    def is_using_postgres(self) -> bool:
        """是否使用PostgreSQL"""
        return self._use_pg

    def _get_store(self):
        """
        获取当前使用的存储。
        
        FIX: 现在会检查 _use_pg 标志，如果 PG 已被禁用则返回内存存储。
        注意：如果 _use_pg 为 True 但 PG 连接实际已断开，调用方应捕获异常
        并调用 disable_postgres() 来正确切换到内存存储。
        """
        if self._use_pg and self._pg is not None:
            return self._pg
        return self._memory

    async def append(
        self,
        stream_key: str,
        seq: int,
        event_type: str,
        aggregate_id: str,
        aggregate_type: str,
        data: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        ts_ms: Optional[int] = None,
        schema_version: int = 1,
    ) -> str:
        """
        幂等追加事件（带自动降级）
        
        如果PG可用，优先使用PG；否则使用内存存储。
        """
        try:
            if self._use_pg and self._pg is not None:
                return await self._pg.append(
                    stream_key=stream_key,
                    seq=seq,
                    event_type=event_type,
                    aggregate_id=aggregate_id,
                    aggregate_type=aggregate_type,
                    data=data,
                    metadata=metadata,
                    event_id=event_id,
                    timestamp=timestamp,
                    ts_ms=ts_ms,
                    schema_version=schema_version,
                )
        except Exception as e:
            logger.warning(
                "EVENT_STORE_PG_FAILED_FALLBACK",
                extra={
                    "stream_key": stream_key,
                    "seq": seq,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            self._use_pg = False

        # Fallback to memory - need to create DomainEvent from fields
        from trader.core.domain.models.events import DomainEvent, EventType
        
        # Create a domain event from the fields
        # Safely convert event_type string to EventType enum, fall back to string if invalid
        try:
            event_type_enum = EventType(event_type) if isinstance(event_type, str) else event_type
        except ValueError:
            # Invalid EventType string, use as-is (memory store accepts string event types)
            logger.warning(
                "EVENT_STORE_INVALID_EVENT_TYPE",
                extra={
                    "event_type": str(event_type),
                    "falling_back_to_string": True,
                },
            )
            event_type_enum = event_type
        
        domain_event = DomainEvent(
            event_id=event_id or str(uuid.uuid4()),
            event_type=event_type_enum,
            aggregate_id=aggregate_id,
            aggregate_type=aggregate_type,
            timestamp=timestamp or datetime.now(timezone.utc),
            data=data,
            metadata=metadata or {},
        )
        
        return await self._memory.append(domain_event)

    async def read_stream(
        self,
        stream_key: str,
        from_seq: int = 0,
        limit: int = 1000,
    ) -> List[StreamEvent]:
        """
        读取事件流。
        
        WARNING: 当使用内存回退时，存在以下语义差异：
        - 内存存储按 aggregate_id 组织事件，而非 stream_key
        - 因此 stream_key 参数在内存回退时被忽略
        - 返回的是所有事件的子集（按插入顺序），seq 使用内存中的索引位置
        
        调用方需要注意：如果必须按 stream_key 严格过滤，请确保 PostgreSQL 可用。
        """
        try:
            if self._use_pg and self._pg is not None:
                return await self._pg.read_stream(
                    stream_key=stream_key,
                    from_seq=from_seq,
                    limit=limit,
                )
        except Exception as e:
            logger.warning(
                "EVENT_STORE_READ_PG_FAILED_FALLBACK",
                extra={
                    "stream_key": stream_key,
                    "error": str(e),
                },
            )
            self._use_pg = False

        # Memory fallback - memory store organizes events by aggregate_id, not stream_key.
        # FIX #4: Add explicit warning when stream_key filtering is ignored
        # stream_key parameter is effectively ignored in this fallback path.
        # This is a semantic limitation of the fallback mechanism.
        logger.warning(
            "EVENT_STORE_STREAM_KEY_IGNORED_IN_MEMORY_FALLBACK",
            extra={
                "stream_key": stream_key,
                "warning": "Memory store does not support stream_key filtering; all events are returned",
            },
        )
        memory_events = await self._memory.get_events(
            aggregate_id=None,  # Memory store uses aggregate_id, not stream_key
            event_type=None,
            since=None,
            limit=limit,
        )
        
        # Convert memory events to StreamEvent format
        # Note: Memory store doesn't have stream_key, so we filter differently
        return [
            StreamEvent(
                event_id=e.event_id,
                stream_key=stream_key,  # Use provided stream_key
                seq=i,  # Use index as seq
                event_type=e.event_type,
                aggregate_id=e.aggregate_id,
                aggregate_type=e.aggregate_type,
                timestamp=e.timestamp,
                ts_ms=int(e.timestamp.timestamp() * 1000),
                data=e.data,
                metadata=e.metadata,
                schema_version=1,
            )
            for i, e in enumerate(memory_events)
            if i >= from_seq
        ][:limit]

    async def get_latest_seq(self, stream_key: str) -> Optional[int]:
        """
        获取流中最新的序列号。
        
        Returns:
            Optional[int]: 最新序列号，如果流为空则返回-1。
            如果使用内存回退且无法追踪stream_key的seq，则返回None。
        """
        try:
            if self._use_pg and self._pg is not None:
                return await self._pg.get_latest_seq(stream_key)
        except Exception as e:
            logger.warning(
                "EVENT_STORE_GET_LATEST_SEQ_PG_FAILED",
                extra={
                    "stream_key": stream_key,
                    "error": str(e),
                },
            )
            self._use_pg = False

        # Memory fallback - memory store does not support stream_key-based seq tracking
        # This is a limitation of the fallback; events are stored by aggregate_id, not stream_key
        logger.warning(
            "EVENT_STORE_GET_LATEST_SEQ_MEMORY_FALLBACK",
            extra={
                "stream_key": stream_key,
                "warning": "Memory store does not track seq per stream_key; returning None",
            },
        )
        return None

    async def snapshot_at(
        self,
        stream_key: str,
        seq: int,
    ) -> Optional[StreamEvent]:
        """获取指定序列号的事件"""
        try:
            if self._use_pg and self._pg is not None:
                return await self._pg.snapshot_at(stream_key, seq)
        except Exception as e:
            logger.warning(
                "EVENT_STORE_SNAPSHOT_PG_FAILED",
                extra={
                    "stream_key": stream_key,
                    "seq": seq,
                    "error": str(e),
                },
            )
            self._use_pg = False

        # Memory fallback - read stream and get specific seq
        events = await self.read_stream(stream_key, from_seq=seq, limit=1)
        if events:
            return events[0]
        # FIX #4: Return None explicitly when no event found at given seq
        # This makes the behavior explicit and consistent with PG version
        logger.debug(
            "EVENT_STORE_SNAPSHOT_NOT_FOUND",
            extra={"stream_key": stream_key, "seq": seq},
        )
        return None

    async def append_domain_event(
        self,
        stream_key: str,
        domain_event,
        seq: Optional[int] = None,
    ) -> str:
        """追加领域事件"""
        if seq is None:
            latest_seq = await self.get_latest_seq(stream_key)
            # Memory fallback may return None if stream_key seq tracking is unavailable
            if latest_seq is not None:
                seq = latest_seq + 1
            else:
                # Memory fallback: Use lock to ensure atomic seq calculation and append.
                # This prevents race conditions where concurrent requests would get the same seq.
                async with self._fallback_lock:
                    # Re-check latest_seq after acquiring lock (another request may have appended)
                    latest_seq = await self.get_latest_seq(stream_key)
                    if latest_seq is not None:
                        seq = latest_seq + 1
                    else:
                        # Still None after lock, calculate from memory store
                        memory_events = await self._memory.get_events(
                            aggregate_id=None,
                            event_type=None,
                            since=None,
                            limit=10000,
                        )
                        seq = len(memory_events)
        
        event_type = domain_event.event_type.value if hasattr(domain_event.event_type, 'value') else str(domain_event.event_type)
        
        return await self.append(
            stream_key=stream_key,
            seq=seq,
            event_type=event_type,
            aggregate_id=domain_event.aggregate_id,
            aggregate_type=domain_event.aggregate_type,
            data=domain_event.data,
            metadata=domain_event.metadata,
            event_id=domain_event.event_id,
            timestamp=domain_event.timestamp,
            schema_version=1,
        )

    def enable_postgres(self, pg_event_store: PostgresEventStore) -> None:
        """启用PostgreSQL EventStore"""
        self._pg = pg_event_store
        self._use_pg = True
        logger.info("EVENT_STORE_PG_ENABLED")

    def disable_postgres(self) -> None:
        """禁用PostgreSQL，使用内存存储"""
        self._use_pg = False
        logger.info("EVENT_STORE_PG_DISABLED")