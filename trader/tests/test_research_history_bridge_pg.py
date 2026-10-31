"""PostgreSQL integration tests for the history -> Research bridge.

Runs against an isolated database that is **never dropped automatically**: each
test uses unique identifiers so repeated runs stay independent.

Covers:
- the atomic publish (Research dataset + provenance link + history state)
- rollback: a failing publish leaves no selectable half product
- idempotency of the publish
- the metadata-only Research catalog and its filters
- the FAILED-is-terminal retry contract
- the single-owner history worker lock
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from typing import Any, Dict, List
from urllib.parse import urlparse, urlunparse

import pytest

from trader.adapters.persistence.history_dataset_store import (
    HISTORY_WORKER_LOCK_KEY,
    HistoryDatasetStore,
)
from trader.adapters.persistence.postgres import ASYNCPG_AVAILABLE, PostgreSQLStorage
from trader.adapters.persistence.research_history_bridge_store import (
    ResearchHistoryBridgeStore,
    ResearchHistoryStoreError,
)
from trader.core.domain.services.ohlcv_archive import KlineBar
from trader.trading.datasets import dataset_metadata
from trader.trading.jobs import PostgresResearchStore
from trader.trading.research_bridge import (
    CONVERSION_VERSION,
    build_research_dataset,
    build_research_provenance,
)

TEST_DB_NAME = "qts_research_bridge_test"
RUN_ID = uuid.uuid4().hex[:8]

skip_if_no_asyncpg = pytest.mark.skipif(
    not ASYNCPG_AVAILABLE, reason="asyncpg package not installed"
)


def _base_dsn() -> str:
    return os.getenv(
        "POSTGRES_CONNECTION_STRING", "postgresql://trader:trader_pwd@127.0.0.1:5432/trading"
    )


def _with_database(dsn: str, database: str) -> str:
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{database}"))


@pytest.fixture(scope="module")
def isolated_dsn():
    base = _base_dsn()
    admin_dsn = _with_database(base, "postgres")
    target_dsn = _with_database(base, TEST_DB_NAME)

    async def _ensure_database() -> None:
        import asyncpg

        conn = await asyncpg.connect(admin_dsn)
        try:
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB_NAME
            )
            if not exists:
                await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
        finally:
            await conn.close()

    import asyncio

    try:
        asyncio.run(_ensure_database())
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Cannot prepare isolated test database: {exc}")
    return target_dsn


def _bars(count: int, start_ms: int = 0) -> List[KlineBar]:
    result = []
    for index in range(count):
        ts = start_ms + index * 60_000
        result.append(
            KlineBar(
                open_time_ms=ts,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5"),
                volume=Decimal("12.5"),
            )
        )
    return result


def _link(history_dataset_id: str, document: Dict[str, Any], **overrides: Any) -> Dict[str, Any]:
    payload = dict(
        history_dataset_id=history_dataset_id,
        source_url="https://example.invalid/BTCUSDT-1m-2026-09.zip",
        checksum_url="https://example.invalid/BTCUSDT-1m-2026-09.zip.CHECKSUM",
        official_sha256="c" * 64,
        archive_sha256="b" * 64,
        archive_version=1,
        feature_version="binance_spot_1m_BTCUSDT_2026-09_bbbbbbbbbbbb",
        symbol="BTCUSDT",
        interval="1m",
        month="2026-09",
        coverage_start_ms=0,
        coverage_end_ms=4 * 60_000,
        bar_count=4,
        conversion_version=CONVERSION_VERSION,
        quality={"complete": True},
    )
    payload.update(overrides)
    return build_research_provenance(**payload)


async def _publish_fixture(bridge: ResearchHistoryBridgeStore, store: HistoryDatasetStore, symbol):
    """Create one queued job + one pending history dataset and publish it.

    The job row is inserted directly: the store's ``create_job`` reuses an
    active job for the same (source, symbol, interval, month), which would
    collide with the rows left over by earlier runs.
    """
    job_id = f"hjob_bridge_{uuid.uuid4().hex[:12]}"
    dataset_id = f"hds_bridge_{uuid.uuid4().hex[:12]}"
    # The isolated database is never dropped, so every fixture needs its own
    # archive version to satisfy the (source, market, symbol, interval, month,
    # version) uniqueness constraint.
    version = uuid.uuid4().int % 1_000_000_000
    async with store.storage.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO history_ingest_jobs
                (job_id, source, market, symbol, interval, month, status, stage)
            VALUES ($1, 'binance_vision', 'spot', $2, '1m', '2026-09', 'FAILED', 'QUEUED')
            """,
            job_id,
            symbol,
        )
    await store.insert_dataset(
        dataset_id=dataset_id,
        source="binance_vision",
        market="spot",
        symbol=symbol,
        interval="1m",
        month="2026-09",
        version=version,
        feature_version=f"binance_spot_1m_{symbol}_2026-09_bbbbbbbbbbbb",
        archive_path="/tmp/none.zip",
        archive_sha256="b" * 64,
        official_sha256="c" * 64,
        source_url="https://example.invalid/x.zip",
        checksum_url="https://example.invalid/x.zip.CHECKSUM",
        row_count=4,
        expected_points=4,
        gap_count=0,
        first_ts_ms=0,
        last_ts_ms=3 * 60_000,
        quality={"complete": True},
        status="PENDING",
        persistence_ok=False,
        backtest_eligible=False,
        job_id=job_id,
    )
    return job_id, dataset_id


@skip_if_no_asyncpg
async def test_publish_writes_dataset_link_and_history_state_in_one_transaction(
    isolated_dsn,
):
    import asyncpg

    storage = PostgreSQLStorage(connection_string=isolated_dsn)
    await storage.connect()
    pool = await asyncpg.create_pool(isolated_dsn, min_size=1, max_size=4)
    store = HistoryDatasetStore(storage=storage)
    bridge = ResearchHistoryBridgeStore(storage=storage)
    research = PostgresResearchStore(pool)
    await research.initialize()
    try:
        job_id, dataset_id = await _publish_fixture(bridge, store, "BTCUSDT")
        document = build_research_dataset(
            _bars(4), symbol="BTCUSDT", archive_sha256="b" * 64, month="2026-09"
        )
        link = _link(dataset_id, document)

        published = await bridge.publish_dataset(
            job_id=job_id,
            history_dataset_id=dataset_id,
            feature_version="binance_spot_1m_BTCUSDT_2026-09_bbbbbbbbbbbb",
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

        assert published["dataset"]["status"] == "PUBLISHED"
        assert published["dataset"]["persistence_ok"] is True
        assert published["dataset"]["backtest_eligible"] is True
        assert published["dataset"]["research_dataset_id"] == document["dataset_id"]
        assert published["job"]["status"] == "SUCCEEDED"

        stored = await research.get_dataset(document["dataset_id"])
        assert stored is not None
        assert stored["dataset_id"] == document["dataset_id"]

        record = await research.get_dataset_link(document["dataset_id"])
        assert record["provenance_hash"] == link["provenance_hash"]
        assert record["official_eligible"] is True
        assert record["history_dataset_id"] == dataset_id
    finally:
        await pool.close()
        await storage.disconnect()


@skip_if_no_asyncpg
async def test_publish_rolls_back_when_the_job_row_is_missing(isolated_dsn):
    import asyncpg

    storage = PostgreSQLStorage(connection_string=isolated_dsn)
    await storage.connect()
    pool = await asyncpg.create_pool(isolated_dsn, min_size=1, max_size=4)
    store = HistoryDatasetStore(storage=storage)
    bridge = ResearchHistoryBridgeStore(storage=storage)
    research = PostgresResearchStore(pool)
    await research.initialize()
    try:
        _, dataset_id = await _publish_fixture(bridge, store, "ETHUSDT")
        document = build_research_dataset(
            _bars(4), symbol="ETHUSDT", archive_sha256="b" * 64, month="2026-09"
        )
        link = _link(dataset_id, document, symbol="ETHUSDT")

        with pytest.raises(ResearchHistoryStoreError):
            await bridge.publish_dataset(
                job_id="hjob_does_not_exist",
                history_dataset_id=dataset_id,
                feature_version="binance_spot_1m_ETHUSDT_2026-09_bbbbbbbbbbbb",
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

        # Nothing of the failed publish may survive.
        assert await research.get_dataset(document["dataset_id"]) is None
        assert await research.get_dataset_link(document["dataset_id"]) is None
        history_dataset = await store.get_dataset(dataset_id)
        assert history_dataset["status"] == "PENDING"
        assert history_dataset["research_dataset_id"] is None
    finally:
        await pool.close()
        await storage.disconnect()


@skip_if_no_asyncpg
async def test_publish_is_idempotent(isolated_dsn):
    import asyncpg

    storage = PostgreSQLStorage(connection_string=isolated_dsn)
    await storage.connect()
    pool = await asyncpg.create_pool(isolated_dsn, min_size=1, max_size=4)
    store = HistoryDatasetStore(storage=storage)
    bridge = ResearchHistoryBridgeStore(storage=storage)
    research = PostgresResearchStore(pool)
    await research.initialize()
    try:
        job_id, dataset_id = await _publish_fixture(bridge, store, "BTCUSDT")
        document = build_research_dataset(
            _bars(4), symbol="BTCUSDT", archive_sha256="b" * 64, month="2026-09"
        )
        link = _link(dataset_id, document)
        for _ in range(2):
            await bridge.publish_dataset(
                job_id=job_id,
                history_dataset_id=dataset_id,
                feature_version="binance_spot_1m_BTCUSDT_2026-09_bbbbbbbbbbbb",
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

        catalog = await research.list_datasets(symbol="BTCUSDT")
        matching = [row for row in catalog if row["dataset_id"] == document["dataset_id"]]
        assert len(matching) == 1
        assert matching[0]["official_eligible"] is True
        assert matching[0]["provenance_hash"] == link["provenance_hash"]
    finally:
        await pool.close()
        await storage.disconnect()


@skip_if_no_asyncpg
async def test_catalog_returns_metadata_without_the_bar_payload(isolated_dsn):
    import asyncpg

    storage = PostgreSQLStorage(connection_string=isolated_dsn)
    await storage.connect()
    pool = await asyncpg.create_pool(isolated_dsn, min_size=1, max_size=4)
    store = HistoryDatasetStore(storage=storage)
    bridge = ResearchHistoryBridgeStore(storage=storage)
    research = PostgresResearchStore(pool)
    await research.initialize()
    try:
        job_id, dataset_id = await _publish_fixture(bridge, store, "BTCUSDT")
        document = build_research_dataset(
            _bars(4), symbol="BTCUSDT", archive_sha256="b" * 64, month="2026-09"
        )
        link = _link(dataset_id, document)
        await bridge.publish_dataset(
            job_id=job_id,
            history_dataset_id=dataset_id,
            feature_version="binance_spot_1m_BTCUSDT_2026-09_bbbbbbbbbbbb",
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

        catalog = await research.list_datasets(symbol="BTCUSDT")
        entry = next(row for row in catalog if row["dataset_id"] == document["dataset_id"])
        assert entry["metadata"]["symbol"] == "BTCUSDT"
        assert entry["metadata"]["bar_count"] == 4
        assert entry["month"] == "2026-09"
        assert "bars" not in entry
        assert entry["metadata"]["bar_count"] == 4

        only_official = await research.list_datasets(symbol="BTCUSDT", official_only=True)
        assert document["dataset_id"] in {row["dataset_id"] for row in only_official}

        other_month = await research.list_datasets(symbol="BTCUSDT", month="1999-01")
        assert other_month == []
    finally:
        await pool.close()
        await storage.disconnect()


@skip_if_no_asyncpg
async def test_failed_runs_are_terminal_and_retry_is_explicit(isolated_dsn):
    import asyncpg

    pool = await asyncpg.create_pool(isolated_dsn, min_size=1, max_size=4)
    research = PostgresResearchStore(pool)
    await research.initialize()
    try:
        document = build_research_dataset(
            _bars(4), symbol="BTCUSDT", archive_sha256="b" * 64, month="2026-09"
        )
        await research.register_dataset(document["identity"], document["bars"])
        from trader.trading.jobs import BacktestApplication

        record = await BacktestApplication(research).submit(
            {
                "dataset_ids": [document["dataset_id"]],
                "strategy": {"module_path": "trader.strategies.ema_cross_btc"},
            }
        )
        run_id = record["run_id"]
        # The isolated database is never dropped, so older queued runs may still
        # be around: drain them until our own run is claimed.
        claimed = None
        for _ in range(100):
            candidate = await research.claim("worker-1")
            if candidate is None:
                break
            if candidate["run_id"] == run_id:
                claimed = candidate
                break
            await research.finish(
                candidate["run_id"],
                "worker-1",
                candidate["fencing_token"],
                error="drained stale run",
            )
        assert claimed is not None
        await research.finish(run_id, "worker-1", claimed["fencing_token"], error="boom")
        assert (await research.get(run_id))["status"] == "FAILED"

        # A failed run is terminal: no worker may claim it.
        drained = await research.claim("worker-2")
        assert drained is None or drained["run_id"] != run_id

        requeued = await research.retry(run_id)
        assert requeued["status"] == "QUEUED"
        assert requeued["run_id"] == run_id
        assert requeued["manifest_hash"] == record["manifest_hash"]
        assert requeued["attempt"] == 1

        # A second retry is refused while the run is queued again.
        with pytest.raises(ValueError):
            await research.retry(run_id)
        assert await research.retry("bt_missing") is None
    finally:
        await pool.close()


@skip_if_no_asyncpg
async def test_history_worker_lock_allows_a_single_owner(isolated_dsn):
    storage = PostgreSQLStorage(connection_string=isolated_dsn)
    await storage.connect()
    first = HistoryDatasetStore(storage=storage)
    second = HistoryDatasetStore(storage=storage)
    try:
        assert await first.try_acquire_worker_lock(HISTORY_WORKER_LOCK_KEY) is True
        # A second owner (another process) must not be able to take the lock.
        assert await second.try_acquire_worker_lock(HISTORY_WORKER_LOCK_KEY) is False
        await first.release_worker_lock(HISTORY_WORKER_LOCK_KEY)
        assert await second.try_acquire_worker_lock(HISTORY_WORKER_LOCK_KEY) is True
        await second.release_worker_lock(HISTORY_WORKER_LOCK_KEY)
    finally:
        await storage.disconnect()
