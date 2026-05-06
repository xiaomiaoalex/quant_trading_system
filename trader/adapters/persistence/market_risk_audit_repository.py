from __future__ import annotations

import asyncio
import logging
from typing import Any

from trader.adapters.persistence.postgres import check_postgres_connection
from trader.adapters.persistence.postgres.risk_audit_storage import PostgresMarketRiskAuditStorage
from trader.core.domain.models.market_risk import MarketRiskAuditEvent
from trader.storage.in_memory import InMemoryStorage, get_storage

logger = logging.getLogger(__name__)


class MarketRiskAuditRepository:
    def __init__(
        self,
        *,
        storage: InMemoryStorage | None = None,
        postgres_storage: Any | None = None,
    ) -> None:
        self._memory_storage = storage or get_storage()
        self._postgres_storage = postgres_storage
        self._postgres_storage_injected = postgres_storage is not None
        self._init_lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def append(self, event: MarketRiskAuditEvent) -> dict[str, Any]:
        pg_storage = await self._ensure_postgres()
        if pg_storage is not None:
            try:
                record = await pg_storage.append(event)
                self._append_memory_projection(event)
                return record
            except Exception as exc:
                logger.warning(
                    "PostgreSQL risk audit append failed, falling back to memory: %s",
                    exc,
                )

        return self._append_memory_projection(event)

    async def list_events(
        self,
        *,
        stream_key: str | None = None,
        event_type: str | None = None,
        trace_id: str | None = None,
        since_ts_ms: int | None = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        pg_storage = await self._ensure_postgres()
        if pg_storage is not None:
            try:
                return await pg_storage.list_events(
                    stream_key=stream_key,
                    event_type=event_type,
                    trace_id=trace_id,
                    since_ts_ms=since_ts_ms,
                    limit=limit,
                )
            except Exception as exc:
                logger.warning(
                    "PostgreSQL risk audit query failed, falling back to memory: %s",
                    exc,
                )

        return self._memory_storage.list_events(
            stream_key=stream_key,
            event_type=event_type,
            trace_id=trace_id,
            since_ts_ms=since_ts_ms,
            limit=limit,
        )

    async def _ensure_postgres(self) -> Any | None:
        if self._postgres_storage_injected and self._postgres_storage is not None:
            return self._postgres_storage

        current_loop = asyncio.get_running_loop()
        if self._loop is not current_loop:
            self._postgres_storage = None
            self._loop = current_loop
            self._init_lock = asyncio.Lock()

        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

        async with self._init_lock:
            if self._postgres_storage is not None:
                return self._postgres_storage

            ok, message = await check_postgres_connection(timeout=2.0)
            if not ok:
                logger.debug("PostgreSQL unavailable for risk audit: %s", message)
                return None

            try:
                from trader.adapters.persistence.postgres import _get_pool

                pool = await _get_pool(timeout=2.0)
                if pool is None:
                    return None
                storage = PostgresMarketRiskAuditStorage(pool)
                await storage.initialize()
                self._postgres_storage = storage
                return storage
            except Exception as exc:
                logger.warning("Failed to initialize PostgreSQL risk audit storage: %s", exc)
                self._postgres_storage = None
                return None

    def _append_memory_projection(self, event: MarketRiskAuditEvent) -> dict[str, Any]:
        record = event.to_record()
        return self._memory_storage.append_event(
            {
                "stream_key": record["stream_key"],
                "event_type": record["event_type"],
                "schema_version": record["schema_version"],
                "trace_id": record["trace_id"],
                "ts_ms": record["ts_ms"],
                "payload": record["payload"],
            }
        )


_market_risk_audit_repository: MarketRiskAuditRepository | None = None


def get_market_risk_audit_repository() -> MarketRiskAuditRepository:
    global _market_risk_audit_repository
    if _market_risk_audit_repository is None:
        _market_risk_audit_repository = MarketRiskAuditRepository()
    return _market_risk_audit_repository


def reset_market_risk_audit_repository() -> None:
    global _market_risk_audit_repository
    _market_risk_audit_repository = None
