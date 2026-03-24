"""
Extended Tests for Stream Base and Private/Public Streams
========================================================
增加 stream_base, private_stream, public_stream 模块的测试覆盖率
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from trader.adapters.binance.stream_base import (
    BaseStreamFSM,
    StreamConfig,
    StreamState,
    StreamEvent,
    StreamMetrics,
)
from trader.adapters.binance.private_stream import (
    PrivateStreamManager,
    PrivateStreamConfig,
    BinanceCredentials,
)
from trader.adapters.binance.public_stream import (
    PublicStreamManager,
    PublicStreamConfig,
)


class ExtendedStreamFSM(BaseStreamFSM):
    """用于扩展测试的虚拟状态机"""
    def __init__(self, name="TestStream"):
        super().__init__(name, StreamConfig())
        self._on_start_called = False
        self._on_stop_called = False

    async def _on_start(self):
        self._on_start_called = True

    async def _on_stop(self):
        self._on_stop_called = True


class TestStreamFSMExtended:
    """扩展的 Stream FSM 测试"""

    def test_name_property(self):
        """测试 name 属性"""
        fsm = ExtendedStreamFSM("CustomName")
        assert fsm.name == "CustomName"

    def test_state_property(self):
        """测试 state 属性"""
        fsm = ExtendedStreamFSM()
        assert fsm.state == StreamState.IDLE

    def test_metrics_property(self):
        """测试 metrics 属性"""
        fsm = ExtendedStreamFSM()
        metrics = fsm.metrics
        assert isinstance(metrics, StreamMetrics)

    def test_set_state_updates_metrics(self):
        """测试状态更新同步到指标"""
        fsm = ExtendedStreamFSM()
        fsm._set_state(StreamState.CONNECTED)
        assert fsm.metrics.state == StreamState.CONNECTED

    def test_register_handler_multiple(self):
        """测试注册多个处理器"""
        fsm = ExtendedStreamFSM()
        count = {"value": 0}

        def handler1(event, data):
            count["value"] += 1

        def handler2(event, data):
            count["value"] += 1

        fsm.register_handler(StreamEvent.CONNECTED, handler1)
        fsm.register_handler(StreamEvent.CONNECTED, handler2)

        async def run():
            await fsm._trigger_event(StreamEvent.CONNECTED, None)

        asyncio.run(run())

        assert count["value"] == 2

    @pytest.mark.asyncio
    async def test_trigger_event_with_exception(self):
        """测试触发事件时处理器抛出异常"""
        fsm = ExtendedStreamFSM()

        def bad_handler(event, data):
            raise ValueError("Test error")

        fsm.register_handler(StreamEvent.CONNECTED, bad_handler)

        await fsm._trigger_event(StreamEvent.CONNECTED, None)

    @pytest.mark.asyncio
    async def test_wait_until_stopped(self):
        """测试等待直到停止"""
        fsm = ExtendedStreamFSM()
        await fsm.start()  # 先启动，才能正确停止

        async def stop_later():
            await asyncio.sleep(0.01)
            await fsm.stop()

        task = asyncio.create_task(stop_later())
        await fsm.wait_until_stopped()
        await task

    def test_reconnect_storm_no_storm(self):
        """测试无风暴情况"""
        fsm = ExtendedStreamFSM()
        fsm._config.max_reconnect_per_window = 5

        fsm._record_reconnect()
        fsm._record_reconnect()

        assert fsm._check_reconnect_storm() is False

    def test_reconnect_storm_window_cleanup(self):
        """测试风暴窗口清理"""
        fsm = ExtendedStreamFSM()
        fsm._config.reconnect_window_seconds = 1

        fsm._record_reconnect()
        fsm._record_reconnect()

        assert len(fsm._reconnect_timestamps) == 2


class TestStreamConfigExtended:
    """扩展的 StreamConfig 测试"""

    def test_config_defaults(self):
        """测试默认配置"""
        config = StreamConfig()

        assert config.reconnect_max_attempts == 10
        assert config.reconnect_base_delay == 1.0
        assert config.stale_timeout_seconds == 30.0
        assert config.pong_timeout_seconds == 10.0

    def test_config_explicit(self):
        """测试显式配置"""
        config = StreamConfig(
            reconnect_max_attempts=5,
            reconnect_base_delay=2.0,
            stale_timeout_seconds=60.0
        )

        assert config.reconnect_max_attempts == 5
        assert config.reconnect_base_delay == 2.0
        assert config.stale_timeout_seconds == 60.0


class TestStreamMetricsExtended:
    """扩展的 StreamMetrics 测试"""

    def test_metrics_defaults(self):
        """测试默认指标"""
        metrics = StreamMetrics()

        assert metrics.state == StreamState.IDLE
        assert metrics.connect_count == 0
        assert metrics.disconnect_count == 0
        assert metrics.reconnect_count == 0
        assert metrics.stale_count == 0

    def test_metrics_values(self):
        """测试指标值"""
        metrics = StreamMetrics()
        metrics.connect_count = 5
        metrics.disconnect_count = 3
        metrics.reconnect_count = 2
        metrics.stale_count = 1

        assert metrics.connect_count == 5
        assert metrics.disconnect_count == 3
        assert metrics.reconnect_count == 2
        assert metrics.stale_count == 1


class TestStreamEventExtended:
    """扩展的 StreamEvent 测试"""

    def test_all_events(self):
        """测试所有事件"""
        assert StreamEvent.START.value == "START"
        assert StreamEvent.CONNECTED.value == "CONNECTED"
        assert StreamEvent.DISCONNECTED.value == "DISCONNECTED"
        assert StreamEvent.RECONNECT.value == "RECONNECT"
        assert StreamEvent.STALE_DETECTED.value == "STALE_DETECTED"
        assert StreamEvent.DATA_RECEIVED.value == "DATA_RECEIVED"
        assert StreamEvent.ERROR.value == "ERROR"
        assert StreamEvent.STOP.value == "STOP"

    def test_event_count(self):
        """测试事件数量"""
        assert len(StreamEvent) == 8


class TestStreamStateExtended:
    """扩展的 StreamState 测试"""

    def test_all_states(self):
        """测试所有状态"""
        assert StreamState.IDLE.value == "IDLE"
        assert StreamState.CONNECTING.value == "CONNECTING"
        assert StreamState.CONNECTED.value == "CONNECTED"
        assert StreamState.RECONNECTING.value == "RECONNECTING"
        assert StreamState.STALE_DATA.value == "STALE_DATA"
        assert StreamState.DEGRADED.value == "DEGRADED"
        assert StreamState.DISCONNECTED.value == "DISCONNECTED"
        assert StreamState.ERROR.value == "ERROR"
        assert StreamState.ALIGNING.value == "ALIGNING"

    def test_state_count(self):
        """测试状态数量"""
        assert len(StreamState) == 9


class TestPrivateStreamExtended2:
    """更多 Private Stream 测试"""

    @pytest.mark.asyncio
    async def test_private_stream_set_state(self):
        """测试设置私有流状态"""
        creds = BinanceCredentials(api_key="key", secret_key="secret")
        manager = PrivateStreamManager(credentials=creds)

        manager._set_state(StreamState.CONNECTED)

        assert manager.state == StreamState.CONNECTED

    @pytest.mark.asyncio
    async def test_private_stream_metrics(self):
        """测试私有流指标"""
        creds = BinanceCredentials(api_key="key", secret_key="secret")
        manager = PrivateStreamManager(credentials=creds)

        metrics = manager.metrics

        assert metrics.connect_count == 0
        assert metrics.reconnect_count == 0


class TestPublicStreamExtended2:
    """更多 Public Stream 测试"""

    @pytest.mark.asyncio
    async def test_public_stream_set_state(self):
        """测试设置公有流状态"""
        config = PublicStreamConfig(streams=["btcusdt@trade"])
        manager = PublicStreamManager(config=config)

        manager._set_state(StreamState.CONNECTED)

        assert manager.state == StreamState.CONNECTED

    @pytest.mark.asyncio
    async def test_public_stream_metrics(self):
        """测试公有流指标"""
        config = PublicStreamConfig(streams=["btcusdt@trade"])
        manager = PublicStreamManager(config=config)

        metrics = manager.metrics

        assert metrics.connect_count == 0
        assert metrics.reconnect_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
