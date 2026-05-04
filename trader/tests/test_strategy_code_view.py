from __future__ import annotations

from fastapi.testclient import TestClient

from trader.api.main import app


def test_strategy_code_view_returns_module_source_for_builtin_strategy():
    with TestClient(app) as client:
        response = client.get("/v1/strategies/ema_cross_btc/code/view")

    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy_id"] == "ema_cross_btc"
    assert payload["source_type"] == "module_entrypoint"
    assert payload["module_path"] == "trader.strategies.ema_cross_btc"
    assert "class EmaCrossBtcStrategy" in payload["code"]
    assert "def get_plugin()" in payload["code"]


def test_strategy_code_view_prefers_saved_code_version():
    with TestClient(app) as client:
        created = client.post(
            "/v1/strategies/code",
            json={
                "strategy_id": "code_view_dynamic",
                "name": "Code View Dynamic",
                "code": "def get_plugin():\n    return None\n",
                "register_if_missing": True,
                "created_by": "test",
            },
        )
        assert created.status_code == 201

        response = client.get("/v1/strategies/code_view_dynamic/code/view")

    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy_id"] == "code_view_dynamic"
    assert payload["source_type"] == "saved_code"
    assert payload["code_version"] == 1
    assert payload["created_by"] == "test"
    assert "def get_plugin()" in payload["code"]
