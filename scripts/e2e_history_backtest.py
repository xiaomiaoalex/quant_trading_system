#!/usr/bin/env python3
"""
Real History Data End-to-End Acceptance Script
===============================================
用币安公开归档的真实历史行情，完整跑通：

    下载 → 校验 → 规范化 → PostgreSQL 严格持久化 → 数据集登记
        → 真实 vectorbt 回测 → 报告追溯证据
        → 模拟重启后历史任务 / 数据集 / 行情仍可查询

安全约束：
- 只连接隔离测试数据库（默认 ``qts_history_e2e``），不触碰生产库
- 只读公开归档，不涉及任何账户 / 下单 / 部署
- 原始归档落在 ``MARKET_DATA_ROOT`` 指定的目录

用法::

    python scripts/e2e_history_backtest.py \
        --symbols BTCUSDT,ETHUSDT --month 2026-09 \
        --admin-dsn postgresql://trader:trader_pwd@127.0.0.1:5432/postgres
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse, urlunparse

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT"
DEFAULT_MONTH = "2026-09"
DEFAULT_TEST_DB = "qts_history_e2e"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real history data E2E acceptance")
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--month", default=DEFAULT_MONTH)
    parser.add_argument(
        "--admin-dsn",
        default=os.environ.get(
            "E2E_ADMIN_DSN", "postgresql://trader:trader_pwd@127.0.0.1:5432/postgres"
        ),
        help="DSN pointing at the maintenance database (used to create the isolated DB)",
    )
    parser.add_argument("--test-db", default=DEFAULT_TEST_DB)
    parser.add_argument(
        "--market-data-root",
        default=os.environ.get("MARKET_DATA_ROOT", ""),
        help="Archive root directory (defaults to <project>/data/market)",
    )
    parser.add_argument("--keep-db", action="store_true", help="Do not drop the isolated DB")
    return parser.parse_args()


def _with_database(dsn: str, database: str) -> str:
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{database}"))


async def _ensure_database(admin_dsn: str, test_dsn: str, test_db: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(admin_dsn)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", test_db)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{test_db}"')
            print(f"[e2e] created isolated database {test_db}")
    finally:
        await conn.close()
    del test_dsn


async def _run(args: argparse.Namespace) -> Dict[str, Any]:
    test_dsn = _with_database(args.admin_dsn, args.test_db)
    os.environ["POSTGRES_CONNECTION_STRING"] = test_dsn
    if args.market_data_root:
        os.environ["MARKET_DATA_ROOT"] = args.market_data_root

    await _ensure_database(args.admin_dsn, test_dsn, args.test_db)

    # 延迟导入，确保环境变量（隔离 DSN / 行情根目录）已经生效
    from trader.adapters.persistence.feature_store import get_strict_feature_store
    from trader.api.main import _seed_strategies
    from trader.api.models.schemas import BacktestRequest
    from trader.core.domain.services.ohlcv_archive import (
        expected_points_for_month,
        last_bar_open_ms,
        month_bounds_ms,
        parse_month,
    )
    from trader.services.backtesting.backtest_dataset_resolution import resolve_backtest_request
    from trader.services.deployment import BacktestService
    from trader.services.history_data_ingestion import (
        HistoryIngestionService,
        reset_history_dataset_store,
    )

    year, month_number = parse_month(args.month)
    expected_points = expected_points_for_month(year, month_number)
    month_start_ms, month_end_exclusive_ms = month_bounds_ms(year, month_number)
    month_last_bar_ms = last_bar_open_ms(year, month_number)

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    summary: Dict[str, Any] = {
        "month": args.month,
        "expected_points": expected_points,
        "month_start_ms": month_start_ms,
        "month_last_bar_ms": month_last_bar_ms,
        "isolated_database": args.test_db,
        "datasets": [],
        "backtests": [],
        "errors": [],
    }

    service = HistoryIngestionService()
    _seed_strategies()

    for symbol in symbols:
        print(f"[e2e] === {symbol} {args.month} ===")
        job = await service.submit_job(symbol=symbol, month=args.month, requested_by="e2e")
        print(f"[e2e] job {job['job_id']} status={job['status']}")
        result = await service.run_job(job["job_id"])
        if result.get("status") != "SUCCEEDED":
            raise SystemExit(
                f"[e2e] FAILED job {job['job_id']}: "
                f"{result.get('error_code')} {result.get('error_reason')}"
            )

        dataset_id = str(result["dataset_id"])
        store = await service._get_store()
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

        summary["datasets"].append(
            {
                "symbol": symbol,
                "dataset_id": dataset_id,
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
                "archive_path": dataset["archive_path"],
            }
        )
        print(
            f"[e2e] dataset {dataset_id} rows={dataset['row_count']} "
            f"sha256={dataset['archive_sha256'][:16]}…"
        )

        # ---- 真实回测 ----
        request = BacktestRequest(
            strategy_id="ema_cross_btc",
            version=1,
            engine="vectorbt",
            symbols=[symbol],
            start_ts_ms=None,
            end_ts_ms=None,
            venue="BINANCE",
            requested_by="e2e",
            dataset_id=dataset_id,
            initial_capital=100000.0,
            fee_bps=10.0,
            slippage_bps=5.0,
            benchmark=symbol,
            risk_mode="raw_only",
        )
        resolved = await resolve_backtest_request(request)
        assert resolved.data_mode == "real_feature_store"
        assert resolved.engine == "vectorbt"
        assert resolved.interval == "1m"
        assert resolved.start_ts_ms == month_start_ms
        assert resolved.end_ts_ms == month_last_bar_ms

        backtest_service = BacktestService()
        run = backtest_service.create_backtest(resolved)
        task = BacktestService._tasks.get(run.run_id)
        if task is not None:
            await task
        report = backtest_service.get_backtest_raw(run.run_id)
        assert report is not None, "backtest record missing"
        if report["status"] != "COMPLETED":
            raise SystemExit(f"[e2e] backtest FAILED: {report.get('error')}")

        metrics = report["metrics"]
        provenance = metrics.get("data_provenance") or {}
        quality = metrics.get("data_quality_summary") or {}
        cost_model = metrics.get("cost_model") or {}
        assert provenance.get("dataset_id") == dataset_id
        assert provenance.get("interval") == "1m"
        assert provenance.get("market") == "spot"
        assert provenance.get("archive_sha256") == dataset["archive_sha256"]
        assert provenance.get("official_sha256") == dataset["official_sha256"]
        assert provenance.get("source_url") == dataset["source_url"]
        assert provenance.get("feature_version") == dataset["feature_version"]
        assert cost_model.get("fee_bps") == 10.0
        assert cost_model.get("slippage_bps") == 5.0
        assert quality.get("integrity_verified") is True
        assert int(quality.get("verified_rows") or 0) == expected_points

        summary["backtests"].append(
            {
                "symbol": symbol,
                "run_id": run.run_id,
                "status": report["status"],
                "engine": report["engine"],
                "data_mode": metrics.get("backtest_data_mode"),
                "feature_version": metrics.get("feature_version"),
                "start_ts_ms": report["start_ts_ms"],
                "end_ts_ms": report["end_ts_ms"],
                "total_return_pct": metrics.get("total_return_pct"),
                "sharpe_ratio": metrics.get("sharpe_ratio"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "trade_count": metrics.get("trade_count"),
                "fee_bps": metrics.get("fee_bps"),
                "slippage_bps": metrics.get("slippage_bps"),
                "verified_rows": quality.get("verified_rows"),
                "source_url": provenance.get("source_url"),
                "archive_sha256": provenance.get("archive_sha256"),
            }
        )
        print(
            f"[e2e] backtest {run.run_id} {report['status']} "
            f"rows={quality.get('verified_rows')} trades={metrics.get('trade_count')}"
        )

    # ---- 模拟重启：全新实例读取历史任务 / 数据集 / 行情 ----
    await reset_history_dataset_store()
    restarted_service = HistoryIngestionService()
    jobs = await restarted_service.list_jobs()
    datasets = await restarted_service.list_datasets()
    strict_store = get_strict_feature_store()
    read_back = {}
    for dataset in datasets:
        points = await strict_store.read_feature_range(
            symbol=dataset["symbol"],
            feature_name="ohlcv",
            start_time=int(dataset["first_ts_ms"]),
            end_time=int(dataset["last_ts_ms"]),
            version=dataset["feature_version"],
        )
        read_back[dataset["symbol"]] = len(points)

    summary["restart"] = {
        "jobs": len(jobs),
        "datasets": len(datasets),
        "job_statuses": sorted({str(job["status"]) for job in jobs}),
        "ohlcv_rows_read_back": read_back,
    }
    for symbol, count in read_back.items():
        assert count == expected_points, f"{symbol}: read back {count} rows"

    print("[e2e] restart check ok:", json.dumps(summary["restart"], ensure_ascii=False))

    # 释放归档下载客户端的 HTTP 会话
    try:
        from trader.services.history_data_ingestion import reset_history_archive_client

        await reset_history_archive_client()
    except Exception:  # pragma: no cover - best effort cleanup
        pass
    return summary


def main() -> int:
    args = _parse_args()
    summary = asyncio.run(_run(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("[e2e] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
