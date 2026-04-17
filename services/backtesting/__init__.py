"""
Backtesting - 回测框架集成模块
=============================
提供与回测引擎集成的端口和适配器。

核心组件：
- ports: 协议定义（BacktestEnginePort, DataProviderPort, ResultReporterPort, StrategyAdapterPort）
- adapters: 具体实现（QuantConnect Lean, VectorBT）

使用方式：
1. 实现 DataProviderPort 获取历史数据
2. 使用 StrategyAdapterPort 将策略转换为框架格式
3. 通过 BacktestEnginePort 执行回测
4. 利用 ResultReporterPort 存储和检索报告

示例：
    # QuantConnect Lean 回测
    engine = LeanBacktestEngine()
    reporter = PostgresResultReporter()
    adapter = QuantConnectStrategyAdapter()
    
    result = await engine.run_backtest(config, strategy)
    report = BacktestReport(report_id="1", strategy_name="MyStrategy", config=config, result=result)
    await reporter.save_report(report)
"""
from trader.services.backtesting.ports import (
    BacktestEnginePort,
    BacktestConfig,
    BacktestResult,
    BacktestReport,
    BacktestFeature,
    OptimizationMethod,
    OptimizationResult,
    DataProviderPort,
    OHLCV,
    ResultReporterPort,
    StrategyAdapterPort,
    FrameworkType,
)
from trader.core.domain.models.signal import Signal

# Report formatter exports
from trader.services.backtesting.report_formatter import (
    ReportFormatter,
    StandardizedBacktestReport,
    ReturnMetrics,
    RiskMetrics,
    RiskAdjustedMetrics,
    TradeStatistics,
    BenchmarkComparison,
    MetaInfo,
)

# Visualizer exports
from trader.services.backtesting.visualizer import (
    BacktestVisualizer,
    PlotConfig,
    plot_equity_curve,
    plot_drawdown,
    plot_monthly_heatmap,
    plot_trade_markers,
    plot_returns_distribution,
    plot_combined,
)

# Validation exports
from trader.services.backtesting.validation import (
    ValidationStatus,
    WalkForwardAnalyzer,
    WalkForwardReport,
    WalkForwardSplit,
    KFoldValidator,
    KFoldReport,
    KFoldSplit,
    SensitivityAnalyzer,
    SensitivityReport,
    SensitivityResult,
    OverfittingDetector,
    OverfittingReport,
)

# Lifecycle integration exports
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

# Performance benchmark exports
from trader.services.backtesting.performance_benchmark import (
    PerformanceBenchmark,
    PerformanceTargets,
    BenchmarkResult,
    BenchmarkReport,
    BenchmarkRunner,
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
]
