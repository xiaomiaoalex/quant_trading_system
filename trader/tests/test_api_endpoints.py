"""
Unit Tests - API Endpoints
=========================
Tests for FastAPI endpoints using TestClient.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from trader.api.main import app
from trader.adapters.persistence.risk_repository import reset_risk_event_repository
from trader.storage.in_memory import reset_storage


class TestHealthEndpoint:
    """Test health check endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        reset_storage()
        reset_risk_event_repository()

    def test_health_check(self):
        """Test health check returns 200"""
        response = self.client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "time" in data
        time_str = data["time"]
        assert "+00:00" not in time_str, f"Time should use 'Z' instead of '+00:00': {time_str}"
        assert time_str.endswith("Z"), f"Time should end with Z: {time_str}"

    def test_liveness_check(self):
        """Test liveness probe returns 200"""
        response = self.client.get("/health/live")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "time" in data

    def test_readiness_check(self):
        """Test readiness probe returns 200"""
        response = self.client.get("/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["ok", "degraded"]
        assert "time" in data
        assert "checks" in data

    def test_dependency_check(self):
        """Test dependency probe returns detailed status"""
        response = self.client.get("/health/dependency")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["ok", "degraded"]
        assert "time" in data
        assert "checks" in data
        assert "dependencies" in data
        assert "postgresql" in data["checks"]
        assert "storage" in data["checks"]

    def test_dependency_check_structure(self):
        """Test dependency check returns proper JSON structure"""
        response = self.client.get("/health/dependency")
        assert response.status_code == 200
        data = response.json()
        
        assert "postgresql" in data["dependencies"]
        assert "storage" in data["dependencies"]
        
        postgresql = data["dependencies"]["postgresql"]
        assert "status" in postgresql
        assert "message" in postgresql
        
        storage = data["dependencies"]["storage"]
        assert "status" in storage
        assert "message" in storage

    def test_dependency_check_postgresql_not_configured(self):
        """Test dependency check when PostgreSQL is not configured"""
        response = self.client.get("/health/dependency")
        assert response.status_code == 200
        data = response.json()
        
        assert "postgresql" in data["checks"]
        pg_status = data["checks"]["postgresql"]["status"]
        assert pg_status in ["not_configured", "degraded", "unhealthy"]
        
        overall_status = data["status"]
        if pg_status in ["degraded", "unhealthy"]:
            assert overall_status == "degraded"

    def test_readiness_check_storage_failure(self):
        """Test readiness check when storage throws exception"""
        from trader.api.routes import health as health_module
        
        original_check = health_module._check_storage_health
        
        def failing_check():
            from trader.api.models.schemas import ComponentHealth
            return ComponentHealth(
                status="unhealthy",
                message="Simulated storage failure"
            )
        
        health_module._check_storage_health = failing_check
        
        try:
            response = self.client.get("/health/ready")
            assert response.status_code == 200
            data = response.json()
            
            assert data["status"] == "degraded"
            assert "storage" in data["checks"]
            assert data["checks"]["storage"]["status"] == "unhealthy"
        finally:
            health_module._check_storage_health = original_check

    def test_utc_time_format(self):
        """Test UTC time format is RFC3339 compliant"""
        response = self.client.get("/health/live")
        assert response.status_code == 200
        data = response.json()
        
        assert "time" in data
        time_str = data["time"]
        
        if "+00:00" in time_str:
            assert False, f"Time should use 'Z' instead of '+00:00': {time_str}"
        
        assert time_str.endswith("Z"), f"Time should end with Z: {time_str}"


class TestStrategyEndpoints:
    """Test strategy API endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        reset_storage()

    def test_list_strategies_empty(self):
        """Test listing strategies when empty"""
        response = self.client.get("/v1/strategies/registry")
        assert response.status_code == 200
        assert response.json() == []

    def test_register_strategy(self):
        """Test registering a strategy"""
        payload = {
            "strategy_id": "strat_001",
            "name": "Mean Reversion",
            "description": "Test strategy",
            "entrypoint": "strategies.mean_reversion:Strategy"
        }
        response = self.client.post("/v1/strategies/registry", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["strategy_id"] == "strat_001"
        assert data["name"] == "Mean Reversion"

    def test_get_strategy(self):
        """Test getting a strategy"""
        # First create a strategy
        payload = {
            "strategy_id": "strat_001",
            "name": "Test",
            "entrypoint": "test:Strategy"
        }
        self.client.post("/v1/strategies/registry", json=payload)

        # Now get it
        response = self.client.get("/v1/strategies/registry/strat_001")
        assert response.status_code == 200
        data = response.json()
        assert data["strategy_id"] == "strat_001"

    def test_get_strategy_not_found(self):
        """Test getting non-existent strategy"""
        response = self.client.get("/v1/strategies/registry/nonexistent")
        assert response.status_code == 404


class TestDeploymentEndpoints:
    """Test deployment API endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        reset_storage()

    def test_list_deployments_empty(self):
        """Test listing deployments when empty"""
        response = self.client.get("/v1/deployments")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_deployment(self):
        """Test creating a deployment"""
        payload = {
            "deployment_id": "deploy_001",
            "strategy_id": "strat_001",
            "version": 1,
            "account_id": "acc_001",
            "venue": "BINANCE",
            "symbols": ["BTCUSDT"],
            "created_by": "system"
        }
        response = self.client.post("/v1/deployments", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["deployment_id"] == "deploy_001"
        assert data["status"] == "STOPPED"

    def test_start_deployment(self):
        """Test starting a deployment"""
        # First create deployment
        payload = {
            "deployment_id": "deploy_001",
            "strategy_id": "strat_001",
            "version": 1,
            "account_id": "acc_001",
            "venue": "BINANCE",
            "symbols": ["BTCUSDT"],
            "created_by": "system"
        }
        self.client.post("/v1/deployments", json=payload)

        # Now start it
        response = self.client.post("/v1/deployments/deploy_001/start")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True

    def test_stop_deployment(self):
        """Test stopping a deployment"""
        # First create and start deployment
        payload = {
            "deployment_id": "deploy_001",
            "strategy_id": "strat_001",
            "version": 1,
            "account_id": "acc_001",
            "venue": "BINANCE",
            "symbols": ["BTCUSDT"],
            "created_by": "system"
        }
        self.client.post("/v1/deployments", json=payload)
        self.client.post("/v1/deployments/deploy_001/start")

        # Now stop it
        response = self.client.post("/v1/deployments/deploy_001/stop")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True


class TestBacktestEndpoints:
    """Test backtest API endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        reset_storage()

    def test_create_backtest(self):
        """Test creating a backtest"""
        payload = {
            "strategy_id": "strat_001",
            "version": 1,
            "symbols": ["BTCUSDT"],
            "start_ts_ms": 1700000000000,
            "end_ts_ms": 1700100000000,
            "venue": "BINANCE",
            "requested_by": "user001"
        }
        response = self.client.post("/v1/backtests", json=payload)
        assert response.status_code == 202
        data = response.json()
        assert data["run_id"] is not None
        assert data["status"] == "RUNNING"


class TestRiskEndpoints:
    """Test risk API endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        reset_storage()

    def test_get_risk_limits_default(self):
        """Test getting default risk limits"""
        response = self.client.get("/v1/risk/limits")
        assert response.status_code == 200
        # Returns None when no limits are set

    def test_set_risk_limits(self):
        """Test setting risk limits"""
        payload = {
            "scope": "GLOBAL",
            "config": {"max_daily_loss": 5000},
            "created_by": "admin"
        }
        response = self.client.post("/v1/risk/limits", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == 1

    def test_ingest_risk_event_created(self):
        """Test ingesting a new risk event"""
        payload = {
            "dedup_key": "risk-key-001",
            "severity": "HIGH",
            "reason": "ENV_RISK:AdapterDegraded:binance_adapter",
            "metrics": {"private_stream_state": "DEGRADED"},
            "recommended_level": 1,
            "scope": "GLOBAL",
            "ts_ms": 1700000000000,
            "adapter_name": "binance_adapter",
            "venue": "BINANCE",
            "account_id": "acc_001",
        }
        response = self.client.post("/v1/risk/events", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["ok"] is True

    def test_ingest_risk_event_duplicate(self):
        """Test ingesting duplicate risk event by dedup_key"""
        payload = {
            "dedup_key": "risk-key-dup",
            "severity": "HIGH",
            "reason": "ENV_RISK:AdapterDegraded:binance_adapter",
            "metrics": {"private_stream_state": "DEGRADED"},
            "recommended_level": 1,
            "scope": "GLOBAL",
            "ts_ms": 1700000000000,
            "adapter_name": "binance_adapter",
            "venue": "BINANCE",
            "account_id": "acc_001",
        }
        first = self.client.post("/v1/risk/events", json=payload)
        assert first.status_code == 201

        second = self.client.post("/v1/risk/events", json=payload)
        assert second.status_code == 409
        data = second.json()
        assert data["ok"] is True

    def test_ingest_risk_event_invalid_payload(self):
        """Test ingesting risk event with invalid severity"""
        payload = {
            "dedup_key": "test-key",
            "severity": "INVALID_SEVERITY",
            "reason": "test",
            "recommended_level": 1,
            "scope": "GLOBAL",
            "ts_ms": 1700000000000,
        }
        response = self.client.post("/v1/risk/events", json=payload)
        assert response.status_code == 422

    def test_ingest_risk_event_missing_required_fields(self):
        """Test ingesting risk event with missing required fields"""
        payload = {
            "dedup_key": "test-key",
        }
        response = self.client.post("/v1/risk/events", json=payload)
        assert response.status_code == 422

    def test_risk_event_upgrade_idempotency(self):
        """Test that same dedup_key does not trigger duplicate upgrade"""
        payload = {
            "dedup_key": "idempotency-test-key",
            "severity": "HIGH",
            "reason": "Test upgrade idempotency",
            "metrics": {},
            "recommended_level": 2,
            "scope": "GLOBAL",
            "ts_ms": 1700000000000,
            "adapter_name": "test_adapter",
        }
        
        first = self.client.post("/v1/risk/events", json=payload)
        assert first.status_code == 201
        
        killswitch_response = self.client.get("/v1/killswitch?scope=GLOBAL")
        assert killswitch_response.status_code == 200
        assert killswitch_response.json()["level"] == 2
        
        second = self.client.post("/v1/risk/events", json=payload)
        assert second.status_code == 409
        
        killswitch_after = self.client.get("/v1/killswitch?scope=GLOBAL")
        assert killswitch_after.json()["level"] == 2

    def test_risk_event_no_downgrade(self):
        """Test that lower recommended_level does not cause downgrade"""
        high_payload = {
            "dedup_key": "high-level-key",
            "severity": "CRITICAL",
            "reason": "Test no downgrade",
            "metrics": {},
            "recommended_level": 3,
            "scope": "GLOBAL",
            "ts_ms": 1700000000000,
            "adapter_name": "test_adapter",
        }
        
        response = self.client.post("/v1/risk/events", json=high_payload)
        assert response.status_code == 201
        
        killswitch_after_high = self.client.get("/v1/killswitch?scope=GLOBAL")
        assert killswitch_after_high.json()["level"] == 3
        
        low_payload = {
            "dedup_key": "low-level-key",
            "severity": "LOW",
            "reason": "Should not downgrade",
            "metrics": {},
            "recommended_level": 1,
            "scope": "GLOBAL",
            "ts_ms": 1700000001000,
            "adapter_name": "test_adapter",
        }
        
        response = self.client.post("/v1/risk/events", json=low_payload)
        assert response.status_code == 201
        
        killswitch_after_low = self.client.get("/v1/killswitch?scope=GLOBAL")
        assert killswitch_after_low.json()["level"] == 3

    def test_recover_pending_effects(self):
        """Test recovery endpoint replays PENDING effects with correct scope/level"""
        import asyncio
        from trader.services.risk import RiskService
        from trader.storage.in_memory import reset_storage
        
        reset_storage()
        reset_risk_event_repository()
        service = RiskService()
        
        upgrade_key = "test_recovery_key"
        asyncio.run(service.try_record_upgrade_with_effect(
            upgrade_key, "GLOBAL", 2, "Test recovery", "dedup_recovery"
        ))
        
        state_before = self.client.get("/v1/killswitch?scope=GLOBAL")
        level_before = state_before.json()["level"]
        
        recover_response = self.client.post("/v1/risk/recover")
        assert recover_response.status_code == 200
        result = recover_response.json()
        assert result["ok"] is True
        
        state_after = self.client.get("/v1/killswitch?scope=GLOBAL")
        assert state_after.json()["level"] == 2

    def test_recover_failed_effects(self):
        """Test recovery endpoint replays FAILED effects with correct scope/level"""
        from trader.services.risk import RiskService
        from trader.storage.in_memory import reset_storage
        
        reset_storage()
        reset_risk_event_repository()
        service = RiskService()
        
        upgrade_key = "test_failed_recovery_key"
        asyncio.run(service.try_record_upgrade_with_effect(
            upgrade_key, "GLOBAL", 3, "Test failed recovery", "dedup_failed"
        ))
        asyncio.run(service.mark_effect_failed(upgrade_key, "Simulated failure"))
        
        recover_response = self.client.post("/v1/risk/recover")
        assert recover_response.status_code == 200
        result = recover_response.json()
        assert result["ok"] is True
        
        state_after = self.client.get("/v1/killswitch?scope=GLOBAL")
        assert state_after.json()["level"] == 3

    def test_ingest_risk_event_rolls_back_killswitch_when_effect_mark_fails(self, monkeypatch):
        """Test side-effect compensation restores KillSwitch when effect status write fails"""
        from trader.services.risk import RiskService

        async def _failing_mark_effect_applied(self, upgrade_key: str) -> None:
            raise RuntimeError("mark applied failed")

        monkeypatch.setattr(RiskService, "mark_effect_applied", _failing_mark_effect_applied)

        payload = {
            "dedup_key": "risk-key-compensation",
            "severity": "HIGH",
            "reason": "Compensation test",
            "metrics": {},
            "recommended_level": 2,
            "scope": "GLOBAL",
            "ts_ms": 1700000000000,
            "adapter_name": "test_adapter",
        }

        response = self.client.post("/v1/risk/events", json=payload)
        assert response.status_code == 500
        assert response.json()["ok"] is False

        killswitch_state = self.client.get("/v1/killswitch?scope=GLOBAL").json()
        assert killswitch_state["level"] == 0

        pending = asyncio.run(RiskService().get_pending_effects())
        assert len(pending) == 1
        assert pending[0]["status"] == "FAILED"

    def test_recover_pending_effects_marks_applied_when_killswitch_already_at_target(self):
        """Test recovery is idempotent if KillSwitch is already at target level"""
        from trader.services.killswitch import KillSwitchService, KillSwitchSetRequest
        from trader.services.risk import RiskService
        from trader.storage.in_memory import reset_storage

        reset_storage()
        reset_risk_event_repository()
        service = RiskService()
        killswitch = KillSwitchService()

        upgrade_key = "test_recover_idempotent_key"
        asyncio.run(service.try_record_upgrade_with_effect(
            upgrade_key, "GLOBAL", 2, "Test idempotent recovery", "dedup_recover_idempotent"
        ))
        killswitch.set_state(KillSwitchSetRequest(
            scope="GLOBAL", level=2, reason="already_applied", updated_by="test"
        ))

        recover_response = self.client.post("/v1/risk/recover")
        assert recover_response.status_code == 200
        result = recover_response.json()
        assert result["ok"] is True
        assert "1 recovered" in result["message"]

        pending_after = asyncio.run(service.get_pending_effects())
        assert pending_after == []


class TestOrderEndpoints:
    """Test order API endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        reset_storage()

    def test_list_orders_empty(self):
        """Test listing orders when empty"""
        response = self.client.get("/v1/orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_executions_empty(self):
        """Test listing executions when empty"""
        response = self.client.get("/v1/executions")
        assert response.status_code == 200
        assert response.json() == []


class TestPortfolioEndpoints:
    """Test portfolio API endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        reset_storage()

    def test_list_positions_empty(self):
        """Test listing positions when empty"""
        response = self.client.get("/v1/portfolio/positions")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_pnl(self):
        """Test getting PnL"""
        response = self.client.get("/v1/portfolio/pnl")
        assert response.status_code == 200
        data = response.json()
        assert "realized_pnl" in data
        assert "unrealized_pnl" in data


class TestEventEndpoints:
    """Test event API endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        reset_storage()

    def test_list_events_empty(self):
        """Test listing events when empty"""
        response = self.client.get("/v1/events")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_snapshot_not_found(self):
        """Test getting snapshot when not exists"""
        response = self.client.get("/v1/snapshots/latest?stream_key=nonexistent")
        assert response.status_code == 200
        # Returns None when no snapshot exists

    def test_trigger_replay(self):
        """Test triggering replay"""
        payload = {
            "stream_key": "orders",
            "requested_by": "admin"
        }
        response = self.client.post("/v1/replay", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True


class TestKillSwitchEndpoints:
    """Test kill switch API endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        reset_storage()

    def test_get_kill_switch_default(self):
        """Test getting default kill switch state"""
        response = self.client.get("/v1/killswitch")
        assert response.status_code == 200
        data = response.json()
        assert data["level"] == 0
        assert data["scope"] == "GLOBAL"

    def test_set_kill_switch(self):
        """Test setting kill switch"""
        payload = {
            "scope": "GLOBAL",
            "level": 2,
            "reason": "Emergency",
            "updated_by": "admin"
        }
        response = self.client.post("/v1/killswitch", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["level"] == 2


class TestBrokerEndpoints:
    """Test broker API endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        reset_storage()

    def test_list_brokers_empty(self):
        """Test listing brokers when empty"""
        response = self.client.get("/v1/brokers")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_broker_status_not_found(self):
        """Test getting status for non-existent broker"""
        response = self.client.get("/v1/brokers/acc_001/status")
        assert response.status_code == 404
