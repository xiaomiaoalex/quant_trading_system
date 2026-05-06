from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from trader.core.domain.models.market_risk import MarketRiskAuditEvent

MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS risk_audit_events (
    id BIGSERIAL PRIMARY KEY,
    stream_key VARCHAR(255) NOT NULL,
    event_type VARCHAR(255) NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    trace_id VARCHAR(255) NOT NULL,
    ts_ms BIGINT NOT NULL,
    asset_class VARCHAR(64) NOT NULL,
    venue VARCHAR(255) NOT NULL,
    account_id VARCHAR(255) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_risk_audit_events_stream_key_ts
    ON risk_audit_events(stream_key, ts_ms DESC);
CREATE INDEX IF NOT EXISTS idx_risk_audit_events_event_type_ts
    ON risk_audit_events(event_type, ts_ms DESC);
CREATE INDEX IF NOT EXISTS idx_risk_audit_events_trace_id
    ON risk_audit_events(trace_id);
CREATE INDEX IF NOT EXISTS idx_risk_audit_events_asset_venue_ts
    ON risk_audit_events(asset_class, venue, ts_ms DESC);
"""


class PostgresMarketRiskAuditStorage:
    def __init__(self, pool_or_connection: Any) -> None:
        self._pool = pool_or_connection

    async def initialize(self) -> None:
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        async with self._pool.acquire() as conn:
            await conn.execute(MIGRATION_SQL)

    async def append(self, event: MarketRiskAuditEvent) -> dict[str, Any]:
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")

        record = event.to_record()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO risk_audit_events (
                    stream_key, event_type, schema_version, trace_id, ts_ms,
                    asset_class, venue, account_id, payload
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id, stream_key, event_type, schema_version, trace_id, ts_ms,
                          asset_class, venue, account_id, payload, created_at
                """,
                record["stream_key"],
                record["event_type"],
                record["schema_version"],
                record["trace_id"],
                record["ts_ms"],
                record["asset_class"],
                record["venue"],
                record["account_id"],
                json.dumps(record["payload"]),
            )

        return self._row_to_record(row)

    async def list_events(
        self,
        *,
        stream_key: str | None = None,
        event_type: str | None = None,
        trace_id: str | None = None,
        since_ts_ms: int | None = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, stream_key, event_type, schema_version, trace_id, ts_ms,
                       asset_class, venue, account_id, payload, created_at
                FROM risk_audit_events
                WHERE ($1::text IS NULL OR stream_key = $1)
                  AND ($2::text IS NULL OR event_type = $2)
                  AND ($3::text IS NULL OR trace_id = $3)
                  AND ($4::bigint IS NULL OR ts_ms >= $4)
                ORDER BY ts_ms DESC, id DESC
                LIMIT $5
                """,
                stream_key,
                event_type,
                trace_id,
                since_ts_ms,
                limit,
            )

        return [self._row_to_record(row) for row in rows]

    def _row_to_record(self, row: Any) -> dict[str, Any]:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        elif not isinstance(payload, dict):
            payload = {}

        created_at = row.get("created_at") if isinstance(row, dict) else row["created_at"]
        if isinstance(created_at, datetime):
            created_at_value = created_at.isoformat()
        else:
            created_at_value = created_at

        return {
            "event_id": row["id"],
            "stream_key": row["stream_key"],
            "event_type": row["event_type"],
            "schema_version": row["schema_version"],
            "trace_id": row["trace_id"],
            "ts_ms": row["ts_ms"],
            "asset_class": row["asset_class"],
            "venue": row["venue"],
            "account_id": row["account_id"],
            "payload": payload,
            "created_at": created_at_value,
        }


async def create_postgres_market_risk_audit_storage(
    connection_string: str | None = None,
) -> PostgresMarketRiskAuditStorage:
    import os

    import asyncpg

    dsn = connection_string or os.environ.get("POSTGRES_CONNECTION_STRING")
    if dsn is None:
        raise ValueError("PostgreSQL connection string not provided")

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    storage = PostgresMarketRiskAuditStorage(pool)
    await storage.initialize()
    return storage
