"""
Backtesting - 回测框架集成模块
=============================
提供与回测引擎集成的端口和适配器。

核心组件：
- ports: 协议定义（BacktestEnginePort, DataProviderPort, ResultReporterPort, StrategyAdapterPort）
- adapters: 当前 active implementation 为 VectorBT / VectorBTAdapterWithRisk
- research bridge: Qlib 只输出研究预测，经内部 Signal 和 RiskEngine 后进入回测

使用方式：
1. 实现 DataProviderPort 获取历史数据
2. 使用 StrategyAdapterPort 将策略转换为框架格式
3. 通过 BacktestEnginePort 执行回测
4. 利用 ResultReporterPort 存储和检索报告

示例：
    # VectorBT 快速回测
    engine = VectorBTAdapter()
    reporter = PostgresResultReporter()

    result = await engine.run_backtest(config, strategy)
    report = BacktestReport(report_id="1", strategy_name="MyStrategy", config=config, result=result)
    await reporter.save_report(report)

Legacy note:
    QuantConnect Lean 相关运行时代码已清理；历史选型背景仅保留在 docs/adr 文档中。
"""

from trader.core.domain.models.signal import Signal

# Backtest Risk Integration exports
from trader.services.backtesting.backtest_risk_integration import (
    BacktestRiskEnginePort,
    BacktestRiskIntegration,
    BacktestRiskReport,
    BacktestSignalResult,
    BacktestSignalStatus,
)

# Binance data provider exports
from trader.services.backtesting.binance_data_provider import BinanceDataConfig, BinanceDataProvider

# Binance execution adapter exports
from trader.services.backtesting.binance_execution_adapter import BinanceExecutionAdapter

# Data pipeline exports
from trader.services.backtesting.data_pipeline import (
    DataCache,
    DataGap,
    DataGapReason,
    DataPipeline,
    DataQualityIssue,
    DataQualityReport,
    DataQualityStatus,
    DataValidator,
    ParallelBacktestRunner,
    PipelineConfig,
    create_pipeline,
)

# Event-Driven Risk Replay exports
from trader.services.backtesting.event_driven_risk_replay import (
    EventDrivenRiskReplay,
    EventDrivenRiskReplayResult,
    OrderDecision,
    ReplayFill,
    ReplayOrder,
    ReplayRiskDecision,
)

# Lifecycle integration exports
from trader.services.backtesting.lifecycle_integration import (
    AutoApprovalRules,
    BacktestJob,
    BacktestLifecycleIntegration,
    BacktestLifecycleStatus,
    ParameterSweepResult,
    StrategyComparison,
    calculate_scorecard,
    is_valid_transition,
)

# Market Ports exports
from trader.services.backtesting.market_cost_model_port import (
    ChinaStockCostModel,
    ChinaStockCostModelConfig,
    CostBreakdown,
    CostCalculationRequest,
    CostCalculationResult,
    MarketCostModelPort,
    NoOpCostModel,
)
from trader.services.backtesting.market_rule_snapshot_provider_port import (
    AssetClass,
    ChinaStockMetadata,
    ChinaStockSnapshotProvider,
    FakeMarketRuleSnapshotProvider,
    MarketRuleSnapshot,
    MarketRuleSnapshotProviderPort,
    Venue,
)

# Performance benchmark exports
from trader.services.backtesting.performance_benchmark import (
    BenchmarkReport,
    BenchmarkResult,
    BenchmarkRunner,
    PerformanceBenchmark,
    PerformanceTargets,
)
from trader.services.backtesting.ports import (
    OHLCV,
    BacktestConfig,
    BacktestEnginePort,
    BacktestFeature,
    BacktestReport,
    BacktestResult,
    DataProviderPort,
    FrameworkType,
    OptimizationMethod,
    OptimizationResult,
    ResultReporterPort,
    StrategyAdapterPort,
)

# Report formatter exports
from trader.services.backtesting.report_formatter import (
    BenchmarkComparison,
    MetaInfo,
    ReportFormatter,
    ReturnMetrics,
    RiskAdjustedMetrics,
    RiskMetrics,
    StandardizedBacktestReport,
    TradeStatistics,
)

# Risk Aware Order Processor exports
from trader.services.backtesting.risk_aware_order_processor import (
    ExecutableOrder,
    RiskAwareExecutionReport,
    RiskAwareOrderProcessor,
)

# VectorBT adapter exports
from trader.services.backtesting.slippage import (
    BinanceSlippageConfig,
    SlippageModel,
    calculate_slippage,
)
from trader.services.backtesting.trading_calendar_port import (
    ChinaStockCalendar,
    FakeTradingCalendar,
    TradingCalendarPort,
    TradingCalendarSnapshot,
    TradingPhase,
    TradingSession,
)

# Validation exports
from trader.services.backtesting.validation import (
    KFoldReport,
    KFoldSplit,
    KFoldValidator,
    OverfittingDetector,
    OverfittingReport,
    SensitivityAnalyzer,
    SensitivityReport,
    SensitivityResult,
    ValidationStatus,
    WalkForwardAnalyzer,
    WalkForwardReport,
    WalkForwardSplit,
)

# VectorBT adapter exports
from trader.services.backtesting.vectorbt_adapter import VectorBTAdapter, VectorBTConfig

# VectorBT Risk Adapter exports
from trader.services.backtesting.vectorbt_risk_adapter import (
    VectorBTAdapterWithRisk,
    VectorBTRiskAdapterConfig,
    VectorBTRiskInputPlan,
    VectorBTRiskMetrics,
)

# Visualizer exports
from trader.services.backtesting.visualizer import (
    BacktestVisualizer,
    PlotConfig,
    plot_combined,
    plot_drawdown,
    plot_equity_curve,
    plot_monthly_heatmap,
    plot_returns_distribution,
    plot_trade_markers,
)

__all__ = [
    # Ports
    "BacktestEnginePort",
    "BacktestConfig",
    "BacktestResult",
    "BacktestReport",
    "BacktestFeature",
    "OptimizationMethod",
    "OptimizationResult",
    "DataProviderPort",
    "OHLCV",
    "ResultReporterPort",
    "StrategyAdapterPort",
    "FrameworkType",
    "Signal",
    # Report Formatter
    "ReportFormatter",
    "StandardizedBacktestReport",
    "ReturnMetrics",
    "RiskMetrics",
    "RiskAdjustedMetrics",
    "TradeStatistics",
    "BenchmarkComparison",
    "MetaInfo",
    # Visualizer
    "BacktestVisualizer",
    "PlotConfig",
    "plot_equity_curve",
    "plot_drawdown",
    "plot_monthly_heatmap",
    "plot_trade_markers",
    "plot_returns_distribution",
    "plot_combined",
    # Validation
    "ValidationStatus",
    "WalkForwardAnalyzer",
    "WalkForwardReport",
    "WalkForwardSplit",
    "KFoldValidator",
    "KFoldReport",
    "KFoldSplit",
    "SensitivityAnalyzer",
    "SensitivityReport",
    "SensitivityResult",
    "OverfittingDetector",
    "OverfittingReport",
    # Lifecycle Integration
    "AutoApprovalRules",
    "BacktestLifecycleIntegration",
    "BacktestJob",
    "BacktestLifecycleStatus",
    "calculate_scorecard",
    "is_valid_transition",
    "ParameterSweepResult",
    "StrategyComparison",
    # Data Pipeline
    "DataCache",
    "DataGap",
    "DataGapReason",
    "DataPipeline",
    "DataQualityIssue",
    "DataQualityReport",
    "DataQualityStatus",
    "DataValidator",
    "ParallelBacktestRunner",
    "PipelineConfig",
    "create_pipeline",
    # Performance Benchmark
    "PerformanceBenchmark",
    "PerformanceTargets",
    "BenchmarkResult",
    "BenchmarkReport",
    "BenchmarkRunner",
    # VectorBT Adapter
    "VectorBTAdapter",
    "VectorBTConfig",
    # VectorBT Risk Adapter
    "VectorBTAdapterWithRisk",
    "VectorBTRiskAdapterConfig",
    "VectorBTRiskInputPlan",
    "VectorBTRiskMetrics",
    # Backtest Risk Integration
    "BacktestRiskEnginePort",
    "BacktestRiskIntegration",
    "BacktestRiskReport",
    "BacktestSignalResult",
    "BacktestSignalStatus",
    # Event-Driven Risk Replay
    "EventDrivenRiskReplay",
    "EventDrivenRiskReplayResult",
    "OrderDecision",
    "ReplayFill",
    "ReplayOrder",
    "ReplayRiskDecision",
    # Risk Aware Order Processor
    "ExecutableOrder",
    "RiskAwareExecutionReport",
    "RiskAwareOrderProcessor",
    # Binance Data Provider
    "BinanceDataProvider",
    "BinanceDataConfig",
    # Binance Execution Adapter
    "BinanceExecutionAdapter",
    # Slippage Model
    "BinanceSlippageConfig",
    "SlippageModel",
    "calculate_slippage",
    # Market Ports
    "TradingCalendarPort",
    "TradingCalendarSnapshot",
    "TradingPhase",
    "TradingSession",
    "FakeTradingCalendar",
    "ChinaStockCalendar",
    "MarketCostModelPort",
    "CostCalculationRequest",
    "CostCalculationResult",
    "CostBreakdown",
    "NoOpCostModel",
    "ChinaStockCostModel",
    "ChinaStockCostModelConfig",
    "MarketRuleSnapshotProviderPort",
    "MarketRuleSnapshot",
    "AssetClass",
    "Venue",
    "ChinaStockMetadata",
    "FakeMarketRuleSnapshotProvider",
    "ChinaStockSnapshotProvider",
]
