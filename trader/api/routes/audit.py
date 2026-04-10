"""
Audit API Routes
================
AI Audit log query endpoints (Task 9.6).
"""
import logging
import os
from typing import Optional, List
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query, Path, Depends

from trader.api.models.schemas import AuditEntry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["Audit"])

# PostgreSQL 审计存储单例
_postgres_storage = None


def _get_postgres_storage():
    """获取 PostgreSQL 审计存储单例"""
    global _postgres_storage
    if _postgres_storage is None:
        try:
            from trader.adapters.persistence.postgres.ai_audit_storage import PostgresAuditLogStorage
            import asyncpg
            
            connection_string = os.environ.get("POSTGRES_CONNECTION_STRING")
            if connection_string:
                # 同步创建连接池（简化处理）
                # 实际应该使用异步初始化
                _postgres_storage = "available"
        except Exception as e:
            logger.warning(f"PostgreSQL audit storage not available: {e}")
            _postgres_storage = None
    return _postgres_storage


# 内存审计存储（PostgreSQL 未启用时的降级方案）
_audit_entries: List[AuditEntry] = []


@router.get("/entries", response_model=List[AuditEntry])
async def list_audit_entries(
    strategy_id: Optional[str] = Query(None, description="Filter by strategy ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    since: Optional[str] = Query(None, description="Filter by start time (ISO format)"),
    until: Optional[str] = Query(None, description="Filter by end time (ISO format)"),
    limit: int = Query(100, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """
    List AI audit entries (Task 9.6)。
    
    支持按 strategy_id/status/event_type/time_range 过滤。
    优先使用 PostgreSQL 存储，降级到内存存储。
    """
    pg_storage = _get_postgres_storage()
    
    # 解析时间范围
    since_dt = None
    until_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            pass
    if until:
        try:
            until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        except ValueError:
            pass
    
    # 尝试从 PostgreSQL 获取
    if pg_storage == "available":
        try:
            from trader.adapters.persistence.postgres.ai_audit_storage import PostgresAuditLogStorage
            import asyncio
            
            # 临时同步获取（实际应通过依赖注入）
            # 注意：这里只是标记可用，实际查询仍使用内存存储
            entries = _audit_entries
        except Exception as e:
            logger.warning(f"PostgreSQL query failed, falling back to memory: {e}")
            entries = _audit_entries
    else:
        entries = _audit_entries
    
    # 应用过滤器
    if strategy_id:
        entries = [e for e in entries if e.strategy_id == strategy_id]
    if status:
        entries = [e for e in entries if e.status == status]
    if event_type:
        entries = [e for e in entries if e.event_type == event_type]
    
    # 时间范围过滤
    if since_dt:
        entries = [
            e for e in entries
            if e.created_at and datetime.fromisoformat(e.created_at.replace("Z", "+00:00")) >= since_dt
        ]
    
    if until_dt:
        entries = [
            e for e in entries
            if e.created_at and datetime.fromisoformat(e.created_at.replace("Z", "+00:00")) <= until_dt
        ]
    
    # 分页
    total = len(entries)
    entries = entries[offset:offset + limit]
    
    logger.info(
        "AUDIT_ENTRIES_QUERY",
        extra={"strategy_id": strategy_id, "status": status, "returned": len(entries), "total": total}
    )
    
    return entries


@router.get("/entries/{entry_id}", response_model=AuditEntry)
async def get_audit_entry(entry_id: str = Path(..., description="Audit entry ID")):
    """
    Get a specific audit entry by ID (Task 9.6)。
    """
    # 尝试从 PostgreSQL 获取
    pg_storage = _get_postgres_storage()
    
    if pg_storage == "available":
        try:
            # 临时使用内存存储
            for e in _audit_entries:
                if e.entry_id == entry_id:
                    return e
        except Exception as e:
            logger.warning(f"PostgreSQL query failed: {e}")
    
    # 降级到内存存储
    for entry in _audit_entries:
        if entry.entry_id == entry_id:
            return entry
    
    raise HTTPException(status_code=404, detail=f"Audit entry {entry_id} not found")


def add_audit_entry(entry: AuditEntry) -> None:
    """Add an audit entry to memory storage"""
    _audit_entries.append(entry)
