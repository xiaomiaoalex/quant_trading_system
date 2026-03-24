"""
Unit Tests - API Models
========================
Tests for Pydantic schemas and data validation.
"""
import pytest
from datetime import datetime
from decimal import Decimal

from trader.api.models.schemas import (
    Strategy,
    StrategyRegisterRequest,
    StrategyVersion,
    VersionedConfig,
    Deployment,
    DeploymentCreateRequest,
    BacktestRequest,
    BacktestRun,
    OrderView,
    ExecutionView,
    PositionView,
    PnlView,
    EventEnvelope,
    SnapshotEnvelope,
    ReplayRequest,
    KillSwitchState,
    KillSwitchSetRequest,
    BrokerAccount,
    BrokerStatus,
    HealthResponse,
    ActionResult,
)


class TestStrategyModels:
    """Test strategy-related models"""

    def test_strategy_register_request(self):
        """Test StrategyRegisterRequest creation"""
        req = StrategyRegisterRequest(
            strategy_id="strat_001",
            name="Mean Reversion",
            description="Mean reversion strategy",
            entrypoint="strategies.mean_reversion:Strategy",
            language="python"
        )
        assert req.strategy_id == "strat_001"
        assert req.name == "Mean Reversion"
        assert req.language == "python"

    def test_strategy_model(self):
        """Test Strategy model"""
        strategy = Strategy(
            strategy_id="strat_001",
            name="Mean Reversion",
            entrypoint="strategies.mean_reversion:Strategy",
            created_at="2026-02-25T12:00:00Z",
            updated_at="2026-02-25T12:00:00Z"
        )
        assert strategy.strategy_id == "strat_001"
        assert strategy.created_at is not None

    def test_strategy_version(self):
        """Test StrategyVersion model"""
        version = StrategyVersion(
            strategy_id="strat_001",
            version=1,
            code_ref="git:abc123",
            param_schema={"param1": {"type": "number"}},
            created_by="system"
        )
        assert version.version == 1
        assert version.code_ref == "git:abc123"

    def test_versioned_config(self):
        """Test VersionedConfig model"""
        config = VersionedConfig(
            scope="GLOBAL",
            version=1,
            config={"max_position_size": 1000},
            created_by="admin"
        )
        assert config.scope == "GLOBAL"
        assert config.version == 1
        assert config.config["max_position_size"] == 1000


class TestDeploymentModels:
    """Test deployment-related models"""

    def test_deployment_create_request(self):
        """Test DeploymentCreateRequest creation"""
        req = DeploymentCreateRequest(
            deployment_id="deploy_001",
            strategy_id="strat_001",
            version=1,
            account_id="acc_001",
            venue="BINANCE",
            symbols=["BTCUSDT", "ETHUSDT"],
            created_by="system"
        )
        assert req.deployment_id == "deploy_001"
        assert req.venue == "BINANCE"
        assert len(req.symbols) == 2

    def test_deployment_model(self):
        """Test Deployment model"""
        deployment = Deployment(
            deployment_id="deploy_001",
            strategy_id="strat_001",
            version=1,
            account_id="acc_001",
            venue="BINANCE",
            symbols=["BTCUSDT"],
            status="STOPPED"
        )
        assert deployment.status == "STOPPED"
        assert deployment.params_version is None


class TestBacktestModels:
    """Test backtest-related models"""

    def test_backtest_request(self):
        """Test BacktestRequest creation"""
        req = BacktestRequest(
            strategy_id="strat_001",
            version=1,
            symbols=["BTCUSDT"],
            start_ts_ms=1700000000000,
            end_ts_ms=1700100000000,
            venue="BINANCE",
            requested_by="user001"
        )
        assert req.strategy_id == "strat_001"
        assert req.start_ts_ms < req.end_ts_ms

    def test_backtest_run(self):
        """Test BacktestRun model"""
        run = BacktestRun(
            run_id="run_001",
            status="RUNNING",
            strategy_id="strat_001",
            version=1,
            symbols=["BTCUSDT"],
            start_ts_ms=1700000000000,
            end_ts_ms=1700100000000
        )
        assert run.status == "RUNNING"
        assert run.metrics is None


class TestOrderModels:
    """Test order-related models"""

    def test_order_view(self):
        """Test OrderView model"""
        order = OrderView(
            cl_ord_id="ord_001",
            trace_id="trace_001",
            account_id="acc_001",
            strategy_id="strat_001",
            venue="BINANCE",
            instrument="BTCUSDT",
            side="BUY",
            order_type="LIMIT",
            qty="1.0",
            limit_price="50000.0",
            tif="GTC",
            status="NEW"
        )
        assert order.side == "BUY"
        assert order.order_type == "LIMIT"

    def test_execution_view(self):
        """Test ExecutionView model"""
        exec_view = ExecutionView(
            cl_ord_id="ord_001",
            exec_id="exec_001",
            ts_ms=1700000000000,
            fill_qty="1.0",
            fill_price="50000.0",
            fee="10.0",
            fee_currency="USDT"
        )
        assert exec_view.fill_qty == "1.0"
        assert exec_view.fee == "10.0"


class TestPortfolioModels:
    """Test portfolio-related models"""

    def test_position_view(self):
        """Test PositionView model"""
        position = PositionView(
            account_id="acc_001",
            venue="BINANCE",
            instrument="BTCUSDT",
            qty="1.0",
            avg_cost="49000.0",
            mark_price="50000.0",
            unrealized_pnl="1000.0",
            realized_pnl="0.0"
        )
        assert position.unrealized_pnl == "1000.0"

    def test_pnl_view(self):
        """Test PnlView model"""
        pnl = PnlView(
            account_id="acc_001",
            venue="BINANCE",
            realized_pnl="500.0",
            unrealized_pnl="1000.0",
            total_pnl="1500.0"
        )
        assert pnl.total_pnl == "1500.0"


class TestEventModels:
    """Test event-related models"""

    def test_event_envelope(self):
        """Test EventEnvelope model"""
        event = EventEnvelope(
            stream_key="orders",
            event_type="ORDER_CREATED",
            schema_version=1,
            trace_id="trace_001",
            ts_ms=1700000000000,
            payload={"order_id": "ord_001"}
        )
        assert event.event_type == "ORDER_CREATED"
        assert event.payload["order_id"] == "ord_001"

    def test_snapshot_envelope(self):
        """Test SnapshotEnvelope model"""
        snapshot = SnapshotEnvelope(
            stream_key="positions",
            snapshot_type="PositionSnapshot",
            ts_ms=1700000000000,
            payload={"BTCUSDT": {"qty": "1.0"}}
        )
        assert snapshot.snapshot_type == "PositionSnapshot"

    def test_replay_request(self):
        """Test ReplayRequest model"""
        request = ReplayRequest(
            stream_key="orders",
            requested_by="admin"
        )
        assert request.stream_key == "orders"
        assert request.from_ts_ms is None


class TestKillSwitchModels:
    """Test kill switch models"""

    def test_kill_switch_state(self):
        """Test KillSwitchState model"""
        state = KillSwitchState(
            scope="GLOBAL",
            level=2,
            reason="Emergency stop",
            updated_by="system"
        )
        assert state.level == 2
        assert state.scope == "GLOBAL"

    def test_kill_switch_set_request(self):
        """Test KillSwitchSetRequest model"""
        request = KillSwitchSetRequest(
            scope="GLOBAL",
            level=1,
            reason="Testing",
            updated_by="admin"
        )
        assert request.level == 1


class TestBrokerModels:
    """Test broker-related models"""

    def test_broker_account(self):
        """Test BrokerAccount model"""
        broker = BrokerAccount(
            account_id="acc_001",
            venue="BINANCE",
            broker_type="BINANCE",
            status="READY",
            capabilities={"supports_margin": True}
        )
        assert broker.status == "READY"

    def test_broker_status(self):
        """Test BrokerStatus model"""
        status = BrokerStatus(
            account_id="acc_001",
            connected=True,
            last_heartbeat_ts_ms=1700000000000
        )
        assert status.connected is True


class TestCommonModels:
    """Test common models"""

    def test_health_response(self):
        """Test HealthResponse model"""
        health = HealthResponse()
        assert health.status == "ok"
        assert health.time is not None

    def test_action_result(self):
        """Test ActionResult model"""
        result = ActionResult(ok=True, message="Success")
        assert result.ok is True
        assert result.message == "Success"

        result_fail = ActionResult(ok=False)
        assert result_fail.ok is False
