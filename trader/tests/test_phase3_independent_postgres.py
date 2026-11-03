"""Phase 3 independent tester acceptance on real PostgreSQL (acceptance 3.10).

Isolation: the Research DDL is schema-qualified (``research.*``) and the history
tables live in the fixed public schema, so a temporary ``search_path`` cannot
isolate this path. Every case creates one uniquely named disposable database,
uses only that database, and drops exactly that database in ``finally``. The
developer/runtime database and any existing schema are never touched.

The DSN is read only from ``POSTGRES_CONNECTION_STRING`` and never appears in
this file. A required PG case without that variable fails; it never skips.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlparse, urlunparse

import pytest

from trader.adapters.persistence.history_dataset_store import HistoryDatasetStore
from trader.adapters.persistence.postgres import PostgreSQLStorage
from trader.adapters.persistence.research_history_bridge_store import ResearchHistoryBridgeStore
from trader.core.domain.services.ohlcv_archive import KlineBar
from trader.trading.datasets import dataset_metadata, digest
from trader.trading.eligibility import (
    DATASET_PROVENANCE_UNTRUSTED,
    HISTORY_STORE_UNAVAILABLE,
    require_published_dataset,
)
from trader.trading.jobs import BacktestApplication, PostgresResearchStore
from trader.trading.research_bridge import (
    CONVERSION_VERSION,
    build_research_dataset,
    build_research_provenance,
)

MINUTE = 60_000
SHA = "e" * 64


def _base_dsn():
    value = os.getenv("POSTGRES_CONNECTION_STRING", "")
    if not value:
        pytest.fail("POSTGRES_CONNECTION_STRING is required for phase-3 PostgreSQL tests")
    return value


def _with_database(dsn, database):
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{database}"))


def _identity(*, symbol="BTCUSDT"):
    base = symbol[:-4]
    return {
        "venue": "BINANCE",
        "market_type": "SPOT",
        "symbol": symbol,
        "base_asset": base,
        "quote_asset": "USDT",
        "interval": "1m",
        # The source string is deliberately the official one: without a trusted
        # published link it must still be refused.
        "source": "binance_vision",
        "version": "p3i-pg-1",
    }


def _bars(*, count=4):
    bars = []
    for index in range(count):
        opened = index * MINUTE
        bars.append(
            {
                "open_time_ms": opened,
                "close_time_ms": opened + MINUTE,
                "available_at_ms": opened + MINUTE,
                "open": "100",
                "high": "110",
                "low": "90",
                "close": "100",
                "volume": "3",
            }
        )
    return bars


def _kline_bars(*, count=4):
    return [
        KlineBar(
            open_time_ms=index * MINUTE,
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("100"),
            volume=Decimal("3"),
        )
        for index in range(count)
    ]


def _manifest(dataset_id):
    return {
        "dataset_ids": [dataset_id],
        "strategy": {"module_path": "trader.strategies.ema_cross_btc", "params": {}},
        "initial_cash": "1000",
        "fee_bps": "10",
        "slippage_bps": "5",
        "engine": "event_runtime",
        "purpose": "research_screening",
    }


async def _eligibility_of(dataset_id, bridge, history):
    return await require_published_dataset(dataset_id, bridge=bridge, history_store=history)


@asynccontextmanager
async def research_pg():
    import asyncpg

    base = _base_dsn()
    name = "qts_phase3_indp_" + uuid.uuid4().hex[:12]
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

        async def eligible(dataset_id):
            return await _eligibility_of(dataset_id, bridge, history)

        application = BacktestApplication(research, eligibility=eligible)
        yield SimpleNamespace(
            research=research,
            history=history,
            bridge=bridge,
            storage=storage,
            application=application,
            pool=pool,
        )
    finally:
        if pool is not None:
            await pool.close()
        if storage is not None and storage.is_connected:
            await storage.disconnect()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def _job_count(pool):
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT count(*) FROM research.backtest_jobs")


async def _pending_history(ctx, *, symbol, month):
    """Register a PENDING history dataset plus its ingest job."""
    job = await ctx.history.create_job(
        job_id="hjob_p3i_" + uuid.uuid4().hex[:10],
        source="binance_vision",
        market="spot",
        symbol=symbol,
        interval="1m",
        month=month,
    )
    history_dataset_id = "hds_p3i_" + uuid.uuid4().hex[:10]
    feature_version = f"binance_spot_1m_{symbol}_{month}_eeeeeeeeeeee"
    await ctx.history.insert_dataset(
        dataset_id=history_dataset_id,
        source="binance_vision",
        market="spot",
        symbol=symbol,
        interval="1m",
        month=month,
        version=1,
        feature_version=feature_version,
        archive_path="/tmp/p3i-nonexistent.zip",
        archive_sha256=SHA,
        official_sha256=SHA,
        source_url="https://example.invalid/p3i.zip",
        checksum_url="https://example.invalid/p3i.zip.CHECKSUM",
        row_count=4,
        expected_points=4,
        gap_count=0,
        first_ts_ms=0,
        last_ts_ms=3 * MINUTE,
        quality={"complete": True},
        status="PENDING",
        persistence_ok=False,
        backtest_eligible=False,
        job_id=job["job_id"],
    )
    return job, history_dataset_id, feature_version


async def _insert_link_row(ctx, dataset_id, history_dataset_id, *, symbol, month, link_hash):
    """Insert a trusted-looking link directly (independently of publish_dataset)."""
    async with ctx.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO research.dataset_links (
                research_dataset_id, history_dataset_id, source_url, checksum_url,
                official_sha256, archive_sha256, archive_version, feature_version,
                symbol, interval, month, coverage_start_ms, coverage_end_ms,
                bar_count, conversion_version, quality, provenance_hash, official_eligible
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb,$17,TRUE)
            """,
            dataset_id,
            history_dataset_id,
            "https://example.invalid/p3i.zip",
            "https://example.invalid/p3i.zip.CHECKSUM",
            SHA,
            SHA,
            1,
            f"binance_spot_1m_{symbol}_{month}_eeeeeeeeeeee",
            symbol,
            "1m",
            month,
            0,
            4 * MINUTE,
            4,
            "p3i_v1",
            '{"complete": true}',
            link_hash,
        )


async def _publish(ctx, *, symbol="BTCUSDT", month="2026-11"):
    """Publish an independently built archive projection through the real bridge."""
    job, history_dataset_id, feature_version = await _pending_history(
        ctx, symbol=symbol, month=month
    )
    document = build_research_dataset(
        _kline_bars(),
        symbol=symbol,
        archive_sha256=SHA,
        month=month,
    )
    link = build_research_provenance(
        history_dataset_id=history_dataset_id,
        source_url="https://example.invalid/p3i.zip",
        checksum_url="https://example.invalid/p3i.zip.CHECKSUM",
        official_sha256=SHA,
        archive_sha256=SHA,
        archive_version=1,
        feature_version=feature_version,
        symbol=symbol,
        interval="1m",
        month=month,
        coverage_start_ms=0,
        coverage_end_ms=4 * MINUTE,
        bar_count=4,
        conversion_version=CONVERSION_VERSION,
        quality={"complete": True},
    )
    await ctx.bridge.publish_dataset(
        job_id=job["job_id"],
        history_dataset_id=history_dataset_id,
        feature_version=feature_version,
        row_count=4,
        expected_points=4,
        gap_count=0,
        first_ts_ms=0,
        last_ts_ms=3 * MINUTE,
        quality={"complete": True},
        link=link,
        research_document=document,
        research_metadata=dataset_metadata(document),
    )
    return document, link, history_dataset_id


# ---------------------------------------------------------------- 3.10 ------


@pytest.mark.asyncio
async def test_p3i_3_10_real_postgres_refuses_submission_without_a_trusted_link():
    async with research_pg() as ctx:
        data = await ctx.research.register_dataset(_identity(), _bars())
        with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
            await ctx.application.submit(_manifest(data["dataset_id"]))
        # No durable work is queued for an untrusted dataset.
        assert await _job_count(ctx.pool) == 0


@pytest.mark.asyncio
async def test_p3i_3_10_real_postgres_refuses_a_linked_but_unpublished_history_dataset():
    async with research_pg() as ctx:
        data = await ctx.research.register_dataset(_identity(), _bars())
        _job, history_dataset_id, _feature_version = await _pending_history(
            ctx, symbol="BTCUSDT", month="2026-10"
        )
        await _insert_link_row(
            ctx,
            data["dataset_id"],
            history_dataset_id,
            symbol="BTCUSDT",
            month="2026-10",
            link_hash="a" * 64,
        )
        # The link is official_eligible with a provenance hash, but the history
        # dataset is still PENDING: publication state is the deciding fact.
        with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
            await ctx.application.submit(_manifest(data["dataset_id"]))
        assert await _job_count(ctx.pool) == 0

        # Positive control: publishing all three flags makes the same link usable.
        async with ctx.pool.acquire() as conn:
            await conn.execute(
                "UPDATE history_datasets SET status='PUBLISHED', persistence_ok=TRUE, "
                "backtest_eligible=TRUE WHERE dataset_id=$1",
                history_dataset_id,
            )
        record = await ctx.application.submit(_manifest(data["dataset_id"]))
        assert record["manifest"]["provenance"] == {
            data["dataset_id"]: "a" * 64,
        }
        assert await _job_count(ctx.pool) == 1


@pytest.mark.asyncio
async def test_p3i_3_10_real_postgres_publish_submit_retry_fail_closed_lifecycle():
    async with research_pg() as ctx:
        document, link, history_dataset_id = await _publish(ctx)
        dataset_id = document["dataset_id"]
        record = await ctx.application.submit(_manifest(dataset_id))
        assert record["manifest"]["provenance"] == {
            dataset_id: link["provenance_hash"],
        }
        assert await _job_count(ctx.pool) == 1

        claimed = await ctx.research.claim("p3i-worker")
        assert claimed is not None
        await ctx.research.finish(
            record["run_id"], "p3i-worker", claimed["fencing_token"], error="boom"
        )
        frozen_manifest = (await ctx.research.get(record["run_id"]))["manifest"]

        # Revoking publication must refuse both a new submission and a retry.
        async with ctx.pool.acquire() as conn:
            await conn.execute(
                "UPDATE history_datasets SET status='PENDING' WHERE dataset_id=$1",
                history_dataset_id,
            )
        with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
            await ctx.application.submit(_manifest(dataset_id))
        with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
            await ctx.application.retry(record["run_id"])
        failed = await ctx.research.get(record["run_id"])
        assert failed["status"] == "FAILED"
        assert failed["attempt"] == 1
        assert failed["manifest"] == frozen_manifest
        assert failed["manifest_hash"] == digest(frozen_manifest)
        assert failed["error"] == "boom"
        assert await _job_count(ctx.pool) == 1

        # Restoring publication re-queues the same run with frozen inputs.
        async with ctx.pool.acquire() as conn:
            await conn.execute(
                "UPDATE history_datasets SET status='PUBLISHED' WHERE dataset_id=$1",
                history_dataset_id,
            )
        requeued = await ctx.application.retry(record["run_id"])
        assert requeued["status"] == "QUEUED"
        assert requeued["run_id"] == record["run_id"]
        assert requeued["manifest"] == frozen_manifest
        assert requeued["manifest_hash"] == digest(frozen_manifest)


@pytest.mark.asyncio
async def test_p3i_3_10_real_postgres_published_flags_are_each_required():
    async with research_pg() as ctx:
        document, _link, history_dataset_id = await _publish(ctx, symbol="ETHUSDT", month="2026-09")
        for column in ("persistence_ok", "backtest_eligible"):
            async with ctx.pool.acquire() as conn:
                await conn.execute(
                    f"UPDATE history_datasets SET {column}=FALSE WHERE dataset_id=$1",
                    history_dataset_id,
                )
            with pytest.raises(ValueError, match=DATASET_PROVENANCE_UNTRUSTED):
                await ctx.application.submit(_manifest(document["dataset_id"]))
            assert await _job_count(ctx.pool) == 0
            async with ctx.pool.acquire() as conn:
                await conn.execute(
                    f"UPDATE history_datasets SET {column}=TRUE WHERE dataset_id=$1",
                    history_dataset_id,
                )


@pytest.mark.asyncio
async def test_p3i_3_10_real_postgres_history_outage_returns_the_stable_rejection_code():
    async with research_pg() as ctx:
        document, _link, _history_dataset_id = await _publish(ctx, month="2026-12")
        record = await ctx.application.submit(_manifest(document["dataset_id"]))
        claimed = await ctx.research.claim("p3i-worker")
        owner, token = "p3i-worker", claimed["fencing_token"]
        await ctx.research.finish(record["run_id"], owner, token, error="boom")
        frozen_manifest = (await ctx.research.get(record["run_id"]))["manifest"]

        # Close the history plane connection; the research queue stays readable.
        await ctx.storage.disconnect()
        with pytest.raises(ValueError) as submitted:
            await ctx.application.submit(_manifest(document["dataset_id"]))
        assert str(submitted.value) == HISTORY_STORE_UNAVAILABLE
        with pytest.raises(ValueError) as retried:
            await ctx.application.retry(record["run_id"])
        assert str(retried.value) == HISTORY_STORE_UNAVAILABLE

        failed = await ctx.research.get(record["run_id"])
        assert failed["status"] == "FAILED"
        assert failed["attempt"] == 1
        assert failed["manifest"] == frozen_manifest
        assert failed["manifest_hash"] == digest(frozen_manifest)
        assert await _job_count(ctx.pool) == 1
