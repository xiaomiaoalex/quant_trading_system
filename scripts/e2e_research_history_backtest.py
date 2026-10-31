#!/usr/bin/env python3
"""Real Research history data end-to-end acceptance.

Runs the Binance Vision real archives through the **new Research plane**:

    download -> verify -> strict persistence -> atomic Research publish
        -> event runtime backtest -> persistent report
        -> restart / interruption / retry / determinism checks
        -> performance record (elapsed, memory, API response times)

Safety constraints:

- Only connects to an isolated test database (default ``qts_research_history_e2e``).
  The database is **never dropped automatically**.
- Only reads public archives. No account, no order, no deployment.
- The archives land under ``MARKET_DATA_ROOT``.

Usage::

    python scripts/e2e_research_history_backtest.py \
        --symbols BTCUSDT,ETHUSDT --month 2026-09 \
        --admin-dsn postgresql://trader:trader_pwd@127.0.0.1:5432/postgres
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT"
DEFAULT_MONTH = "2026-09"
DEFAULT_TEST_DB = "qts_research_history_e2e"
DEFAULT_INITIAL_CASH = "100000"
DEFAULT_FEE_BPS = "10"
DEFAULT_SLIPPAGE_BPS = "5"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real Research history data E2E acceptance")
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--month", default=DEFAULT_MONTH)
    parser.add_argument(
        "--admin-dsn",
        default=os.environ.get(
            "E2E_ADMIN_DSN", "postgresql://trader:trader_pwd@127.0.0.1:5432/postgres"
        ),
    )
    parser.add_argument("--test-db", default=DEFAULT_TEST_DB)
    parser.add_argument(
        "--market-data-root",
        default=os.environ.get("MARKET_DATA_ROOT", ""),
        help="Archive root directory (defaults to <project>/data/market)",
    )
    parser.add_argument(
        "--strategy", default="trader.strategies.ema_cross_btc", help="Installed strategy module"
    )
    parser.add_argument("--skip-network", action="store_true", help="Require local archives only")
    return parser.parse_args()


def _with_database(dsn: str, database: str) -> str:
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{database}"))


async def _ensure_database(admin_dsn: str, test_db: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(admin_dsn)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", test_db)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{test_db}"')
            print(f"[e2e] created isolated database {test_db}")
    finally:
        await conn.close()


def _percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(percentile * (len(ordered) - 1)))))
    return ordered[index]


def _rss_mb() -> Optional[float]:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:  # pragma: no cover - psutil is optional
        return None


async def _run(args: argparse.Namespace) -> Dict[str, Any]:
    test_dsn = _with_database(args.admin_dsn, args.test_db)
    os.environ["POSTGRES_CONNECTION_STRING"] = test_dsn
    if args.market_data_root:
        os.environ["MARKET_DATA_ROOT"] = args.market_data_root

    await _ensure_database(args.admin_dsn, args.test_db)

    import asyncpg

    from trader.adapters.persistence.history_dataset_store import HistoryDatasetStore
    from trader.adapters.persistence.postgres import PostgreSQLStorage
    from trader.adapters.persistence.research_history_bridge_store import (
        ResearchHistoryBridgeStore,
    )
    from trader.core.domain.services.ohlcv_archive import (
        expected_points_for_month,
        last_bar_open_ms,
        month_bounds_ms,
        parse_month,
    )
    from trader.services.history_data_ingestion import HistoryIngestionService
    from trader.trading.jobs import (
        BacktestApplication,
        PostgresResearchStore,
        ResearchWorker,
    )
    from trader.trading.research import ResearchExecutor

    year, month_number = parse_month(args.month)
    expected_points = expected_points_for_month(year, month_number)
    month_start_ms, month_end_exclusive_ms = month_bounds_ms(year, month_number)
    month_last_bar_ms = last_bar_open_ms(year, month_number)
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]

    summary: Dict[str, Any] = {
        "month": args.month,
        "expected_points": expected_points,
        "month_start_ms": month_start_ms,
        "month_end_exclusive_ms": month_end_exclusive_ms,
        "isolated_database": args.test_db,
        "datasets": [],
        "backtests": [],
        "errors": [],
    }

    storage = PostgreSQLStorage(connection_string=test_dsn)
    await storage.connect()
    pool = await asyncpg.create_pool(test_dsn, min_size=1, max_size=6)
    store = HistoryDatasetStore(storage=storage)
    bridge = ResearchHistoryBridgeStore(storage=storage)
    await bridge.ensure_research_schema()
    research = PostgresResearchStore(pool)
    await research.initialize()
    service = HistoryIngestionService(store=store, bridge_store=bridge)

    async def dataset_eligible(dataset_id: str) -> bool:
        link = await bridge.find_link_by_research_dataset(dataset_id)
        if not link or not link.get("official_eligible"):
            return False
        row = await store.get_dataset(str(link["history_dataset_id"]))
        return bool(
            row
            and row.get("status") == "PUBLISHED"
            and row.get("persistence_ok")
            and row.get("backtest_eligible")
        )

    application = BacktestApplication(research, eligibility=dataset_eligible)

    try:
        # ==================== 1. import + publish ====================
        for symbol in symbols:
            print(f"[e2e] === {symbol} {args.month} ===")
            started = time.perf_counter()
            job = await service.submit_job(symbol=symbol, month=args.month, requested_by="e2e")
            result = await service.run_job(job["job_id"])
            elapsed = time.perf_counter() - started
            if result.get("status") != "SUCCEEDED":
                raise SystemExit(
                    f"[e2e] FAILED job {job['job_id']}: "
                    f"{result.get('error_code')} {result.get('error_reason')}"
                )

            dataset_id = str(result["dataset_id"])
            dataset = await store.get_dataset(dataset_id)
            assert dataset is not None
            assert (
                int(dataset["row_count"]) == expected_points
            ), f"{symbol}: row_count {dataset['row_count']} != {expected_points}"
            assert int(dataset["gap_count"]) == 0
            assert int(dataset["first_ts_ms"]) == month_start_ms
            assert int(dataset["last_ts_ms"]) == month_last_bar_ms
            assert dataset["official_sha256"] == dataset["archive_sha256"]
            assert dataset["status"] == "PUBLISHED"
            assert dataset["persistence_ok"] is True
            assert dataset["backtest_eligible"] is True
            research_dataset_id = dataset.get("research_dataset_id")
            assert research_dataset_id, f"{symbol}: no research dataset id"

            document = await research.get_dataset(str(research_dataset_id))
            assert document is not None, f"{symbol}: research dataset missing"
            assert document["quality"]["bar_count"] == expected_points
            assert document["bars"][0]["open_time_ms"] == month_start_ms
            assert document["bars"][-1]["close_time_ms"] == month_end_exclusive_ms
            for bar in document["bars"][:64]:
                assert bar["close_time_ms"] == bar["open_time_ms"] + 60_000
                assert bar["available_at_ms"] == bar["close_time_ms"]

            link = await bridge.find_link_by_research_dataset(str(research_dataset_id))
            assert link is not None
            assert link["official_eligible"] is True
            assert link["provenance_hash"]
            assert link["archive_sha256"] == dataset["archive_sha256"]
            assert link["bar_count"] == expected_points

            summary["datasets"].append(
                {
                    "symbol": symbol,
                    "history_dataset_id": dataset_id,
                    "research_dataset_id": research_dataset_id,
                    "feature_version": dataset["feature_version"],
                    "row_count": int(dataset["row_count"]),
                    "expected_points": int(dataset["expected_points"]),
                    "gap_count": int(dataset["gap_count"]),
                    "first_ts_ms": int(dataset["first_ts_ms"]),
                    "last_ts_ms": int(dataset["last_ts_ms"]),
                    "first_utc": datetime.fromtimestamp(
                        int(dataset["first_ts_ms"]) / 1000, tz=timezone.utc
                    ).isoformat(),
                    "last_utc": datetime.fromtimestamp(
                        int(dataset["last_ts_ms"]) / 1000, tz=timezone.utc
                    ).isoformat(),
                    "official_sha256": dataset["official_sha256"],
                    "archive_sha256": dataset["archive_sha256"],
                    "source_url": dataset["source_url"],
                    "provenance_hash": link["provenance_hash"],
                    "import_seconds": round(elapsed, 3),
                }
            )
            print(
                f"[e2e] dataset {dataset_id} -> {research_dataset_id} "
                f"rows={dataset['row_count']} gaps={dataset['gap_count']} "
                f"in {elapsed:.1f}s"
            )

        # ==================== 2. event runtime backtests ====================
        for symbol in symbols:
            research_dataset_id = str(
                next(
                    item["research_dataset_id"]
                    for item in summary["datasets"]
                    if item["symbol"] == symbol
                )
            )
            manifest = {
                "dataset_ids": [research_dataset_id],
                "strategy": {"module_path": args.strategy, "params": {}},
                "initial_cash": DEFAULT_INITIAL_CASH,
                "fee_bps": DEFAULT_FEE_BPS,
                "slippage_bps": DEFAULT_SLIPPAGE_BPS,
                "engine": "event_runtime",
                "purpose": "research_screening",
            }
            started = time.perf_counter()
            record = await application.submit(manifest)
            run_id = record["run_id"]
            assert record["manifest"]["provenance"].get(
                research_dataset_id
            ), "provenance must be frozen into the manifest"
            worker = ResearchWorker(research, owner="e2e-worker")
            finished = await worker.run_once(ResearchExecutor(research).execute)
            elapsed = time.perf_counter() - started
            assert finished is not None, f"{symbol}: worker produced no result"
            assert (
                finished["status"] == "SUCCEEDED"
            ), f"{symbol}: {finished['status']} {finished.get('error')}"
            result_payload = finished["result"]
            assert result_payload["engine"] == "event_runtime"
            assert len(result_payload["equity_points"]) == expected_points
            assert result_payload["equity_points"][0]["at_ms"] == month_start_ms + 60_000
            assert result_payload["equity_points"][-1]["at_ms"] == month_end_exclusive_ms
            assert result_payload["equity_curve"]
            assert result_payload["report"]["provenance"].get(research_dataset_id)
            assert result_payload["report"]["strategy"]["code_hash"]
            assert result_payload["report"]["costs"]["fee_bps"] == "10"
            assert result_payload["report"]["costs"]["slippage_bps"] == "5"
            assert result_payload["report"]["runtime"]["runtime_version"]
            assert result_payload["report"]["executions"]["rejections"] is not None
            assert (
                result_payload["admissible"] is False
            ), "a screening run is never deployment admission"

            summary["backtests"].append(
                {
                    "symbol": symbol,
                    "run_id": run_id,
                    "status": finished["status"],
                    "manifest_hash": finished["manifest_hash"],
                    "net_value": result_payload["net_value"],
                    "total_return": result_payload["total_return"],
                    "max_drawdown_pct": result_payload["max_drawdown_pct"],
                    "total_fees": result_payload["total_fees"],
                    "fill_count": len(result_payload["fills"]),
                    "equity_points": len(result_payload["equity_points"]),
                    "seconds": round(elapsed, 3),
                }
            )
            print(
                f"[e2e] backtest {run_id} {finished['status']} "
                f"net={result_payload['net_value']} in {elapsed:.1f}s"
            )

        # ==================== 3. interruption + explicit retry ====================
        interrupted = await application.submit(
            {
                "dataset_ids": [summary["datasets"][0]["research_dataset_id"]],
                "strategy": {"module_path": args.strategy, "params": {}},
                "initial_cash": DEFAULT_INITIAL_CASH,
                "fee_bps": DEFAULT_FEE_BPS,
                "slippage_bps": DEFAULT_SLIPPAGE_BPS,
                "engine": "event_runtime",
                "purpose": "research_screening",
            }
        )
        interrupted_run_id = interrupted["run_id"]

        async def exploding(record_to_run):
            raise RuntimeError("simulated worker interruption")

        failed = await ResearchWorker(research, owner="e2e-worker").run_once(exploding)
        assert failed is not None and failed["status"] == "FAILED"
        assert "simulated worker interruption" in failed["error"]

        # A FAILED run is terminal: the worker must not pick it up again.
        assert await research.claim("e2e-worker-2") is None

        requeued = await research.retry(interrupted_run_id)
        assert requeued["status"] == "QUEUED"
        assert requeued["run_id"] == interrupted_run_id
        assert requeued["manifest_hash"] == interrupted["manifest_hash"]

        recovered = await ResearchWorker(research, owner="e2e-worker-3").run_once(
            ResearchExecutor(research).execute
        )
        assert recovered is not None and recovered["status"] == "SUCCEEDED"
        assert recovered["manifest_hash"] == interrupted["manifest_hash"]
        summary["interruption"] = {
            "run_id": interrupted_run_id,
            "failed_then_retried": True,
            "attempt_after_retry": recovered["attempt"],
            "manifest_hash_preserved": True,
        }

        # ==================== 4. determinism ====================
        repeat_manifest = {
            "dataset_ids": [summary["datasets"][0]["research_dataset_id"]],
            "strategy": {"module_path": args.strategy, "params": {}},
            "initial_cash": DEFAULT_INITIAL_CASH,
            "fee_bps": DEFAULT_FEE_BPS,
            "slippage_bps": DEFAULT_SLIPPAGE_BPS,
            "engine": "event_runtime",
            "purpose": "research_screening",
        }
        first_record = await application.submit(repeat_manifest)
        first_result = await ResearchWorker(research, owner="e2e-det-1").run_once(
            ResearchExecutor(research).execute
        )
        second_record = await application.submit(repeat_manifest)
        second_result = await ResearchWorker(research, owner="e2e-det-2").run_once(
            ResearchExecutor(research).execute
        )
        assert first_result["status"] == "SUCCEEDED"
        assert second_result["status"] == "SUCCEEDED"
        assert first_record["manifest_hash"] == second_record["manifest_hash"]
        first_business = {k: v for k, v in first_result["result"].items() if k != "report"}
        second_business = {k: v for k, v in second_result["result"].items() if k != "report"}
        assert first_business == second_business, "repeated frozen input must repeat the result"
        assert first_result["result"]["report"]["performance"] == (
            second_result["result"]["report"]["performance"]
        )
        summary["determinism"] = {
            "manifest_hash_equal": True,
            "business_result_equal": True,
        }

        # ==================== 5. restart ====================
        await pool.close()
        await storage.disconnect()

        restarted_storage = PostgreSQLStorage(connection_string=test_dsn)
        await restarted_storage.connect()
        restarted_pool = await asyncpg.create_pool(test_dsn, min_size=1, max_size=4)
        restarted_store = HistoryDatasetStore(storage=restarted_storage)
        restarted_bridge = ResearchHistoryBridgeStore(storage=restarted_storage)
        restarted_research = PostgresResearchStore(restarted_pool)
        await restarted_research.initialize()
        try:
            catalog = await restarted_research.list_datasets(official_only=True)
            assert len(catalog) >= len(symbols)
            for symbol in symbols:
                research_dataset_id = str(
                    next(
                        item["research_dataset_id"]
                        for item in summary["datasets"]
                        if item["symbol"] == symbol
                    )
                )
                document = await restarted_research.get_dataset(research_dataset_id)
                assert document is not None
                assert document["quality"]["bar_count"] == expected_points
                link = await restarted_bridge.find_link_by_research_dataset(research_dataset_id)
                assert link is not None and link["official_eligible"] is True
            jobs = await restarted_store.list_jobs(limit=100)
            datasets = await restarted_store.list_datasets(limit=100)
            runs = await restarted_research.list(limit=100)
            summary["restart"] = {
                "history_jobs": len(jobs),
                "history_datasets": len(datasets),
                "research_datasets": len(catalog),
                "research_runs": len(runs),
                "run_statuses": sorted({str(run["status"]) for run in runs}),
            }
        finally:
            await restarted_pool.close()
            await restarted_storage.disconnect()

        # ==================== 6. API response times ====================
        api_timings = await _measure_api(test_dsn, args)
        summary["performance"] = api_timings
        print("[e2e] performance:", json.dumps(api_timings, ensure_ascii=False))

        # ==================== 7. real process restart ====================
        summary["process_restart"] = await _process_restart_phase(
            test_dsn, args, [item["research_dataset_id"] for item in summary["datasets"]]
        )
        print("[e2e] process restart:", json.dumps(summary["process_restart"], ensure_ascii=False))
        return summary
    finally:
        try:
            from trader.services.history_data_ingestion import (
                reset_history_archive_client,
            )

            await reset_history_archive_client()
        except Exception:  # pragma: no cover - best effort cleanup
            pass
        try:
            await pool.close()
        except Exception:  # pragma: no cover - best effort cleanup
            pass
        try:
            await storage.disconnect()
        except Exception:  # pragma: no cover - best effort cleanup
            pass


async def _process_restart_phase(
    test_dsn: str, args: argparse.Namespace, dataset_ids: List[str]
) -> Dict[str, Any]:
    """真正重启 Control API / 历史 worker / Research worker 进程后的可查询性验证。"""
    import socket
    import subprocess

    import httpx

    from trader.api.auth import hash_password

    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    root = Path(__file__).resolve().parents[1]
    port = _free_port()
    password = "e2e-password"
    env = dict(os.environ)
    env["POSTGRES_CONNECTION_STRING"] = test_dsn
    env["QTS_USERNAME"] = "e2e"
    env["QTS_PASSWORD_HASH"] = hash_password(password)
    env["QTS_ORIGIN"] = f"http://127.0.0.1:{port}"
    env["QTS_API_HOST"] = "127.0.0.1"
    env["QTS_TRADING_API_PORT"] = str(port)
    if args.market_data_root:
        env["MARKET_DATA_ROOT"] = args.market_data_root

    result: Dict[str, Any] = {}

    # ---- Control API 进程 ----
    api = subprocess.Popen(
        [sys.executable, "-m", "trader.runtime.control_main"],
        cwd=str(root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base, timeout=15.0) as client:
            deadline = time.time() + 90
            healthy = False
            while time.time() < deadline:
                if api.poll() is not None:
                    break
                try:
                    response = await client.get("/health")
                    if response.status_code == 200:
                        healthy = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.5)
            assert healthy, "control API process did not become healthy"

            login = await client.post(
                "/v1/auth/login", json={"username": "e2e", "password": password}
            )
            assert login.status_code == 200, login.text

            catalog = await client.get("/v1/research/datasets", params={"limit": 50})
            assert catalog.status_code == 200, catalog.text
            entries = catalog.json()
            assert entries, "the Research catalog must not be empty after a restart"
            for entry in entries:
                assert entry["official_eligible"] is True
                assert entry["bar_count"] == 43200
                assert entry["provenance_hash"]

            runs = await client.get("/v1/research/backtests", params={"limit": 100})
            assert runs.status_code == 200, runs.text
            run_records = runs.json()
            assert any(
                item["status"] == "SUCCEEDED" for item in run_records
            ), "no successful run survived the restart"

            history = await client.get("/v1/data/history/datasets", params={"limit": 50})
            assert history.status_code == 200, history.text
            history_records = history.json()
            assert history_records, "no history dataset survived the restart"
            for record in history_records:
                assert record["research_dataset_id"], "history dataset lost its research link"

            strategies = await client.get("/v1/research/strategies")
            assert strategies.status_code == 200, strategies.text
            assert strategies.json(), "the strategy catalogue must not be empty"

            result["control_api"] = {
                "port": port,
                "catalog_entries": len(entries),
                "runs": len(run_records),
                "history_datasets": len(history_records),
                "strategies": len(strategies.json()),
            }
    finally:
        api.terminate()
        try:
            api.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            api.kill()

    # ---- 历史 worker 进程 ----
    history_worker = subprocess.run(
        [sys.executable, "-m", "trader.runtime.history_worker_main", "--once"],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert (
        history_worker.returncode == 0
    ), f"history worker failed: {history_worker.stdout}\n{history_worker.stderr}"
    result["history_worker"] = {
        "returncode": history_worker.returncode,
        "stdout_tail": history_worker.stdout.strip().splitlines()[-1:] or [],
    }

    # ---- Research worker 进程 ----
    import asyncpg

    from trader.trading.jobs import PostgresResearchStore

    pool = await asyncpg.create_pool(test_dsn, min_size=1, max_size=4)
    research = PostgresResearchStore(pool)
    await research.initialize()
    try:
        dataset_id = dataset_ids[0]
        record = await BacktestApplication(research).submit(
            {
                "dataset_ids": [dataset_id],
                "strategy": {"module_path": args.strategy, "params": {}},
                "initial_cash": DEFAULT_INITIAL_CASH,
                "fee_bps": DEFAULT_FEE_BPS,
                "slippage_bps": DEFAULT_SLIPPAGE_BPS,
                "engine": "event_runtime",
                "purpose": "research_screening",
            }
        )
        run_id = record["run_id"]
        attempts = 0
        status = "QUEUED"
        while attempts < 30 and status not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            attempts += 1
            worker = subprocess.run(
                [sys.executable, "-m", "trader.runtime.research_worker_main", "--once"],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
                timeout=900,
            )
            assert (
                worker.returncode == 0
            ), f"research worker failed: {worker.stdout}\n{worker.stderr}"
            current = await research.get(run_id)
            status = str(current["status"])
        assert status == "SUCCEEDED", f"research worker did not finish the run: {status}"
        finished = await research.get(run_id)
        result["research_worker"] = {
            "run_id": run_id,
            "status": status,
            "attempts": attempts,
            "result_equity_points": len(finished["result"]["equity_points"]),
        }
    finally:
        await pool.close()

    return result


async def _measure_api(test_dsn: str, args: argparse.Namespace) -> Dict[str, Any]:
    """Measure the Research catalog and submit endpoints over the real app."""
    import asyncpg

    from trader.adapters.persistence.history_dataset_store import HistoryDatasetStore
    from trader.adapters.persistence.postgres import PostgreSQLStorage
    from trader.adapters.persistence.research_history_bridge_store import (
        ResearchHistoryBridgeStore,
    )
    from trader.api.trading_app import create_trading_app
    from trader.services.history_data_ingestion import HistoryIngestionService
    from trader.trading.jobs import BacktestApplication, PostgresResearchStore

    storage = PostgreSQLStorage(connection_string=test_dsn)
    await storage.connect()
    pool = await asyncpg.create_pool(test_dsn, min_size=1, max_size=4)
    store = HistoryDatasetStore(storage=storage)
    bridge = ResearchHistoryBridgeStore(storage=storage)
    research = PostgresResearchStore(pool)
    await research.initialize()
    service = HistoryIngestionService(store=store, bridge_store=bridge)

    async def dataset_eligible(dataset_id: str) -> bool:
        link = await bridge.find_link_by_research_dataset(dataset_id)
        if not link:
            return False
        row = await store.get_dataset(str(link["history_dataset_id"]))
        return bool(row and row.get("status") == "PUBLISHED")

    app = create_trading_app(
        research_store=research,
        research_application=BacktestApplication(research, eligibility=dataset_eligible),
        history_service=service,
    )

    catalog_times: List[float] = []
    submit_times: List[float] = []
    try:
        import httpx

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://control") as client:
            for _ in range(5):
                started = time.perf_counter()
                response = await client.get("/v1/research/datasets", params={"limit": 50})
                catalog_times.append(time.perf_counter() - started)
                assert response.status_code == 200, response.text
            catalog = response.json()
            assert catalog, "the Research catalog must not be empty"

            dataset_id = str(catalog[0]["dataset_id"])
            manifest = {
                "dataset_ids": [dataset_id],
                "strategy": {"module_path": args.strategy, "params": {}},
                "initial_cash": DEFAULT_INITIAL_CASH,
                "fee_bps": DEFAULT_FEE_BPS,
                "slippage_bps": DEFAULT_SLIPPAGE_BPS,
                "engine": "event_runtime",
                "purpose": "research_screening",
            }
            for _ in range(5):
                started = time.perf_counter()
                response = await client.post("/v1/research/backtests", json={"manifest": manifest})
                submit_times.append(time.perf_counter() - started)
                assert response.status_code == 202, response.text
    finally:
        await pool.close()
        await storage.disconnect()

    return {
        "catalog_p95_seconds": round(_percentile(catalog_times, 0.95), 4),
        "catalog_max_seconds": round(max(catalog_times), 4),
        "submit_p95_seconds": round(_percentile(submit_times, 0.95), 4),
        "submit_max_seconds": round(max(submit_times), 4),
        "rss_mb": round(_rss_mb(), 1) if _rss_mb() else None,
        "host_cpu_count": os.cpu_count(),
        "platform": sys.platform,
    }


def main() -> int:
    args = _parse_args()
    summary = asyncio.run(_run(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("[e2e] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
