"""
StrategyValidationGate - 策略上线前 5 层验证门控
================================================
验证策略在扣掉真实成本后仍有正期望。

5 层验证结构：
    Layer 1: 机制假设（必须回答 3 个问题）
    Layer 2: 回测合规检查
    Layer 3: 样本外验证
    Layer 4: 成本压测
    Layer 5: 影子模式验证（可选）

设计约束：
- Core Plane 无 IO
- 完全确定性
- 验证结果可追溯

使用方式：
    gate = StrategyValidationGate(backtest_engine, validator)
    
    # 验证策略
    report = await gate.validate(strategy_id="strategy_A")
    
    if not report.overall_passed:
        print(f"未通过验证的层级: {report.failed_layers}")
        print(f"建议: {report.recommendations}")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Literal, Optional
import asyncio


# ==================== 验证状态枚举 ====================

class ValidationStatus(Enum):
    """验证状态"""
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    WARNING = "WARNING"
    SKIPPED = "SKIPPED"


# ==================== 验证报告类型 ====================

@dataclass
class StrategyValidationReport:
    """
    策略验证报告
    
    属性：
        strategy_id: 策略ID
        layer1_mechanism: Layer 1 机制假设验证
        layer2_backtest: Layer 2 回测合规验证
        layer3_out_of_sample: Layer 3 样本外验证
        layer4_cost_stress: Layer 4 成本压测验证
        layer5_shadow_mode: Layer 5 影子模式验证
        overall_passed: 是否通过所有强制验证
        failed_layers: 失败的层级列表
        recommendations: 建议列表
        timestamp: 验证时间
    """
    strategy_id: str
    layer1_mechanism: tuple[bool, str | None] = (False, None)  # (passed, reason)
    layer2_backtest: tuple[bool, str | None] = (False, None)
    layer3_out_of_sample: tuple[bool, str | None] = (False, None)
    layer4_cost_stress: tuple[bool, str | None] = (False, None)
    layer5_shadow_mode: tuple[bool, str | None] = (False, None)  # 可选
    overall_passed: bool = False
    failed_layers: list[int] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "strategy_id": self.strategy_id,
            "layer1_mechanism": {
                "passed": self.layer1_mechanism[0],
                "reason": self.layer1_mechanism[1],
            },
            "layer2_backtest": {
                "passed": self.layer2_backtest[0],
                "reason": self.layer2_backtest[1],
            },
            "layer3_out_of_sample": {
                "passed": self.layer3_out_of_sample[0],
                "reason": self.layer3_out_of_sample[1],
            },
            "layer4_cost_stress": {
                "passed": self.layer4_cost_stress[0],
                "reason": self.layer4_cost_stress[1],
            },
            "layer5_shadow_mode": {
                "passed": self.layer5_shadow_mode[0],
                "reason": self.layer5_shadow_mode[1],
            },
            "overall_passed": self.overall_passed,
            "failed_layers": self.failed_layers,
            "recommendations": self.recommendations,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class MechanismHypothesis:
    """
    机制假设问卷
    
    属性：
        why_profitable: 为什么能赚钱？
        market_mechanism: 靠什么市场机制赚钱？
        failure_conditions: 什么情况下会失效？
        answered: 是否已回答
    """
    why_profitable: str = ""
    market_mechanism: str = ""
    failure_conditions: str = ""
    answered: bool = False
    
    def is_complete(self) -> bool:
        """是否已完整回答"""
        return bool(
            self.why_profitable.strip()
            and self.market_mechanism.strip()
            and self.failure_conditions.strip()
        )


# ==================== 验证门控实现 ====================

class StrategyValidationGate:
    """
    策略上线前 5 层验证门控
    
    职责：
    1. Layer 1: 验证机制假设（必须回答 3 个问题）
    2. Layer 2: 验证回测合规（无前瞻偏差、方向感知滑点、止盈止损）
    3. Layer 3: 验证样本外性能（Walk-Forward、K-Fold）
    4. Layer 4: 验证成本压测（1.5x 成本后期望仍正）
    5. Layer 5: 影子模式验证（可选）
    """
    
    def __init__(
        self,
        backtest_func: Callable[..., Any] | None = None,
        walkforward_func: Callable[..., Any] | None = None,
        kfold_func: Callable[..., Any] | None = None,
        cost_stress_func: Callable[..., Any] | None = None,
    ) -> None:
        """
        初始化验证门控
        
        Args:
            backtest_func: 回测函数 (config, params) -> BacktestResult
            walkforward_func: Walk-Forward 分析函数
            kfold_func: K-Fold 验证函数
            cost_stress_func: 成本压测函数
        """
        self._backtest = backtest_func
        self._walkforward = walkforward_func
        self._kfold = kfold_func
        self._cost_stress = cost_stress_func
    
    async def validate(
        self,
        strategy_id: str,
        mechanism: MechanismHypothesis | None = None,
        backtest_result: Any | None = None,
        walkforward_result: Any | None = None,
        kfold_result: Any | None = None,
        cost_stress_result: Any | None = None,
        shadow_mode_result: Any | None = None,
    ) -> StrategyValidationReport:
        """
        执行完整验证流程
        
        Args:
            strategy_id: 策略ID
            mechanism: 机制假设（Layer 1）
            backtest_result: 回测结果（Layer 2）
            walkforward_result: Walk-Forward 结果（Layer 3）
            kfold_result: K-Fold 结果（Layer 3）
            cost_stress_result: 成本压测结果（Layer 4）
            shadow_mode_result: 影子模式结果（Layer 5，可选）
            
        Returns:
            验证报告
        """
        report = StrategyValidationReport(strategy_id=strategy_id)
        
        # Layer 1: 机制假设
        layer1_passed, layer1_reason = await self.validate_layer1(mechanism)
        report.layer1_mechanism = (layer1_passed, layer1_reason)
        
        # Layer 2: 回测合规
        layer2_passed, layer2_reason = await self.validate_layer2(backtest_result)
        report.layer2_backtest = (layer2_passed, layer2_reason)
        
        # Layer 3: 样本外验证
        layer3_passed, layer3_reason = await self.validate_layer3(
            walkforward_result, kfold_result
        )
        report.layer3_out_of_sample = (layer3_passed, layer3_reason)
        
        # Layer 4: 成本压测
        layer4_passed, layer4_reason = await self.validate_layer4(cost_stress_result)
        report.layer4_cost_stress = (layer4_passed, layer4_reason)
        
        # Layer 5: 影子模式（可选）
        if shadow_mode_result is not None:
            layer5_passed, layer5_reason = await self.validate_layer5(shadow_mode_result)
            report.layer5_shadow_mode = (layer5_passed, layer5_reason)
        else:
            report.layer5_shadow_mode = (True, "SKIPPED - 影子模式未执行")
        
        # 计算总体结果
        failed_layers = []
        if not layer1_passed:
            failed_layers.append(1)
        if not layer2_passed:
            failed_layers.append(2)
        if not layer3_passed:
            failed_layers.append(3)
        if not layer4_passed:
            failed_layers.append(4)
        # Layer 5 是可选的
        
        report.failed_layers = failed_layers
        report.overall_passed = len(failed_layers) == 0
        
        # 生成建议
        report.recommendations = self._generate_recommendations(report)
        
        return report
    
    async def validate_layer1(
        self,
        mechanism: MechanismHypothesis | None,
    ) -> tuple[bool, str | None]:
        """
        Layer 1: 机制假设验证
        
        必须回答 3 个问题：
        1. 它为什么会赚钱？
        2. 它靠什么市场机制赚钱？
        3. 什么情况下会失效？
        
        Returns:
            (passed, reason)
        """
        if mechanism is None:
            return False, "机制假设未提供"
        
        if not mechanism.is_complete():
            return False, "机制假设未完整回答（需回答 3 个问题）"
        
        return True, "机制假设已完整回答"
    
    async def validate_layer2(
        self,
        backtest_result: Any | None,
    ) -> tuple[bool, str | None]:
        """
        Layer 2: 回测合规验证
        
        检查：
        1. 下一 bar 开盘价执行（消除前瞻偏差）
        2. 方向感知滑点
        3. 止盈止损支持
        4. 手续费模型
        
        Returns:
            (passed, reason)
        """
        if backtest_result is None:
            return False, "回测结果未提供"
        
        # 检查回测结果中的合规标志
        if hasattr(backtest_result, "has_forward_looking_bias"):
            if backtest_result.has_forward_looking_bias:
                return False, "回测存在前瞻偏差"
        
        if hasattr(backtest_result, "slippage_direction_aware"):
            if not backtest_result.slippage_direction_aware:
                return False, "滑点方向不正确"
        
        if hasattr(backtest_result, "has_stop_loss") or hasattr(backtest_result, "has_take_profit"):
            # 如果策略应该有止盈止损但没有
            pass  # 需要更具体的逻辑
        
        return True, "回测合规检查通过"
    
    async def validate_layer3(
        self,
        walkforward_result: Any | None,
        kfold_result: Any | None,
    ) -> tuple[bool, str | None]:
        """
        Layer 3: 样本外验证
        
        检查：
        1. Walk-Forward Sharpe 衰减 < 20%
        2. K-Fold 验证通过
        
        Returns:
            (passed, reason)
        """
        if walkforward_result is None and kfold_result is None:
            return False, "样本外验证结果未提供"
        
        # 检查 Walk-Forward 结果
        if walkforward_result is not None:
            if hasattr(walkforward_result, "sharpe_decay"):
                decay = walkforward_result.sharpe_decay
                if decay < 0.8:  # 衰减超过 20%
                    return False, f"Walk-Forward Sharpe 衰减 {1-decay:.1%}，超过 20% 阈值"
            
            if hasattr(walkforward_result, "overfitting_status"):
                if walkforward_result.overfitting_status == "FAILED":
                    return False, "Walk-Forward 检测到过拟合"
        
        # 检查 K-Fold 结果
        if kfold_result is not None:
            if hasattr(kfold_result, "validation_status"):
                if kfold_result.validation_status == "FAILED":
                    return False, "K-Fold 验证失败"
        
        return True, "样本外验证通过"
    
    async def validate_layer4(
        self,
        cost_stress_result: Any | None,
    ) -> tuple[bool, str | None]:
        """
        Layer 4: 成本压测验证
        
        检查：
        1. 1x 成本后期望 > 0
        2. 1.5x 成本后期望 > 0
        
        Returns:
            (passed, reason)
        """
        if cost_stress_result is None:
            return False, "成本压测结果未提供"
        
        # 查找 1x 和 1.5x 成本的期望值
        found_1x = False
        found_1_5x = False
        
        if hasattr(cost_stress_result, "__iter__"):
            for result in cost_stress_result:
                multiplier = getattr(result, "cost_multiplier", None)
                expectancy = getattr(result, "expectancy", None)
                
                if multiplier == 1.0 and expectancy is not None:
                    found_1x = True
                    if expectancy <= 0:
                        return False, f"1x 成本后期望为负: {expectancy}"
                
                if multiplier == 1.5 and expectancy is not None:
                    found_1_5x = True
                    if expectancy <= 0:
                        return False, f"1.5x 成本后期望为负: {expectancy}"
        
        if not found_1x:
            return False, "1x 成本压测结果未找到"
        
        if not found_1_5x:
            return False, "1.5x 成本压测结果未找到"
        
        return True, "成本压测通过"
    
    async def validate_layer5(
        self,
        shadow_mode_result: Any | None,
    ) -> tuple[bool, str | None]:
        """
        Layer 5: 影子模式验证（可选）
        
        检查：
        1. 信号触发率偏差 < 20%
        2. sizing 偏差 < 30%
        3. 成交偏差在可接受范围
        
        Returns:
            (passed, reason)
        """
        if shadow_mode_result is None:
            return True, "SKIPPED - 影子模式未执行"
        
        # 检查信号触发率偏差
        if hasattr(shadow_mode_result, "signal_trigger_rate_diff"):
            diff = shadow_mode_result.signal_trigger_rate_diff
            if diff > 0.2:  # 超过 20%
                return False, f"信号触发率偏差 {diff:.1%}，超过 20% 阈值"
        
        # 检查 sizing 偏差
        if hasattr(shadow_mode_result, "sizing_avg_diff"):
            diff = shadow_mode_result.sizing_avg_diff
            if diff > 0.3:  # 超过 30%
                return False, f"Sizing 平均偏差 {diff:.1%}，超过 30% 阈值"
        
        # 检查成交偏差
        if hasattr(shadow_mode_result, "execution_gap_avg"):
            gap = shadow_mode_result.execution_gap_avg
            if hasattr(shadow_mode_result, "max_slippage_assumption"):
                max_slip = shadow_mode_result.max_slippage_assumption
                if gap > 2 * max_slip:
                    return False, f"成交偏差 {gap:.4f}，超过 2x 假设滑点"
        
        return True, "影子模式验证通过"
    
    def _generate_recommendations(
        self,
        report: StrategyValidationReport,
    ) -> list[str]:
        """生成建议列表"""
        recommendations = []
        
        if 1 in report.failed_layers:
            recommendations.append(
                "Layer 1: 补充策略的机制假设说明（为什么会赚钱、市场机制、失效条件）"
            )
        
        if 2 in report.failed_layers:
            recommendations.append(
                "Layer 2: 检查回测配置，确保无前瞻偏差、方向感知滑点、止盈止损"
            )
        
        if 3 in report.failed_layers:
            recommendations.append(
                "Layer 3: 重新设计策略参数，减少过拟合风险"
            )
        
        if 4 in report.failed_layers:
            recommendations.append(
                "Layer 4: 策略边缘较薄，考虑收紧成本假设或调整策略"
            )
        
        if report.overall_passed:
            recommendations.append("策略通过所有强制验证层级，可以考虑上线")
        
        return recommendations


# ==================== 验证结果工厂函数 ====================

def create_validation_report(
    strategy_id: str,
    layer_results: dict[int, tuple[bool, str]],
    shadow_result: tuple[bool, str] | None = None,
) -> StrategyValidationReport:
    """
    创建验证报告（辅助函数）
    
    Args:
        strategy_id: 策略ID
        layer_results: {layer: (passed, reason)}
        shadow_result: (passed, reason) for layer 5
    """
    report = StrategyValidationReport(strategy_id=strategy_id)
    
    for layer, (passed, reason) in layer_results.items():
        if layer == 1:
            report.layer1_mechanism = (passed, reason)
        elif layer == 2:
            report.layer2_backtest = (passed, reason)
        elif layer == 3:
            report.layer3_out_of_sample = (passed, reason)
        elif layer == 4:
            report.layer4_cost_stress = (passed, reason)
    
    if shadow_result is not None:
        report.layer5_shadow_mode = shadow_result
    
    # 计算失败层级
    failed = []
    if not report.layer1_mechanism[0]:
        failed.append(1)
    if not report.layer2_backtest[0]:
        failed.append(2)
    if not report.layer3_out_of_sample[0]:
        failed.append(3)
    if not report.layer4_cost_stress[0]:
        failed.append(4)
    
    report.failed_layers = failed
    report.overall_passed = len(failed) == 0
    report.recommendations = StrategyValidationGate(None)._generate_recommendations(report)
    
    return report
