"""
Unit Tests for Backtesting Report Formatter and Visualizer
===========================================================

Tests for:
- ReportFormatter
- StandardizedBacktestReport
- BacktestVisualizer
- plot_* convenience functions
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List
import unittest

from trader.services.backtesting.report_formatter import (
    ReportFormatter,
    StandardizedBacktestReport,
    ReturnMetrics,
    RiskMetrics,
    RiskAdjustedMetrics,
    TradeStatistics,
    BenchmarkComparison,
    MetaInfo,
)
from trader.services.backtesting.ports import BacktestConfig, BacktestResult


class MockEquityPoint:
    """Mock equity point for testing."""
    def __init__(self, timestamp: datetime, equity: float):
        self.timestamp = timestamp
        self.equity = equity


class MockTrade:
    """Mock trade for testing."""
    def __init__(
        self,
        entry_time: datetime,
        exit_time: datetime,
        pnl: float,
        entry_price: float = 100.0,
        exit_price: float = 105.0,
        direction: str = "BUY",
    ):
        self.entry_time = entry_time
        self.exit_time = exit_time
        self.pnl = Decimal(str(pnl))
        self.entry_price = Decimal(str(entry_price))
        self.exit_price = Decimal(str(exit_price))
        self.direction = direction


class MockBacktestResult:
    """Mock backtest result for testing."""
    def __init__(
        self,
        total_return: float,
        sharpe_ratio: float,
        max_drawdown: float,
        win_rate: float,
        profit_factor: float,
        num_trades: int,
        final_capital: float,
        equity_curve: List[Dict[str, Any]],
        trades: List[Dict[str, Any]],
    ):
        self.total_return = Decimal(str(total_return))
        self.sharpe_ratio = Decimal(str(sharpe_ratio))
        self.max_drawdown = Decimal(str(max_drawdown))
        self.win_rate = Decimal(str(win_rate))
        self.profit_factor = Decimal(str(profit_factor))
        self.num_trades = num_trades
        self.final_capital = Decimal(str(final_capital))
        self.equity_curve = equity_curve
        self.trades = trades


class MockBacktestReport:
    """Mock backtest report for testing."""
    def __init__(self, result: Any, config: Any):
        self.result = result
        self.config = config


class TestReturnMetrics(unittest.TestCase):
    """Tests for ReturnMetrics dataclass."""
    
    def test_return_metrics_creation(self):
        """Test creating ReturnMetrics."""
        metrics = ReturnMetrics(
            total_return=Decimal("15.5"),
            annual_return=Decimal("12.3"),
            cumulative_return=Decimal("15.5"),
            monthly_returns=[Decimal("1.2"), Decimal("-0.5"), Decimal("2.1")],
            best_month=Decimal("2.1"),
            worst_month=Decimal("-0.5"),
        )
        
        self.assertEqual(metrics.total_return, Decimal("15.5"))
        self.assertEqual(metrics.annual_return, Decimal("12.3"))
        self.assertEqual(len(metrics.monthly_returns), 3)
        self.assertEqual(metrics.best_month, Decimal("2.1"))
        self.assertEqual(metrics.worst_month, Decimal("-0.5"))


class TestRiskMetrics(unittest.TestCase):
    """Tests for RiskMetrics dataclass."""
    
    def test_risk_metrics_creation(self):
        """Test creating RiskMetrics."""
        metrics = RiskMetrics(
            max_drawdown=Decimal("5000"),
            max_drawdown_percent=Decimal("5.0"),
            max_drawdown_duration=30,
            var_95=Decimal("2.5"),
            cvar_95=Decimal("3.8"),
            volatility=Decimal("15.2"),
        )
        
        self.assertEqual(metrics.max_drawdown, Decimal("5000"))
        self.assertEqual(metrics.max_drawdown_percent, Decimal("5.0"))
        self.assertEqual(metrics.max_drawdown_duration, 30)
        self.assertEqual(metrics.var_95, Decimal("2.5"))


class TestRiskAdjustedMetrics(unittest.TestCase):
    """Tests for RiskAdjustedMetrics dataclass."""
    
    def test_risk_adjusted_metrics_creation(self):
        """Test creating RiskAdjustedMetrics."""
        metrics = RiskAdjustedMetrics(
            sharpe_ratio=Decimal("1.5"),
            sortino_ratio=Decimal("2.0"),
            calmar_ratio=Decimal("1.2"),
            sterling_ratio=Decimal("0.8"),
            omega_ratio=Decimal("1.3"),
        )
        
        self.assertEqual(metrics.sharpe_ratio, Decimal("1.5"))
        self.assertEqual(metrics.sortino_ratio, Decimal("2.0"))
        self.assertEqual(metrics.calmar_ratio, Decimal("1.2"))


class TestTradeStatistics(unittest.TestCase):
    """Tests for TradeStatistics dataclass."""
    
    def test_trade_statistics_creation(self):
        """Test creating TradeStatistics."""
        stats = TradeStatistics(
            total_trades=100,
            winning_trades=60,
            losing_trades=40,
            win_rate=Decimal("60.0"),
            profit_factor=Decimal("1.5"),
            avg_trade_duration=4.5,
            avg_win=Decimal("500"),
            avg_loss=Decimal("300"),
            largest_win=Decimal("2000"),
            largest_loss=Decimal("800"),
            avg_trades_per_day=Decimal("0.5"),
        )
        
        self.assertEqual(stats.total_trades, 100)
        self.assertEqual(stats.winning_trades, 60)
        self.assertEqual(stats.losing_trades, 40)
        self.assertEqual(stats.win_rate, Decimal("60.0"))


class TestBenchmarkComparison(unittest.TestCase):
    """Tests for BenchmarkComparison dataclass."""
    
    def test_benchmark_comparison_creation(self):
        """Test creating BenchmarkComparison."""
        bench = BenchmarkComparison(
            benchmark_total_return=Decimal("10.0"),
            benchmark_annual_return=Decimal("8.5"),
            benchmark_max_drawdown=Decimal("12.0"),
            alpha=Decimal("5.5"),
            beta=Decimal("0.9"),
            correlation=Decimal("0.85"),
            tracking_error=Decimal("3.0"),
            information_ratio=Decimal("1.8"),
        )
        
        self.assertEqual(bench.benchmark_total_return, Decimal("10.0"))
        self.assertEqual(bench.alpha, Decimal("5.5"))
        self.assertEqual(bench.beta, Decimal("0.9"))


class TestMetaInfo(unittest.TestCase):
    """Tests for MetaInfo dataclass."""
    
    def test_meta_info_creation(self):
        """Test creating MetaInfo."""
        start = datetime(2023, 1, 1, tzinfo=timezone.utc)
        end = datetime(2023, 12, 31, tzinfo=timezone.utc)
        
        meta = MetaInfo(
            framework="quantconnect",
            data_range=(start, end),
            trading_days=252,
            initial_capital=Decimal("100000"),
            final_capital=Decimal("115000"),
            strategy_name="TestStrategy",
            symbol="BTCUSDT",
            interval="1h",
        )
        
        self.assertEqual(meta.framework, "quantconnect")
        self.assertEqual(meta.trading_days, 252)
        self.assertEqual(meta.strategy_name, "TestStrategy")


class TestStandardizedBacktestReport(unittest.TestCase):
    """Tests for StandardizedBacktestReport dataclass."""
    
    def test_report_creation(self):
        """Test creating StandardizedBacktestReport."""
        returns = ReturnMetrics(
            total_return=Decimal("15.5"),
            annual_return=Decimal("12.3"),
            cumulative_return=Decimal("15.5"),
            monthly_returns=[],
            best_month=Decimal("0"),
            worst_month=Decimal("0"),
        )
        risk = RiskMetrics(
            max_drawdown=Decimal("5000"),
            max_drawdown_percent=Decimal("5.0"),
            max_drawdown_duration=30,
            var_95=Decimal("2.5"),
            cvar_95=Decimal("3.8"),
            volatility=Decimal("15.2"),
        )
        risk_adj = RiskAdjustedMetrics(
            sharpe_ratio=Decimal("1.5"),
            sortino_ratio=Decimal("2.0"),
            calmar_ratio=Decimal("1.2"),
            sterling_ratio=Decimal("0.8"),
            omega_ratio=Decimal("1.3"),
        )
        trades = TradeStatistics(
            total_trades=100,
            winning_trades=60,
            losing_trades=40,
            win_rate=Decimal("60.0"),
            profit_factor=Decimal("1.5"),
            avg_trade_duration=4.5,
            avg_win=Decimal("500"),
            avg_loss=Decimal("300"),
            largest_win=Decimal("2000"),
            largest_loss=Decimal("800"),
            avg_trades_per_day=Decimal("0.5"),
        )
        
        report = StandardizedBacktestReport(
            returns=returns,
            risk=risk,
            risk_adjusted=risk_adj,
            trades=trades,
        )
        
        self.assertEqual(report.returns.total_return, Decimal("15.5"))
        self.assertEqual(report.risk.max_drawdown, Decimal("5000"))
        self.assertEqual(report.trades.total_trades, 100)
    
    def test_report_to_dict(self):
        """Test converting report to dictionary."""
        returns = ReturnMetrics(
            total_return=Decimal("15.5"),
            annual_return=Decimal("12.3"),
            cumulative_return=Decimal("15.5"),
            monthly_returns=[Decimal("1.0"), Decimal("2.0")],
            best_month=Decimal("2.0"),
            worst_month=Decimal("1.0"),
        )
        risk = RiskMetrics(
            max_drawdown=Decimal("5000"),
            max_drawdown_percent=Decimal("5.0"),
            max_drawdown_duration=30,
            var_95=Decimal("2.5"),
            cvar_95=Decimal("3.8"),
            volatility=Decimal("15.2"),
        )
        risk_adj = RiskAdjustedMetrics(
            sharpe_ratio=Decimal("1.5"),
            sortino_ratio=Decimal("2.0"),
            calmar_ratio=Decimal("1.2"),
            sterling_ratio=Decimal("0.8"),
            omega_ratio=Decimal("1.3"),
        )
        trades = TradeStatistics(
            total_trades=100,
            winning_trades=60,
            losing_trades=40,
            win_rate=Decimal("60.0"),
            profit_factor=Decimal("1.5"),
            avg_trade_duration=4.5,
            avg_win=Decimal("500"),
            avg_loss=Decimal("300"),
            largest_win=Decimal("2000"),
            largest_loss=Decimal("800"),
            avg_trades_per_day=Decimal("0.5"),
        )
        
        report = StandardizedBacktestReport(
            returns=returns,
            risk=risk,
            risk_adjusted=risk_adj,
            trades=trades,
        )
        
        result = report.to_dict()
        
        self.assertIn("returns", result)
        self.assertIn("risk", result)
        self.assertIn("risk_adjusted", result)
        self.assertIn("trades", result)
        self.assertEqual(result["returns"]["total_return"], 15.5)
        self.assertEqual(result["risk"]["max_drawdown"], 5000.0)
        self.assertEqual(result["trades"]["total_trades"], 100)


class TestReportFormatter(unittest.TestCase):
    """Tests for ReportFormatter class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.formatter = ReportFormatter()
        
        # Create mock config
        self.config = BacktestConfig(
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000"),
            symbol="BTCUSDT",
            interval="1h",
        )
        
        # Create mock equity curve
        base_time = datetime(2023, 1, 1, tzinfo=timezone.utc)
        self.equity_curve = [
            {"timestamp": base_time + timedelta(days=i), "equity": 100000 + i * 100}
            for i in range(100)
        ]
        
        # Create mock trades
        self.trades = [
            {
                "entry_time": base_time + timedelta(days=i * 10),
                "exit_time": base_time + timedelta(days=i * 10 + 5),
                "pnl": Decimal("500") if i % 2 == 0 else Decimal("-300"),
                "entry_price": Decimal("100"),
                "exit_price": Decimal("105") if i % 2 == 0 else Decimal("97"),
                "direction": "BUY",
            }
            for i in range(10)
        ]
    
    def test_format_basic(self):
        """Test basic formatting."""
        result = MockBacktestResult(
            total_return=15.5,
            sharpe_ratio=1.5,
            max_drawdown=5.0,
            win_rate=60.0,
            profit_factor=1.5,
            num_trades=10,
            final_capital=115500,
            equity_curve=self.equity_curve,
            trades=self.trades,
        )
        
        report = self.formatter.format(result, self.config, "TestStrategy")
        
        self.assertIsInstance(report, StandardizedBacktestReport)
        self.assertIsNotNone(report.returns)
        self.assertIsNotNone(report.risk)
        self.assertIsNotNone(report.trades)
        self.assertIsNotNone(report.meta)
        
        # Check meta info
        self.assertEqual(report.meta.strategy_name, "TestStrategy")
        self.assertEqual(report.meta.symbol, "BTCUSDT")
        self.assertEqual(report.meta.initial_capital, Decimal("100000"))
    
    def test_format_with_empty_equity_curve(self):
        """Test formatting with empty equity curve."""
        result = MockBacktestResult(
            total_return=0,
            sharpe_ratio=0,
            max_drawdown=0,
            win_rate=0,
            profit_factor=0,
            num_trades=0,
            final_capital=100000,
            equity_curve=[],
            trades=[],
        )
        
        report = self.formatter.format(result, self.config, "EmptyTest")
        
        self.assertIsInstance(report, StandardizedBacktestReport)
        # Should handle empty data gracefully
    
    def test_calculate_buy_and_hold(self):
        """Test Buy & Hold benchmark calculation."""
        equity_curve = [
            MockEquityPoint(datetime(2023, 1, 1, tzinfo=timezone.utc), 100000),
            MockEquityPoint(datetime(2023, 6, 30, tzinfo=timezone.utc), 110000),
            MockEquityPoint(datetime(2023, 12, 31, tzinfo=timezone.utc), 120000),
        ]
        
        result = self.formatter.calculate_buy_and_hold(self.config, equity_curve)
        
        self.assertIn("total_return", result)
        self.assertIn("annual_return", result)
        self.assertIn("max_drawdown", result)
        self.assertGreater(result["total_return"], 0)
    
    def test_calculate_sharpe_ratio(self):
        """Test Sharpe ratio calculation."""
        returns = [Decimal("0.01"), Decimal("-0.005"), Decimal("0.015"), Decimal("0.008"), Decimal("-0.002")]
        
        sharpe = self.formatter._calculate_sharpe_ratio(returns)
        
        self.assertIsInstance(sharpe, Decimal)
        # Sharpe should be positive for positive returns with low volatility
        self.assertGreater(float(sharpe), 0)
    
    def test_calculate_sortino_ratio(self):
        """Test Sortino ratio calculation."""
        returns = [Decimal("0.01"), Decimal("-0.005"), Decimal("0.015"), Decimal("0.008"), Decimal("-0.002")]
        
        sortino = self.formatter._calculate_sortino_ratio(returns)
        
        self.assertIsInstance(sortino, Decimal)
    
    def test_calculate_var(self):
        """Test VaR calculation."""
        returns = [Decimal(str(r)) for r in [0.01, -0.02, 0.015, -0.01, 0.005, -0.015, 0.02]]
        
        var = self.formatter._calculate_var(returns, 0.95)
        
        self.assertIsInstance(var, Decimal)
        self.assertGreater(var, 0)
    
    def test_calculate_cvar(self):
        """Test CVaR calculation."""
        returns = [Decimal(str(r)) for r in [0.01, -0.02, 0.015, -0.01, 0.005, -0.015, 0.02]]
        
        cvar = self.formatter._calculate_cvar(returns, 0.95)
        
        self.assertIsInstance(cvar, Decimal)
        self.assertGreater(cvar, 0)
    
    def test_calculate_volatility(self):
        """Test volatility calculation."""
        returns = [Decimal(str(r)) for r in [0.01, -0.005, 0.015, 0.008, -0.002, 0.012, -0.008, 0.01]]
        
        vol = self.formatter._calculate_volatility(returns)
        
        self.assertIsInstance(vol, Decimal)
        self.assertGreater(vol, 0)
    
    def test_calculate_omega_ratio(self):
        """Test Omega ratio calculation."""
        returns = [Decimal("0.01"), Decimal("-0.005"), Decimal("0.015"), Decimal("0.008"), Decimal("-0.002")]
        
        omega = self.formatter._calculate_omega_ratio(returns)
        
        self.assertIsInstance(omega, Decimal)
        self.assertGreater(omega, 0)
    
    def test_calculate_equity_drawdown(self):
        """Test drawdown calculation."""
        equity_curve = [
            {"timestamp": datetime(2023, 1, 1, tzinfo=timezone.utc), "equity": 100000},
            {"timestamp": datetime(2023, 2, 1, tzinfo=timezone.utc), "equity": 110000},
            {"timestamp": datetime(2023, 3, 1, tzinfo=timezone.utc), "equity": 105000},  # Drawdown from 110000
            {"timestamp": datetime(2023, 4, 1, tzinfo=timezone.utc), "equity": 115000},
        ]
        
        max_dd, max_dd_pct = self.formatter._calculate_equity_drawdown(equity_curve, Decimal("100000"))
        
        self.assertIsInstance(max_dd, Decimal)
        self.assertIsInstance(max_dd_pct, Decimal)
        # Should have a drawdown from 110000 to 105000 = 5000
        self.assertEqual(max_dd, Decimal("5000"))
    
    def test_monthly_returns_calculation(self):
        """Test monthly returns calculation."""
        # Create equity curve spanning multiple months
        base = datetime(2023, 1, 1, tzinfo=timezone.utc)
        equity_curve = [
            {"timestamp": base + timedelta(days=i), "equity": 100000 + i * 100}
            for i in range(90)  # ~3 months
        ]
        
        monthly = self.formatter._calculate_monthly_returns(equity_curve)
        
        self.assertIsInstance(monthly, list)
        # Should have returns for each month with data
    
    def test_format_with_benchmark(self):
        """Test formatting with benchmark comparison."""
        backtest_result = MockBacktestResult(
            total_return=15.5,
            sharpe_ratio=1.5,
            max_drawdown=5.0,
            win_rate=60.0,
            profit_factor=1.5,
            num_trades=10,
            final_capital=115500,
            equity_curve=self.equity_curve,
            trades=self.trades,
        )
        
        # Wrap in MockBacktestReport since format_with_benchmark expects .result attribute
        report_wrapper = MockBacktestReport(result=backtest_result, config=self.config)
        
        benchmark = {
            "total_return": Decimal("10.0"),
            "annual_return": Decimal("8.5"),
            "max_drawdown": Decimal("12.0"),
            "max_drawdown_percent": Decimal("12.0"),
        }
        
        report = self.formatter.format_with_benchmark(report_wrapper, self.config, benchmark, "TestStrategy")
        
        self.assertIsInstance(report, StandardizedBacktestReport)
        self.assertIsNotNone(report.benchmark)
        self.assertEqual(report.benchmark.benchmark_total_return, Decimal("10.0"))
        self.assertEqual(report.benchmark.alpha, Decimal("5.5"))  # Strategy beat benchmark by 5.5%


class TestReportFormatterEdgeCases(unittest.TestCase):
    """Tests for edge cases in ReportFormatter."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.formatter = ReportFormatter()
    
    def test_empty_equity_curve(self):
        """Test handling of empty equity curve."""
        config = BacktestConfig(
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000"),
            symbol="BTCUSDT",
        )
        
        result = MockBacktestResult(
            total_return=0,
            sharpe_ratio=0,
            max_drawdown=0,
            win_rate=0,
            profit_factor=0,
            num_trades=0,
            final_capital=100000,
            equity_curve=[],
            trades=[],
        )
        
        returns = self.formatter._calculate_return_metrics(result, [], config)
        
        self.assertEqual(returns.total_return, Decimal("0"))
        self.assertEqual(len(returns.monthly_returns), 0)
    
    def test_single_point_equity_curve(self):
        """Test handling of single point equity curve."""
        config = BacktestConfig(
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000"),
            symbol="BTCUSDT",
        )
        
        equity_curve = [
            {"timestamp": datetime(2023, 1, 1, tzinfo=timezone.utc), "equity": 100000}
        ]
        
        result = MockBacktestResult(
            total_return=0,
            sharpe_ratio=0,
            max_drawdown=0,
            win_rate=0,
            profit_factor=0,
            num_trades=0,
            final_capital=100000,
            equity_curve=equity_curve,
            trades=[],
        )
        
        risk = self.formatter._calculate_risk_metrics(result, equity_curve, config)
        
        self.assertEqual(risk.max_drawdown, Decimal("0"))
    
    def test_all_winning_trades(self):
        """Test handling of all winning trades."""
        config = BacktestConfig(
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000"),
            symbol="BTCUSDT",
        )
        
        trades = [
            {"pnl": Decimal("100"), "entry_time": datetime(2023, 1, 1), "exit_time": datetime(2023, 1, 2)}
            for _ in range(10)
        ]
        
        stats = self.formatter._calculate_trade_statistics(trades, [], config)
        
        self.assertEqual(stats.total_trades, 10)
        self.assertEqual(stats.winning_trades, 10)
        self.assertEqual(stats.losing_trades, 0)
        self.assertEqual(stats.win_rate, Decimal("100"))
    
    def test_all_losing_trades(self):
        """Test handling of all losing trades."""
        config = BacktestConfig(
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000"),
            symbol="BTCUSDT",
        )
        
        trades = [
            {"pnl": Decimal("-100"), "entry_time": datetime(2023, 1, 1), "exit_time": datetime(2023, 1, 2)}
            for _ in range(10)
        ]
        
        stats = self.formatter._calculate_trade_statistics(trades, [], config)
        
        self.assertEqual(stats.total_trades, 10)
        self.assertEqual(stats.winning_trades, 0)
        self.assertEqual(stats.losing_trades, 10)
        self.assertEqual(stats.win_rate, Decimal("0"))
    
    def test_zero_initial_capital(self):
        """Test handling of zero initial capital."""
        config = BacktestConfig(
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("0"),
            symbol="BTCUSDT",
        )
        
        result = MockBacktestResult(
            total_return=0,
            sharpe_ratio=0,
            max_drawdown=0,
            win_rate=0,
            profit_factor=0,
            num_trades=0,
            final_capital=0,
            equity_curve=[],
            trades=[],
        )
        
        returns = self.formatter._calculate_return_metrics(result, [], config)
        
        # Should handle gracefully without division by zero
        self.assertIsNotNone(returns)


class TestBacktestVisualizer(unittest.TestCase):
    """Tests for BacktestVisualizer class."""
    
    def test_plot_config_defaults(self):
        """Test PlotConfig default values."""
        from trader.services.backtesting.visualizer import PlotConfig
        
        config = PlotConfig()
        
        self.assertEqual(config.figsize, (12, 6))
        self.assertEqual(config.dpi, 100)
        self.assertIsNotNone(config.color_scheme)
        self.assertIn("equity", config.color_scheme)
        self.assertIn("drawdown", config.color_scheme)
    
    def test_plot_config_custom_values(self):
        """Test PlotConfig with custom values."""
        from trader.services.backtesting.visualizer import PlotConfig
        
        config = PlotConfig(
            figsize=(16, 9),
            dpi=150,
            color_scheme={"custom": "#ABCDEF"},
        )
        
        self.assertEqual(config.figsize, (16, 9))
        self.assertEqual(config.dpi, 150)
        self.assertEqual(config.color_scheme["custom"], "#ABCDEF")
    
    def test_visualizer_integration_with_report(self):
        """Test visualizer can process a StandardizedBacktestReport."""
        from trader.services.backtesting.visualizer import BacktestVisualizer
        from trader.services.backtesting.visualizer import _HAS_MATPLOTLIB
        
        if not _HAS_MATPLOTLIB:
            self.skipTest("matplotlib not available")
        
        # Create a mock report with required attributes
        returns = ReturnMetrics(
            total_return=Decimal("15.5"),
            annual_return=Decimal("12.3"),
            cumulative_return=Decimal("15.5"),
            monthly_returns=[],
            best_month=Decimal("0"),
            worst_month=Decimal("0"),
        )
        risk = RiskMetrics(
            max_drawdown=Decimal("5000"),
            max_drawdown_percent=Decimal("5.0"),
            max_drawdown_duration=30,
            var_95=Decimal("2.5"),
            cvar_95=Decimal("3.8"),
            volatility=Decimal("15.2"),
        )
        risk_adj = RiskAdjustedMetrics(
            sharpe_ratio=Decimal("1.5"),
            sortino_ratio=Decimal("2.0"),
            calmar_ratio=Decimal("1.2"),
            sterling_ratio=Decimal("0.8"),
            omega_ratio=Decimal("1.3"),
        )
        trades = TradeStatistics(
            total_trades=100,
            winning_trades=60,
            losing_trades=40,
            win_rate=Decimal("60.0"),
            profit_factor=Decimal("1.5"),
            avg_trade_duration=4.5,
            avg_win=Decimal("500"),
            avg_loss=Decimal("300"),
            largest_win=Decimal("2000"),
            largest_loss=Decimal("800"),
            avg_trades_per_day=Decimal("0.5"),
        )
        
        report = StandardizedBacktestReport(
            returns=returns,
            risk=risk,
            risk_adjusted=risk_adj,
            trades=trades,
        )
        
        # Add equity curve to result
        base_time = datetime(2023, 1, 1, tzinfo=timezone.utc)
        report.result = MockBacktestResult(
            total_return=15.5,
            sharpe_ratio=1.5,
            max_drawdown=5.0,
            win_rate=60.0,
            profit_factor=1.5,
            num_trades=10,
            final_capital=115500,
            equity_curve=[
                {"timestamp": base_time + timedelta(days=i), "equity": 100000 + i * 100}
                for i in range(50)
            ],
            trades=[
                {"entry_time": base_time, "exit_time": base_time + timedelta(days=5), "pnl": 500}
            ],
        )
        
        viz = BacktestVisualizer()
        
        # These should not raise exceptions
        # Note: We're not actually checking the figure output, just that methods don't crash
        self.assertIsNotNone(viz.config)
        self.assertIsNotNone(viz._get_equity_curve(report))
        self.assertIsNotNone(viz._get_trades(report))


class TestVisualizerConvenienceFunctions(unittest.TestCase):
    """Tests for visualizer convenience functions."""
    
    def test_convenience_functions_exist(self):
        """Test that convenience functions are importable."""
        from trader.services.backtesting.visualizer import (
            plot_equity_curve,
            plot_drawdown,
            plot_monthly_heatmap,
            plot_trade_markers,
            plot_returns_distribution,
            plot_combined,
        )
        
        self.assertTrue(callable(plot_equity_curve))
        self.assertTrue(callable(plot_drawdown))
        self.assertTrue(callable(plot_monthly_heatmap))
        self.assertTrue(callable(plot_trade_markers))
        self.assertTrue(callable(plot_returns_distribution))
        self.assertTrue(callable(plot_combined))


class TestVisualizerTimestampParsing(unittest.TestCase):
    """Tests for timestamp parsing in visualizer."""
    
    def setUp(self):
        """Set up test fixtures."""
        from trader.services.backtesting.visualizer import BacktestVisualizer, _HAS_MATPLOTLIB
        
        if not _HAS_MATPLOTLIB:
            self.skipTest("matplotlib not available")
            return
        
        self.viz = BacktestVisualizer()
    
    def test_parse_timestamp_int(self):
        """Test parsing integer timestamp."""
        ts = 1704067200  # 2024-01-01 00:00:00 UTC
        
        result = self.viz._parse_timestamps([ts])
        
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], datetime)
    
    def test_parse_timestamp_float(self):
        """Test parsing float timestamp."""
        ts = 1704067200.0
        
        result = self.viz._parse_timestamps([ts])
        
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], datetime)
    
    def test_parse_timestamp_string(self):
        """Test parsing ISO format string."""
        ts = "2024-01-01T00:00:00+00:00"
        
        result = self.viz._parse_timestamps([ts])
        
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], datetime)
    
    def test_parse_timestamp_datetime(self):
        """Test passing datetime directly."""
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        
        result = self.viz._parse_timestamps([ts])
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ts)
    
    def test_parse_timestamps_mixed(self):
        """Test parsing mixed format timestamps."""
        ts1 = 1704067200
        ts2 = "2024-01-02T00:00:00+00:00"
        ts3 = datetime(2024, 1, 3, tzinfo=timezone.utc)
        
        result = self.viz._parse_timestamps([ts1, ts2, ts3])
        
        self.assertEqual(len(result), 3)
        self.assertTrue(all(isinstance(ts, datetime) for ts in result))


class TestVisualizerEquityParsing(unittest.TestCase):
    """Tests for equity value parsing in visualizer."""
    
    def setUp(self):
        """Set up test fixtures."""
        from trader.services.backtesting.visualizer import BacktestVisualizer, _HAS_MATPLOTLIB
        
        if not _HAS_MATPLOTLIB:
            self.skipTest("matplotlib not available")
            return
        
        self.viz = BacktestVisualizer()
    
    def test_parse_equity_int(self):
        """Test parsing integer equity."""
        result = self.viz._parse_equities([100000])
        
        self.assertEqual(result, [100000.0])
    
    def test_parse_equity_float(self):
        """Test parsing float equity."""
        result = self.viz._parse_equities([100000.5])
        
        self.assertEqual(result, [100000.5])
    
    def test_parse_equity_decimal(self):
        """Test parsing Decimal equity."""
        result = self.viz._parse_equities([Decimal("100000.50")])
        
        self.assertEqual(result, [100000.5])
    
    def test_parse_equity_string(self):
        """Test parsing string equity."""
        result = self.viz._parse_equities(["100000.50"])
        
        self.assertEqual(result, [100000.5])
    
    def test_parse_equities_mixed(self):
        """Test parsing mixed format equities."""
        result = self.viz._parse_equities([100000, Decimal("100001.5"), "100002.0"])
        
        self.assertEqual(result, [100000.0, 100001.5, 100002.0])


if __name__ == "__main__":
    unittest.main()
