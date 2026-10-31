"""Real PostgreSQL integration tests for durable research jobs and fencing."""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest

from trader.tests.test_research_runtime import bars, identity
from trader.trading.jobs import BacktestApplication, PostgresResearchStore, ResearchWorker


def _asyncpg():
    # Import lazily: other test modules replace sys.modules["asyncpg"] with a mock.
    import importlib

    return importlib.import_module("asyncpg")


ADMIN_DSN = os.getenv(
    "POSTGRES_CONNECTION_STRING", "postgresql://trader:trader_pwd@127.0.0.1:5432/trading"
)
BASE_DSN = ADMIN_DSN.rsplit("/", 1)[0]


@asynccontextmanager
async def research_store():
    try:
        asyncpg = _asyncpg()
    except ImportError:
        pytest.skip("asyncpg not installed")
    name = "qts_research_test_" + uuid.uuid4().hex[:12]
    admin = await asyncpg.connect(ADMIN_DSN)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    except Exception as exc:  # insufficient privilege
        await admin.close()
        pytest.skip(f"cannot create test database: {exc}")
    pool = None
    try:
        pool = await asyncpg.create_pool(f"{BASE_DSN}/{name}", min_size=1, max_size=4)
        store = PostgresResearchStore(pool)
        await store.initialize()
        yield store, pool
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


def manifest(dataset_id):
    return {
        "dataset_ids": [dataset_id],
        "strategy": {"module_path": "trader.strategies.ema_cross_btc", "params": {}},
        "initial_cash": "1000",
        "fee_bps": "10",
        "slippage_bps": "5",
        "engine": "event_runtime",
        "purpose": "research_screening",
    }


@pytest.mark.asyncio
async def test_postgres_job_lifecycle_and_fencing_prevents_double_publish():
    async with research_store() as (store, pool):
        data = await store.register_dataset(identity(), bars())
        application = BacktestApplication(store)
        job = await application.submit(manifest(data["dataset_id"]))
        assert job["status"] == "QUEUED"

        first = await store.claim("worker-1", lease_seconds=1)
        assert first["run_id"] == job["run_id"]
        token = first["fencing_token"]
        # An unexpired lease cannot be stolen.
        assert await store.claim("worker-2", lease_seconds=30) is None

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE research.backtest_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE run_id=$1",
                job["run_id"],
            )
        second = await store.claim("worker-2", lease_seconds=30)
        assert second["fencing_token"] > token

        with pytest.raises(RuntimeError):
            await store.finish(job["run_id"], "worker-1", token, result={"stale": True})
        published = await store.finish(
            job["run_id"], "worker-2", second["fencing_token"], result={"ok": True}
        )
        assert published["status"] == "SUCCEEDED"
        assert published["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_postgres_worker_failure_retry_and_cancel():
    async with research_store() as (store, _pool):
        data = await store.register_dataset(identity(), bars())
        application = BacktestApplication(store)
        job = await application.submit(manifest(data["dataset_id"]))

        worker = ResearchWorker(store, owner="worker-1")

        async def boom(record):
            raise RuntimeError("engine exploded")

        failed = await worker.run_once(boom)
        assert failed["status"] == "FAILED"
        assert "engine exploded" in failed["error"]

        retry = ResearchWorker(store, owner="worker-2")

        async def ok(record):
            return {"net_value": "1000", "dataset_ids": record["manifest"]["dataset_ids"]}

        succeeded = await retry.run_once(ok)
        assert succeeded["status"] == "SUCCEEDED"
        assert succeeded["result"]["net_value"] == "1000"

        second_job = await application.submit(manifest(data["dataset_id"]))
        await store.cancel(second_job["run_id"])
        cancelled = await ResearchWorker(store, owner="worker-3").run_once(ok)
        assert cancelled is None
        assert (await store.get(second_job["run_id"]))["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_postgres_dataset_is_content_addressed_and_immutable():
    async with research_store() as (store, _pool):
        first = await store.register_dataset(identity(), bars())
        second = await store.register_dataset(identity(), bars())
        changed = await store.register_dataset(identity(), bars(close_price="101"))
        assert first["dataset_id"] == second["dataset_id"]
        assert first["dataset_id"] != changed["dataset_id"]
        assert (await store.get_dataset(first["dataset_id"]))["dataset_id"] == first["dataset_id"]


@pytest.mark.asyncio
async def test_postgres_dataset_tampering_is_rejected_at_execution():
    from trader.trading.research import ResearchExecutor

    async with research_store() as (store, pool):
        data = await store.register_dataset(identity(), bars())
        job = await BacktestApplication(store).submit(manifest(data["dataset_id"]))
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE research.datasets SET document=
                   jsonb_set(document, '{bars,0,volume}', '"999"'::jsonb)
                   WHERE dataset_id=$1""",
                data["dataset_id"],
            )
        with pytest.raises(ValueError, match="Dataset content hash mismatch"):
            await ResearchExecutor(store).execute(job)
