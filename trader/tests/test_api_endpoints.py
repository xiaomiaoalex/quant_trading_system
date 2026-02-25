"""
Unit Tests - API Endpoints
=========================
Tests for FastAPI endpoints using TestClient.
"""
import pytest
from fastapi.testclient import TestClient

from trader.api.main import app
from trader.storage.in_memory import reset_storage


class TestHealthEndpoint:
    """Test health check endpoint"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        reset_storage()

    def test_health_check(self):
        """Test health check returns 200"""
        response = self.client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "time" in data


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
