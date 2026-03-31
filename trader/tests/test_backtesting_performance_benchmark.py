"""
Unit Tests for Backtesting Performance Benchmark
===============================================

Tests for:
- PerformanceBenchmark
- BenchmarkResult
- BenchmarkReport
- PerformanceTargets
- BenchmarkRunner
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from trader.services.backtesting.performance_benchmark import (
    BenchmarkReport,
    BenchmarkResult,
    BenchmarkRunner,
    PerformanceBenchmark,
    PerformanceTargets,
)


class TestPerformanceTargets(unittest.TestCase):
    """Tests for PerformanceTargets dataclass"""
    
    def test_default_targets(self):
        """Test default performance targets"""
        targets = PerformanceTargets()
        
        self.assertEqual(targets.one_year_backtest_max, 30.0)
        self.assertEqual(targets.five_year_backtest_max, 120.0)
        self.assertEqual(targets.param_optimization_max, 600.0)
        self.assertEqual(targets.memory_max, 2 * 1024 * 1024 * 1024)
        self.assertEqual(targets.min_bars_per_second, 10000)
    
    def test_custom_targets(self):
        """Test custom performance targets"""
        targets = PerformanceTargets(
            one_year_backtest_max=60.0,
            memory_max=4 * 1024 * 1024 * 1024,
        )
        
        self.assertEqual(targets.one_year_backtest_max, 60.0)
        self.assertEqual(targets.memory_max, 4 * 1024 * 1024 * 1024)


class TestBenchmarkResult(unittest.TestCase):
    """Tests for BenchmarkResult dataclass"""
    
    def test_result_creation(self):
        """Test creating BenchmarkResult"""
        result = BenchmarkResult(
            name="Test",
            duration_seconds=10.5,
            passed=True,
            target_seconds=30.0,
            actual_value=10.5,
            expected_value=30.0,
        )
        
        self.assertEqual(result.name, "Test")
        self.assertEqual(result.duration_seconds, 10.5)
        self.assertTrue(result.passed)
        self.assertAlmostEqual(result.margin_percent, 65.0, places=1)
    
    def test_result_margin_percent(self):
        """Test margin percent calculation"""
        result = BenchmarkResult(
            name="Test",
            duration_seconds=20.0,
            passed=True,
            target_seconds=30.0,
            actual_value=20.0,
            expected_value=30.0,
        )
        
        # (30 - 20) / 30 * 100 = 33.33%
        self.assertAlmostEqual(result.margin_percent, 33.33, places=1)
    
    def test_result_zero_expected(self):
        """Test margin percent with zero expected value"""
        result = BenchmarkResult(
            name="Test",
            duration_seconds=10.0,
            passed=False,
            target_seconds=0,
            actual_value=10.0,
            expected_value=0,
        )
        
        self.assertEqual(result.margin_percent, 0.0)


class TestBenchmarkReport(unittest.TestCase):
    """Tests for BenchmarkReport dataclass"""
    
    def test_report_creation(self):
        """Test creating BenchmarkReport"""
        result = BenchmarkResult(
            name="Test",
            duration_seconds=10.0,
            passed=True,
            target_seconds=30.0,
            actual_value=10.0,
            expected_value=30.0,
        )
        
        report = BenchmarkReport(
            timestamp=datetime.now(timezone.utc),
            targets=PerformanceTargets(),
            results=[result],
            system_info={"python_version": "3.12"},
        )
        
        self.assertEqual(len(report.results), 1)
        self.assertTrue(report.all_passed)
        self.assertEqual(report.total_duration, 10.0)
    
    def test_report_all_passed(self):
        """Test all_passed property"""
        results = [
            BenchmarkResult(name="Test1", duration_seconds=10, passed=True, target_seconds=30, actual_value=10, expected_value=30),
            BenchmarkResult(name="Test2", duration_seconds=20, passed=True, target_seconds=30, actual_value=20, expected_value=30),
        ]
        
        report = BenchmarkReport(
            timestamp=datetime.now(timezone.utc),
            targets=PerformanceTargets(),
            results=results,
            system_info={},
        )
        
        self.assertTrue(report.all_passed)
    
    def test_report_some_failed(self):
        """Test all_passed when some fail"""
        results = [
            BenchmarkResult(name="Test1", duration_seconds=10, passed=True, target_seconds=30, actual_value=10, expected_value=30),
            BenchmarkResult(name="Test2", duration_seconds=50, passed=False, target_seconds=30, actual_value=50, expected_value=30),
        ]
        
        report = BenchmarkReport(
            timestamp=datetime.now(timezone.utc),
            targets=PerformanceTargets(),
            results=results,
            system_info={},
        )
        
        self.assertFalse(report.all_passed)
    
    def test_report_summary(self):
        """Test summary generation"""
        results = [
            BenchmarkResult(name="Test1", duration_seconds=10, passed=True, target_seconds=30, actual_value=10, expected_value=30),
            BenchmarkResult(name="Test2", duration_seconds=50, passed=False, target_seconds=30, actual_value=50, expected_value=30),
        ]
        
        report = BenchmarkReport(
            timestamp=datetime.now(timezone.utc),
            targets=PerformanceTargets(),
            results=results,
            system_info={"python_version": "3.12"},
        )
        
        summary = report.summary()
        
        self.assertEqual(summary["total_benchmarks"], 2)
        self.assertEqual(summary["passed"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["success_rate"], "50.0%")


class TestPerformanceBenchmark(unittest.TestCase):
    """Tests for PerformanceBenchmark class"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.benchmark = PerformanceBenchmark()
    
    def test_benchmark_creation(self):
        """Test creating PerformanceBenchmark"""
        self.assertIsNotNone(self.benchmark._targets)
        self.assertEqual(len(self.benchmark._results), 0)
    
    def test_benchmark_custom_targets(self):
        """Test creating benchmark with custom targets"""
        targets = PerformanceTargets(one_year_backtest_max=60.0)
        benchmark = PerformanceBenchmark(targets=targets)
        
        self.assertEqual(benchmark._targets.one_year_backtest_max, 60.0)
    
    def test_generate_test_data(self):
        """Test test data generation"""
        data = self.benchmark._generate_test_data(
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            days=1,
            interval_minutes=60,
        )
        
        # 24 hours / 60 min = 24 bars
        self.assertEqual(len(data), 24)
        
        # Check first bar structure
        bar = data[0]
        self.assertIn("timestamp", bar)
        self.assertIn("open", bar)
        self.assertIn("high", bar)
        self.assertIn("low", bar)
        self.assertIn("close", bar)
        self.assertIn("volume", bar)
    
    def test_run_backtest_sync(self):
        """Test synchronous backtest execution"""
        data = [{"close": 100}, {"close": 101}]
        
        result = self.benchmark._run_backtest_sync(data)
        
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["data_points"], 2)
    
    def test_get_system_info(self):
        """Test system info collection"""
        info = self.benchmark._get_system_info()
        
        self.assertIn("python_version", info)
        self.assertIn("platform", info)


class TestBenchmarkRunner(unittest.TestCase):
    """Tests for BenchmarkRunner class"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.runner = BenchmarkRunner()
    
    def test_runner_creation(self):
        """Test creating BenchmarkRunner"""
        self.assertEqual(len(self.runner._benchmarks), 0)
        self.assertEqual(len(self.runner._reports), 0)
    
    def test_add_benchmark(self):
        """Test adding a benchmark"""
        benchmark = PerformanceBenchmark()
        self.runner.add_benchmark("Test Benchmark", benchmark)
        
        self.assertEqual(len(self.runner._benchmarks), 1)
    
    def test_run_all(self):
        """Test running all benchmarks"""
        benchmark = PerformanceBenchmark()
        self.runner.add_benchmark("Test", benchmark)
        
        reports = self.runner.run_all()
        
        self.assertEqual(len(reports), 1)
    
    def test_generate_report_no_benchmarks(self):
        """Test generating report with no benchmarks run"""
        report = self.runner.generate_report()
        
        self.assertEqual(report["status"], "no benchmarks run")
    
    def test_generate_report_with_benchmarks(self):
        """Test generating report after running benchmarks"""
        benchmark = PerformanceBenchmark()
        self.runner.add_benchmark("Test", benchmark)
        self.runner.run_all()
        
        report = self.runner.generate_report()
        
        self.assertEqual(report["total_benchmarks"], 1)
        self.assertEqual(report["passed"], 1)
        self.assertEqual(report["failed"], 0)


class TestBenchmarkIntegration(unittest.TestCase):
    """Integration tests for benchmark module"""
    
    def test_full_benchmark_run(self):
        """Test running a full benchmark session"""
        benchmark = PerformanceBenchmark()
        
        # Run 1-year backtest
        result = benchmark.test_1year_backtest()
        
        self.assertIsInstance(result, BenchmarkResult)
        self.assertEqual(result.name, "1-Year Backtest")
        
        # Run memory test
        mem_result = benchmark.test_memory_usage()
        self.assertIsInstance(mem_result, BenchmarkResult)
        
        # Run throughput test
        throughput_result = benchmark.test_throughput()
        self.assertIsInstance(throughput_result, BenchmarkResult)
        
        # Check all results collected
        self.assertEqual(len(benchmark._results), 3)
    
    def test_all_tests_pass_with_mock_engine(self):
        """Test that all tests pass with mock engine"""
        benchmark = PerformanceBenchmark()
        
        # Run all tests
        report = benchmark.run_all()
        
        # Should all pass with mock engine
        self.assertTrue(report.all_passed)


if __name__ == "__main__":
    unittest.main()
