"""
Signal Domain Layer - 信号领域层
========================================

信号计算模块，纯函数实现，无IO依赖。

子模块：
- capital_structure_signals: 资金结构信号（FundingRate, OI, LongShortRatio）
- trend_signals: 趋势信号（EMA, Momentum, BollingerBand）
- price_volume_signals: 价量信号（VolumeExpansion, VolatilityCompression）
"""

from trader.core.domain.signals.capital_structure_signals import (
    FundingRateZScore,
    OIChangeRateDivergence,
    LongShortRatioAnomaly,
    DivergenceDirection,
)

from trader.core.domain.signals.trend_signals import (
    TrendDirection,
    PriceSample,
    EMACrossover,
    EMACrossoverResult,
    PriceMomentum,
    PriceMomentumResult,
    BollingerBandPosition,
    BollingerBandResult,
)

from trader.core.domain.signals.price_volume_signals import (
    VolumeDirection,
    VolumeSample,
    VolumeExpansion,
    VolumeExpansionResult,
    PriceVolumeSample,
    VolatilityCompression,
    VolatilityCompressionResult,
)

__all__ = [
    # Capital Structure Signals
    "FundingRateZScore",
    "OIChangeRateDivergence",
    "LongShortRatioAnomaly",
    "DivergenceDirection",
    # Trend Signals
    "TrendDirection",
    "PriceSample",
    "EMACrossover",
    "EMACrossoverResult",
    "PriceMomentum",
    "PriceMomentumResult",
    "BollingerBandPosition",
    "BollingerBandResult",
    # Price Volume Signals
    "VolumeDirection",
    "VolumeSample",
    "VolumeExpansion",
    "VolumeExpansionResult",
    "PriceVolumeSample",
    "VolatilityCompression",
    "VolatilityCompressionResult",
]
