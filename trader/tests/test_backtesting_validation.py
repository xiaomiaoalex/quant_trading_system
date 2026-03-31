"""
Unit Tests for Backtesting Validation Framework
================================================

Tests for:
- WalkForwardAnalyzer
- KFoldValidator
- SensitivityAnalyzer
- OverfittingDetector
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List
import unittest

from trader.services.backtesting.validation import (
    ValidationStatus,
    WalkForwardAnalyzer,
    WalkForwardReport,
    WalkForwardSplit,
    KFoldValidator,
    KFoldReport,
    KFoldSplit,
    SensitivityAnalyzer,
    SensitivityReport,
    SensitivityResult,
    OverfittingDetector,
    OverfittingReport,
)
from trader.services.backtesting.ports import BacktestConfig, BacktestResult


class MockBacktestResult:
    """Mock backtest result for testing."""
    def __init__(
        self,
        total_return: float = 0,
        sharpe_ratio: float = 0,
        max_drawdown: float = 0,
        win_rate: float = 0,
        profit_factor: float = 0,
        num_trades: int = 0,
        final_capital: float = 100000,
    ):
        self.total_return = Decimal(str(total_return))
        self.sharpe_ratio = Decimal(str(sharpe_ratio))
        self.max_drawdown = Decimal(str(max_drawdown))
        self.win_rate = Decimal(str(win_rate))
        self.profit_factor = Decimal(str(profit_factor))
        self.num_trades = num_trades
        self.final_capital = Decimal(str(final_capital))
        self.equity_curve = []
        self.trades = []
        self.metrics = {}


def create_mock_data(start: datetime, days: int, start_price: float = 100) -> List[Dict[str, Any]]:
    """Create mock price data."""
    data = []
    price = start_price
    for i in range(days):
        ts = start + timedelta(days=i)
        price = price * (1 + (0.001 if i % 2 == 0 else -0.0005))
        data.append({
            "timestamp": ts,
            "open": price,
            "high": price * 1.01,
            "low": price * 0.99,
            "close": price,
            "volume": 1000,
        })
    return data


class TestValidationStatus(unittest.TestCase):
    """Tests for ValidationStatus enum."""
    
    def test_validation_status_values(self):
        """Test ValidationStatus enum values."""
        self.assertEqual(ValidationStatus.PASSED.value, "PASSED")
        self.assertEqual(ValidationStatus.FAILED.value, "FAILED")
        self.assertEqual(ValidationStatus.WARNING.value, "WARNING")
        self.assertEqual(ValidationStatus.INSUFFICIENT_DATA.value, "INSUFFICIENT_DATA")


class TestWalkForwardSplit(unittest.TestCase):
    """Tests for WalkForwardSplit dataclass."""
    
    def test_walk_forward_split_creation(self):
        """Test creating WalkForwardSplit."""
        now = datetime.now(timezone.utc)
        split = WalkForwardSplit(
            split_index=0,
            train_start=now,
            train_end=now + timedelta(days=90),
            test_start=now + timedelta(days=90),
            test_end=now + timedelta(days=120),
            train_result=None,
            test_result=None,
            best_params={"rsi_period": 14},
            train_metrics={"sharpe_ratio": 1.5},
            test_metrics={"sharpe_ratio": 1.2},
        )
        
        self.assertEqual(split.split_index, 0)
        self.assertEqual(split.best_params, {"rsi_period": 14})
        self.assertEqual(split.train_metrics["sharpe_ratio"], 1.5)


class TestWalkForwardReport(unittest.TestCase):
    """Tests for WalkForwardReport dataclass."""
    
    def test_walk_forward_report_creation(self):
        """Test creating WalkForwardReport."""
        report = WalkForwardReport(
            splits=[],
            in_sample_metrics={"sharpe_ratio_mean": 1.5},
            out_of_sample_metrics={"sharpe_ratio_mean": 1.2},
            overfitting_score=0.2,
            overfitting_status=ValidationStatus.PASSED,
            consistency_score=0.85,
            avg_params_stability={"rsi_period": 1.0},
        )
        
        self.assertEqual(report.overfitting_score, 0.2)
        self.assertEqual(report.overfitting_status, ValidationStatus.PASSED)
        self.assertEqual(report.consistency_score, 0.85)


class TestWalkForwardAnalyzer(unittest.TestCase):
    """Tests for WalkForwardAnalyzer class."""
    
    def setUp(self):
        """Set up test fixtures."""
        def mock_backtest(config: BacktestConfig, params: Dict[str, Any]) -> MockBacktestResult:
            """Mock backtest function."""
            return MockBacktestResult(
                total_return=10.0,
                sharpe_ratio=1.5,
                max_drawdown=5.0,
                win_rate=60.0,
                num_trades=20,
            )
        
        self.analyzer = WalkForwardAnalyzer(backtest_func=mock_backtest)
        self.mock_data = create_mock_data(datetime(2023, 1, 1, tzinfo=timezone.utc), 365)
    
    def test_analyze_insufficient_data(self):
        """Test analyze with insufficient data."""
        analyzer = WalkForwardAnalyzer(backtest_func=lambda c, p: MockBacktestResult())
        report = analyzer.analyze(
            strategy_class=object,
            param_grid={"period": [10, 20]},
            data=[],
            train_period=timedelta(days=90),
            test_period=timedelta(days=30),
        )
        
        self.assertEqual(report.overfitting_status, ValidationStatus.INSUFFICIENT_DATA)
    
    def test_analyze_basic(self):
        """Test basic walk-forward analysis."""
        report = self.analyzer.analyze(
            strategy_class=object,
            param_grid={"period": [10, 20]},
            data=self.mock_data,
            train_period=timedelta(days=90),
            test_period=timedelta(days=30),
            n_splits=3,
        )
        
        self.assertIsInstance(report, WalkForwardReport)
        self.assertGreaterEqual(len(report.splits), 1)
        self.assertIn("sharpe_ratio_mean", report.in_sample_metrics)
    
    def test_analyze_extract_metrics(self):
        """Test metric extraction from mock backtest."""
        result = MockBacktestResult(
            total_return=15.5,
            sharpe_ratio=1.8,
            max_drawdown=4.2,
            win_rate=62.0,
        )
        
        metrics = self.analyzer._extract_metrics(result, "sharpe_ratio")
        
        self.assertEqual(metrics["sharpe_ratio"], 1.8)
        self.assertEqual(metrics["total_return"], 15.5)
        self.assertEqual(metrics["max_drawdown"], 4.2)
    
    def test_analyze_aggregate_metrics(self):
        """Test metric aggregation."""
        metrics_list = [
            {"sharpe_ratio": 1.0, "total_return": 10.0},
            {"sharpe_ratio": 2.0, "total_return": 20.0},
            {"sharpe_ratio": 1.5, "total_return": 15.0},
        ]
        
        aggregated = self.analyzer._aggregate_metrics(metrics_list)
        
        self.assertEqual(aggregated["sharpe_ratio_mean"], 1.5)
        self.assertEqual(aggregated["total_return_mean"], 15.0)
        self.assertGreater(aggregated["sharpe_ratio_std"], 0)
    
    def test_calculate_overfitting_passed(self):
        """Test overfitting calculation - passed case."""
        in_sample = {"sharpe_ratio_mean": 1.5}
        out_sample = {"sharpe_ratio_mean": 1.3}
        
        score, status = self.analyzer._calculate_overfitting(in_sample, out_sample)
        
        self.assertLess(score, 0.5)
        self.assertEqual(status, ValidationStatus.PASSED)
    
    def test_calculate_overfitting_failed(self):
        """Test overfitting calculation - failed case."""
        in_sample = {"sharpe_ratio_mean": 2.0}
        out_sample = {"sharpe_ratio_mean": 0.5}
        
        score, status = self.analyzer._calculate_overfitting(in_sample, out_sample)
        
        self.assertGreater(score, 0.5)
        self.assertEqual(status, ValidationStatus.FAILED)
    
    def test_calculate_consistency_high(self):
        """Test consistency calculation - high consistency."""
        metrics_list = [
            {"sharpe_ratio": 1.0},
            {"sharpe_ratio": 1.1},
            {"sharpe_ratio": 0.9},
        ]
        
        consistency = self.analyzer._calculate_consistency(metrics_list)
        
        self.assertGreater(consistency, 0.5)
    
    def test_calculate_consistency_low(self):
        """Test consistency calculation - low consistency."""
        metrics_list = [
            {"sharpe_ratio": 1.0},
            {"sharpe_ratio": 3.0},
            {"sharpe_ratio": 0.5},
        ]
        
        consistency = self.analyzer._calculate_consistency(metrics_list)
        
        self.assertLess(consistency, 0.5)
    
    def test_calculate_param_stability(self):
        """Test parameter stability calculation."""
        params_list = [
            {"rsi_period": 14, "ma_period": 20},
            {"rsi_period": 14, "ma_period": 21},
            {"rsi_period": 14, "ma_period": 19},
        ]
        
        stability = self.analyzer._calculate_param_stability(params_list)
        
        self.assertEqual(stability["rsi_period"], 1.0)  # All same
        self.assertLess(stability["ma_period"], 1.0)  # Different
    
    def test_filter_data_by_date(self):
        """Test date filtering."""
        start = datetime(2023, 1, 1, tzinfo=timezone.utc)
        data = create_mock_data(start, 30)
        
        filtered = self.analyzer._filter_data_by_date(
            data,
            start + timedelta(days=5),
            start + timedelta(days=15),
        )
        
        self.assertLess(len(filtered), len(data))
        self.assertGreaterEqual(len(filtered), 5)


class TestKFoldSplit(unittest.TestCase):
    """Tests for KFoldSplit dataclass."""
    
    def test_kfold_split_creation(self):
        """Test creating KFoldSplit."""
        now = datetime.now(timezone.utc)
        split = KFoldSplit(
            fold_index=0,
            train_start=now,
            train_end=now + timedelta(days=200),
            val_start=now + timedelta(days=200),
            val_end=now + timedelta(days=250),
            train_result=None,
            val_result=None,
            metrics={"sharpe_ratio": 1.2},
        )
        
        self.assertEqual(split.fold_index, 0)
        self.assertEqual(split.metrics["sharpe_ratio"], 1.2)


class TestKFoldReport(unittest.TestCase):
    """Tests for KFoldReport dataclass."""
    
    def test_kfold_report_creation(self):
        """Test creating KFoldReport."""
        report = KFoldReport(
            folds=[],
            avg_train_metrics={"sharpe_ratio": 1.5},
            avg_val_metrics={"sharpe_ratio": 1.2},
            metric_std={"sharpe_ratio": 0.2},
            validation_status=ValidationStatus.PASSED,
        )
        
        self.assertEqual(report.validation_status, ValidationStatus.PASSED)
        self.assertEqual(report.avg_val_metrics["sharpe_ratio"], 1.2)


class TestKFoldValidator(unittest.TestCase):
    """Tests for KFoldValidator class."""
    
    def setUp(self):
        """Set up test fixtures."""
        def mock_backtest(config: BacktestConfig, params: Dict[str, Any]) -> MockBacktestResult:
            return MockBacktestResult(
                total_return=10.0,
                sharpe_ratio=1.5,
                max_drawdown=5.0,
            )
        
        self.validator = KFoldValidator(backtest_func=mock_backtest)
        self.mock_data = create_mock_data(datetime(2023, 1, 1, tzinfo=timezone.utc), 200)
    
    def test_validate_insufficient_data(self):
        """Test validate with insufficient data."""
        validator = KFoldValidator(backtest_func=lambda c, p: MockBacktestResult())
        report = validator.validate(
            strategy_class=object,
            params={},
            data=[],
            n_folds=5,
        )
        
        self.assertEqual(report.validation_status, ValidationStatus.INSUFFICIENT_DATA)
    
    def test_validate_basic(self):
        """Test basic K-fold validation."""
        report = self.validator.validate(
            strategy_class=object,
            params={"period": 14},
            data=self.mock_data,
            n_folds=3,
        )
        
        self.assertIsInstance(report, KFoldReport)
        self.assertGreaterEqual(len(report.folds), 1)
    
    def test_extract_metrics(self):
        """Test metric extraction."""
        result = MockBacktestResult(
            sharpe_ratio=1.8,
            total_return=12.5,
            max_drawdown=3.2,
        )
        
        metrics = self.validator._extract_metrics(result, "sharpe_ratio")
        
        self.assertEqual(metrics["sharpe_ratio"], 1.8)
        self.assertEqual(metrics["total_return"], 12.5)
    
    def test_aggregate_metrics(self):
        """Test metric aggregation."""
        metrics_list = [
            {"sharpe_ratio": 1.0, "total_return": 10.0},
            {"sharpe_ratio": 2.0, "total_return": 20.0},
        ]
        
        aggregated = self.validator._aggregate_metrics(metrics_list)
        
        self.assertEqual(aggregated["sharpe_ratio"], 1.5)
        self.assertEqual(aggregated["total_return"], 15.0)
    
    def test_calculate_metric_std(self):
        """Test metric standard deviation."""
        metrics_list = [
            {"sharpe_ratio": 1.0},
            {"sharpe_ratio": 2.0},
            {"sharpe_ratio": 1.5},
        ]
        
        std_dict = self.validator._calculate_metric_std(metrics_list)
        
        self.assertGreater(std_dict["sharpe_ratio"], 0)
    
    def test_determine_status_passed(self):
        """Test status determination - passed."""
        status = self.validator._determine_status(
            {"sharpe_ratio": 1.5},
            {"sharpe_ratio": 0.1},
        )
        
        self.assertEqual(status, ValidationStatus.PASSED)
    
    def test_determine_status_warning(self):
        """Test status determination - warning."""
        status = self.validator._determine_status(
            {"sharpe_ratio": 0.7},
            {"sharpe_ratio": 0.4},
        )
        
        self.assertEqual(status, ValidationStatus.WARNING)


class TestSensitivityResult(unittest.TestCase):
    """Tests for SensitivityResult dataclass."""
    
    def test_sensitivity_result_creation(self):
        """Test creating SensitivityResult."""
        result = SensitivityResult(
            param_name="rsi_period",
            param_values=[10, 14, 20, 28],
            metric_name="sharpe_ratio",
            metric_values=[1.0, 1.5, 1.3, 0.8],
            best_value=14,
            best_metric=1.5,
            sensitivity_score=0.3,
            stability="HIGH",
        )
        
        self.assertEqual(result.param_name, "rsi_period")
        self.assertEqual(result.best_value, 14)
        self.assertEqual(result.stability, "HIGH")


class TestSensitivityReport(unittest.TestCase):
    """Tests for SensitivityReport dataclass."""
    
    def test_sensitivity_report_creation(self):
        """Test creating SensitivityReport."""
        report = SensitivityReport(
            results=[],
            overall_sensitivity=0.4,
            most_sensitive_params=["ma_period"],
            stable_params=["rsi_period"],
            recommendation="ma_period needs fine-tuning",
        )
        
        self.assertEqual(report.overall_sensitivity, 0.4)
        self.assertEqual(report.most_sensitive_params, ["ma_period"])
        self.assertIn("ma_period", report.recommendation)


class TestSensitivityAnalyzer(unittest.TestCase):
    """Tests for SensitivityAnalyzer class."""
    
    def setUp(self):
        """Set up test fixtures."""
        def mock_backtest(config: BacktestConfig, params: Dict[str, Any]) -> MockBacktestResult:
            period = params.get("period", 14)
            # Simulate different results for different periods
            sharpe = 1.5 if period == 14 else 1.0
            return MockBacktestResult(
                total_return=10.0 + period * 0.1,
                sharpe_ratio=sharpe,
            )
        
        self.analyzer = SensitivityAnalyzer(backtest_func=mock_backtest)
        self.mock_data = create_mock_data(datetime(2023, 1, 1, tzinfo=timezone.utc), 100)
    
    def test_analyze_single_param(self):
        """Test analyzing single parameter."""
        result = self.analyzer._analyze_single_param(
            strategy_class=object,
            param_name="period",
            param_values=[10, 14, 20],
            full_grid={"period": [10, 14, 20]},
            data=self.mock_data,
            metric="sharpe_ratio",
            initial_capital=Decimal("100000"),
        )
        
        self.assertEqual(result.param_name, "period")
        self.assertEqual(len(result.param_values), 3)
        self.assertGreater(len(result.metric_values), 0)
    
    def test_calculate_sensitivity(self):
        """Test sensitivity calculation."""
        # Low variation - low sensitivity
        low_sens = self.analyzer._calculate_sensitivity([1.0, 1.05, 0.95])
        self.assertLess(low_sens, 0.3)
        
        # High variation - high sensitivity
        high_sens = self.analyzer._calculate_sensitivity([1.0, 3.0, 0.5])
        self.assertGreater(high_sens, 0.5)
    
    def test_generate_recommendation(self):
        """Test recommendation generation."""
        results = [
            SensitivityResult(
                param_name="rsi_period",
                param_values=[10, 14, 20],
                metric_name="sharpe_ratio",
                metric_values=[1.0, 1.5, 0.8],
                best_value=14,
                best_metric=1.5,
                sensitivity_score=0.8,
                stability="LOW",
            ),
            SensitivityResult(
                param_name="ma_period",
                param_values=[20, 50, 100],
                metric_name="sharpe_ratio",
                metric_values=[1.2, 1.25, 1.22],
                best_value=50,
                best_metric=1.25,
                sensitivity_score=0.2,
                stability="HIGH",
            ),
        ]
        
        recommendation = self.analyzer._generate_recommendation(results, 0.5)
        
        self.assertIsInstance(recommendation, str)
        self.assertGreater(len(recommendation), 0)


class TestOverfittingReport(unittest.TestCase):
    """Tests for OverfittingReport dataclass."""
    
    def test_overfitting_report_creation(self):
        """Test creating OverfittingReport."""
        report = OverfittingReport(
            in_sample_sharpe=1.5,
            out_of_sample_sharpe=1.2,
            sharpe_ratio_decay=0.8,
            return_decay=0.85,
            drawdown_ratio=1.2,
            validation_status=ValidationStatus.PASSED,
            overfitting_indicators=[],
            recommendations=["Strategy is robust"],
        )
        
        self.assertEqual(report.in_sample_sharpe, 1.5)
        self.assertEqual(report.out_of_sample_sharpe, 1.2)
        self.assertEqual(report.validation_status, ValidationStatus.PASSED)


class TestOverfittingDetector(unittest.TestCase):
    """Tests for OverfittingDetector class."""
    
    def test_detect_passed(self):
        """Test overfitting detection - passed case."""
        detector = OverfittingDetector()
        
        report = WalkForwardReport(
            splits=[],
            in_sample_metrics={
                "sharpe_ratio_mean": 1.5,
                "total_return_mean": 15.0,
                "max_drawdown_mean": 5.0,
            },
            out_of_sample_metrics={
                "sharpe_ratio_mean": 1.3,
                "total_return_mean": 12.0,
                "max_drawdown_mean": 6.0,
            },
            overfitting_score=0.15,
            overfitting_status=ValidationStatus.PASSED,
            consistency_score=0.8,
            avg_params_stability={},
        )
        
        result = detector.detect(report)
        
        self.assertEqual(result.validation_status, ValidationStatus.PASSED)
        self.assertGreater(result.sharpe_ratio_decay, 0.5)
    
    def test_detect_failed(self):
        """Test overfitting detection - failed case."""
        detector = OverfittingDetector()
        
        report = WalkForwardReport(
            splits=[],
            in_sample_metrics={
                "sharpe_ratio_mean": 2.0,
                "total_return_mean": 25.0,
                "max_drawdown_mean": 5.0,
            },
            out_of_sample_metrics={
                "sharpe_ratio_mean": 0.5,
                "total_return_mean": 5.0,
                "max_drawdown_mean": 15.0,
            },
            overfitting_score=0.75,
            overfitting_status=ValidationStatus.FAILED,
            consistency_score=0.2,
            avg_params_stability={},
        )
        
        result = detector.detect(report)
        
        self.assertEqual(result.validation_status, ValidationStatus.FAILED)
        self.assertLess(result.sharpe_ratio_decay, 0.5)
        self.assertGreater(len(result.overfitting_indicators), 0)
    
    def test_detect_warning(self):
        """Test overfitting detection - warning case."""
        detector = OverfittingDetector()
        
        report = WalkForwardReport(
            splits=[],
            in_sample_metrics={
                "sharpe_ratio_mean": 1.5,
                "total_return_mean": 15.0,
                "max_drawdown_mean": 5.0,
            },
            out_of_sample_metrics={
                "sharpe_ratio_mean": 0.8,
                "total_return_mean": 10.0,
                "max_drawdown_mean": 8.0,
            },
            overfitting_score=0.45,
            overfitting_status=ValidationStatus.WARNING,
            consistency_score=0.5,
            avg_params_stability={},
        )
        
        result = detector.detect(report)
        
        self.assertEqual(result.validation_status, ValidationStatus.WARNING)
    
    def test_detect_insufficient_data(self):
        """Test overfitting detection with insufficient data."""
        detector = OverfittingDetector()
        
        report = WalkForwardReport(
            splits=[],
            in_sample_metrics={},
            out_of_sample_metrics={},
            overfitting_score=0.0,
            overfitting_status=ValidationStatus.INSUFFICIENT_DATA,
            consistency_score=0.0,
            avg_params_stability={},
        )
        
        result = detector.detect(report)
        
        self.assertEqual(result.validation_status, ValidationStatus.INSUFFICIENT_DATA)


if __name__ == "__main__":
    unittest.main()
