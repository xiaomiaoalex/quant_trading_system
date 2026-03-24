"""
Feature Store - 版本化特征持久化仓库
===================================
负责特征的版本化管理与持久化。

特点：
- 优先使用 PostgreSQL 进行持久化
- PostgreSQL 不可用时自动回退到 InMemoryStorage
- 保证幂等性：同 key 同 value 幂等成功；同 key 不同 value 抛 FeatureVersionConflictError

约束：
- Core Plane 禁止 IO；IO 仅在 adapter/persistence/service
- Fail-Closed：异常路径不得 silent pass
- 写路径必须幂等，状态语义必须可验证
"""
import asyncio
import logging
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.adapters.persistence.postgres import (
    PostgreSQLStorage,
    is_postgres_available,
    check_postgres_connection,
)

logger = logging.getLogger(__name__)


class FeatureVersionConflictError(Exception):
    """Raised when the same feature key has different values"""
    pass


@dataclass
class FeatureRecord:
    """Feature record representation"""
    symbol: str
    feature_name: str
    version: str
    ts_ms: int
    value: Any
    meta: Dict[str, Any]


class FeatureStore:
    """
    Feature Store - 版本化特征持久化仓库
    
    职责：
    - 管理特征的持久化
    - 提供版本化特征读写
    - 同 key 同 value 幂等成功
    - 同 key 不同 value 抛 FeatureVersionConflictError
    - PostgreSQL 不可用时自动回退到 InMemoryStorage
    """

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._memory_storage = storage or get_storage()
        self._postgres_storage: Optional[PostgreSQLStorage] = None
        self._use_postgres = False
        self._init_lock: Optional[asyncio.Lock] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _make_key(self, symbol: str, feature_name: str, version: str, ts_ms: int) -> str:
        """Generate unique key for feature"""
        return f"{symbol}:{feature_name}:{version}:{ts_ms}"

    def _make_value_hash(self, value: Any) -> str:
        """Generate hash for feature value"""
        value_str = json.dumps(value, sort_keys=True, default=str)
        return hashlib.sha256(value_str.encode()).hexdigest()[:16]

    async def _ensure_postgres(self) -> bool:
        """Ensure PostgreSQL is available"""
        current_loop = asyncio.get_running_loop()

        if self._loop is not None and self._loop is not current_loop:
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
                    logger.info("PostgreSQL connected successfully for feature store")
                    return True
                except Exception as e:
                    logger.warning(f"Failed to connect to PostgreSQL: {e}, falling back to in-memory storage")
                    self._use_postgres = False
                    self._postgres_storage = None
            
            logger.debug(f"PostgreSQL not available: {msg}")
            return False

    async def _reset_postgres_connection(self) -> None:
        """Reset cached PostgreSQL storage when loop/context changes."""
        if self._postgres_storage is not None:
            try:
                await self._postgres_storage.disconnect()
            except Exception as e:
                logger.debug(f"Failed to disconnect PostgreSQL storage during reset: {e}")
            self._postgres_storage = None
            self._use_postgres = False
            self._loop = None
            self._init_lock = None

    def _check_and_store_memory(
        self,
        symbol: str,
        feature_name: str,
        version: str,
        ts_ms: int,
        value: Any,
        meta: Dict[str, Any],
    ) -> Tuple[bool, bool]:
        """
        Check and store in memory storage.
        
        Returns:
            Tuple of (created: bool, is_duplicate: bool)
        """
        key = self._make_key(symbol, feature_name, version, ts_ms)
        value_hash = self._make_value_hash(value)
        
        existing = self._memory_storage.feature_values_by_key.get(key)
        if existing is not None:
            existing_hash = existing.get("value_hash")
            if existing_hash == value_hash:
                return False, True
            else:
                raise FeatureVersionConflictError(
                    f"Feature version conflict for {key}: existing value hash {existing_hash} != new value hash {value_hash}"
                )
        
        now = datetime.now(timezone.utc).isoformat() + "Z"
        feature = {
            "symbol": symbol,
            "feature_name": feature_name,
            "version": version,
            "ts_ms": ts_ms,
            "value": value,
            "meta": meta,
            "value_hash": value_hash,
            "ingested_at": now,
        }
        self._memory_storage.feature_values_by_key[key] = feature
        return True, False

    async def write_feature(
        self,
        symbol: str,
        feature_name: str,
        version: str,
        ts_ms: int,
        value: Any,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, bool]:
        """
        Write a feature value with version control.
        
        语义：同 key 同 value 幂等成功；同 key 不同 value 抛 FeatureVersionConflictError
        
        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            feature_name: Feature name (e.g., "ema_20", "volume_ratio")
            version: Feature version (e.g., "v1", "v2")
            ts_ms: Timestamp in milliseconds
            value: Feature value (any JSON-serializable type)
            meta: Optional metadata dictionary
            
        Returns:
            Tuple of (created: bool, is_duplicate: bool)
            - created=True, is_duplicate=False: 新写入
            - created=False, is_duplicate=True: 幂等重复（相同 key 相同 value）
            
        Raises:
            FeatureVersionConflictError: 同 key 不同 value
        """
        meta = meta or {}
        
        if await self._ensure_postgres():
            try:
                return await self._postgres_write_feature(
                    symbol, feature_name, version, ts_ms, value, meta
                )
            except Exception as e:
                logger.warning(f"PostgreSQL write_feature failed: {e}, falling back to in-memory")
        
        return self._check_and_store_memory(symbol, feature_name, version, ts_ms, value, meta)

    async def _postgres_write_feature(
        self,
        symbol: str,
        feature_name: str,
        version: str,
        ts_ms: int,
        value: Any,
        meta: Dict[str, Any],
    ) -> Tuple[bool, bool]:
        """Write feature to PostgreSQL"""
        key = self._make_key(symbol, feature_name, version, ts_ms)
        value_hash = self._make_value_hash(value)
        
        async with self._postgres_storage._pool.acquire() as conn:
            existing = await conn.fetchrow(
                """
                SELECT value_hash FROM feature_values 
                WHERE symbol = $1 AND feature_name = $2 AND version = $3 AND ts_ms = $4
                """,
                symbol, feature_name, version, ts_ms,
            )
            
            if existing is not None:
                if existing["value_hash"] == value_hash:
                    return False, True
                else:
                    raise FeatureVersionConflictError(
                        f"Feature version conflict for {key}: "
                        f"existing value hash {existing['value_hash']} != new value hash {value_hash}"
                    )
            
            await conn.execute(
                """
                INSERT INTO feature_values (symbol, feature_name, version, ts_ms, value, meta, value_hash)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                symbol, feature_name, version, ts_ms,
                json.dumps(value), json.dumps(meta), value_hash,
            )
        
        return True, False

    async def read_feature(
        self,
        symbol: str,
        feature_name: str,
        version: str,
        ts_ms: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Read a feature value at specific timestamp.
        
        Args:
            symbol: Trading symbol
            feature_name: Feature name
            version: Feature version
            ts_ms: Timestamp in milliseconds
            
        Returns:
            Feature record or None if not found
        """
        if await self._ensure_postgres():
            try:
                return await self._postgres_read_feature(symbol, feature_name, version, ts_ms)
            except Exception as e:
                logger.warning(f"PostgreSQL read_feature failed: {e}, falling back to in-memory")
        
        key = self._make_key(symbol, feature_name, version, ts_ms)
        feature = self._memory_storage.feature_values_by_key.get(key)
        if feature is None:
            return None
        
        return {
            "symbol": feature["symbol"],
            "feature_name": feature["feature_name"],
            "version": feature["version"],
            "ts_ms": feature["ts_ms"],
            "value": feature["value"],
            "meta": feature["meta"],
        }

    async def _postgres_read_feature(
        self,
        symbol: str,
        feature_name: str,
        version: str,
        ts_ms: int,
    ) -> Optional[Dict[str, Any]]:
        """Read feature from PostgreSQL"""
        async with self._postgres_storage._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT symbol, feature_name, version, ts_ms, value, meta
                FROM feature_values 
                WHERE symbol = $1 AND feature_name = $2 AND version = $3 AND ts_ms = $4
                """,
                symbol, feature_name, version, ts_ms,
            )
            
            if row is None:
                return None
            
            return {
                "symbol": row["symbol"],
                "feature_name": row["feature_name"],
                "version": row["version"],
                "ts_ms": row["ts_ms"],
                "value": json.loads(row["value"]) if isinstance(row["value"], str) else row["value"],
                "meta": json.loads(row["meta"]) if isinstance(row["meta"], str) else row["meta"],
            }

    async def list_versions(
        self,
        symbol: str,
        feature_name: str,
    ) -> List[Dict[str, str]]:
        """
        List all available versions for a feature.
        
        Args:
            symbol: Trading symbol
            feature_name: Feature name
            
        Returns:
            List of version info dictionaries with 'version' and 'latest_ts_ms' keys
        """
        if await self._ensure_postgres():
            try:
                return await self._postgres_list_versions(symbol, feature_name)
            except Exception as e:
                logger.warning(f"PostgreSQL list_versions failed: {e}, falling back to in-memory")
        
        memory_versions: Dict[str, int] = {}
        prefix = f"{symbol}:{feature_name}:"
        
        for key, feature in self._memory_storage.feature_values_by_key.items():
            if key.startswith(prefix):
                version = feature["version"]
                ts_ms = feature["ts_ms"]
                if version not in memory_versions or ts_ms > memory_versions[version]:
                    memory_versions[version] = ts_ms
        
        return [
            {"version": version, "latest_ts_ms": ts_ms}
            for version, ts_ms in sorted(memory_versions.items())
        ]

    async def _postgres_list_versions(
        self,
        symbol: str,
        feature_name: str,
    ) -> List[Dict[str, str]]:
        """List versions from PostgreSQL"""
        async with self._postgres_storage._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT version, MAX(ts_ms) as latest_ts_ms
                FROM feature_values 
                WHERE symbol = $1 AND feature_name = $2
                GROUP BY version
                ORDER BY version
                """,
                symbol, feature_name,
            )
            
            return [
                {"version": row["version"], "latest_ts_ms": row["latest_ts_ms"]}
                for row in rows
            ]


def get_feature_store() -> FeatureStore:
    """Get or create the global FeatureStore instance"""
    if not hasattr(get_feature_store, "_instance"):
        get_feature_store._instance = FeatureStore()
    return get_feature_store._instance
