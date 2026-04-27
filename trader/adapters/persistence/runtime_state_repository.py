"""PG-first runtime state repository."""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from trader.adapters.persistence.postgres import PostgreSQLStorage, check_postgres_connection
from trader.storage.in_memory import InMemoryStorage, get_storage

logger = logging.getLogger(__name__)


class RuntimeStateRepository:
    """Persist strategy runtime states keyed by deployment_id."""

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
                logger.debug("PostgreSQL unavailable for runtime state: %s", msg)
                return False
            try:
                self._postgres_storage = PostgreSQLStorage()
                await self._postgres_storage.connect()
                await self._ensure_tables()
                self._use_postgres = True
                return True
            except Exception as exc:
                logger.warning("Failed to connect PostgreSQL for runtime state: %s", exc)
                self._clear_postgres_state()
                return False

    async def _ensure_tables(self) -> None:
        async with self._postgres_storage._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_runtime_states (
                    deployment_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config JSONB DEFAULT '{}',
                    symbols JSONB DEFAULT '[]',
                    account_id TEXT,
                    venue TEXT,
                    mode TEXT,
                    env TEXT,
                    started_at BIGINT,
                    last_tick_at BIGINT,
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_runtime_states_strategy_id ON strategy_runtime_states(strategy_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_runtime_states_status ON strategy_runtime_states(status)")

    async def save_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        deployment_id = state.get("deployment_id") or state.get("strategy_id")
        if not deployment_id:
            raise ValueError("runtime state requires deployment_id or strategy_id")
        self._memory_storage.strategy_runtime_states[deployment_id] = state.copy()

        if await self._ensure_postgres():
            try:
                async with self._postgres_storage._pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO strategy_runtime_states
                        (deployment_id, strategy_id, status, config, symbols,
                         account_id, venue, mode, env, started_at, last_tick_at, updated_at)
                        VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9, $10, $11, NOW())
                        ON CONFLICT (deployment_id) DO UPDATE SET
                            strategy_id = EXCLUDED.strategy_id,
                            status = EXCLUDED.status,
                            config = EXCLUDED.config,
                            symbols = EXCLUDED.symbols,
                            account_id = EXCLUDED.account_id,
                            venue = EXCLUDED.venue,
                            mode = EXCLUDED.mode,
                            env = EXCLUDED.env,
                            started_at = EXCLUDED.started_at,
                            last_tick_at = EXCLUDED.last_tick_at,
                            updated_at = NOW()
                        """,
                        deployment_id,
                        state.get("strategy_id", ""),
                        state.get("status", "RUNNING"),
                        json.dumps(state.get("config", {})),
                        json.dumps(state.get("symbols", [])),
                        state.get("account_id"),
                        state.get("venue"),
                        state.get("mode"),
                        state.get("env"),
                        state.get("started_at"),
                        state.get("last_tick_at"),
                    )
            except Exception as exc:
                logger.warning("PostgreSQL save runtime state failed (best-effort): %s", exc)
        return self._memory_storage.strategy_runtime_states[deployment_id]

    async def save_strategy_runtime_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return await self.save_state(state)

    async def get_state(self, deployment_id: str) -> Optional[Dict[str, Any]]:
        if await self._ensure_postgres():
            try:
                async with self._postgres_storage._pool.acquire() as conn:
                    row = await conn.fetchrow(
                        """
                        SELECT deployment_id, strategy_id, status, config, symbols,
                               account_id, venue, mode, env, started_at, last_tick_at
                        FROM strategy_runtime_states
                        WHERE deployment_id = $1
                        """,
                        deployment_id,
                    )
                if row:
                    return self._row_to_state(row)
            except Exception as exc:
                logger.warning("PostgreSQL get runtime state failed, falling back to memory: %s", exc)
        return self._memory_storage.strategy_runtime_states.get(deployment_id)

    async def get_strategy_runtime_state(self, deployment_id: str) -> Optional[Dict[str, Any]]:
        return await self.get_state(deployment_id)

    def _row_to_state(self, row: Any) -> Dict[str, Any]:
        config = row["config"]
        symbols = row["symbols"]
        return {
            "deployment_id": row["deployment_id"],
            "strategy_id": row["strategy_id"],
            "status": row["status"],
            "config": json.loads(config) if isinstance(config, str) else (config or {}),
            "symbols": json.loads(symbols) if isinstance(symbols, str) else (symbols or []),
            "account_id": row["account_id"],
            "venue": row["venue"],
            "mode": row["mode"],
            "env": row["env"],
            "started_at": row["started_at"],
            "last_tick_at": row["last_tick_at"],
        }

    async def list_running_states(self) -> List[Dict[str, Any]]:
        if await self._ensure_postgres():
            try:
                async with self._postgres_storage._pool.acquire() as conn:
                    rows = await conn.fetch(
                        """
                        SELECT deployment_id, strategy_id, status, config, symbols,
                               account_id, venue, mode, env, started_at, last_tick_at
                        FROM strategy_runtime_states
                        WHERE status = 'RUNNING'
                        """
                    )
                return [self._row_to_state(row) for row in rows]
            except Exception as exc:
                logger.warning("PostgreSQL list running states failed, falling back to memory: %s", exc)
        return self._memory_storage.list_running_strategy_states()

    async def list_running_strategy_states(self) -> List[Dict[str, Any]]:
        return await self.list_running_states()

    async def list_all_states(self) -> List[Dict[str, Any]]:
        if await self._ensure_postgres():
            try:
                async with self._postgres_storage._pool.acquire() as conn:
                    rows = await conn.fetch(
                        """
                        SELECT deployment_id, strategy_id, status, config, symbols,
                               account_id, venue, mode, env, started_at, last_tick_at
                        FROM strategy_runtime_states
                        ORDER BY updated_at DESC
                        """
                    )
                return [self._row_to_state(row) for row in rows]
            except Exception as exc:
                logger.warning("PostgreSQL list states failed, falling back to memory: %s", exc)
        return self._memory_storage.list_strategy_runtime_states()

    async def list_strategy_runtime_states(self) -> List[Dict[str, Any]]:
        return await self.list_all_states()

    async def delete_state(self, deployment_id: str) -> bool:
        self._memory_storage.strategy_runtime_states.pop(deployment_id, None)
        if await self._ensure_postgres():
            async with self._postgres_storage._pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM strategy_runtime_states WHERE deployment_id = $1",
                    deployment_id,
                )
            return result.endswith("1")
        return True

    async def delete_strategy_runtime_state(self, deployment_id: str) -> bool:
        return await self.delete_state(deployment_id)


_repository_instance: Optional[RuntimeStateRepository] = None


def get_runtime_state_repository() -> RuntimeStateRepository:
    global _repository_instance
    if _repository_instance is None:
        _repository_instance = RuntimeStateRepository()
    return _repository_instance


def reset_runtime_state_repository() -> None:
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
