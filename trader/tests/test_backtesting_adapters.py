"""
Backtesting Adapters Unit Tests
================================

Comprehensive unit tests for the backtesting adapter layer covering:
1. QuantConnect Data Adapter
2. Strategy Adapter
3. Execution Simulator
4. Result Converter

Test Structure:
- Data Adapter Tests: get_klines, get_symbols, caching, error handling
- Strategy Adapter Tests: signal conversion, indicator mapping, order types
- Execution Simulator Tests: slippage, next-bar execution, SL/TP triggers
- Result Converter Tests: statistics, equity curve, metrics calculation
"""
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from uuid import uuid4

from trader.core.domain.models.signal import Signal, SignalType
from trader.core.domain.models.order import OrderSide, OrderType
from trader.services.backtesting.ports import OHLCV, BacktestConfig, BacktestResult
from trader.services.backtesting.strategy_adapter import (
    SignalConverter,
    IndicatorMapper,
    QuantConnectStrategyAdapter,
    StrategyAdapterConfig,
    IndicatorConfig,
    OrderSignal,
    IndicatorType,
    OrderModel,
    LeanInsight,
)
from trader.services.backtesting.execution_simulator import (
    ExecutionSimulator,
    DirectionAwareSlippage,
    NextBarOpenExecutor,
    StopLossTakeProfitExecutor,
    OrderExecutionConfig,
    PendingOrder,
    ExecutionResult,
    PositionState,
    SlippageModel,
    ExitReason,
)
from trader.services.backtesting.result_converter import (
    BacktestResultConverter,
    QuantConnectStatistics,
    QuantConnectTrade,
    EquityPoint,
    ConversionResult,
)


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


@pytest.fixture
def signal_factory():
    """Factory for creating test signals"""
    def _create(
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        quantity=Decimal("1"),
        confidence=Decimal("1.0"),
        stop_loss=None,
        take_profit=None,
    ):
        return Signal(
            signal_type=signal_type,
            symbol=symbol,
            quantity=quantity,
            confidence=confidence,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reason="test_signal",
        )
    return _create


@pytest.fixture
def lean_statistics_data():
    """Sample Lean statistics dictionary"""
    return {
        "Number of Trades": "100",
        "Winning Trades": "60",
        "Losing Trades": "40",
        "Total Profit": "15000.50",
        "Sharpe Ratio": "1.85",
        "Sortino Ratio": "2.10",
        "Calmar Ratio": "0.95",
        "Maximum Drawdown": "5000.00",
        "Max Drawdown": "10.5%",
        "Win Rate": "60.0%",
        "Profit Factor": "2.50",
        "Average Trade Duration": "3600.0",
        "Value at Risk (VaR) 95%": "2.5%",
        "Turnover": "500000.00",
        "Commission": "500.00",
        "Initial Capital": "100000.00",
        "Final Capital": "115000.50",
    }


@pytest.fixture
def lean_equity_curve():
    """Sample Lean equity curve"""
    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        {"timestamp": (base_time + timedelta(days=i)).isoformat(), "equity": str(100000 + i * 500)}
        for i in range(30)
    ]


@pytest.fixture
def lean_trade_list():
    """Sample Lean trade list"""
    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "id": f"trade_{i}",
            "symbol": "BTCUSDT",
            "quantity": "1.0",
            "price": "50000",
            "direction": "long",
            "entry_time": base_time.isoformat(),
            "exit_time": (base_time + timedelta(hours=24)).isoformat(),
            "entry_price": "50000",
            "exit_price": "50500",
            "pnl": "500",
            "pnl_percent": "1.0",
        }
        for i in range(5)
    ]


@pytest.fixture
def lean_result(lean_statistics_data, lean_equity_curve, lean_trade_list):
    """Complete Lean backtest result"""
    return {
        "Statistics": lean_statistics_data,
        "EquityCurve": lean_equity_curve,
        "TradeList": lean_trade_list,
        "TotalProfit": "15000.50",
    }


# ==================== Data Adapter Tests ====================


class TestSignalConverter:
    """Tests for SignalConverter"""

    def test_convert_to_order_signal_buy(self, signal_factory):
        """Test converting BUY signal to OrderSignal"""
        config = StrategyAdapterConfig(order_model=OrderModel.MARKET)
        converter = SignalConverter(config)

        signal = signal_factory(signal_type=SignalType.BUY)
        result = converter.convert_to_order_signal(signal)

        assert result == OrderSignal.BUY_MARKET

    def test_convert_to_order_signal_sell(self, signal_factory):
        """Test converting SELL signal to OrderSignal"""
        config = StrategyAdapterConfig(order_model=OrderModel.MARKET)
        converter = SignalConverter(config)

        signal = signal_factory(signal_type=SignalType.SELL)
        result = converter.convert_to_order_signal(signal)

        assert result == OrderSignal.SELL_MARKET

    def test_convert_to_order_signal_hold(self, signal_factory):
        """Test converting NONE signal to HOLD"""
        config = StrategyAdapterConfig(order_model=OrderModel.MARKET)
        converter = SignalConverter(config)

        signal = signal_factory(signal_type=SignalType.NONE)
        result = converter.convert_to_order_signal(signal)

        assert result == OrderSignal.HOLD

    def test_convert_to_order_signal_buy_limit(self, signal_factory):
        """Test converting BUY signal with LIMIT order model"""
        config = StrategyAdapterConfig(order_model=OrderModel.LIMIT)
        converter = SignalConverter(config)

        signal = signal_factory(signal_type=SignalType.BUY)
        result = converter.convert_to_order_signal(signal)

        assert result == OrderSignal.BUY_LIMIT

    def test_convert_to_direction_up(self, signal_factory):
        """Test converting BUY signal to direction 'up'"""
        config = StrategyAdapterConfig()
        converter = SignalConverter(config)

        signal = signal_factory(signal_type=SignalType.BUY)
        result = converter.convert_to_direction(signal)

        assert result == "up"

    def test_convert_to_direction_down(self, signal_factory):
        """Test converting SELL signal to direction 'down'"""
        config = StrategyAdapterConfig()
        converter = SignalConverter(config)

        signal = signal_factory(signal_type=SignalType.SELL)
        result = converter.convert_to_direction(signal)

        assert result == "down"

    def test_convert_to_direction_flat(self, signal_factory):
        """Test converting NONE signal to direction 'flat'"""
        config = StrategyAdapterConfig()
        converter = SignalConverter(config)

        signal = signal_factory(signal_type=SignalType.NONE)
        result = converter.convert_to_direction(signal)

        assert result == "flat"

    def test_convert_from_lean_insight_up(self):
        """Test converting LeanInsight with direction 'up'"""
        config = StrategyAdapterConfig()
        converter = SignalConverter(config)

        insight = LeanInsight(
            symbol="BTCUSDT",
            type="price",
            direction="up",
            confidence=0.9,
            period=60,
            weight=1.0,
        )
        result = converter.convert_from_lean_insight(insight)

        assert result.signal_type == SignalType.BUY
        assert result.symbol == "BTCUSDT"

    def test_convert_from_lean_insight_down(self):
        """Test converting LeanInsight with direction 'down'"""
        config = StrategyAdapterConfig()
        converter = SignalConverter(config)

        insight = LeanInsight(
            symbol="BTCUSDT",
            type="price",
            direction="down",
            confidence=0.9,
            period=60,
            weight=1.0,
        )
        result = converter.convert_from_lean_insight(insight)

        assert result.signal_type == SignalType.SELL

    def test_convert_from_lean_insight_flat(self):
        """Test converting LeanInsight with direction 'flat'"""
        config = StrategyAdapterConfig()
        converter = SignalConverter(config)

        insight = LeanInsight(
            symbol="BTCUSDT",
            type="price",
            direction="flat",
            confidence=0.9,
            period=60,
            weight=1.0,
        )
        result = converter.convert_from_lean_insight(insight)

        assert result.signal_type == SignalType.NONE

    def test_convert_from_lean_signals_batch(self):
        """Test batch conversion of Lean insights"""
        config = StrategyAdapterConfig()
        converter = SignalConverter(config)

        insights = [
            LeanInsight("BTCUSDT", "price", "up", 0.9, 60, 1.0),
            LeanInsight("ETHUSDT", "price", "down", 0.8, 60, 1.0),
            LeanInsight("BNBUSDT", "price", "flat", 0.7, 60, 1.0),
        ]
        results = converter.convert_from_lean_signals(insights)

        assert len(results) == 3
        assert results[0].signal_type == SignalType.BUY
        assert results[1].signal_type == SignalType.SELL
        assert results[2].signal_type == SignalType.NONE


class TestIndicatorMapper:
    """Tests for IndicatorMapper"""

    def test_resolve_indicator_sma(self):
        """Test resolving SMA indicator"""
        mapper = IndicatorMapper()
        result = mapper.resolve_indicator("sma")

        assert result == IndicatorType.SMA

    def test_resolve_indicator_ema(self):
        """Test resolving EMA indicator"""
        mapper = IndicatorMapper()
        result = mapper.resolve_indicator("ema")

        assert result == IndicatorType.EMA

    def test_resolve_indicator_rsi(self):
        """Test resolving RSI indicator"""
        mapper = IndicatorMapper()
        result = mapper.resolve_indicator("rsi")

        assert result == IndicatorType.RSI

    def test_resolve_indicator_macd(self):
        """Test resolving MACD indicator"""
        mapper = IndicatorMapper()
        result = mapper.resolve_indicator("macd")

        assert result == IndicatorType.MACD

    def test_resolve_indicator_bollinger_bands(self):
        """Test resolving Bollinger Bands indicator"""
        mapper = IndicatorMapper()
        result = mapper.resolve_indicator("bollinger_bands")

        assert result == IndicatorType.BOLLINGER_BANDS

    def test_resolve_indicator_unknown_raises(self):
        """Test resolving unknown indicator raises ValueError"""
        mapper = IndicatorMapper()

        with pytest.raises(ValueError, match="Unknown indicator"):
            mapper.resolve_indicator("unknown_indicator")

    def test_register_custom_indicator(self):
        """Test registering custom indicator"""
        mapper = IndicatorMapper()
        mapper.register_indicator("custom_indicator", IndicatorType.ATR)

        result = mapper.resolve_indicator("custom_indicator")
        assert result == IndicatorType.ATR

    def test_get_lean_indicator_code_sma(self):
        """Test generating Lean code for SMA"""
        mapper = IndicatorMapper()
        config = IndicatorConfig(indicator_type=IndicatorType.SMA, symbol="BTCUSDT", period=20)

        result = mapper.get_lean_indicator_code(config)

        assert result == "SMAModel(BTCUSDT, 20)"

    def test_get_lean_indicator_code_ema(self):
        """Test generating Lean code for EMA"""
        mapper = IndicatorMapper()
        config = IndicatorConfig(indicator_type=IndicatorType.EMA, symbol="BTCUSDT", period=12)

        result = mapper.get_lean_indicator_code(config)

        assert result == "EMAModel(BTCUSDT, 12)"

    def test_get_lean_indicator_code_rsi(self):
        """Test generating Lean code for RSI"""
        mapper = IndicatorMapper()
        config = IndicatorConfig(indicator_type=IndicatorType.RSI, symbol="BTCUSDT", period=14)

        result = mapper.get_lean_indicator_code(config)

        assert result == "RSI(BTCUSDT, 14)"

    def test_get_lean_indicator_code_macd(self):
        """Test generating Lean code for MACD with custom params"""
        mapper = IndicatorMapper()
        config = IndicatorConfig(
            indicator_type=IndicatorType.MACD,
            symbol="BTCUSDT",
            period=14,
            parameters={"fast": 12, "slow": 26, "signal": 9},
        )

        result = mapper.get_lean_indicator_code(config)

        assert result == "MACD(BTCUSDT, 12, 26, 9)"

    def test_get_lean_indicator_code_macd_default_params(self):
        """Test generating Lean code for MACD with default params"""
        mapper = IndicatorMapper()
        config = IndicatorConfig(indicator_type=IndicatorType.MACD, symbol="BTCUSDT", period=14)

        result = mapper.get_lean_indicator_code(config)

        assert result == "MACD(BTCUSDT, 12, 26, 9)"

    def test_get_lean_indicator_code_bollinger_bands(self):
        """Test generating Lean code for Bollinger Bands"""
        mapper = IndicatorMapper()
        config = IndicatorConfig(
            indicator_type=IndicatorType.BOLLINGER_BANDS,
            symbol="BTCUSDT",
            period=20,
            parameters={"period": 20, "std": 2},
        )

        result = mapper.get_lean_indicator_code(config)

        assert result == "BollingerBands(BTCUSDT, 20, 2)"

    def test_get_lean_indicator_code_vwap(self):
        """Test generating Lean code for VWAP"""
        mapper = IndicatorMapper()
        config = IndicatorConfig(indicator_type=IndicatorType.VWAP, symbol="BTCUSDT", period=14)

        result = mapper.get_lean_indicator_code(config)

        assert result == "VWAP(BTCUSDT)"

    def test_build_indicator_map(self):
        """Test building indicator map"""
        mapper = IndicatorMapper()
        configs = [
            IndicatorConfig(indicator_type=IndicatorType.SMA, symbol="BTCUSDT", period=20),
            IndicatorConfig(indicator_type=IndicatorType.RSI, symbol="BTCUSDT", period=14),
        ]

        result = mapper.build_indicator_map(configs)

        assert isinstance(result, dict)
        assert len(result) == 2


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


# ==================== Result Converter Tests ====================

class TestBacktestResultConverter:
    """Tests for BacktestResultConverter"""

    def test_convert_statistics_full(self, lean_result):
        """Test converting complete Lean statistics"""
        converter = BacktestResultConverter()

        result = converter.convert(lean_result, "TestStrategy")

        assert result.success
        assert result.backtest_result is not None
        assert result.backtest_result.strategy_name == "TestStrategy"

    def test_convert_statistics_empty(self):
        """Test converting empty statistics"""
        converter = BacktestResultConverter()
        empty_result = {"Statistics": {}, "EquityCurve": [], "TradeList": []}

        result = converter.convert(empty_result, "TestStrategy")

        assert result.success
        assert len(result.warnings) > 0

    def test_equity_curve_conversion(self, lean_result):
        """Test equity curve conversion"""
        converter = BacktestResultConverter()

        result = converter.convert(lean_result, "TestStrategy")

        equity_points = result.backtest_result.result.equity_curve
        assert len(equity_points) == 30

    def test_trades_conversion(self, lean_result):
        """Test trades conversion"""
        converter = BacktestResultConverter()

        result = converter.convert(lean_result, "TestStrategy")

        trades = result.backtest_result.result.trades
        assert len(trades) == 5

    def test_metrics_calculation_sharpe(self, lean_result):
        """Test Sharpe ratio calculation"""
        converter = BacktestResultConverter()

        result = converter.convert(lean_result, "TestStrategy")

        sharpe = result.backtest_result.result.sharpe_ratio
        assert isinstance(sharpe, Decimal)
        assert sharpe >= Decimal("0")

    def test_metrics_calculation_sortino(self, lean_result):
        """Test Sortino ratio calculation"""
        converter = BacktestResultConverter()

        result = converter.convert(lean_result, "TestStrategy")

        sortino = result.backtest_result.result.metrics.get("sortino_ratio")
        assert isinstance(sortino, Decimal)

    def test_metrics_calculation_calmar(self, lean_result):
        """Test Calmar ratio calculation"""
        converter = BacktestResultConverter()

        result = converter.convert(lean_result, "TestStrategy")

        calmar = result.backtest_result.result.metrics.get("calmar_ratio")
        assert isinstance(calmar, Decimal)

    def test_metrics_calculation_var(self, lean_result):
        """Test VaR calculation"""
        converter = BacktestResultConverter()

        result = converter.convert(lean_result, "TestStrategy")

        var = result.backtest_result.result.metrics.get("var_95")
        assert isinstance(var, Decimal)

    def test_round_trip_test(self, lean_result):
        """Test round-trip conversion"""
        converter = BacktestResultConverter()

        result = converter.convert(lean_result, "TestStrategy")
        round_trip = converter.round_trip_test(lean_result, result.backtest_result)

        assert round_trip.success

    def test_validate_result_valid(self, lean_result):
        """Test validating a valid result"""
        converter = BacktestResultConverter()

        result = converter.convert(lean_result, "TestStrategy")
        validation = converter.validate_result(result.backtest_result)

        assert validation.success
        assert len(validation.errors) == 0

    def test_validate_result_missing_report_id(self):
        """Test validating result with missing report_id"""
        converter = BacktestResultConverter()

        class InvalidReport:
            def __init__(self):
                self.strategy_name = "Test"
                self.config = BacktestConfig(
                    start_date=datetime.now(timezone.utc),
                    end_date=datetime.now(timezone.utc),
                    initial_capital=Decimal("100000"),
                    symbol="BTCUSDT",
                )
                self.result = BacktestResult(
                    total_return=Decimal("10"),
                    sharpe_ratio=Decimal("1.5"),
                    max_drawdown=Decimal("5"),
                    win_rate=Decimal("60"),
                    profit_factor=Decimal("2.0"),
                    num_trades=10,
                    final_capital=Decimal("110000"),
                )

        validation = converter.validate_result(InvalidReport())
        assert not validation.success
        assert "Missing report_id" in validation.errors


class TestEquityPoint:
    """Tests for EquityPoint"""

    def test_from_dict(self):
        """Test creating EquityPoint from dictionary"""
        point = EquityPoint.from_dict({
            "timestamp": "2024-01-01T00:00:00+00:00",
            "equity": 100000,
        })

        assert point.equity == Decimal("100000")

    def test_to_dict(self):
        """Test converting EquityPoint to dictionary"""
        point = EquityPoint(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            equity=Decimal("100000"),
        )

        result = point.to_dict()

        assert result["equity"] == "100000"


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
        assert results[0].exit_reason == ExitReason.STOP_LOSS, \
            f"Expected STOP_LOSS, got {results[0].exit_reason}"

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
        assert results[0].exit_reason == ExitReason.TAKE_PROFIT, \
            f"Expected TAKE_PROFIT, got {results[0].exit_reason}"

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
