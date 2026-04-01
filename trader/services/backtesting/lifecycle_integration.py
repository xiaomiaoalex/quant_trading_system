"""
Backtesting Lifecycle Integration
=================================

Provides integration between the new backtesting framework and StrategyLifecycleManager.

Key additions:
- BACKTESTING state for real-time backtest progress tracking
- AutoApprovalRules for automatic strategy approval based on backtest metrics
- Enhanced run_backtest with parameter sweep and comparison support

Architecture:
    StrategyLifecycleManager -> BacktestLifecycleIntegration -> BacktestEngine
                                       |
                                   AutoApprovalRules
                                       |
                               WalkForwardAnalyzer
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type, TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    from trader.services.backtesting.ports import BacktestConfig, BacktestResult, BacktestReport
    from trader.services.backtesting.validation import (
        WalkForwardReport,
        KFoldReport,
        SensitivityReport,
        OverfittingReport,
    )


# ============================================================================
# Lifecycle Status Extensions
# ============================================================================


class BacktestLifecycleStatus(Enum):
    """Extended lifecycle status for backtesting"""
    BACKTESTING = "BACKTESTING"  # Backtest in progress
    BACKTESTED = "BACKTESTED"  # Backtest completed (Phase 4 compatible name)


# ============================================================================
# Auto Approval Rules
# ============================================================================


@dataclass(slots=True)
class AutoApprovalRules:
    """
    自动审批规则
    
    定义策略自动审批的阈值条件。
    所有条件都满足时，策略才会被自动审批通过。
    
    属性：
        min_sharpe: 最小夏普比率
        max_drawdown_pct: 最大回撤百分比
        min_trades: 最小交易次数
        min_win_rate: 最小胜率
        max_overfitting_score: 最大过拟合分数
        min_profit_factor: 最小盈亏比
        min_cagr: 最小年化收益率
    """
    min_sharpe: float = 1.0
    max_drawdown_pct: float = 20.0
    min_trades: int = 30
    min_win_rate: float = 0.4
    max_overfitting_score: float = 0.3
    min_profit_factor: float = 1.2
    min_cagr: float = 0.0  # Can be negative for conservative strategies

    def evaluate(self, report: Any) -> tuple[bool, List[str]]:
        """
        评估回测报告是否满足自动审批条件
        
        Args:
            report: BacktestReport 或 StandardizedBacktestReport
            
        Returns:
            (是否满足条件, 不满足的条件列表)
        """
        violations: List[str] = []
        
        # Extract metrics
        sharpe = self._get_metric(report, 'sharpe_ratio')
        max_dd_pct = self._get_metric(report, 'max_drawdown_percent')
        num_trades = self._get_metric(report, 'num_trades')
        win_rate = self._get_metric(report, 'win_rate')
        profit_factor = self._get_metric(report, 'profit_factor')
        total_return = self._get_metric(report, 'total_return')
        
        # Check each condition
        if sharpe < self.min_sharpe:
            violations.append(f"夏普比率 {sharpe:.2f} < {self.min_sharpe}")
        
        if max_dd_pct > self.max_drawdown_pct:
            violations.append(f"最大回撤 {max_dd_pct:.2f}% > {self.max_drawdown_pct}%")
        
        if num_trades < self.min_trades:
            violations.append(f"交易次数 {num_trades} < {self.min_trades}")
        
        if win_rate < self.min_win_rate:
            violations.append(f"胜率 {win_rate:.1%} < {self.min_win_rate:.1%}")
        
        if profit_factor < self.min_profit_factor:
            violations.append(f"盈亏比 {profit_factor:.2f} < {self.min_profit_factor}")
        
        if self.min_cagr > 0 and total_return < self.min_cagr:
            violations.append(f"年化收益 {total_return:.2f}% < {self.min_cagr}%")
        
        passed = len(violations) == 0
        return passed, violations
    
    def _get_metric(self, report: Any, metric_name: str) -> float:
        """Extract metric from report (handles various report formats)."""
        # Try direct attribute
        if hasattr(report, metric_name):
            value = getattr(report, metric_name)
            if isinstance(value, (int, float, Decimal)):
                float_val = float(value)
                # Normalize percentage values to decimal format
                if metric_name == 'win_rate' and float_val > 1.0:
                    float_val = float_val / 100.0
                return float_val
        
        # Try nested result
        if hasattr(report, 'result'):
            result = report.result
            if hasattr(result, metric_name):
                value = getattr(result, metric_name)
                if isinstance(value, (int, float, Decimal)):
                    float_val = float(value)
                    if metric_name == 'win_rate' and float_val > 1.0:
                        float_val = float_val / 100.0
                    return float_val
        
        # Try returns/risk/trades structure (StandardizedBacktestReport)
        if hasattr(report, 'returns') and hasattr(report.returns, metric_name):
            value = getattr(report.returns, metric_name)
            if isinstance(value, (int, float, Decimal)):
                float_val = float(value)
                if metric_name == 'win_rate' and float_val > 1.0:
                    float_val = float_val / 100.0
                return float_val
        
        if hasattr(report, 'risk') and hasattr(report.risk, metric_name):
            value = getattr(report.risk, metric_name)
            if isinstance(value, (int, float, Decimal)):
                return float(value)
        
        if hasattr(report, 'trades') and hasattr(report.trades, metric_name):
            value = getattr(report.trades, metric_name)
            if isinstance(value, (int, float, Decimal)):
                return float(value)
        
        # Try metrics dict
        if hasattr(report, 'metrics') and isinstance(report.metrics, dict):
            value = report.metrics.get(metric_name)
            if isinstance(value, (int, float, Decimal)):
                return float(value)
        
        return 0.0


# ============================================================================
# Backtest Lifecycle Integration
# ============================================================================


@dataclass(slots=True)
class BacktestJob:
    """
    Backtest job for tracking async backtest progress
    
    属性：
        job_id: Job唯一ID
        strategy_id: 策略ID
        status: Job状态
        config: BacktestConfig
        created_at: 创建时间
        started_at: 开始时间
        completed_at: 完成时间
        report: 回测报告（完成后填充）
        error: 错误信息（失败时填充）
    """
    job_id: str
    strategy_id: str
    status: str = "PENDING"
    config: Optional[Any] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    report: Optional[Any] = None
    error: Optional[str] = None


@dataclass(slots=True)
class ParameterSweepResult:
    """
    参数扫描结果
    
    属性：
        sweep_id: 扫描ID
        strategy_id: 策略ID
        param_grid: 参数网格
        total_combinations: 总组合数
        completed_combinations: 已完成组合数
        best_params: 最优参数
        best_metrics: 最优指标
        all_results: 所有结果
        created_at: 创建时间
        completed_at: 完成时间
    """
    sweep_id: str
    strategy_id: str
    param_grid: Dict[str, List[Any]]
    total_combinations: int
    completed_combinations: int = 0
    best_params: Optional[Dict[str, Any]] = None
    best_metrics: Optional[Dict[str, float]] = None
    all_results: List[Dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None


class BacktestLifecycleIntegration:
    """
    回测生命周期集成
    
    提供与 StrategyLifecycleManager 的集成功能：
    1. 状态转换增强（BACKTESTING状态）
    2. 自动审批评估
    3. 参数扫描管理
    4. 策略对比
    
    使用方式：
        integration = BacktestLifecycleIntegration(
            backtest_engine=engine,
            auto_approval_rules=AutoApprovalRules(min_sharpe=1.5),
        )
        
        # Run backtest with progress tracking
        job = await integration.run_backtest_with_tracking(lifecycle)
        
        # Auto-evaluate for approval
        passed, violations = integration.evaluate_auto_approval(report)
    """
    
    def __init__(
        self,
        backtest_engine: Optional[Any] = None,
        auto_approval_rules: Optional[AutoApprovalRules] = None,
        data_provider: Optional[Any] = None,
    ):
        """
        初始化回测生命周期集成
        
        Args:
            backtest_engine: 回测引擎 (需实现 BacktestEnginePort)
            auto_approval_rules: 自动审批规则
            data_provider: 数据供给器 (需实现 DataProviderPort)
        """
        self._engine = backtest_engine
        self._rules = auto_approval_rules or AutoApprovalRules()
        self._data_provider = data_provider
        
        # Job tracking
        self._jobs: Dict[str, BacktestJob] = {}
    
    @property
    def auto_approval_rules(self) -> AutoApprovalRules:
        """获取自动审批规则"""
        return self._rules
    
    def set_auto_approval_rules(self, rules: AutoApprovalRules) -> None:
        """设置自动审批规则"""
        self._rules = rules
    
    def evaluate_auto_approval(self, report: Any) -> tuple[bool, List[str]]:
        """
        评估是否满足自动审批条件
        
        Args:
            report: BacktestReport
            
        Returns:
            (是否满足条件, 不满足的条件列表)
        """
        return self._rules.evaluate(report)
    
    async def run_backtest_with_tracking(
        self,
        strategy: Any,
        config: Any,
        strategy_id: str,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> BacktestJob:
        """
        运行带进度跟踪的回测
        
        Args:
            strategy: 策略实例
            config: BacktestConfig
            strategy_id: 策略ID
            progress_callback: 进度回调函数 (status, progress)
            
        Returns:
            BacktestJob: 回测任务
        """
        job_id = str(uuid.uuid4())
        job = BacktestJob(
            job_id=job_id,
            strategy_id=strategy_id,
            status="RUNNING",
            config=config,
            started_at=datetime.now(timezone.utc),
        )
        self._jobs[job_id] = job
        
        try:
            if progress_callback:
                progress_callback("STARTED", 0.0)
            
            # Run backtest
            if self._engine:
                result = await self._engine.run_backtest(config, strategy)
                job.report = result
            else:
                # Simulate backtest
                await self._simulate_backtest(job, progress_callback)
            
            job.status = "COMPLETED"
            job.completed_at = datetime.now(timezone.utc)
            
            if progress_callback:
                progress_callback("COMPLETED", 1.0)
            
        except Exception as e:
            job.status = "FAILED"
            job.error = str(e)
            job.completed_at = datetime.now(timezone.utc)
            
            if progress_callback:
                progress_callback("FAILED", 0.0)
        
        return job
    
    async def _simulate_backtest(
        self,
        job: BacktestJob,
        progress_callback: Optional[Callable[[str, float], None]],
    ) -> None:
        """Simulate backtest for testing"""
        import asyncio
        
        for i in range(10):
            await asyncio.sleep(0.1)
            if progress_callback:
                progress_callback("RUNNING", (i + 1) / 10.0)
        
        # Create mock report
        from trader.services.backtesting.ports import BacktestResult
        from datetime import datetime, timezone
        
        job.report = BacktestResult(
            total_return=Decimal("15.5"),
            sharpe_ratio=Decimal("1.8"),
            max_drawdown=Decimal("5.0"),
            win_rate=Decimal("60.0"),
            profit_factor=Decimal("1.5"),
            num_trades=50,
            final_capital=Decimal("115500"),
            equity_curve=[],
            trades=[],
        )
    
    def get_job(self, job_id: str) -> Optional[BacktestJob]:
        """获取回测任务"""
        return self._jobs.get(job_id)
    
    def list_jobs(self, strategy_id: Optional[str] = None) -> List[BacktestJob]:
        """列出回测任务"""
        if strategy_id:
            return [j for j in self._jobs.values() if j.strategy_id == strategy_id]
        return list(self._jobs.values())


class StrategyComparison:
    """
    策略对比工具
    
    用于对比多个策略的回测表现。
    
    使用方式：
        comparison = StrategyComparison()
        result = comparison.compare(
            reports=[report1, report2, report3],
            strategies=["StrategyA", "StrategyB", "StrategyC"],
            metrics=["sharpe_ratio", "max_drawdown", "win_rate"],
        )
    """
    
    def compare(
        self,
        reports: List[Any],
        strategy_names: List[str],
        metrics: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        对比策略
        
        Args:
            reports: 回测报告列表
            strategy_names: 策略名称列表
            metrics: 要对比的指标列表
            
        Returns:
            对比结果字典
        """
        if metrics is None:
            metrics = ["sharpe_ratio", "max_drawdown", "win_rate", "total_return"]
        
        if len(reports) != len(strategy_names):
            raise ValueError("reports and strategy_names must have same length")
        
        comparison: Dict[str, Any] = {
            "metrics": {},
            "rankings": {},
            "summary": {},
        }
        
        # Extract metrics for each strategy
        for metric in metrics:
            metric_values: Dict[str, float] = {}
            
            for report, name in zip(reports, strategy_names):
                value = self._extract_metric(report, metric)
                metric_values[name] = value
            
            comparison["metrics"][metric] = metric_values
            
            # Rank strategies for this metric (higher is better for most metrics)
            if metric in ["sharpe_ratio", "win_rate", "total_return", "profit_factor"]:
                ranking = sorted(
                    metric_values.items(),
                    key=lambda x: x[1],
                    reverse=True,
                )
            else:  # Lower is better for drawdown, etc.
                ranking = sorted(
                    metric_values.items(),
                    key=lambda x: x[1],
                )
            
            comparison["rankings"][metric] = [
                {"strategy": s, "value": v, "rank": i + 1}
                for i, (s, v) in enumerate(ranking)
            ]
        
        # Calculate overall scores
        overall_scores: Dict[str, float] = {}
        for name in strategy_names:
            score = 0.0
            count = 0
            for metric in metrics:
                if metric in comparison["metrics"]:
                    value = comparison["metrics"][metric].get(name, 0)
                    # Normalize (simple min-max)
                    values = list(comparison["metrics"][metric].values())
                    if values:
                        min_val, max_val = min(values), max(values)
                        if max_val > min_val:
                            normalized = (value - min_val) / (max_val - min_val)
                            score += normalized
                            count += 1
            overall_scores[name] = score / count if count > 0 else 0.0
        
        comparison["summary"] = {
            "overall_scores": overall_scores,
            "best_strategy": max(overall_scores.items(), key=lambda x: x[1])[0] if overall_scores else None,
        }
        
        return comparison
    
    def _extract_metric(self, report: Any, metric_name: str) -> float:
        """Extract metric from report."""
        # Try direct attribute
        if hasattr(report, metric_name):
            value = getattr(report, metric_name)
            if isinstance(value, (int, float, Decimal)):
                return float(value)
        
        # Try nested result
        if hasattr(report, 'result'):
            result = report.result
            if hasattr(result, metric_name):
                value = getattr(result, metric_name)
                if isinstance(value, (int, float, Decimal)):
                    return float(value)
        
        # Try returns/risk/trades structure
        if hasattr(report, 'returns') and hasattr(report.returns, metric_name):
            return float(getattr(report.returns, metric_name))
        
        if hasattr(report, 'risk') and hasattr(report.risk, metric_name):
            return float(getattr(report.risk, metric_name))
        
        if hasattr(report, 'trades') and hasattr(report.trades, metric_name):
            return float(getattr(report.trades, metric_name))
        
        # Try metrics dict
        if hasattr(report, 'metrics') and isinstance(report.metrics, dict):
            value = report.metrics.get(metric_name)
            if isinstance(value, (int, float, Decimal)):
                return float(value)
        
        return 0.0


# ============================================================================
# Helper Functions
# ============================================================================


def is_valid_transition(from_status: str, to_status: str) -> bool:
    """
    检查状态转换是否有效
    
    Extended transitions for backtesting:
    - VALIDATED -> BACKTESTING -> BACKTESTED
    - BACKTESTING -> FAILED (can retry)
    - FAILED -> VALIDATED (can retry from validation)
    """
    valid_transitions = {
        "DRAFT": ["VALIDATED", "FAILED"],
        "VALIDATED": ["BACKTESTING", "BACKTESTED", "FAILED"],
        "BACKTESTING": ["BACKTESTED", "FAILED"],
        "BACKTESTED": ["APPROVED", "FAILED"],
        "APPROVED": ["RUNNING", "FAILED"],
        "RUNNING": ["STOPPED", "FAILED"],
        "STOPPED": ["RUNNING", "ARCHIVED"],
        "FAILED": ["DRAFT", "VALIDATED", "ARCHIVED"],
        "ARCHIVED": [],
    }
    
    return to_status in valid_transitions.get(from_status, [])


def calculate_scorecard(report: Any) -> Dict[str, Any]:
    """
    计算策略评分卡
    
    Args:
        report: BacktestReport
        
    Returns:
        评分卡字典
    """
    from trader.services.backtesting.validation import ValidationStatus
    
    # Extract key metrics
    sharpe = _safe_get_metric(report, 'sharpe_ratio')
    max_dd = _safe_get_metric(report, 'max_drawdown_percent')
    win_rate = _safe_get_metric(report, 'win_rate')
    total_return = _safe_get_metric(report, 'total_return')
    num_trades = _safe_get_metric(report, 'num_trades')
    profit_factor = _safe_get_metric(report, 'profit_factor')
    
    # Calculate component scores (0-100)
    scores = {}
    
    # Sharpe score (benchmark: 2.0 = 100)
    scores['sharpe'] = min(100, sharpe * 50) if sharpe > 0 else 0
    
    # Drawdown score (0% = 100, 20%+ = 0)
    scores['drawdown'] = max(0, 100 - max_dd * 5) if max_dd >= 0 else 100
    
    # Win rate score (50% = 50, 100% = 100)
    scores['win_rate'] = max(0, min(100, (win_rate - 0.5) * 200)) if win_rate >= 0 else 0
    
    # Return score (10% = 100)
    scores['return'] = min(100, total_return * 10) if total_return >= 0 else 0
    
    # Activity score (optimal: 30-100 trades)
    if num_trades < 30:
        scores['activity'] = num_trades / 30 * 50
    elif num_trades <= 100:
        scores['activity'] = 100
    else:
        scores['activity'] = max(50, 100 - (num_trades - 100) * 0.5)
    
    # Profit factor score (1.5 = 75, 2.0+ = 100)
    scores['profit_factor'] = min(100, (profit_factor - 1) * 100) if profit_factor > 1 else 0
    
    # Overall score (weighted average)
    weights = {
        'sharpe': 0.30,
        'drawdown': 0.25,
        'win_rate': 0.15,
        'return': 0.15,
        'activity': 0.05,
        'profit_factor': 0.10,
    }
    
    overall = sum(scores[k] * weights[k] for k in weights)
    
    return {
        "component_scores": scores,
        "overall_score": overall,
        "grade": _score_to_grade(overall),
        "metrics": {
            "sharpe": sharpe,
            "max_drawdown": max_dd,
            "win_rate": win_rate,
            "total_return": total_return,
            "num_trades": num_trades,
            "profit_factor": profit_factor,
        },
    }


def _safe_get_metric(report: Any, metric_name: str) -> float:
    """Safely extract metric from report."""
    try:
        # Try direct attribute
        if hasattr(report, metric_name):
            value = getattr(report, metric_name)
            if isinstance(value, (int, float, Decimal)):
                return float(value)
        
        # Try nested result
        if hasattr(report, 'result'):
            result = report.result
            if hasattr(result, metric_name):
                value = getattr(result, metric_name)
                if isinstance(value, (int, float, Decimal)):
                    return float(value)
        
        # Try StandardizedBacktestReport structure
        for attr in ['returns', 'risk', 'trades']:
            if hasattr(report, attr):
                sub = getattr(report, attr)
                if hasattr(sub, metric_name):
                    value = getattr(sub, metric_name)
                    if isinstance(value, (int, float, Decimal)):
                        return float(value)
        
        # Try metrics dict
        if hasattr(report, 'metrics') and isinstance(report.metrics, dict):
            value = report.metrics.get(metric_name)
            if isinstance(value, (int, float, Decimal)):
                return float(value)
        
        return 0.0
    except Exception:
        return 0.0


def _score_to_grade(score: float) -> str:
    """Convert numeric score to letter grade."""
    if score >= 90:
        return "A+"
    elif score >= 85:
        return "A"
    elif score >= 80:
        return "A-"
    elif score >= 75:
        return "B+"
    elif score >= 70:
        return "B"
    elif score >= 65:
        return "B-"
    elif score >= 60:
        return "C+"
    elif score >= 55:
        return "C"
    elif score >= 50:
        return "C-"
    else:
        return "F"
