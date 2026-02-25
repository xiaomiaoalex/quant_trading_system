"""
Private Stream Manager Unit Tests
==================================
测试私有流状态机的功能。
"""
import pytest
import asyncio

from trader.adapters.binance.private_stream import (
    PrivateStreamManager,
    PrivateStreamConfig,
    BinanceCredentials,
    RawOrderUpdate,
    RawFillUpdate,
)
from trader.adapters.binance.stream_base import StreamState


class TestBinanceCredentials:
    """Binance Credentials 测试"""

    def test_credentials_creation(self):
        """测试凭证创建"""
        creds = BinanceCredentials(
            api_key="test_api_key",
            secret_key="test_secret_key",
            testnet=True
        )

        assert creds.api_key == "test_api_key"
        assert creds.secret_key == "test_secret_key"
        assert creds.testnet is True


class TestPrivateStreamConfig:
    """Private Stream Config 测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = PrivateStreamConfig()

        assert config.listen_key_ttl == 3600
        assert config.listen_key_refresh_interval == 1800
        assert config.pong_timeout == 10
        assert config.stale_timeout == 30


class TestPrivateStreamManager:
    """Private Stream Manager 测试"""

    def test_initialization(self):
        """测试初始化"""
        creds = BinanceCredentials(api_key="test", secret_key="test")
        config = PrivateStreamConfig()
        manager = PrivateStreamManager(creds, config)

        assert manager.name == "PrivateStream"
        assert manager.state == StreamState.IDLE

    def test_order_handler_registration(self):
        """测试订单处理器注册"""
        creds = BinanceCredentials(api_key="test", secret_key="test")
        manager = PrivateStreamManager(creds)

        calls = []

        def handler(update: RawOrderUpdate):
            calls.append(update)

        manager.register_order_handler(handler)

        assert len(manager._order_update_handlers) == 1

    def test_fill_handler_registration(self):
        """测试成交处理器注册"""
        creds = BinanceCredentials(api_key="test", secret_key="test")
        manager = PrivateStreamManager(creds)

        calls = []

        def handler(update: RawFillUpdate):
            calls.append(update)

        manager.register_fill_handler(handler)

        assert len(manager._fill_update_handlers) == 1

    def test_parse_order_update(self):
        """测试订单更新解析"""
        creds = BinanceCredentials(api_key="test", secret_key="test")
        manager = PrivateStreamManager(creds)

        data = {
            "c": "client_order_123",
            "t": "broker_order_456",
            "X": "FILLED",
            "z": "1.5",
            "L": "50000.0",
        }
        exchange_ts = 1609459200000

        result = manager._parse_order_update(data, exchange_ts)

        assert result.cl_ord_id == "client_order_123"
        assert result.broker_order_id == "broker_order_456"
        assert result.status == "FILLED"
        assert result.filled_qty == 1.5
        assert result.avg_price == 50000.0
        assert result.source == "WS"

    def test_parse_fill_update(self):
        """测试成交更新解析"""
        creds = BinanceCredentials(api_key="test", secret_key="test")
        manager = PrivateStreamManager(creds)

        data = {
            "c": "client_order_123",
            "t": "trade_789",
            "x": "TRADE",
            "S": "BUY",
            "p": "50000.0",
            "q": "0.1",
            "n": "0.5",
        }
        exchange_ts = 1609459200000

        result = manager._parse_fill_update(data, exchange_ts)

        assert result.cl_ord_id == "client_order_123"
        assert result.trade_id == 789
        assert result.exec_type == "TRADE"
        assert result.side == "BUY"
        assert result.price == 50000.0
        assert result.qty == 0.1
        assert result.commission == 0.5


class TestRawOrderUpdate:
    """Raw Order Update 测试"""

    def test_creation(self):
        """测试创建"""
        update = RawOrderUpdate(
            cl_ord_id="test_123",
            broker_order_id="broker_456",
            status="FILLED",
            filled_qty=1.5,
            avg_price=50000.0,
            exchange_ts_ms=1609459200000,
            local_receive_ts_ms=1609459200000,
        )

        assert update.cl_ord_id == "test_123"
        assert update.status == "FILLED"
        assert update.source == "WS"


class TestRawFillUpdate:
    """Raw Fill Update 测试"""

    def test_creation(self):
        """测试创建"""
        update = RawFillUpdate(
            cl_ord_id="test_123",
            trade_id=789,
            exec_type="TRADE",
            side="BUY",
            price=50000.0,
            qty=0.1,
            commission=0.5,
            exchange_ts_ms=1609459200000,
            local_receive_ts_ms=1609459200000,
        )

        assert update.cl_ord_id == "test_123"
        assert update.trade_id == 789
        assert update.exec_type == "TRADE"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
