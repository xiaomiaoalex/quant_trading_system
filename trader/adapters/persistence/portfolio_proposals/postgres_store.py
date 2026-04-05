"""
PostgreSQL Store - PostgreSQL 存储实现
======================================

此模块实现基于 PostgreSQL 的 PortfolioProposalStore。

设计原则：
1. 依赖现有 PostgreSQLStorage
2. 不假设 PostgreSQLStorage 有 fetch/fetchone/execute 方法
3. 必须按真实 API 使用：connect() + pool.acquire() + conn.fetch/fetchrow/execute
4. SQL 实现清晰
5. 有从数据库 row 到领域模型的映射方法

数据库表结构：
```sql
CREATE TABLE portfolio_proposals (
    proposal_id VARCHAR(255) PRIMARY KEY,
    proposal_type VARCHAR(50) NOT NULL,
    specialist_type VARCHAR(100) DEFAULT '',
    payload JSONB NOT NULL,
    status VARCHAR(50) NOT NULL,
    feature_version VARCHAR(100) DEFAULT '',
    prompt_version VARCHAR(100) DEFAULT '',
    trace_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    
    -- 索引
    CONSTRAINT uq_proposal_content_hash UNIQUE (content_hash)
);

CREATE INDEX idx_portfolio_proposals_status ON portfolio_proposals(status);
CREATE INDEX idx_portfolio_proposals_type ON portfolio_proposals(proposal_type);
CREATE INDEX idx_portfolio_proposals_specialist ON portfolio_proposals(specialist_type);
CREATE INDEX idx_portfolio_proposals_created_at ON portfolio_proposals(created_at DESC);
```
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg
    from trader.adapters.persistence.postgres import PostgreSQLStorage

from trader.adapters.persistence.portfolio_proposals.models import (
    ProposalModel,
    ProposalStatus,
    ProposalType,
)
from trader.adapters.persistence.portfolio_proposals.store_protocol import (
    PortfolioProposalStore,
)

logger = logging.getLogger(__name__)


class DecimalEncoder(json.JSONEncoder):
    """JSON 编码器，支持 Decimal 类型"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def _decode_proposal_row(row: asyncpg.Record) -> ProposalModel:
    """
    将数据库行解码为 ProposalModel
    
    Args:
        row: asyncpg Record 对象
        
    Returns:
        ProposalModel 实例
    """
    # 获取列名（兼容不同查询方式）
    data = {
        "proposal_id": row["proposal_id"],
        "proposal_type": row["proposal_type"],
        "specialist_type": row["specialist_type"] or "",
        "payload": row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"]),
        "status": row["status"],
        "feature_version": row["feature_version"] or "",
        "prompt_version": row["prompt_version"] or "",
        "trace_id": row["trace_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "content_hash": row["content_hash"],
    }
    return ProposalModel.from_dict(data)


class PostgresPortfolioProposalStore:
    """
    PostgreSQL 存储实现
    
    依赖现有 PostgreSQLStorage，按其真实 API 工作：
    - 调用 connect() 建立连接
    - 使用 pool.acquire() 获取连接
    - 在连接上执行 SQL
    
    语义约束：
    - save() 是 upsert（ON CONFLICT DO UPDATE）
    - delete() 对不存在的 id 静默成功
    - list_*() 按 created_at DESC 排序
    """
    
    # SQL 模板
    _CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS portfolio_proposals (
            proposal_id VARCHAR(255) PRIMARY KEY,
            proposal_type VARCHAR(50) NOT NULL,
            specialist_type VARCHAR(100) DEFAULT '',
            payload JSONB NOT NULL,
            status VARCHAR(50) NOT NULL,
            feature_version VARCHAR(100) DEFAULT '',
            prompt_version VARCHAR(100) DEFAULT '',
            trace_id VARCHAR(255) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            content_hash VARCHAR(64) NOT NULL
        )
    """
    
    _CREATE_INDEXES_SQL = [
        """
        CREATE INDEX IF NOT EXISTS idx_portfolio_proposals_status 
        ON portfolio_proposals(status)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_portfolio_proposals_type 
        ON portfolio_proposals(proposal_type)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_portfolio_proposals_specialist 
        ON portfolio_proposals(specialist_type)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_portfolio_proposals_created_at 
        ON portfolio_proposals(created_at DESC)
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolio_proposals_content_hash 
        ON portfolio_proposals(content_hash)
        """,
    ]
    
    _UPSERT_SQL = """
        INSERT INTO portfolio_proposals (
            proposal_id, proposal_type, specialist_type, payload,
            status, feature_version, prompt_version, trace_id,
            created_at, updated_at, content_hash
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (proposal_id) DO UPDATE SET
            proposal_type = EXCLUDED.proposal_type,
            specialist_type = EXCLUDED.specialist_type,
            payload = EXCLUDED.payload,
            status = EXCLUDED.status,
            feature_version = EXCLUDED.feature_version,
            prompt_version = EXCLUDED.prompt_version,
            updated_at = EXCLUDED.updated_at,
            content_hash = EXCLUDED.content_hash
    """
    
    _GET_BY_ID_SQL = """
        SELECT proposal_id, proposal_type, specialist_type, payload,
               status, feature_version, prompt_version, trace_id,
               created_at, updated_at, content_hash
        FROM portfolio_proposals
        WHERE proposal_id = $1
    """
    
    _LIST_BY_STATUS_SQL = """
        SELECT proposal_id, proposal_type, specialist_type, payload,
               status, feature_version, prompt_version, trace_id,
               created_at, updated_at, content_hash
        FROM portfolio_proposals
        WHERE status = $1
        ORDER BY created_at DESC
        LIMIT $2 OFFSET $3
    """
    
    _LIST_BY_TYPE_SQL = """
        SELECT proposal_id, proposal_type, specialist_type, payload,
               status, feature_version, prompt_version, trace_id,
               created_at, updated_at, content_hash
        FROM portfolio_proposals
        WHERE proposal_type = $1
        ORDER BY created_at DESC
        LIMIT $2 OFFSET $3
    """
    
    _LIST_BY_SPECIALIST_SQL = """
        SELECT proposal_id, proposal_type, specialist_type, payload,
               status, feature_version, prompt_version, trace_id,
               created_at, updated_at, content_hash
        FROM portfolio_proposals
        WHERE proposal_type = $1 AND specialist_type = $2
        ORDER BY created_at DESC
        LIMIT $3 OFFSET $4
    """
    
    _DELETE_SQL = """
        DELETE FROM portfolio_proposals WHERE proposal_id = $1
    """
    
    _COUNT_SQL = """
        SELECT COUNT(*) FROM portfolio_proposals
    """
    
    def __init__(
        self,
        postgres_storage: Optional[PostgreSQLStorage] = None,
        auto_initialize: bool = True,
    ) -> None:
        """
        初始化 PostgresStore
        
        Args:
            postgres_storage: 现有的 PostgreSQLStorage 实例。
                              如果为 None，将在 initialize() 时创建。
            auto_initialize: 是否在 __init__ 时自动初始化。
                            设为 False 允许延迟初始化。
        """
        self._storage: Optional[PostgreSQLStorage] = postgres_storage
        self._initialized = False
        self._init_lock = asyncio.Lock()
        
        if auto_initialize and postgres_storage is not None:
            # 同步环境下的简单初始化检查
            pass
    
    @property
    def is_initialized(self) -> bool:
        """检查是否已初始化"""
        return self._initialized
    
    async def initialize(self) -> None:
        """
        初始化存储
        
        如果没有提供 postgres_storage，则创建一个新的。
        然后创建表和索引（如果不存在）。
        """
        if self._initialized:
            return
        
        async with self._init_lock:
            if self._initialized:
                return
            
            if self._storage is None:
                from trader.adapters.persistence.postgres import PostgreSQLStorage
                self._storage = PostgreSQLStorage()
            
            # 建立连接
            await self._storage.connect()
            
            # 创建表和索引
            async with self._storage.acquire() as conn:
                # 创建表
                await conn.execute(self._CREATE_TABLE_SQL)
                
                # 创建索引
                for idx_sql in self._CREATE_INDEXES_SQL:
                    try:
                        await conn.execute(idx_sql)
                    except Exception as e:
                        # 索引可能已存在，忽略错误
                        logger.debug(f"Index creation skipped: {e}")
            
            self._initialized = True
            logger.info("PostgresPortfolioProposalStore initialized")
    
    async def _ensure_initialized(self) -> None:
        """确保已初始化"""
        if not self._initialized:
            await self.initialize()
    
    def _model_to_params(self, proposal: ProposalModel) -> tuple:
        """
        将 ProposalModel 转换为 SQL 参数
        
        Args:
            proposal: 提案模型
            
        Returns:
            (proposal_id, proposal_type, specialist_type, payload,
             status, feature_version, prompt_version, trace_id,
             created_at, updated_at, content_hash)
        """
        return (
            proposal.proposal_id,
            proposal.proposal_type.value,
            proposal.specialist_type,
            json.dumps(proposal.payload, cls=DecimalEncoder),
            proposal.status.value,
            proposal.feature_version,
            proposal.prompt_version,
            proposal.trace_id,
            proposal.created_at,
            proposal.updated_at,
            proposal.content_hash,
        )
    
    async def save(self, proposal: ProposalModel) -> str:
        """
        保存提案（Upsert）
        
        Args:
            proposal: 提案模型
            
        Returns:
            proposal_id
        """
        await self._ensure_initialized()
        
        proposal.save()  # 更新 updated_at
        
        async with self._storage.acquire() as conn:
            await conn.execute(
                self._UPSERT_SQL,
                *self._model_to_params(proposal)
            )
        
        return proposal.proposal_id
    
    async def get_by_id(self, proposal_id: str) -> Optional[ProposalModel]:
        """
        按 ID 获取提案
        
        Args:
            proposal_id: 提案 ID
            
        Returns:
            提案模型，如果不存在则返回 None
        """
        await self._ensure_initialized()
        
        async with self._storage.acquire() as conn:
            row = await conn.fetchrow(self._GET_BY_ID_SQL, proposal_id)
        
        if row is None:
            return None
        
        return _decode_proposal_row(row)
    
    async def list_by_status(
        self,
        status: ProposalStatus,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ProposalModel]:
        """
        按状态列出提案
        
        Args:
            status: 提案状态
            limit: 返回数量限制
            offset: 偏移量
            
        Returns:
            提案列表，按 created_at DESC 排序
        """
        await self._ensure_initialized()
        
        async with self._storage.acquire() as conn:
            rows = await conn.fetch(
                self._LIST_BY_STATUS_SQL,
                status.value,
                limit,
                offset,
            )
        
        return [_decode_proposal_row(row) for row in rows]
    
    async def list_by_type(
        self,
        proposal_type: ProposalType,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ProposalModel]:
        """
        按类型列出提案
        
        Args:
            proposal_type: 提案类型
            limit: 返回数量限制
            offset: 偏移量
            
        Returns:
            提案列表，按 created_at DESC 排序
        """
        await self._ensure_initialized()
        
        async with self._storage.acquire() as conn:
            rows = await conn.fetch(
                self._LIST_BY_TYPE_SQL,
                proposal_type.value,
                limit,
                offset,
            )
        
        return [_decode_proposal_row(row) for row in rows]
    
    async def list_by_specialist(
        self,
        specialist_type: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ProposalModel]:
        """
        按 Specialist 类型列出提案（仅 SLEEVE 类型）
        
        Args:
            specialist_type: Specialist 类型
            limit: 返回数量限制
            offset: 偏移量
            
        Returns:
            提案列表，按 created_at DESC 排序
        """
        await self._ensure_initialized()
        
        async with self._storage.acquire() as conn:
            rows = await conn.fetch(
                self._LIST_BY_SPECIALIST_SQL,
                ProposalType.SLEEVE.value,
                specialist_type,
                limit,
                offset,
            )
        
        return [_decode_proposal_row(row) for row in rows]
    
    async def delete(self, proposal_id: str) -> None:
        """
        删除提案
        
        对不存在的 proposal_id 静默成功（幂等）。
        
        Args:
            proposal_id: 提案 ID
        """
        await self._ensure_initialized()
        
        async with self._storage.acquire() as conn:
            await conn.execute(self._DELETE_SQL, proposal_id)
    
    async def exists(self, proposal_id: str) -> bool:
        """
        检查提案是否存在
        
        Args:
            proposal_id: 提案 ID
            
        Returns:
            True 如果存在，否则 False
        """
        result = await self.get_by_id(proposal_id)
        return result is not None
    
    async def count(self) -> int:
        """
        统计提案总数
        
        Returns:
            提案总数
        """
        await self._ensure_initialized()
        
        async with self._storage.acquire() as conn:
            row = await conn.fetchrow(self._COUNT_SQL)
        
        return row["count"] if row else 0
    
    async def health_check(self) -> bool:
        """
        健康检查
        
        检查 PostgreSQL 连接是否可用。
        """
        try:
            await self._ensure_initialized()
            async with self._storage.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return False
    
    def __repr__(self) -> str:
        return f"PostgresPortfolioProposalStore(initialized={self._initialized})"