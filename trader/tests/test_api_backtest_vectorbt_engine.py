from __future__ import annotations

import time

from fastapi.testclient import TestClient

from trader.api.main import app


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
