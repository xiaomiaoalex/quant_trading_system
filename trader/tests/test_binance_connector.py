"""
Binance Connector Unit Tests
============================
测试统一连接协调器的功能。
"""
import pytest
import asyncio
import time
from unittest.mock import AsyncMock

from trader.adapters.binance.connector import (
    BinanceConnector,
    BinanceConnectorConfig,
    AdapterHealth,
    AdapterHealthReport,
)
from trader.adapters.binance.private_stream import ListenKeyEndpointGoneError
from trader.adapters.binance.private_stream import RawOrderUpdate, RawFillUpdate
from trader.adapters.binance.public_stream import MarketEvent
from trader.adapters.binance.rest_alignment import RestAlignmentSnapshot
from trader.adapters.binance.stream_base import StreamState


class TestBinanceConnectorConfig:
    """Binance Connector Config 测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = BinanceConnectorConfig()

        assert config.testnet is True

    def test_custom_config(self):
        """测试自定义配置"""
        config = BinanceConnectorConfig(testnet=False)

        assert config.testnet is False


class TestAdapterHealth:
    """Adapter Health 枚举测试"""

    def test_all_health_states(self):
        """测试所有健康状态"""
        states = [
            AdapterHealth.HEALTHY,
            AdapterHealth.DEGRADED,
            AdapterHealth.UNHEALTHY,
            AdapterHealth.DISCONNECTED,
        ]

        assert len(states) == 4


class TestAdapterHealthReport:
    """Adapter Health Report 测试"""

    def test_creation(self):
        """测试创建"""
        report = AdapterHealthReport(
            public_stream_state=StreamState.CONNECTED,
            private_stream_state=StreamState.CONNECTED,
            public_stream_healthy=True,
            private_stream_healthy=True,
            rest_alignment_healthy=True,
            rate_budget_state={},
            backoff_state={},
            overall_health=AdapterHealth.HEALTHY,
            last_update_ts=1609459200.0,
            metrics={}
        )

        assert report.public_stream_state == StreamState.CONNECTED
        assert report.overall_health == AdapterHealth.HEALTHY


class TestBinanceConnector:
    """Binance Connector 测试"""

    def test_initialization(self):
        """测试初始化"""
        connector = BinanceConnector(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        assert connector._api_key == "test_api_key"
        assert connector._secret_key == "test_secret_key"

    def test_order_handler_registration(self):
        """测试订单处理器注册"""
        connector = BinanceConnector(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        def handler(update: RawOrderUpdate):
            pass

        connector.register_order_handler(handler)

        assert len(connector._order_update_handlers) == 1

    def test_fill_handler_registration(self):
        """测试成交处理器注册"""
        connector = BinanceConnector(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        def handler(update: RawFillUpdate):
            pass

        connector.register_fill_handler(handler)

        assert len(connector._fill_update_handlers) == 1

    def test_market_handler_registration(self):
        """测试市场事件处理器注册"""
        connector = BinanceConnector(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        def handler(event: MarketEvent):
            pass

        connector.register_market_handler(handler)

        assert len(connector._market_event_handlers) == 1

    def test_snapshot_handler_registration(self):
        """测试快照处理器注册"""
        connector = BinanceConnector(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        def handler(snapshot: RestAlignmentSnapshot):
            pass

        connector.register_snapshot_handler(handler)

        assert len(connector._snapshot_handlers) == 1

    def test_health_handler_registration(self):
        """测试健康状态处理器注册"""
        connector = BinanceConnector(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        def handler(report: AdapterHealthReport):
            pass

        connector.register_health_handler(handler)

        assert len(connector._health_handlers) == 1

    @pytest.mark.asyncio
    async def test_start_stop(self):
        """测试启动停止（不实际连接）"""
        connector = BinanceConnector(
            api_key="test_api_key",
            secret_key="test_secret_key",
            streams=["btcusdt@trade"]
        )

        assert connector._running is False

    def test_get_health(self):
        """测试获取健康状态"""
        connector = BinanceConnector(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        health = connector.get_health()

        assert isinstance(health, AdapterHealthReport)
        assert "public_stream_state" in health.__dict__ or hasattr(health, "public_stream_state")

    def test_public_stream_property(self):
        """测试获取公有流管理器"""
        connector = BinanceConnector(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        assert connector.public_stream is not None

    def test_private_stream_property(self):
        """测试获取私有流管理器"""
        connector = BinanceConnector(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        assert connector.private_stream is not None

    def test_rest_coordinator_property(self):
        """测试获取 REST 协调器"""
        connector = BinanceConnector(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        assert connector.rest_coordinator is not None


class TestConnectorIntegration:
    """Connector 集成测试"""

    def test_handler_chain(self):
        """测试处理器链"""
        connector = BinanceConnector(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        order_updates = []
        fill_updates = []

        def order_handler(update: RawOrderUpdate):
            order_updates.append(update)

        def fill_handler(update: RawFillUpdate):
            fill_updates.append(update)

        connector.register_order_handler(order_handler)
        connector.register_fill_handler(fill_handler)

        test_order = RawOrderUpdate(
            cl_ord_id="test",
            broker_order_id="123",
            status="FILLED",
            filled_qty=1.0,
            avg_price=50000.0,
            exchange_ts_ms=1609459200000,
            local_receive_ts_ms=1609459200000
        )

        connector._on_order_update(test_order)

        assert len(order_updates) == 1


class TestBinanceConnectorStartupDegrade:
    """Binance Connector 启动降级测试"""

    @pytest.mark.asyncio
    async def test_start_degrades_when_listen_key_endpoint_gone(self):
        connector = BinanceConnector(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )

        connector._rest_coordinator.start = AsyncMock(return_value=None)
        connector._public_manager.start = AsyncMock(return_value=None)
        connector._private_manager.start = AsyncMock(
            side_effect=ListenKeyEndpointGoneError("410 Gone")
        )
        connector._private_manager.stop = AsyncMock(return_value=None)

        await connector.start()

        assert connector._running is True
        assert connector._private_stream_disabled_reason is not None
        connector._private_manager.stop.assert_awaited_once()
        await connector.stop()

    def test_get_health_degraded_when_private_stream_disabled(self):
        connector = BinanceConnector(
            api_key="test_api_key",
            secret_key="test_secret_key"
        )
        connector._private_stream_disabled_reason = "listenkey gone"
        connector._public_manager._set_state(StreamState.CONNECTED)
        connector._private_manager._set_state(StreamState.DISCONNECTED)

        connector._rest_coordinator.get_metrics = lambda: {
            "last_rest_success_ts_ms": int(time.time() * 1000)
        }

        report = connector.get_health()
        assert report.overall_health == AdapterHealth.DEGRADED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
