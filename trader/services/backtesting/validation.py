"""
Out-of-Sample Validation Framework
==================================

Provides validation methods for backtesting:
- Walk-Forward Analysis: Rolling window optimization with out-of-sample testing
- K-Fold Cross-Validation: Time-series aware cross-validation
- Parameter Sensitivity Analysis: Grid scan with stability evaluation
- Overfitting Detection: In-sample vs out-of-sample comparison

Architecture:
    BacktestConfig -> WalkForwardAnalyzer -> WalkForwardReport
                -> KFoldValidator -> KFoldReport
                -> SensitivityAnalyzer -> SensitivityReport
                -> OverfittingDetector -> OverfittingReport
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Type
import statistics

from trader.services.backtesting.ports import BacktestConfig, BacktestResult, FrameworkType


class ValidationStatus(Enum):
    """Validation status."""
    PASSED = "PASSED"
    FAILED = "FAILED"
    WARNING = "WARNING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(slots=True)
class WalkForwardSplit:
    """Single Walk-Forward split result."""
    split_index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_result: Optional[BacktestResult]
    test_result: Optional[BacktestResult]
    best_params: Dict[str, Any]
    train_metrics: Dict[str, Any]
    test_metrics: Dict[str, Any]


@dataclass(slots=True)
class WalkForwardReport:
    """
    Walk-Forward Analysis Report
    
    属性：
        splits: 各分割的结果列表
        in_sample_metrics: 样本内聚合指标
        out_of_sample_metrics: 样本外聚合指标
        overfitting_score: 过拟合分数 (0-1, 越高越容易过拟合)
        overfitting_status: 过拟合检测状态
        consistency_score: 稳定性分数
    """
    splits: List[WalkForwardSplit]
    in_sample_metrics: Dict[str, Any]
    out_of_sample_metrics: Dict[str, Any]
    overfitting_score: float
    overfitting_status: ValidationStatus
    consistency_score: float
    avg_params_stability: Dict[str, float]  # 参数稳定性


@dataclass(slots=True)
class KFoldSplit:
    """Single K-Fold split result."""
    fold_index: int
    train_start: datetime
    train_end: datetime
    val_start: datetime
    val_end: datetime
    train_result: Optional[BacktestResult]
    val_result: Optional[BacktestResult]
    metrics: Dict[str, Any]


@dataclass(slots=True)
class KFoldReport:
    """
    K-Fold Cross-Validation Report
    
    属性：
        folds: 各折叠的结果列表
        avg_train_metrics: 平均训练指标
        avg_val_metrics: 平均验证指标
        metric_std: 指标标准差 (稳定性)
        validation_status: 验证状态
    """
    folds: List[KFoldSplit]
    avg_train_metrics: Dict[str, float]
    avg_val_metrics: Dict[str, float]
    metric_std: Dict[str, float]
    validation_status: ValidationStatus


@dataclass(slots=True)
class SensitivityResult:
    """Parameter sensitivity analysis result for a single parameter."""
    param_name: str
    param_values: List[Any]
    metric_name: str
    metric_values: List[float]
    best_value: Any
    best_metric: float
    sensitivity_score: float  # 0-1, 越高越敏感
    stability: str  # "HIGH", "MEDIUM", "LOW"


@dataclass(slots=True)
class SensitivityReport:
    """
    Parameter Sensitivity Analysis Report
    
    属性：
        results: 各参数的分析结果
        overall_sensitivity: 总体敏感度
        most_sensitive_params: 最敏感的参数列表
        stable_params: 稳定的参数列表
        recommendation: 参数设置建议
    """
    results: List[SensitivityResult]
    overall_sensitivity: float
    most_sensitive_params: List[str]
    stable_params: List[str]
    recommendation: str


@dataclass(slots=True)
class OverfittingReport:
    """
    Overfitting Detection Report
    
    属性：
        in_sample_sharpe: 样本内夏普比率
        out_of_sample_sharpe: 样本外夏普比率
        sharpe_ratio_decay: 夏普比率衰减 (样本外/样本内)
        return_decay: 收益衰减
        drawdown_ratio: 回撤比率
        validation_status: 检测状态
        overfitting_indicators: 过拟合指标列表
        recommendations: 建议列表
    """
    in_sample_sharpe: float
    out_of_sample_sharpe: float
    sharpe_ratio_decay: float
    return_decay: float
    drawdown_ratio: float
    validation_status: ValidationStatus
    overfitting_indicators: List[str]
    recommendations: List[str]


class WalkForwardAnalyzer:
    """
    Walk-Forward 分析器
    
    执行滚动窗口优化和样本外验证:
    时间线: | train1 | test1 | train2 | test2 | train3 | test3 | ...
    
    使用方式:
        analyzer = WalkForwardAnalyzer(backtest_engine)
        report = analyzer.analyze(
            strategy_class=MyStrategy,
            param_grid={"rsi_period": [14, 21, 28]},
            train_period=timedelta(days=90),
            test_period=timedelta(days=30),
            n_splits=5,
        )
    """
    
    def __init__(
        self,
        backtest_func: Callable[[BacktestConfig, Dict[str, Any]], BacktestResult],
        optimization_func: Optional[Callable] = None,
    ):
        """
        初始化 Walk-Forward 分析器
        
        Args:
            backtest_func: 回测执行函数 (config, params) -> BacktestResult
            optimization_func: 参数优化函数 (可选)
        """
        self._backtest = backtest_func
        self._optimize = optimization_func
    
    def analyze(
        self,
        strategy_class: Type[Any],
        param_grid: Dict[str, Sequence[Any]],
        data: List[Any],
        train_period: timedelta,
        test_period: timedelta,
        n_splits: int = 5,
        metric: str = "sharpe_ratio",
        initial_capital: Decimal = Decimal("100000"),
    ) -> WalkForwardReport:
        """
        执行 Walk-Forward 分析
        
        Args:
            strategy_class: 策略类
            param_grid: 参数网格
            data: 历史数据列表
            train_period: 训练期长度
            test_period: 测试期长度
            n_splits: 分割数量
            metric: 优化指标
            initial_capital: 初始资金
            
        Returns:
            WalkForwardReport: 分析报告
        """
        if len(data) < 2:
            return self._create_insufficient_data_report()
        
        splits: List[WalkForwardSplit] = []
        all_train_metrics: List[Dict[str, Any]] = []
        all_test_metrics: List[Dict[str, Any]] = []
        all_best_params: List[Dict[str, Any]] = []
        
        # Calculate window sizes
        total_period = train_period + test_period
        total_days = total_period.days * n_splits
        
        # Get date range from data
        start_date = data[0].get("timestamp", datetime.now(timezone.utc)) if isinstance(data[0], dict) else getattr(data[0], 'timestamp', datetime.now(timezone.utc))
        if isinstance(start_date, (int, float)):
            start_date = datetime.fromtimestamp(start_date, tz=timezone.utc)
        
        for i in range(n_splits):
            # Calculate train and test periods
            train_start = start_date + timedelta(days=i * test_period.days)
            train_end = train_start + train_period
            test_start = train_end
            test_end = test_start + test_period
            
            # Skip if test end exceeds data range
            test_end_ts = test_end.timestamp() if isinstance(test_end, datetime) else test_end
            last_data_ts = data[-1].get("timestamp", datetime.now(timezone.utc).timestamp()) if isinstance(data[-1], dict) else getattr(data[-1], 'timestamp', datetime.now(timezone.utc).timestamp())
            if isinstance(last_data_ts, datetime):
                last_data_ts = last_data_ts.timestamp()
            
            if test_end_ts > last_data_ts + 86400:  # Allow 1 day tolerance
                break
            
            # Optimize on training period
            train_data = self._filter_data_by_date(data, train_start, train_end)
            
            if len(train_data) < 10:  # Need minimum data
                continue
            
            best_params = self._optimize_params(strategy_class, param_grid, train_data, metric, initial_capital)
            all_best_params.append(best_params)
            
            # Backtest on training period with best params
            train_config = BacktestConfig(
                start_date=train_start,
                end_date=train_end,
                initial_capital=initial_capital,
                symbol="UNKNOWN",
            )
            train_result = self._backtest(train_config, best_params)
            train_metrics = self._extract_metrics(train_result, metric)
            all_train_metrics.append(train_metrics)
            
            # Test on test period with best params
            test_data = self._filter_data_by_date(data, test_start, test_end)
            
            if len(test_data) < 5:
                test_result = None
                test_metrics = {}
            else:
                test_config = BacktestConfig(
                    start_date=test_start,
                    end_date=test_end,
                    initial_capital=initial_capital,
                    symbol="UNKNOWN",
                )
                test_result = self._backtest(test_config, best_params)
                test_metrics = self._extract_metrics(test_result, metric)
                all_test_metrics.append(test_metrics)
            
            split = WalkForwardSplit(
                split_index=i,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                train_result=train_result,
                test_result=test_result,
                best_params=best_params,
                train_metrics=train_metrics,
                test_metrics=test_metrics,
            )
            splits.append(split)
        
        # Calculate aggregated metrics
        in_sample_metrics = self._aggregate_metrics(all_train_metrics)
        out_of_sample_metrics = self._aggregate_metrics(all_test_metrics)
        
        # Calculate overfitting score
        overfitting_score, overfitting_status = self._calculate_overfitting(
            in_sample_metrics, out_of_sample_metrics
        )
        
        # Calculate consistency score
        consistency_score = self._calculate_consistency(all_test_metrics)
        
        # Calculate parameter stability
        avg_params_stability = self._calculate_param_stability(all_best_params)
        
        return WalkForwardReport(
            splits=splits,
            in_sample_metrics=in_sample_metrics,
            out_of_sample_metrics=out_of_sample_metrics,
            overfitting_score=overfitting_score,
            overfitting_status=overfitting_status,
            consistency_score=consistency_score,
            avg_params_stability=avg_params_stability,
        )
    
    def _filter_data_by_date(
        self,
        data: List[Any],
        start: datetime,
        end: datetime,
    ) -> List[Any]:
        """Filter data by date range."""
        result = []
        for point in data:
            ts = point.get("timestamp", datetime.now(timezone.utc)) if isinstance(point, dict) else getattr(point, 'timestamp', None)
            if isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(ts, tz=timezone.utc)
            if ts and start <= ts <= end:
                result.append(point)
        return result
    
    def _optimize_params(
        self,
        strategy_class: Type[Any],
        param_grid: Dict[str, Sequence[Any]],
        data: List[Any],
        metric: str,
        initial_capital: Decimal,
    ) -> Dict[str, Any]:
        """Optimize parameters on training data."""
        if self._optimize:
            return self._optimize(strategy_class, param_grid, data, metric, initial_capital)
        
        # Simple grid search fallback
        best_params = {}
        best_score = float('-inf')
        
        import itertools
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        
        for combination in itertools.product(*values):
            params = dict(zip(keys, combination))
            
            # Run backtest
            start_date = data[0].get("timestamp", datetime.now(timezone.utc)) if isinstance(data[0], dict) else getattr(data[0], 'timestamp', datetime.now(timezone.utc))
            end_date = data[-1].get("timestamp", datetime.now(timezone.utc)) if isinstance(data[-1], dict) else getattr(data[-1], 'timestamp', datetime.now(timezone.utc))
            
            config = BacktestConfig(
                start_date=start_date,
                end_date=end_date,
                initial_capital=initial_capital,
                symbol="UNKNOWN",
            )
            
            result = self._backtest(config, params)
            metrics = self._extract_metrics(result, metric)
            score = metrics.get(metric, 0)
            
            if score > best_score:
                best_score = score
                best_params = params
        
        return best_params
    
    def _extract_metrics(self, result: Optional[BacktestResult], metric: str) -> Dict[str, Any]:
        """Extract metrics from backtest result."""
        if result is None:
            return {}
        
        metrics: Dict[str, Any] = {}
        
        # Try direct attributes
        if hasattr(result, metric):
            metrics[metric] = float(getattr(result, metric))
        
        if hasattr(result, 'sharpe_ratio'):
            metrics['sharpe_ratio'] = float(result.sharpe_ratio)
        if hasattr(result, 'max_drawdown'):
            metrics['max_drawdown'] = float(result.max_drawdown)
        if hasattr(result, 'total_return'):
            metrics['total_return'] = float(result.total_return)
        if hasattr(result, 'win_rate'):
            metrics['win_rate'] = float(result.win_rate)
        if hasattr(result, 'num_trades'):
            metrics['num_trades'] = result.num_trades
        
        # Try nested result
        if hasattr(result, 'result'):
            nested = result.result
            if hasattr(nested, metric):
                metrics[metric] = float(getattr(nested, metric))
        
        # Try metrics dict
        if hasattr(result, 'metrics') and isinstance(result.metrics, dict):
            for k, v in result.metrics.items():
                if isinstance(v, (int, float, Decimal)):
                    metrics[k] = float(v)
        
        return metrics
    
    def _aggregate_metrics(self, metrics_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Aggregate metrics across splits."""
        if not metrics_list:
            return {}
        
        aggregated: Dict[str, Any] = {}
        
        # Collect all metric names
        all_keys: set = set()
        for m in metrics_list:
            all_keys.update(m.keys())
        
        for key in all_keys:
            values = [m.get(key, 0) for m in metrics_list if key in m]
            if values:
                aggregated[f"{key}_mean"] = statistics.mean(values)
                aggregated[f"{key}_std"] = statistics.stdev(values) if len(values) > 1 else 0
                aggregated[f"{key}_min"] = min(values)
                aggregated[f"{key}_max"] = max(values)
        
        return aggregated
    
    def _calculate_overfitting(
        self,
        in_sample: Dict[str, Any],
        out_of_sample: Dict[str, Any],
    ) -> Tuple[float, ValidationStatus]:
        """Calculate overfitting score."""
        if not out_of_sample:
            return 0.0, ValidationStatus.INSUFFICIENT_DATA
        
        in_sharpe = in_sample.get("sharpe_ratio_mean", 0)
        out_sharpe = out_of_sample.get("sharpe_ratio_mean", 0)
        
        if in_sharpe <= 0:
            return 0.0, ValidationStatus.WARNING
        
        decay = out_sharpe / in_sharpe if in_sharpe > 0 else 0
        
        # Overfitting score: 1 - decay (higher = more overfitting)
        score = max(0.0, 1.0 - decay)
        
        if decay >= 0.8:
            status = ValidationStatus.PASSED
        elif decay >= 0.5:
            status = ValidationStatus.WARNING
        else:
            status = ValidationStatus.FAILED
        
        return score, status
    
    def _calculate_consistency(self, metrics_list: List[Dict[str, Any]]) -> float:
        """Calculate consistency score (coefficient of variation)."""
        if len(metrics_list) < 2:
            return 1.0
        
        # Use Sharpe ratio for consistency
        sharpe_values = [m.get("sharpe_ratio", 0) for m in metrics_list]
        sharpe_values = [s for s in sharpe_values if s != 0]
        
        if not sharpe_values:
            return 1.0
        
        mean = statistics.mean(sharpe_values)
        if mean == 0:
            return 0.0
        
        std = statistics.stdev(sharpe_values) if len(sharpe_values) > 1 else 0
        cv = std / abs(mean)
        
        # Consistency = 1 - cv (clamped to 0-1)
        return max(0.0, min(1.0, 1.0 - cv))
    
    def _calculate_param_stability(self, params_list: List[Dict[str, Any]]) -> Dict[str, float]:
        """Calculate parameter stability across splits."""
        if not params_list:
            return {}
        
        stability: Dict[str, float] = {}
        
        # Collect all parameter names
        all_keys: set = set()
        for p in params_list:
            all_keys.update(p.keys())
        
        for key in all_keys:
            values = [p.get(key) for p in params_list if key in p]
            
            if not values or len(values) < 2:
                stability[key] = 1.0
                continue
            
            # Check if all values are the same
            if all(v == values[0] for v in values):
                stability[key] = 1.0
            else:
                # Calculate coefficient of variation for numeric values
                numeric_values = [v for v in values if isinstance(v, (int, float))]
                if numeric_values and len(numeric_values) > 1:
                    mean = statistics.mean(numeric_values)
                    if mean != 0:
                        std = statistics.stdev(numeric_values)
                        cv = std / abs(mean)
                        stability[key] = max(0.0, 1.0 - min(cv, 1.0))
                    else:
                        stability[key] = 0.0
                else:
                    stability[key] = 0.5  # Mixed or non-numeric
        
        return stability
    
    def _create_insufficient_data_report(self) -> WalkForwardReport:
        """Create report for insufficient data."""
        return WalkForwardReport(
            splits=[],
            in_sample_metrics={},
            out_of_sample_metrics={},
            overfitting_score=0.0,
            overfitting_status=ValidationStatus.INSUFFICIENT_DATA,
            consistency_score=0.0,
            avg_params_stability={},
        )


class KFoldValidator:
    """
    K-Fold 交叉验证器
    
    执行时间序列感知的 K-Fold 交叉验证。
    与标准 K-Fold 不同，验证时不打乱数据顺序。
    
    使用方式:
        validator = KFoldValidator(backtest_engine)
        report = validator.validate(
            strategy_class=MyStrategy,
            params={"rsi_period": 14},
            data=historical_data,
            n_folds=5,
        )
    """
    
    def __init__(
        self,
        backtest_func: Callable[[BacktestConfig, Dict[str, Any]], BacktestResult],
    ):
        """
        初始化 K-Fold 验证器
        
        Args:
            backtest_func: 回测执行函数
        """
        self._backtest = backtest_func
    
    def validate(
        self,
        strategy_class: Type[Any],
        params: Dict[str, Any],
        data: List[Any],
        n_folds: int = 5,
        metric: str = "sharpe_ratio",
        initial_capital: Decimal = Decimal("100000"),
    ) -> KFoldReport:
        """
        执行 K-Fold 交叉验证
        
        Args:
            strategy_class: 策略类
            params: 策略参数
            data: 历史数据
            n_folds: 折叠数量
            metric: 评估指标
            initial_capital: 初始资金
            
        Returns:
            KFoldReport: 验证报告
        """
        if len(data) < n_folds * 2:
            return self._create_insufficient_data_report()
        
        # Split data into n_folds
        fold_size = len(data) // n_folds
        folds: List[KFoldSplit] = []
        all_train_metrics: List[Dict[str, Any]] = []
        all_val_metrics: List[Dict[str, Any]] = []
        
        for i in range(n_folds):
            # Calculate indices
            val_start_idx = i * fold_size
            val_end_idx = val_start_idx + fold_size if i < n_folds - 1 else len(data)
            
            # Training: everything before validation
            # Validation: current fold
            train_end_idx = val_start_idx
            val_start = data[val_start_idx].get("timestamp", datetime.now(timezone.utc)) if isinstance(data[val_start_idx], dict) else getattr(data[val_start_idx], 'timestamp', datetime.now(timezone.utc))
            val_end = data[val_end_idx - 1].get("timestamp", datetime.now(timezone.utc)) if isinstance(data[val_end_idx - 1], dict) else getattr(data[val_end_idx - 1], 'timestamp', datetime.now(timezone.utc))
            
            train_data = data[:train_end_idx]
            val_data = data[val_start_idx:val_end_idx]
            
            if len(train_data) < 10 or len(val_data) < 5:
                continue
            
            train_start = train_data[0].get("timestamp", datetime.now(timezone.utc)) if isinstance(train_data[0], dict) else getattr(train_data[0], 'timestamp', datetime.now(timezone.utc))
            train_end = train_data[-1].get("timestamp", datetime.now(timezone.utc)) if isinstance(train_data[-1], dict) else getattr(train_data[-1], 'timestamp', datetime.now(timezone.utc))
            
            # Backtest on training
            train_config = BacktestConfig(
                start_date=train_start,
                end_date=train_end,
                initial_capital=initial_capital,
                symbol="UNKNOWN",
            )
            train_result = self._backtest(train_config, params)
            train_metrics = self._extract_metrics(train_result, metric)
            all_train_metrics.append(train_metrics)
            
            # Backtest on validation
            val_config = BacktestConfig(
                start_date=val_start,
                end_date=val_end,
                initial_capital=initial_capital,
                symbol="UNKNOWN",
            )
            val_result = self._backtest(val_config, params)
            val_metrics = self._extract_metrics(val_result, metric)
            all_val_metrics.append(val_metrics)
            
            fold = KFoldSplit(
                fold_index=i,
                train_start=train_start,
                train_end=train_end,
                val_start=val_start,
                val_end=val_end,
                train_result=train_result,
                val_result=val_result,
                metrics=val_metrics,
            )
            folds.append(fold)
        
        # Aggregate metrics
        avg_train = self._aggregate_metrics(all_train_metrics)
        avg_val = self._aggregate_metrics(all_val_metrics)
        metric_std = self._calculate_metric_std(all_val_metrics)
        
        # Determine validation status
        status = self._determine_status(avg_val, metric_std)
        
        return KFoldReport(
            folds=folds,
            avg_train_metrics=avg_train,
            avg_val_metrics=avg_val,
            metric_std=metric_std,
            validation_status=status,
        )
    
    def _extract_metrics(self, result: Optional[BacktestResult], metric: str) -> Dict[str, Any]:
        """Extract metrics from backtest result."""
        if result is None:
            return {}
        
        metrics: Dict[str, Any] = {}
        
        if hasattr(result, metric):
            metrics[metric] = float(getattr(result, metric))
        
        if hasattr(result, 'sharpe_ratio'):
            metrics['sharpe_ratio'] = float(result.sharpe_ratio)
        if hasattr(result, 'max_drawdown'):
            metrics['max_drawdown'] = float(result.max_drawdown)
        if hasattr(result, 'total_return'):
            metrics['total_return'] = float(result.total_return)
        if hasattr(result, 'win_rate'):
            metrics['win_rate'] = float(result.win_rate)
        if hasattr(result, 'num_trades'):
            metrics['num_trades'] = result.num_trades
        
        if hasattr(result, 'result'):
            nested = result.result
            if hasattr(nested, metric):
                metrics[metric] = float(getattr(nested, metric))
        
        if hasattr(result, 'metrics') and isinstance(result.metrics, dict):
            for k, v in result.metrics.items():
                if isinstance(v, (int, float, Decimal)):
                    metrics[k] = float(v)
        
        return metrics
    
    def _aggregate_metrics(self, metrics_list: List[Dict[str, Any]]) -> Dict[str, float]:
        """Aggregate metrics."""
        if not metrics_list:
            return {}
        
        aggregated: Dict[str, float] = {}
        all_keys: set = set()
        for m in metrics_list:
            all_keys.update(m.keys())
        
        for key in all_keys:
            values = [m.get(key, 0) for m in metrics_list if key in m]
            if values:
                aggregated[key] = statistics.mean(values)
        
        return aggregated
    
    def _calculate_metric_std(self, metrics_list: List[Dict[str, Any]]) -> Dict[str, float]:
        """Calculate standard deviation of metrics."""
        std_dict: Dict[str, float] = {}
        all_keys: set = set()
        for m in metrics_list:
            all_keys.update(m.keys())
        
        for key in all_keys:
            values = [m.get(key, 0) for m in metrics_list if key in m]
            if len(values) > 1:
                std_dict[key] = statistics.stdev(values)
            else:
                std_dict[key] = 0.0
        
        return std_dict
    
    def _determine_status(self, avg_metrics: Dict[str, float], metric_std: Dict[str, float]) -> ValidationStatus:
        """Determine validation status."""
        sharpe = avg_metrics.get('sharpe_ratio', 0)
        sharpe_std = metric_std.get('sharpe_ratio', 0)
        
        # Check stability (low std = stable)
        if sharpe_std > sharpe * 0.5 and sharpe_std > 0.5:  # High variability
            return ValidationStatus.WARNING
        
        if sharpe >= 1.0:
            return ValidationStatus.PASSED
        elif sharpe >= 0.5:
            return ValidationStatus.WARNING
        else:
            return ValidationStatus.FAILED
    
    def _create_insufficient_data_report(self) -> KFoldReport:
        """Create report for insufficient data."""
        return KFoldReport(
            folds=[],
            avg_train_metrics={},
            avg_val_metrics={},
            metric_std={},
            validation_status=ValidationStatus.INSUFFICIENT_DATA,
        )


class SensitivityAnalyzer:
    """
    参数敏感性分析器
    
    对参数网格进行扫描，评估各参数的敏感度和稳定性。
    
    使用方式:
        analyzer = SensitivityAnalyzer(backtest_engine)
        report = analyzer.analyze(
            strategy_class=MyStrategy,
            param_grid={"rsi_period": range(10, 30)},
            data=historical_data,
        )
    """
    
    def __init__(
        self,
        backtest_func: Callable[[BacktestConfig, Dict[str, Any]], BacktestResult],
    ):
        """初始化敏感性分析器"""
        self._backtest = backtest_func
    
    def analyze(
        self,
        strategy_class: Type[Any],
        param_grid: Dict[str, Sequence[Any]],
        data: List[Any],
        metric: str = "sharpe_ratio",
        initial_capital: Decimal = Decimal("100000"),
    ) -> SensitivityReport:
        """
        执行参数敏感性分析
        
        Args:
            strategy_class: 策略类
            param_grid: 参数网格
            data: 历史数据
            metric: 评估指标
            initial_capital: 初始资金
            
        Returns:
            SensitivityReport: 分析报告
        """
        results: List[SensitivityResult] = []
        
        for param_name, param_values in param_grid.items():
            # Test each value while holding others constant
            result = self._analyze_single_param(
                strategy_class, param_name, param_values, param_grid, data, metric, initial_capital
            )
            results.append(result)
        
        # Calculate overall sensitivity
        overall_sensitivity = statistics.mean([r.sensitivity_score for r in results]) if results else 0.0
        
        # Identify sensitive and stable params
        sensitive_threshold = 0.7
        stable_threshold = 0.3
        
        most_sensitive = [r.param_name for r in results if r.sensitivity_score >= sensitive_threshold]
        stable_params = [r.param_name for r in results if r.sensitivity_score <= stable_threshold]
        
        # Generate recommendation
        recommendation = self._generate_recommendation(results, overall_sensitivity)
        
        return SensitivityReport(
            results=results,
            overall_sensitivity=overall_sensitivity,
            most_sensitive_params=most_sensitive,
            stable_params=stable_params,
            recommendation=recommendation,
        )
    
    def _analyze_single_param(
        self,
        strategy_class: Type[Any],
        param_name: str,
        param_values: Sequence[Any],
        full_grid: Dict[str, Sequence[Any]],
        data: List[Any],
        metric: str,
        initial_capital: Decimal,
    ) -> SensitivityResult:
        """Analyze sensitivity for a single parameter."""
        # Use median/baseline for other params
        baseline_params = {}
        for k, v in full_grid.items():
            if k != param_name:
                baseline_params[k] = v[len(v) // 2]  # Median value
        
        metric_values: List[float] = []
        
        for value in param_values:
            params = {**baseline_params, param_name: value}
            
            start_date = data[0].get("timestamp", datetime.now(timezone.utc)) if isinstance(data[0], dict) else getattr(data[0], 'timestamp', datetime.now(timezone.utc))
            end_date = data[-1].get("timestamp", datetime.now(timezone.utc)) if isinstance(data[-1], dict) else getattr(data[-1], 'timestamp', datetime.now(timezone.utc))
            
            config = BacktestConfig(
                start_date=start_date,
                end_date=end_date,
                initial_capital=initial_capital,
                symbol="UNKNOWN",
            )
            
            result = self._backtest(config, params)
            metrics = self._extract_metrics(result, metric)
            score = metrics.get(metric, 0)
            metric_values.append(float(score))
        
        # Find best
        best_idx = metric_values.index(max(metric_values)) if metric_values else 0
        best_value = param_values[best_idx]
        best_metric = metric_values[best_idx] if metric_values else 0.0
        
        # Calculate sensitivity score
        sensitivity_score = self._calculate_sensitivity(metric_values)
        
        # Determine stability
        if sensitivity_score <= 0.3:
            stability = "HIGH"
        elif sensitivity_score <= 0.7:
            stability = "MEDIUM"
        else:
            stability = "LOW"
        
        return SensitivityResult(
            param_name=param_name,
            param_values=list(param_values),
            metric_name=metric,
            metric_values=metric_values,
            best_value=best_value,
            best_metric=best_metric,
            sensitivity_score=sensitivity_score,
            stability=stability,
        )
    
    def _extract_metrics(self, result: Optional[BacktestResult], metric: str) -> Dict[str, Any]:
        """Extract metrics from result."""
        if result is None:
            return {}
        
        metrics: Dict[str, Any] = {}
        
        if hasattr(result, metric):
            metrics[metric] = float(getattr(result, metric))
        
        if hasattr(result, 'sharpe_ratio'):
            metrics['sharpe_ratio'] = float(result.sharpe_ratio)
        if hasattr(result, 'total_return'):
            metrics['total_return'] = float(result.total_return)
        if hasattr(result, 'result') and hasattr(result.result, metric):
            metrics[metric] = float(getattr(result.result, metric))
        if hasattr(result, 'metrics') and isinstance(result.metrics, dict):
            for k, v in result.metrics.items():
                if isinstance(v, (int, float, Decimal)):
                    metrics[k] = float(v)
        
        return metrics
    
    def _calculate_sensitivity(self, values: List[float]) -> float:
        """Calculate sensitivity score (coefficient of variation)."""
        if len(values) < 2:
            return 0.0
        
        mean = statistics.mean(values)
        if mean == 0:
            return 1.0  # High sensitivity if all zeros
        
        std = statistics.stdev(values)
        cv = std / abs(mean)
        
        # Clamp to 0-1
        return min(1.0, cv)
    
    def _generate_recommendation(self, results: List[SensitivityResult], overall: float) -> str:
        """Generate parameter recommendation."""
        sensitive = [r.param_name for r in results if r.sensitivity_score > 0.7]
        stable = [r.param_name for r in results if r.sensitivity_score < 0.3]
        
        parts = []
        
        if sensitive:
            parts.append(f"参数 {', '.join(sensitive)} 较敏感，建议细化搜索网格")
        if stable:
            parts.append(f"参数 {', '.join(stable)} 较稳定，可使用默认值")
        if overall > 0.7:
            parts.append("总体敏感度较高，注意过拟合风险")
        elif overall < 0.3:
            parts.append("总体敏感度较低，策略较为稳健")
        
        return "; ".join(parts) if parts else "参数敏感性处于中等水平"


class OverfittingDetector:
    """
    过拟合检测器
    
    比较样本内和样本外性能，检测过拟合迹象。
    
    使用方式:
        detector = OverfittingDetector()
        report = detector.detect(walk_forward_report)
    """
    
    def detect(
        self,
        walk_forward_report: WalkForwardReport,
    ) -> OverfittingReport:
        """
        检测过拟合
        
        Args:
            walk_forward_report: Walk-Forward 分析报告
            
        Returns:
            OverfittingReport: 过拟合检测报告
        """
        in_sample = walk_forward_report.in_sample_metrics
        out_sample = walk_forward_report.out_of_sample_metrics
        
        # Check for insufficient data
        if not in_sample or not out_sample:
            return OverfittingReport(
                in_sample_sharpe=0.0,
                out_of_sample_sharpe=0.0,
                sharpe_ratio_decay=0.0,
                return_decay=0.0,
                drawdown_ratio=0.0,
                validation_status=ValidationStatus.INSUFFICIENT_DATA,
                overfitting_indicators=["Insufficient data for overfitting detection"],
                recommendations=["More data needed for reliable overfitting analysis"],
            )
        
        # Extract Sharpe ratios
        in_sharpe = in_sample.get("sharpe_ratio_mean", 0)
        out_sharpe = out_sample.get("sharpe_ratio_mean", 0)
        
        # Sharpe decay
        sharpe_decay = out_sharpe / in_sharpe if in_sharpe > 0 else 0
        
        # Return decay
        in_return = in_sample.get("total_return_mean", 0)
        out_return = out_sample.get("total_return_mean", 0)
        return_decay = out_return / in_return if in_return > 0 else 0
        
        # Drawdown ratio
        in_dd = abs(in_sample.get("max_drawdown_mean", 0))
        out_dd = abs(out_sample.get("max_drawdown_mean", 0))
        drawdown_ratio = out_dd / in_dd if in_dd > 0 else 0
        
        # Determine status
        indicators: List[str] = []
        recommendations: List[str] = []
        
        if sharpe_decay < 0.5:
            indicators.append("夏普比率衰减严重 (>50%)")
            recommendations.append("考虑简化策略，减少参数数量")
        
        if return_decay < 0.5:
            indicators.append("收益衰减严重")
        
        if drawdown_ratio > 2.0:
            indicators.append("样本外回撤显著增加")
            recommendations.append("注意风险控制参数")
        
        if walk_forward_report.overfitting_score > 0.5:
            indicators.append("Walk-Forward 过拟合分数较高")
        
        if walk_forward_report.consistency_score < 0.3:
            indicators.append("样本外表现不一致")
            recommendations.append("策略在不同市场环境下表现差异大")
        
        # Determine status
        if sharpe_decay >= 0.8 and return_decay >= 0.7 and drawdown_ratio <= 1.5:
            status = ValidationStatus.PASSED
            recommendations.append("策略表现稳健，过拟合风险较低")
        elif sharpe_decay >= 0.5:
            status = ValidationStatus.WARNING
            recommendations.append("策略存在一定过拟合风险，建议进一步验证")
        else:
            status = ValidationStatus.FAILED
            recommendations.append("策略过拟合风险较高，建议重新设计或简化")
        
        return OverfittingReport(
            in_sample_sharpe=in_sharpe,
            out_of_sample_sharpe=out_sharpe,
            sharpe_ratio_decay=sharpe_decay,
            return_decay=return_decay,
            drawdown_ratio=drawdown_ratio,
            validation_status=status,
            overfitting_indicators=indicators,
            recommendations=recommendations,
        )
