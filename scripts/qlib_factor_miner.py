"""
Qlib Factor Miner - 因子重要性挖掘
====================================

职责：
- 从特征重要性角度挖掘有效因子
- 使用 LightGBM 分析特征贡献度
- 输出因子排名和重要性报告

约束：
- 本模块位于 scripts/ (研究域)
- 不直接触发交易
- 因子分析结果用于策略信号生成
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# =============================================================================
# Factor Types (因子类型)
# =============================================================================

@dataclass
class FactorImportance:
    """
    因子重要性
    
    单个特征/因子的重要性指标
    """
    factor_name: str
    importance: float  # 重要性得分 (0-1)
    rank: int  # 排名 (1 = 最高)
    direction: str  # "positive" | "negative" | "neutral"
    stability: float  # 稳定性得分 (0-1)
    contribution_pct: float  # 贡献占比 (%)
    
    def is_stable(self, threshold: float = 0.7) -> bool:
        """是否稳定因子"""
        return self.stability >= threshold
    
    def is_positive_contributor(self) -> bool:
        """是否为正向贡献因子"""
        return self.direction == "positive"


@dataclass
class FactorReport:
    """
    因子分析报告
    
    完整的因子挖掘结果
    """
    report_id: str
    model_version: str
    feature_version: str
    top_factors: List[FactorImportance]
    all_factors: List[FactorImportance]
    total_factors: int
    positive_count: int
    negative_count: int
    neutral_count: int
    generated_at: str
    recommendations: List[str]  # 使用建议
    
    def top_n(self, n: int = 10) -> List[FactorImportance]:
        """获取 Top N 因子"""
        return self.all_factors[:n]
    
    def stable_factors(self, threshold: float = 0.7) -> List[FactorImportance]:
        """获取稳定因子"""
        return [f for f in self.all_factors if f.is_stable(threshold)]
    
    def positive_factors(self) -> List[FactorImportance]:
        """获取正向贡献因子"""
        return [f for f in self.all_factors if f.is_positive_contributor()]


@dataclass
class FactorMiningConfig:
    """
    因子挖掘配置
    """
    min_importance: float = 0.01  # 最小重要性阈值
    top_n: int = 20  # Top N 因子数量
    stability_window: int = 5  # 稳定性评估窗口
    direction_threshold: float = 0.5  # 方向判断阈值


# =============================================================================
# Factor Miner (因子挖掘器)
# =============================================================================

class QlibFactorMiner:
    """
    Qlib 因子挖掘器
    
    核心功能：
    1. 从 LightGBM 模型提取特征重要性
    2. 计算因子稳定性和方向性
    3. 生成因子排名和报告
    """
    
    def __init__(self, config: Optional[FactorMiningConfig] = None):
        self._config = config or FactorMiningConfig()
    
    def analyze_feature_importance(
        self,
        importance_dict: Dict[str, float],
        model_id: Optional[str] = None,
        feature_version: Optional[str] = None,
    ) -> FactorReport:
        """
        分析特征重要性并生成因子报告
        
        Args:
            importance_dict: 特征重要性字典 {feature_name: importance_score}
            model_id: 模型ID (可选)
            feature_version: 特征版本 (可选)
            
        Returns:
            FactorReport
        """
        logger.info(f"Analyzing {len(importance_dict)} features")
        
        # 按重要性排序
        sorted_features = sorted(
            importance_dict.items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        total_importance = sum(importance_dict.values())
        
        # 构建因子列表
        factors: List[FactorImportance] = []
        for rank, (name, importance) in enumerate(sorted_features, 1):
            contribution_pct = (importance / total_importance * 100) if total_importance > 0 else 0
            
            factor = FactorImportance(
                factor_name=name,
                importance=importance,
                rank=rank,
                direction=self._determine_direction(name, importance_dict),
                stability=self._calculate_stability(name, importance),
                contribution_pct=contribution_pct,
            )
            factors.append(factor)
        
        # 统计方向分布
        positive_count = sum(1 for f in factors if f.direction == "positive")
        negative_count = sum(1 for f in factors if f.direction == "negative")
        neutral_count = len(factors) - positive_count - negative_count
        
        # 生成建议
        recommendations = self._generate_recommendations(factors)
        
        report = FactorReport(
            report_id=f"factor_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            model_version=model_id or "unknown",
            feature_version=feature_version or "unknown",
            top_factors=factors[:self._config.top_n],
            all_factors=factors,
            total_factors=len(factors),
            positive_count=positive_count,
            negative_count=negative_count,
            neutral_count=neutral_count,
            generated_at=datetime.now(timezone.utc).isoformat(),
            recommendations=recommendations,
        )
        
        return report
    
    def _determine_direction(
        self,
        factor_name: str,
        importance_dict: Dict[str, float],
    ) -> str:
        """
        判断因子方向性
        
        基于因子名称和上下文判断方向
        简化实现：实际应用中需要基于回归系数判断
        """
        # 简化的方向判断
        # 实际上需要分析因子与标签的相关性
        positive_indicators = ("long", "buy", "up", "bull", "positive")
        negative_indicators = ("short", "sell", "down", "bear", "negative")
        
        name_lower = factor_name.lower()
        
        for indicator in positive_indicators:
            if indicator in name_lower:
                return "positive"
        
        for indicator in negative_indicators:
            if indicator in name_lower:
                return "negative"
        
        # 默认中性
        return "neutral"
    
    def _calculate_stability(self, factor_name: str, importance: float) -> float:
        """
        计算因子稳定性
        
        简化实现：基于重要性水平的稳定性估计
        实际应用中需要基于多窗口/多季节的交叉验证
        """
        # 简化：重要性高于平均的因子更稳定
        if importance > 0.1:
            return 0.9
        elif importance > 0.05:
            return 0.7
        elif importance > 0.01:
            return 0.5
        else:
            return 0.3
    
    def _generate_recommendations(self, factors: List[FactorImportance]) -> List[str]:
        """生成使用建议"""
        recommendations = []
        
        # Top 因子建议
        top_5 = factors[:5]
        if top_5:
            top_names = [f.factor_name for f in top_5]
            recommendations.append(
                f"Top 5 因子: {', '.join(top_names)} - 建议作为策略核心信号"
            )
        
        # 正向因子建议
        positive_factors = [f for f in factors if f.direction == "positive"]
        if positive_factors:
            recommendations.append(
                f"正向因子 {len(positive_factors)} 个 - 可用于构建做多信号"
            )
        
        # 稳定因子建议
        stable_factors = [f for f in factors if f.stability >= 0.7]
        if stable_factors:
            recommendations.append(
                f"稳定因子 {len(stable_factors)} 个 - 建议用于实盘推理"
            )
        
        # 风险提示
        low_importance = [f for f in factors if f.importance < self._config.min_importance]
        if low_importance:
            recommendations.append(
                f"低重要性因子 {len(low_importance)} 个 - 建议剔除以减少噪声"
            )
        
        return recommendations
    
    def compare_factor_sets(
        self,
        report_a: FactorReport,
        report_b: FactorReport,
    ) -> Dict[str, Any]:
        """
        比较两个因子集
        
        用于版本间因子稳定性分析
        """
        common_factors = set(
            f.factor_name for f in report_a.top_factors
        ) & set(
            f.factor_name for f in report_b.top_factors
        )
        
        only_in_a = set(
            f.factor_name for f in report_a.top_factors
        ) - set(
            f.factor_name for f in report_b.top_factors
        )
        
        only_in_b = set(
            f.factor_name for f in report_b.top_factors
        ) - set(
            f.factor_name for f in report_a.top_factors
        )
        
        return {
            "common_count": len(common_factors),
            "common_factors": list(common_factors),
            "only_in_a": list(only_in_a),
            "only_in_b": list(only_in_b),
            "stability_score": len(common_factors) / max(len(report_a.top_factors), 1),
        }


# =============================================================================
# Main Entry Point (主入口 - 供 Hermes 编排调用)
# =============================================================================

def analyze_factor_importance(
    importance_dict: Dict[str, float],
    model_id: Optional[str] = None,
    feature_version: Optional[str] = None,
    config: Optional[FactorMiningConfig] = None,
) -> FactorReport:
    """
    分析因子重要性的主入口函数
    
    供 Hermes 编排脚本调用
    """
    miner = QlibFactorMiner(config)
    report = miner.analyze_feature_importance(
        importance_dict=importance_dict,
        model_id=model_id,
        feature_version=feature_version,
    )
    
    logger.info(f"Factor analysis complete: {report.total_factors} factors, "
                f"{report.positive_count} positive, {report.negative_count} negative")
    
    return report


# =============================================================================
# CLI Interface (命令行接口)
# =============================================================================

if __name__ == "__main__":
    # 示例用法
    sample_importance = {
        "EMA20": 0.15,
        "EMA50": 0.12,
        "RSI14": 0.10,
        "BOLL_UPPER": 0.08,
        "FUNDING_RATE": 0.07,
        "OI": 0.06,
        "VOLUME_RATIO": 0.05,
        "CLOSE": 0.04,
        "HIGH": 0.03,
        "LOW": 0.02,
    }
    
    report = analyze_factor_importance(
        importance_dict=sample_importance,
        model_id="m240416.abcd",
        feature_version="v1",
    )
    
    print(f"Factor Report: {report.report_id}")
    print(f"Total factors: {report.total_factors}")
    print(f"Top 5 factors:")
    for factor in report.top_factors:
        print(f"  {factor.rank}. {factor.factor_name}: {factor.importance:.4f} ({factor.contribution_pct:.1f}%)")
    print(f"\nRecommendations:")
    for rec in report.recommendations:
        print(f"  - {rec}")