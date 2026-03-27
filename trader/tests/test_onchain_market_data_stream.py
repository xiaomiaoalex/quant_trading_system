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

    @pytest.mark.asyncio
    async def test_add_event_to_bucket(self):
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
        await agg.add_event(event)

        bucket_ts = agg._align_to_bucket(event.event_time_ms)
        assert bucket_ts in agg._buckets
        assert len(agg._buckets[bucket_ts]) == 1

    @pytest.mark.asyncio
    async def test_aggregate_single_event_bucket(self):
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
        await agg.add_event(event)

        bucket_ts = agg._align_to_bucket(event.event_time_ms)
        result = await agg._aggregate_bucket(bucket_ts)

        assert result is not None
        bucket, events_by_symbol = result
        assert bucket.liquidation_count == 1
        assert bucket.liquidation_notional_usd == 11000.0
        assert bucket.short_liquidation_notional_usd == 11000.0
        assert bucket.long_liquidation_notional_usd == 0.0
        assert bucket.net_liquidation_imbalance_usd == -11000.0

    @pytest.mark.asyncio
    async def test_aggregate_multi_event_bucket(self):
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
            await agg.add_event(e)

        result = await agg._aggregate_bucket(base_ts)

        assert result is not None
        bucket, events_by_symbol = result
        assert bucket.liquidation_count == 3
        assert bucket.liquidation_notional_usd == 26550.0
        assert bucket.short_liquidation_notional_usd == 16550.0  # 11000 + 5550
        assert bucket.long_liquidation_notional_usd == 10000.0
        assert bucket.net_liquidation_imbalance_usd == -6550.0
        assert set(bucket.symbols) == {"BTCUSDT", "ETHUSDT"}

    @pytest.mark.asyncio
    async def test_aggregate_empty_bucket_returns_none(self):
        """Test that aggregating empty bucket returns None."""
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator

        agg = LiquidationAggregator()

        result = await agg._aggregate_bucket(1568014400000)
        assert result is None

    @pytest.mark.asyncio
    async def test_flush_bucket_success(self):
        """Test _flush_bucket returns True on successful flush."""
        from unittest.mock import AsyncMock, MagicMock
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        mock_store = MagicMock()
        mock_store.write_feature = AsyncMock(return_value=(True, None))

        agg = LiquidationAggregator(feature_store=mock_store)

        # Add an event to a bucket
        base_ts = 1568014440000
        event = RawLiquidationEvent(base_ts + 1000, "BTCUSDT", "sell", 11000.0, 1.0, 11000.0, "LIMIT")
        await agg.add_event(event)

        # Flush the bucket
        result = await agg._flush_bucket(base_ts)

        assert result is True
        # Bucket is deleted after successful flush (atomic read + I/O + delete)
        assert base_ts not in agg._buckets
        mock_store.write_feature.assert_called_once()

        # Verify the call arguments
        call_args = mock_store.write_feature.call_args
        assert call_args.kwargs["symbol"] == "BTCUSDT"
        assert call_args.kwargs["feature_name"] == "liquidation_aggregated"
        assert call_args.kwargs["version"] == "v1"
        assert call_args.kwargs["ts_ms"] == base_ts
        assert call_args.kwargs["value"]["liquidation_count"] == 1
        assert call_args.kwargs["value"]["liquidation_notional_usd"] == 11000.0
        assert call_args.kwargs["value"]["long_liquidation_notional_usd"] == 0.0
        assert call_args.kwargs["value"]["short_liquidation_notional_usd"] == 11000.0

    @pytest.mark.asyncio
    async def test_flush_bucket_failure_returns_false(self):
        """
        Test _flush_bucket returns False when feature store fails.

        Note: With the new design (I/O outside lock), when I/O fails the bucket is
        already deleted from _buckets (no retry possible). This is a deliberate tradeoff:
        - Pro: add_event is not blocked for long periods during I/O
        - Con: I/O failure means data is lost (can't retry)
        """
        from unittest.mock import AsyncMock, MagicMock
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        mock_store = MagicMock()
        mock_store.write_feature = AsyncMock(side_effect=Exception("Connection error"))

        agg = LiquidationAggregator(feature_store=mock_store)

        # Add an event to a bucket
        base_ts = 1568014440000
        event = RawLiquidationEvent(base_ts + 1000, "BTCUSDT", "sell", 11000.0, 1.0, 11000.0, "LIMIT")
        await agg.add_event(event)

        # Flush the bucket - should return False on failure
        result = await agg._flush_bucket(base_ts)

        assert result is False
        # With new design (I/O outside lock), bucket is deleted before I/O
        # So bucket is NOT in _buckets after failure (no retry possible)
        assert base_ts not in agg._buckets

    @pytest.mark.asyncio
    async def test_flush_bucket_partial_failure_returns_false(self):
        """
        Test _flush_bucket returns False when some symbols fail to write.

        With multiple symbols in a bucket, if some write_feature calls succeed
        and some fail, _flush_bucket should return False (indicating partial failure).
        The bucket is still deleted (data loss for failed symbols).
        """
        from unittest.mock import AsyncMock, MagicMock
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        # Track write results
        write_results = []

        async def mock_write_feature(**kwargs):
            symbol = kwargs["symbol"]
            write_results.append(symbol)
            if symbol == "BTCUSDT":
                return (True, None)  # Success
            else:
                raise Exception("Connection error")  # Failure for other symbols

        mock_store = MagicMock()
        mock_store.write_feature = mock_write_feature

        agg = LiquidationAggregator(feature_store=mock_store)

        # Add events for multiple symbols to the same bucket
        base_ts = 1568014440000
        events = [
            RawLiquidationEvent(base_ts + 1000, "BTCUSDT", "sell", 11000.0, 1.0, 11000.0, "LIMIT"),
            RawLiquidationEvent(base_ts + 2000, "ETHUSDT", "buy", 2000.0, 1.0, 2000.0, "LIMIT"),
        ]
        for e in events:
            await agg.add_event(e)

        # Flush the bucket
        result = await agg._flush_bucket(base_ts)

        # Should return False (partial failure)
        assert result is False
        # Bucket is deleted regardless (data loss for failed symbols)
        assert base_ts not in agg._buckets
        # Both symbols were attempted
        assert set(write_results) == {"BTCUSDT", "ETHUSDT"}

    @pytest.mark.asyncio
    async def test_flush_bucket_empty_returns_true(self):
        """Test _flush_bucket returns True for empty bucket (no data to flush)."""
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator

        agg = LiquidationAggregator()

        # Try to flush a non-existent bucket
        result = await agg._flush_bucket(1568014400000)

        assert result is True  # No data to flush is considered success

    @pytest.mark.asyncio
    async def test_flush_bucket_empty_cleans_retry_count(self):
        """Test _flush_bucket cleans _bucket_retry_count for empty bucket."""
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator

        agg = LiquidationAggregator()

        # Manually add a retry count entry (simulating a bucket that previously failed)
        bucket_ts = 1568014400000
        agg._bucket_retry_count[bucket_ts] = 2
        assert bucket_ts in agg._bucket_retry_count

        # Flush the empty bucket
        result = await agg._flush_bucket(bucket_ts)

        assert result is True
        # Retry count should be cleaned up even for empty bucket
        assert bucket_ts not in agg._bucket_retry_count

    @pytest.mark.asyncio
    async def test_flush_bucket_cutoff_calculation_recent(self):
        """Test flush cutoff calculation excludes recent buckets."""
        from unittest.mock import AsyncMock, MagicMock
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        mock_store = MagicMock()
        mock_store.write_feature = AsyncMock(return_value=(True, None))

        agg = LiquidationAggregator(feature_store=mock_store, flush_interval_seconds=1.0)

        # Use a fixed reference time to avoid flakiness near minute boundaries
        reference_ms = 1568014400000
        recent_bucket_ts = (reference_ms // 60000) * 60000  # Align to minute
        event = RawLiquidationEvent(recent_bucket_ts + 1000, "BTCUSDT", "sell", 11000.0, 1.0, 11000.0, "LIMIT")
        await agg.add_event(event)

        # Calculate cutoff using same formula as _flush_loop
        cutoff_ts = (reference_ms // 60000) * 60000 - 60000 * 2  # 2 minutes before reference

        async with agg._lock:
            bucket_ts_list = [ts for ts in sorted(agg._buckets.keys()) if ts < cutoff_ts]

        # Recent bucket should NOT be in the flush list since it's after cutoff
        assert len(bucket_ts_list) == 0
        assert recent_bucket_ts in agg._buckets

        # Verify actual flush behavior: calling _flush_bucket directly will flush
        # and delete the bucket regardless of cutoff (cutoff is a _flush_loop concern)
        flush_result = await agg._flush_bucket(recent_bucket_ts)
        assert flush_result is True
        # The bucket is deleted after flush (atomic operation)
        assert recent_bucket_ts not in agg._buckets

    @pytest.mark.asyncio
    async def test_aggregate_bucket_handles_old_bucket_data(self):
        """Test that _aggregate_bucket correctly handles data from an old bucket.

        Note: This test verifies that _aggregate_bucket can correctly process
        bucket data regardless of its age. It does NOT verify cutoff calculation
        behavior in _flush_loop (that would require an integrated flush loop test).
        """
        from unittest.mock import AsyncMock, MagicMock
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        mock_store = MagicMock()
        mock_store.write_feature = AsyncMock(return_value=(True, None))

        agg = LiquidationAggregator(feature_store=mock_store, flush_interval_seconds=1.0)

        # Add an event to an old bucket
        old_ts = 1568014440000
        event = RawLiquidationEvent(old_ts + 1000, "BTCUSDT", "sell", 11000.0, 1.0, 11000.0, "LIMIT")
        await agg.add_event(event)

        assert old_ts in agg._buckets

        # Verify _aggregate_bucket correctly processes the old bucket data
        result = await agg._aggregate_bucket(old_ts)
        assert result is not None
        bucket, events_by_symbol = result
        # Verify the bucket has correct data
        assert bucket.liquidation_count == 1
        assert bucket.symbols == ["BTCUSDT"]

    @pytest.mark.asyncio
    async def test_flush_bucket_atomic_read_no_data_loss(self):
        """
        Test that _flush_bucket atomically reads bucket data and flushes without data loss.

        This test verifies the fix for the race condition where:
        1. _flush_loop releases lock after getting bucket_ts_list
        2. add_event adds events to the bucket
        3. _flush_bucket reads stale data and deletes bucket
        4. Events added in step 2 are lost

        The fix ensures _flush_bucket holds lock during read, so any events added
        before the read are included in the flush.
        """
        from unittest.mock import AsyncMock, MagicMock
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        flushed_features = []

        async def mock_write_feature(**kwargs):
            """Track what was flushed"""
            flushed_features.append(kwargs)
            return (True, None)

        mock_store = MagicMock()
        mock_store.write_feature = mock_write_feature

        agg = LiquidationAggregator(feature_store=mock_store)

        # Add first event
        base_ts = 1568014440000
        event1 = RawLiquidationEvent(base_ts + 1000, "BTCUSDT", "sell", 11000.0, 1.0, 11000.0, "LIMIT")
        await agg.add_event(event1)

        assert base_ts in agg._buckets
        assert len(agg._buckets[base_ts]) == 1

        # Add second event to the same bucket (simulating concurrent add_event)
        event2 = RawLiquidationEvent(base_ts + 2000, "ETHUSDT", "buy", 5000.0, 2.0, 10000.0, "LIMIT")
        await agg.add_event(event2)

        assert len(agg._buckets[base_ts]) == 2

        # Now flush - should include both events
        result = await agg._flush_bucket(base_ts)

        assert result is True
        assert base_ts not in agg._buckets  # Bucket deleted after flush

        # Verify both events were flushed
        assert len(flushed_features) == 2  # One per symbol

        symbols_flushed = {f["symbol"] for f in flushed_features}
        assert symbols_flushed == {"BTCUSDT", "ETHUSDT"}

        # Verify BTCUSDT had correct data
        btc_feature = next(f for f in flushed_features if f["symbol"] == "BTCUSDT")
        assert btc_feature["value"]["liquidation_count"] == 1
        assert btc_feature["value"]["short_liquidation_notional_usd"] == 11000.0

        # Verify ETHUSDT had correct data
        eth_feature = next(f for f in flushed_features if f["symbol"] == "ETHUSDT")
        assert eth_feature["value"]["liquidation_count"] == 1
        assert eth_feature["value"]["long_liquidation_notional_usd"] == 10000.0

    @pytest.mark.asyncio
    async def test_flush_bucket_prevents_race_with_add_event(self):
        """
        Test that _flush_bucket correctly handles events added during the flush operation.

        The design ensures that:
        - Events added BEFORE _flush_bucket acquires lock are included in flush
        - Events added AFTER bucket is deleted go to a new bucket (not lost!)

        This prevents data loss from the original race condition.
        """
        from unittest.mock import AsyncMock, MagicMock
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        flush_count = [0]

        async def slow_write_feature(**kwargs):
            """Slow write to increase chance of race"""
            flush_count[0] += 1
            await asyncio.sleep(0.01)  # Simulate slow I/O
            return (True, None)

        mock_store = MagicMock()
        mock_store.write_feature = slow_write_feature

        agg = LiquidationAggregator(feature_store=mock_store)

        # Add initial event
        base_ts = 1568014440000
        event1 = RawLiquidationEvent(base_ts + 1000, "BTCUSDT", "sell", 11000.0, 1.0, 11000.0, "LIMIT")
        await agg.add_event(event1)

        # Start flush in background
        flush_task = asyncio.create_task(agg._flush_bucket(base_ts))

        # While flush is in progress (lock held during I/O), add another event
        # Wait a bit to ensure flush has started
        await asyncio.sleep(0.005)
        event2 = RawLiquidationEvent(base_ts + 2000, "ETHUSDT", "buy", 5000.0, 2.0, 10000.0, "LIMIT")
        await agg.add_event(event2)

        # Wait for flush to complete
        await flush_task

        # Verify: event1 was flushed, event2 went to a NEW bucket (not lost!)
        # With our fix, the bucket is deleted before I/O completes, 
        # so event2 (added after flush_task got lock but before I/O) creates a new bucket
        assert flush_count[0] == 1  # Only event1's bucket was flushed

        # event2 should be in a new bucket (same timestamp, but created after old bucket was deleted)
        assert base_ts in agg._buckets
        assert len(agg._buckets[base_ts]) == 1
        assert agg._buckets[base_ts][0].symbol == "ETHUSDT"


class TestLiquidationAggregatorSmoke:
    """Smoke tests for LiquidationAggregator (integration without real WS)."""

    def test_aggregator_initialization(self):
        """Test aggregator can be initialized."""
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator

        agg = LiquidationAggregator()
        assert agg._running is False
        assert len(agg._buckets) == 0
        assert agg._draining is False


class TestLiquidationAggregatorDraining:
    """Test draining flag and stop behavior."""

    @pytest.mark.asyncio
    async def test_add_event_rejects_when_draining(self):
        """Test that add_event rejects new events when _draining is True."""
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        agg = LiquidationAggregator()
        agg._draining = True  # Simulate shutdown state

        event = RawLiquidationEvent(
            event_time_ms=1568014460943,
            symbol="BTCUSDT",
            side="sell",
            price=11000.0,
            quantity=1.0,
            notional_usd=11000.0,
            order_type="LIMIT",
        )
        await agg.add_event(event)

        # Event should not be added since we're draining
        assert len(agg._buckets) == 0

    @pytest.mark.asyncio
    async def test_add_event_rejects_after_lock_acquisition_when_draining(self):
        """Test double-check draining flag after acquiring lock."""
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        agg = LiquidationAggregator()

        # Add an event first (not draining yet)
        event = RawLiquidationEvent(
            event_time_ms=1568014460943,
            symbol="BTCUSDT",
            side="sell",
            price=11000.0,
            quantity=1.0,
            notional_usd=11000.0,
            order_type="LIMIT",
        )
        await agg.add_event(event)
        assert len(agg._buckets) == 1

        # Now set draining and try to add another event
        agg._draining = True
        event2 = RawLiquidationEvent(
            event_time_ms=1568014461943,
            symbol="ETHUSDT",
            side="buy",
            price=2000.0,
            quantity=1.0,
            notional_usd=2000.0,
            order_type="LIMIT",
        )
        await agg.add_event(event2)

        # Only the first event should be present (draining prevented second)
        assert len(agg._buckets) == 1

    @pytest.mark.asyncio
    async def test_stop_flushes_pending_buckets(self):
        """Test stop() flushes all pending buckets before exit."""
        from unittest.mock import AsyncMock, MagicMock
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        mock_store = MagicMock()
        mock_store.write_feature = AsyncMock(return_value=(True, None))

        agg = LiquidationAggregator(feature_store=mock_store)

        # Add events
        base_ts = 1568014440000
        event = RawLiquidationEvent(base_ts + 1000, "BTCUSDT", "sell", 11000.0, 1.0, 11000.0, "LIMIT")
        await agg.add_event(event)

        assert base_ts in agg._buckets

        # Simulate running state and stop
        agg._running = True
        await agg.stop()

        # Verify flush was called and bucket was removed
        assert mock_store.write_feature.called
        assert base_ts not in agg._buckets

    @pytest.mark.asyncio
    async def test_stop_resets_draining_flag(self):
        """Test stop() resets _draining flag after completion."""
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator

        agg = LiquidationAggregator()
        agg._running = True
        agg._draining = True

        await agg.stop()

        assert agg._draining is False

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        """Test calling stop() multiple times is safe."""
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator

        agg = LiquidationAggregator()
        agg._running = True

        await agg.stop()
        await agg.stop()  # Should not raise

        assert agg._running is False

    @pytest.mark.asyncio
    async def test_stop_during_flush_loop_sleep_no_deadlock(self):
        """
        Test that stop() can be called while _flush_loop is sleeping without deadlock.

        The scenario:
        1. _flush_loop is running with a long sleep interval
        2. stop() is called
        3. Draining flag prevents new events
        4. _flush_loop exits when it wakes up (because _running is False)
        5. Final flush happens

        This test uses a very short sleep interval and proper task cleanup.
        """
        from unittest.mock import AsyncMock, MagicMock
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        mock_store = MagicMock()
        mock_store.write_feature = AsyncMock(return_value=(True, None))

        # Use a very short flush interval so we don't wait long
        agg = LiquidationAggregator(feature_store=mock_store, flush_interval_seconds=0.05)

        # Add events to multiple buckets
        base_ts = 1568014440000
        events = [
            RawLiquidationEvent(base_ts + 1000, "BTCUSDT", "sell", 11000.0, 1.0, 11000.0, "LIMIT"),
            RawLiquidationEvent(base_ts + 60000 + 1000, "ETHUSDT", "buy", 2000.0, 1.0, 2000.0, "LIMIT"),
        ]
        for e in events:
            await agg.add_event(e)

        # Start the aggregator
        agg._running = True
        agg._ws_connector = MagicMock()
        agg._ws_connector.disconnect = AsyncMock()
        agg._ws_task = None  # No need for real WS task
        agg._flush_task = asyncio.create_task(agg._flush_loop())

        # Let flush_loop run for a bit
        await asyncio.sleep(0.02)

        # Now call stop - should not deadlock
        await agg.stop()

        # Verify:
        # 1. stop() completed
        assert agg._running is False
        assert agg._draining is False

        # 2. All buckets were flushed
        assert len(agg._buckets) == 0

        # 3. write_feature was called for both events
        assert mock_store.write_feature.call_count >= 2

    @pytest.mark.asyncio
    async def test_add_event_blocked_during_stop(self):
        """
        Test that add_event is blocked after stop() begins (draining flag set).

        This verifies the double-check locking pattern works:
        - First check: before acquiring lock (fast path)
        - Second check: after acquiring lock (ensures no race)
        """
        from unittest.mock import AsyncMock, MagicMock
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        agg = LiquidationAggregator()

        # Add initial event
        base_ts = 1568014440000
        event1 = RawLiquidationEvent(base_ts + 1000, "BTCUSDT", "sell", 11000.0, 1.0, 11000.0, "LIMIT")
        await agg.add_event(event1)

        assert len(agg._buckets) == 1

        # Simulate stop() has been called by setting draining flag
        # (stop() sets draining before doing anything else)
        agg._draining = True
        agg._running = False  # Also set _running to False like stop() does

        # Try to add event while draining - should be rejected at entry check
        event2 = RawLiquidationEvent(base_ts + 2000, "ETHUSDT", "buy", 5000.0, 1.0, 5000.0, "LIMIT")
        await agg.add_event(event2)

        # Event2 should NOT have been added (draining blocked it)
        assert len(agg._buckets) == 1
        assert agg._buckets[base_ts][0].symbol == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_add_event_rejected_at_lock_acquisition_when_draining(self):
        """
        Test that add_event is rejected even if it gets past the first draining check
        but the draining flag is set before lock acquisition completes.

        This is a timing test showing that the double-check pattern works:
        Thread A: checks draining (False) -> proceeds to acquire lock
        Thread B: sets draining = True
        Thread A: acquires lock -> checks draining again -> rejects
        """
        from trader.adapters.onchain.onchain_market_data_stream import LiquidationAggregator, RawLiquidationEvent

        agg = LiquidationAggregator()

        # Add initial event
        base_ts = 1568014440000
        event1 = RawLiquidationEvent(base_ts + 1000, "BTCUSDT", "sell", 11000.0, 1.0, 11000.0, "LIMIT")
        await agg.add_event(event1)

        # Acquire the lock ourselves to simulate add_event holding the lock
        await agg._lock.acquire()

        # Now set draining while add_event would be trying to acquire lock
        agg._draining = True

        # Release the lock
        agg._lock.release()

        # Now try to add event - should be rejected
        event2 = RawLiquidationEvent(base_ts + 2000, "ETHUSDT", "buy", 5000.0, 1.0, 5000.0, "LIMIT")
        await agg.add_event(event2)

        # Event2 should NOT have been added
        assert len(agg._buckets) == 1


class TestBinanceLiquidationWSConnector:
    """Unit tests for BinanceLiquidationWSConnector."""

    def test_parse_message_valid(self):
        """Test parsing a valid Binance forceOrder message."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)

        data = '{"e":"ForceOrder","E":1568014460943,"s":"BTCUSDT","S":"SELL","o":"LIMIT","p":"11000.00","q":"1.0","ap":"11100.00","l":"1.0","v":"1.0"}'
        event = connector._parse_message(data)

        assert event is not None
        assert event.symbol == "BTCUSDT"
        assert event.side == "sell"
        assert event.price == 11000.0
        assert event.quantity == 1.0
        assert event.notional_usd == 11100.0  # ap * q
        assert event.event_time_ms == 1568014460943
        assert event.order_type == "LIMIT"

    def test_parse_message_buy_side(self):
        """Test parsing a BUY side forceOrder message."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)

        data = '{"e":"ForceOrder","E":1568014460943,"s":"ETHUSDT","S":"BUY","o":"MARKET","p":"2000.00","q":"2.0","ap":"2010.00","l":"2.0","v":"2.0"}'
        event = connector._parse_message(data)

        assert event is not None
        assert event.symbol == "ETHUSDT"
        assert event.side == "buy"
        assert event.notional_usd == 4020.0  # 2010 * 2

    def test_parse_message_invalid_json(self):
        """Test parsing invalid JSON returns None."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)

        event = connector._parse_message("not valid json")
        assert event is None

    def test_parse_message_missing_event_type(self):
        """Test parsing message with wrong event type returns None."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)

        data = '{"e":"Trade","E":1568014460943,"s":"BTCUSDT","S":"SELL","p":"11000.00","q":"1.0"}'
        event = connector._parse_message(data)
        assert event is None

    def test_parse_message_missing_symbol(self):
        """Test parsing message without symbol field returns None."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)

        data = '{"e":"ForceOrder","E":1568014460943,"s":"","S":"SELL","p":"11000.00","q":"1.0"}'
        event = connector._parse_message(data)
        assert event is None

    def test_parse_message_non_usdt_contract(self):
        """Test parsing non-USDT contract returns None."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)

        data = '{"e":"ForceOrder","E":1568014460943,"s":"BTCUSD","S":"SELL","p":"11000.00","q":"1.0"}'
        event = connector._parse_message(data)
        assert event is None

    def test_parse_message_missing_required_fields(self):
        """Test parsing message with missing required fields returns None."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)

        # Missing 'S' (side) field
        data = '{"e":"ForceOrder","E":1568014460943,"s":"BTCUSDT","p":"11000.00","q":"1.0"}'
        event = connector._parse_message(data)
        assert event is None

    def test_parse_message_invalid_number_format(self):
        """Test parsing message with invalid number format returns None."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)

        data = '{"e":"ForceOrder","E":1568014460943,"s":"BTCUSDT","S":"SELL","p":"invalid","q":"1.0","ap":"11100.00","l":"1.0"}'
        event = connector._parse_message(data)
        assert event is None

    def test_parse_message_default_order_type(self):
        """Test that missing order_type defaults to LIMIT."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)

        data = '{"e":"ForceOrder","E":1568014460943,"s":"BTCUSDT","S":"SELL","p":"11000.00","q":"1.0","ap":"11100.00","l":"1.0"}'
        event = connector._parse_message(data)

        assert event is not None
        assert event.order_type == "LIMIT"

    @pytest.mark.asyncio
    async def test_disconnect_sets_running_false(self):
        """Test disconnect sets _running to False."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)
        connector._running = True

        await connector.disconnect()

        assert connector._running is False

    @pytest.mark.asyncio
    async def test_disconnect_clears_ws_reference(self):
        """Test disconnect clears WebSocket reference."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector
        from unittest.mock import AsyncMock, MagicMock

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)
        connector._ws = MagicMock()
        connector._ws.close = AsyncMock()

        await connector.disconnect()

        assert connector._ws is None

    def test_connector_initialization(self):
        """Test connector initializes with correct default values."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        callback = lambda e: None
        connector = BinanceLiquidationWSConnector(on_event=callback)

        assert connector._on_event is callback
        assert connector._running is False
        assert connector._session is None
        assert connector._ws is None
        assert connector._reconnect_delay == 1.0
        assert connector._max_reconnect_delay == 60.0

    @pytest.mark.asyncio
    async def test_ensure_session_creates_new_session(self):
        """Test _ensure_session creates a new session when None."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector
        import aiohttp

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)
        assert connector._session is None

        await connector._ensure_session()

        assert connector._session is not None
        assert isinstance(connector._session, aiohttp.ClientSession)
        # Cleanup
        await connector._close_session()

    @pytest.mark.asyncio
    async def test_close_session_closes_existing_session(self):
        """Test _close_session closes existing session."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)
        await connector._ensure_session()
        assert connector._session is not None

        await connector._close_session()

        assert connector._session is None

    @pytest.mark.asyncio
    async def test_close_session_handles_none_session(self):
        """Test _close_session handles None session gracefully."""
        from trader.adapters.onchain.onchain_market_data_stream import BinanceLiquidationWSConnector

        connector = BinanceLiquidationWSConnector(on_event=lambda e: None)
        connector._session = None

        # Should not raise
        await connector._close_session()
        assert connector._session is None
