"""
StrategyValidationGate 单元测试
==============================
测试策略上线前 5 层验证门控的功能。
"""
import pytest
from datetime import datetime, timezone
from decimal import Decimal
from dataclasses import dataclass

from trader.services.strategy_validation_gate import (
    StrategyValidationGate,
    StrategyValidationReport,
    MechanismHypothesis,
    ValidationStatus,
    create_validation_report,
)


# ==================== 辅助 Mock 类型 ====================

@dataclass
class MockBacktestResult:
    """Mock 回测结果"""
    has_forward_looking_bias: bool = False
    slippage_direction_aware: bool = True
    has_stop_loss: bool = True
    has_take_profit: bool = True


@dataclass
class MockWalkForwardResult:
    """Mock Walk-Forward 结果"""
    sharpe_decay: float = 0.9  # 10% 衰减
    overfitting_status: str = "PASSED"


@dataclass
class MockKFoldResult:
    """Mock K-Fold 结果"""
    validation_status: str = "PASSED"


@dataclass
class MockCostStressResult:
    """Mock 成本压测结果"""
    cost_multiplier: float
    expectancy: float


@dataclass
class MockShadowModeResult:
    """Mock 影子模式结果"""
    signal_trigger_rate_diff: float = 0.1  # 10%
    sizing_avg_diff: float = 0.2  # 20%
    execution_gap_avg: float = 0.001
    max_slippage_assumption: float = 0.001


# ==================== Fixtures ====================

@pytest.fixture
def gate() -> StrategyValidationGate:
    """创建验证门控"""
    return StrategyValidationGate()


@pytest.fixture
def complete_mechanism() -> MechanismHypothesis:
    """完整的机制假设"""
    return MechanismHypothesis(
        why_profitable="趋势跟踪策略在趋势市场中盈利",
        market_mechanism="动量效应：涨的继续涨",
        failure_conditions="震荡市场中会亏损",
        answered=True,
    )


@pytest.fixture
def incomplete_mechanism() -> MechanismHypothesis:
    """不完整的机制假设"""
    return MechanismHypothesis(
        why_profitable="趋势跟踪策略在趋势市场中盈利",
        market_mechanism="",  # 未填写
        failure_conditions="",
        answered=False,
    )


# ==================== Layer 1: 机制假设测试 ====================

class TestLayer1Mechanism:
    """Layer 1: 机制假设验证"""
    
    @pytest.mark.asyncio
    async def test_layer1_pass_with_complete_mechanism(
        self,
        gate: StrategyValidationGate,
        complete_mechanism: MechanismHypothesis,
    ):
        """完整回答时通过"""
        passed, reason = await gate.validate_layer1(complete_mechanism)
        
        assert passed is True
        assert reason is not None
        assert "完整" in reason
    
    @pytest.mark.asyncio
    async def test_layer1_fail_with_incomplete_mechanism(
        self,
        gate: StrategyValidationGate,
        incomplete_mechanism: MechanismHypothesis,
    ):
        """未完整回答时失败"""
        passed, reason = await gate.validate_layer1(incomplete_mechanism)
        
        assert passed is False
        assert "未完整" in reason
    
    @pytest.mark.asyncio
    async def test_layer1_fail_with_none_mechanism(self, gate: StrategyValidationGate):
        """未提供机制假设时失败"""
        passed, reason = await gate.validate_layer1(None)
        
        assert passed is False
        assert "未提供" in reason
    
    def test_mechanism_is_complete(self, complete_mechanism: MechanismHypothesis):
        """完整机制假设验证"""
        assert complete_mechanism.is_complete() is True
    
    def test_mechanism_is_not_complete(self, incomplete_mechanism: MechanismHypothesis):
        """不完整机制假设验证"""
        assert incomplete_mechanism.is_complete() is False


# ==================== Layer 2: 回测合规测试 ====================

class TestLayer2Backtest:
    """Layer 2: 回测合规验证"""
    
    @pytest.mark.asyncio
    async def test_layer2_pass_with_valid_result(
        self,
        gate: StrategyValidationGate,
    ):
        """合规回测结果通过"""
        result = MockBacktestResult(
            has_forward_looking_bias=False,
            slippage_direction_aware=True,
        )
        
        passed, reason = await gate.validate_layer2(result)
        
        assert passed is True
        assert "通过" in reason
    
    @pytest.mark.asyncio
    async def test_layer2_fail_with_forward_bias(
        self,
        gate: StrategyValidationGate,
    ):
        """存在前瞻偏差时失败"""
        result = MockBacktestResult(has_forward_looking_bias=True)
        
        passed, reason = await gate.validate_layer2(result)
        
        assert passed is False
        assert "前瞻偏差" in reason
    
    @pytest.mark.asyncio
    async def test_layer2_fail_with_none_result(self, gate: StrategyValidationGate):
        """未提供回测结果时失败"""
        passed, reason = await gate.validate_layer2(None)
        
        assert passed is False
        assert "未提供" in reason


# ==================== Layer 3: 样本外验证测试 ====================

class TestLayer3OutOfSample:
    """Layer 3: 样本外验证"""
    
    @pytest.mark.asyncio
    async def test_layer3_pass_with_good_walkforward(
        self,
        gate: StrategyValidationGate,
    ):
        """Walk-Forward 结果良好时通过"""
        result = MockWalkForwardResult(
            sharpe_decay=0.85,  # 15% 衰减
            overfitting_status="PASSED",
        )
        
        passed, reason = await gate.validate_layer3(
            walkforward_result=result,
            kfold_result=None,
        )
        
        assert passed is True
        assert "通过" in reason
    
    @pytest.mark.asyncio
    async def test_layer3_fail_with_high_decay(
        self,
        gate: StrategyValidationGate,
    ):
        """Sharpe 衰减过高时失败"""
        result = MockWalkForwardResult(
            sharpe_decay=0.7,  # 30% 衰减
            overfitting_status="PASSED",
        )
        
        passed, reason = await gate.validate_layer3(
            walkforward_result=result,
            kfold_result=None,
        )
        
        assert passed is False
        assert "衰减" in reason
    
    @pytest.mark.asyncio
    async def test_layer3_fail_with_no_results(self, gate: StrategyValidationGate):
        """未提供任何结果时失败"""
        passed, reason = await gate.validate_layer3(None, None)
        
        assert passed is False
        assert "未提供" in reason
    
    @pytest.mark.asyncio
    async def test_layer3_fail_with_kfold_failure(
        self,
        gate: StrategyValidationGate,
    ):
        """K-Fold 验证失败时失败"""
        walkforward = MockWalkForwardResult()
        kfold = MockKFoldResult(validation_status="FAILED")
        
        passed, reason = await gate.validate_layer3(walkforward, kfold)
        
        assert passed is False
        assert "K-Fold" in reason


# ==================== Layer 4: 成本压测测试 ====================

class TestLayer4CostStress:
    """Layer 4: 成本压测验证"""
    
    @pytest.mark.asyncio
    async def test_layer4_pass_with_positive_expectancy(
        self,
        gate: StrategyValidationGate,
    ):
        """1x 和 1.5x 成本后期望都为正时通过"""
        results = [
            MockCostStressResult(cost_multiplier=1.0, expectancy=15.0),
            MockCostStressResult(cost_multiplier=1.5, expectancy=5.0),
            MockCostStressResult(cost_multiplier=2.0, expectancy=-2.0),
        ]
        
        passed, reason = await gate.validate_layer4(results)
        
        assert passed is True
        assert "通过" in reason
    
    @pytest.mark.asyncio
    async def test_layer4_fail_with_negative_1x_expectancy(
        self,
        gate: StrategyValidationGate,
    ):
        """1x 成本后期望为负时失败"""
        results = [
            MockCostStressResult(cost_multiplier=1.0, expectancy=-5.0),
            MockCostStressResult(cost_multiplier=1.5, expectancy=10.0),
        ]
        
        passed, reason = await gate.validate_layer4(results)
        
        assert passed is False
        assert "1x" in reason
    
    @pytest.mark.asyncio
    async def test_layer4_fail_with_negative_1_5x_expectancy(
        self,
        gate: StrategyValidationGate,
    ):
        """1.5x 成本后期望为负时失败"""
        results = [
            MockCostStressResult(cost_multiplier=1.0, expectancy=15.0),
            MockCostStressResult(cost_multiplier=1.5, expectancy=-2.0),
        ]
        
        passed, reason = await gate.validate_layer4(results)
        
        assert passed is False
        assert "1.5x" in reason
    
    @pytest.mark.asyncio
    async def test_layer4_fail_with_no_results(self, gate: StrategyValidationGate):
        """未提供结果时失败"""
        passed, reason = await gate.validate_layer4(None)
        
        assert passed is False
        assert "未提供" in reason
    
    @pytest.mark.asyncio
    async def test_layer4_fail_with_missing_1x(
        self,
        gate: StrategyValidationGate,
    ):
        """缺少 1x 结果时失败"""
        results = [
            MockCostStressResult(cost_multiplier=1.5, expectancy=5.0),
        ]
        
        passed, reason = await gate.validate_layer4(results)
        
        assert passed is False
        assert "1x" in reason


# ==================== Layer 5: 影子模式测试 ====================

class TestLayer5ShadowMode:
    """Layer 5: 影子模式验证"""
    
    @pytest.mark.asyncio
    async def test_layer5_pass_within_thresholds(
        self,
        gate: StrategyValidationGate,
    ):
        """偏差在阈值内时通过"""
        result = MockShadowModeResult(
            signal_trigger_rate_diff=0.1,  # 10%
            sizing_avg_diff=0.2,  # 20%
        )
        
        passed, reason = await gate.validate_layer5(result)
        
        assert passed is True
        assert "通过" in reason
    
    @pytest.mark.asyncio
    async def test_layer5_fail_with_high_signal_diff(
        self,
        gate: StrategyValidationGate,
    ):
        """信号触发率偏差过高时失败"""
        result = MockShadowModeResult(
            signal_trigger_rate_diff=0.3,  # 30%，超过 20%
            sizing_avg_diff=0.1,
        )
        
        passed, reason = await gate.validate_layer5(result)
        
        assert passed is False
        assert "信号触发率" in reason
    
    @pytest.mark.asyncio
    async def test_layer5_fail_with_high_sizing_diff(
        self,
        gate: StrategyValidationGate,
    ):
        """Sizing 偏差过高时失败"""
        result = MockShadowModeResult(
            signal_trigger_rate_diff=0.1,
            sizing_avg_diff=0.5,  # 50%，超过 30%
        )
        
        passed, reason = await gate.validate_layer5(result)
        
        assert passed is False
        assert "Sizing" in reason
    
    @pytest.mark.asyncio
    async def test_layer5_skip_with_none(self, gate: StrategyValidationGate):
        """未提供结果时跳过"""
        passed, reason = await gate.validate_layer5(None)
        
        assert passed is True
        assert "SKIPPED" in reason


# ==================== 完整验证流程测试 ====================

class TestFullValidation:
    """完整验证流程测试"""
    
    @pytest.mark.asyncio
    async def test_full_validation_all_pass(
        self,
        gate: StrategyValidationGate,
        complete_mechanism: MechanismHypothesis,
    ):
        """所有层级都通过"""
        report = await gate.validate(
            strategy_id="strategy_A",
            mechanism=complete_mechanism,
            backtest_result=MockBacktestResult(),
            walkforward_result=MockWalkForwardResult(),
            kfold_result=MockKFoldResult(),
            cost_stress_result=[
                MockCostStressResult(cost_multiplier=1.0, expectancy=15.0),
                MockCostStressResult(cost_multiplier=1.5, expectancy=5.0),
            ],
            shadow_mode_result=MockShadowModeResult(),
        )
        
        assert report.overall_passed is True
        assert len(report.failed_layers) == 0
    
    @pytest.mark.asyncio
    async def test_full_validation_layer1_fails(
        self,
        gate: StrategyValidationGate,
        incomplete_mechanism: MechanismHypothesis,
    ):
        """Layer 1 失败"""
        report = await gate.validate(
            strategy_id="strategy_A",
            mechanism=incomplete_mechanism,
            backtest_result=MockBacktestResult(),
            walkforward_result=MockWalkForwardResult(),
            cost_stress_result=[
                MockCostStressResult(cost_multiplier=1.0, expectancy=15.0),
                MockCostStressResult(cost_multiplier=1.5, expectancy=5.0),
            ],
        )
        
        assert report.overall_passed is False
        assert 1 in report.failed_layers
        assert len(report.failed_layers) >= 1
    
    @pytest.mark.asyncio
    async def test_full_validation_layer4_fails(
        self,
        gate: StrategyValidationGate,
        complete_mechanism: MechanismHypothesis,
    ):
        """Layer 4 失败"""
        report = await gate.validate(
            strategy_id="strategy_A",
            mechanism=complete_mechanism,
            backtest_result=MockBacktestResult(),
            walkforward_result=MockWalkForwardResult(),
            cost_stress_result=[
                MockCostStressResult(cost_multiplier=1.0, expectancy=15.0),
                MockCostStressResult(cost_multiplier=1.5, expectancy=-2.0),  # 失败
            ],
        )
        
        assert report.overall_passed is False
        assert 4 in report.failed_layers
    
    @pytest.mark.asyncio
    async def test_recommendations_generated(
        self,
        gate: StrategyValidationGate,
        incomplete_mechanism: MechanismHypothesis,
    ):
        """建议正确生成"""
        report = await gate.validate(
            strategy_id="strategy_A",
            mechanism=incomplete_mechanism,
        )
        
        assert len(report.recommendations) > 0
        assert any("Layer 1" in r for r in report.recommendations)


# ==================== 工厂函数测试 ====================

class TestFactoryFunction:
    """验证报告工厂函数测试"""
    
    def test_create_validation_report_all_pass(self):
        """所有层级都通过"""
        report = create_validation_report(
            strategy_id="strategy_A",
            layer_results={
                1: (True, "通过"),
                2: (True, "通过"),
                3: (True, "通过"),
                4: (True, "通过"),
            },
            shadow_result=(True, "通过"),
        )
        
        assert report.overall_passed is True
        assert len(report.failed_layers) == 0
    
    def test_create_validation_report_some_fail(self):
        """部分层级失败"""
        report = create_validation_report(
            strategy_id="strategy_A",
            layer_results={
                1: (True, "通过"),
                2: (False, "失败"),
                3: (True, "通过"),
                4: (False, "失败"),
            },
        )
        
        assert report.overall_passed is False
        assert 2 in report.failed_layers
        assert 4 in report.failed_layers
        assert 1 not in report.failed_layers


# ==================== 序列化测试 ====================

class TestSerialization:
    """验证报告序列化测试"""
    
    def test_report_to_dict(self):
        """验证报告转字典"""
        report = StrategyValidationReport(
            strategy_id="strategy_A",
            layer1_mechanism=(True, "通过"),
            layer2_backtest=(True, "通过"),
            layer3_out_of_sample=(True, "通过"),
            layer4_cost_stress=(True, "通过"),
            overall_passed=True,
            failed_layers=[],
            recommendations=["策略通过所有强制验证"],
        )
        
        d = report.to_dict()
        
        assert d["strategy_id"] == "strategy_A"
        assert d["layer1_mechanism"]["passed"] is True
        assert d["overall_passed"] is True
        assert "timestamp" in d


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
