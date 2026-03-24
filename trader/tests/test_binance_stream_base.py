"""
Stream Base FSM Unit Tests
===========================
测试基础状态机框架。
"""
import asyncio
import pytest

from trader.adapters.binance.stream_base import (
    BaseStreamFSM,
    StreamConfig,
    StreamState,
    StreamEvent,
    StreamMetrics,
)


class DummyStreamFSM(BaseStreamFSM):
    """用于测试的虚拟状态机"""

    def __init__(self):
        super().__init__("TestStream", StreamConfig())
        self._start_called = False
        self._stop_called = False

    async def _on_start(self) -> None:
        self._start_called = True

    async def _on_stop(self) -> None:
        self._stop_called = True


class TestBaseStreamFSM:
    """Base Stream FSM 测试"""

    def test_initial_state(self):
        """测试初始状态"""
        fsm = DummyStreamFSM()

        assert fsm.state == StreamState.IDLE
        assert fsm.is_running() is False

    @pytest.mark.asyncio
    async def test_start(self):
        """测试启动"""
        fsm = DummyStreamFSM()

        await fsm.start()

        assert fsm.is_running() is True
        assert fsm._start_called is True

    @pytest.mark.asyncio
    async def test_stop(self):
        """测试停止"""
        fsm = DummyStreamFSM()
        await fsm.start()

        await fsm.stop()

        assert fsm.is_running() is False
        assert fsm._stop_called is True

    @pytest.mark.asyncio
    async def test_double_start(self):
        """测试重复启动"""
        fsm = DummyStreamFSM()

        await fsm.start()
        await fsm.start()

        assert fsm.is_running() is True

    def test_event_handler(self):
        """测试事件处理器"""
        fsm = DummyStreamFSM()
        events_received = []

        def handler(event, data):
            events_received.append((event, data))

        fsm.register_handler(StreamEvent.CONNECTED, handler)

        async def run_test():
            await fsm._trigger_event(StreamEvent.CONNECTED, "test_data")

        asyncio.run(run_test())

        assert len(events_received) == 1
        assert events_received[0][0] == StreamEvent.CONNECTED

    def test_state_transition(self):
        """测试状态转换"""
        fsm = DummyStreamFSM()

        fsm._set_state(StreamState.CONNECTING)
        assert fsm.state == StreamState.CONNECTING

        fsm._set_state(StreamState.CONNECTED)
        assert fsm.state == StreamState.CONNECTED

    def test_metrics_initialization(self):
        """测试指标初始化"""
        fsm = DummyStreamFSM()
        metrics = fsm.metrics

        assert metrics.state == StreamState.IDLE
        assert metrics.connect_count == 0
        assert metrics.disconnect_count == 0
        assert metrics.reconnect_count == 0

    def test_reconnect_storm_detection(self):
        """测试重连风暴检测"""
        fsm = DummyStreamFSM()
        fsm._config.max_reconnect_per_window = 3

        fsm._record_reconnect()
        fsm._record_reconnect()
        fsm._record_reconnect()

        assert fsm._check_reconnect_storm() is True

    def test_get_status(self):
        """测试状态获取"""
        fsm = DummyStreamFSM()
        status = fsm.get_status()

        assert status["name"] == "TestStream"
        assert status["running"] is False
        assert "metrics" in status


class TestStreamMetrics:
    """Stream Metrics 测试"""

    def test_default_values(self):
        """测试默认值"""
        metrics = StreamMetrics()

        assert metrics.state == StreamState.IDLE
        assert metrics.connect_count == 0
        assert metrics.disconnect_count == 0
        assert metrics.reconnect_count == 0
        assert metrics.stale_count == 0


class TestStreamState:
    """Stream State 枚举测试"""

    def test_all_states(self):
        """测试所有状态"""
        states = [
            StreamState.IDLE,
            StreamState.CONNECTING,
            StreamState.CONNECTED,
            StreamState.RECONNECTING,
            StreamState.STALE_DATA,
            StreamState.DEGRADED,
            StreamState.DISCONNECTED,
            StreamState.ERROR,
        ]

        assert len(states) == 8


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
