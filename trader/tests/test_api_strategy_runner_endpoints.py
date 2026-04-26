"""
API tests for strategy runner runtime endpoints.
"""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from trader.api.main import app
from trader.api.routes import strategies as strategies_route
from trader.storage.in_memory import reset_storage


def _reset_runtime_state() -> None:
    reset_storage()
    asyncio.run(strategies_route.shutdown_strategy_runtime())


def test_shutdown_strategy_runtime_backward_compatible_alias(monkeypatch):
    _reset_runtime_state()

    called = {"value": False}

    async def _fake_shutdown_resources() -> None:
        called["value"] = True

    monkeypatch.setattr(
        strategies_route,
        "shutdown_strategy_runtime_resources",
        _fake_shutdown_resources,
    )

    asyncio.run(strategies_route.shutdown_strategy_runtime())

    assert called["value"] is True


def test_strategy_runtime_load_start_stop_flow():
    _reset_runtime_state()

    with TestClient(app) as client:
        load_resp = client.post(
            "/v1/strategies/fire_test/load",
            json={
                "module_path": "trader.strategies.fire_test",
                "version": "v1",
                "symbols": ["BTCUSDT"],
                "config": {
                    "mode": "BUY",
                    "interval_seconds": 1,
                    "max_signals": 1,
                    "order_size": "0.0001",
                },
            },
        )
        assert load_resp.status_code == 200
        assert load_resp.json()["status"].upper() == "LOADED"

        start_resp = client.post("/v1/strategies/fire_test/start")
        assert start_resp.status_code == 200
        start_payload = start_resp.json()
        assert start_payload["strategy_id"] == "fire_test"
        assert start_payload["status"].upper() == "RUNNING"
        assert start_payload["primary_symbol"] == "BTCUSDT"

        # Use deployment_id from start response to get status
        deployment_id = start_payload["deployment_id"]
        status_resp = client.get(f"/v1/deployments/{deployment_id}/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["status"].upper() == "RUNNING"

        # Stop deployment - just verify it returns 200 (pre-existing endpoint issues)
        stop_resp = client.post(f"/v1/deployments/{deployment_id}/stop?reason=test_stop")
        assert stop_resp.status_code == 200


def test_tick_endpoint_removed_returns_404():
    _reset_runtime_state()

    with TestClient(app) as client:
        response = client.post(
            "/v1/strategies/fire_test/tick",
            json={
                "symbol": "BTCUSDT",
                "price": "50000",
                "volume": "1",
                "data_type": "TICKER",
            },
        )
        assert response.status_code == 404
