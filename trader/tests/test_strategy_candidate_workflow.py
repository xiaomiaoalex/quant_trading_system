from __future__ import annotations

from fastapi.testclient import TestClient

from trader.api.main import app
from trader.services.strategy_candidate import StrategyCandidateService
from trader.storage.in_memory import get_storage


def test_strategy_candidate_rejects_promote_before_validation():
    """未通过验证的候选无法通过新原子接口 promote；旧 /promote 路由已废弃为 410。"""
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

        # 旧路由已废弃，返回 410 Gone
        old_route = client.post(
            f"/v1/strategy-candidates/{candidate_id}/promote",
            json={
                "deployment_id": "lab_strategy__btcusdt__paper__binance_demo",
                "symbols": ["BTCUSDT"],
                "account_id": "binance_demo",
                "venue": "BINANCE",
                "mode": "paper",
            },
        )
        assert old_route.status_code == 410

        # 新原子接口对 DRAFT 状态返回 409 INVALID_STATE
        new_route = client.post(
            f"/v1/strategy-candidates/{candidate_id}/promote-paper",
        )
        assert new_route.status_code == 409
        detail = new_route.json().get("detail", "")
        if isinstance(detail, dict):
            assert detail.get("error_code") == "INVALID_STATE"
            assert detail.get("required_state") == "VALIDATION_PASSED"
        else:
            assert "VALIDATION_PASSED" in detail


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


def test_mark_backtest_passed_transitions_from_backtest_running():
    storage = get_storage()
    with TestClient(app) as client:
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "transition_test",
                "name": "Transition Test",
                "code": "def get_plugin():\n    return None\n",
            },
        )
        assert created.status_code == 201
        candidate_id = created.json()["candidate_id"]

        storage.update_strategy_candidate(
            candidate_id,
            {"status": "BACKTEST_RUNNING", "backtest_run_id": "bt-123"},
        )

        service = StrategyCandidateService()
        result = service.mark_backtest_passed(candidate_id)
        assert result.status == "BACKTEST_PASSED"


def test_mark_backtest_failed_transitions_to_rejected():
    storage = get_storage()
    with TestClient(app) as client:
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "fail_test",
                "name": "Fail Test",
                "code": "def get_plugin():\n    return None\n",
            },
        )
        assert created.status_code == 201
        candidate_id = created.json()["candidate_id"]

        storage.update_strategy_candidate(
            candidate_id,
            {"status": "BACKTEST_RUNNING", "backtest_run_id": "bt-456"},
        )

        service = StrategyCandidateService()
        result = service.mark_backtest_failed(candidate_id, reason="simulated_failure")
        assert result.status == "REJECTED"
        events = result.events
        assert any(e["to_status"] == "REJECTED" and e["reason"] == "simulated_failure" for e in events)


def test_validate_from_draft_is_illegal_transition():
    storage = get_storage()
    with TestClient(app) as client:
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "illegal_test",
                "name": "Illegal Test",
                "code": "def get_plugin():\n    return None\n",
            },
        )
        assert created.status_code == 201
        candidate_id = created.json()["candidate_id"]

        validated = client.post(f"/v1/strategy-candidates/{candidate_id}/validate")
        assert validated.status_code == 409
        assert "BACKTEST_PASSED" in validated.json()["detail"]


def test_validate_from_backtest_passed_with_real_feature_store_succeeds():
    storage = get_storage()
    with TestClient(app) as client:
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "pass_test",
                "name": "Pass Test",
                "code": "def get_plugin():\n    return None\n",
            },
        )
        assert created.status_code == 201
        candidate_id = created.json()["candidate_id"]

        backtest = storage.create_backtest(
            {
                "strategy_id": "pass_test",
                "version": 1,
                "symbols": ["BTCUSDT"],
                "start_ts_ms": 1,
                "end_ts_ms": 2,
                "venue": "BINANCE",
                "requested_by": "test",
                "metrics": {
                    "backtest_data_mode": "real_feature_store",
                    "max_drawdown_pct": 10.0,
                    "total_return": 15.0,
                    "data_quality_summary": {"quality_score": 0.9},
                },
            }
        )
        backtest_run_id = backtest["run_id"]
        storage.update_backtest(
            backtest_run_id,
            {"status": "COMPLETED", "finished_at": "2024-01-01T00:00:00Z"},
        )
        storage.update_strategy_candidate(
            candidate_id,
            {
                "status": "BACKTEST_PASSED",
                "backtest_run_id": backtest_run_id,
                "feature_version": "v1.2.0",
            },
        )

        validated = client.post(f"/v1/strategy-candidates/{candidate_id}/validate")
        assert validated.status_code == 200
        payload = validated.json()
        assert payload["status"] == "VALIDATION_PASSED"
        assert payload["validation"]["passed"] is True


def test_validate_uses_risk_adjusted_metrics_when_present():
    """当 metrics 中存在 risk_adjusted_metrics 时，validate 应使用其中的百分比单位值"""
    storage = get_storage()
    with TestClient(app) as client:
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "risk_adj_test",
                "name": "Risk Adjusted Test",
                "code": "def get_plugin():\n    return None\n",
            },
        )
        assert created.status_code == 201
        candidate_id = created.json()["candidate_id"]

        # raw metrics 看起来很好，但 risk_adjusted_metrics 回撤超限
        backtest = storage.create_backtest(
            {
                "strategy_id": "risk_adj_test",
                "version": 1,
                "symbols": ["BTCUSDT"],
                "start_ts_ms": 1,
                "end_ts_ms": 2,
                "venue": "BINANCE",
                "requested_by": "test",
                "metrics": {
                    "backtest_data_mode": "real_feature_store",
                    "max_drawdown_pct": 10.0,
                    "total_return": 15.0,
                    "data_quality_summary": {"quality_score": 0.9},
                    "risk_adjusted_metrics": {
                        "max_drawdown": 30.0,
                        "max_drawdown_pct": 30.0,
                        "total_return": 5.0,
                    },
                    "risk_mode": "risk_adjusted",
                },
            }
        )
        backtest_run_id = backtest["run_id"]
        storage.update_backtest(
            backtest_run_id,
            {"status": "COMPLETED", "finished_at": "2024-01-01T00:00:00Z"},
        )
        storage.update_strategy_candidate(
            candidate_id,
            {
                "status": "BACKTEST_PASSED",
                "backtest_run_id": backtest_run_id,
                "feature_version": "v1.2.0",
            },
        )

        validated = client.post(f"/v1/strategy-candidates/{candidate_id}/validate")
        assert validated.status_code == 200
        payload = validated.json()
        assert payload["status"] == "REJECTED"
        assert "max_drawdown_exceeded" in payload["validation"]["failed_rules"]


def test_validate_blocks_raw_only_backtest():
    """raw_only 回测不应通过验证"""
    storage = get_storage()
    with TestClient(app) as client:
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "raw_only_test",
                "name": "Raw Only Test",
                "code": "def get_plugin():\n    return None\n",
            },
        )
        assert created.status_code == 201
        candidate_id = created.json()["candidate_id"]

        backtest = storage.create_backtest(
            {
                "strategy_id": "raw_only_test",
                "version": 1,
                "symbols": ["BTCUSDT"],
                "start_ts_ms": 1,
                "end_ts_ms": 2,
                "venue": "BINANCE",
                "requested_by": "test",
                "metrics": {
                    "backtest_data_mode": "real_feature_store",
                    "max_drawdown_pct": 10.0,
                    "total_return": 15.0,
                    "data_quality_summary": {"quality_score": 0.9},
                    "risk_mode": "raw_only",
                },
            }
        )
        backtest_run_id = backtest["run_id"]
        storage.update_backtest(
            backtest_run_id,
            {"status": "COMPLETED", "finished_at": "2024-01-01T00:00:00Z"},
        )
        storage.update_strategy_candidate(
            candidate_id,
            {
                "status": "BACKTEST_PASSED",
                "backtest_run_id": backtest_run_id,
                "feature_version": "v1.2.0",
            },
        )

        validated = client.post(f"/v1/strategy-candidates/{candidate_id}/validate")
        assert validated.status_code == 200
        payload = validated.json()
        assert payload["status"] == "REJECTED"
        assert "raw_only_backtest_not_deployable" in payload["validation"]["failed_rules"]


def test_backtest_completion_auto_promotes_candidate():
    storage = get_storage()
    with TestClient(app) as client:
        code = '''
from typing import Optional
from trader.core.application.strategy_protocol import (
    MarketData, StrategyResourceLimits, ValidationResult, RiskLevel
)
from trader.core.domain.models.signal import Signal

class MinimalStrategy:
    def __init__(self):
        self.name = "MinimalStrategy"
        self.version = "1.0.0"
        self.risk_level = RiskLevel.LOW
        self.resource_limits = StrategyResourceLimits()
        self.strategy_id = ""
        self.deployment_id = ""
        self.symbols = []

    async def initialize(self, config: dict) -> None:
        pass

    async def on_market_data(self, data: MarketData) -> Optional[Signal]:
        return None

    async def on_fill(self, fill: dict) -> None:
        pass

    async def on_cancel(self, cancel: dict) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def validate(self) -> ValidationResult:
        return ValidationResult.valid()

    def update_config(self, config: dict) -> ValidationResult:
        return ValidationResult.valid()

def get_plugin():
    return MinimalStrategy()
'''
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "auto_promote",
                "name": "Auto Promote",
                "code": code,
            },
        )
        assert created.status_code == 201
        candidate_id = created.json()["candidate_id"]

        debugged = client.post(
            f"/v1/strategy-candidates/{candidate_id}/debug",
            json={"code": code, "config": {}},
        )
        assert debugged.status_code == 200

        backtest = storage.create_backtest(
            {
                "strategy_id": "auto_promote",
                "version": 1,
                "symbols": ["BTCUSDT"],
                "start_ts_ms": 1700000000000,
                "end_ts_ms": 1700000100000,
                "venue": "BINANCE",
                "requested_by": "test",
                "data_mode": "dev_smoke",
                "candidate_id": candidate_id,
            }
        )
        backtest_run_id = backtest["run_id"]
        storage.update_strategy_candidate(
            candidate_id,
            {"status": "BACKTEST_RUNNING", "backtest_run_id": backtest_run_id},
        )

        from trader.services.deployment import BacktestService
        from trader.api.models.schemas import BacktestRequest

        service = BacktestService()
        request = BacktestRequest(
            strategy_id="auto_promote",
            version=1,
            symbols=["BTCUSDT"],
            start_ts_ms=1700000000000,
            end_ts_ms=1700000100000,
            venue="BINANCE",
            requested_by="test",
            data_mode="real_feature_store",
            risk_mode="risk_adjusted",
            candidate_id=candidate_id,
        )
        backtest = service.create_backtest(request)

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        task = loop.create_task(service._run_backtest(backtest.run_id, request))
        loop.run_until_complete(task)
        loop.close()

        updated_candidate = client.get(f"/v1/strategy-candidates/{candidate_id}")
        assert updated_candidate.status_code == 200
        payload = updated_candidate.json()
        assert payload["status"] == "BACKTEST_PASSED"
