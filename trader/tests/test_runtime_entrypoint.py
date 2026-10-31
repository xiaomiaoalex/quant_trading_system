"""Entrypoint smoke test: process liveness and fail-closed trading readiness."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_entrypoint_starts_api_and_refuses_trading_without_credentials(monkeypatch):
    # Import first: the module loads .env, then the test overrides the values so
    # the smoke test never touches the real sandbox or the development database.
    from trader.runtime.trading_main import app

    monkeypatch.setenv("BINANCE_API_KEY", "")
    monkeypatch.setenv("BINANCE_SECRET_KEY", "")
    monkeypatch.setenv(
        "POSTGRES_CONNECTION_STRING",
        "postgresql://trader:trader_pwd@127.0.0.1:1/trading",
    )

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
