"""
Extended Coverage Tests for DegradedCascade
==========================================
增加 degraded_cascade 模块的测试覆盖率
"""
import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from trader.adapters.binance.degraded_cascade import (
    DegradedCascadeController,
    CascadeConfig,
    CascadeMetrics,
    CascadeState,
)
from trader.adapters.binance.connector import AdapterHealth, AdapterHealthReport
from trader.adapters.binance.environmental_risk import (
    EnvironmentalRiskEvent,
    RiskSeverity,
    RiskScope,
    RecommendedLevel,
)


class TestDegradedCascadeExtended:
    """扩展的 DegradedCascade 测试"""

    def test_cascade_config_defaults(self):
        """测试默认配置"""
        config = CascadeConfig()

        assert config.control_plane_base_url == "http://localhost:8080"
        assert config.self_protection_trigger_ms == 30000

    def test_cascade_config_explicit(self):
        """测试显式配置"""
        config = CascadeConfig(
            control_plane_base_url="http://custom:8080",
            self_protection_trigger_ms=60000
        )

        assert config.control_plane_base_url == "http://custom:8080"
        assert config.self_protection_trigger_ms == 60000

    def test_cascade_metrics_defaults(self):
        """测试默认指标"""
        metrics = CascadeMetrics()

        assert metrics.degraded_enter_count == 0
        assert metrics.degraded_exit_count == 0
        assert metrics.risk_events_reported == 0

    @pytest.mark.asyncio
    async def test_ensure_http_client_creates_new(self):
        """测试创建新的 HTTP 客户端"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )
        controller._http_client = None

        client = await controller._ensure_http_client()

        assert client is not None

    @pytest.mark.asyncio
    async def test_ensure_http_client_reuses_existing(self):
        """测试复用现有的 HTTP 客户端"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        mock_client = MagicMock()
        mock_client.closed = False
        controller._http_client = mock_client

        client = await controller._ensure_http_client()

        assert client == mock_client

    def test_can_open_new_position_normal(self):
        """测试正常状态下可以开仓"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        assert controller.can_open_new_position() is True

    def test_can_cancel_order_normal(self):
        """测试正常状态下可以撤单"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        assert controller.can_cancel_order() is True

    @pytest.mark.asyncio
    async def test_exit_self_protection_already_inactive(self):
        """测试退出自保 - 已经是 inactive"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        controller._self_protection_active = False

        await controller._exit_self_protection()

        assert controller.is_self_protection_active is False

    @pytest.mark.asyncio
    async def test_close(self):
        """测试关闭"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        controller._http_client = MagicMock()

        await controller.close()

    @pytest.mark.asyncio
    async def test_on_degraded_enter_already_degraded(self):
        """测试已进入 DEGRADED 状态"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        controller._state = CascadeState.DEGRADED
        controller._http_client = MagicMock()

        health = AdapterHealthReport(
            public_stream_state=MagicMock(value="CONNECTED"),
            private_stream_state=MagicMock(value="DEGRADED"),
            public_stream_healthy=True,
            private_stream_healthy=False,
            rest_alignment_healthy=True,
            rate_budget_state={},
            backoff_state={},
            overall_health=AdapterHealth.DEGRADED,
            last_update_ts=1609459200.0,
            metrics={}
        )

        await controller.on_adapter_health_changed(health, "test_reason")

        assert controller.state == CascadeState.DEGRADED


class TestCascadeConfigExtended:
    """Cascade 配置扩展测试"""

    def test_dedup_window_ms(self):
        """测试去重窗口"""
        config = CascadeConfig(dedup_window_ms=120000)
        assert config.dedup_window_ms == 120000

    def test_min_report_interval_ms(self):
        """测试最小上报间隔"""
        config = CascadeConfig(min_report_interval_ms=10000)
        assert config.min_report_interval_ms == 10000


class TestCascadeMetricsExtended:
    """Cascade 指标扩展测试"""

    def test_metrics_increment(self):
        """测试指标递增"""
        metrics = CascadeMetrics()
        metrics.degraded_enter_count += 1
        metrics.risk_events_reported += 1

        assert metrics.degraded_enter_count == 1
        assert metrics.risk_events_reported == 1


class TestCascadeStateValues:
    """状态枚举值测试"""

    def test_state_values(self):
        """测试状态值"""
        assert CascadeState.NORMAL.value == "NORMAL"
        assert CascadeState.DEGRADED.value == "DEGRADED"
        assert CascadeState.SELF_PROTECTION.value == "SELF_PROTECTION"
        assert CascadeState.RECOVERING.value == "RECOVERING"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
