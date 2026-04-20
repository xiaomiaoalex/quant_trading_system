"""
Private Stream Manager Unit Tests
==================================
测试私有流状态机的功能。
"""
import pytest
import asyncio
import time

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
        status = manager.get_status()
        runtime = status.get("private_runtime", {})
        assert runtime.get("selected_mode") == "unknown"
        assert runtime.get("ws_api_subscribe_attempts") == 0
        assert runtime.get("ws_api_subscribe_success_rate") is None

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

    def test_ws_api_subscribe_success_rate_in_status(self):
        """测试 ws-api 订阅成功率指标"""
        creds = BinanceCredentials(api_key="test", secret_key="test")
        manager = PrivateStreamManager(creds)
        manager._ws_api_subscribe_attempts = 5
        manager._ws_api_subscribe_success = 4
        manager._ws_api_subscribe_failures = 1

        status = manager.get_status()
        runtime = status.get("private_runtime", {})
        assert runtime.get("ws_api_subscribe_success_rate") == 0.8

    def test_parse_order_update(self):
        """测试订单更新解析"""
        creds = BinanceCredentials(api_key="test", secret_key="test")
        manager = PrivateStreamManager(creds)

        data = {
            "c": "client_order_123",
            "i": 456,
            "X": "FILLED",
            "z": "1.5",
            "Z": "75000.0",
        }
        exchange_ts = 1609459200000

        result = manager._parse_order_update(data, exchange_ts)

        assert result.cl_ord_id == "client_order_123"
        assert result.broker_order_id == "456"
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
            "i": 456,
            "t": 789,
            "I": "exec_001",
            "x": "TRADE",
            "S": "BUY",
            "s": "BTCUSDT",
            "L": "50000.0",
            "l": "0.1",
            "n": "0.5",
        }
        exchange_ts = 1609459200000

        result = manager._parse_fill_update(data, exchange_ts)

        assert result.cl_ord_id == "client_order_123"
        assert result.broker_order_id == "456"
        assert result.symbol == "BTCUSDT"
        assert result.trade_id == 789
        assert result.exec_id == "exec_001"
        assert result.exec_type == "TRADE"
        assert result.side == "BUY"
        assert result.price == 50000.0
        assert result.qty == 0.1
        assert result.commission == 0.5

    def test_parse_fill_update_non_trade_returns_none(self):
        """非 TRADE executionReport 不应被解析为成交。"""
        creds = BinanceCredentials(api_key="test", secret_key="test")
        manager = PrivateStreamManager(creds)

        data = {
            "c": "client_order_123",
            "x": "NEW",
            "S": "BUY",
            "L": "0",
            "l": "0",
        }
        result = manager._parse_fill_update(data, 1609459200000)
        assert result is None

    @pytest.mark.asyncio
    async def test_receive_loop_does_not_use_wait_for_timeout(self, monkeypatch):
        """私有流接收循环不应再依赖 wait_for 超时判活。"""
        creds = BinanceCredentials(api_key="test", secret_key="test")
        manager = PrivateStreamManager(creds)

        class FakeWS:
            async def recv(self) -> str:
                return '{"e":"balanceUpdate","E":1}'

        manager._ws = FakeWS()
        manager._running = True

        async def fake_handle(_message: str) -> None:
            manager._running = False

        async def fail_wait_for(*_args, **_kwargs):
            raise AssertionError("asyncio.wait_for should not be used in receive loop")

        monkeypatch.setattr(asyncio, "wait_for", fail_wait_for)
        manager._handle_message = fake_handle  # type: ignore[method-assign]

        await manager._receive_loop()
        assert manager._running is False

    @pytest.mark.asyncio
    async def test_stale_check_skips_trade_stale_before_first_execution_report(self, monkeypatch):
        """未收到 executionReport 前，不应按 data stale 触发重连。"""
        creds = BinanceCredentials(api_key="test", secret_key="test")
        manager = PrivateStreamManager(creds)
        manager._running = True
        manager._selected_mode = "legacy_listen_key"
        manager._has_seen_execution_report = False
        manager._last_data_ts = time.time() - 180.0
        manager._last_pong_ts = time.time()
        manager._last_user_event_ts = time.time()

        reconnect_calls = {"count": 0}

        async def fake_reconnect() -> None:
            reconnect_calls["count"] += 1

        async def stop_after_one_tick(_seconds: float) -> None:
            manager._running = False

        monkeypatch.setattr(asyncio, "sleep", stop_after_one_tick)
        manager.reconnect = fake_reconnect  # type: ignore[method-assign]

        await manager._stale_check_loop()
        assert reconnect_calls["count"] == 0


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
            broker_order_id="456",
            symbol="BTCUSDT",
            exec_id="exec_001",
        )

        assert update.cl_ord_id == "test_123"
        assert update.trade_id == 789
        assert update.exec_id == "exec_001"
        assert update.exec_type == "TRADE"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
