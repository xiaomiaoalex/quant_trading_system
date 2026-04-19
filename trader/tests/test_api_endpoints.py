"""
Unit Tests - API Endpoints
=========================
Tests for FastAPI endpoints using TestClient.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from trader.api.main import app
from trader.adapters.persistence.postgres import PostgreSQLStorage, close_pool, is_postgres_available
from trader.adapters.persistence.risk_repository import reset_risk_event_repository
from trader.storage.in_memory import get_storage, reset_storage


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


async def _seed_audit_entries() -> list[str]:
    from insight.chat_interface import create_chat_interface
    from trader.api.routes.audit import clear_audit_entries
    from trader.api.routes.chat import set_chat_interface

    clear_audit_entries()
    interface = create_chat_interface()
    set_chat_interface(interface)

    audit_log = interface.get_audit_log()
    assert audit_log is not None

    first = await audit_log.log_generation(
        prompt="Generate momentum strategy",
        generated_code="class S: pass",
        llm_backend="mock",
        llm_model="gpt-4",
        strategy_name="Audit Alpha",
        strategy_id="audit_alpha",
        metadata={"tag": "alpha"},
    )
    await audit_log.submit_for_approval(first.entry_id)

    second = await audit_log.log_generation(
        prompt="Generate mean reversion strategy",
        generated_code="class M: pass",
        llm_backend="mock",
        llm_model="gpt-4",
        strategy_name="Audit Beta",
        strategy_id="audit_beta",
        metadata={"tag": "beta"},
    )
    return [first.entry_id, second.entry_id]


class TestHealthEndpoint:
    """Test health check endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        reset_storage()
        reset_risk_event_repository()
        asyncio.run(_clear_postgres_risk_state())

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

    def test_dependency_check_postgresql_status(self):
        """Test dependency check for PostgreSQL status"""
        response = self.client.get("/health/dependency")
        assert response.status_code == 200
        data = response.json()
        
        assert "postgresql" in data["checks"]
        pg_status = data["checks"]["postgresql"]["status"]
        # PostgreSQL health check only returns: healthy, not_configured, unhealthy
        # (degraded is not returned by _check_postgresql_health)
        assert pg_status in ["healthy", "not_configured", "unhealthy"]
        
        overall_status = data["status"]
        if pg_status == "healthy":
            assert overall_status == "ok"
        elif pg_status == "unhealthy":
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

    def test_strategy_code_debug_and_load_flow(self):
        """Test code create/debug/load/start full flow."""
        code = """
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from trader.core.application.strategy_protocol import MarketData, RiskLevel, StrategyPlugin, StrategyResourceLimits, ValidationResult
from trader.core.domain.models.signal import Signal, SignalType

@dataclass(slots=True)
class TestCodeStrategy:
    strategy_id: str = "code_flow"
    name: str = "Code Flow"
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    resource_limits: StrategyResourceLimits = field(default_factory=StrategyResourceLimits)
    _last: Decimal | None = None

    async def initialize(self, config: dict[str, Any]) -> None:
        self._last = None

    async def on_market_data(self, data: MarketData):
        if self._last is None:
            self._last = data.price
            return None
        signal = Signal(
            strategy_name=self.strategy_id,
            signal_type=SignalType.BUY if data.price >= self._last else SignalType.SELL,
            symbol=data.symbol,
            price=data.price,
            quantity=Decimal("1"),
            reason="code_flow",
        )
        self._last = data.price
        return signal

    async def on_fill(self, order_id: str, symbol: str, side: str, quantity: float, price: float) -> None:
        return None

    async def on_cancel(self, order_id: str, reason: str) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def update_config(self, config: dict[str, Any]) -> ValidationResult:
        return ValidationResult.valid()

    def validate(self) -> ValidationResult:
        return ValidationResult.valid()

_plugin = TestCodeStrategy()
def get_plugin() -> StrategyPlugin:
    return _plugin
"""
        create_resp = self.client.post(
            "/v1/strategies/code",
            json={
                "strategy_id": "code_flow",
                "name": "Code Flow",
                "description": "test code flow",
                "code": code,
                "created_by": "tester",
                "register_if_missing": True,
            },
        )
        assert create_resp.status_code == 201
        created = create_resp.json()
        assert created["code_version"] == 1

        latest_resp = self.client.get("/v1/strategies/code_flow/code/latest")
        assert latest_resp.status_code == 200
        assert latest_resp.json()["checksum"] == created["checksum"]

        debug_resp = self.client.post(
            "/v1/strategies/code/debug",
            json={"strategy_id": "code_flow_debug", "code": code, "config": {}},
        )
        assert debug_resp.status_code == 200
        debug_data = debug_resp.json()
        assert debug_data["ok"] is True
        assert debug_data["syntax_ok"] is True
        assert debug_data["protocol_ok"] is True

        load_resp = self.client.post(
            "/v1/strategies/code_flow/load",
            json={"version": "v1", "code_version": 1, "config": {}},
        )
        assert load_resp.status_code == 200
        assert load_resp.json()["status"] == "LOADED"

        start_resp = self.client.post("/v1/strategies/code_flow/start")
        assert start_resp.status_code == 200
        assert start_resp.json()["status"] == "RUNNING"

    def test_load_dynamic_strategy_missing_code_returns_404(self):
        """Dynamic strategy without saved code should return 404/400 instead of 500."""
        register_resp = self.client.post(
            "/v1/strategies/registry",
            json={
                "strategy_id": "dynamic_no_code",
                "name": "Dynamic No Code",
                "entrypoint": "dynamic:dynamic_no_code",
            },
        )
        assert register_resp.status_code == 201

        load_resp = self.client.post(
            "/v1/strategies/dynamic_no_code/load",
            json={"code_version": 3, "version": "v1", "config": {}},
        )
        assert load_resp.status_code == 404


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
        self.client.post(
            "/v1/strategies/registry",
            json={
                "strategy_id": "ema_cross_btc",
                "name": "EMA Cross BTC",
                "entrypoint": "trader.strategies.ema_cross_btc",
            },
        )
        payload = {
            "strategy_id": "ema_cross_btc",
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
        assert data["status"] in ("PENDING", "RUNNING")

    def test_async_backtest_with_code_version_persists_report(self):
        """End-to-end: code register -> async backtest -> report persisted."""
        code = """
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from trader.core.application.strategy_protocol import MarketData, RiskLevel, StrategyPlugin, StrategyResourceLimits, ValidationResult
from trader.core.domain.models.signal import Signal, SignalType

@dataclass(slots=True)
class BtFlowStrategy:
    strategy_id: str = "bt_flow"
    name: str = "Backtest Flow"
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    resource_limits: StrategyResourceLimits = field(default_factory=StrategyResourceLimits)
    _last: Decimal | None = None
    _toggle: bool = False

    async def initialize(self, config: dict[str, Any]) -> None:
        self._last = None
        self._toggle = False

    async def on_market_data(self, data: MarketData):
        if self._last is None:
            self._last = data.price
            return None
        self._toggle = not self._toggle
        signal = Signal(
            strategy_name=self.strategy_id,
            signal_type=SignalType.BUY if self._toggle else SignalType.SELL,
            symbol=data.symbol,
            price=data.price,
            quantity=Decimal("1"),
            reason="bt_flow",
        )
        self._last = data.price
        return signal

    async def on_fill(self, order_id: str, symbol: str, side: str, quantity: float, price: float) -> None:
        return None

    async def on_cancel(self, order_id: str, reason: str) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def update_config(self, config: dict[str, Any]) -> ValidationResult:
        return ValidationResult.valid()

    def validate(self) -> ValidationResult:
        return ValidationResult.valid()

_plugin = BtFlowStrategy()
def get_plugin() -> StrategyPlugin:
    return _plugin
"""
        register_resp = self.client.post(
            "/v1/strategies/code",
            json={
                "strategy_id": "bt_flow",
                "name": "Backtest Flow",
                "description": "e2e test",
                "code": code,
                "created_by": "tester",
                "register_if_missing": True,
            },
        )
        assert register_resp.status_code == 201
        code_version = register_resp.json()["code_version"]
        assert code_version == 1

        create_resp = self.client.post(
            "/v1/backtests",
            json={
                "strategy_id": "bt_flow",
                "version": 1,
                "strategy_code_version": code_version,
                "symbols": ["BTCUSDT"],
                "start_ts_ms": 1700000000000,
                "end_ts_ms": 1700200000000,
                "venue": "BINANCE",
                "requested_by": "tester",
            },
        )
        assert create_resp.status_code == 202
        run_id = create_resp.json()["run_id"]

        final_status = None
        for _ in range(80):
            status_resp = self.client.get(f"/v1/backtests/{run_id}")
            assert status_resp.status_code == 200
            status_data = status_resp.json()
            final_status = status_data["status"]
            if final_status in ("COMPLETED", "FAILED"):
                break
            time.sleep(0.05)

        assert final_status == "COMPLETED"
        assert status_data["progress"] == 1.0
        assert status_data["artifact_ref"]
        assert status_data["artifact_ref"].startswith("backtest_report:")

        report_resp = self.client.get(f"/v1/backtests/{run_id}/report")
        assert report_resp.status_code == 200
        report = report_resp.json()
        assert report["status"] == "COMPLETED"
        assert isinstance(report["returns"], dict)
        assert isinstance(report["risk"], dict)
        assert isinstance(report["equity_curve"], list)
        assert isinstance(report["trades"], list)
        assert report["metrics"] is not None


class TestAuditEndpoints:
    """Test audit query API endpoints (Task 9.6)."""

    def setup_method(self):
        self.client = TestClient(app)
        self.entry_ids = asyncio.run(_seed_audit_entries())

    def test_list_audit_entries(self):
        response = self.client.get("/api/audit/entries")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
        assert all("entry_id" in item for item in data)

    def test_list_audit_entries_with_filters(self):
        response = self.client.get(
            "/api/audit/entries",
            params={"strategy_id": "audit_alpha", "status": "pending", "event_type": "submitted"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["strategy_id"] == "audit_alpha"
        assert data[0]["status"] == "pending"
        assert data[0]["event_type"] == "submitted"

    def test_get_audit_entry(self):
        entry_id = self.entry_ids[0]
        response = self.client.get(f"/api/audit/entries/{entry_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["entry_id"] == entry_id
        assert data["strategy_id"] == "audit_alpha"

    def test_get_audit_entry_not_found(self):
        response = self.client.get("/api/audit/entries/not-exists")
        assert response.status_code == 404

    def test_list_audit_entries_invalid_since(self):
        response = self.client.get("/api/audit/entries", params={"since": "not-a-time"})
        assert response.status_code == 400
        assert "Invalid since format" in response.json()["detail"]

    def test_list_audit_entries_time_range_and_pagination(self):
        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        until = (now + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        response = self.client.get(
            "/api/audit/entries",
            params={"since": since, "until": until, "limit": 1, "offset": 0},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1


class TestRiskEndpoints:
    """Test risk API endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        reset_storage()
        reset_risk_event_repository()
        asyncio.run(_clear_postgres_risk_state())

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


class TestTimeWindowConfigEndpoints:
    """Test time window config API endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        from trader.api.routes import risk
        # Reset the time window policy singleton to avoid state pollution
        risk._time_window_policy = None

    def teardown_method(self):
        """Cleanup after each test"""
        from trader.api.routes import risk
        risk._time_window_policy = None

    def test_get_time_window_config_default(self):
        """Test getting default time window config"""
        response = self.client.get("/v1/risk/time-window/config")
        assert response.status_code == 200
        data = response.json()
        # Default config should have 3 slots
        assert len(data["slots"]) == 3
        assert data["default_coefficient"] == 1.0
        # Verify slot periods
        periods = [s["period"] for s in data["slots"]]
        assert "RESTRICTED" in periods
        assert "OFF_PEAK" in periods
        assert "PRIME" in periods

    def test_update_time_window_config(self):
        """Test updating time window config"""
        new_config = {
            "slots": [
                {
                    "period": "PRIME",
                    "start_hour": 9,
                    "start_minute": 0,
                    "end_hour": 17,
                    "end_minute": 0,
                    "position_coefficient": 1.0,
                    "allow_new_position": True,
                },
                {
                    "period": "OFF_PEAK",
                    "start_hour": 17,
                    "start_minute": 0,
                    "end_hour": 22,
                    "end_minute": 0,
                    "position_coefficient": 0.5,
                    "allow_new_position": True,
                },
            ],
            "default_coefficient": 0.8,
            "updated_by": "test_user",
        }
        response = self.client.put("/v1/risk/time-window/config", json=new_config)
        assert response.status_code == 200
        data = response.json()
        assert len(data["slots"]) == 2
        assert data["default_coefficient"] == 0.8

    def test_update_time_window_config_invalid_period(self):
        """Test updating time window config with invalid period returns 422"""
        invalid_config = {
            "slots": [
                {
                    "period": "INVALID_PERIOD",
                    "start_hour": 9,
                    "start_minute": 0,
                    "end_hour": 17,
                    "end_minute": 0,
                    "position_coefficient": 1.0,
                    "allow_new_position": True,
                },
            ],
            "default_coefficient": 1.0,
            "updated_by": "test_user",
        }
        response = self.client.put("/v1/risk/time-window/config", json=invalid_config)
        assert response.status_code == 422
        error_data = response.json()
        # Pydantic validates the Literal field and returns a validation error
        assert "Input should be" in str(error_data["detail"])

    def test_update_time_window_config_preserves_order(self):
        """Test that config update returns slots in priority order (RESTRICTED first)"""
        new_config = {
            "slots": [
                {
                    "period": "PRIME",
                    "start_hour": 8,
                    "start_minute": 0,
                    "end_hour": 16,
                    "end_minute": 0,
                    "position_coefficient": 1.0,
                    "allow_new_position": True,
                },
                {
                    "period": "RESTRICTED",
                    "start_hour": 22,
                    "start_minute": 0,
                    "end_hour": 8,
                    "end_minute": 0,
                    "position_coefficient": 0.0,
                    "allow_new_position": False,
                },
            ],
            "default_coefficient": 1.0,
            "updated_by": "test_user",
        }
        response = self.client.put("/v1/risk/time-window/config", json=new_config)
        assert response.status_code == 200
        data = response.json()
        # RESTRICTED should be first due to priority sorting
        assert data["slots"][0]["period"] == "RESTRICTED"
        assert data["slots"][1]["period"] == "PRIME"

    def test_update_time_window_config_evaluation(self):
        """Test that updated config correctly evaluates time periods via public API"""
        # Setup config with known time windows:
        # - RESTRICTED: 22:00-08:00 (overnight)
        # - PRIME: 09:00-16:00
        # - OFF_PEAK: 16:00-22:00
        new_config = {
            "slots": [
                {
                    "period": "PRIME",
                    "start_hour": 9,
                    "start_minute": 0,
                    "end_hour": 16,
                    "end_minute": 0,
                    "position_coefficient": 1.0,
                    "allow_new_position": True,
                },
                {
                    "period": "OFF_PEAK",
                    "start_hour": 16,
                    "start_minute": 0,
                    "end_hour": 22,
                    "end_minute": 0,
                    "position_coefficient": 0.5,
                    "allow_new_position": True,
                },
                {
                    "period": "RESTRICTED",
                    "start_hour": 22,
                    "start_minute": 0,
                    "end_hour": 8,
                    "end_minute": 0,
                    "position_coefficient": 0.0,
                    "allow_new_position": False,
                },
            ],
            "default_coefficient": 1.0,
            "updated_by": "test_user",
        }
        response = self.client.put("/v1/risk/time-window/config", json=new_config)
        assert response.status_code == 200

        # Test PRIME time (10:30) via public API
        resp_prime = self.client.get("/v1/risk/time-window/evaluate", params={"hour": 10, "minute": 30})
        assert resp_prime.status_code == 200
        data_prime = resp_prime.json()
        assert data_prime["period"] == "PRIME"
        assert data_prime["position_coefficient"] == 1.0
        assert data_prime["allow_new_position"] is True

        # Test OFF_PEAK time (18:00) via public API
        resp_offpeak = self.client.get("/v1/risk/time-window/evaluate", params={"hour": 18, "minute": 0})
        assert resp_offpeak.status_code == 200
        data_offpeak = resp_offpeak.json()
        assert data_offpeak["period"] == "OFF_PEAK"
        assert data_offpeak["position_coefficient"] == 0.5
        assert data_offpeak["allow_new_position"] is True

        # Test RESTRICTED time (23:00 - within overnight window) via public API
        resp_restricted = self.client.get("/v1/risk/time-window/evaluate", params={"hour": 23, "minute": 0})
        assert resp_restricted.status_code == 200
        data_restricted = resp_restricted.json()
        assert data_restricted["period"] == "RESTRICTED"
        assert data_restricted["position_coefficient"] == 0.0
        assert data_restricted["allow_new_position"] is False

        # Test RESTRICTED time (03:00 - also within overnight window 22:00-08:00) via public API
        resp_restricted2 = self.client.get("/v1/risk/time-window/evaluate", params={"hour": 3, "minute": 0})
        assert resp_restricted2.status_code == 200
        assert resp_restricted2.json()["period"] == "RESTRICTED"

        # Verify slots are stored in priority order via GET config endpoint (RESTRICTED first)
        resp_config = self.client.get("/v1/risk/time-window/config")
        assert resp_config.status_code == 200
        config_data = resp_config.json()
        assert len(config_data["slots"]) == 3
        assert config_data["slots"][0]["period"] == "RESTRICTED"
        assert config_data["slots"][1]["period"] == "OFF_PEAK"
        assert config_data["slots"][2]["period"] == "PRIME"

    def test_evaluate_time_window_invalid_hour(self):
        """Test that invalid hour values return 422"""
        # Test hour=25 (out of range)
        response = self.client.get("/v1/risk/time-window/evaluate", params={"hour": 25, "minute": 0})
        assert response.status_code == 422
        
        # Test hour=-1 (negative)
        response = self.client.get("/v1/risk/time-window/evaluate", params={"hour": -1, "minute": 0})
        assert response.status_code == 422

    def test_evaluate_time_window_invalid_minute(self):
        """Test that invalid minute values return 422"""
        # Test minute=60 (out of range)
        response = self.client.get("/v1/risk/time-window/evaluate", params={"hour": 10, "minute": 60})
        assert response.status_code == 422
        
        # Test minute=-1 (negative)
        response = self.client.get("/v1/risk/time-window/evaluate", params={"hour": 10, "minute": -1})
        assert response.status_code == 422


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

    @staticmethod
    def _seed_replay_events() -> None:
        storage = get_storage()
        storage.append_event(
            {
                "stream_key": "orders",
                "event_type": "ORDER_CREATED",
                "trace_id": "trace-1",
                "ts_ms": int(time.time() * 1000) - 1000,
                "payload": {
                    "client_order_id": "ord-1",
                },
            }
        )
        storage.append_event(
            {
                "stream_key": "orders",
                "event_type": "ORDER_SUBMITTED",
                "trace_id": "trace-2",
                "ts_ms": int(time.time() * 1000),
                "payload": {
                    "client_order_id": "ord-1",
                },
            }
        )

    def setup_method(self):
        """Setup for each test"""
        from trader.api.routes.events import clear_replay_jobs

        self.client = TestClient(app)
        reset_storage()
        clear_replay_jobs()

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
        """Test triggering replay (Task 9.7 - returns ReplayJob)"""
        self._seed_replay_events()
        payload = {
            "stream_key": "orders",
            "requested_by": "admin"
        }
        response = self.client.post("/v1/replay", json=payload)
        assert response.status_code == 200
        data = response.json()
        # 新 API 返回 ReplayJob 格式
        assert "job_id" in data
        assert data["status"] in ("PENDING", "RUNNING", "COMPLETED", "FAILED")
        assert data["stream_key"] == "orders"
        assert data["requested_by"] == "admin"

    def test_get_replay_status(self):
        self._seed_replay_events()
        payload = {
            "stream_key": "orders",
            "requested_by": "admin",
        }
        response = self.client.post("/v1/replay", json=payload)
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        status_response = self.client.get(f"/v1/replay/{job_id}")
        assert status_response.status_code == 200
        data = status_response.json()
        assert data["job_id"] == job_id
        assert data["stream_key"] == "orders"
        assert data["status"] in ("PENDING", "RUNNING", "COMPLETED")
        if data["status"] == "COMPLETED":
            assert data["result_summary"]["events_total"] >= 2

    def test_get_replay_status_not_found(self):
        response = self.client.get("/v1/replay/not-found")
        assert response.status_code == 404

    def test_list_replay_jobs(self):
        self._seed_replay_events()
        payload = {"stream_key": "orders", "requested_by": "admin"}
        first = self.client.post("/v1/replay", json=payload)
        assert first.status_code == 200
        second = self.client.post("/v1/replay", json={"stream_key": "orders", "requested_by": "alice"})
        assert second.status_code == 200

        list_all = self.client.get("/v1/replay")
        assert list_all.status_code == 200
        all_jobs = list_all.json()
        assert len(all_jobs) >= 2

        list_admin = self.client.get("/v1/replay", params={"requested_by": "admin"})
        assert list_admin.status_code == 200
        admin_jobs = list_admin.json()
        assert len(admin_jobs) >= 1
        assert all(item["requested_by"] == "admin" for item in admin_jobs)


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
