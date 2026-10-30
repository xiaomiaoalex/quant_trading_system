"""Entrypoint smoke test: process liveness and fail-closed trading readiness."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_entrypoint_starts_api_and_refuses_trading_without_credentials(monkeypatch):
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_SECRET_KEY", raising=False)
    from trader.runtime.trading_main import app

    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        trading = client.get("/health/trading")
        assert trading.status_code == 503
        assert trading.json()["ready"] is False
        # Trading endpoints remain fail-closed and do not send anything.
        command = client.post(
            "/v1/trading/orders",
            json={
                "command_id": "entrypoint-smoke",
                "account_id": "acct",
                "symbol": "BTCUSDT",
                "base_asset": "BTC",
                "quote_asset": "USDT",
                "side": "BUY",
                "qty": "1",
                "price": "100",
            },
        )
        assert command.status_code in {401, 503}
