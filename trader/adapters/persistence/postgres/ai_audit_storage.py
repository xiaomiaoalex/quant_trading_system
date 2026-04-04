"""
PostgresAuditLogStorage - PostgreSQL AI审计日志存储
================================================
将 AI 审计日志从内存存储迁移到 PostgreSQL。

实现 AuditLogStorage 协议，提供生产级持久化存储。

表结构：
    ai_audit_log (
        id BIGSERIAL PRIMARY KEY,
        entry_id UUID NOT NULL UNIQUE,
        strategy_id VARCHAR(100) NOT NULL,
        strategy_name VARCHAR(255),
        version VARCHAR(50),
        event_type VARCHAR(50) NOT NULL,
        status VARCHAR(20) NOT NULL,
        prompt TEXT,
        generated_code TEXT,
        code_hash VARCHAR(64),
        llm_backend VARCHAR(50),
        llm_model VARCHAR(100),
        execution_result JSONB,
        approver VARCHAR(100),
        approval_comment TEXT,
        metadata JSONB,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )

索引：
    - idx_ai_audit_strategy: (strategy_id)
    - idx_ai_audit_status: (status)
    - idx_ai_audit_created: (created_at DESC)
    - idx_ai_audit_entry_id: (entry_id)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trader.core.domain.models.events import EventType
from insight.ai_audit_log import (
    AIAuditLog,
    AuditEntry,
    AuditEventType,
    AuditLogStorage,
    AuditStatistics,
    AuditStatus,
)


# ==================== 类型映射 ====================

_STATUS_TO_STR = {
    AuditStatus.DRAFT: "draft",
    AuditStatus.PENDING: "pending",
    AuditStatus.APPROVED: "approved",
    AuditStatus.REJECTED: "rejected",
    AuditStatus.REVISION: "revision",
    AuditStatus.ACTIVE: "active",
    AuditStatus.ARCHIVED: "archived",
    AuditStatus.DELETED: "deleted",
}

_STR_TO_STATUS = {v: k for k, v in _STATUS_TO_STR.items()}

_EVENT_TO_STR = {
    AuditEventType.GENERATED: "generated",
    AuditEventType.VALIDATED: "validated",
    AuditEventType.SUBMITTED: "submitted",
    AuditEventType.APPROVED: "approved",
    AuditEventType.REJECTED: "rejected",
    AuditEventType.REVISION_REQUESTED: "revision",
    AuditEventType.DEPLOYED: "deployed",
    AuditEventType.UNDEPLOYED: "undeployed",
    AuditEventType.ARCHIVED: "archived",
    AuditEventType.MODIFIED: "modified",
    AuditEventType.EXECUTED: "executed",
    AuditEventType.ERROR: "error",
}

_STR_TO_EVENT = {v: k for k, v in _EVENT_TO_STR.items()}


# ==================== Postgres 实现 ====================

class PostgresAuditLogStorage:
    """
    PostgreSQL AI 审计日志存储
    
    实现 AuditLogStorage 协议，将审计日志持久化到 PostgreSQL。
    
    依赖：
    - 需要 PostgreSQL 数据库
    - 需要创建 ai_audit_log 表
    """
    
    def __init__(
        self,
        pool_or_connection: Any = None,
    ) -> None:
        """
        初始化 PostgresAuditLogStorage
        
        Args:
            pool_or_connection: 数据库连接池或连接对象
        """
        self._pool = pool_or_connection
    
    def _entry_to_row(self, entry: AuditEntry) -> dict:
        """将 AuditEntry 转换为数据库行"""
        return {
            "entry_id": entry.entry_id,
            "strategy_id": entry.strategy_id,
            "strategy_name": entry.strategy_name,
            "version": entry.version,
            "event_type": _EVENT_TO_STR.get(entry.event_type, entry.event_type.value),
            "status": _STATUS_TO_STR.get(entry.status, entry.status.value),
            "prompt": entry.prompt,
            "generated_code": entry.generated_code,
            "code_hash": entry.code_hash,
            "llm_backend": entry.llm_backend,
            "llm_model": entry.llm_model,
            "execution_result": json.dumps(entry.execution_result) if entry.execution_result else None,
            "approver": entry.approver,
            "approval_comment": entry.approval_comment,
            "metadata": json.dumps(entry.metadata) if entry.metadata else None,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
            "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
        }
    
    def _row_to_entry(self, row: dict) -> AuditEntry:
        """将数据库行转换为 AuditEntry"""
        return AuditEntry(
            entry_id=row["entry_id"],
            strategy_id=row["strategy_id"],
            strategy_name=row["strategy_name"] or "",
            version=row["version"] or "",
            event_type=_STR_TO_EVENT.get(row["event_type"], AuditEventType.GENERATED),
            status=_STR_TO_STATUS.get(row["status"], AuditStatus.DRAFT),
            prompt=row["prompt"] or "",
            generated_code=row["generated_code"] or "",
            code_hash=row["code_hash"] or "",
            llm_backend=row["llm_backend"] or "",
            llm_model=row["llm_model"] or "",
            execution_result=json.loads(row["execution_result"]) if row["execution_result"] else None,
            approver=row["approver"],
            approval_comment=row["approval_comment"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else datetime.now(timezone.utc),
        )
    
    async def save(self, entry: AuditEntry) -> None:
        """
        保存审计条目
        
        使用 INSERT ... ON CONFLICT DO UPDATE 实现幂等写入。
        """
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        
        row = self._entry_to_row(entry)
        
        query = """
            INSERT INTO ai_audit_log (
                entry_id, strategy_id, strategy_name, version,
                event_type, status, prompt, generated_code, code_hash,
                llm_backend, llm_model, execution_result, approver,
                approval_comment, metadata, created_at, updated_at
            ) VALUES (
                %(entry_id)s, %(strategy_id)s, %(strategy_name)s, %(version)s,
                %(event_type)s, %(status)s, %(prompt)s, %(generated_code)s, %(code_hash)s,
                %(llm_backend)s, %(llm_model)s, %(execution_result)s, %(approver)s,
                %(approval_comment)s, %(metadata)s, %(created_at)s, %(updated_at)s
            )
            ON CONFLICT (entry_id) DO UPDATE SET
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at,
                metadata = EXCLUDED.metadata
        """
        
        async with self._pool.acquire() as conn:
            await conn.execute(query, row)
    
    async def get(self, entry_id: str) -> Optional[AuditEntry]:
        """获取审计条目"""
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        
        query = "SELECT * FROM ai_audit_log WHERE entry_id = $1"
        
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, entry_id)
        
        if row is None:
            return None
        
        return self._row_to_entry(dict(row))
    
    async def get_by_strategy(self, strategy_id: str) -> List[AuditEntry]:
        """获取策略的所有版本"""
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        
        query = """
            SELECT * FROM ai_audit_log
            WHERE strategy_id = $1
            ORDER BY created_at DESC
        """
        
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, strategy_id)
        
        return [self._row_to_entry(dict(row)) for row in rows]
    
    async def list_by_status(
        self,
        status: AuditStatus,
        limit: int = 100,
    ) -> List[AuditEntry]:
        """按状态查询"""
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        
        status_str = _STATUS_TO_STR.get(status, status.value)
        query = """
            SELECT * FROM ai_audit_log
            WHERE status = $1
            ORDER BY created_at DESC
            LIMIT $2
        """
        
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, status_str, limit)
        
        return [self._row_to_entry(dict(row)) for row in rows]
    
    async def list_by_time_range(
        self,
        start: datetime,
        end: datetime,
        limit: int = 100,
    ) -> List[AuditEntry]:
        """按时间范围查询"""
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        
        query = """
            SELECT * FROM ai_audit_log
            WHERE created_at >= $1 AND created_at <= $2
            ORDER BY created_at DESC
            LIMIT $3
        """
        
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, start.isoformat(), end.isoformat(), limit)
        
        return [self._row_to_entry(dict(row)) for row in rows]
    
    async def list_recent(self, limit: int = 100) -> List[AuditEntry]:
        """获取最近的审计记录"""
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        
        query = """
            SELECT * FROM ai_audit_log
            ORDER BY created_at DESC
            LIMIT $1
        """
        
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, limit)
        
        return [self._row_to_entry(dict(row)) for row in rows]


# ==================== 迁移脚本 ====================

MIGRATION_SQL = """
-- AI Audit Log 表迁移脚本
-- 创建 ai_audit_log 表用于存储 AI 审计日志

CREATE TABLE IF NOT EXISTS ai_audit_log (
    id BIGSERIAL PRIMARY KEY,
    entry_id UUID NOT NULL UNIQUE,
    strategy_id VARCHAR(100) NOT NULL,
    strategy_name VARCHAR(255),
    version VARCHAR(50),
    event_type VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,
    prompt TEXT,
    generated_code TEXT,
    code_hash VARCHAR(64),
    llm_backend VARCHAR(50),
    llm_model VARCHAR(100),
    execution_result JSONB,
    approver VARCHAR(100),
    approval_comment TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_ai_audit_strategy ON ai_audit_log(strategy_id);
CREATE INDEX IF NOT EXISTS idx_ai_audit_status ON ai_audit_log(status);
CREATE INDEX IF NOT EXISTS idx_ai_audit_created ON ai_audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_audit_entry_id ON ai_audit_log(entry_id);

-- 注释
COMMENT ON TABLE ai_audit_log IS 'AI Strategy Audit Log - stores all AI-generated code audit trails';
COMMENT ON COLUMN ai_audit_log.entry_id IS 'Unique entry identifier (UUID)';
COMMENT ON COLUMN ai_audit_log.strategy_id IS 'Strategy identifier';
COMMENT ON COLUMN ai_audit_log.event_type IS 'Audit event type (generated/validated/submitted/approved/rejected/deployed)';
COMMENT ON COLUMN ai_audit_log.status IS 'Current status (draft/pending/approved/rejected/active/archived)';
COMMENT ON COLUMN ai_audit_log.code_hash IS 'SHA-256 hash of generated code for integrity verification';
COMMENT ON COLUMN ai_audit_log.execution_result IS 'JSON result of code execution if applicable';
COMMENT ON COLUMN ai_audit_log.metadata IS 'Extended metadata as JSON';
"""


# ==================== 工厂函数 ====================

async def create_postgres_audit_storage(
    connection_string: str | None = None,
) -> PostgresAuditLogStorage:
    """
    创建 PostgreSQL 审计存储
    
    Args:
        connection_string: PostgreSQL 连接字符串
        
    Returns:
        PostgresAuditLogStorage 实例
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
    
    return PostgresAuditLogStorage(pool_or_connection=pool)
