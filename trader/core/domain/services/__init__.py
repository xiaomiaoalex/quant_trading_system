# Domain Services - 领域服务包
"""
本包包含交易系统的核心领域服务。

核心服务：
- DepthChecker: 订单簿深度检查和滑点估算
- EscapeTimeSimulator: 平仓时间模拟器

重要原则：
1. Core Plane 禁止 IO
2. 服务应该是纯计算逻辑
3. 类型注解必须完整
"""

from trader.core.domain.services.alternative_data_health_gate import (
    AlternativeDataHealthGate,
    DataHealthConfig,
    DataHealthLevel,
    DataHealthMetrics,
    DataHealthThresholds,
    DataReliabilityResult,
    DataSourceType,
)
from trader.core.domain.services.depth_checker import (
    DepthChecker,
    DepthCheckerConfig,
    DepthCheckPreTradePlugin,
    DepthCheckResult,
    MarketDataPort,
)
from trader.core.domain.services.drawdown_venue_deleverage import (
    DeLeverageAction,
    DeLeverageConfig,
    DeLeverageResult,
    DrawdownThresholds,
    DrawdownVenueDeleverage,
    VenueHealthThresholds,
)
from trader.core.domain.services.escape_time_simulator import (
    CooldownProviderPort,
    DepthLevel,
    EscapeTimeResult,
    EscapeTimeSimulator,
    EscapeTimeSimulatorConfig,
    KillSwitchLevel,
    KillSwitchProviderPort,
)
from trader.core.domain.services.funding_oi_window_calculator import (
    FundingOIWindowCalculator,
    FundingRateZScoreResult,
    OIChangeRateResult,
)
from trader.core.domain.services.portfolio_exposure_aggregator import (
    ClusterExposure,
    PortfolioExposureAggregator,
)
from trader.core.domain.services.position_risk_constructor import (
    CooldownResult,
    CooldownTrackerPort,
    MarketRegime,
    MinThresholdResult,
    PerSymbolExposureResult,
    PositionRiskConstruction,
    PositionRiskConstructor,
    PositionRiskConstructorConfig,
    RegimeDiscountResult,
    RegimeProviderPort,
    TotalExposureResult,
)
from trader.core.domain.services.risk_mode_controller import (
    RiskModeController,
    RiskModeControllerConfig,
)
from trader.core.domain.services.market_rule_engine import (
    MarketRuleEngine,
    MarketRuleEngineConfig,
)
from trader.core.domain.services.risk_sizer import RiskSizer, SizerConfig, SizerInputs, SizerResult
from trader.core.domain.services.risk_sizing_engine import (
    ClusterExposureInput,
    ConstraintInput,
    RiskSizingEngine,
    SymbolExposureInput,
)

__all__ = [
    # DepthChecker
    "DepthChecker",
    "DepthCheckerConfig",
    "DepthCheckPreTradePlugin",
    "MarketDataPort",
    "DepthCheckResult",
    # PositionRiskConstructor
    "PositionRiskConstructor",
    "PositionRiskConstructorConfig",
    "MarketRegime",
    "PerSymbolExposureResult",
    "TotalExposureResult",
    "CooldownResult",
    "MinThresholdResult",
    "RegimeDiscountResult",
    "PositionRiskConstruction",
    "RegimeProviderPort",
    "CooldownTrackerPort",
    # EscapeTimeSimulator
    "EscapeTimeSimulator",
    "EscapeTimeSimulatorConfig",
    "EscapeTimeResult",
    "DepthLevel",
    "KillSwitchLevel",
    "KillSwitchProviderPort",
    "CooldownProviderPort",
    # RiskSizer (Phase 6 M2)
    "RiskSizer",
    "SizerConfig",
    "SizerInputs",
    "SizerResult",
    # DrawdownVenueDeleverage (Phase 6 M3)
    "DrawdownVenueDeleverage",
    "DeLeverageAction",
    "DeLeverageConfig",
    "DeLeverageResult",
    "DrawdownThresholds",
    "VenueHealthThresholds",
    # AlternativeDataHealthGate (Phase 6 M5)
    "AlternativeDataHealthGate",
    "DataHealthConfig",
    "DataHealthLevel",
    "DataHealthMetrics",
    "DataHealthThresholds",
    "DataReliabilityResult",
    "DataSourceType",
    # PortfolioExposureAggregator (Crypto Risk P3)
    "ClusterExposure",
    "PortfolioExposureAggregator",
    # FundingOIWindowCalculator (Crypto Risk P4.6)
    "FundingOIWindowCalculator",
    "FundingRateZScoreResult",
    "OIChangeRateResult",
    # RiskSizingEngine (Crypto Risk P5)
    "RiskSizingEngine",
    "SymbolExposureInput",
    "ClusterExposureInput",
    "ConstraintInput",
    # RiskModeController (Crypto Risk P6)
    "RiskModeController",
    "RiskModeControllerConfig",
    # MarketRuleEngine (P9)
    "MarketRuleEngine",
    "MarketRuleEngineConfig",
]
