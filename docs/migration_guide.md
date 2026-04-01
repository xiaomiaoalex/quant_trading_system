# Backtesting Framework Migration Guide

> **Migration from self-developed evaluator to QuantConnect Lean framework**

## Overview

Phase 5 replaces the self-developed backtesting module (`strategy_evaluator`) with the QuantConnect Lean framework for production-quality backtesting.

## Why Migrate?

The self-developed `strategy_evaluator.py` had critical issues:

| Issue | Impact |
|-------|--------|
| Look-ahead bias | Used current bar close instead of next bar open |
| Slippage direction error | Always added slippage for both BUY and SELL |
| No TP/SL support | Couldn't properly test risk-controlled strategies |
| No out-of-sample validation | High overfitting risk |

## New Framework Benefits

- **Correct execution model**: Next-bar open price execution
- **Direction-aware slippage**: BUY adds slippage, SELL subtracts
- **TP/SL support**: Within-bar high/low trigger check
- **Walk-Forward Analysis**: Rolling window optimization
- **K-Fold Cross-Validation**: Time-series aware validation
- **Apache 2.0 License**: Permissive, commercial-use friendly

## Quick Migration

### Old Code (Deprecated)

```python
from trader.services.strategy_evaluator import BacktestEngine, StrategyMetrics

engine = BacktestEngine()
result = await engine.run_backtest(strategy, config)
```

### New Code (Recommended)

```python
from trader.services.backtesting import (
    QuantConnectLeanBacktestEngine,
    ReportFormatter,
)

engine = QuantConnectLeanBacktestEngine()
report = await engine.run_backtest(config, strategy)

# Format to standardized report
formatter = ReportFormatter()
std_report = formatter.format(report, config)
```

## Component Mapping

| Old Component | New Component | Notes |
|--------------|---------------|-------|
| `BacktestEngine` | `QuantConnectLeanBacktestEngine` | Main backtest engine |
| `StrategyMetrics` | `StandardizedBacktestReport` | Contains all metrics |
| `BacktestReport` | `BacktestReport` | Report structure |
| `LiveEvaluator` | `StrategyLifecycleManager` | Real-time evaluation |
| N/A | `WalkForwardAnalyzer` | Out-of-sample validation |
| N/A | `KFoldValidator` | Cross-validation |
| N/A | `SensitivityAnalyzer` | Parameter sensitivity |
| N/A | `DataValidator` | Data quality checks |

## API Changes

### BacktestConfig

**Old:**
```python
BacktestConfig(
    initial_capital=10000,
    symbols=["BTCUSDT"],
    interval="1h",
)
```

**New:**
```python
from trader.services.backtesting import BacktestConfig
from datetime import datetime, timezone

BacktestConfig(
    start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
    initial_capital=Decimal("10000"),
    symbol="BTCUSDT",
    interval="1h",
)
```

### BacktestResult

**Old:**
```python
result.metrics.sharpe_ratio
result.metrics.max_drawdown
result.metrics.win_rate
```

**New:**
```python
# Direct attributes
result.sharpe_ratio
result.max_drawdown
result.win_rate

# Or through StandardizedBacktestReport
std_report.risk_adjusted.sharpe_ratio
std_report.risk.max_drawdown_percent
std_report.trades.win_rate
```

## Validation Framework

### Walk-Forward Analysis

```python
from trader.services.backtesting import WalkForwardAnalyzer

analyzer = WalkForwardAnalyzer(backtest_engine)
wf_report = analyzer.analyze(
    strategy_class=MyStrategy,
    param_grid={"rsi_period": [14, 21, 28]},
    data=historical_data,
    train_period=timedelta(days=90),
    test_period=timedelta(days=30),
    n_splits=5,
)

# Check overfitting
if wf_report.overfitting_status == ValidationStatus.PASSED:
    print("Strategy is robust")
else:
    print("Strategy may be overfitting")
```

### Auto-Approval Rules

```python
from trader.services.backtesting import AutoApprovalRules

rules = AutoApprovalRules(
    min_sharpe=1.5,
    max_drawdown_pct=15.0,
    min_trades=50,
    min_win_rate=0.5,
)

passed, violations = rules.evaluate(backtest_report)
if not passed:
    print(f"Violations: {violations}")
```

## Data Pipeline

```python
from trader.services.backtesting import DataPipeline, DataValidator

pipeline = DataPipeline()
data, quality = await pipeline.load_data(
    "BTCUSDT", "1h", start_date, end_date
)

if not quality.is_acceptable():
    print(f"Data issues: {quality.issues}")
```

## Performance Benchmarking

```python
from trader.services.backtesting import PerformanceBenchmark

benchmark = PerformanceBenchmark(engine)
report = benchmark.run_all()

if report.all_passed:
    print("Performance targets met")
else:
    for result in report.results:
        if not result.passed:
            print(f"FAILED: {result.name}")
```

## Timeline

- **Phase 5 Week 1-2**: Core adapter layer and architecture
- **Phase 5 Week 3**: Validation framework and lifecycle integration
- **Phase 5 Week 4**: Data pipeline and performance (current)
- **Ongoing**: Legacy module deprecation, documentation

## Support

For questions or issues, please refer to:
- `trader/services/backtesting/README.md`
- `docs/backtesting_architecture.md`
- `PLAN.md` (Phase 5 section)
