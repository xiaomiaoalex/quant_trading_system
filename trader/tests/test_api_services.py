"""
Unit Tests - Services Layer
==========================
Tests for business logic services.
"""
import asyncio

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from trader.adapters.persistence.postgres import PostgreSQLStorage, close_pool, is_postgres_available
from trader.storage.in_memory import InMemoryStorage, reset_storage
from trader.services import (
    StrategyService,
    DeploymentService,
    BacktestService,
    RiskService,
    OrderService,
    PortfolioService,
    EventService,
    KillSwitchService,
    BrokerService,
)
from trader.api.models.schemas import (
    StrategyRegisterRequest,
    StrategyVersionCreateRequest,
    VersionedConfigUpsertRequest,
    DeploymentCreateRequest,
    BacktestRequest,
    OrderView,
    ExecutionView,
    PositionView,
    ReplayRequest,
    KillSwitchSetRequest,
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


class TestStrategyService:
    """Test StrategyService"""

    def setup_method(self):
        """Setup for each test"""
        self.storage = reset_storage()
        self.service = StrategyService(self.storage)

    def test_register_strategy(self):
        """Test registering a strategy"""
        request = StrategyRegisterRequest(
            strategy_id="strat_001",
            name="Mean Reversion",
            description="Test strategy",
            entrypoint="strategies.mean_reversion:Strategy"
        )
        strategy = self.service.register_strategy(request)
        assert strategy.strategy_id == "strat_001"
        assert strategy.name == "Mean Reversion"

    def test_get_strategy(self):
        """Test getting a strategy"""
        request = StrategyRegisterRequest(
            strategy_id="strat_001",
            name="Test",
            entrypoint="test:Strategy"
        )
        self.service.register_strategy(request)

        strategy = self.service.get_strategy("strat_001")
        assert strategy is not None
        assert strategy.strategy_id == "strat_001"

    def test_list_strategies(self):
        """Test listing strategies"""
        request1 = StrategyRegisterRequest(strategy_id="strat_001", name="Test1", entrypoint="test1:Strategy")
        request2 = StrategyRegisterRequest(strategy_id="strat_002", name="Test2", entrypoint="test2:Strategy")
        self.service.register_strategy(request1)
        self.service.register_strategy(request2)

        strategies = self.service.list_strategies()
        assert len(strategies) == 2

    def test_create_version(self):
        """Test creating strategy version"""
        request = StrategyRegisterRequest(strategy_id="strat_001", name="Test", entrypoint="test:Strategy")
        self.service.register_strategy(request)

        version_request = StrategyVersionCreateRequest(
            version=1,
            code_ref="git:abc123",
            param_schema={"param1": {"type": "number"}}
        )
        version = self.service.create_version("strat_001", version_request)
        assert version.version == 1
        assert version.code_ref == "git:abc123"

    def test_list_versions(self):
        """Test listing strategy versions"""
        request = StrategyRegisterRequest(strategy_id="strat_001", name="Test", entrypoint="test:Strategy")
        self.service.register_strategy(request)

        version_request = StrategyVersionCreateRequest(version=1, code_ref="git:v1", param_schema={})
        self.service.create_version("strat_001", version_request)

        versions = self.service.list_versions("strat_001")
        assert len(versions) == 1

    def test_create_params(self):
        """Test creating strategy params"""
        request = StrategyRegisterRequest(strategy_id="strat_001", name="Test", entrypoint="test:Strategy")
        self.service.register_strategy(request)

        params_request = VersionedConfigUpsertRequest(
            scope="strat_001",
            config={"max_position": 100},
            created_by="admin"
        )
        params = self.service.create_params("strat_001", params_request)
        assert params.version == 1
        assert params.config["max_position"] == 100


class TestDeploymentService:
    """Test DeploymentService"""

    def setup_method(self):
        """Setup for each test"""
        self.storage = reset_storage()
        self.service = DeploymentService(self.storage)

    def test_create_deployment(self):
        """Test creating a deployment"""
        request = DeploymentCreateRequest(
            deployment_id="deploy_001",
            strategy_id="strat_001",
            version=1,
            account_id="acc_001",
            venue="BINANCE",
            symbols=["BTCUSDT"],
            created_by="system"
        )
        deployment = self.service.create_deployment(request)
        assert deployment.deployment_id == "deploy_001"
        assert deployment.status == "STOPPED"

    def test_list_deployments(self):
        """Test listing deployments"""
        request = DeploymentCreateRequest(
            deployment_id="deploy_001",
            strategy_id="strat_001",
            version=1,
            account_id="acc_001",
            venue="BINANCE",
            symbols=["BTCUSDT"],
            created_by="system"
        )
        self.service.create_deployment(request)

        deployments = self.service.list_deployments()
        assert len(deployments) == 1

    def test_start_deployment(self):
        """Test starting a deployment"""
        request = DeploymentCreateRequest(
            deployment_id="deploy_001",
            strategy_id="strat_001",
            version=1,
            account_id="acc_001",
            venue="BINANCE",
            symbols=["BTCUSDT"],
            created_by="system"
        )
        self.service.create_deployment(request)

        result = self.service.start_deployment("deploy_001")
        assert result.ok is True

        deployment = self.service.get_deployment("deploy_001")
        assert deployment.status == "RUNNING"

    def test_stop_deployment(self):
        """Test stopping a deployment"""
        request = DeploymentCreateRequest(
            deployment_id="deploy_001",
            strategy_id="strat_001",
            version=1,
            account_id="acc_001",
            venue="BINANCE",
            symbols=["BTCUSDT"],
            created_by="system"
        )
        self.service.create_deployment(request)
        self.service.start_deployment("deploy_001")

        result = self.service.stop_deployment("deploy_001")
        assert result.ok is True

        deployment = self.service.get_deployment("deploy_001")
        assert deployment.status == "STOPPED"


class TestBacktestService:
    """Test BacktestService"""

    def setup_method(self):
        """Setup for each test"""
        self.storage = reset_storage()
        self.service = BacktestService(self.storage)

    def test_create_backtest(self):
        """Test creating a backtest"""
        request = BacktestRequest(
            strategy_id="strat_001",
            version=1,
            symbols=["BTCUSDT"],
            start_ts_ms=1700000000000,
            end_ts_ms=1700100000000,
            venue="BINANCE",
            requested_by="user001"
        )
        backtest = self.service.create_backtest(request)
        assert backtest.run_id is not None
        assert backtest.status == "RUNNING"

    def test_get_backtest(self):
        """Test getting a backtest"""
        request = BacktestRequest(
            strategy_id="strat_001",
            version=1,
            symbols=["BTCUSDT"],
            start_ts_ms=1700000000000,
            end_ts_ms=1700100000000,
            venue="BINANCE",
            requested_by="user001"
        )
        created = self.service.create_backtest(request)

        backtest = self.service.get_backtest(created.run_id)
        assert backtest.run_id == created.run_id


class TestRiskService:
    """Test RiskService"""

    def setup_method(self):
        """Setup for each test"""
        from trader.adapters.persistence.risk_repository import reset_risk_event_repository
        reset_risk_event_repository()
        self.storage = reset_storage()
        asyncio.run(_clear_postgres_risk_state())
        self.service = RiskService(self.storage)

    def test_set_limits(self):
        """Test setting risk limits"""
        request = VersionedConfigUpsertRequest(
            scope="GLOBAL",
            config={"max_daily_loss": 5000},
            created_by="admin"
        )
        limits = self.service.set_limits(request)
        assert limits.version == 1
        assert limits.config["max_daily_loss"] == 5000

    def test_get_limits(self):
        """Test getting risk limits"""
        request = VersionedConfigUpsertRequest(
            scope="GLOBAL",
            config={"max_position": 1000},
            created_by="admin"
        )
        self.service.set_limits(request)

        limits = self.service.get_limits("GLOBAL")
        assert limits is not None
        assert limits.version == 1

    @pytest.mark.asyncio
    async def test_try_record_upgrade_first_write(self):
        """Test try_record_upgrade returns True for first write"""
        upgrade_key = "upgrade:test:1:dedup_001"
        upgrade_data = {
            "scope": "TEST",
            "level": 1,
            "reason": "Test upgrade",
            "dedup_key": "dedup_001",
        }
        
        result = await self.service.try_record_upgrade(upgrade_key, upgrade_data)
        assert result is True
        
        record = await self.service.get_upgrade_record(upgrade_key)
        assert record is not None
        assert record["scope"] == "TEST"
        assert record["level"] == 1

    @pytest.mark.asyncio
    async def test_try_record_upgrade_duplicate(self):
        """Test try_record_upgrade returns False for duplicate"""
        upgrade_key = "upgrade:test:1:dedup_002"
        upgrade_data = {
            "scope": "TEST",
            "level": 1,
            "reason": "Test upgrade",
            "dedup_key": "dedup_002",
        }
        
        result1 = await self.service.try_record_upgrade(upgrade_key, upgrade_data)
        assert result1 is True
        
        result2 = await self.service.try_record_upgrade(upgrade_key, upgrade_data)
        assert result2 is False

    @pytest.mark.asyncio
    async def test_concurrent_upgrade_idempotency(self):
        """Test concurrent upgrades only trigger once"""
        from trader.services.killswitch import KillSwitchService
        
        upgrade_key = "upgrade:test:2:dedup_003"
        upgrade_data = {
            "scope": "TEST",
            "level": 2,
            "reason": "Concurrent test",
            "dedup_key": "dedup_003",
        }
        
        killswitch_service = KillSwitchService()
        killswitch_service.set_state = AsyncMock()
        
        async def try_upgrade_and_set():
            is_first = await self.service.try_record_upgrade(upgrade_key, upgrade_data)
            if is_first:
                await killswitch_service.set_state(KillSwitchSetRequest(
                    scope="TEST",
                    level=2,
                    reason="test",
                    updated_by="test"
                ))
        
        import asyncio
        await asyncio.gather(
            try_upgrade_and_set(),
            try_upgrade_and_set(),
            try_upgrade_and_set(),
        )
        
        assert killswitch_service.set_state.call_count == 1

    @pytest.mark.asyncio
    async def test_upgrade_with_effect_tracking(self):
        """Test upgrade with effect status tracking (PENDING -> APPLIED)"""
        upgrade_key = "upgrade:test:3:dedup_004"
        
        is_first_upgrade, is_first_effect = await self.service.try_record_upgrade_with_effect(
            upgrade_key,
            "TEST",
            3,
            "Effect tracking test",
            "dedup_004",
        )
        
        assert is_first_upgrade is True
        assert is_first_effect is True
        
        pending = await self.service.get_pending_effects()
        assert len(pending) == 1
        assert pending[0]["upgrade_key"] == upgrade_key
        assert pending[0]["status"] == "PENDING"
        
        await self.service.mark_effect_applied(upgrade_key)
        
        pending_after = await self.service.get_pending_effects()
        assert len(pending_after) == 0

    @pytest.mark.asyncio
    async def test_upgrade_effect_failure_tracking(self):
        """Test upgrade with effect failure tracking"""
        upgrade_key = "upgrade:test:4:dedup_005"
        
        await self.service.try_record_upgrade_with_effect(
            upgrade_key,
            "TEST",
            4,
            "Failure test",
            "dedup_005",
        )
        
        await self.service.mark_effect_failed(upgrade_key, "Test error")
        
        pending = await self.service.get_pending_effects()
        assert len(pending) == 1
        assert pending[0]["status"] == "FAILED"
        assert pending[0]["last_error"] == "Test error"

    @pytest.mark.asyncio
    async def test_duplicate_effect_idempotency(self):
        """Test duplicate effect with different upgrade_keys doesn't interfere"""
        from trader.services.killswitch import KillSwitchService
        
        upgrade_key1 = "upgrade:test:5:dedup_006"
        upgrade_key2 = "upgrade:test:5:dedup_007"
        
        is_first_upgrade1, is_first_effect1 = await self.service.try_record_upgrade_with_effect(
            upgrade_key1, "TEST", 5, "Idempotency test 1", "dedup_006",
        )
        assert is_first_upgrade1 is True
        assert is_first_effect1 is True
        
        is_first_upgrade2, is_first_effect2 = await self.service.try_record_upgrade_with_effect(
            upgrade_key2, "TEST", 5, "Idempotency test 2", "dedup_007",
        )
        assert is_first_upgrade2 is True
        assert is_first_effect2 is True
        
        is_first_upgrade_dup, is_first_effect_dup = await self.service.try_record_upgrade_with_effect(
            upgrade_key1, "TEST", 5, "Idempotency test 1", "dedup_006",
        )
        assert is_first_upgrade_dup is False
        assert is_first_effect_dup is False

    @pytest.mark.asyncio
    async def test_step6_failure_pending_effect_recovery(self):
        """Test Step6: intent written but side-effect not executed - effect stays PENDING"""
        upgrade_key = "upgrade:test:2:dedup_007"
        
        is_first_upgrade, is_first_effect = await self.service.try_record_upgrade_with_effect(
            upgrade_key, "TEST", 2, "Step6 test", "dedup_007",
        )
        
        assert is_first_upgrade is True
        assert is_first_effect is True
        
        pending = await self.service.get_pending_effects()
        assert len(pending) == 1
        assert pending[0]["status"] == "PENDING"
        
        from trader.services.killswitch import KillSwitchService, KillSwitchSetRequest
        ks = KillSwitchService()
        ks.set_state(KillSwitchSetRequest(
            scope="TEST", level=2, reason="recovered", updated_by="recovery"
        ))
        
        await self.service.mark_effect_applied(upgrade_key)
        
        pending_after = await self.service.get_pending_effects()
        assert len(pending_after) == 0

    @pytest.mark.asyncio
    async def test_step8_failure_effect_marked_failed(self):
        """Test Step8: side-effect executed but status write fails - effect stays FAILED"""
        upgrade_key = "upgrade:test:2:dedup_008"
        
        is_first_upgrade, is_first_effect = await self.service.try_record_upgrade_with_effect(
            upgrade_key, "TEST", 2, "Step8 test", "dedup_008",
        )
        
        await self.service.mark_effect_failed(upgrade_key, "Network error during status write")
        
        pending = await self.service.get_pending_effects()
        assert len(pending) == 1
        assert pending[0]["status"] == "FAILED"
        assert "Network error" in pending[0]["last_error"]

    @pytest.mark.asyncio
    async def test_idempotency_under_retry(self):
        """Test that retry after failure maintains idempotency"""
        upgrade_key = "upgrade:test:2:dedup_009"
        
        is_first_upgrade, is_first_effect = await self.service.try_record_upgrade_with_effect(
            upgrade_key, "TEST", 2, "Retry test", "dedup_009",
        )
        
        assert is_first_effect is True
        
        await self.service.mark_effect_failed(upgrade_key, "First attempt failed")
        
        is_first_upgrade2, is_first_effect2 = await self.service.try_record_upgrade_with_effect(
            upgrade_key, "TEST", 2, "Retry test", "dedup_009",
        )
        
        assert is_first_upgrade2 is False
        assert is_first_effect2 is False
        
        pending = await self.service.get_pending_effects()
        assert len(pending) == 1
        assert pending[0]["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_step2_dedup_failure_no_upgrade_recorded(self):
        """Test Step2: dedup fails (duplicate event), upgrade and effect should NOT be recorded"""
        upgrade_key = "upgrade:test:2:dedup_step2"
        
        event_data = {
            "dedup_key": "dedup_step2",
            "scope": "TEST",
            "reason": "Step2 test",
            "recommended_level": 2,
        }
        
        event_id1, created1, is_first_upgrade1, is_first_effect1 = await self.service.ingest_event_with_upgrade(
            event_data, upgrade_key, 2
        )
        
        assert created1 is True
        assert is_first_upgrade1 is True
        assert is_first_effect1 is True
        
        pending1 = await self.service.get_pending_effects()
        assert len(pending1) == 1
        
        event_id2, created2, is_first_upgrade2, is_first_effect2 = await self.service.ingest_event_with_upgrade(
            event_data, upgrade_key, 2
        )
        
        assert created2 is False
        assert is_first_upgrade2 is False
        assert is_first_effect2 is False
        
        pending2 = await self.service.get_pending_effects()
        assert len(pending2) == 1


class TestOrderService:
    """Test OrderService"""

    def setup_method(self):
        """Setup for each test"""
        self.storage = reset_storage()
        self.service = OrderService(self.storage)

    def test_list_orders_empty(self):
        """Test listing orders when empty"""
        orders = self.service.list_orders()
        assert len(orders) == 0

    def test_cancel_order(self):
        """Test cancelling an order"""
        # First create an order directly in storage
        self.storage.create_order({
            "cl_ord_id": "ord_001",
            "account_id": "acc_001",
            "strategy_id": "strat_001",
            "venue": "BINANCE",
            "instrument": "BTCUSDT",
            "side": "BUY",
            "order_type": "LIMIT",
            "qty": "1.0",
            "status": "NEW"
        })

        result = self.service.cancel_order("ord_001")
        assert result.ok is True

    def test_list_executions(self):
        """Test listing executions"""
        self.storage.create_execution({
            "cl_ord_id": "ord_001",
            "exec_id": "exec_001",
            "ts_ms": 1700000000000,
            "fill_qty": "1.0",
            "fill_price": "50000.0"
        })

        executions = self.service.list_executions()
        assert len(executions) == 1


class TestPortfolioService:
    """Test PortfolioService"""

    def setup_method(self):
        """Setup for each test"""
        self.storage = reset_storage()
        self.service = PortfolioService(self.storage)

    def test_list_positions_empty(self):
        """Test listing positions when empty"""
        positions = self.service.list_positions()
        assert len(positions) == 0

    def test_get_pnl_empty(self):
        """Test getting PnL when empty"""
        pnl = self.service.get_pnl()
        assert pnl.realized_pnl == "0"
        assert pnl.unrealized_pnl == "0"


class TestEventService:
    """Test EventService"""

    def setup_method(self):
        """Setup for each test"""
        self.storage = reset_storage()
        self.service = EventService(self.storage)

    def test_list_events_empty(self):
        """Test listing events when empty"""
        events = self.service.list_events()
        assert len(events) == 0

    def test_trigger_replay(self):
        """Test triggering replay"""
        request = ReplayRequest(
            stream_key="orders",
            requested_by="admin"
        )
        result = self.service.trigger_replay(request)
        assert result.ok is True


class TestKillSwitchService:
    """Test KillSwitchService"""

    def setup_method(self):
        """Setup for each test"""
        self.storage = reset_storage()
        self.service = KillSwitchService(self.storage)

    def test_get_default_state(self):
        """Test getting default kill switch state"""
        state = self.service.get_state("GLOBAL")
        assert state.level == 0
        assert state.scope == "GLOBAL"

    def test_set_state(self):
        """Test setting kill switch"""
        request = KillSwitchSetRequest(
            scope="GLOBAL",
            level=2,
            reason="Emergency",
            updated_by="admin"
        )
        state = self.service.set_state(request)
        assert state.level == 2
        assert state.reason == "Emergency"


class TestBrokerService:
    """Test BrokerService"""

    def setup_method(self):
        """Setup for each test"""
        self.storage = reset_storage()
        self.service = BrokerService(self.storage)

    def test_list_brokers_empty(self):
        """Test listing brokers when empty"""
        brokers = self.service.list_brokers()
        assert len(brokers) == 0

    def test_get_status_not_found(self):
        """Test getting status for non-existent broker"""
        status = self.service.get_status("acc_001")
        assert status is None
