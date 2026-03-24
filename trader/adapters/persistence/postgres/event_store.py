"""
PostgreSQL Event Store - 通用事件溯源存储实现
============================================
基于PostgreSQL的Event Store实现，支持：
- 幂等追加（相同的stream_key+seq不会重复插入）
- 按stream读取事件
- 快照保存与恢复

依赖：
- PostgreSQL数据库
- asyncpg包
"""
import logging
import json
from typing import List, Optional, Dict, Any, TYPE_CHECKING
from datetime import datetime, timezone
from dataclasses import dataclass

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class StreamEvent:
    """事件流中的事件"""
    event_id: str
    stream_key: str
    seq: int
    event_type: str
    aggregate_id: str
    aggregate_type: str
    timestamp: datetime
    ts_ms: int
    data: Dict[str, Any]
    metadata: Dict[str, Any]
    schema_version: int = 1


class PostgresEventStore:
    """
    PostgreSQL Event Store
    
    实现通用事件溯源的持久化存储，支持幂等追加。
    
    表结构（由002_event_log.sql创建）：
    - event_id: 事件唯一ID
    - stream_key: 事件流键（如order-123, position-456）
    - seq: 流内序列号
    - event_type: 事件类型
    - aggregate_id: 聚合根ID
    - aggregate_type: 聚合根类型
    - timestamp: 时间戳
    - ts_ms: 毫秒时间戳
    - data: 事件数据（JSONB）
    - metadata: 元数据（JSONB）
    - schema_version: 模式版本
    """

    def __init__(
        self,
        pool: "asyncpg.Pool",
    ):
        self._pool = pool

    @staticmethod
    def _decode_json_field(value: Any) -> Dict[str, Any]:
        """Normalize JSON/JSONB payloads from asyncpg into dicts."""
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return json.loads(value)
        return {}

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
        幂等追加事件到流
        
        Args:
            stream_key: 事件流键
            seq: 流内序列号
            event_type: 事件类型
            aggregate_id: 聚合根ID
            aggregate_type: 聚合根类型
            data: 事件数据
            metadata: 元数据
            event_id: 事件ID（可选，自动生成）
            timestamp: 时间戳（可选，默认当前时间）
            ts_ms: 毫秒时间戳（可选，自动从timestamp计算）
            schema_version: 模式版本
            
        Returns:
            event_id: 事件的唯一ID
            
        Note:
            如果相同的(stream_key, seq)已存在，DO NOTHING，不报错。
        """
        import uuid
        
        if event_id is None:
            event_id = str(uuid.uuid4())
        
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        if ts_ms is None:
            ts_ms = int(timestamp.timestamp() * 1000)
        
        if metadata is None:
            metadata = {}
        
        async with self._pool.acquire() as conn:
            try:
                # Use INSERT with ON CONFLICT DO NOTHING and RETURNING to detect conflicts
                # If conflict occurs, query the existing event_id at that seq
                row = await conn.fetchrow(
                    """
                    INSERT INTO event_log (event_id, stream_key, seq, event_type, aggregate_id, aggregate_type, timestamp, ts_ms, data, metadata, schema_version)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    ON CONFLICT (stream_key, seq) DO NOTHING
                    RETURNING event_id
                    """,
                    event_id,
                    stream_key,
                    seq,
                    event_type,
                    aggregate_id,
                    aggregate_type,
                    timestamp,
                    ts_ms,
                    json.dumps(data),
                    json.dumps(metadata),
                    schema_version,
                )
                
                stored_event_id = row["event_id"] if row else None
                
                if stored_event_id is None:
                    # Conflict occurred - query the existing event at this seq
                    existing = await conn.fetchrow(
                        """
                        SELECT event_id FROM event_log 
                        WHERE stream_key = $1 AND seq = $2
                        """,
                        stream_key,
                        seq,
                    )
                    if existing:
                        stored_event_id = existing["event_id"]
                        logger.debug(
                            "EVENT_APPEND_CONFLICT",
                            extra={
                                "stream_key": stream_key,
                                "seq": seq,
                                "requested_event_id": event_id,
                                "stored_event_id": stored_event_id,
                            },
                        )
                    else:
                        # This shouldn't happen - conflict detected but no existing event found
                        # Log warning but still return the requested event_id
                        logger.warning(
                            "EVENT_APPEND_CONFLICT_NO_EXISTING",
                            extra={
                                "stream_key": stream_key,
                                "seq": seq,
                                "requested_event_id": event_id,
                            },
                        )
                        stored_event_id = event_id
                else:
                    logger.debug(
                        "EVENT_APPENDED",
                        extra={
                            "event_id": stored_event_id,
                            "stream_key": stream_key,
                            "seq": seq,
                            "event_type": event_type,
                        },
                    )
                    
            except Exception as e:
                logger.error(
                    "EVENT_STORE_APPEND_ERROR",
                    extra={
                        "stream_key": stream_key,
                        "seq": seq,
                        "error": str(e),
                    },
                )
                raise
        
        # Return the actual stored event_id (may differ from caller's event_id if conflict occurred)
        return stored_event_id

    async def read_stream(
        self,
        stream_key: str,
        from_seq: int = 0,
        limit: int = 1000,
    ) -> List[StreamEvent]:
        """
        读取事件流
        
        Args:
            stream_key: 事件流键
            from_seq: 从哪个序列号开始读取（不包含）
            limit: 最大返回数量
            
        Returns:
            List[StreamEvent]: 事件列表
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT event_id, stream_key, seq, event_type, aggregate_id, aggregate_type, timestamp, ts_ms, data, metadata, schema_version
                FROM event_log
                WHERE stream_key = $1 AND seq > $2
                ORDER BY seq ASC
                LIMIT $3
                """,
                stream_key,
                from_seq,
                limit,
            )
        
        return [
            StreamEvent(
                event_id=row["event_id"],
                stream_key=row["stream_key"],
                seq=row["seq"],
                event_type=row["event_type"],
                aggregate_id=row["aggregate_id"],
                aggregate_type=row["aggregate_type"],
                timestamp=row["timestamp"],
                ts_ms=row["ts_ms"],
                data=self._decode_json_field(row["data"]),
                metadata=self._decode_json_field(row["metadata"]),
                schema_version=row["schema_version"],
            )
            for row in rows
        ]

    async def get_latest_seq(self, stream_key: str) -> Optional[int]:
        """
        获取流中最新的序列号
        
        Args:
            stream_key: 事件流键
            
        Returns:
            int: 最新序列号，如果流为空则返回-1
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT MAX(seq) as max_seq
                FROM event_log
                WHERE stream_key = $1
                """,
                stream_key,
            )
        
        if row["max_seq"] is None:
            return -1
        return row["max_seq"]

    async def snapshot_at(
        self,
        stream_key: str,
        seq: int,
    ) -> Optional[StreamEvent]:
        """
        获取指定序列号的事件（快照点）
        
        Args:
            stream_key: 事件流键
            seq: 序列号
            
        Returns:
            Optional[StreamEvent]: 指定序列号的事件，如果不存在则返回None
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT event_id, stream_key, seq, event_type, aggregate_id, aggregate_type, timestamp, ts_ms, data, metadata, schema_version
                FROM event_log
                WHERE stream_key = $1 AND seq = $2
                """,
                stream_key,
                seq,
            )
        
        if row is None:
            return None
        
        return StreamEvent(
            event_id=row["event_id"],
            stream_key=row["stream_key"],
            seq=row["seq"],
            event_type=row["event_type"],
            aggregate_id=row["aggregate_id"],
            aggregate_type=row["aggregate_type"],
            timestamp=row["timestamp"],
            ts_ms=row["ts_ms"],
            data=self._decode_json_field(row["data"]),
            metadata=self._decode_json_field(row["metadata"]),
            schema_version=row["schema_version"],
        )

    async def get_stream_info(self, stream_key: str) -> Dict[str, Any]:
        """
        获取流的信息
        
        Args:
            stream_key: 事件流键
            
        Returns:
            Dict: 流信息，包括事件数量、最新序列号等
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 
                    COUNT(*) as event_count,
                    COALESCE(MAX(seq), -1) as latest_seq,
                    MIN(ts_ms) as earliest_ts_ms,
                    MAX(ts_ms) as latest_ts_ms
                FROM event_log
                WHERE stream_key = $1
                """,
                stream_key,
            )
        
        return {
            "stream_key": stream_key,
            "event_count": row["event_count"],
            "latest_seq": row["latest_seq"],
            "earliest_ts_ms": row["earliest_ts_ms"],
            "latest_ts_ms": row["latest_ts_ms"],
        }

    async def append_domain_event(
        self,
        stream_key: str,
        domain_event,
        seq: Optional[int] = None,
    ) -> str:
        """
        追加领域事件（方便方法）
        
        Args:
            stream_key: 事件流键
            domain_event: DomainEvent对象
            seq: 序列号（可选，默认自动分配，使用原子操作避免竞态）
            
        Returns:
            event_id
        """
        import uuid
        
        event_id = domain_event.event_id or str(uuid.uuid4())
        event_type = domain_event.event_type.value if hasattr(domain_event.event_type, 'value') else str(domain_event.event_type)
        timestamp = domain_event.timestamp or datetime.now(timezone.utc)
        ts_ms = int(timestamp.timestamp() * 1000)
        
        if domain_event.metadata is None:
            metadata = {}
        else:
            metadata = domain_event.metadata
        
        if seq is not None:
            # Explicit seq provided, use regular append.
            # NOTE: Caller is responsible for ensuring seq uniqueness within the stream.
            # No advisory lock is used because caller explicitly specifies seq.
            # Use this path only when seq is already known (e.g., replaying events).
            return await self.append(
                stream_key=stream_key,
                seq=seq,
                event_type=event_type,
                aggregate_id=domain_event.aggregate_id,
                aggregate_type=domain_event.aggregate_type,
                data=domain_event.data,
                metadata=metadata,
                event_id=event_id,
                timestamp=timestamp,
                schema_version=1,
            )
        
        # Auto-assign seq using atomic CTE with advisory lock to prevent race condition
        # Use pg_advisory_xact_lock(hashtext(stream_key)) to ensure exclusive access.
        # This locks the stream_key atomically, preventing concurrent transactions from
        # both calculating the same next_seq when the stream is empty (no rows to FOR UPDATE).
        async with self._pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """
                    WITH lock AS (
                        SELECT pg_advisory_xact_lock(hashtext($1)) AS lock_result
                    ),
                    new_event AS (
                        SELECT COALESCE(MAX(seq), -1) + 1 as next_seq
                        FROM event_log
                        WHERE stream_key = $1
                    ),
                    insert_result AS (
                        INSERT INTO event_log (event_id, stream_key, seq, event_type, aggregate_id, aggregate_type, timestamp, ts_ms, data, metadata, schema_version)
                        SELECT $2, $1, new_event.next_seq, $3, $4, $5, $6, $7, $8, $9, $10
                        FROM new_event
                        ON CONFLICT (stream_key, seq) DO NOTHING
                        RETURNING event_id
                    ),
                    -- If insert succeeded, use that event_id; otherwise query the existing one
                    final AS (
                        SELECT event_id FROM insert_result
                        UNION ALL
                        SELECT e.event_id 
                        FROM event_log e, new_event n
                        WHERE e.stream_key = $1 AND e.seq = n.next_seq
                        AND NOT EXISTS (SELECT 1 FROM insert_result)
                    )
                    SELECT event_id FROM final LIMIT 1
                    """,
                    stream_key,
                    event_id,
                    event_type,
                    domain_event.aggregate_id,
                    domain_event.aggregate_type,
                    timestamp,
                    ts_ms,
                    json.dumps(domain_event.data),
                    json.dumps(metadata),
                    1,
                )
                
                stored_event_id = row["event_id"] if row else None
                
                if stored_event_id == event_id:
                    logger.debug(
                        "DOMAIN_EVENT_APPENDED",
                        extra={"stream_key": stream_key, "event_id": stored_event_id},
                    )
                else:
                    # Conflict occurred - a different event_id was already stored at this seq
                    logger.debug(
                        "DOMAIN_EVENT_CONCURRENT_INSERT",
                        extra={"stream_key": stream_key, "requested_event_id": event_id, "stored_event_id": stored_event_id},
                    )
                    
            except Exception as e:
                logger.error(
                    "EVENT_STORE_DOMAIN_EVENT_ERROR",
                    extra={
                        "stream_key": stream_key,
                        "error": str(e),
                    },
                )
                raise
        
        # Return the actual stored event_id (may differ from caller's event_id if conflict occurred)
        return stored_event_id