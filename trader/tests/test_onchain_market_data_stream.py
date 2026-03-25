"""
Unit Tests - On-Chain Market Data Adapter
=========================================
Tests for OnChainMarketDataAdapter including:
- Configuration validation
- Data fetching (with mocked HTTP responses)
- Feature Store integration
- Graceful degradation on errors
- Start/stop lifecycle
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from trader.adapters.onchain.onchain_market_data_stream import (
    OnChainMarketDataAdapter,
    OnChainMarketDataConfig,
    LiquidationRecord,
    ExchangeFlowRecord,
    StablecoinSupplyRecord,
    get_onchain_adapter,
    get_onchain_adapter_async,
)
from trader.adapters.persistence.feature_store import FeatureStore
from trader.storage.in_memory import reset_storage


def _safe_stop_adapter(adapter) -> None:
    """
    安全停止适配器，处理 event loop 已关闭的情况

    在 pytest 环境中，teardown 时 event loop 可能已被关闭，
    直接使用 run_until_complete 会抛出 RuntimeError。
    """
    if adapter._running:
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_closed():
                loop.run_until_complete(adapter.stop())
        except RuntimeError:
            # Loop 已关闭，忽略
            adapter._running = False


class TestOnChainMarketDataConfig:
    """Test configuration dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = OnChainMarketDataConfig()
        assert config.binance_base_url == "https://fapi.binance.com"
        assert config.liquidation_poll_interval == 60.0
        assert config.flow_poll_interval == 300.0
        assert config.supply_poll_interval == 1800.0
        assert config.request_timeout == 10.0
        assert config.max_retries == 3

    def test_custom_config(self):
        """Test custom configuration values."""
        config = OnChainMarketDataConfig(
            binance_base_url="https://test.example.com",
            liquidation_poll_interval=30.0,
            max_retries=5,
        )
        assert config.binance_base_url == "https://test.example.com"
        assert config.liquidation_poll_interval == 30.0
        assert config.max_retries == 5


class TestOnChainMarketDataAdapterLifecycle:
    """Test adapter start/stop lifecycle."""

    def _create_mock_session(self):
        """Create a mock aiohttp session that returns empty responses."""
        mock_session = AsyncMock()
        mock_session.closed = False  # Must be False to prevent _ensure_session from creating real session
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=[])
        mock_session.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.__aexit__ = AsyncMock()
        return mock_session

    def setup_method(self):
        """Setup fresh storage and adapter for each test."""
        reset_storage()
        self.adapter = OnChainMarketDataAdapter()
        # Pre-create mock session to prevent real HTTP calls during start()
        self.adapter._session = self._create_mock_session()

    def teardown_method(self):
        """Cleanup adapter and storage."""
        _safe_stop_adapter(self.adapter)
        reset_storage()

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        """Test adapter can be started and stopped."""
        assert not self.adapter._running

        await self.adapter.start()
        assert self.adapter._running
        assert self.adapter._liquidation_task is not None
        assert self.adapter._flow_task is not None
        assert self.adapter._supply_task is not None

        await self.adapter.stop()
        assert not self.adapter._running

    @pytest.mark.asyncio
    async def test_start_with_symbols(self):
        """Test starting adapter with custom symbols."""
        symbols = ["BTCUSDT", "ETHUSDT"]
        await self.adapter.start(symbols=symbols)

        assert self.adapter._symbols == {"BTCUSDT", "ETHUSDT"}

        await self.adapter.stop()

    @pytest.mark.asyncio
    async def test_double_start_warning(self):
        """Test that starting twice logs a warning."""
        await self.adapter.start()
        # Should not raise, just log warning
        await self.adapter.start()
        await self.adapter.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_noop(self):
        """Test that stopping without starting doesn't raise."""
        await self.adapter.stop()  # Should not raise


class TestOnChainMarketDataAdapterSymbols:
    """Test symbol management."""

    def setup_method(self):
        """Setup fresh adapter for each test."""
        self.adapter = OnChainMarketDataAdapter()

    @pytest.mark.asyncio
    async def test_add_symbol(self):
        """Test adding a symbol."""
        self.adapter.add_symbol("BTCUSDT")
        assert "BTCUSDT" in self.adapter._symbols

    @pytest.mark.asyncio
    async def test_remove_symbol(self):
        """Test removing a symbol."""
        self.adapter.add_symbol("BTCUSDT")
        self.adapter.remove_symbol("BTCUSDT")
        assert "BTCUSDT" not in self.adapter._symbols

    @pytest.mark.asyncio
    async def test_remove_nonexistent_symbol(self):
        """Test removing a symbol that doesn't exist doesn't raise."""
        self.adapter.remove_symbol("NONEXISTENT")  # Should not raise


class TestOnChainMarketDataAdapterFetch:
    """Test data fetching methods."""

    def setup_method(self):
        """Setup fresh storage and adapter for each test."""
        reset_storage()
        self.adapter = OnChainMarketDataAdapter()

    def teardown_method(self):
        """Cleanup adapter and storage."""
        _safe_stop_adapter(self.adapter)
        reset_storage()

    @pytest.mark.asyncio
    async def test_fetch_binance_liquidation_stream_empty_response(self):
        """Test fetching liquidation stream with empty response."""
        mock_session = AsyncMock()
        mock_session.closed = False
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=[])
        mock_session.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.__aexit__ = AsyncMock()

        self.adapter._session = mock_session

        records = await self.adapter._fetch_binance_liquidation_stream()
        assert records == []

    @pytest.mark.asyncio
    async def test_fetch_binance_liquidation_stream_error(self):
        """Test fetching liquidation stream with error response."""
        mock_session = AsyncMock()
        mock_session.closed = False
        mock_response = AsyncMock()
        mock_response.status = 429
        mock_response.text = AsyncMock(return_value="Rate limit")
        mock_session.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.__aexit__ = AsyncMock()

        self.adapter._session = mock_session

        records = await self.adapter._fetch_binance_liquidation_stream()
        assert records == []

    @pytest.mark.asyncio
    async def test_fetch_stablecoin_supply_success(self):
        """Test fetching stablecoin supply successfully using mocked session."""
        import aiohttp
        
        # Create mock response that works as async context manager
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=[
            {
                "id": "tether",
                "symbol": "usdt",
                "total_supply": 83000000000,
                "market_cap_change_percentage_24h": 0.05,
            }
        ])
        
        # mock_response should return itself when used as async context manager
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__.return_value = None
        
        # Create mock session - get() should return the mock_response directly
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        mock_session.closed = False
        mock_session.__aenter__.return_value = AsyncMock()
        mock_session.__aexit__.return_value = AsyncMock()
        
        # Create new adapter with mocked session
        adapter = OnChainMarketDataAdapter()
        adapter._session = mock_session
        
        # Call the fetch method
        record = await adapter._fetch_stablecoin_supply("USDT")

        # Verify the record was returned with correct data
        assert record is not None
        assert record.symbol == "USDT"
        assert record.total_supply == 83000000000.0
        assert record.supply_change_24h == 0.05

    @pytest.mark.asyncio
    async def test_fetch_stablecoin_supply_not_found(self):
        """Test fetching stablecoin supply when not found."""
        mock_session = AsyncMock()
        mock_session.closed = False
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=[])
        mock_session.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.__aexit__ = AsyncMock()

        self.adapter._session = mock_session

        record = await self.adapter._fetch_stablecoin_supply("UNKNOWN")
        assert record is None

    @pytest.mark.asyncio
    async def test_fetch_stablecoin_supply_rate_limit(self):
        """Test fetching stablecoin supply when rate limited."""
        mock_session = AsyncMock()
        mock_session.closed = False
        mock_response = AsyncMock()
        mock_response.status = 429
        mock_session.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.__aexit__ = AsyncMock()

        self.adapter._session = mock_session

        record = await self.adapter._fetch_stablecoin_supply("USDT")
        assert record is None

    @pytest.mark.asyncio
    async def test_fetch_exchange_flows_returns_none(self):
        """Test that exchange flows returns None (requires API key)."""
        # Exchange flow requires API key, should return None gracefully
        record = await self.adapter._fetch_exchange_flows("BTC")
        assert record is None


class TestOnChainMarketDataAdapterWrite:
    """Test Feature Store write operations."""

    def setup_method(self):
        """Setup fresh storage and adapter for each test."""
        reset_storage()
        self.adapter = OnChainMarketDataAdapter()

    def teardown_method(self):
        """Cleanup storage after each test."""
        reset_storage()

    @pytest.mark.asyncio
    async def test_write_liquidation_to_store(self):
        """Test writing liquidation record to Feature Store."""
        record = LiquidationRecord(
            symbol="BTCUSDT",
            side="sell",
            price=50000.0,
            quantity=1.5,
            quantity_usd=75000.0,
            exchange_ts_ms=1700000000000,
            local_ts_ms=1700000000500,
        )

        await self.adapter._write_liquidation_to_store(record)

        # Verify feature was written by reading it back
        feature = await self.adapter._feature_store.read_feature(
            symbol="BTCUSDT",
            feature_name="liquidation",
            version="v1",
            ts_ms=1700000000000,
        )

        assert feature is not None
        assert feature["value"]["side"] == "sell"
        assert feature["value"]["price"] == 50000.0
        assert feature["value"]["quantity_usd"] == 75000.0

    @pytest.mark.asyncio
    async def test_write_flow_to_store(self):
        """Test writing exchange flow record to Feature Store."""
        record = ExchangeFlowRecord(
            symbol="BTC",
            inflow=1000.0,
            outflow=800.0,
            net_flow=200.0,
            exchange_ts_ms=1700000000000,
            local_ts_ms=1700000000500,
        )

        await self.adapter._write_flow_to_store(record)

        feature = await self.adapter._feature_store.read_feature(
            symbol="BTC",
            feature_name="exchange_flow",
            version="v1",
            ts_ms=1700000000000,
        )

        assert feature is not None
        assert feature["value"]["net_flow"] == 200.0

    @pytest.mark.asyncio
    async def test_write_supply_to_store(self):
        """Test writing stablecoin supply record to Feature Store."""
        record = StablecoinSupplyRecord(
            symbol="USDT",
            total_supply=83000000000.0,
            supply_change_24h=0.05,
            exchange_ts_ms=1700000000000,
            local_ts_ms=1700000000500,
        )

        await self.adapter._write_supply_to_store(record)

        feature = await self.adapter._feature_store.read_feature(
            symbol="USDT",
            feature_name="stablecoin_supply",
            version="v1",
            ts_ms=1700000000000,
        )

        assert feature is not None
        assert feature["value"]["total_supply"] == 83000000000.0

    @pytest.mark.asyncio
    async def test_write_graceful_degradation_on_error(self):
        """Test that write errors are logged but don't propagate."""
        # Create a mock feature store that raises an error
        mock_store = MagicMock(spec=FeatureStore)
        mock_store.write_feature = AsyncMock(side_effect=RuntimeError("DB error"))

        adapter = OnChainMarketDataAdapter(feature_store=mock_store)

        record = LiquidationRecord(
            symbol="BTCUSDT",
            side="buy",
            price=50000.0,
            quantity=1.0,
            quantity_usd=50000.0,
            exchange_ts_ms=1700000000000,
            local_ts_ms=1700000000500,
        )

        # Should not raise despite error - verify error is caught internally
        try:
            await adapter._write_liquidation_to_store(record)
        except RuntimeError:
            pytest.fail("Write error propagated instead of being caught gracefully")

        # Verify the mock was called (error was handled)
        mock_store.write_feature.assert_called_once()


class TestOnChainMarketDataAdapterPollLoops:
    """Test polling loop behavior."""

    def setup_method(self):
        """Setup fresh storage and adapter for each test."""
        reset_storage()
        self.adapter = OnChainMarketDataAdapter()

    def teardown_method(self):
        """Cleanup adapter and storage."""
        _safe_stop_adapter(self.adapter)
        reset_storage()

    @pytest.mark.asyncio
    async def test_liquidation_poll_loop_stops_on_cancel(self):
        """Test liquidation poll loop stops on cancellation."""
        self.adapter._running = True
        fetch_count = [0]

        async def mock_fetch():
            fetch_count[0] += 1
            await asyncio.sleep(1.0)  # Long sleep that will be interrupted

        with patch.object(self.adapter, "_fetch_and_write_liquidations", mock_fetch):
            task = asyncio.create_task(self.adapter._liquidation_poll_loop())
            await asyncio.sleep(0.05)  # Let it start one iteration
            task.cancel()

            # The task should complete without raising (CancelledError is caught internally)
            await task

            # Verify task completed and fetch was attempted at least once
            assert fetch_count[0] >= 1


class TestOnChainMarketDataAdapterIntegration:
    """Integration tests with mocked external services."""

    def setup_method(self):
        """Setup fresh storage and adapter for each test."""
        reset_storage()
        self.adapter = OnChainMarketDataAdapter()

    def teardown_method(self):
        """Cleanup adapter and storage."""
        _safe_stop_adapter(self.adapter)
        reset_storage()

    @pytest.mark.asyncio
    async def test_full_cycle_with_mocked_responses(self):
        """Test a full fetch cycle with mocked HTTP responses."""
        # Mock the session
        mock_session = AsyncMock()
        mock_session.closed = False

        # Mock Binance ticker response
        binance_response = AsyncMock()
        binance_response.status = 200
        binance_response.json = AsyncMock(return_value=[
            {"symbol": "BTCUSDT", "lastPrice": "50000.0"},
        ])

        # Mock CoinGecko response
        coingecko_response = AsyncMock()
        coingecko_response.status = 200
        coingecko_response.json = AsyncMock(return_value=[
            {"id": "tether", "symbol": "usdt", "total_supply": 83000000000},
        ])

        responses = [binance_response, coingecko_response]

        async def mock_get(url, **kwargs):
            resp = responses.pop(0) if responses else AsyncMock()
            resp.status = 200
            return resp

        mock_session.get = mock_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        self.adapter._session = mock_session

        # Fetch liquidation
        await self.adapter._fetch_and_write_liquidations()

        # Fetch supplies
        await self.adapter._fetch_and_write_supplies()

        # Verify no errors occurred (degraded gracefully)


class TestGlobalAdapterInstance:
    """Test global adapter singleton."""

    def setup_method(self):
        """Reset global adapter."""
        import trader.adapters.onchain.onchain_market_data_stream as module
        # Reset to None before test - ensure cleanup from any previous failed test
        if module._global_adapter is not None:
            if module._global_adapter._running:
                try:
                    asyncio.get_event_loop().run_until_complete(
                        module._global_adapter.stop()
                    )
                except RuntimeError:
                    pass  # Loop may already be closed
            module._global_adapter = None
        module._global_adapter = None

    def teardown_method(self):
        """Cleanup global adapter after each test."""
        import trader.adapters.onchain.onchain_market_data_stream as module
        # Properly stop and reset the global adapter to prevent resource leaks
        try:
            if module._global_adapter is not None:
                if module._global_adapter._running:
                    asyncio.get_event_loop().run_until_complete(
                        module._global_adapter.stop()
                    )
                module._global_adapter = None
        except RuntimeError:
            # Event loop may be closed - clear state anyway
            module._global_adapter = None

    @pytest.mark.asyncio
    async def test_get_onchain_adapter_singleton(self):
        """Test that get_onchain_adapter returns singleton after initialization."""
        # First initialize via async version
        adapter1 = await get_onchain_adapter_async()
        adapter2 = await get_onchain_adapter_async()
        # Both should return the same instance
        assert adapter1 is adapter2
        # Sync version should also work after initialization
        adapter3 = get_onchain_adapter()
        assert adapter3 is adapter1

    @pytest.mark.asyncio
    async def test_lifecycle_functions(self):
        """Test start/stop lifecycle functions."""
        from trader.adapters.onchain.onchain_market_data_stream import (
            start_onchain_service,
            stop_onchain_service,
        )

        # Patch _ensure_session to use a mock session instead of making real HTTP calls
        mock_session = AsyncMock()
        mock_session.closed = False
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=[])
        mock_session.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.__aexit__ = AsyncMock()

        # Use async version to get initialized adapter
        adapter = await get_onchain_adapter_async()
        adapter._session = mock_session

        # Now call start which will use the pre-set mock session
        await adapter.start()
        assert adapter.is_running()

        await stop_onchain_service()
        assert not adapter.is_running()


class TestRawLiquidationEventParsing:
    """Test RawLiquidationEvent WebSocket message parsing."""

    def test_parse_valid_force_order_message(self):
        """Test parsing valid Binance forceOrder WebSocket message."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)

        message = '{"e":"ForceOrder","E":1568014460943,"s":"BTCUSDT","S":"SELL","o":"LIMIT","p":"11000.00","q":"1.0","ap":"11100.00","l":"1.0","v":"1.0"}'
        event = connector._parse_message(message)

        assert event is not None
        assert event.symbol == "BTCUSDT"
        assert event.side == "sell"
        assert event.price == 11000.00
        assert event.quantity == 1.0
        assert event.notional_usd == 11100.00  # ap * q
        assert event.order_type == "LIMIT"
        assert event.event_time_ms == 1568014460943

    def test_parse_buy_liquidation(self):
        """Test parsing BUY side liquidation."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)

        message = '{"e":"ForceOrder","E":1568014460943,"s":"ETHUSDT","S":"BUY","o":"MARKET","p":"2000.00","q":"5.0","ap":"2010.00","l":"5.0","v":"5.0"}'
        event = connector._parse_message(message)

        assert event is not None
        assert event.side == "buy"
        assert event.notional_usd == 10050.00  # 2010 * 5

    def test_parse_non_usdt_symbol_rejected(self):
        """Test that non-USDT symbols are filtered out."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)

        # BNBUSD should be filtered
        message = '{"e":"ForceOrder","E":1568014460943,"s":"BNBUSD","S":"SELL","o":"LIMIT","p":"300.00","q":"1.0","ap":"305.00","l":"1.0","v":"1.0"}'
        event = connector._parse_message(message)

        assert event is None

    def test_parse_non_force_order_message_returns_none(self):
        """Test that non-ForceOrder messages return None."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)

        #trade message
        message = '{"e":"trade","E":1568014460943,"s":"BTCUSDT","p":"11000.00"}'
        event = connector._parse_message(message)

        assert event is None

    def test_parse_invalid_json_returns_none(self):
        """Test that invalid JSON returns None without raising."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)

        event = connector._parse_message("not valid json")
        assert event is None


class TestLiquidationAggregator:
    """Test LiquidationAggregator 1m bucket functionality."""

    def test_bucket_alignment(self):
        """Test that events are aligned to 1m buckets."""
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator

        agg = LiquidationAggregator()

        # Event at 1568014460943 should align to bucket starting at 1568014440000 (1568014460943 // 60000 * 60000)
        ts = 1568014460943
        aligned = agg._align_to_bucket(ts)
        assert aligned == 1568014440000

    def test_add_event_to_bucket(self):
        """Test adding events to bucket."""
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        agg = LiquidationAggregator()

        event = RawLiquidationEvent(
            event_time_ms=1568014460943,
            symbol="BTCUSDT",
            side="sell",
            price=11000.0,
            quantity=1.0,
            notional_usd=11000.0,
            order_type="LIMIT",
        )
        agg.add_event(event)

        bucket_ts = agg._align_to_bucket(event.event_time_ms)
        assert bucket_ts in agg._buckets
        assert len(agg._buckets[bucket_ts]) == 1

    def test_aggregate_single_event_bucket(self):
        """Test aggregating a bucket with single event."""
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        agg = LiquidationAggregator()

        event = RawLiquidationEvent(
            event_time_ms=1568014460943,
            symbol="BTCUSDT",
            side="sell",
            price=11000.0,
            quantity=1.0,
            notional_usd=11000.0,
            order_type="LIMIT",
        )
        agg.add_event(event)

        bucket_ts = agg._align_to_bucket(event.event_time_ms)
        bucket = agg._aggregate_bucket(bucket_ts)

        assert bucket is not None
        assert bucket.liquidation_count == 1
        assert bucket.liquidation_notional_usd == 11000.0
        assert bucket.short_liquidation_notional_usd == 11000.0
        assert bucket.long_liquidation_notional_usd == 0.0
        assert bucket.net_liquidation_imbalance_usd == -11000.0

    def test_aggregate_multi_event_bucket(self):
        """Test aggregating a bucket with multiple events."""
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        agg = LiquidationAggregator()

        # Use timestamps that align to the same bucket (same minute)
        # 1568014440000 is already aligned to 1m bucket
        base_ts = 1568014440000

        events = [
            RawLiquidationEvent(base_ts + 1000, "BTCUSDT", "sell", 11000.0, 1.0, 11000.0, "LIMIT"),
            RawLiquidationEvent(base_ts + 5000, "BTCUSDT", "sell", 11100.0, 0.5, 5550.0, "LIMIT"),
            RawLiquidationEvent(base_ts + 10000, "ETHUSDT", "buy", 2000.0, 5.0, 10000.0, "MARKET"),
        ]

        for e in events:
            agg.add_event(e)

        bucket = agg._aggregate_bucket(base_ts)

        assert bucket is not None
        assert bucket.liquidation_count == 3
        assert bucket.liquidation_notional_usd == 26550.0
        assert bucket.short_liquidation_notional_usd == 16550.0  # 11000 + 5550
        assert bucket.long_liquidation_notional_usd == 10000.0
        assert bucket.net_liquidation_imbalance_usd == -6550.0
        assert set(bucket.symbols) == {"BTCUSDT", "ETHUSDT"}

    def test_aggregate_empty_bucket_returns_none(self):
        """Test that aggregating empty bucket returns None."""
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator

        agg = LiquidationAggregator()

        bucket = agg._aggregate_bucket(1568014400000)
        assert bucket is None


class TestLiquidationAggregatorSmoke:
    """Smoke tests for LiquidationAggregator (integration without real WS)."""

    def test_aggregator_initialization(self):
        """Test aggregator can be initialized."""
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator

        agg = LiquidationAggregator()
        assert agg._running is False
        assert len(agg._buckets) == 0
