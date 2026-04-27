"""PG-first execution repository."""
import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from trader.adapters.persistence.postgres import PostgreSQLStorage, check_postgres_connection
from trader.storage.in_memory import InMemoryStorage, get_storage

logger = logging.getLogger(__name__)


class ExecutionRepository:
    """Persist executions with cl_ord_id + exec_id idempotency."""

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
                logger.debug("PostgreSQL unavailable for executions: %s", msg)
                return False

            try:
                self._postgres_storage = PostgreSQLStorage()
                await self._postgres_storage.connect()
                self._use_postgres = True
                return True
            except Exception as exc:
                logger.warning("Failed to connect PostgreSQL for executions: %s", exc)
                self._clear_postgres_state()
                return False

    async def save_execution(self, execution_data: Dict[str, Any]) -> tuple[str, bool]:
        """Strict PG-first write. Raises when PG is unavailable."""
        if await self._ensure_postgres():
            try:
                return await self._postgres_storage.create_execution(execution_data)
            except Exception:
                logger.exception(
                    "PostgreSQL save_execution failed: cl_ord_id=%s exec_id=%s",
                    execution_data.get("cl_ord_id"),
                    execution_data.get("exec_id"),
                )
                raise
        raise RuntimeError(
            "PostgreSQL unavailable for execution write (fail-closed). "
            f"cl_ord_id={execution_data.get('cl_ord_id')}, exec_id={execution_data.get('exec_id')}"
        )

    async def save_execution_best_effort(self, execution_data: Dict[str, Any]) -> tuple[str, bool]:
        """Best-effort write for dev/test paths without configured PG."""
        if await self._ensure_postgres():
            try:
                return await self._postgres_storage.create_execution(execution_data)
            except Exception as exc:
                logger.warning("PostgreSQL save_execution failed, falling back to memory: %s", exc)

        execution_id = execution_data.get("execution_id") or str(uuid.uuid4())
        before = self._memory_storage.get_execution_dedup_stats().get("execution_dedup_hits", 0)
        result = self._memory_storage.create_execution({**execution_data, "execution_id": execution_id})
        after = self._memory_storage.get_execution_dedup_stats().get("execution_dedup_hits", before)
        created = after == before
        if not created:
            execution_id = result.get("execution_id") or execution_id
        return execution_id, created

    async def get_execution(self, cl_ord_id: str, exec_id: str) -> Optional[Dict[str, Any]]:
        if await self._ensure_postgres():
            try:
                return await self._postgres_storage.get_execution(cl_ord_id, exec_id)
            except Exception as exc:
                logger.warning("PostgreSQL get_execution failed, falling back to memory: %s", exc)
        return self._memory_storage.execution_by_key.get(f"{cl_ord_id}:{exec_id}")

    async def list_executions(
        self,
        cl_ord_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        since_ts_ms: Optional[int] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        if await self._ensure_postgres():
            try:
                return await self._postgres_storage.list_executions(
                    cl_ord_id=cl_ord_id,
                    strategy_id=strategy_id,
                    since_ts_ms=since_ts_ms,
                    limit=limit,
                )
            except Exception as exc:
                logger.warning("PostgreSQL list_executions failed, falling back to memory: %s", exc)
        return self._memory_storage.list_executions(cl_ord_id=cl_ord_id, since_ts_ms=since_ts_ms, limit=limit)


_repository_instance: Optional[ExecutionRepository] = None


def get_execution_repository() -> ExecutionRepository:
    global _repository_instance
    if _repository_instance is None:
        _repository_instance = ExecutionRepository()
    return _repository_instance


def reset_execution_repository() -> None:
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
