"""
Unit Tests - Services Layer
==========================
Tests for business logic services.
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from trader.adapters.persistence.postgres import (
    PostgreSQLStorage,
    close_pool,
    is_postgres_available,
)
from trader.api.models.schemas import (
    StrategyRegisterRequest,
    StrategyVersionCreateRequest,
    VersionedConfigUpsertRequest,
)
from trader.services import RiskService, StrategyService
from trader.storage.in_memory import InMemoryStorage, reset_storage


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
            entrypoint="strategies.mean_reversion:Strategy",
        )
        strategy = self.service.register_strategy(request)
        assert strategy.strategy_id == "strat_001"
        assert strategy.name == "Mean Reversion"

    def test_get_strategy(self):
        """Test getting a strategy"""
        request = StrategyRegisterRequest(
            strategy_id="strat_001", name="Test", entrypoint="test:Strategy"
        )
        self.service.register_strategy(request)

        strategy = self.service.get_strategy("strat_001")
        assert strategy is not None
        assert strategy.strategy_id == "strat_001"

    def test_list_strategies(self):
        """Test listing strategies"""
        request1 = StrategyRegisterRequest(
            strategy_id="strat_001", name="Test1", entrypoint="test1:Strategy"
        )
        request2 = StrategyRegisterRequest(
            strategy_id="strat_002", name="Test2", entrypoint="test2:Strategy"
        )
        self.service.register_strategy(request1)
        self.service.register_strategy(request2)

        strategies = self.service.list_strategies()
        assert len(strategies) == 2

    def test_create_version(self):
        """Test creating strategy version"""
        request = StrategyRegisterRequest(
            strategy_id="strat_001", name="Test", entrypoint="test:Strategy"
        )
        self.service.register_strategy(request)

        version_request = StrategyVersionCreateRequest(
            version="1", code_ref="git:abc123", param_schema={"param1": {"type": "number"}}
        )
        version = self.service.create_version("strat_001", version_request)
        assert version.version == 1
        assert version.code_ref == "git:abc123"

    def test_list_versions(self):
        """Test listing strategy versions"""
        request = StrategyRegisterRequest(
            strategy_id="strat_001", name="Test", entrypoint="test:Strategy"
        )
        self.service.register_strategy(request)

        version_request = StrategyVersionCreateRequest(
            version="1", code_ref="git:v1", param_schema={}
        )
        self.service.create_version("strat_001", version_request)

        versions = self.service.list_versions("strat_001")
        assert len(versions) == 1

    def test_create_params(self):
        """Test creating strategy params"""
        request = StrategyRegisterRequest(
            strategy_id="strat_001", name="Test", entrypoint="test:Strategy"
        )
        self.service.register_strategy(request)

        params_request = VersionedConfigUpsertRequest(
            scope="strat_001", config={"max_position": 100}, created_by="admin"
        )
        params = self.service.create_params("strat_001", params_request)
        assert params.version == 1
        assert params.config["max_position"] == 100


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
            scope="GLOBAL", config={"max_daily_loss": 5000}, created_by="admin"
        )
        limits = self.service.set_limits(request)
        assert limits.version == 1
        assert limits.config["max_daily_loss"] == 5000

    def test_get_limits(self):
        """Test getting risk limits"""
        request = VersionedConfigUpsertRequest(
            scope="GLOBAL", config={"max_position": 1000}, created_by="admin"
        )
        self.service.set_limits(request)

        limits = self.service.get_limits("GLOBAL")
        assert limits is not None
        assert limits.version == 1
