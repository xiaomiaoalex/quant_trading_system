"""
Signal Domain Layer - 信号领域层
=================================

资金结构信号计算模块，纯函数实现，无IO依赖。

导出：
- FundingRateZScore: 资金费率Z-Score信号
- OIChangeRateDivergence: OI变化率与价格背离信号
- LongShortRatioAnomaly: 多空比异常信号
"""

from trader.core.domain.signals.capital_structure_signals import (
    FundingRateZScore,
    OIChangeRateDivergence,
    LongShortRatioAnomaly,
    DivergenceDirection,
)

__all__ = [
    "FundingRateZScore",
    "OIChangeRateDivergence",
    "LongShortRatioAnomaly",
    "DivergenceDirection",
]
