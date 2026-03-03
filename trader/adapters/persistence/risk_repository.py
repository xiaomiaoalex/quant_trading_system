"""
Risk Events Repository - 风险事件持久化仓库
=============================================
负责 risk_events 和 risk_upgrades 的持久化。

特点：
- 优先使用 PostgreSQL 进行持久化
- PostgreSQL 不可用时自动回退到 InMemoryStorage
- 保证幂等性（dedup_key 唯一约束）

Note:
- 日志记录待 Sprint 2 补齐（当前仅保证功能回退）
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.adapters.persistence.postgres import (
    PostgreSQLStorage,
    is_postgres_available,
    _get_pool,
    check_postgres_connection,
)

logger = logging.getLogger(__name__)


class RiskEventRepository:
    """
    Risk Events Repository - 风险事件持久化仓库
    
    职责：
    - 管理 risk_events 的持久化
    - 管理 risk_upgrades 的持久化
    - 提供幂等性保证（dedup_key 唯一约束）
    - PostgreSQL 不可用时自动回退到 InMemoryStorage
    """

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._memory_storage = storage or get_storage()
        self._postgres_storage: Optional[PostgreSQLStorage] = None
        self._use_postgres = False
        self._init_lock = asyncio.Lock()

    async def _ensure_postgres(self) -> bool:
        """确保 PostgreSQL 可用"""
        if self._use_postgres and self._postgres_storage is not None:
            return True
        
        async with self._init_lock:
            if self._use_postgres and self._postgres_storage is not None:
                return True
            
            is_available, msg = await check_postgres_connection(timeout=2.0)
            if is_available:
                try:
                    self._postgres_storage = PostgreSQLStorage()
                    await self._postgres_storage.connect()
                    self._use_postgres = True
                    logger.info("PostgreSQL connected successfully for risk events")
                    return True
                except Exception as e:
                    logger.warning(f"Failed to connect to PostgreSQL: {e}, falling back to in-memory storage")
                    self._use_postgres = False
                    self._postgres_storage = None
            
            logger.debug(f"PostgreSQL not available: {msg}")
            return False

    async def save_risk_event(self, event_data: Dict[str, Any]) -> Tuple[str, bool]:
        """
        保存风险事件（带幂等性保证）
        
        Args:
            event_data: 事件数据字典
            
        Returns:
            Tuple of (event_id, created) - created 为 True 表示新建，False 表示重复
        """
        if await self._ensure_postgres():
            try:
                return await self._postgres_storage.save_risk_event(event_data)
            except Exception as e:
                logger.warning(f"PostgreSQL save_risk_event failed: {e}, falling back to in-memory")
        
        dedup_key = event_data["dedup_key"]
        existing = self._memory_storage.risk_events_by_key.get(dedup_key)
        if existing is not None:
            return existing.get("event_id", ""), False

        now = datetime.now(timezone.utc).isoformat() + "Z"
        event = {
            **event_data,
            "ingested_at": now,
        }
        self._memory_storage.risk_events_by_key[dedup_key] = event
        return event.get("event_id", ""), True

    async def get_risk_event(self, dedup_key: str) -> Optional[Dict[str, Any]]:
        """
        获取风险事件
        
        Args:
            dedup_key: 去重键
            
        Returns:
            事件数据字典，如果不存在则返回 None
        """
        if await self._ensure_postgres():
            try:
                stored = await self._postgres_storage.get_risk_event(dedup_key)
                if stored:
                    full_data = stored.data if isinstance(stored.data, dict) else {}
                    return {
                        "event_id": stored.event_id,
                        "dedup_key": stored.dedup_key,
                        "scope": stored.scope,
                        "reason": stored.reason,
                        "recommended_level": stored.recommended_level,
                        "ingested_at": stored.ingested_at.isoformat() + "Z",
                        **full_data,
                    }
            except Exception as e:
                logger.warning(f"PostgreSQL get_risk_event failed: {e}, falling back to in-memory")
        
        return self._memory_storage.risk_events_by_key.get(dedup_key)

    async def save_upgrade_record(self, upgrade_key: str, upgrade_data: Dict[str, Any]) -> None:
        """
        保存升级记录（带幂等性保证）
        
        Args:
            upgrade_key: 升级键
            upgrade_data: 升级数据
        """
        if await self._ensure_postgres():
            try:
                await self._postgres_storage.save_upgrade_record(upgrade_key, upgrade_data)
                return
            except Exception as e:
                logger.warning(f"PostgreSQL save_upgrade_record failed: {e}, falling back to in-memory")
        
        now = datetime.now(timezone.utc).isoformat() + "Z"
        self._memory_storage.risk_upgrades[upgrade_key] = {
            **upgrade_data,
            "recorded_at": now,
        }

    async def get_upgrade_record(self, upgrade_key: str) -> Optional[Dict[str, Any]]:
        """
        获取升级记录
        
        Args:
            upgrade_key: 升级键
            
        Returns:
            升级记录数据，如果不存在则返回 None
        """
        if await self._ensure_postgres():
            try:
                stored = await self._postgres_storage.get_upgrade_record(upgrade_key)
                if stored:
                    return {
                        "upgrade_key": stored.upgrade_key,
                        "scope": stored.scope,
                        "level": stored.level,
                        "reason": stored.reason,
                        "dedup_key": stored.dedup_key,
                        "recorded_at": stored.recorded_at.isoformat() + "Z",
                    }
            except Exception as e:
                logger.warning(f"PostgreSQL get_upgrade_record failed: {e}, falling back to in-memory")
        
        return self._memory_storage.risk_upgrades.get(upgrade_key)


_repository_instance: Optional[RiskEventRepository] = None


def get_risk_event_repository() -> RiskEventRepository:
    """获取全局 RiskEventRepository 实例"""
    global _repository_instance
    if _repository_instance is None:
        _repository_instance = RiskEventRepository()
    return _repository_instance


def reset_risk_event_repository() -> None:
    """重置全局 RiskEventRepository 实例"""
    global _repository_instance
    _repository_instance = None
