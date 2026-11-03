"""Independent real-PostgreSQL/process acceptance for phase 4.

The test creates a disposable database, submits through the Control research
API, starts a separate Research Worker OS process against that database only,
and re-reads the published result through a fresh pool. A missing PostgreSQL
environment is a hard failure, not a skip.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest

from trader.api.trading_app import create_trading_app
from trader.trading.jobs import BacktestApplication, PostgresResearchStore
from trader.trading.version import SPOT_RUNTIME_VERSION

ROOT = Path(__file__).resolve().parents[2]


def identity() -> dict:
    return {
        "venue": "binance",
        "market_type": "SPOT",
        "symbol": "BTCUSDT",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "interval": "1m",
        "source": "independent-phase4-postgres",
        "version": "1",
    }


def bars() -> list[dict]:
    prices = [100] * 26 + [101, 102, 103, 104, 105]
    rows = []
    for index, price in enumerate(prices):
        open_ms = index * 60_000
        rows.append(
            {
                "open_time_ms": open_ms,
                "close_time_ms": open_ms + 60_000,
                "available_at_ms": open_ms + 60_000,
                "open": str(price),
                "high": str(price),
                "low": str(price),
                "close": str(price),
                "volume": "1",
            }
        )
    return rows


def manifest(dataset_id: str, provenance_hash: str) -> dict:
    return {
        "dataset_ids": [dataset_id],
        "strategies": [
            {
                "strategy_id": "beta",
                "module_path": "trader.strategies.ema_cross_btc",
                "params": {"order_size": "1"},
            },
            {
                "strategy_id": "alpha",
                "module_path": "trader.strategies.ema_cross_btc",
                "params": {"order_size": "2"},
            },
        ],
        "book_allocations": {"book:beta": "500", "book:alpha": "500"},
        "initial_cash": "1000",
        "fee_bps": "10",
        "slippage_bps": "0",
        "engine": "event_runtime",
        "purpose": "research_screening",
        "runtime_version": SPOT_RUNTIME_VERSION,
        "provenance": {dataset_id: provenance_hash},
    }


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def redact(text: str) -> str:
    text = re.sub(r"postgres(?:ql)?://\S+", "<dsn>", str(text))
    return text[-6000:]


async def _create_database() -> tuple[str, str, object]:
    try:
        asyncpg = importlib.import_module("asyncpg")
    except ImportError:
        pytest.fail("asyncpg is required for the real-PostgreSQL phase-4 test")

    admin_dsn = os.environ.get("POSTGRES_CONNECTION_STRING", "").strip()
    if not admin_dsn:
        pytest.fail("POSTGRES_CONNECTION_STRING is required; no default DSN is accepted")
    parsed = urlsplit(admin_dsn)
    if not parsed.scheme or not parsed.netloc or not parsed.path.strip("/"):
        pytest.fail("POSTGRES_CONNECTION_STRING must be a database DSN")
    name = "qts_phase4_ind_" + uuid.uuid4().hex[:12]
    try:
        admin = await asyncpg.connect(admin_dsn)
    except Exception as exc:
        pytest.fail(f"cannot connect to the PostgreSQL admin DSN ({type(exc).__name__})")
    try:
        try:
            await admin.execute(f'CREATE DATABASE "{name}"')
        except Exception as exc:
            pytest.fail(
                "cannot create the task-owned disposable database "
                f"({type(exc).__name__}); no pytest skip is allowed for this check"
            )
    finally:
        await admin.close()
    dsn = urlunsplit((parsed.scheme, parsed.netloc, "/" + name, parsed.query, parsed.fragment))
    return name, dsn, asyncpg


async def _drop_database(name: str) -> None:
    asyncpg = importlib.import_module("asyncpg")
    admin_dsn = os.environ.get("POSTGRES_CONNECTION_STRING", "").strip()
    if not admin_dsn:
        return
    try:
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()
    except Exception:
        # Cleanup must not hide the original test failure; the database name is
        # random and task-owned, never an existing runtime database.
        return


@asynccontextmanager
async def research_database():
    name, dsn, asyncpg = await _create_database()
    pool = None
    try:
        try:
            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        except Exception as exc:
            pytest.fail(f"cannot open the disposable database pool ({type(exc).__name__})")
        store = PostgresResearchStore(pool)
        await store.initialize()
        yield store, pool, dsn
    finally:
        if pool is not None:
            await pool.close()
        await _drop_database(name)


async def trust_dataset(pool, document: dict, provenance_hash: str) -> dict:
    link = {
        "history_dataset_id": "hds_" + uuid.uuid4().hex[:12],
        "symbol": document["identity"]["symbol"],
        "interval": document["identity"]["interval"],
        "month": "2026-11",
        "conversion_version": "independent_phase4_v1",
        "provenance_hash": provenance_hash,
        "bar_count": len(document["bars"]),
    }
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO research.dataset_links (
                research_dataset_id, history_dataset_id, symbol, interval, month,
                conversion_version, provenance_hash, official_eligible, bar_count
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE, $8)
            ON CONFLICT (research_dataset_id) DO UPDATE SET
                official_eligible = TRUE,
                provenance_hash = EXCLUDED.provenance_hash,
                history_dataset_id = EXCLUDED.history_dataset_id
            """,
            document["dataset_id"],
            link["history_dataset_id"],
            link["symbol"],
            link["interval"],
            link["month"],
            link["conversion_version"],
            link["provenance_hash"],
            link["bar_count"],
        )
    return link


async def eligibility(store):
    async def check(dataset_id: str) -> bool:
        link = await store.get_dataset_link(dataset_id)
        return bool(link and link.get("official_eligible") and link.get("provenance_hash"))

    return check


class PoisonLive:
    """Any access proves the research path touched live account state."""

    def __init__(self):
        self.accessed = False

    def __getattr__(self, name):
        self.accessed = True
        raise AssertionError(f"research path touched live service member: {name}")


async def submit_via_control(store, application, manifest_value: dict):
    poison = PoisonLive()
    app = create_trading_app(
        trading_service=poison,
        research_store=store,
        research_application=application,
        include_history=False,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://control") as client:
        response = await client.post("/v1/research/backtests", json={"manifest": manifest_value})
    assert response.status_code == 202, response.text
    assert poison.accessed is False, "Control research submission touched live state"
    return response.json(), app


def run_worker(dsn: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["POSTGRES_CONNECTION_STRING"] = dsn
    env["QTS_WORKER_OWNER"] = "phase4-independent-worker-" + uuid.uuid4().hex[:8]
    env["QTS_WORKER_POLL_SECONDS"] = "0.05"
    env["PYTHONPATH"] = str(ROOT) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    # The worker receives no live account credentials, account database path or
    # exchange transport. Its only durable dependency is the research database.
    for key in list(env):
        if key.startswith("BINANCE_") or key.startswith("QTS_EXCHANGE_"):
            env.pop(key, None)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "trader.runtime.research_worker_main", "--once"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("research worker process timed out")
    if proc.returncode != 0:
        pytest.fail(
            f"research worker process failed with exit code {proc.returncode}\n"
            + redact(proc.stdout + "\n" + proc.stderr)
        )
    return proc


async def wait_for_status(store, run_id: str, expected: str, timeout: float = 20.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        record = await store.get(run_id)
        if record is not None and record.get("status") == expected:
            return record
        await asyncio.sleep(0.05)
    record = await store.get(run_id)
    pytest.fail(f"run {run_id} did not reach {expected}: {record}")


async def test_control_worker_postgres_publish_reload_and_tamper_detection():
    provenance_hash = "b" * 64
    async with research_database() as (store, pool, dsn):
        data = await store.register_dataset(identity(), bars())
        await trust_dataset(pool, data, provenance_hash)
        request_manifest = manifest(data["dataset_id"], provenance_hash)
        application = BacktestApplication(store, eligibility=await eligibility(store))

        job, _app = await submit_via_control(store, application, request_manifest)
        run_id = job["run_id"]
        assert job["status"] == "QUEUED"
        assert job["manifest_hash"] == sha(job["manifest"])
        assert job["manifest"]["runtime_version"] == SPOT_RUNTIME_VERSION
        assert job["manifest"]["provenance"] == {data["dataset_id"]: provenance_hash}

        proc = run_worker(dsn)
        assert proc.returncode == 0
        record = await wait_for_status(store, run_id, "SUCCEEDED")
        result = record["result"]
        assert result["engine"] == "event_runtime"
        assert result["runtime_version"] == SPOT_RUNTIME_VERSION
        assert result["manifest_hash"] == job["manifest_hash"]
        assert result["dataset_ids"] == [data["dataset_id"]]
        assert result["dataset_provenance"] == {data["dataset_id"]: provenance_hash}
        assert result["book_allocations"] == {"book:alpha": "500", "book:beta": "500"}
        assert [item["strategy_id"] for item in result["strategies"]] == ["alpha", "beta"]
        assert [item["book_id"] for item in result["strategies"]] == [
            "book:alpha",
            "book:beta",
        ]

        # Hand arithmetic: both strategies cross at bar 26 (close 101) and fill
        # at bar 27 open 102. alpha=2, beta=1, fee=10 bps.
        assert result["positions"] == {"BTCUSDT": "3"}
        assert result["cash"] == "693.694"
        assert result["net_value"] == "1008.694"
        assert result["total_fees"] == "0.306"
        books = {item["book_id"]: item for item in result["books"]}
        assert books["book:alpha"]["balances"]["USDT"] == "295.796"
        assert books["book:alpha"]["positions"] == {"BTCUSDT": "2"}
        assert books["book:alpha"]["fees"] == "0.204"
        assert books["book:alpha"]["equity"] == "505.796"
        assert books["book:beta"]["balances"]["USDT"] == "397.898"
        assert books["book:beta"]["positions"] == {"BTCUSDT": "1"}
        assert books["book:beta"]["fees"] == "0.102"
        assert books["book:beta"]["equity"] == "502.898"
        assert sum(Decimal(item["equity"]) for item in books.values()) == Decimal(
            result["net_value"]
        )
        assert result["book_fees"]["book:alpha"] == "0.204"
        assert result["book_fees"]["book:beta"] == "0.102"
        assert sum(Decimal(value) for value in result["book_fees"].values()) == Decimal("0.306")
        assert len(result["fills"]) == 2
        assert {(fill["book_id"], fill["price"], fill["qty"]) for fill in result["fills"]} == {
            ("book:alpha", "102", "2"),
            ("book:beta", "102", "1"),
        }

        # Reload through a fresh pool: publication is durable, not an in-memory
        # worker object. No live account table exists in this database.
        asyncpg = importlib.import_module("asyncpg")
        fresh_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        try:
            fresh_store = PostgresResearchStore(fresh_pool)
            reloaded = await fresh_store.get(run_id)
            assert reloaded is not None
            assert canonical(reloaded["result"]) == canonical(result)
            tables = await fresh_pool.fetch(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog','information_schema')"
            )
            assert tables
            assert {row["table_schema"] for row in tables} == {"research"}
        finally:
            await fresh_pool.close()

        # Same frozen manifest in a second fresh process/run is deterministic.
        job2, _app2 = await submit_via_control(store, application, request_manifest)
        assert job2["manifest_hash"] == job["manifest_hash"]
        run_worker(dsn)
        record2 = await wait_for_status(store, job2["run_id"], "SUCCEEDED")
        assert canonical(record2["result"]) == canonical(result)

        # Stored-manifest tampering is refused and not silently rewritten.
        original_manifest = copy.deepcopy(record2["manifest"])
        await pool.execute(
            "UPDATE research.backtest_jobs SET status='FAILED', error=NULL WHERE run_id=$1",
            job2["run_id"],
        )
        await pool.execute(
            "UPDATE research.backtest_jobs "
            "SET manifest=jsonb_set(manifest, '{strategies,0,params,order_size}', "
            "'\"999\"'::jsonb, false), updated_at=clock_timestamp() WHERE run_id=$1",
            job2["run_id"],
        )
        with pytest.raises(ValueError, match="Frozen manifest hash mismatch"):
            await application.retry(job2["run_id"])
        tampered = await pool.fetchrow(
            "SELECT manifest #>> '{strategies,0,params,order_size}' AS order_size, "
            "manifest_hash FROM research.backtest_jobs WHERE run_id=$1",
            job2["run_id"],
        )
        assert tampered["order_size"] == "999"
        assert tampered["manifest_hash"] == job2["manifest_hash"]

        await pool.execute(
            "UPDATE research.backtest_jobs SET manifest=$2::jsonb, status='FAILED', "
            "error=NULL WHERE run_id=$1",
            job2["run_id"],
            canonical(original_manifest),
        )
        retried = await application.retry(job2["run_id"])
        assert retried["status"] == "QUEUED"
        run_worker(dsn)
        record3 = await wait_for_status(store, job2["run_id"], "SUCCEEDED")
        assert canonical(record3["result"]) == canonical(result)
