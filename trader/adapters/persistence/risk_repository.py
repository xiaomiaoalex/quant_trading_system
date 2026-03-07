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
from typing import Optional, Dict, Any, Tuple, List

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
        self._init_lock: Optional[asyncio.Lock] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def _reset_postgres_connection(self) -> None:
        """Reset cached PostgreSQL storage when loop/context changes."""
        if self._postgres_storage is not None:
            try:
                await self._postgres_storage.disconnect()
            except Exception as e:
                logger.debug(f"Failed to disconnect PostgreSQL storage during reset: {e}")
                pool = getattr(self._postgres_storage, "_pool", None)
                if pool is not None:
                    try:
                        pool.terminate()
                    except Exception as terminate_error:
                        logger.debug(f"Failed to terminate PostgreSQL pool during reset: {terminate_error}")
        self._postgres_storage = None
        self._use_postgres = False

    async def _ensure_postgres(self) -> bool:
        """确保 PostgreSQL 可用"""
        current_loop = asyncio.get_running_loop()

        if self._loop is not current_loop:
            await self._reset_postgres_connection()
            self._loop = current_loop
            self._init_lock = asyncio.Lock()

        if self._use_postgres and self._postgres_storage is not None:
            return True

        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

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

    async def try_record_upgrade(self, upgrade_key: str, upgrade_data: Dict[str, Any]) -> bool:
        """
        Try to record an upgrade action. Returns True if first write, False if already exists.
        
        Args:
            upgrade_key: Unique upgrade key
            upgrade_data: Dictionary containing:
                - scope: Risk scope
                - level: Target level
                - reason: Upgrade reason
                - dedup_key: Related dedup key
                
        Returns:
            True if this is the first time recording this upgrade_key, False if already exists
        """
        if await self._ensure_postgres():
            try:
                result = await self._postgres_storage.try_record_upgrade(upgrade_key, upgrade_data)
                return result
            except Exception as e:
                logger.warning(f"PostgreSQL try_record_upgrade failed: {e}, falling back to in-memory")
        
        return self._memory_storage.try_record_upgrade(upgrade_key, upgrade_data)

    async def try_record_upgrade_with_effect(self, upgrade_key: str, scope: str, level: int,
                                            reason: str, dedup_key: str) -> Tuple[bool, bool]:
        """
        Atomically record upgrade and side-effect intent in a single transaction.
        
        Returns:
            Tuple of (is_first_upgrade, is_first_effect)
        """
        if await self._ensure_postgres():
            try:
                return await self._postgres_storage.try_record_upgrade_with_effect(
                    upgrade_key, scope, level, reason, dedup_key
                )
            except Exception as e:
                logger.warning(f"PostgreSQL try_record_upgrade_with_effect failed: {e}, falling back to in-memory")
        
        return self._memory_storage.try_record_upgrade_with_effect(
            upgrade_key, scope, level, reason, dedup_key
        )

    async def mark_effect_applied(self, upgrade_key: str) -> None:
        """Mark side-effect as successfully applied"""
        if await self._ensure_postgres():
            try:
                await self._postgres_storage.mark_effect_applied(upgrade_key)
                return
            except Exception as e:
                logger.warning(f"PostgreSQL mark_effect_applied failed: {e}, falling back to in-memory")
        
        self._memory_storage.mark_effect_applied(upgrade_key)

    async def mark_effect_failed(self, upgrade_key: str, error: str) -> None:
        """Mark side-effect as failed with error message"""
        if await self._ensure_postgres():
            try:
                await self._postgres_storage.mark_effect_failed(upgrade_key, error)
                return
            except Exception as e:
                logger.warning(f"PostgreSQL mark_effect_failed failed: {e}, falling back to in-memory")
        
        self._memory_storage.mark_effect_failed(upgrade_key, error)

    async def get_pending_effects(self) -> List[Dict[str, Any]]:
        """Get all pending or failed effects for recovery"""
        if await self._ensure_postgres():
            try:
                return await self._postgres_storage.get_pending_effects()
            except Exception as e:
                logger.warning(f"PostgreSQL get_pending_effects failed: {e}, falling back to in-memory")
        
        return self._memory_storage.get_pending_effects()

    async def ingest_event_with_upgrade(self, event_data: Dict[str, Any], 
                                       upgrade_key: str, upgrade_level: int) -> Tuple[Optional[str], bool, bool, bool]:
        """
        Atomically ingest risk event and record upgrade with effect.
        
        Returns:
            Tuple of (event_id, created, is_first_upgrade, is_first_effect)
        """
        if await self._ensure_postgres():
            try:
                return await self._postgres_storage.ingest_event_with_upgrade(
                    event_data, upgrade_key, upgrade_level
                )
            except Exception as e:
                logger.warning(f"PostgreSQL ingest_event_with_upgrade failed: {e}, falling back to in-memory")
        
        return self._memory_storage.ingest_event_with_upgrade(
            event_data, upgrade_key, upgrade_level
        )


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
    if _repository_instance is not None and _repository_instance._postgres_storage is not None:
        try:
            asyncio.run(_repository_instance._reset_postgres_connection())
        except RuntimeError:
            _repository_instance._postgres_storage = None
            _repository_instance._use_postgres = False
            _repository_instance._loop = None
            _repository_instance._init_lock = None
    _repository_instance = None
