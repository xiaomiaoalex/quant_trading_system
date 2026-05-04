from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from trader.api.crypto_risk_runtime import get_crypto_risk_runtime_manager
from trader.api.main import app
from trader.core.domain.models.crypto_risk import CryptoRiskBudget
from trader.storage.in_memory import reset_storage


@pytest.fixture(autouse=True)
def reset_crypto_risk_runtime_manager() -> None:
    get_crypto_risk_runtime_manager().reset_for_tests()
    reset_storage()


def test_get_crypto_risk_runtime_status_defaults_disabled() -> None:
    client = TestClient(app)

    response = client.get("/v1/risk/crypto/runtime")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["wired"] is False
    assert payload["fail_closed"] is False
    assert payload["risk_budget"]["total_notional_cap"] == "0"


def test_patch_crypto_risk_budget_requires_wired_runtime() -> None:
    client = TestClient(app)

    response = client.patch(
        "/v1/risk/crypto/budget",
        json={"total_notional_cap": "1000", "updated_by": "operator"},
    )

    assert response.status_code == 409
    assert "not wired" in response.json()["detail"]


@pytest.mark.asyncio
async def test_patch_crypto_risk_budget_updates_runtime_manager() -> None:
    manager = get_crypto_risk_runtime_manager()
    manager.set_runtime_for_tests(
        risk_budget=CryptoRiskBudget(total_notional_cap=Decimal("10000")),
    )
    client = TestClient(app)

    response = client.patch(
        "/v1/risk/crypto/budget",
        json={
            "total_notional_cap": "25000",
            "symbol_notional_caps": {"btc/usdt": "10000"},
            "symbol_clusters": {"btc/usdt": "BTC_BETA"},
            "cluster_notional_caps": {"BTC_BETA": "15000"},
            "max_margin_ratio": "0.70",
            "updated_by": "operator",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["wired"] is True
    assert payload["updated_by"] == "operator"
    assert payload["risk_budget"]["total_notional_cap"] == "25000"
    assert payload["risk_budget"]["symbol_notional_caps"] == {"BTCUSDT": "10000"}
    assert payload["risk_budget"]["symbol_clusters"] == {"BTCUSDT": "BTC_BETA"}
    assert payload["risk_budget"]["cluster_notional_caps"] == {"BTC_BETA": "15000"}
    assert payload["risk_budget"]["max_margin_ratio"] == "0.70"
    assert payload["risk_budget"]["min_liquidation_buffer_ratio"] == "0"


@pytest.mark.asyncio
async def test_patch_crypto_risk_budget_writes_audit_event() -> None:
    manager = get_crypto_risk_runtime_manager()
    manager.set_runtime_for_tests(
        risk_budget=CryptoRiskBudget(
            total_notional_cap=Decimal("10000"),
            symbol_notional_caps={"BTCUSDT": Decimal("5000")},
        ),
    )
    client = TestClient(app)

    response = client.patch(
        "/v1/risk/crypto/budget",
        json={
            "total_notional_cap": "25000",
            "symbol_notional_caps": {"ETHUSDT": "6000"},
            "symbol_clusters": {"ETHUSDT": "ETH_BETA"},
            "cluster_notional_caps": {"ETH_BETA": "12000"},
            "updated_by": "risk_operator",
        },
    )
    assert response.status_code == 200

    audit_response = client.get("/v1/risk/crypto/budget/audit")

    assert audit_response.status_code == 200
    events = audit_response.json()
    assert len(events) == 1
    event = events[0]
    assert event["stream_key"] == "risk:crypto"
    assert event["event_type"] == "crypto_risk.budget_updated"
    assert event["trace_id"].startswith("crypto-risk-budget:")
    payload = event["payload"]
    assert payload["updated_by"] == "risk_operator"
    assert payload["previous_budget"]["total_notional_cap"] == "10000"
    assert payload["previous_budget"]["symbol_notional_caps"] == {"BTCUSDT": "5000"}
    assert payload["new_budget"]["total_notional_cap"] == "25000"
    assert payload["new_budget"]["symbol_notional_caps"] == {"ETHUSDT": "6000"}
    assert payload["new_budget"]["symbol_clusters"] == {"ETHUSDT": "ETH_BETA"}
    assert payload["new_budget"]["cluster_notional_caps"] == {"ETH_BETA": "12000"}
    assert payload["runtime_before"]["wired"] is True
    assert payload["runtime_after"]["wired"] is True


@pytest.mark.asyncio
async def test_failed_crypto_risk_budget_update_does_not_write_audit_event() -> None:
    client = TestClient(app)

    response = client.patch(
        "/v1/risk/crypto/budget",
        json={"total_notional_cap": "25000", "updated_by": "risk_operator"},
    )
    audit_response = client.get("/v1/risk/crypto/budget/audit")

    assert response.status_code == 409
    assert audit_response.status_code == 200
    assert audit_response.json() == []
