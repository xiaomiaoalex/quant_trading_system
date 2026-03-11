"""
PostgreSQL-backed risk idempotency contract tests.

These tests cover the Sprint 4 contract gate scenarios using the public API
plus restart simulation via storage/repository resets.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from trader.api.main import app
from trader.api.models.schemas import KillSwitchSetRequest
from trader.api.routes import risk as risk_route_module
from trader.adapters.persistence.postgres import (
    PostgreSQLStorage,
    close_pool,
    is_postgres_available,
)
from trader.adapters.persistence.risk_repository import reset_risk_event_repository
from trader.services.killswitch import KillSwitchService
from trader.services.risk import RiskService
from trader.storage.in_memory import reset_storage


skip_if_no_postgres = pytest.mark.skipif(
    not is_postgres_available(),
    reason="PostgreSQL not available. Set POSTGRES_CONNECTION_STRING or POSTGRES_HOST/POSTGRES_DB/POSTGRES_USER",
)


async def _clear_postgres_risk_state() -> None:
    if not is_postgres_available():
        return
    storage = PostgreSQLStorage()
    await storage.connect()
    try:
        await storage.clear()
    finally:
        await storage.disconnect()
        await close_pool()


def _risk_payload(dedup_key: str, recommended_level: int = 2) -> dict:
    return {
        "dedup_key": dedup_key,
        "severity": "HIGH",
        "reason": f"contract:{dedup_key}",
        "metrics": {"source": "contract-test"},
        "recommended_level": recommended_level,
        "scope": "GLOBAL",
        "ts_ms": 1700000000000,
        "adapter_name": "contract_test_adapter",
        "venue": "BINANCE",
        "account_id": "acc_contract",
    }


@skip_if_no_postgres
class TestRiskIdempotencyPersistence:
    def setup_method(self) -> None:
        reset_storage()
        reset_risk_event_repository()
        asyncio.run(_clear_postgres_risk_state())
        self.client = TestClient(app)

    def teardown_method(self) -> None:
        self.client.close()
        reset_storage()
        reset_risk_event_repository()
        asyncio.run(_clear_postgres_risk_state())

    def _restart_control_plane(self) -> None:
        self.client.close()
        reset_storage()
        reset_risk_event_repository()
        self.client = TestClient(app)

    def test_first_post_returns_201_for_new_dedup_key(self) -> None:
        response = self.client.post("/v1/risk/events", json=_risk_payload("contract-first-001"))

        assert response.status_code == 201
        assert response.json() == {"ok": True, "message": "risk event accepted"}

    def test_duplicate_dedup_key_returns_409_after_restart(self) -> None:
        payload = _risk_payload("contract-restart-dedup-001")

        first = self.client.post("/v1/risk/events", json=payload)
        assert first.status_code == 201

        self._restart_control_plane()

        second = self.client.post("/v1/risk/events", json=payload)
        assert second.status_code == 409
        assert second.json() == {"ok": True, "message": "risk event duplicate"}

    def test_duplicate_upgrade_key_does_not_reapply_effect_after_restart(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _risk_payload("contract-restart-upgrade-001")

        first = self.client.post("/v1/risk/events", json=payload)
        assert first.status_code == 201

        effect_calls = 0
        original_apply = risk_route_module._apply_killswitch_effect

        async def _count_calls(*args, **kwargs):
            nonlocal effect_calls
            effect_calls += 1
            return await original_apply(*args, **kwargs)

        monkeypatch.setattr(risk_route_module, "_apply_killswitch_effect", _count_calls)

        self._restart_control_plane()

        second = self.client.post("/v1/risk/events", json=payload)
        assert second.status_code == 409
        assert effect_calls == 0

    def test_step6_pending_effect_is_recovered_after_restart(self) -> None:
        payload = _risk_payload("contract-step6-001")
        upgrade_key = f"upgrade:{payload['scope']}:{payload['recommended_level']}:{payload['dedup_key']}"

        event_id, created, is_first_upgrade, is_first_effect = asyncio.run(
            RiskService().ingest_event_with_upgrade(
                payload,
                upgrade_key,
                payload["recommended_level"],
            )
        )
        assert event_id is not None
        assert created is True
        assert is_first_upgrade is True
        assert is_first_effect is True

        self._restart_control_plane()

        recover = self.client.post("/v1/risk/recover")
        assert recover.status_code == 200
        assert recover.json()["ok"] is True
        assert "1 recovered" in recover.json()["message"]

        killswitch_state = self.client.get("/v1/killswitch?scope=GLOBAL")
        assert killswitch_state.status_code == 200
        assert killswitch_state.json()["level"] == payload["recommended_level"]

        pending_after = asyncio.run(RiskService().get_pending_effects())
        assert pending_after == []

    def test_step8_failed_effect_recovers_idempotently_when_state_already_applied(self) -> None:
        service = RiskService()
        killswitch = KillSwitchService()
        upgrade_key = "upgrade:GLOBAL:3:contract-step8-001"

        is_first_upgrade, is_first_effect = asyncio.run(
            service.try_record_upgrade_with_effect(
                upgrade_key,
                "GLOBAL",
                3,
                "contract-step8-001",
                "contract-step8-001",
            )
        )
        assert is_first_upgrade is True
        assert is_first_effect is True

        killswitch.set_state(
            KillSwitchSetRequest(
                scope="GLOBAL",
                level=3,
                reason="already_applied",
                updated_by="contract_test",
            )
        )
        asyncio.run(service.mark_effect_failed(upgrade_key, "effect status write failed"))

        recover = self.client.post("/v1/risk/recover")
        assert recover.status_code == 200
        assert recover.json()["ok"] is True
        assert "1 recovered" in recover.json()["message"]

        killswitch_state = self.client.get("/v1/killswitch?scope=GLOBAL")
        assert killswitch_state.status_code == 200
        assert killswitch_state.json()["level"] == 3

        pending_after = asyncio.run(service.get_pending_effects())
        assert pending_after == []
