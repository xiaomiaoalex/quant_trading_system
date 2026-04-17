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


def test_tick_strategy_once_returns_signal_and_order_when_oms_callback_runs(monkeypatch):
    _reset_runtime_state()

    async def _fake_submit_live_order(strategy_id, signal):
        result = {
            "client_order_id": "mock_order_001",
            "broker_order_id": "mock_broker_001",
            "symbol": signal.symbol,
            "side": signal.get_order_side().value,
            "status": "SUBMITTED",
            "filled_quantity": "0",
            "avg_price": "0",
        }
        strategies_route._last_order_results[strategy_id] = result
        return result

    monkeypatch.setattr(strategies_route, "_submit_live_order", _fake_submit_live_order)
    monkeypatch.setattr(strategies_route, "_is_live_order_strategy_allowed", lambda _: False)

    with TestClient(app) as client:
        load_response = client.post(
            "/v1/strategies/fire_test/load",
            json={
                "module_path": "trader.strategies.fire_test",
                "version": "v1",
                "config": {
                    "mode": "BUY",
                    "interval_seconds": 1,
                    "max_signals": 1,
                    "order_size": "0.0001",
                },
            },
        )
        assert load_response.status_code == 200

        start_response = client.post("/v1/strategies/fire_test/start")
        assert start_response.status_code == 200

        tick_response = client.post(
            "/v1/strategies/fire_test/tick",
            json={
                "symbol": "BTCUSDT",
                "price": "50000",
                "volume": "1",
                "data_type": "TICKER",
            },
        )
        assert tick_response.status_code == 200
        payload = tick_response.json()
        assert payload["strategy_id"] == "fire_test"
        assert payload["signal_generated"] is True
        assert payload["signal_type"] == "BUY"
        assert payload["order_submitted"] is True
        assert payload["order_result"]["client_order_id"] == "mock_order_001"


def test_tick_strategy_once_rejects_live_trading_without_credentials(monkeypatch):
    _reset_runtime_state()
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_SECRET_KEY", raising=False)
    monkeypatch.setenv("LIVE_ORDER_STRATEGIES", "fire_test")

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
        assert response.status_code == 400
        assert "BINANCE_API_KEY" in response.json()["detail"]

