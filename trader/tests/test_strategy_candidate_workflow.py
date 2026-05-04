from __future__ import annotations

from fastapi.testclient import TestClient

from trader.api.main import app
from trader.storage.in_memory import get_storage


def test_strategy_candidate_rejects_promote_before_validation():
    with TestClient(app) as client:
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "lab_strategy",
                "name": "Lab Strategy",
                "code": "def get_plugin():\n    return None\n",
            },
        )
        assert created.status_code == 201
        candidate_id = created.json()["candidate_id"]

        promoted = client.post(
            f"/v1/strategy-candidates/{candidate_id}/promote",
            json={
                "deployment_id": "lab_strategy__btcusdt__paper__binance_demo",
                "symbols": ["BTCUSDT"],
                "account_id": "binance_demo",
                "venue": "BINANCE",
                "mode": "paper",
            },
        )

        assert promoted.status_code == 409
        assert "VALIDATION_PASSED" in promoted.json()["detail"]


def test_strategy_candidate_validation_blocks_dev_smoke_backtest():
    storage = get_storage()
    strategy = storage.create_strategy(
        {
            "strategy_id": "lab_strategy",
            "name": "Lab Strategy",
            "entrypoint": "dynamic:lab_strategy",
        }
    )
    assert strategy["strategy_id"] == "lab_strategy"

    with TestClient(app) as client:
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "lab_strategy",
                "name": "Lab Strategy",
                "code": "def get_plugin():\n    return None\n",
            },
        )
        candidate_id = created.json()["candidate_id"]
        storage.create_backtest(
            {
                "strategy_id": "lab_strategy",
                "version": 1,
                "symbols": ["BTCUSDT"],
                "start_ts_ms": 1,
                "end_ts_ms": 2,
                "venue": "BINANCE",
                "requested_by": "test",
                "metrics": {
                    "backtest_data_mode": "dev_smoke",
                    "max_drawdown_pct": 1.0,
                    "total_return": 10.0,
                    "data_quality_summary": {"quality_score": 1.0},
                },
            }
        )
        backtest_run_id = next(iter(storage.backtests))
        storage.update_strategy_candidate(
            candidate_id,
            {
                "status": "BACKTEST_PASSED",
                "backtest_run_id": backtest_run_id,
                "feature_version": "dev_smoke",
            },
        )

        validated = client.post(f"/v1/strategy-candidates/{candidate_id}/validate")

        assert validated.status_code == 200
        payload = validated.json()
        assert payload["status"] == "REJECTED"
        assert "dev_smoke_backtest_not_deployable" in payload["validation"]["failed_rules"]


def test_delete_draft_strategy_candidate_removes_it_from_research_list():
    with TestClient(app) as client:
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "delete_me",
                "name": "Delete Me",
                "code": "def get_plugin():\n    return None\n",
            },
        )
        assert created.status_code == 201
        candidate_id = created.json()["candidate_id"]

        deleted = client.delete(f"/v1/strategy-candidates/{candidate_id}")
        assert deleted.status_code == 200
        assert deleted.json()["ok"] is True

        missing = client.get(f"/v1/strategy-candidates/{candidate_id}")
        assert missing.status_code == 404
        listed = client.get("/v1/strategy-candidates")
        assert candidate_id not in {item["candidate_id"] for item in listed.json()}


def test_delete_running_strategy_candidate_is_rejected():
    storage = get_storage()
    with TestClient(app) as client:
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "running_candidate",
                "name": "Running Candidate",
                "code": "def get_plugin():\n    return None\n",
            },
        )
        candidate_id = created.json()["candidate_id"]
        storage.update_strategy_candidate(
            candidate_id,
            {
                "status": "PAPER_RUNNING",
                "deployment_id": "running_candidate__btcusdt__paper__binance_demo",
            },
        )

        deleted = client.delete(f"/v1/strategy-candidates/{candidate_id}")

        assert deleted.status_code == 409
        assert "PAPER_RUNNING" in deleted.json()["detail"]


def test_allocation_profile_and_trace_endpoints():
    with TestClient(app) as client:
        upserted = client.put(
            "/v1/allocations/deploy-1",
            json={
                "strategy_id": "lab_strategy",
                "max_notional": 1000,
                "max_symbol_exposure": 500,
                "max_portfolio_weight": 0.2,
                "min_confidence": 0.7,
                "priority": 10,
                "enabled": True,
            },
        )
        assert upserted.status_code == 200
        assert upserted.json()["remaining_notional"] == 1000

        trace = client.post(
            "/v1/allocations/deploy-1/traces",
            json={
                "strategy_id": "lab_strategy",
                "symbol": "BTCUSDT",
                "raw_requested_size": 1500,
                "risk_sized_qty": 1500,
                "allocated_qty": 1000,
                "final_order_qty": 1000,
                "allocation_decision": "clipped",
                "reject_or_clip_reason": "max_notional",
            },
        )
        assert trace.status_code == 201

        traces = client.get("/v1/allocations/deploy-1/traces")
        assert traces.status_code == 200
        assert traces.json()[0]["allocation_decision"] == "clipped"


def test_portfolio_autopilot_tick_records_pause_decision():
    with TestClient(app) as client:
        client.put(
            "/v1/allocations/deploy-1",
            json={
                "strategy_id": "lab_strategy",
                "max_notional": 1000,
                "max_symbol_exposure": 500,
                "max_portfolio_weight": 0.2,
                "priority": 10,
                "enabled": True,
            },
        )

        ticked = client.post(
            "/v1/portfolio-autopilot/tick",
            json={
                "data_stale": True,
                "portfolio_exposure": 100,
                "max_portfolio_exposure": 1000,
            },
        )

        assert ticked.status_code == 200
        decisions = ticked.json()["decisions"]
        assert decisions
        assert decisions[0]["action"] == "PAUSE"
        assert decisions[0]["deployment_id"] == "deploy-1"
