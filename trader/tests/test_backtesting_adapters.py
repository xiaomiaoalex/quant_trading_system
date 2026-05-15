"""
Backtesting Adapters Unit Tests
================================

Comprehensive unit tests for the backtesting adapter layer covering:
1. Execution Simulator
2. Direction-aware slippage
3. Stop-loss / take-profit triggers
4. Critical next-bar execution behavior

Test Structure:
- Execution Simulator Tests: slippage, next-bar execution, SL/TP triggers
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trader.core.domain.models.order import OrderSide, OrderType
from trader.services.backtesting.execution_simulator import (
    DirectionAwareSlippage,
    ExecutionResult,
    ExecutionSimulator,
    ExitReason,
    NextBarOpenExecutor,
    OrderExecutionConfig,
    PendingOrder,
    PositionState,
    SlippageModel,
    StopLossTakeProfitExecutor,
)
from trader.services.backtesting.ports import OHLCV

# ==================== Fixtures ====================


@pytest.fixture
def sample_ohlcv():
    """Create sample OHLCV data"""
    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        OHLCV(
            timestamp=base_time + timedelta(hours=i),
            open=Decimal("50000"),
            high=Decimal("50500"),
            low=Decimal("49500"),
            close=Decimal("50200"),
            volume=Decimal("100"),
        )
        for i in range(10)
    ]


@pytest.fixture
def ohlcv_factory():
    """Factory for creating OHLCV with customizable values"""

    def _create(
        timestamp=None,
        open_price=Decimal("50000"),
        high=Decimal("50500"),
        low=Decimal("49500"),
        close=Decimal("50200"),
        volume=Decimal("100"),
    ):
        return OHLCV(
            timestamp=timestamp or datetime.now(timezone.utc),
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )

    return _create


@pytest.fixture
def execution_config():
    """Default execution configuration"""
    return OrderExecutionConfig(
        slippage_model=SlippageModel.PERCENTAGE,
        slippage_rate=Decimal("0.0005"),
        commission_rate=Decimal("0.001"),
        tp_percentage=Decimal("0.02"),
        sl_percentage=Decimal("0.01"),
        max_bars_held=100,
        enable_slippage=True,
        enable_commission=True,
    )


# ==================== Execution Simulator Tests ====================


class TestDirectionAwareSlippage:
    """Tests for DirectionAwareSlippage"""

    def test_buy_slippage_adds_to_price(self, execution_config):
        """Test BUY slippage adds to price (unfavorable to buyer)"""
        slippage = DirectionAwareSlippage(execution_config.slippage_rate)

        open_price = Decimal("50000")
        buy_price, slippage_cost = slippage.calculate(
            open_price,
            OrderSide.BUY,
            quantity=Decimal("1"),
            volume=Decimal("100"),
            model=SlippageModel.PERCENTAGE,
        )

        assert buy_price > open_price
        assert buy_price == open_price * (Decimal("1") + execution_config.slippage_rate)

    def test_sell_slippage_subtracts_from_price(self, execution_config):
        """Test SELL slippage subtracts from price (unfavorable to seller)"""
        slippage = DirectionAwareSlippage(execution_config.slippage_rate)

        open_price = Decimal("50000")
        sell_price, slippage_cost = slippage.calculate(
            open_price,
            OrderSide.SELL,
            quantity=Decimal("1"),
            volume=Decimal("100"),
            model=SlippageModel.PERCENTAGE,
        )

        assert sell_price < open_price
        assert sell_price == open_price * (Decimal("1") - execution_config.slippage_rate)

    def test_no_slippage_model(self, execution_config):
        """Test NO_SLIPPAGE model returns open price"""
        slippage = DirectionAwareSlippage(execution_config.slippage_rate)

        open_price = Decimal("50000")
        price, cost = slippage.calculate(
            open_price,
            OrderSide.BUY,
            model=SlippageModel.NO_SLIPPAGE,
        )

        assert price == open_price
        assert cost == Decimal("0")

    def test_fixed_slippage_model(self, execution_config):
        """Test FIXED slippage model"""
        slippage = DirectionAwareSlippage(Decimal("10"))

        open_price = Decimal("50000")
        buy_price, cost = slippage.calculate(
            open_price,
            OrderSide.BUY,
            model=SlippageModel.FIXED,
        )

        assert buy_price == Decimal("50010")
        assert cost == Decimal("10")

    def test_percentage_slippage_model(self, execution_config):
        """Test PERCENTAGE slippage model"""
        slippage = DirectionAwareSlippage(Decimal("0.001"))

        open_price = Decimal("50000")
        buy_price, cost = slippage.calculate(
            open_price,
            OrderSide.BUY,
            quantity=Decimal("1"),
            model=SlippageModel.PERCENTAGE,
        )

        assert buy_price == Decimal("50050")
        assert cost == Decimal("50")

    def test_calculate_rate(self, execution_config):
        """Test slippage rate calculation"""
        slippage = DirectionAwareSlippage(execution_config.slippage_rate)

        execution_price = Decimal("50025")
        open_price = Decimal("50000")

        rate = slippage.calculate_rate(execution_price, open_price, OrderSide.BUY)

        assert rate == Decimal("0.0005")


class TestNextBarOpenExecutor:
    """Tests for NextBarOpenExecutor"""

    def test_queue_and_get_pending_orders(self, execution_config):
        """Test queueing and retrieving pending orders"""
        executor = NextBarOpenExecutor(execution_config)

        order = PendingOrder(
            order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            created_bar_index=0,
        )
        executor.queue_order(order)

        pending = executor.get_pending_orders()
        assert len(pending) == 1
        assert pending[0].order_id == "test_1"

    def test_cancel_order(self, execution_config):
        """Test canceling a pending order"""
        executor = NextBarOpenExecutor(execution_config)

        order = PendingOrder(
            order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            created_bar_index=0,
        )
        executor.queue_order(order)

        cancelled = executor.cancel_order("test_1")
        assert cancelled is not None
        assert executor.get_pending_orders() == []

    def test_execute_pending_at_next_bar_open(self, execution_config, ohlcv_factory):
        """Test execution at next bar open price (not current bar close)"""
        executor = NextBarOpenExecutor(execution_config)

        order = PendingOrder(
            order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            created_bar_index=0,
        )
        executor.queue_order(order)

        bar_n1 = ohlcv_factory(
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            open_price=Decimal("50200"),
            high=Decimal("50500"),
            low=Decimal("49800"),
            close=Decimal("50400"),
        )

        results = executor.execute_pending(bar_n1, bar_index=1)

        assert len(results) == 1
        assert results[0].exit_reason == ExitReason.SIGNAL
        assert results[0].price > bar_n1.open

    def test_execute_pending_stop_loss_triggered(self, execution_config, ohlcv_factory):
        """Test stop-loss triggers within bar"""
        executor = NextBarOpenExecutor(execution_config)

        order = PendingOrder(
            order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            stop_loss=Decimal("49500"),
            created_bar_index=0,
        )
        executor.queue_order(order)

        bar_n1 = ohlcv_factory(
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            open_price=Decimal("50200"),
            high=Decimal("50500"),
            low=Decimal("49000"),
            close=Decimal("49100"),
        )

        results = executor.execute_pending(bar_n1, bar_index=1)

        assert len(results) == 1
        assert results[0].exit_reason == ExitReason.STOP_LOSS
        assert results[0].price == Decimal("49500")

    def test_execute_pending_take_profit_triggered(self, execution_config, ohlcv_factory):
        """Test take-profit triggers within bar"""
        executor = NextBarOpenExecutor(execution_config)

        order = PendingOrder(
            order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            take_profit=Decimal("51000"),
            created_bar_index=0,
        )
        executor.queue_order(order)

        bar_n1 = ohlcv_factory(
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            open_price=Decimal("50200"),
            high=Decimal("51500"),
            low=Decimal("49800"),
            close=Decimal("51400"),
        )

        results = executor.execute_pending(bar_n1, bar_index=1)

        assert len(results) == 1
        assert results[0].exit_reason == ExitReason.TAKE_PROFIT
        assert results[0].price == Decimal("51000")

    def test_execute_pending_sell_stop_loss(self, execution_config, ohlcv_factory):
        """Test SELL order stop-loss triggers (high touches SL)"""
        executor = NextBarOpenExecutor(execution_config)

        order = PendingOrder(
            order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            quantity=Decimal("1"),
            stop_loss=Decimal("50500"),
            created_bar_index=0,
        )
        executor.queue_order(order)

        bar_n1 = ohlcv_factory(
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            open_price=Decimal("50000"),
            high=Decimal("51000"),
            low=Decimal("49500"),
            close=Decimal("50900"),
        )

        results = executor.execute_pending(bar_n1, bar_index=1)

        assert len(results) == 1
        assert results[0].exit_reason == ExitReason.STOP_LOSS
        assert results[0].price == Decimal("50500")


class TestStopLossTakeProfitExecutor:
    """Tests for StopLossTakeProfitExecutor"""

    def test_calculate_levels_buy(self, execution_config):
        """Test calculating SL/TP levels for BUY order"""
        executor = StopLossTakeProfitExecutor(execution_config)

        sl, tp = executor.calculate_levels(Decimal("50000"), OrderSide.BUY)

        assert sl == Decimal("50000") * (Decimal("1") - Decimal("0.01"))
        assert tp == Decimal("50000") * (Decimal("1") + Decimal("0.02"))

    def test_calculate_levels_sell(self, execution_config):
        """Test calculating SL/TP levels for SELL order"""
        executor = StopLossTakeProfitExecutor(execution_config)

        sl, tp = executor.calculate_levels(Decimal("50000"), OrderSide.SELL)

        assert sl == Decimal("50000") * (Decimal("1") + Decimal("0.01"))
        assert tp == Decimal("50000") * (Decimal("1") - Decimal("0.02"))

    def test_calculate_levels_zero_sl(self, execution_config):
        """Test calculating levels with zero SL percentage"""
        config = OrderExecutionConfig(
            slippage_rate=Decimal("0.0005"),
            commission_rate=Decimal("0.001"),
            tp_percentage=Decimal("0.02"),
            sl_percentage=Decimal("0"),
            max_bars_held=100,
        )
        executor = StopLossTakeProfitExecutor(config)

        sl, tp = executor.calculate_levels(Decimal("50000"), OrderSide.BUY)

        assert sl is None
        assert tp == Decimal("50000") * (Decimal("1") + Decimal("0.02"))

    def test_check_trigger_buy_stop_loss(self, execution_config, ohlcv_factory):
        """Test BUY order stop-loss trigger check"""
        executor = StopLossTakeProfitExecutor(execution_config)

        bar = ohlcv_factory(
            open_price=Decimal("50200"),
            high=Decimal("50500"),
            low=Decimal("49500"),
        )

        exit_reason, price = executor.check_trigger(
            bar,
            OrderSide.BUY,
            stop_loss=Decimal("49600"),
            take_profit=Decimal("51000"),
        )

        assert exit_reason == ExitReason.STOP_LOSS
        assert price == Decimal("49500")

    def test_check_trigger_buy_take_profit(self, execution_config, ohlcv_factory):
        """Test BUY order take-profit trigger check"""
        executor = StopLossTakeProfitExecutor(execution_config)

        bar = ohlcv_factory(
            open_price=Decimal("50200"),
            high=Decimal("51200"),
            low=Decimal("49800"),
        )

        exit_reason, price = executor.check_trigger(
            bar,
            OrderSide.BUY,
            stop_loss=Decimal("49600"),
            take_profit=Decimal("51000"),
        )

        assert exit_reason == ExitReason.TAKE_PROFIT
        assert price == Decimal("51200")

    def test_check_trigger_sell_stop_loss(self, execution_config, ohlcv_factory):
        """Test SELL order stop-loss trigger check"""
        executor = StopLossTakeProfitExecutor(execution_config)

        bar = ohlcv_factory(
            open_price=Decimal("50000"),
            high=Decimal("50800"),
            low=Decimal("49500"),
        )

        exit_reason, price = executor.check_trigger(
            bar,
            OrderSide.SELL,
            stop_loss=Decimal("50500"),
            take_profit=Decimal("49000"),
        )

        assert exit_reason == ExitReason.STOP_LOSS
        assert price == Decimal("50800")

    def test_check_trigger_sell_take_profit(self, execution_config, ohlcv_factory):
        """Test SELL order take-profit trigger check"""
        executor = StopLossTakeProfitExecutor(execution_config)

        bar = ohlcv_factory(
            open_price=Decimal("50000"),
            high=Decimal("50500"),
            low=Decimal("48800"),
        )

        exit_reason, price = executor.check_trigger(
            bar,
            OrderSide.SELL,
            stop_loss=Decimal("50500"),
            take_profit=Decimal("49000"),
        )

        assert exit_reason == ExitReason.TAKE_PROFIT
        assert price == Decimal("48800")

    def test_check_trigger_no_trigger(self, execution_config, ohlcv_factory):
        """Test no trigger when price doesn't reach SL/TP"""
        executor = StopLossTakeProfitExecutor(execution_config)

        bar = ohlcv_factory(
            open_price=Decimal("50000"),
            high=Decimal("50300"),
            low=Decimal("49800"),
        )

        exit_reason, price = executor.check_trigger(
            bar,
            OrderSide.BUY,
            stop_loss=Decimal("49000"),
            take_profit=Decimal("51000"),
        )

        assert exit_reason is None
        assert price is None


class TestExecutionSimulator:
    """Tests for ExecutionSimulator"""

    def test_queue_entry_creates_pending_order(self, execution_config, ohlcv_factory):
        """Test queue_entry creates a pending order"""
        simulator = ExecutionSimulator(execution_config)

        order = simulator.queue_entry(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            stop_loss=Decimal("49500"),
            take_profit=Decimal("51000"),
            signal_price=Decimal("50000"),
        )

        assert order.symbol == "BTCUSDT"
        assert order.side == OrderSide.BUY
        assert order.quantity == Decimal("1")

    def test_open_position_creates_position(self, execution_config):
        """Test open_position creates a position"""
        simulator = ExecutionSimulator(execution_config)

        position = simulator.open_position(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            entry_bar_index=0,
            entry_price=Decimal("50000"),
            timestamp=datetime.now(timezone.utc),
            stop_loss=Decimal("49500"),
            take_profit=Decimal("51000"),
        )

        assert position.symbol == "BTCUSDT"
        assert position.entry_price == Decimal("50000")
        assert simulator.positions["BTCUSDT"] is position

    def test_process_bar_exits_before_entries(self, execution_config, ohlcv_factory):
        """Test process_bar processes exits before entries"""
        simulator = ExecutionSimulator(execution_config)

        position = simulator.open_position(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            entry_bar_index=0,
            entry_price=Decimal("50000"),
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            stop_loss=Decimal("49500"),
        )
        simulator._bar_index = 1

        bar = ohlcv_factory(
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            open_price=Decimal("50200"),
            high=Decimal("50500"),
            low=Decimal("49000"),
            close=Decimal("49100"),
        )

        exits, entries = simulator.process_bar(bar)

        assert len(exits) == 1
        assert exits[0].exit_reason == ExitReason.STOP_LOSS
        assert "BTCUSDT" not in simulator.positions

    def test_commission_calculation(self, execution_config):
        """Test commission is calculated correctly on execution price"""
        config = OrderExecutionConfig(
            slippage_rate=Decimal("0.0005"),
            commission_rate=Decimal("0.001"),
            tp_percentage=Decimal("0.02"),
            sl_percentage=Decimal("0.01"),
            max_bars_held=100,
            enable_commission=True,
        )
        simulator = ExecutionSimulator(config)

        order = simulator.queue_entry(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
        )
        simulator._bar_index = 1

        bar = OHLCV(
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            open=Decimal("50000"),
            high=Decimal("50500"),
            low=Decimal("49500"),
            close=Decimal("50200"),
            volume=Decimal("100"),
        )

        results = simulator.execute_pending(bar)

        assert len(results) == 1
        assert results[0].commission > Decimal("0")
        assert results[0].commission == results[0].quantity * results[0].price * Decimal("0.001")

    def test_slippage_applied_on_entry(self, execution_config, ohlcv_factory):
        """Test slippage is applied on entry execution"""
        simulator = ExecutionSimulator(execution_config)

        order = simulator.queue_entry(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
        )
        simulator._bar_index = 1

        bar = ohlcv_factory(
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            open_price=Decimal("50000"),
            high=Decimal("50500"),
            low=Decimal("49500"),
            close=Decimal("50200"),
            volume=Decimal("100"),
        )

        results = simulator.execute_pending(bar)

        assert len(results) == 1
        assert results[0].price > bar.open
        assert results[0].slippage > Decimal("0")

    def test_reset_clears_state(self, execution_config):
        """Test reset clears all state"""
        simulator = ExecutionSimulator(execution_config)

        simulator.open_position(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            entry_bar_index=0,
            entry_price=Decimal("50000"),
            timestamp=datetime.now(timezone.utc),
        )
        simulator.queue_entry("ETHUSDT", OrderSide.SELL, Decimal("1"))

        simulator.reset()

        assert len(simulator.positions) == 0
        assert len(simulator.closed_positions) == 0


# ==================== Critical Integration Tests ====================


class TestSlippageDirectionCritical:
    """Critical tests for slippage direction - MUST PASS"""

    def test_buy_slippage_always_adds(self, execution_config):
        """CRITICAL: BUY slippage must always add to price"""
        slippage = DirectionAwareSlippage(execution_config.slippage_rate)
        open_price = Decimal("50000")

        buy_price, _ = slippage.calculate(
            open_price,
            OrderSide.BUY,
            model=SlippageModel.PERCENTAGE,
        )

        assert buy_price > open_price, f"BUY price {buy_price} must be > open {open_price}"

    def test_sell_slippage_always_subtracts(self, execution_config):
        """CRITICAL: SELL slippage must always subtract from price"""
        slippage = DirectionAwareSlippage(execution_config.slippage_rate)
        open_price = Decimal("50000")

        sell_price, _ = slippage.calculate(
            open_price,
            OrderSide.SELL,
            model=SlippageModel.PERCENTAGE,
        )

        assert sell_price < open_price, f"SELL price {sell_price} must be < open {open_price}"


class TestNextBarExecutionCritical:
    """Critical tests for next-bar execution - MUST PASS"""

    def test_execution_uses_next_bar_open(self, execution_config, ohlcv_factory):
        """CRITICAL: Execution must use next bar's open, not current bar"""
        executor = NextBarOpenExecutor(execution_config)

        order = PendingOrder(
            order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            created_bar_index=5,
        )
        executor.queue_order(order)

        bar_n = ohlcv_factory(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open_price=Decimal("50000"),
            high=Decimal("50500"),
            low=Decimal("49500"),
            close=Decimal("50200"),
        )

        bar_n1 = ohlcv_factory(
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            open_price=Decimal("51000"),
            high=Decimal("51500"),
            low=Decimal("50500"),
            close=Decimal("51200"),
        )

        results_n = executor.execute_pending(bar_n, bar_index=6)

        assert len(results_n) == 1
        assert results_n[0].exit_reason == ExitReason.SIGNAL
        expected_price = bar_n.open * (Decimal("1") + execution_config.slippage_rate)
        assert results_n[0].price == expected_price


class TestStopLossTriggerCritical:
    """Critical tests for stop-loss trigger - MUST PASS"""

    def test_stop_loss_triggers_when_price_drops(self, execution_config, ohlcv_factory):
        """CRITICAL: SL must trigger when price drops to SL level"""
        executor = NextBarOpenExecutor(execution_config)

        order = PendingOrder(
            order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            stop_loss=Decimal("49500"),
            created_bar_index=0,
        )
        executor.queue_order(order)

        bar_n1 = ohlcv_factory(
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            open_price=Decimal("50200"),
            high=Decimal("50500"),
            low=Decimal("49000"),
            close=Decimal("49100"),
        )

        results = executor.execute_pending(bar_n1, bar_index=1)

        assert len(results) == 1
        assert (
            results[0].exit_reason == ExitReason.STOP_LOSS
        ), f"Expected STOP_LOSS, got {results[0].exit_reason}"

    def test_take_profit_triggers_when_price_rises(self, execution_config, ohlcv_factory):
        """CRITICAL: TP must trigger when price rises to TP level"""
        executor = NextBarOpenExecutor(execution_config)

        order = PendingOrder(
            order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            take_profit=Decimal("51000"),
            created_bar_index=0,
        )
        executor.queue_order(order)

        bar_n1 = ohlcv_factory(
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            open_price=Decimal("50200"),
            high=Decimal("51500"),
            low=Decimal("49800"),
            close=Decimal("51400"),
        )

        results = executor.execute_pending(bar_n1, bar_index=1)

        assert len(results) == 1
        assert (
            results[0].exit_reason == ExitReason.TAKE_PROFIT
        ), f"Expected TAKE_PROFIT, got {results[0].exit_reason}"

    def test_within_bar_high_low_used_for_trigger(self, execution_config, ohlcv_factory):
        """CRITICAL: Within-bar high/low must be checked for SL/TP"""
        executor = NextBarOpenExecutor(execution_config)

        order = PendingOrder(
            order_id="test_1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            stop_loss=Decimal("49600"),
            created_bar_index=0,
        )
        executor.queue_order(order)

        bar_n1 = ohlcv_factory(
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            open_price=Decimal("50000"),
            high=Decimal("50500"),
            low=Decimal("49000"),
            close=Decimal("50200"),
        )

        results = executor.execute_pending(bar_n1, bar_index=1)

        assert len(results) == 1
        assert results[0].exit_reason == ExitReason.STOP_LOSS
        assert results[0].price == Decimal("49600")
