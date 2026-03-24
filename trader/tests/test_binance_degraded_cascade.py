"""
Degraded Cascade Controller Unit Tests
======================================
测试级联控制器的功能。
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from trader.adapters.binance.degraded_cascade import (
    DegradedCascadeController,
    CascadeConfig,
    CascadeMetrics,
    CascadeState,
)
from trader.adapters.binance.connector import AdapterHealth, AdapterHealthReport, StreamState
from trader.adapters.binance.environmental_risk import (
    EnvironmentalRiskEvent,
    RiskSeverity,
    RiskScope,
    RecommendedLevel,
)


class TestCascadeConfig:
    """Cascade Config 测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = CascadeConfig(control_plane_base_url="http://localhost:8080")

        assert config.control_plane_base_url == "http://localhost:8080"
        assert config.dedup_window_ms == 60000
        assert config.min_report_interval_ms == 5000
        assert config.self_protection_trigger_ms == 30000


class TestCascadeMetrics:
    """Cascade Metrics 测试"""

    def test_default_values(self):
        """测试默认值"""
        metrics = CascadeMetrics()

        assert metrics.degraded_enter_count == 0
        assert metrics.degraded_exit_count == 0
        assert metrics.risk_events_reported == 0
        assert metrics.self_protection_entered == 0


class TestDegradedCascadeController:
    """Degraded Cascade Controller 测试"""

    def test_initialization(self):
        """测试初始化"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        assert controller.state == CascadeState.NORMAL
        assert controller.is_self_protection_active is False

    def test_can_open_new_position_normal(self):
        """测试正常状态下可以开新仓"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        assert controller.can_open_new_position() is True

    def test_can_open_new_position_self_protection(self):
        """测试自保模式下不能开新仓"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        controller._self_protection_active = True

        assert controller.can_open_new_position() is False

    def test_can_cancel_order_always(self):
        """测试撤单总是允许"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        controller._self_protection_active = True

        assert controller.can_cancel_order() is True

    @pytest.mark.asyncio
    async def test_on_adapter_health_changed_enter_degraded(self):
        """测试进入 DEGRADED 模式"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

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

        assert controller.state in [CascadeState.DEGRADED, CascadeState.SELF_PROTECTION]
        assert controller.metrics.degraded_enter_count >= 1

    @pytest.mark.asyncio
    async def test_on_adapter_health_changed_exit_degraded(self):
        """测试退出 DEGRADED 模式"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        controller._state = CascadeState.DEGRADED
        controller._http_client = MagicMock()

        health = AdapterHealthReport(
            public_stream_state=MagicMock(value="CONNECTED"),
            private_stream_state=MagicMock(value="CONNECTED"),
            public_stream_healthy=True,
            private_stream_healthy=True,
            rest_alignment_healthy=True,
            rate_budget_state={},
            backoff_state={},
            overall_health=AdapterHealth.HEALTHY,
            last_update_ts=1609459200.0,
            metrics={}
        )

        await controller.on_adapter_health_changed(health, "recovered")

        assert controller.state == CascadeState.NORMAL
        assert controller.metrics.degraded_exit_count >= 1

    @pytest.mark.asyncio
    async def test_self_protection_callback(self):
        """测试自保回调"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        callback_triggered = []

        async def callback(active, reason):
            callback_triggered.append((active, reason))

        controller.register_self_protection_callback(callback)

        await controller._trigger_self_protection(Exception("test error"))

        assert len(callback_triggered) == 1
        assert callback_triggered[0][0] is True
        assert controller.is_self_protection_active is True

    @pytest.mark.asyncio
    async def test_recovery_callback(self):
        """测试恢复回调"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        callback_triggered = []

        async def callback():
            callback_triggered.append(True)

        controller.register_recovery_callback(callback)

        controller._state = CascadeState.SELF_PROTECTION
        controller._self_protection_active = True
        controller._reported_dedup_keys = {}

        await controller._exit_self_protection()

        assert controller.is_self_protection_active is False

    def test_should_report_dedup(self):
        """测试幂等去重"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        event1 = MagicMock()
        event1.dedup_key = "test_key"

        assert controller._should_report(event1) is True

        now_ms = int(time.time() * 1000)
        controller._reported_dedup_keys["test_key"] = now_ms

        assert controller._should_report(event1) is False

    def test_should_report_rate_limit(self):
        """测试频率限制"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        controller._last_report_ts["risk"] = time.time()

        event = MagicMock()
        event.dedup_key = "new_key"

        result = controller._should_report(event)

        assert result is False

    def test_get_status(self):
        """测试状态获取"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        status = controller.get_status()

        assert "state" in status
        assert "self_protection_active" in status
        assert "metrics" in status

    def test_get_local_events(self):
        """测试获取本地事件"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        events = controller.get_local_events()

        assert isinstance(events, list)


class TestCascadeState:
    """Cascade State 枚举测试"""

    def test_all_states(self):
        """测试所有状态"""
        states = [
            CascadeState.NORMAL,
            CascadeState.DEGRADED,
            CascadeState.SELF_PROTECTION,
            CascadeState.RECOVERING,
        ]

        assert len(states) == 4


class TestIntegration:
    """集成测试"""

    @pytest.mark.asyncio
    async def test_full_degraded_flow(self):
        """测试完整的降级流程"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response.__aenter__.return_value)
        mock_client.closed = False

        controller._http_client = mock_client

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

        await controller.on_adapter_health_changed(health, "network_issue")

        assert controller.metrics.degraded_enter_count >= 1

    @pytest.mark.asyncio
    async def test_self_protection_blocks_new_positions(self):
        """测试自保模式下阻止新开仓"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        await controller._trigger_self_protection()

        assert controller.can_open_new_position() is False

    @pytest.mark.asyncio
    async def test_self_protection_allows_cancel(self):
        """测试自保模式下允许撤单"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080"
        )

        await controller._trigger_self_protection()

        assert controller.can_cancel_order() is True

    @pytest.mark.asyncio
    async def test_control_plane_failure_triggers_local_killswitch(self):
        """测试控制面不可达时触发本地锁死"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080",
            config=CascadeConfig(
                control_plane_base_url="http://localhost:8080",
                max_retries_per_event=1,
                self_protection_trigger_ms=100
            )
        )

        try:
            original_post = controller._post_risk_event
            async def mock_post(event):
                return False
            controller._post_risk_event = mock_post

            health = AdapterHealthReport(
                public_stream_state=StreamState.CONNECTED,
                private_stream_state=StreamState.DEGRADED,
                public_stream_healthy=True,
                private_stream_healthy=False,
                rest_alignment_healthy=True,
                rate_budget_state={},
                backoff_state={},
                overall_health=AdapterHealth.DEGRADED,
                last_update_ts=time.time(),
                metrics={}
            )

            await controller._report_to_control_plane(health, "test_reason")

            assert controller.metrics.risk_events_failed >= 1
            assert controller.is_self_protection_active is True
        finally:
            await controller.close()

    @pytest.mark.asyncio
    async def test_recovery_no_report_storm(self):
        """测试恢复后不产生上报风暴（dedup 生效）"""
        controller = DegradedCascadeController(
            control_plane_base_url="http://localhost:8080",
            config=CascadeConfig(
                control_plane_base_url="http://localhost:8080",
                min_report_interval_ms=1000
            )
        )

        dedup_key = "dedup_test_key"

        should_report_1 = controller._should_report(EnvironmentalRiskEvent(
            dedup_key=dedup_key,
            severity=RiskSeverity.HIGH,
            reason="test",
            scope=RiskScope.GLOBAL
        ))
        assert should_report_1 is True

        controller._reported_dedup_keys[dedup_key] = time.time()

        should_report_2 = controller._should_report(EnvironmentalRiskEvent(
            dedup_key=dedup_key,
            severity=RiskSeverity.HIGH,
            reason="test",
            scope=RiskScope.GLOBAL
        ))
        assert should_report_2 is False


import time


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
