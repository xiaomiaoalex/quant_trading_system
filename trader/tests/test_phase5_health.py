"""Phase-5 health semantics: live, ready and trading are distinct states."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from trader.api.models.schemas import ComponentHealth
from trader.api.routes import health as health_routes
from trader.api.trading_app import create_trading_app


def _client(provider=None) -> TestClient:
    return TestClient(create_trading_app(readiness_provider=provider))


def test_live_is_always_200_even_when_trading_is_unconfigured():
    with _client() as client:
        assert client.get("/health/live").status_code == 200
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        trading = client.get("/health/trading")
        assert trading.status_code == 503
        assert trading.json()["reasons"] == ["TRADING_RUNTIME_NOT_CONFIGURED"]


def test_trading_503_carries_reasons_and_ready_200_when_provider_ready():
    class Blocked:
        async def check(self):
            return {
                "ready": False,
                "reasons": ["EXECUTION_INSTANCE_UNAVAILABLE", "DR_EVIDENCE_MISSING"],
                "account_id": "acct",
                "db_available": True,
            }

    with _client(Blocked()) as client:
        response = client.get("/health/trading")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "blocked"
        assert body["ready"] is False
        assert "EXECUTION_INSTANCE_UNAVAILABLE" in body["reasons"]
        assert "DR_EVIDENCE_MISSING" in body["reasons"]
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200

    class Ready:
        async def check(self):
            return {"ready": True, "reasons": [], "account_id": "acct"}

    with _client(Ready()) as client:
        response = client.get("/health/trading")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["ready"] is True


def test_provider_exception_is_a_trading_503_not_a_live_failure():
    class Broken:
        async def check(self):
            raise RuntimeError("storage detail that must not crash the process")

    with _client(Broken()) as client:
        trading = client.get("/health/trading")
        assert trading.status_code == 503
        assert trading.json()["ready"] is False
        assert trading.json()["reasons"]
        assert client.get("/health/live").status_code == 200


def test_ready_reports_degraded_without_masking_trading(monkeypatch):
    monkeypatch.setattr(
        health_routes,
        "_check_storage_health",
        lambda: ComponentHealth(status="unhealthy", message="storage down"),
    )
    with _client() as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["status"] == "degraded"
        assert ready.json()["reasons"] == ["STORAGE_UNAVAILABLE"]
        assert client.get("/health/live").status_code == 200
        # A degraded API dependency never becomes a trading-ready signal.
        assert client.get("/health/trading").status_code == 503
