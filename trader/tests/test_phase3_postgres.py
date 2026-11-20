"""Phase 3 engineer real-PostgreSQL acceptance: the eligibility gate fails closed.

Isolation: each case creates a unique throwaway database and drops it. A
schema-only ``search_path`` cannot isolate this path because the frozen Research
schema is referenced by its qualified name ``research.*``; a disposable database
isolates every schema and never touches the developer or runtime database.

The DSN comes only from ``POSTGRES_CONNECTION_STRING``. A required case without
a reachable PostgreSQL instance fails; it never skips.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlparse, urlunparse

import pytest

from trader.adapters.persistence.history_dataset_store import HistoryDatasetStore
from trader.adapters.persistence.postgres import PostgreSQLStorage
from trader.adapters.persistence.research_history_bridge_store import ResearchHistoryBridgeStore
from trader.tests.test_research_history_bridge_pg import _bars, _link, _publish_fixture
from trader.trading.datasets import dataset_metadata
from trader.trading.eligibility import (
    DATASET_PROVENANCE_UNTRUSTED,
    HISTORY_STORE_UNAVAILABLE,
    require_published_dataset,
)
from trader.trading.jobs import BacktestApplication, PostgresResearchStore
from trader.trading.research_bridge import build_research_dataset


def _base_dsn() -> str:
    value = os.getenv("POSTGRES_CONNECTION_STRING", "")
    if not value:
        pytest.fail("POSTGRES_CONNECTION_STRING is required for phase-3 PostgreSQL tests")
    return value


def _with_database(dsn: str, database: str) -> str:
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{database}"))


def _manifest(dataset_id: str) -> dict:
    return {
        "dataset_ids": [dataset_id],
        "strategy": {"module_path": "trader.strategies.ema_cross_btc", "params": {}},
        "initial_cash": "1000",
        "fee_bps": "10",
        "slippage_bps": "5",
        "engine": "event_runtime",
        "purpose": "research_screening",
    }


@asynccontextmanager
async def research_pg():
    import asyncpg

    base = _base_dsn()
    name = "qts_phase3_" + uuid.uuid4().hex[:12]
    admin = await asyncpg.connect(_with_database(base, "postgres"), timeout=5)
    dsn = _with_database(base, name)
    pool = None
    storage = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        research = PostgresResearchStore(pool)
        await research.initialize()
        storage = PostgreSQLStorage(connection_string=dsn)
        history = HistoryDatasetStore(storage=storage)
        await history.connect()
        bridge = ResearchHistoryBridgeStore(storage=history.storage)
        yield research, history, bridge, pool, storage
    finally:
        if pool is not None:
            await pool.close()
        if storage is not None and storage.is_connected:
            await storage.disconnect()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def _publish(bridge, history, symbol="BTCUSDT"):
    job_id, dataset_id = await _publish_fixture(bridge, history, symbol)
    document = build_research_dataset(
        _bars(4), symbol=symbol, archive_sha256="b" * 64, month="2026-09"
    )
    link = _link(dataset_id, document, symbol=symbol)
    await bridge.publish_dataset(
        job_id=job_id,
        history_dataset_id=dataset_id,
        feature_version=f"binance_spot_1m_{symbol}_2026-09_bbbbbbbbbbbb",
        row_count=4,
        expected_points=4,
        gap_count=0,
        first_ts_ms=0,
        last_ts_ms=3 * 60_000,
        quality={"complete": True},
        link=link,
        research_document=document,
        research_metadata=dataset_metadata(document),
    )
    return document, link, dataset_id


@pytest.mark.asyncio
async def test_eligibility_gate_fails_closed_on_real_postgres():
    async with research_pg() as (research, history, bridge, pool, storage):
        document, link, history_dataset_id = await _publish(bridge, history)
        dataset_id = document["dataset_id"]

        async def eligibility(candidate: str) -> bool:
            return await require_published_dataset(candidate, bridge=bridge, history_store=history)

        application = BacktestApplication(research, eligibility=eligibility)
        record = await application.submit(_manifest(dataset_id))
        assert record["manifest"]["provenance"] == {dataset_id: link["provenance_hash"]}

        claimed = await research.claim("worker-1")
        assert claimed is not None
        await research.finish(claimed["run_id"], "worker-1", claimed["fencing_token"], error="boom")

        # The link remains, but the history dataset is no longer published: the
        # gate must refuse on PostgreSQL itself, not only in a mock.
        await pool.execute(
            "UPDATE history_datasets SET status='PENDING' WHERE dataset_id=$1",
            history_dataset_id,
        )
        before = len(await research.list(limit=500))
        with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
            await application.submit(_manifest(dataset_id))
        assert len(await research.list(limit=500)) == before, "refused work never queues"

        with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
            await application.retry(record["run_id"])
        failed = await research.get(record["run_id"])
        assert failed["status"] == "FAILED"
        assert failed["attempt"] == 1

        await pool.execute(
            "UPDATE history_datasets SET status='PUBLISHED' WHERE dataset_id=$1",
            history_dataset_id,
        )
        requeued = await application.retry(record["run_id"])
        assert requeued["status"] == "QUEUED"

        claimed = await research.claim("worker-2")
        assert claimed is not None and claimed["run_id"] == record["run_id"]
        await research.finish(
            claimed["run_id"], "worker-2", claimed["fencing_token"], error="boom again"
        )

        # Historical storage becomes unavailable (connection closed). The
        # phase-1 stable rejection code must survive the real PostgreSQL path.
        await storage.disconnect()
        with pytest.raises(ValueError) as refused:
            await application.submit(_manifest(dataset_id))
        assert str(refused.value) == HISTORY_STORE_UNAVAILABLE
        with pytest.raises(ValueError) as retried:
            await application.retry(record["run_id"])
        assert str(retried.value) == HISTORY_STORE_UNAVAILABLE
        assert (await research.get(record["run_id"]))["status"] == "FAILED"
