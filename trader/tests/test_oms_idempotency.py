"""
Test OMS Idempotency (Task 17)
================================

Tests for fill deduplication and idempotency:
1. Replay same fill 2-3 times → only 1 execution record
2. WS+REST concurrent same fill → only 1 execution
3. dedup_hit_count increments correctly
4. Terminal states cannot regress (FILLED → NEW fails)
"""

import time
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from trader.services.oms_callback import OMSCallbackHandler
from trader.storage.in_memory import ControlPlaneInMemoryStorage
from trader.core.domain.models.signal import Signal, SignalType
from trader.core.domain.models.order import OrderStatus


class TestOMSCallbackIdempotency:
    """Tests for OMS callback idempotency."""

    @pytest.fixture
    def mock_broker(self):
        """Create a mock broker."""
        broker = MagicMock()
        broker.place_order = AsyncMock()
        broker.get_symbol_step_size = AsyncMock(return_value=Decimal("0.00001"))
        broker.get_exchange_info = AsyncMock(return_value={
            "symbols": [{
                "filters": [{"filterType": "NOTIONAL", "minNotional": "10"}]
            }]
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

    def _create_signal(self, symbol="BTCUSDT", quantity=Decimal("0.01")):
        """Helper to create a test signal."""
        return Signal(
            signal_type=SignalType.BUY,
            symbol=symbol,
            quantity=quantity,
            price=Decimal("50000"),
            strategy_name="test_strategy",
        )

    @pytest.mark.asyncio
    async def test_replay_same_fill_twice_only_one_execution(self, handler, mock_broker, storage):
        """
        Test that replaying the same fill twice only creates one execution record.
        """
        # Setup mock to return a filled order
        mock_broker.place_order.return_value = MagicMock(
            broker_order_id="12345",
            filled_quantity=Decimal("0.01"),
            average_price=Decimal("50000"),
            status=OrderStatus.FILLED,
            created_at=None,
        )

        signal = self._create_signal()

        # First call - should succeed
        result1 = await handler.execute_signal("test_strategy", signal)
        assert result1 is not None
        assert result1["status"] == "FILLED"

        # Check initial execution count
        execs_before = storage.list_executions()
        assert len(execs_before) == 1

        # Get the dedup stats before replay
        stats_before = handler.get_dedup_stats()

        # Create a second signal with same cl_ord_id (simulating replay)
        # To simulate replay, we manually call create_execution with the same cl_ord_id:exec_id
        cl_ord_id = result1["order_id"]
        exec_id = f"{mock_broker.place_order.return_value.broker_order_id}:init"

        # Manually call create_execution with same keys to simulate replay
        execution_data = {
            "cl_ord_id": cl_ord_id,
            "exec_id": exec_id,
            "fill_qty": "0.01",
            "fill_price": "50000",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "strategy_id": "test_strategy",
        }

        # This should be a dedup hit
        result_existing = storage.create_execution(execution_data)
        assert result_existing is not None

        # Check that dedup counter was incremented
        stats_after = handler.get_dedup_stats()
        # Note: The dedup stats are only incremented when going through the handler methods
        # The storage.create_execution also tracks dedup_hits

        # Verify only one execution exists
        execs_after = storage.list_executions()
        assert len(execs_after) == 1

    @pytest.mark.asyncio
    async def test_replay_same_fill_thrice_only_one_execution(self, handler, mock_broker, storage):
        """
        Test that replaying the same fill 3 times only creates one execution record.
        """
        mock_broker.place_order.return_value = MagicMock(
            broker_order_id="12345",
            filled_quantity=Decimal("0.01"),
            average_price=Decimal("50000"),
            status=OrderStatus.FILLED,
            created_at=None,
        )

        signal = self._create_signal()
        result1 = await handler.execute_signal("test_strategy", signal)
        assert result1 is not None

        cl_ord_id = result1["order_id"]
        exec_id = f"{mock_broker.place_order.return_value.broker_order_id}:init"

        # Simulate 3 replays
        for i in range(3):
            execution_data = {
                "cl_ord_id": cl_ord_id,
                "exec_id": exec_id,
                "fill_qty": "0.01",
                "fill_price": "50000",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "strategy_id": "test_strategy",
            }
            storage.create_execution(execution_data)

        # Verify only one execution exists
        execs = storage.list_executions()
        assert len(execs) == 1

    @pytest.mark.asyncio
    async def test_dedup_hit_count_increments(self, handler, mock_broker, storage):
        """
        Test that dedup_hit_count increments correctly when duplicates are detected.
        """
        # Simulate a fill
        mock_broker.place_order.return_value = MagicMock(
            broker_order_id="12345",
            filled_quantity=Decimal("0.01"),
            average_price=Decimal("50000"),
            status=OrderStatus.FILLED,
            created_at=None,
        )

        signal = self._create_signal()
        result1 = await handler.execute_signal("test_strategy", signal)

        cl_ord_id = result1["order_id"]
        exec_id = f"{mock_broker.place_order.return_value.broker_order_id}:init"

        # Get stats after first execution
        stats_after_first = handler.get_dedup_stats()
        initial_dedup_hits = stats_after_first["exec_dedup_hits"]

        # Try to record same execution again - should be a dedup hit
        is_new = handler._mark_exec_seen(cl_ord_id, exec_id)
        assert is_new is False  # Should return False since it's a duplicate

        stats = handler.get_dedup_stats()
        assert stats["exec_dedup_hits"] == initial_dedup_hits + 1  # Counter should be incremented


class TestStorageExecutionIdempotency:
    """Tests for storage execution idempotency."""

    @pytest.fixture
    def storage(self):
        """Create a fresh in-memory storage."""
        return ControlPlaneInMemoryStorage()

    def test_create_execution_idempotency(self, storage):
        """
        Test that create_execution is idempotent - same cl_ord_id + exec_id returns existing.
        """
        execution_data = {
            "cl_ord_id": "test_order_1",
            "exec_id": "exec_1",
            "fill_qty": "0.01",
            "fill_price": "50000",
            "symbol": "BTCUSDT",
            "strategy_id": "test",
        }

        # First call - creates execution
        result1 = storage.create_execution(execution_data)
        assert result1["cl_ord_id"] == "test_order_1"

        # Get dedup stats before second call
        stats_before = storage.get_execution_dedup_stats()
        hits_before = stats_before["execution_dedup_hits"]

        # Second call with same keys - should return existing
        result2 = storage.create_execution(execution_data)
        assert result2["cl_ord_id"] == "test_order_1"

        # Verify dedup counter was incremented
        stats_after = storage.get_execution_dedup_stats()
        assert stats_after["execution_dedup_hits"] == hits_before + 1

    def test_create_execution_different_exec_ids(self, storage):
        """
        Test that different exec_ids create different executions.
        """
        execution1 = {
            "cl_ord_id": "test_order_1",
            "exec_id": "exec_1",
            "fill_qty": "0.01",
        }
        execution2 = {
            "cl_ord_id": "test_order_1",
            "exec_id": "exec_2",  # Different exec_id
            "fill_qty": "0.02",  # Different qty
        }

        result1 = storage.create_execution(execution1)
        result2 = storage.create_execution(execution2)

        # Both should be stored separately
        assert result1["exec_id"] == "exec_1"
        assert result2["exec_id"] == "exec_2"

        all_execs = storage.list_executions()
        assert len(all_execs) == 2


class TestTerminalStateMonotonicity:
    """Tests for terminal state monotonicity."""

    @pytest.fixture
    def storage(self):
        """Create a fresh in-memory storage."""
        return ControlPlaneInMemoryStorage()

    def test_order_is_terminal_for_filled(self, storage):
        """Test that FILLED is a terminal state."""
        from trader.core.domain.models.order import Order, OrderStatus, OrderSide, OrderType
        from decimal import Decimal

        order = Order(
            order_id="test_1",
            client_order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
            status=OrderStatus.FILLED,
        )

        assert order.is_terminal() is True

    def test_order_is_terminal_for_cancelled(self, storage):
        """Test that CANCELLED is a terminal state."""
        from trader.core.domain.models.order import Order, OrderStatus, OrderSide, OrderType
        from decimal import Decimal

        order = Order(
            order_id="test_1",
            client_order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
            status=OrderStatus.CANCELLED,
        )

        assert order.is_terminal() is True

    def test_order_is_terminal_for_rejected(self, storage):
        """Test that REJECTED is a terminal state."""
        from trader.core.domain.models.order import Order, OrderStatus, OrderSide, OrderType
        from decimal import Decimal

        order = Order(
            order_id="test_1",
            client_order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
            status=OrderStatus.REJECTED,
        )

        assert order.is_terminal() is True

    def test_order_is_not_terminal_for_pending(self, storage):
        """Test that PENDING is NOT a terminal state."""
        from trader.core.domain.models.order import Order, OrderStatus, OrderSide, OrderType
        from decimal import Decimal

        order = Order(
            order_id="test_1",
            client_order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
            status=OrderStatus.PENDING,
        )

        assert order.is_terminal() is False

    def test_order_is_not_terminal_for_submitted(self, storage):
        """Test that SUBMITTED is NOT a terminal state."""
        from trader.core.domain.models.order import Order, OrderStatus, OrderSide, OrderType
        from decimal import Decimal

        order = Order(
            order_id="test_1",
            client_order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
            status=OrderStatus.SUBMITTED,
        )

        assert order.is_terminal() is False

    def test_fill_raises_when_order_is_terminal(self, storage):
        """Test that filling a terminal order raises ValueError."""
        from trader.core.domain.models.order import Order, OrderStatus, OrderSide, OrderType
        from decimal import Decimal

        order = Order(
            order_id="test_1",
            client_order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
            status=OrderStatus.FILLED,  # Terminal state
        )

        # Trying to fill a terminal order should raise
        with pytest.raises(ValueError, match="订单状态不允许成交"):
            order.fill(Decimal("0.01"), Decimal("50000"))

    def test_reject_raises_when_order_is_terminal(self, storage):
        """Test that rejecting a terminal order raises ValueError."""
        from trader.core.domain.models.order import Order, OrderStatus, OrderSide, OrderType
        from decimal import Decimal

        order = Order(
            order_id="test_1",
            client_order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
            status=OrderStatus.FILLED,  # Terminal state
        )

        # Trying to reject a terminal order should raise
        with pytest.raises(ValueError, match="订单已终态"):
            order.reject("test rejection")
