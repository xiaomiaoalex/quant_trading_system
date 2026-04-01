"""
Unit Tests for Backtesting Lifecycle Integration
===============================================

Tests for:
- AutoApprovalRules
- BacktestLifecycleIntegration
- StrategyComparison
- Helper functions
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List
import unittest

from trader.services.backtesting.lifecycle_integration import (
    AutoApprovalRules,
    BacktestLifecycleIntegration,
    BacktestJob,
    BacktestLifecycleStatus,
    calculate_scorecard,
    is_valid_transition,
    ParameterSweepResult,
    StrategyComparison,
)


class MockBacktestResult:
    """Mock backtest result for testing."""
    def __init__(
        self,
        total_return: float = 15.0,
        sharpe_ratio: float = 1.5,
        max_drawdown: float = 5.0,
        max_drawdown_percent: float = 5.0,
        win_rate: float = 60.0,
        profit_factor: float = 1.5,
        num_trades: int = 50,
    ):
        self.total_return = Decimal(str(total_return))
        self.sharpe_ratio = Decimal(str(sharpe_ratio))
        self.max_drawdown = Decimal(str(max_drawdown))
        self.max_drawdown_percent = Decimal(str(max_drawdown_percent))
        self.win_rate = Decimal(str(win_rate))
        self.profit_factor = Decimal(str(profit_factor))
        self.num_trades = num_trades
        self.final_capital = Decimal("115000")
        self.equity_curve = []
        self.trades = []


class TestAutoApprovalRules(unittest.TestCase):
    """Tests for AutoApprovalRules dataclass."""
    
    def test_default_rules(self):
        """Test default auto approval rules."""
        rules = AutoApprovalRules()
        
        self.assertEqual(rules.min_sharpe, 1.0)
        self.assertEqual(rules.max_drawdown_pct, 20.0)
        self.assertEqual(rules.min_trades, 30)
        self.assertEqual(rules.min_win_rate, 0.4)
        self.assertEqual(rules.max_overfitting_score, 0.3)
    
    def test_custom_rules(self):
        """Test custom auto approval rules."""
        rules = AutoApprovalRules(
            min_sharpe=1.5,
            max_drawdown_pct=15.0,
            min_trades=50,
            min_win_rate=0.5,
        )
        
        self.assertEqual(rules.min_sharpe, 1.5)
        self.assertEqual(rules.max_drawdown_pct, 15.0)
        self.assertEqual(rules.min_trades, 50)
        self.assertEqual(rules.min_win_rate, 0.5)
    
    def test_evaluate_passing_report(self):
        """Test evaluation of passing report."""
        rules = AutoApprovalRules(
            min_sharpe=1.0,
            max_drawdown_pct=20.0,
            min_trades=30,
            min_win_rate=0.4,
        )
        
        report = MockBacktestResult(
            total_return=15.0,
            sharpe_ratio=1.5,
            max_drawdown=5.0,
            max_drawdown_percent=5.0,
            win_rate=60.0,
            profit_factor=1.5,
            num_trades=50,
        )
        
        passed, violations = rules.evaluate(report)
        
        self.assertTrue(passed)
        self.assertEqual(len(violations), 0)
    
    def test_evaluate_failing_sharpe(self):
        """Test evaluation failing due to low Sharpe."""
        rules = AutoApprovalRules(min_sharpe=2.0)
        
        report = MockBacktestResult(sharpe_ratio=1.5)
        
        passed, violations = rules.evaluate(report)
        
        self.assertFalse(passed)
        self.assertTrue(any("夏普比率" in v for v in violations))
    
    def test_evaluate_failing_drawdown(self):
        """Test evaluation failing due to high drawdown."""
        rules = AutoApprovalRules(max_drawdown_pct=10.0)
        
        report = MockBacktestResult(max_drawdown_percent=15.0)
        
        passed, violations = rules.evaluate(report)
        
        self.assertFalse(passed)
        self.assertTrue(any("最大回撤" in v for v in violations))
    
    def test_evaluate_failing_trades(self):
        """Test evaluation failing due to low trade count."""
        rules = AutoApprovalRules(min_trades=50)
        
        report = MockBacktestResult(num_trades=20)
        
        passed, violations = rules.evaluate(report)
        
        self.assertFalse(passed)
        self.assertTrue(any("交易次数" in v for v in violations))
    
    def test_evaluate_failing_win_rate(self):
        """Test evaluation failing due to low win rate."""
        rules = AutoApprovalRules(min_win_rate=0.6)
        
        report = MockBacktestResult(win_rate=50.0)
        
        passed, violations = rules.evaluate(report)
        
        self.assertFalse(passed)
        self.assertTrue(any("胜率" in v for v in violations))
    
    def test_get_metric_from_result(self):
        """Test metric extraction from result."""
        rules = AutoApprovalRules()
        
        report = MockBacktestResult(sharpe_ratio=1.8)
        
        sharpe = rules._get_metric(report, 'sharpe_ratio')
        
        self.assertEqual(sharpe, 1.8)


class TestBacktestJob(unittest.TestCase):
    """Tests for BacktestJob dataclass."""
    
    def test_backtest_job_creation(self):
        """Test creating BacktestJob."""
        job = BacktestJob(
            job_id="test_job_1",
            strategy_id="strategy_1",
            status="RUNNING",
        )
        
        self.assertEqual(job.job_id, "test_job_1")
        self.assertEqual(job.strategy_id, "strategy_1")
        self.assertEqual(job.status, "RUNNING")
        self.assertIsNotNone(job.created_at)
    
    def test_backtest_job_defaults(self):
        """Test BacktestJob default values."""
        job = BacktestJob(
            job_id="test_job_1",
            strategy_id="strategy_1",
        )
        
        self.assertEqual(job.status, "PENDING")
        self.assertIsNone(job.started_at)
        self.assertIsNone(job.completed_at)
        self.assertIsNone(job.report)
        self.assertIsNone(job.error)


class TestBacktestLifecycleStatus(unittest.TestCase):
    """Tests for BacktestLifecycleStatus enum."""
    
    def test_status_values(self):
        """Test lifecycle status values."""
        self.assertEqual(BacktestLifecycleStatus.BACKTESTING.value, "BACKTESTING")
        self.assertEqual(BacktestLifecycleStatus.BACKTESTED.value, "BACKTESTED")


class TestBacktestLifecycleIntegration(unittest.TestCase):
    """Tests for BacktestLifecycleIntegration class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.integration = BacktestLifecycleIntegration()
    
    def test_default_auto_approval_rules(self):
        """Test default auto approval rules."""
        rules = self.integration.auto_approval_rules
        
        self.assertIsInstance(rules, AutoApprovalRules)
        self.assertEqual(rules.min_sharpe, 1.0)
    
    def test_set_auto_approval_rules(self):
        """Test setting custom auto approval rules."""
        rules = AutoApprovalRules(min_sharpe=2.0)
        self.integration.set_auto_approval_rules(rules)
        
        self.assertEqual(self.integration.auto_approval_rules.min_sharpe, 2.0)
    
    def test_evaluate_auto_approval(self):
        """Test auto approval evaluation."""
        rules = AutoApprovalRules(min_sharpe=1.0)
        self.integration.set_auto_approval_rules(rules)
        
        report = MockBacktestResult(sharpe_ratio=1.5)
        passed, violations = self.integration.evaluate_auto_approval(report)
        
        self.assertTrue(passed)
    
    def test_get_job_not_found(self):
        """Test getting non-existent job."""
        job = self.integration.get_job("non_existent")
        
        self.assertIsNone(job)
    
    def test_list_jobs_empty(self):
        """Test listing jobs when none exist."""
        jobs = self.integration.list_jobs()
        
        self.assertEqual(len(jobs), 0)
    
    def test_list_jobs_by_strategy(self):
        """Test listing jobs by strategy ID."""
        # Create some mock jobs
        job1 = BacktestJob(job_id="job1", strategy_id="strategy_1")
        job2 = BacktestJob(job_id="job2", strategy_id="strategy_2")
        self.integration._jobs["job1"] = job1
        self.integration._jobs["job2"] = job2
        
        jobs = self.integration.list_jobs("strategy_1")
        
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_id, "job1")


class TestStrategyComparison(unittest.TestCase):
    """Tests for StrategyComparison class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.comparison = StrategyComparison()
    
    def test_compare_same_length_required(self):
        """Test that reports and names must have same length."""
        with self.assertRaises(ValueError):
            self.comparison.compare(
                reports=[MockBacktestResult(), MockBacktestResult()],
                strategy_names=["Strategy1"],
            )
    
    def test_compare_basic(self):
        """Test basic strategy comparison."""
        reports = [
            MockBacktestResult(sharpe_ratio=1.5, total_return=15.0),
            MockBacktestResult(sharpe_ratio=2.0, total_return=20.0),
            MockBacktestResult(sharpe_ratio=1.0, total_return=10.0),
        ]
        names = ["StrategyA", "StrategyB", "StrategyC"]
        
        result = self.comparison.compare(reports, names)
        
        self.assertIn("metrics", result)
        self.assertIn("rankings", result)
        self.assertIn("summary", result)
        self.assertEqual(result["metrics"]["sharpe_ratio"]["StrategyB"], 2.0)
    
    def test_compare_ranking(self):
        """Test strategy ranking."""
        reports = [
            MockBacktestResult(sharpe_ratio=1.5),
            MockBacktestResult(sharpe_ratio=2.0),
            MockBacktestResult(sharpe_ratio=1.0),
        ]
        names = ["A", "B", "C"]
        
        result = self.comparison.compare(reports, names)
        
        sharpe_ranking = result["rankings"]["sharpe_ratio"]
        self.assertEqual(sharpe_ranking[0]["strategy"], "B")  # Best
        self.assertEqual(sharpe_ranking[2]["strategy"], "C")  # Worst
    
    def test_compare_best_strategy(self):
        """Test best strategy identification."""
        reports = [
            MockBacktestResult(sharpe_ratio=1.0, win_rate=50.0),
            MockBacktestResult(sharpe_ratio=2.0, win_rate=60.0),
        ]
        names = ["A", "B"]
        
        result = self.comparison.compare(reports, names)
        
        self.assertEqual(result["summary"]["best_strategy"], "B")
    
    def test_extract_metric(self):
        """Test metric extraction."""
        report = MockBacktestResult(sharpe_ratio=1.8)
        
        value = self.comparison._extract_metric(report, "sharpe_ratio")
        
        self.assertEqual(value, 1.8)
    
    def test_extract_missing_metric(self):
        """Test extraction of missing metric."""
        report = MockBacktestResult()
        
        value = self.comparison._extract_metric(report, "non_existent")
        
        self.assertEqual(value, 0.0)


class TestIsValidTransition(unittest.TestCase):
    """Tests for is_valid_transition function."""
    
    def test_valid_draft_to_validated(self):
        """Test DRAFT -> VALIDATED transition."""
        self.assertTrue(is_valid_transition("DRAFT", "VALIDATED"))
    
    def test_valid_validated_to_backtesting(self):
        """Test VALIDATED -> BACKTESTING transition."""
        self.assertTrue(is_valid_transition("VALIDATED", "BACKTESTING"))
    
    def test_valid_backtesting_to_backtested(self):
        """Test BACKTESTING -> BACKTESTED transition."""
        self.assertTrue(is_valid_transition("BACKTESTING", "BACKTESTED"))
    
    def test_valid_backtesting_to_failed(self):
        """Test BACKTESTING -> FAILED transition."""
        self.assertTrue(is_valid_transition("BACKTESTING", "FAILED"))
    
    def test_valid_failed_to_validated_retry(self):
        """Test FAILED -> VALIDATED retry transition."""
        self.assertTrue(is_valid_transition("FAILED", "VALIDATED"))
    
    def test_invalid_draft_to_running(self):
        """Test invalid DRAFT -> RUNNING transition."""
        self.assertFalse(is_valid_transition("DRAFT", "RUNNING"))
    
    def test_invalid_backtested_to_draft(self):
        """Test invalid BACKTESTED -> DRAFT transition."""
        self.assertFalse(is_valid_transition("BACKTESTED", "DRAFT"))
    
    def test_invalid_archived_transitions(self):
        """Test no transitions from ARCHIVED."""
        self.assertFalse(is_valid_transition("ARCHIVED", "DRAFT"))
        self.assertFalse(is_valid_transition("ARCHIVED", "VALIDATED"))


class TestCalculateScorecard(unittest.TestCase):
    """Tests for calculate_scorecard function."""
    
    def test_scorecard_basic(self):
        """Test basic scorecard calculation."""
        report = MockBacktestResult(
            total_return=15.0,
            sharpe_ratio=1.5,
            max_drawdown_percent=5.0,
            win_rate=60.0,
            profit_factor=1.5,
            num_trades=50,
        )
        
        scorecard = calculate_scorecard(report)
        
        self.assertIn("component_scores", scorecard)
        self.assertIn("overall_score", scorecard)
        self.assertIn("grade", scorecard)
        self.assertIn("metrics", scorecard)
    
    def test_scorecard_sharpe_component(self):
        """Test Sharpe ratio component score."""
        report = MockBacktestResult(sharpe_ratio=2.0)
        
        scorecard = calculate_scorecard(report)
        
        # Sharpe 2.0 * 50 = 100 (capped)
        self.assertEqual(scorecard["component_scores"]["sharpe"], 100.0)
    
    def test_scorecard_drawdown_component(self):
        """Test drawdown component score."""
        report = MockBacktestResult(max_drawdown_percent=10.0)
        
        scorecard = calculate_scorecard(report)
        
        # 100 - 10*5 = 50
        self.assertEqual(scorecard["component_scores"]["drawdown"], 50.0)
    
    def test_scorecard_zero_drawdown(self):
        """Test drawdown score with zero drawdown."""
        report = MockBacktestResult(max_drawdown_percent=0.0)
        
        scorecard = calculate_scorecard(report)
        
        self.assertEqual(scorecard["component_scores"]["drawdown"], 100.0)
    
    def test_scorecard_grade_a_plus(self):
        """Test A+ grade."""
        report = MockBacktestResult(
            total_return=20.0,
            sharpe_ratio=2.0,
            max_drawdown_percent=2.0,
            win_rate=70.0,
            profit_factor=2.0,
            num_trades=60,
        )
        
        scorecard = calculate_scorecard(report)
        
        self.assertIn(scorecard["grade"], ["A+", "A", "A-"])
    
    def test_scorecard_grade_f(self):
        """Test F grade."""
        report = MockBacktestResult(
            total_return=0.0,
            sharpe_ratio=0.0,
            max_drawdown_percent=50.0,
            win_rate=0.0,
            profit_factor=0.5,
            num_trades=5,
        )
        
        scorecard = calculate_scorecard(report)
        
        self.assertEqual(scorecard["grade"], "F")
    
    def test_scorecard_metrics_extraction(self):
        """Test metrics are correctly extracted."""
        report = MockBacktestResult(
            total_return=15.0,
            sharpe_ratio=1.5,
            num_trades=50,
        )
        
        scorecard = calculate_scorecard(report)
        
        self.assertEqual(scorecard["metrics"]["sharpe"], 1.5)
        self.assertEqual(scorecard["metrics"]["total_return"], 15.0)
        self.assertEqual(scorecard["metrics"]["num_trades"], 50)


class TestParameterSweepResult(unittest.TestCase):
    """Tests for ParameterSweepResult dataclass."""
    
    def test_parameter_sweep_result_creation(self):
        """Test creating ParameterSweepResult."""
        result = ParameterSweepResult(
            sweep_id="sweep_1",
            strategy_id="strategy_1",
            param_grid={"period": [10, 20, 30]},
            total_combinations=3,
        )
        
        self.assertEqual(result.sweep_id, "sweep_1")
        self.assertEqual(result.total_combinations, 3)
        self.assertEqual(result.completed_combinations, 0)
        self.assertIsNone(result.best_params)
    
    def test_parameter_sweep_result_defaults(self):
        """Test ParameterSweepResult default values."""
        result = ParameterSweepResult(
            sweep_id="sweep_1",
            strategy_id="strategy_1",
            param_grid={},
            total_combinations=1,
        )
        
        self.assertEqual(result.all_results, [])
        self.assertIsNone(result.completed_at)


if __name__ == "__main__":
    unittest.main()
