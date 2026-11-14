"""Phase 4 acceptance: Control submit -> separate Research Worker -> real PostgreSQL.

The research worker runs as its own OS process against a disposable database
that contains only the ``research`` schema. Successful publication therefore
also proves the research path needs no live account state or exchange access.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from trader.api.trading_app import create_trading_app
from trader.tests.fakes.research_eligibility import always_eligible, trust_postgres_dataset
from trader.trading.datasets import digest
from trader.trading.jobs import BacktestApplication, PostgresResearchStore

ROOT = Path(__file__).resolve().parents[2]


def _admin_dsn():
    # The DSN is environment-only: no default endpoint, credential or database
    # is ever assumed, and the value is never written to argv or an artifact.
    dsn = os.getenv("POSTGRES_CONNECTION_STRING")
    if not dsn:
        pytest.fail("POSTGRES_CONNECTION_STRING is required for phase-4 PostgreSQL tests")
    return dsn


def _asyncpg():
    import importlib

    try:
        return importlib.import_module("asyncpg")
    except ImportError:
        pytest.fail("asyncpg is required for phase-4 PostgreSQL tests")


def identity():
    return {
        "venue": "binance",
        "market_type": "SPOT",
        "symbol": "BTCUSDT",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "interval": "1m",
        "source": "fixture",
        "version": "1",
    }


def bars(prices):
    rows = []
    for index, price in enumerate(prices):
        opened = index * 60_000
        rows.append(
            {
                "open_time_ms": opened,
                "close_time_ms": opened + 60_000,
                "available_at_ms": opened + 60_000,
                "open": str(price),
                "high": str(price),
                "low": str(price),
                "close": str(price),
                "volume": "1",
            }
        )
    return rows


PRICES = [100] * 26 + [101, 102, 103, 104, 105]


def joint_manifest(dataset_id, allocations=None, order_sizes=("1", "1")):
    return {
        "dataset_ids": [dataset_id],
        "strategies": [
            {
                "strategy_id": "alpha",
                "module_path": "trader.strategies.ema_cross_btc",
                "params": {"order_size": order_sizes[0]},
            },
            {
                "strategy_id": "beta",
                "module_path": "trader.strategies.ema_cross_btc",
                "params": {"order_size": order_sizes[1]},
            },
        ],
        "book_allocations": allocations or {"book:alpha": "500", "book:beta": "500"},
        "initial_cash": "1000",
        "fee_bps": "10",
        "slippage_bps": "0",
        "engine": "event_runtime",
        "purpose": "research_screening",
    }


@asynccontextmanager
async def research_database():
    admin_dsn = _admin_dsn()
    asyncpg = _asyncpg()
    name = "qts_phase4_test_" + uuid.uuid4().hex[:12]
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    except Exception as exc:  # insufficient privilege is a hard failure, not a skip
        await admin.close()
        pytest.fail(f"cannot create a disposable test database: {exc}")
    dsn = f"{admin_dsn.rsplit('/', 1)[0]}/{name}"
    pool = None
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        store = PostgresResearchStore(pool)
        await store.initialize()
        yield store, pool, dsn
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


def run_worker_process(dsn):
    """Run the actual worker entry point as a separate process."""
    env = dict(os.environ)
    env["POSTGRES_CONNECTION_STRING"] = dsn
    env["QTS_WORKER_OWNER"] = "phase4-process"
    env["QTS_WORKER_POLL_SECONDS"] = "0.1"
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    # The research worker must not need exchange credentials; remove any that
    # the developer shell happens to carry so the run proves it.
    for key in list(env):
        if key.startswith("BINANCE_") or key == "LIVE_TRADING_ENABLED":
            env.pop(key, None)
    return subprocess.run(
        [sys.executable, "-m", "trader.runtime.research_worker_main", "--once"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


async def submit_via_control(store, manifest):
    app = create_trading_app(
        research_store=store,
        research_application=BacktestApplication(store, eligibility=always_eligible),
        include_history=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post("/v1/research/backtests", json={"manifest": manifest})
        return response


@pytest.mark.asyncio
async def test_control_submission_worker_process_and_postgres_publication():
    """4.1-4.4 / 4.8-4.10 on real PostgreSQL and two processes."""
    async with research_database() as (store, pool, dsn):
        data = await store.register_dataset(identity(), bars(PRICES))
        link = await trust_postgres_dataset(pool, data)
        manifest = joint_manifest(data["dataset_id"])

        response = await submit_via_control(store, manifest)
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["status"] == "QUEUED"
        assert job["manifest_hash"] == digest(job["manifest"])
        run_id = job["run_id"]

        completed = run_worker_process(dsn)
        assert completed.returncode == 0, (
            f"worker returncode={completed.returncode}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )

        # Reload through a brand-new pool: the result is durable across the
        # Control/worker process boundary, not an in-memory fake.
        asyncpg = _asyncpg()
        fresh_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        try:
            fresh_store = PostgresResearchStore(fresh_pool)
            record = await fresh_store.get(run_id)
            assert record is not None
            assert record["status"] == "SUCCEEDED", record.get("error")
            assert record["manifest_hash"] == job["manifest_hash"]
            result = record["result"]
            assert result["runtime_version"] == "spot_bar_v2_shared"
            assert result["manifest_hash"] == job["manifest_hash"]
            assert result["runtime_manifest_hash"]
            assert result["dataset_ids"] == job["manifest"]["dataset_ids"]
            assert result["dataset_provenance"] == {data["dataset_id"]: link["provenance_hash"]}
            books = {item["book_id"]: item for item in result["books"]}
            assert set(books) == {"UNALLOCATED", "book:alpha", "book:beta"}
            for book_id in ("book:alpha", "book:beta"):
                assert books[book_id]["positions"] == {"BTCUSDT": "1"}
                assert books[book_id]["fees"] == "0.102"
                assert books[book_id]["allocation"] == "500"
            assert sum(Decimal(value) for value in result["book_fees"].values()) == Decimal(
                result["total_fees"]
            )
            assert result["report"]["strategies"][0]["strategy_id"] == "alpha"
            assert result["report"]["strategies"][0]["book_id"] == "book:alpha"

            row = await pool.fetchrow(
                "SELECT manifest, manifest_hash, status FROM research.backtest_jobs WHERE run_id=$1",
                run_id,
            )
            stored_manifest = row["manifest"]
            if isinstance(stored_manifest, str):
                stored_manifest = json.loads(stored_manifest)
            assert digest(stored_manifest) == row["manifest_hash"] == job["manifest_hash"]
            assert row["status"] == "SUCCEEDED"

            # The worker was given a database with only research state. No live
            # trading/account table was created or read.
            tables = await pool.fetch(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
            )
            assert tables, "research tables must exist"
            assert {row["table_schema"] for row in tables} == {"research"}

            # A second identical submission runs in a fresh worker process and
            # produces the same frozen hash and byte-identical result.
            second = await submit_via_control(store, manifest)
            assert second.status_code == 202, second.text
            second_job = second.json()
            assert second_job["manifest_hash"] == job["manifest_hash"]
            second_run = run_worker_process(dsn)
            assert second_run.returncode == 0, (
                f"worker returncode={second_run.returncode}\n"
                f"stdout={second_run.stdout}\nstderr={second_run.stderr}"
            )
            second_record = await fresh_store.get(second_job["run_id"])
            assert second_record["status"] == "SUCCEEDED", second_record.get("error")
            assert second_record["result"] == result
        finally:
            await fresh_pool.close()


@pytest.mark.asyncio
async def test_control_rejects_an_over_budget_joint_allocation_before_queueing():
    """4.7 on the Control boundary: the refused input leaves no queued job."""
    async with research_database() as (store, pool, _dsn):
        data = await store.register_dataset(identity(), bars(PRICES))
        await trust_postgres_dataset(pool, data)
        response = await submit_via_control(
            store,
            joint_manifest(
                data["dataset_id"],
                allocations={"book:alpha": "100", "book:beta": "800"},
            ),
        )
        assert response.status_code == 422, response.text
        assert "sum to the initial cash" in response.text
        count = await pool.fetchval("SELECT count(*) FROM research.backtest_jobs")
        assert count == 0
