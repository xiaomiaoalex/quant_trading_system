from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from trader.adapters.persistence.feature_store import get_feature_store
from trader.api.main import app
from trader.storage.in_memory import get_storage


def _wait_for_backtest(client: TestClient, run_id: str) -> dict:
    payload: dict = {}
    for _ in range(60):
        response = client.get(f"/v1/backtests/{run_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"COMPLETED", "FAILED"}:
            return payload
        time.sleep(0.1)
    raise AssertionError(f"Backtest {run_id} did not finish: {payload}")


async def _seed_ohlcv_feature_store(version: str, start_ts_ms: int, points: int = 80) -> None:
    storage = get_storage()
    keys_to_delete = [
        key for key in storage.feature_values_by_key if key.startswith(f"BTCUSDT:ohlcv:{version}:")
    ]
    for key in keys_to_delete:
        del storage.feature_values_by_key[key]

    store = get_feature_store()
    store._ensure_postgres = AsyncMock(return_value=False)

    price = 100.0
    for i in range(points):
        ts_ms = start_ts_ms + i * 3_600_000
        close = price * (1.001 if i % 2 == 0 else 0.999)
        await store.write_feature(
            symbol="BTCUSDT",
            feature_name="ohlcv",
            version=version,
            ts_ms=ts_ms,
            value={
                "open": price,
                "high": max(price, close) * 1.001,
                "low": min(price, close) * 0.999,
                "close": close,
                "volume": 100 + i,
            },
            meta={"interval": "1h", "source": "test"},
        )
        price = close


def test_backtest_api_runs_vectorbt_engine_from_main_endpoint():
    with TestClient(app) as client:
        created = client.post(
            "/v1/backtests",
            json={
                "strategy_id": "ema_cross_btc",
                "version": 1,
                "engine": "vectorbt",
                "symbols": ["BTCUSDT"],
                "start_ts_ms": 1704067200000,
                "end_ts_ms": 1709251200000,
                "venue": "BINANCE",
                "requested_by": "test",
                "feature_version": "dev_smoke",
                "data_mode": "dev_smoke",
                "initial_capital": 100000,
                "fee_bps": 10,
                "slippage_bps": 5,
                "benchmark": "BTCUSDT",
            },
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]

        status = _wait_for_backtest(client, run_id)
        assert status["status"] == "COMPLETED"
        assert status["engine"] == "vectorbt"

        report = client.get(f"/v1/backtests/{run_id}/report")

    assert report.status_code == 200
    payload = report.json()
    assert payload["engine"] == "vectorbt"
    assert payload["metrics"]["backtest_engine"] == "vectorbt"
    assert payload["metrics"]["framework"] == "vectorbt"
    assert payload["metrics"]["backtest_data_mode"] == "dev_smoke"


def test_backtest_api_rejects_unknown_engine():
    with TestClient(app) as client:
        response = client.post(
            "/v1/backtests",
            json={
                "strategy_id": "ema_cross_btc",
                "version": 1,
                "engine": "unknown_engine",
                "symbols": ["BTCUSDT"],
                "start_ts_ms": 1704067200000,
                "end_ts_ms": 1709251200000,
                "venue": "BINANCE",
                "requested_by": "test",
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_backtest_api_runs_vectorbt_real_feature_store_from_feature_store():
    version = "test_real_feature_store_v1"
    start_ts_ms = 1704067200000
    await _seed_ohlcv_feature_store(version=version, start_ts_ms=start_ts_ms)

    with TestClient(app) as client:
        created = client.post(
            "/v1/backtests",
            json={
                "strategy_id": "ema_cross_btc",
                "version": 1,
                "engine": "vectorbt",
                "symbols": ["BTCUSDT"],
                "start_ts_ms": start_ts_ms,
                "end_ts_ms": start_ts_ms + 79 * 3_600_000,
                "venue": "BINANCE",
                "requested_by": "test",
                "feature_version": version,
                "data_mode": "real_feature_store",
                "initial_capital": 100000,
                "fee_bps": 10,
                "slippage_bps": 5,
                "benchmark": "BTCUSDT",
            },
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]

        status = _wait_for_backtest(client, run_id)
        assert status["status"] == "COMPLETED"

        report = client.get(f"/v1/backtests/{run_id}/report")

    assert report.status_code == 200
    payload = report.json()
    quality = payload["metrics"]["data_quality_summary"]
    assert payload["engine"] == "vectorbt"
    assert payload["metrics"]["backtest_data_mode"] == "real_feature_store"
    assert payload["metrics"]["feature_version"] == version
    assert quality["source"] == "feature_store"
    assert quality["missing_data"] is False
    assert quality["quality_score"] >= 0.99
    assert quality["total_points"] == 80


def test_backtest_api_fails_vectorbt_real_feature_store_when_data_missing():
    version = f"missing_real_feature_store_{int(time.time() * 1000)}"

    with TestClient(app) as client:
        created = client.post(
            "/v1/backtests",
            json={
                "strategy_id": "ema_cross_btc",
                "version": 1,
                "engine": "vectorbt",
                "symbols": ["BTCUSDT"],
                "start_ts_ms": 1704067200000,
                "end_ts_ms": 1704096000000,
                "venue": "BINANCE",
                "requested_by": "test",
                "feature_version": version,
                "data_mode": "real_feature_store",
                "initial_capital": 100000,
            },
        )
        assert created.status_code == 202

        status = _wait_for_backtest(client, created.json()["run_id"])

    assert status["status"] == "FAILED"
    assert "FeatureStore missing OHLCV data" in status["error"]
