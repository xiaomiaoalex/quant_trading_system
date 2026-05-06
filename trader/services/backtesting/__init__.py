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

from trader.core.domain.models.signal import Signal

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

# Slippage model exports
from trader.services.backtesting.slippage import (
    BinanceSlippageConfig,
    SlippageModel,
    calculate_slippage,
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
    # Binance Data Provider
    "BinanceDataProvider",
    "BinanceDataConfig",
    # Binance Execution Adapter
    "BinanceExecutionAdapter",
    # Slippage Model
    "BinanceSlippageConfig",
    "SlippageModel",
    "calculate_slippage",
]
