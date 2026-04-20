"""
Test Runtime Observability (Task 19)
===================================

Tests for runtime metrics and observability:
1. OMSCallbackHandler tracks order submission metrics
2. Reject reason counts are tracked
3. Dedup stats include Task 19 metrics
4. MonitorSnapshot includes runtime observability fields
5. Alert rules include runtime threshold rules
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal

from trader.services.oms_callback import OMSCallbackHandler
from trader.storage.in_memory import ControlPlaneInMemoryStorage
from trader.core.domain.models.signal import Signal, SignalType


class TestOMSCallbackObservableMetrics:
    """Tests for OMS callback observable metrics."""

    @pytest.fixture
    def mock_broker(self):
        """Create a mock broker."""
        broker = MagicMock()
        broker.place_order = AsyncMock()
        broker.get_symbol_step_size = AsyncMock(return_value=Decimal("0.00001"))
        broker.get_exchange_info = AsyncMock(return_value={
            "symbols": [{"filters": [{"filterType": "NOTIONAL", "minNotional": "10"}]}]
        })
        broker._fetch_account = AsyncMock()
        broker._account_cache = {
            "balances": [
                {"asset": "USDT", "free": "10000", "locked": "0"},
                {"asset": "BTC", "free": "1", "locked": "0"},
            ]
        }
        return broker

    @pytest.fixture
    def storage(self):
        """Create a fresh in-memory storage."""
        return ControlPlaneInMemoryStorage()

    @pytest.fixture
    def handler(self, mock_broker, storage):
        """Create an OMS callback handler."""
        return OMSCallbackHandler(
            broker=mock_broker,
            storage=storage,
            live_trading_enabled=True,
        )

    def _create_signal(self, symbol="BTCUSDT", quantity=Decimal("0.01"), price=Decimal("50000")):
        """Helper to create a test signal."""
        return Signal(
            signal_type=SignalType.BUY,
            symbol=symbol,
            quantity=quantity,
            price=price,
            strategy_name="test_strategy",
        )

    def test_get_dedup_stats_includes_order_metrics(self, handler):
        """Test that get_dedup_stats includes Task 19 order metrics."""
        stats = handler.get_dedup_stats()
        
        assert "order_submit_ok" in stats
        assert "order_submit_reject" in stats
        assert "order_submit_error" in stats
        assert "reject_reason_counts" in stats
        assert "fill_latency_ms_avg" in stats
        assert "fill_latency_count" in stats

    def test_initial_metrics_are_zero(self, handler):
        """Test that initial metrics are zero."""
        stats = handler.get_dedup_stats()
        
        assert stats["order_submit_ok"] == 0
        assert stats["order_submit_reject"] == 0
        assert stats["order_submit_error"] == 0
        assert stats["reject_reason_counts"] == {}
        assert stats["fill_latency_count"] == 0

    @pytest.mark.asyncio
    async def test_order_submit_ok_incremented_on_success(self, handler, mock_broker):
        """Test that order_submit_ok is incremented on successful submission."""
        mock_broker.place_order.return_value = MagicMock(
            broker_order_id="12345",
            filled_quantity=Decimal("0"),
            average_price=Decimal("0"),
            status=MagicMock(value="NEW"),
            created_at=None,
        )

        signal = self._create_signal()
        await handler.execute_signal("test_strategy", signal)

        stats = handler.get_dedup_stats()
        assert stats["order_submit_ok"] == 1

    @pytest.mark.asyncio
    async def test_order_submit_reject_incremented_on_rejection(self, handler, mock_broker):
        """Test that order_submit_reject is incremented on rejection."""
        # Disable live trading to trigger rejection
        handler._live_trading_enabled_fn = lambda: False

        signal = self._create_signal()
        with pytest.raises(Exception):  # TradingDisabledError
            await handler.execute_signal("test_strategy", signal)

        stats = handler.get_dedup_stats()
        assert stats["order_submit_reject"] >= 1

    @pytest.mark.asyncio
    async def test_reject_reason_counts_tracked(self, handler, mock_broker):
        """Test that reject reason counts are tracked."""
        # Disable live trading
        handler._live_trading_enabled_fn = lambda: False

        signal = self._create_signal()
        with pytest.raises(Exception):
            await handler.execute_signal("test_strategy", signal)

        stats = handler.get_dedup_stats()
        assert "LIVE_TRADING_DISABLED" in stats["reject_reason_counts"]

    @pytest.mark.asyncio
    async def test_missing_symbol_tracked(self, handler, mock_broker):
        """Test that missing symbol is tracked as rejection."""
        signal = Signal(
            signal_type=SignalType.BUY,
            symbol="",  # Empty symbol
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
            strategy_name="test_strategy",
        )
        
        with pytest.raises(Exception):
            await handler.execute_signal("test_strategy", signal)

        stats = handler.get_dedup_stats()
        assert stats["order_submit_reject"] >= 1

    @pytest.mark.asyncio
    async def test_fill_latency_count_incremented(self, handler, mock_broker):
        """Test that fill_latency_count is incremented when fills occur."""
        mock_broker.place_order.return_value = MagicMock(
            broker_order_id="12345",
            filled_quantity=Decimal("0.01"),
            average_price=Decimal("50000"),
            status=MagicMock(value="FILLED"),
            created_at=None,
        )

        signal = self._create_signal()
        await handler.execute_signal("test_strategy", signal)

        stats = handler.get_dedup_stats()
        assert stats["fill_latency_count"] >= 1


class TestMonitorSnapshotObservabilityFields:
    """Tests for MonitorSnapshot observable fields."""

    def test_monitor_snapshot_has_runtime_fields(self):
        """Test that MonitorSnapshot includes Task 19 runtime fields."""
        from trader.api.models.schemas import MonitorSnapshot

        snapshot = MonitorSnapshot()
        
        # Check all Task 19 fields exist
        assert hasattr(snapshot, "tick_rate")
        assert hasattr(snapshot, "tick_lag_ms")
        assert hasattr(snapshot, "order_submit_ok")
        assert hasattr(snapshot, "order_submit_reject")
        assert hasattr(snapshot, "order_submit_error")
        assert hasattr(snapshot, "reject_reason_counts")
        assert hasattr(snapshot, "fill_latency_ms_avg")
        assert hasattr(snapshot, "fill_latency_count")
        assert hasattr(snapshot, "ws_reconnect_count")
        assert hasattr(snapshot, "cl_ord_id_dedup_hits")
        assert hasattr(snapshot, "exec_dedup_hits")

    def test_monitor_snapshot_default_values(self):
        """Test that MonitorSnapshot defaults are correct."""
        from trader.api.models.schemas import MonitorSnapshot

        snapshot = MonitorSnapshot()
        
        assert snapshot.tick_rate is None
        assert snapshot.tick_lag_ms is None
        assert snapshot.order_submit_ok == 0
        assert snapshot.order_submit_reject == 0
        assert snapshot.order_submit_error == 0
        assert snapshot.reject_reason_counts == {}
        assert snapshot.fill_latency_ms_avg is None
        assert snapshot.fill_latency_count == 0
        assert snapshot.ws_reconnect_count == 0
        assert snapshot.cl_ord_id_dedup_hits == 0
        assert snapshot.exec_dedup_hits == 0


class TestMonitorServiceAlertRules:
    """Tests for MonitorService alert rules."""

    def test_default_alert_rules_include_runtime_rules(self):
        """Test that DEFAULT_ALERT_RULES includes Task 19 runtime rules."""
        from trader.services.monitor_service import MonitorService

        rule_names = [r.rule_name for r in MonitorService.DEFAULT_ALERT_RULES]
        
        # Task 19 runtime alert rules
        assert "tick_lag_high" in rule_names
        assert "order_reject_rate_high" in rule_names
        assert "ws_reconnect_high" in rule_names
        assert "fill_latency_high" in rule_names

    def test_tick_lag_rule_properties(self):
        """Test tick_lag_high rule configuration."""
        from trader.services.monitor_service import MonitorService

        rule = next(r for r in MonitorService.DEFAULT_ALERT_RULES if r.rule_name == "tick_lag_high")
        
        assert rule.metric_key == "tick_lag_ms"
        assert rule.threshold == 1000.0
        assert rule.comparison == "gt"
        assert rule.severity == "HIGH"
        assert rule.cooldown_seconds == 60

    def test_fill_latency_rule_properties(self):
        """Test fill_latency_high rule configuration."""
        from trader.services.monitor_service import MonitorService

        rule = next(r for r in MonitorService.DEFAULT_ALERT_RULES if r.rule_name == "fill_latency_high")
        
        assert rule.metric_key == "fill_latency_ms_avg"
        assert rule.threshold == 500.0
        assert rule.comparison == "gt"
        assert rule.severity == "HIGH"
        assert rule.cooldown_seconds == 60

    def test_ws_reconnect_rule_properties(self):
        """Test ws_reconnect_high rule configuration."""
        from trader.services.monitor_service import MonitorService

        rule = next(r for r in MonitorService.DEFAULT_ALERT_RULES if r.rule_name == "ws_reconnect_high")
        
        assert rule.metric_key == "ws_reconnect_count"
        assert rule.threshold == 5.0
        assert rule.comparison == "gt"
        assert rule.severity == "MEDIUM"
        assert rule.cooldown_seconds == 300
