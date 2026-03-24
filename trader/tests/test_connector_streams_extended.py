"""
Extended Coverage Tests for Connector and Streams
================================================
增加 connector, private_stream, public_stream 模块的测试覆盖率
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from trader.adapters.binance.connector import (
    BinanceConnector,
    BinanceConnectorConfig,
    AdapterHealth,
    AdapterHealthReport,
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
from trader.adapters.binance.stream_base import StreamState


class TestBinanceConnectorExtended:
    """扩展的 Connector 测试"""

    def test_connector_config_defaults(self):
        """测试默认配置"""
        config = BinanceConnectorConfig()

        assert config.testnet is True

    def test_connector_config_explicit(self):
        """测试显式配置"""
        config = BinanceConnectorConfig(testnet=False)

        assert config.testnet is False


class TestPrivateStreamExtended:
    """扩展的 Private Stream 测试"""

    def test_credentials_defaults(self):
        """测试默认凭证"""
        creds = BinanceCredentials(
            api_key="test_key",
            secret_key="test_secret"
        )

        assert creds.testnet is True

    def test_private_stream_config_defaults(self):
        """测试默认配置"""
        config = PrivateStreamConfig()

        assert config.base_url == "wss://stream.binance.com:9443/ws"
        assert config.listen_key_ttl == 3600

    def test_private_stream_config_explicit(self):
        """测试显式配置"""
        config = PrivateStreamConfig(
            base_url="wss://custom.url/ws",
            rest_url="https://custom.api/api"
        )

        assert config.base_url == "wss://custom.url/ws"
        assert config.rest_url == "https://custom.api/api"

    @pytest.mark.asyncio
    async def test_private_stream_initialization(self):
        """测试私有流初始化"""
        creds = BinanceCredentials(
            api_key="test_key",
            secret_key="test_secret"
        )

        manager = PrivateStreamManager(credentials=creds)

        assert manager._credentials.api_key == "test_key"
        assert manager.state == StreamState.IDLE


class TestPublicStreamExtended:
    """扩展的 Public Stream 测试"""

    def test_public_stream_config_defaults(self):
        """测试默认配置"""
        config = PublicStreamConfig()

        assert config.reconnect_delay == 1.0
        assert config.max_reconnect_delay == 60.0

    def test_public_stream_config_explicit(self):
        """测试显式配置"""
        config = PublicStreamConfig(
            streams=["btcusdt@trade", "ethusdt@trade"],
            reconnect_delay=2.0,
            max_reconnect_delay=120.0
        )

        assert len(config.streams) == 2
        assert config.reconnect_delay == 2.0

    @pytest.mark.asyncio
    async def test_public_stream_initialization(self):
        """测试公有流初始化"""
        config = PublicStreamConfig(
            streams=["btcusdt@trade"]
        )

        manager = PublicStreamManager(config=config)

        assert manager.state == StreamState.IDLE

    @pytest.mark.asyncio
    async def test_public_stream_get_status(self):
        """测试获取状态"""
        config = PublicStreamConfig(streams=["btcusdt@trade"])
        manager = PublicStreamManager(config=config)

        status = manager.get_status()

        assert "name" in status
        assert "state" in status
        assert "metrics" in status


class TestAdapterHealthReport:
    """适配器健康报告测试"""

    def test_adapter_health_enum(self):
        """测试健康状态枚举"""
        assert AdapterHealth.HEALTHY.value == "HEALTHY"
        assert AdapterHealth.DEGRADED.value == "DEGRADED"
        assert AdapterHealth.UNHEALTHY.value == "UNHEALTHY"
        assert AdapterHealth.DISCONNECTED.value == "DISCONNECTED"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
