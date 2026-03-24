"""
Extended Coverage Tests for REST Alignment
==========================================
增加 rest_alignment 模块的测试覆盖率
"""
import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from trader.adapters.binance.rest_alignment import (
    RESTAlignmentCoordinator,
    AlignmentConfig,
    AlignmentMetrics,
)
from trader.adapters.binance.rate_limit import Priority


class TestRESTAlignmentExtended:
    """扩展的 REST Alignment 测试"""

    def test_alignment_config_defaults(self):
        """测试默认配置"""
        config = AlignmentConfig()

        assert config.base_url == "https://testnet.binance.vision/api"
        assert config.p0_interval_seconds == 60.0
        assert config.p1_interval_seconds == 120.0
        assert config.p2_interval_seconds == 300.0

    def test_alignment_config_explicit(self):
        """测试显式配置"""
        config = AlignmentConfig(
            base_url="https://api.binance.com/api",
            p0_interval_seconds=30.0,
            min_alignment_interval=15.0
        )

        assert config.base_url == "https://api.binance.com/api"
        assert config.p0_interval_seconds == 30.0
        assert config.min_alignment_interval == 15.0

    def test_alignment_metrics_defaults(self):
        """测试默认指标"""
        metrics = AlignmentMetrics()

        assert metrics.total_alignments == 0
        assert metrics.successful_alignments == 0
        assert metrics.failed_alignments == 0

    @pytest.mark.asyncio
    async def test_coordinator_initialization(self):
        """测试协调器初始化"""
        coordinator = RESTAlignmentCoordinator(
            api_key="test_key",
            secret_key="test_secret"
        )

        assert coordinator._running is False
        assert coordinator._api_key == "test_key"
        assert coordinator._secret_key == "test_secret"

    @pytest.mark.asyncio
    async def test_start(self):
        """测试启动"""
        coordinator = RESTAlignmentCoordinator(
            api_key="test_key",
            secret_key="test_secret"
        )

        await coordinator.start()

        assert coordinator._running is True
        assert coordinator._session is not None

    @pytest.mark.asyncio
    async def test_stop_not_running(self):
        """测试停止 - 未运行"""
        coordinator = RESTAlignmentCoordinator(
            api_key="test_key",
            secret_key="test_secret"
        )

        coordinator._running = False

        await coordinator.stop()

        assert coordinator._running is False


class TestAlignmentMetrics:
    """对齐指标测试"""

    def test_metrics_increment(self):
        """测试指标递增"""
        metrics = AlignmentMetrics()

        metrics.total_alignments += 1
        metrics.successful_alignments += 1
        metrics.failed_alignments += 1

        assert metrics.total_alignments == 1
        assert metrics.successful_alignments == 1
        assert metrics.failed_alignments == 1

    def test_metrics_setters(self):
        """测试指标设置"""
        metrics = AlignmentMetrics()

        metrics.last_alignment_ts = time.time()
        metrics.last_alignment_reason = "test"
        metrics.last_error = "error"

        assert metrics.last_alignment_reason == "test"
        assert metrics.last_error == "error"


class TestAlignmentConfigExtended:
    """配置扩展测试"""

    def test_alignment_timeout(self):
        """测试对齐超时"""
        config = AlignmentConfig(alignment_timeout=20.0)
        assert config.alignment_timeout == 20.0

    def test_min_alignment_interval(self):
        """测试最小对齐间隔"""
        config = AlignmentConfig(min_alignment_interval=10.0)
        assert config.min_alignment_interval == 10.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
