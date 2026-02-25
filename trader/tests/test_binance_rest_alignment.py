"""
REST Alignment Coordinator Unit Tests
======================================
测试 REST 对齐协调器的功能。
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from trader.adapters.binance.rest_alignment import (
    RESTAlignmentCoordinator,
    AlignmentConfig,
    AlignmentMetrics,
    RestAlignmentSnapshot,
)
from trader.adapters.binance.rate_limit import Priority


class TestAlignmentConfig:
    """Alignment Config 测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = AlignmentConfig()

        assert config.base_url == "https://testnet.binance.vision/api"
        assert config.p0_interval_seconds == 60.0
        assert config.p1_interval_seconds == 120.0
        assert config.p2_interval_seconds == 300.0


class TestAlignmentMetrics:
    """Alignment Metrics 测试"""

    def test_default_values(self):
        """测试默认值"""
        metrics = AlignmentMetrics()

        assert metrics.total_alignments == 0
        assert metrics.successful_alignments == 0
        assert metrics.failed_alignments == 0


class TestRestAlignmentSnapshot:
    """Rest Alignment Snapshot 测试"""

    def test_creation(self):
        """测试创建"""
        snapshot = RestAlignmentSnapshot(
            open_orders=[{"order_id": "123"}],
            account={"balance": 1000},
            trades=[{"trade_id": "456"}],
            exchange_ts_ms=1609459200000,
            local_ts_ms=1609459200000,
            alignment_reason="manual"
        )

        assert len(snapshot.open_orders) == 1
        assert snapshot.account["balance"] == 1000
        assert len(snapshot.trades) == 1
        assert snapshot.alignment_reason == "manual"


class TestRESTAlignmentCoordinator:
    """REST Alignment Coordinator 测试"""

    def test_initialization(self):
        """测试初始化"""
        coordinator = RESTAlignmentCoordinator(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        assert coordinator._api_key == "test_api_key"
        assert coordinator._secret_key == "test_secret_key"
        assert coordinator._running is False

    def test_snapshot_handler_registration(self):
        """测试快照处理器注册"""
        coordinator = RESTAlignmentCoordinator(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        def handler(snapshot):
            pass

        coordinator.register_snapshot_handler(handler)

        assert len(coordinator._snapshot_handlers) == 1

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """测试启动停止"""
        coordinator = RESTAlignmentCoordinator(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        await coordinator.start()
        assert coordinator._running is True
        assert coordinator._session is not None

        await coordinator.stop()
        assert coordinator._running is False
        assert coordinator._session is None

    @pytest.mark.asyncio
    async def test_force_alignment_too_soon(self):
        """测试频繁对齐被跳过"""
        coordinator = RESTAlignmentCoordinator(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )
        coordinator._config.min_alignment_interval = 60.0

        await coordinator.start()
        coordinator._last_alignment_ts = coordinator._last_alignment_ts - 10

        result = await coordinator.force_alignment("test")

        assert result is None

    def test_get_metrics(self):
        """测试获取指标"""
        coordinator = RESTAlignmentCoordinator(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        metrics = coordinator.get_metrics()

        assert "total_alignments" in metrics
        assert "rate_budget" in metrics


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
