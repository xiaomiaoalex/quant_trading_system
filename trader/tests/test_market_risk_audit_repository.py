from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import pytest

from trader.adapters.persistence.postgres import ASYNCPG_AVAILABLE, is_postgres_available
from trader.core.domain.models.market_risk import AssetClass, MarketRiskAuditEvent
from trader.storage.in_memory import InMemoryStorage


class FakeAuditPool:
    def __init__(self) -> None:
        self.conn = FakeAuditConnection()

    def acquire(self):
        return FakeAuditAcquire(self.conn)


class FakeAuditAcquire:
    def __init__(self, conn: "FakeAuditConnection") -> None:
        self._conn = conn

    async def __aenter__(self) -> "FakeAuditConnection":
        return self._conn

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


class FakeAuditConnection:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.executed: list[str] = []

    async def execute(self, query: str, *args):
        self.executed.append(query)
        return "OK"

    async def fetchrow(self, query: str, *args):
        if "INSERT INTO risk_audit_events" not in query:
            return None
        row = {
            "id": len(self.rows) + 1,
            "stream_key": args[0],
            "event_type": args[1],
            "schema_version": args[2],
            "trace_id": args[3],
            "ts_ms": args[4],
            "asset_class": args[5],
            "venue": args[6],
            "account_id": args[7],
            "payload": args[8],
            "created_at": datetime.now(timezone.utc),
        }
        self.rows.append(row)
        return row

    async def fetch(self, query: str, *args):
        stream_key, event_type, trace_id, since_ts_ms, limit = args
        rows = list(self.rows)
        if stream_key is not None:
            rows = [row for row in rows if row["stream_key"] == stream_key]
        if event_type is not None:
            rows = [row for row in rows if row["event_type"] == event_type]
        if trace_id is not None:
            rows = [row for row in rows if row["trace_id"] == trace_id]
        if since_ts_ms is not None:
            rows = [row for row in rows if row["ts_ms"] >= since_ts_ms]
        rows.sort(key=lambda row: (row["ts_ms"], row["id"]), reverse=True)
        return rows[:limit]


class FailingAuditStorage:
    async def append(self, event: MarketRiskAuditEvent):
        raise RuntimeError("pg down")

    async def list_events(self, *args, **kwargs):
        raise RuntimeError("pg down")


def _event(trace_id: str = "trace-1", ts_ms: int = 1710000000000) -> MarketRiskAuditEvent:
    return MarketRiskAuditEvent(
        stream_key="risk:crypto",
        event_type="crypto_risk.budget_updated",
        trace_id=trace_id,
        ts_ms=ts_ms,
        asset_class=AssetClass.CRYPTO,
        venue="binance",
        account_id="demo",
        payload={"updated_by": "operator"},
    )


skip_if_no_asyncpg = pytest.mark.skipif(
    not ASYNCPG_AVAILABLE,
    reason="asyncpg package not installed",
)

skip_if_no_postgres = pytest.mark.skipif(
    not is_postgres_available(),
    reason="PostgreSQL not available. Set POSTGRES_CONNECTION_STRING or POSTGRES_HOST/POSTGRES_DB/POSTGRES_USER",
)


@pytest.mark.asyncio
async def test_postgres_market_risk_audit_storage_appends_and_filters() -> None:
    from trader.adapters.persistence.postgres.risk_audit_storage import (
        PostgresMarketRiskAuditStorage,
    )

    pool = FakeAuditPool()
    storage = PostgresMarketRiskAuditStorage(pool)
    await storage.initialize()

    saved = await storage.append(_event())
    await storage.append(_event(trace_id="trace-2", ts_ms=1710000000500))

    assert saved["event_id"] == 1
    assert saved["stream_key"] == "risk:crypto"
    assert saved["asset_class"] == "crypto"
    assert saved["payload"] == {"updated_by": "operator"}

    events = await storage.list_events(
        stream_key="risk:crypto",
        event_type="crypto_risk.budget_updated",
        trace_id=None,
        since_ts_ms=1710000000400,
        limit=10,
    )

    assert [event["trace_id"] for event in events] == ["trace-2"]
    assert "risk_audit_events" in " ".join(pool.conn.executed)


@pytest.mark.asyncio
@skip_if_no_asyncpg
@skip_if_no_postgres
async def test_postgres_market_risk_audit_storage_writes_real_database() -> None:
    import asyncpg

    from trader.adapters.persistence.postgres.risk_audit_storage import (
        PostgresMarketRiskAuditStorage,
    )

    connection_string = os.environ.get("POSTGRES_CONNECTION_STRING")
    if connection_string:
        pool = await asyncpg.create_pool(connection_string, min_size=1, max_size=2)
    else:
        pool = await asyncpg.create_pool(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            database=os.environ.get("POSTGRES_DB", "trading"),
            user=os.environ.get("POSTGRES_USER", "trader"),
            password=os.environ.get("POSTGRES_PASSWORD", ""),
            min_size=1,
            max_size=2,
        )
    storage = PostgresMarketRiskAuditStorage(pool)
    trace_id = f"trace-real-pg-{uuid.uuid4()}"
    older_trace_id = f"{trace_id}-older"

    try:
        await storage.initialize()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM risk_audit_events WHERE trace_id = ANY($1::text[])",
                [trace_id, older_trace_id],
            )

        saved = await storage.append(_event(trace_id=trace_id, ts_ms=1710000001000))
        await storage.append(_event(trace_id=older_trace_id, ts_ms=1710000000000))

        assert saved["trace_id"] == trace_id
        assert saved["payload"] == {"updated_by": "operator"}
        assert saved["asset_class"] == "crypto"
        assert saved["venue"] == "binance"

        events = await storage.list_events(
            stream_key="risk:crypto",
            event_type="crypto_risk.budget_updated",
            trace_id=None,
            since_ts_ms=1710000000500,
            limit=10,
        )

        assert trace_id in [event["trace_id"] for event in events]
        assert older_trace_id not in [event["trace_id"] for event in events]
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM risk_audit_events WHERE trace_id = ANY($1::text[])",
                [trace_id, older_trace_id],
            )
        await pool.close()


@pytest.mark.asyncio
async def test_market_risk_audit_repository_falls_back_to_memory() -> None:
    from trader.adapters.persistence.market_risk_audit_repository import MarketRiskAuditRepository

    memory = InMemoryStorage()
    repo = MarketRiskAuditRepository(
        storage=memory,
        postgres_storage=FailingAuditStorage(),
    )

    saved = await repo.append(_event())
    events = await repo.list_events(
        stream_key="risk:crypto",
        event_type="crypto_risk.budget_updated",
        trace_id=None,
        since_ts_ms=None,
        limit=10,
    )

    assert saved["event_id"] == 1
    assert events == [
        {
            "event_id": 1,
            "stream_key": "risk:crypto",
            "event_type": "crypto_risk.budget_updated",
            "schema_version": 1,
            "trace_id": "trace-1",
            "ts_ms": 1710000000000,
            "payload": {"updated_by": "operator"},
        }
    ]
    assert json.dumps(memory.events[0]["payload"]) == '{"updated_by": "operator"}'
