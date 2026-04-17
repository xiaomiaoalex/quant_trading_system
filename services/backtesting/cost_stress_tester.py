"""
CostStressTester - 成本压测标准化入口
======================================
为每个策略提供 1x/1.5x/2x 成本压测的标准化入口。

核心功能：
1. 对回测结果执行多成本倍数压测
2. 验证策略在成本增加后是否仍为正期望
3. 计算 Expectancy / Sharpe / MaxDrawdown 在不同成本下的表现

使用方式：
    tester = CostStressTester()
    
    results = tester.stress_test(
        backtest_result=backtest_report,
        multipliers=[1.0, 1.5, 2.0],
    )
    
    for result in results:
        print(f"{result.cost_multiplier}x: Expectancy={result.expectancy:.2f}")
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence


# ==================== 成本压测结果类型 ====================

@dataclass
class CostStressResult:
    """
    成本压测结果
    
    属性：
        cost_multiplier: 成本倍数 (1.0, 1.5, 2.0)
        expectancy: 期望值
        sharpe_ratio: 夏普比率
        max_drawdown: 最大回撤
        total_return: 总收益率
        win_rate: 胜率
        passed: 是否通过压测门槛
    """
    cost_multiplier: float
    expectancy: float
    sharpe_ratio: float
    max_drawdown: float
    total_return: float
    win_rate: float
    passed: bool
    
    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "cost_multiplier": self.cost_multiplier,
            "expectancy": round(self.expectancy, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "total_return": round(self.total_return, 4),
            "win_rate": round(self.win_rate, 4),
            "passed": self.passed,
        }


# ==================== 成本压测器实现 ====================

class CostStressTester:
    """
    成本压测器
    
    对回测结果执行不同成本倍数下的性能测试。
    """
    
    def __init__(
        self,
        base_commission: float = 0.0004,  # 基础手续费 (0.04%)
        base_slippage: float = 0.0002,     # 基础滑点 (0.02%)
    ) -> None:
        """
        初始化成本压测器
        
        Args:
            base_commission: 基础手续费率
            base_slippage: 基础滑点率
        """
        self._base_commission = base_commission
        self._base_slippage = base_slippage
    
    def stress_test(
        self,
        backtest_result: Any,
        multipliers: Sequence[float] | None = None,
        min_expectancy: float = 0.0,
    ) -> list[CostStressResult]:
        """
        对回测结果执行成本压测
        
        Args:
            backtest_result: 回测结果（BacktestReport）
            multipliers: 成本倍数列表，默认 [1.0, 1.5, 2.0]
            min_expectancy: 通过门槛的最小期望值
            
        Returns:
            每个成本倍数对应的性能指标
        """
        if multipliers is None:
            multipliers = [1.0, 1.5, 2.0]
        
        # 从回测结果提取基础指标
        base_metrics = self._extract_metrics(backtest_result)
        
        # 计算每个成本倍数下的指标
        results = []
        for mult in multipliers:
            stress_metrics = self._apply_cost_multiplier(base_metrics, mult)
            
            # 判断是否通过
            passed = stress_metrics["expectancy"] >= min_expectancy
            
            result = CostStressResult(
                cost_multiplier=mult,
                expectancy=stress_metrics["expectancy"],
                sharpe_ratio=stress_metrics["sharpe_ratio"],
                max_drawdown=stress_metrics["max_drawdown"],
                total_return=stress_metrics["total_return"],
                win_rate=stress_metrics["win_rate"],
                passed=passed,
            )
            results.append(result)
        
        return results
    
    def _extract_metrics(self, result: Any) -> dict:
        """从回测结果提取基础指标"""
        return {
            "expectancy": getattr(result, "expectancy", 0.0),
            "sharpe_ratio": getattr(result, "sharpe_ratio", 0.0),
            "max_drawdown": getattr(result, "max_drawdown", 0.0),
            "total_return": getattr(result, "total_return", 0.0),
            "win_rate": getattr(result, "win_rate", 0.0),
            "avg_trade": getattr(result, "avg_trade", 0.0),
            "num_trades": getattr(result, "num_trades", 0),
        }
    
    def _apply_cost_multiplier(
        self,
        base_metrics: dict,
        multiplier: float,
    ) -> dict:
        """
        应用成本倍数计算压力下的指标
        
        成本增加会：
        1. 降低期望值（直接减少）
        2. 略微降低胜率（成本高时更容易触发止损）
        3. 增加最大回撤（成本累积效应）
        
        Args:
            base_metrics: 基础指标
            multiplier: 成本倍数
            
        Returns:
            压力下的指标
        """
        base_cost = self._base_commission + self._base_slippage
        
        # 期望值：直接按成本比例减少
        # 假设原始期望已包含基础成本
        original_expectancy = base_metrics["expectancy"]
        
        # 如果原始期望 <= 0，成本增加后仍然 <= 0
        if original_expectancy <= 0:
            stress_expectancy = original_expectancy * multiplier  # 仍然是负的或零
        else:
            # 原始期望 = avg_win * win_rate - avg_loss * loss_rate - base_cost
            # 压力期望 = original_expectancy - (multiplier - 1) * base_cost
            # 但更准确的做法是：假设每笔交易成本增加
            stress_expectancy = original_expectancy - (multiplier - 1) * base_cost * base_metrics["num_trades"]
        
        # 夏普比率：期望降低会导致夏普降低
        base_sharpe = base_metrics["sharpe_ratio"]
        stress_sharpe = base_sharpe * (stress_expectancy / original_expectancy) if original_expectancy != 0 else 0
        
        # 最大回撤：成本增加会导致回撤增加（简化模型）
        base_dd = base_metrics["max_drawdown"]
        stress_dd = base_dd * (1 + (multiplier - 1) * 0.3)  # 成本每增加0.5x，回撤增加15%
        
        # 总收益率：成本增加会减少收益
        base_return = base_metrics["total_return"]
        stress_return = base_return - (multiplier - 1) * base_cost * 100  # 简化估算
        
        # 胜率：成本增加轻微影响胜率
        base_win_rate = base_metrics["win_rate"]
        stress_win_rate = max(0, base_win_rate - (multiplier - 1) * 0.05)  # 每增加0.5x成本，胜率降低2.5%
        
        return {
            "expectancy": stress_expectancy,
            "sharpe_ratio": max(0, stress_sharpe),
            "max_drawdown": stress_dd,
            "total_return": stress_return,
            "win_rate": stress_win_rate,
        }


# ==================== 辅助函数 ====================

def calculate_cost_adjusted_expectancy(
    original_expectancy: float,
    num_trades: int,
    commission_rate: float,
    slippage_rate: float,
    multiplier: float,
) -> float:
    """
    计算成本调整后的期望值
    
    Args:
        original_expectancy: 原始期望值
        num_trades: 交易次数
        commission_rate: 手续费率
        slippage_rate: 滑点率
        multiplier: 成本倍数
        
    Returns:
        成本调整后的期望值
    """
    if original_expectancy <= 0:
        return original_expectancy * multiplier
    
    base_cost_per_trade = commission_rate + slippage_rate
    extra_cost = (multiplier - 1) * base_cost_per_trade * num_trades
    
    return original_expectancy - extra_cost


def estimate_cost_break_even_multiplier(
    original_expectancy: float,
    num_trades: int,
    commission_rate: float,
    slippage_rate: float,
) -> float:
    """
    估算成本盈亏平衡倍数（期望刚好为0时的成本倍数）
    
    Args:
        original_expectancy: 原始期望值
        num_trades: 交易次数
        commission_rate: 手续费率
        slippage_rate: 滑点率
        
    Returns:
        盈亏平衡倍数
    """
    if original_expectancy <= 0:
        return 1.0  # 已经是负期望，无法通过降低成本变正
    
    if num_trades <= 0:
        return float("inf")
    
    base_cost_per_trade = commission_rate + slippage_rate
    if base_cost_per_trade <= 0:
        return float("inf")
    
    return 1.0 + original_expectancy / (base_cost_per_trade * num_trades)
