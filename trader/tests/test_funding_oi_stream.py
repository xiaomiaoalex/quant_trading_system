"""
Unit Tests for Funding/OI Stream Adapter
=========================================
测试 Funding Rate 和 Open Interest 适配器功能。
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trader.adapters.binance.funding_oi_stream import (
    FundingOIAdapter,
    FundingOIConfig,
    FundingRecord,
    OIRecord,
    BINANCE_FUTURES_BASE_URL,
)
from trader.adapters.persistence.feature_store import FeatureStore


class MockFeatureStore:
    """模拟 FeatureStore 用于测试"""

    def __init__(self):
        self._features = {}
        self._write_feature_calls = []

    async def write_feature(
        self,
        symbol: str,
        feature_name: str,
        version: str,
        ts_ms: int,
        value: any,
        meta: dict = None,
    ):
        self._write_feature_calls.append({
            "symbol": symbol,
            "feature_name": feature_name,
            "version": version,
            "ts_ms": ts_ms,
            "value": value,
            "meta": meta,
        })
        key = f"{symbol}:{feature_name}:{version}:{ts_ms}"
        self._features[key] = {
            "symbol": symbol,
            "feature_name": feature_name,
            "version": version,
            "ts_ms": ts_ms,
            "value": value,
            "meta": meta,
        }
        return True, False

    def get_written_features(self):
        return self._write_feature_calls

    def clear(self):
        self._features = {}
        self._write_feature_calls = []


class AsyncCtxManager:
    """异步上下文管理器，用于 mock aiohttp response"""
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *args):
        pass


class TestFundingRecord:
    """测试 FundingRecord 数据类"""

    def test_funding_record_creation(self):
        record = FundingRecord(
            symbol="BTCUSDT",
            funding_rate=0.0001,
            exchange_ts_ms=1700000000000,
            local_ts_ms=1700000001000,
            next_funding_time_ms=1700020000000,
        )
        assert record.symbol == "BTCUSDT"
        assert record.funding_rate == 0.0001
        assert record.exchange_ts_ms == 1700000000000
        assert record.local_ts_ms == 1700000001000
        assert record.next_funding_time_ms == 1700020000000


class TestOIRecord:
    """测试 OIRecord 数据类"""

    def test_oi_record_creation(self):
        record = OIRecord(
            symbol="BTCUSDT",
            open_interest=123456.789,
            exchange_ts_ms=1700000000000,
            local_ts_ms=1700000001000,
        )
        assert record.symbol == "BTCUSDT"
        assert record.open_interest == 123456.789
        assert record.exchange_ts_ms == 1700000000000
        assert record.local_ts_ms == 1700000001000


class TestFundingOIConfig:
    """测试 FundingOIConfig 配置"""

    def test_default_config(self):
        config = FundingOIConfig()
        assert config.base_url == BINANCE_FUTURES_BASE_URL
        assert config.funding_poll_interval == 30.0 * 60
        assert config.oi_poll_interval == 5.0 * 60
        assert config.request_timeout == 10.0
        assert config.max_retries == 3
        assert config.funding_interval_hours == 8
        assert config.funding_pre_trigger_minutes == 30

    def test_custom_config(self):
        config = FundingOIConfig(
            base_url="https://test.binance.com",
            funding_poll_interval=3600,
            oi_poll_interval=300,
            max_retries=5,
        )
        assert config.base_url == "https://test.binance.com"
        assert config.funding_poll_interval == 3600
        assert config.oi_poll_interval == 300
        assert config.max_retries == 5


class TestFundingOIAdapter:
    """测试 FundingOIAdapter 适配器"""

    @pytest.fixture
    def mock_feature_store(self):
        """创建模拟的 FeatureStore"""
        return MockFeatureStore()

    @pytest.fixture
    def adapter(self, mock_feature_store):
        """创建适配器实例"""
        config = FundingOIConfig(
            request_timeout=5.0,
            max_retries=2,
            retry_delay=0.1,
        )
        return FundingOIAdapter(config=config, feature_store=mock_feature_store)

    def test_adapter_initialization(self, adapter):
        """测试适配器初始化"""
        assert adapter._config.request_timeout == 5.0
        assert adapter._config.max_retries == 2
        assert adapter._symbols == set()
        assert adapter._running is False

    def test_add_symbol(self, adapter):
        """测试添加 symbol"""
        adapter.add_symbol("BTCUSDT")
        adapter.add_symbol("ETHUSDT")
        assert len(adapter.symbols) == 2
        assert "BTCUSDT" in adapter.symbols
        assert "ETHUSDT" in adapter.symbols

    def test_remove_symbol(self, adapter):
        """测试移除 symbol"""
        adapter.add_symbol("BTCUSDT")
        adapter.add_symbol("ETHUSDT")
        adapter.remove_symbol("BTCUSDT")
        assert len(adapter.symbols) == 1
        assert "BTCUSDT" not in adapter.symbols
        assert "ETHUSDT" in adapter.symbols

    def test_remove_nonexistent_symbol(self, adapter):
        """测试移除不存在的 symbol"""
        adapter.add_symbol("BTCUSDT")
        adapter.remove_symbol("ETHUSDT")  # 不存在的 symbol
        assert len(adapter.symbols) == 1
        assert "BTCUSDT" in adapter.symbols

    def test_is_running(self, adapter):
        """测试运行状态检查"""
        assert adapter.is_running() is False

    @pytest.mark.asyncio
    async def test_fetch_funding_rate_success(self, adapter):
        """测试成功拉取 Funding Rate"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=[{
            "symbol": "BTCUSDT",
            "fundingRate": "0.00010000",
            "fundingTime": 1700000000000,
            "nextFundingTime": 1700020000000,
        }])

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=AsyncCtxManager(mock_response))
        mock_session.closed = False
        adapter._session = mock_session

        record = await adapter._fetch_funding_rate("BTCUSDT")

        assert record is not None
        assert record.symbol == "BTCUSDT"
        assert record.funding_rate == 0.0001
        assert record.exchange_ts_ms == 1700000000000
        assert record.next_funding_time_ms == 1700020000000

    @pytest.mark.asyncio
    async def test_fetch_funding_rate_empty_response(self, adapter):
        """测试 Funding Rate 空响应"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=[])

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=AsyncCtxManager(mock_response))
        mock_session.closed = False
        adapter._session = mock_session

        record = await adapter._fetch_funding_rate("BTCUSDT")

        assert record is None

    @pytest.mark.asyncio
    async def test_fetch_funding_rate_http_error(self, adapter):
        """测试 Funding Rate HTTP 错误"""
        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.text = AsyncMock(return_value="Bad Request")

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=AsyncCtxManager(mock_response))
        mock_session.closed = False
        adapter._session = mock_session

        record = await adapter._fetch_funding_rate("BTCUSDT")

        assert record is None

    @pytest.mark.asyncio
    async def test_fetch_open_interest_success(self, adapter):
        """测试成功拉取 Open Interest"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "symbol": "BTCUSDT",
            "openInterest": "123456.789",
            "updateTime": 1700000000000,
        })

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=AsyncCtxManager(mock_response))
        mock_session.closed = False
        adapter._session = mock_session

        record = await adapter._fetch_open_interest("BTCUSDT")

        assert record is not None
        assert record.symbol == "BTCUSDT"
        assert record.open_interest == 123456.789
        assert record.exchange_ts_ms == 1700000000000

    @pytest.mark.asyncio
    async def test_fetch_open_interest_http_error(self, adapter):
        """测试 Open Interest HTTP 错误"""
        mock_response = AsyncMock()
        mock_response.status = 429
        mock_response.text = AsyncMock(return_value="Rate limit")

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=AsyncCtxManager(mock_response))
        mock_session.closed = False
        adapter._session = mock_session

        record = await adapter._fetch_open_interest("BTCUSDT")

        assert record is None

    @pytest.mark.asyncio
    async def test_write_funding_to_store(self, adapter, mock_feature_store):
        """测试将 Funding Rate 写入 Feature Store"""
        adapter._feature_store = mock_feature_store

        record = FundingRecord(
            symbol="BTCUSDT",
            funding_rate=0.0001,
            exchange_ts_ms=1700000000000,
            local_ts_ms=1700000001000,
            next_funding_time_ms=1700020000000,
        )

        await adapter._write_funding_to_store(record)

        written = mock_feature_store.get_written_features()
        assert len(written) == 1
        assert written[0]["symbol"] == "BTCUSDT"
        assert written[0]["feature_name"] == "funding_rate"
        assert written[0]["version"] == "v1"
        assert written[0]["value"]["funding_rate"] == 0.0001

    @pytest.mark.asyncio
    async def test_write_oi_to_store(self, adapter, mock_feature_store):
        """测试将 Open Interest 写入 Feature Store"""
        adapter._feature_store = mock_feature_store

        record = OIRecord(
            symbol="BTCUSDT",
            open_interest=123456.789,
            exchange_ts_ms=1700000000000,
            local_ts_ms=1700000001000,
        )

        await adapter._write_oi_to_store(record)

        written = mock_feature_store.get_written_features()
        assert len(written) == 1
        assert written[0]["symbol"] == "BTCUSDT"
        assert written[0]["feature_name"] == "open_interest"
        assert written[0]["version"] == "v1"
        assert written[0]["value"]["open_interest"] == 123456.789

    @pytest.mark.asyncio
    async def test_write_to_store_failure_graceful(self, adapter):
        """测试写入失败时的降级保护"""
        adapter._feature_store = MagicMock()
        adapter._feature_store.write_feature = AsyncMock(side_effect=Exception("DB error"))

        record = FundingRecord(
            symbol="BTCUSDT",
            funding_rate=0.0001,
            exchange_ts_ms=1700000000000,
            local_ts_ms=1700000001000,
            next_funding_time_ms=1700020000000,
        )

        # 不应抛出异常
        await adapter._write_funding_to_store(record)

    @pytest.mark.asyncio
    async def test_fetch_all_funding_rates(self, adapter, mock_feature_store):
        """测试拉取多个 symbol 的 Funding Rate"""
        adapter._feature_store = mock_feature_store
        adapter.add_symbol("BTCUSDT")
        adapter.add_symbol("ETHUSDT")

        # 模拟响应
        btc_response = AsyncMock()
        btc_response.status = 200
        btc_response.json = AsyncMock(return_value=[{
            "symbol": "BTCUSDT",
            "fundingRate": "0.00010000",
            "fundingTime": 1700000000000,
            "nextFundingTime": 1700020000000,
        }])

        eth_response = AsyncMock()
        eth_response.status = 200
        eth_response.json = AsyncMock(return_value=[{
            "symbol": "ETHUSDT",
            "fundingRate": "0.00020000",
            "fundingTime": 1700000000000,
            "nextFundingTime": 1700020000000,
        }])

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=[
            AsyncCtxManager(btc_response),
            AsyncCtxManager(eth_response)
        ])
        mock_session.closed = False
        adapter._session = mock_session

        await adapter._fetch_all_funding_rates()

        written = mock_feature_store.get_written_features()
        assert len(written) == 2

    @pytest.mark.asyncio
    async def test_start_and_stop(self, adapter):
        """测试启动和停止"""
        with patch.object(adapter, '_ensure_session', new_callable=AsyncMock):
            with patch.object(adapter, '_fetch_all_funding_rates', new_callable=AsyncMock):
                with patch.object(adapter, '_fetch_all_open_interests', new_callable=AsyncMock):
                    with patch.object(adapter, '_close_session', new_callable=AsyncMock):
                        adapter.add_symbol("BTCUSDT")
                        await adapter.start()

                        assert adapter.is_running() is True

                        await adapter.stop()

                        assert adapter.is_running() is False

    @pytest.mark.asyncio
    async def test_stop_without_start(self, adapter):
        """测试未启动时停止"""
        with patch.object(adapter, '_close_session', new_callable=AsyncMock):
            await adapter.stop()
            # 不应抛出异常
            assert adapter.is_running() is False


class TestGlobalFunctions:
    """测试全局函数"""

    def test_binance_futures_base_url(self):
        """测试 Binance Futures API URL"""
        assert BINANCE_FUTURES_BASE_URL == "https://fapi.binance.com"
