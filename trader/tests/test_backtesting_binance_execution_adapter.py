"""
Tests for BinanceExecutionAdapter
=================================
KillSwitch L2+ blocks backtest, OMS callback receives fills.
"""
import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from trader.services.backtesting.ports import BacktestConfig, BacktestResult
from trader.services.backtesting.binance_execution_adapter import BinanceExecutionAdapter


@pytest.fixture
def sample_config():
    return BacktestConfig(
        start_date=datetime(2021, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2021, 1, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("10000"),
        symbol="BTCUSDT",
        interval="1h",
        commission_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.0005"),
    )


@pytest.fixture
def mock_strategy():
    strategy = MagicMock()
    strategy.generate_signals = AsyncMock(return_value=[])
    return strategy


class TestBinanceExecutionAdapterKillSwitch:
    """KillSwitch integration tests."""

    @pytest.mark.asyncio
    async def test_killswitch_l0_allows_backtest(self, sample_config, mock_strategy):
        """L0 (NORMAL) should allow backtest to run."""
        ks_callback = MagicMock(return_value=0)  # L0
        adapter = BinanceExecutionAdapter(killswitch_callback=ks_callback)

        with patch.object(adapter._vectorbt, "run_backtest", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = BacktestResult(
                total_return=Decimal("0.1"),
                sharpe_ratio=Decimal("1.5"),
                max_drawdown=Decimal("0.05"),
                win_rate=Decimal("0.55"),
                profit_factor=Decimal("1.8"),
                num_trades=10,
                final_capital=Decimal("11000"),
                equity_curve=[],
                trades=[],
                start_date=sample_config.start_date,
                end_date=sample_config.end_date,
            )
            result = await adapter.run_backtest(sample_config, mock_strategy)

        mock_run.assert_called_once_with(sample_config, mock_strategy)
        assert result.total_return == Decimal("0.1")

    @pytest.mark.asyncio
    async def test_killswitch_l1_allows_backtest(self, sample_config, mock_strategy):
        """L1 (NO_NEW_POSITIONS) should allow backtest to run (L1 only affects live trading)."""
        ks_callback = MagicMock(return_value=1)  # L1
        adapter = BinanceExecutionAdapter(killswitch_callback=ks_callback)

        with patch.object(adapter._vectorbt, "run_backtest", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = BacktestResult(
                total_return=Decimal("0.05"),
                sharpe_ratio=Decimal("1.0"),
                max_drawdown=Decimal("0.1"),
                win_rate=Decimal("0.5"),
                profit_factor=Decimal("1.2"),
                num_trades=5,
                final_capital=Decimal("10500"),
                equity_curve=[],
                trades=[],
                start_date=sample_config.start_date,
                end_date=sample_config.end_date,
            )
            result = await adapter.run_backtest(sample_config, mock_strategy)

        mock_run.assert_called_once()
        assert result.total_return == Decimal("0.05")

    @pytest.mark.asyncio
    async def test_killswitch_l2_blocks_backtest(self, sample_config, mock_strategy):
        """L2 (CANCEL_ALL_AND_HALT) should block backtest and return zero result."""
        ks_callback = MagicMock(return_value=2)  # L2
        adapter = BinanceExecutionAdapter(killswitch_callback=ks_callback)

        with patch.object(adapter._vectorbt, "run_backtest", new_callable=AsyncMock) as mock_run:
            result = await adapter.run_backtest(sample_config, mock_strategy)

        mock_run.assert_not_called()
        assert result.total_return == Decimal("0")
        assert result.metrics.get("blocked_by") == "KillSwitch"
        assert result.metrics.get("level") == 2

    @pytest.mark.asyncio
    async def test_killswitch_l3_blocks_backtest(self, sample_config, mock_strategy):
        """L3 (LIQUIDATE_AND_DISCONNECT) should block backtest and return zero result."""
        ks_callback = MagicMock(return_value=3)  # L3
        adapter = BinanceExecutionAdapter(killswitch_callback=ks_callback)

        with patch.object(adapter._vectorbt, "run_backtest", new_callable=AsyncMock) as mock_run:
            result = await adapter.run_backtest(sample_config, mock_strategy)

        mock_run.assert_not_called()
        assert result.total_return == Decimal("0")
        assert result.metrics.get("blocked_by") == "KillSwitch"
        assert result.metrics.get("level") == 3

    @pytest.mark.asyncio
    async def test_no_killswitch_callback_allows_backtest(self, sample_config, mock_strategy):
        """When killswitch_callback is None, backtest runs normally."""
        adapter = BinanceExecutionAdapter(killswitch_callback=None)

        with patch.object(adapter._vectorbt, "run_backtest", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = BacktestResult(
                total_return=Decimal("0.2"),
                sharpe_ratio=Decimal("2.0"),
                max_drawdown=Decimal("0.05"),
                win_rate=Decimal("0.6"),
                profit_factor=Decimal("2.0"),
                num_trades=20,
                final_capital=Decimal("12000"),
                equity_curve=[],
                trades=[],
                start_date=sample_config.start_date,
                end_date=sample_config.end_date,
            )
            result = await adapter.run_backtest(sample_config, mock_strategy)

        mock_run.assert_called_once()


class TestBinanceExecutionAdapterOMS:
    """OMS callback integration tests."""

    @pytest.mark.asyncio
    async def test_oms_callback_receives_backtest_fills(self, sample_config, mock_strategy):
        """OMS callback should be called with backtest fill events."""
        oms_callback = MagicMock()
        adapter = BinanceExecutionAdapter(oms_callback=oms_callback)

        fake_trades = [
            {"trade_id": 0, "pnl": 100.0, "return": 0.01},
            {"trade_id": 1, "pnl": -50.0, "return": -0.005},
        ]

        with patch.object(adapter._vectorbt, "run_backtest", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = BacktestResult(
                total_return=Decimal("0.1"),
                sharpe_ratio=Decimal("1.5"),
                max_drawdown=Decimal("0.05"),
                win_rate=Decimal("0.55"),
                profit_factor=Decimal("1.8"),
                num_trades=2,
                final_capital=Decimal("11000"),
                equity_curve=[],
                trades=fake_trades,
                start_date=sample_config.start_date,
                end_date=sample_config.end_date,
            )
            result = await adapter.run_backtest(sample_config, mock_strategy)

        assert oms_callback.call_count == 2
        first_call = oms_callback.call_args_list[0][0][0]
        assert first_call["type"] == "backtest_fill"
        assert first_call["symbol"] == "BTCUSDT"
        assert first_call["trade"]["trade_id"] == 0

    @pytest.mark.asyncio
    async def test_oms_callback_not_called_when_none(self, sample_config, mock_strategy):
        """When oms_callback is None, no error should occur."""
        adapter = BinanceExecutionAdapter(oms_callback=None)

        with patch.object(adapter._vectorbt, "run_backtest", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = BacktestResult(
                total_return=Decimal("0.1"),
                sharpe_ratio=Decimal("1.5"),
                max_drawdown=Decimal("0.05"),
                win_rate=Decimal("0.55"),
                profit_factor=Decimal("1.8"),
                num_trades=0,
                final_capital=Decimal("11000"),
                equity_curve=[],
                trades=[],
                start_date=sample_config.start_date,
                end_date=sample_config.end_date,
            )
            result = await adapter.run_backtest(sample_config, mock_strategy)

        mock_run.assert_called_once()


class TestBinanceExecutionAdapterOptimization:
    """Optimization delegation tests."""

    @pytest.mark.asyncio
    async def test_run_optimization_delegates_to_vectorbt(self, sample_config, mock_strategy):
        """run_optimization should delegate to VectorBTAdapter."""
        adapter = BinanceExecutionAdapter()
        param_ranges = {"fast_period": [5, 10, 15], "slow_period": [20, 30]}

        with patch.object(adapter._vectorbt, "run_optimization", new_callable=AsyncMock) as mock_opt:
            from trader.services.backtesting.ports import OptimizationResult
            mock_opt.return_value = OptimizationResult(
                best_params={"fast_period": 10, "slow_period": 30},
                best_metrics=BacktestResult(
                    total_return=Decimal("0.15"),
                    sharpe_ratio=Decimal("1.8"),
                    max_drawdown=Decimal("0.05"),
                    win_rate=Decimal("0.55"),
                    profit_factor=Decimal("2.0"),
                    num_trades=15,
                    final_capital=Decimal("11500"),
                ),
                all_results=[],
                optimization_time=1.5,
            )
            result = await adapter.run_optimization(sample_config, mock_strategy, param_ranges)

        mock_opt.assert_called_once_with(sample_config, mock_strategy, param_ranges)
        assert result.best_params["fast_period"] == 10


class TestBinanceExecutionAdapterResultPassThrough:
    """Result pass-through tests."""

    @pytest.mark.asyncio
    async def test_backtest_result_fields_passed_through(self, sample_config, mock_strategy):
        """All BacktestResult fields should be preserved from VectorBT result."""
        adapter = BinanceExecutionAdapter(killswitch_callback=lambda: 0)

        expected_result = BacktestResult(
            total_return=Decimal("0.25"),
            sharpe_ratio=Decimal("2.5"),
            max_drawdown=Decimal("0.03"),
            win_rate=Decimal("0.7"),
            profit_factor=Decimal("3.0"),
            num_trades=50,
            final_capital=Decimal("12500"),
            equity_curve=[{"timestamp": "2021-01-01", "equity": 10000}, {"timestamp": "2021-01-31", "equity": 12500}],
            trades=[{"trade_id": 0, "pnl": 500.0}],
            metrics={"custom_metric": 123},
            start_date=sample_config.start_date,
            end_date=sample_config.end_date,
        )

        with patch.object(adapter._vectorbt, "run_backtest", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = expected_result
            result = await adapter.run_backtest(sample_config, mock_strategy)

        assert result.total_return == Decimal("0.25")
        assert result.sharpe_ratio == Decimal("2.5")
        assert result.max_drawdown == Decimal("0.03")
        assert result.win_rate == Decimal("0.7")
        assert result.profit_factor == Decimal("3.0")
        assert result.num_trades == 50
        assert result.final_capital == Decimal("12500")
        assert len(result.equity_curve) == 2
        assert len(result.trades) == 1
        assert result.metrics["custom_metric"] == 123
