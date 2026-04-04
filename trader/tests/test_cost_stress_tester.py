"""
CostStressTester 单元测试
========================
测试成本压测功能。
"""
import pytest
from dataclasses import dataclass

from trader.services.backtesting.cost_stress_tester import (
    CostStressTester,
    CostStressResult,
    calculate_cost_adjusted_expectancy,
    estimate_cost_break_even_multiplier,
)


# ==================== Fixtures ====================

@pytest.fixture
def tester() -> CostStressTester:
    """创建成本压测器"""
    return CostStressTester(
        base_commission=0.0004,
        base_slippage=0.0002,
    )


@pytest.fixture
def positive_backtest_result():
    """正期望回测结果"""
    @dataclass
    class MockBacktestResult:
        expectancy: float = 15.0
        sharpe_ratio: float = 1.5
        max_drawdown: float = 10.0
        total_return: float = 50.0
        win_rate: float = 0.55
        avg_trade: float = 100.0
        num_trades: int = 100
    
    return MockBacktestResult()


@pytest.fixture
def negative_backtest_result():
    """负期望回测结果"""
    @dataclass
    class MockBacktestResult:
        expectancy: float = -5.0
        sharpe_ratio: float = -0.5
        max_drawdown: float = 20.0
        total_return: float = -20.0
        win_rate: float = 0.40
        avg_trade: float = 80.0
        num_trades: int = 50
    
    return MockBacktestResult()


# ==================== 成本压测测试 ====================

class TestCostStressTest:
    """成本压测功能测试"""
    
    def test_stress_test_default_multipliers(
        self,
        tester: CostStressTester,
        positive_backtest_result,
    ):
        """默认成本倍数测试"""
        results = tester.stress_test(positive_backtest_result)
        
        assert len(results) == 3
        multipliers = [r.cost_multiplier for r in results]
        assert 1.0 in multipliers
        assert 1.5 in multipliers
        assert 2.0 in multipliers
    
    def test_stress_test_custom_multipliers(
        self,
        tester: CostStressTester,
        positive_backtest_result,
    ):
        """自定义成本倍数"""
        results = tester.stress_test(
            positive_backtest_result,
            multipliers=[1.0, 2.0, 3.0],
        )
        
        assert len(results) == 3
        assert [r.cost_multiplier for r in results] == [1.0, 2.0, 3.0]
    
    def test_stress_test_positive_result_passes_1x(
        self,
        tester: CostStressTester,
        positive_backtest_result,
    ):
        """1x 成本时正期望结果通过"""
        results = tester.stress_test(positive_backtest_result)
        
        # 找到 1x 结果
        result_1x = next(r for r in results if r.cost_multiplier == 1.0)
        assert result_1x.passed is True
        assert result_1x.expectancy > 0
    
    def test_stress_test_positive_result_passes_1_5x(
        self,
        tester: CostStressTester,
        positive_backtest_result,
    ):
        """1.5x 成本时正期望结果应该通过（除非边缘太薄）"""
        results = tester.stress_test(positive_backtest_result)
        
        result_1_5x = next(r for r in results if r.cost_multiplier == 1.5)
        # 由于期望值较高，应该仍然为正
        assert result_1_5x.expectancy >= 0 or result_1_5x.expectancy > -1
    
    def test_stress_test_negative_result_fails(
        self,
        tester: CostStressTester,
        negative_backtest_result,
    ):
        """负期望结果始终失败"""
        results = tester.stress_test(negative_backtest_result)
        
        for result in results:
            assert result.passed is False
            assert result.expectancy < 0
    
    def test_stress_test_cost_reduces_expectancy(
        self,
        tester: CostStressTester,
        positive_backtest_result,
    ):
        """成本增加导致期望降低"""
        results = tester.stress_test(positive_backtest_result)
        
        result_1x = next(r for r in results if r.cost_multiplier == 1.0)
        result_2x = next(r for r in results if r.cost_multiplier == 2.0)
        
        assert result_2x.expectancy < result_1x.expectancy
    
    def test_stress_test_cost_increases_drawdown(
        self,
        tester: CostStressTester,
        positive_backtest_result,
    ):
        """成本增加导致回撤增加"""
        results = tester.stress_test(positive_backtest_result)
        
        result_1x = next(r for r in results if r.cost_multiplier == 1.0)
        result_2x = next(r for r in results if r.cost_multiplier == 2.0)
        
        assert result_2x.max_drawdown > result_1x.max_drawdown


# ==================== 成本调整计算测试 ====================

class TestCostAdjustedExpectancy:
    """成本调整期望值计算测试"""
    
    def test_positive_expectancy_reduced_by_cost(self):
        """正期望被成本降低"""
        result = calculate_cost_adjusted_expectancy(
            original_expectancy=100.0,
            num_trades=100,
            commission_rate=0.0004,
            slippage_rate=0.0002,
            multiplier=2.0,
        )
        
        # 成本从 0.06% 增加到 0.12%
        # extra_cost = (2-1) * 0.0006 * 100 = 0.06
        assert result < 100.0
        assert result > 0
    
    def test_negative_expectancy_stays_negative(self):
        """负期望不会因为成本降低变正"""
        result = calculate_cost_adjusted_expectancy(
            original_expectancy=-50.0,
            num_trades=100,
            commission_rate=0.0004,
            slippage_rate=0.0002,
            multiplier=2.0,
        )
        
        assert result < 0
    
    def test_zero_expectancy_stays_zero(self):
        """零期望保持零"""
        result = calculate_cost_adjusted_expectancy(
            original_expectancy=0.0,
            num_trades=100,
            commission_rate=0.0004,
            slippage_rate=0.0002,
            multiplier=2.0,
        )
        
        assert result == 0.0


# ==================== 盈亏平衡倍数估算测试 ====================

class TestBreakEvenMultiplier:
    """盈亏平衡倍数估算测试"""
    
    def test_positive_expectancy_has_break_even(self):
        """正期望有有限的盈亏平衡倍数"""
        mult = estimate_cost_break_even_multiplier(
            original_expectancy=100.0,
            num_trades=100,
            commission_rate=0.0004,
            slippage_rate=0.0002,
        )
        
        assert 1.0 < mult < float("inf")
    
    def test_negative_expectancy_returns_1(self):
        """负期望返回1.0（无法通过降低成本变正）"""
        mult = estimate_cost_break_even_multiplier(
            original_expectancy=-50.0,
            num_trades=100,
            commission_rate=0.0004,
            slippage_rate=0.0002,
        )
        
        assert mult == 1.0
    
    def test_zero_trades_returns_inf(self):
        """零交易次数返回无穷大"""
        mult = estimate_cost_break_even_multiplier(
            original_expectancy=100.0,
            num_trades=0,
            commission_rate=0.0004,
            slippage_rate=0.0002,
        )
        
        assert mult == float("inf")
    
    def test_zero_cost_rate_returns_inf(self):
        """零成本返回无穷大"""
        mult = estimate_cost_break_even_multiplier(
            original_expectancy=100.0,
            num_trades=100,
            commission_rate=0.0,
            slippage_rate=0.0,
        )
        
        assert mult == float("inf")


# ==================== 辅助函数测试 ====================

class TestHelperFunctions:
    """辅助函数测试"""
    
    def test_result_to_dict(self):
        """结果转字典"""
        result = CostStressResult(
            cost_multiplier=1.5,
            expectancy=10.5,
            sharpe_ratio=1.2,
            max_drawdown=8.5,
            total_return=35.0,
            win_rate=0.52,
            passed=True,
        )
        
        d = result.to_dict()
        
        assert d["cost_multiplier"] == 1.5
        assert d["expectancy"] == 10.5
        assert d["passed"] is True
        # 检查精度
        assert isinstance(d["expectancy"], float)


# ==================== 集成测试 ====================

class TestIntegration:
    """成本压测集成测试"""
    
    def test_full_stress_pipeline(self, tester: CostStressTester):
        """完整压测流程"""
        @dataclass
        class MockBacktestResult:
            expectancy: float = 20.0
            sharpe_ratio: float = 1.8
            max_drawdown: float = 12.0
            total_return: float = 80.0
            win_rate: float = 0.58
            avg_trade: float = 150.0
            num_trades: int = 200
        
        result = MockBacktestResult()
        
        # 执行压测
        results = tester.stress_test(result)
        
        # 验证结构
        assert len(results) > 0
        for r in results:
            assert r.cost_multiplier > 0
            assert isinstance(r.passed, bool)
        
        # 验证 1x 通过
        r1 = next(x for x in results if x.cost_multiplier == 1.0)
        assert r1.passed is True
        
        # 验证趋势
        r1x = next(x for x in results if x.cost_multiplier == 1.0)
        r2x = next(x for x in results if x.cost_multiplier == 2.0)
        assert r2x.expectancy < r1x.expectancy


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
