"""PG-first KillSwitch repository."""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trader.adapters.persistence.postgres import PostgreSQLStorage, check_postgres_connection
from trader.storage.in_memory import InMemoryStorage, get_storage

logger = logging.getLogger(__name__)


class KillSwitchRepository:
    """Persist KillSwitch state changes to PG audit log before updating cache."""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._memory_storage = storage or get_storage()
        self._postgres_storage: Optional[PostgreSQLStorage] = None
        self._use_postgres = False
        self._init_lock: Optional[asyncio.Lock] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _clear_postgres_state(self) -> None:
        self._postgres_storage = None
        self._use_postgres = False
        self._init_lock = None
        self._loop = None

    async def _reset_postgres_connection(self) -> None:
        if self._postgres_storage is not None:
            try:
                await self._postgres_storage.disconnect()
            except Exception:
                pool = getattr(self._postgres_storage, "_pool", None)
                if pool is not None:
                    pool.terminate()
        self._clear_postgres_state()

    def _terminate_postgres_connection(self) -> None:
        if self._postgres_storage is not None:
            pool = getattr(self._postgres_storage, "_pool", None)
            if pool is not None:
                pool.terminate()
            self._postgres_storage._pool = None
            self._postgres_storage._connected = False
        self._clear_postgres_state()

    async def _ensure_postgres(self) -> bool:
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
            ok, msg = await check_postgres_connection(timeout=2.0)
            if not ok:
                logger.debug("PostgreSQL unavailable for killswitch: %s", msg)
                return False
            try:
                self._postgres_storage = PostgreSQLStorage()
                await self._postgres_storage.connect()
                await self._ensure_tables()
                self._use_postgres = True
                return True
            except Exception as exc:
                logger.warning("Failed to connect PostgreSQL for killswitch: %s", exc)
                self._clear_postgres_state()
                return False

    async def _ensure_tables(self) -> None:
        async with self._postgres_storage._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS killswitch_log (
                    id SERIAL PRIMARY KEY,
                    scope TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    reason TEXT,
                    updated_by TEXT,
                    previous_level INTEGER NOT NULL DEFAULT 0,
                    ts_ms BIGINT NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_killswitch_log_scope ON killswitch_log(scope)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_killswitch_log_ts_ms ON killswitch_log(ts_ms)")

    async def save_state(
        self,
        scope: str,
        level: int,
        reason: Optional[str],
        updated_by: str,
        previous_level: int = 0,
    ) -> Dict[str, Any]:
        if not await self._ensure_postgres():
            raise RuntimeError(
                f"PostgreSQL unavailable for killswitch write (fail-closed). scope={scope}, level={level}"
            )

        ts_ms = int(time.time() * 1000)
        async with self._postgres_storage._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO killswitch_log (scope, level, reason, updated_by, previous_level, ts_ms)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                scope,
                level,
                reason,
                updated_by,
                previous_level,
                ts_ms,
            )

        state = {
            "scope": scope,
            "level": level,
            "reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat() + "Z",
            "updated_by": updated_by,
        }
        self._memory_storage.kill_switch_states[scope] = state
        return state

    async def save_state_best_effort(
        self,
        scope: str,
        level: int,
        reason: Optional[str],
        updated_by: str,
        previous_level: int = 0,
    ) -> Dict[str, Any]:
        try:
            return await self.save_state(scope, level, reason, updated_by, previous_level)
        except Exception as exc:
            logger.warning("KillSwitch best-effort write falling back to memory: %s", exc)
            state = {
                "scope": scope,
                "level": level,
                "reason": reason,
                "updated_at": datetime.now(timezone.utc).isoformat() + "Z",
                "updated_by": updated_by,
            }
            self._memory_storage.kill_switch_states[scope] = state
            return state

    def get_state(self, scope: str = "GLOBAL") -> Dict[str, Any]:
        state = self._memory_storage.kill_switch_states.get(scope)
        if state is not None:
            return state
        return {
            "scope": scope,
            "level": 0,
            "reason": None,
            "updated_at": datetime.now(timezone.utc).isoformat() + "Z",
            "updated_by": "system",
        }

    async def get_recent_changes(self, scope: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        if not await self._ensure_postgres():
            return []
        query = """
            SELECT id, scope, level, reason, updated_by, previous_level, ts_ms, created_at
            FROM killswitch_log
            WHERE 1=1
        """
        params: list[Any] = []
        if scope:
            params.append(scope)
            query += " AND scope = $1"
        query += f" ORDER BY ts_ms DESC LIMIT ${len(params) + 1}"
        params.append(limit)
        async with self._postgres_storage._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [dict(row) for row in rows]


_repository_instance: Optional[KillSwitchRepository] = None


def get_killswitch_repository() -> KillSwitchRepository:
    global _repository_instance
    if _repository_instance is None:
        _repository_instance = KillSwitchRepository()
    return _repository_instance


def reset_killswitch_repository() -> None:
    global _repository_instance
    if _repository_instance is None:
        return
    repo = _repository_instance
    if repo._postgres_storage is not None:
        try:
            asyncio.get_running_loop()
            repo._terminate_postgres_connection()
        except RuntimeError:
            try:
                asyncio.run(repo._reset_postgres_connection())
            except RuntimeError:
                repo._terminate_postgres_connection()
    _repository_instance = None
