"""
PostgresSnapshotStorage - PostgreSQL Snapshot 持久化存储
=========================================================

将 Control Plane 的快照从内存存储迁移到 PostgreSQL。

实现 EventService 所需的快照存储协议，提供生产级持久化。

表结构：
    control_plane_snapshots (
        id BIGSERIAL PRIMARY KEY,
        stream_key VARCHAR(255) NOT NULL,
        snapshot_type VARCHAR(100) NOT NULL,
        ts_ms BIGINT NOT NULL,
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )

索引：
    - idx_control_plane_snapshots_stream_key: (stream_key)
    - idx_control_plane_snapshots_stream_key_ts: (stream_key, ts_ms DESC)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trader.api.models.schemas import SnapshotEnvelope


# ==================== 类型映射 ====================


# ==================== Postgres 实现 ====================

class PostgresSnapshotStorage:
    """
    PostgreSQL Snapshot 存储
    
    为 EventService 提供生产级的快照持久化能力。
    
    依赖：
    - 需要 PostgreSQL 数据库
    - 需要创建 control_plane_snapshots 表
    """
    
    def __init__(
        self,
        pool_or_connection: Any = None,
    ) -> None:
        """
        初始化 PostgresSnapshotStorage
        
        Args:
            pool_or_connection: 数据库连接池或连接对象
        """
        self._pool = pool_or_connection
    
    def _envelope_to_row(self, envelope: SnapshotEnvelope) -> dict:
        """将 SnapshotEnvelope 转换为数据库行"""
        created_at_value = envelope.created_at or datetime.now(timezone.utc)
        if isinstance(created_at_value, str):
            created_at_value = datetime.fromisoformat(created_at_value)
        
        return {
            "stream_key": envelope.stream_key,
            "snapshot_type": envelope.snapshot_type,
            "ts_ms": envelope.ts_ms,
            "payload": json.dumps(envelope.payload) if envelope.payload else {},
            "created_at": created_at_value,
        }
    
    def _row_to_envelope(self, row: dict) -> SnapshotEnvelope:
        """将数据库行转换为 SnapshotEnvelope"""
        return SnapshotEnvelope(
            snapshot_id=row["id"],
            stream_key=row["stream_key"],
            snapshot_type=row["snapshot_type"],
            ts_ms=row["ts_ms"],
            payload=json.loads(row["payload"]) if row["payload"] else {},
            created_at=row["created_at"].isoformat() if row["created_at"] else None,
        )
    
    async def save(self, envelope: SnapshotEnvelope) -> SnapshotEnvelope:
        """
        保存快照
        
        使用 stream_key 作为唯一键，INSERT ... ON CONFLICT DO UPDATE 实现幂等写入。
        
        Returns:
            保存后的 SnapshotEnvelope（包含生成的 id）
        """
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        
        row = self._envelope_to_row(envelope)
        
        query = """
            INSERT INTO control_plane_snapshots (
                stream_key, snapshot_type, ts_ms, payload, created_at
            ) VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (stream_key) DO UPDATE SET
                snapshot_type = EXCLUDED.snapshot_type,
                ts_ms = EXCLUDED.ts_ms,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            RETURNING id, created_at
        """
        
        async with self._pool.acquire() as conn:
            result = await conn.fetchrow(
                query,
                row["stream_key"],
                row["snapshot_type"],
                row["ts_ms"],
                row["payload"],
                row["created_at"],
            )
        
        # Return with updated id and created_at
        return SnapshotEnvelope(
            snapshot_id=result["id"],
            stream_key=envelope.stream_key,
            snapshot_type=envelope.snapshot_type,
            ts_ms=envelope.ts_ms,
            payload=envelope.payload,
            created_at=result["created_at"].isoformat() if result["created_at"] else envelope.created_at,
        )
    
    async def get_latest(self, stream_key: str) -> Optional[SnapshotEnvelope]:
        """
        获取指定 stream_key 的最新快照
        
        Args:
            stream_key: 流键
            
        Returns:
            SnapshotEnvelope 或 None
        """
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        
        query = """
            SELECT id, stream_key, snapshot_type, ts_ms, payload, created_at
            FROM control_plane_snapshots
            WHERE stream_key = $1
        """
        
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, stream_key)
        
        if row is None:
            return None
        
        return self._row_to_envelope(dict(row))
    
    async def list_by_stream(
        self,
        stream_key: str,
        limit: int = 100,
    ) -> List[SnapshotEnvelope]:
        """
        列出指定 stream_key 的所有快照（按时间倒序）
        
        Args:
            stream_key: 流键
            limit: 最大返回数量
            
        Returns:
            SnapshotEnvelope 列表
        """
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        
        query = """
            SELECT id, stream_key, snapshot_type, ts_ms, payload, created_at
            FROM control_plane_snapshots
            WHERE stream_key = $1
            ORDER BY ts_ms DESC
            LIMIT $2
        """
        
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, stream_key, limit)
        
        return [self._row_to_envelope(dict(row)) for row in rows]
    
    async def list_recent(self, limit: int = 100) -> List[SnapshotEnvelope]:
        """
        获取最近的快照（所有 stream_key）
        
        Args:
            limit: 最大返回数量
            
        Returns:
            SnapshotEnvelope 列表
        """
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        
        query = """
            SELECT id, stream_key, snapshot_type, ts_ms, payload, created_at
            FROM control_plane_snapshots
            ORDER BY updated_at DESC
            LIMIT $1
        """
        
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, limit)
        
        return [self._row_to_envelope(dict(row)) for row in rows]
    
    async def list_snapshots(
        self,
        stream_key: str,
        since_ts_ms: Optional[int] = None,
        until_ts_ms: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        列出指定 stream_key 的快照（支持时间范围过滤）
        
        Args:
            stream_key: 流键
            since_ts_ms: 开始时间戳（毫秒）
            until_ts_ms: 结束时间戳（毫秒）
            limit: 最大返回数量
            
        Returns:
            快照字典列表
        """
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        
        query = """
            SELECT id, stream_key, snapshot_type, ts_ms, payload, created_at
            FROM control_plane_snapshots
            WHERE stream_key = $1
        """
        params = [stream_key]
        
        if since_ts_ms is not None:
            query += " AND ts_ms >= $${len(params) + 1}"
            params.append(since_ts_ms)
        
        if until_ts_ms is not None:
            query += " AND ts_ms <= $${len(params) + 1}"
            params.append(until_ts_ms)
        
        query += " ORDER BY ts_ms DESC LIMIT $" + str(len(params) + 1)
        params.append(limit)
        
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        
        return [
            {
                "snapshot_id": row["id"],
                "stream_key": row["stream_key"],
                "snapshot_type": row["snapshot_type"],
                "ts_ms": row["ts_ms"],
                "payload": json.loads(row["payload"]) if row["payload"] else {},
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
            for row in rows
        ]
    
    async def delete(self, stream_key: str) -> bool:
        """
        删除指定 stream_key 的快照
        
        Args:
            stream_key: 流键
            
        Returns:
            是否删除成功
        """
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        
        query = "DELETE FROM control_plane_snapshots WHERE stream_key = $1"
        
        async with self._pool.acquire() as conn:
            result = await conn.execute(query, stream_key)
        
        return result != "DELETE 0"
    
    async def count(self) -> int:
        """
        获取快照总数
        
        Returns:
            快照数量
        """
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        
        query = "SELECT COUNT(*) as cnt FROM control_plane_snapshots"
        
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query)
        
        return row["cnt"] if row else 0


# ==================== 迁移脚本 ====================

MIGRATION_SQL = """
-- Control Plane Snapshots 表迁移脚本
-- 创建 control_plane_snapshots 表用于存储 Control Plane 快照
-- 注意：使用独立表名以避免与 Event Sourcing 的 snapshots 表冲突

CREATE TABLE IF NOT EXISTS control_plane_snapshots (
    id BIGSERIAL PRIMARY KEY,
    stream_key VARCHAR(255) NOT NULL UNIQUE,
    snapshot_type VARCHAR(100) NOT NULL,
    ts_ms BIGINT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_control_plane_snapshots_stream_key ON control_plane_snapshots(stream_key);
CREATE INDEX IF NOT EXISTS idx_control_plane_snapshots_stream_key_ts ON control_plane_snapshots(stream_key, ts_ms DESC);

-- 注释
COMMENT ON TABLE control_plane_snapshots IS 'Control Plane snapshots - stores latest state snapshots per stream_key';
COMMENT ON COLUMN control_plane_snapshots.stream_key IS 'Unique stream identifier (e.g., strategy:{name}, account:{id})';
COMMENT ON COLUMN control_plane_snapshots.snapshot_type IS 'Snapshot type (e.g., state_snapshot, risk_snapshot)';
COMMENT ON COLUMN control_plane_snapshots.ts_ms IS 'Event timestamp in milliseconds';
COMMENT ON COLUMN control_plane_snapshots.payload IS 'Snapshot data as JSON';
"""


# ==================== 工厂函数 ====================

async def create_postgres_snapshot_storage(
    connection_string: str | None = None,
) -> PostgresSnapshotStorage:
    """
    创建 PostgreSQL 快照存储
    
    Args:
        connection_string: PostgreSQL 连接字符串
        
    Returns:
        PostgresSnapshotStorage 实例
    """
    import asyncpg
    
    if connection_string is None:
        # 从环境变量获取
        import os
        connection_string = os.environ.get("POSTGRES_CONNECTION_STRING")
    
    if connection_string is None:
        raise ValueError("PostgreSQL connection string not provided")
    
    pool = await asyncpg.create_pool(connection_string, min_size=1, max_size=10)
    
    # 创建表（如果不存在）
    async with pool.acquire() as conn:
        await conn.execute(MIGRATION_SQL)
    
    return PostgresSnapshotStorage(pool_or_connection=pool)
