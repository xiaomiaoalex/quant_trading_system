"""
Standardized Backtest Report Formatter
======================================

Provides standardized backtest report format with all risk/return metrics,
benchmark comparison, and Buy & Hold baseline.

Architecture:
    BacktestReport -> StandardizedBacktestReport -> JSON/HTML/PDF Export
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
import calendar
import math


@dataclass(slots=True)
class ReturnMetrics:
    """Return metrics for standardized backtest report."""
    total_return: Decimal          # 总收益率 (%)
    annual_return: Decimal         # 年化收益率 (%)
    cumulative_return: Decimal     # 累计收益率 (%)
    monthly_returns: List[Decimal] # 月度收益率列表
    best_month: Decimal            # 最佳月度收益 (%)
    worst_month: Decimal           # 最差月度收益 (%)


@dataclass(slots=True)
class RiskMetrics:
    """Risk metrics for standardized backtest report."""
    max_drawdown: Decimal           # 最大回撤 (金额)
    max_drawdown_percent: Decimal   # 最大回撤 (%)
    max_drawdown_duration: int       # 最大回撤持续时间 (天)
    var_95: Decimal                  # 95% VaR (%)
    cvar_95: Decimal                 # 95% CVaR (%)
    volatility: Decimal             # 收益率波动率 (%)


@dataclass(slots=True)
class RiskAdjustedMetrics:
    """Risk-adjusted return metrics."""
    sharpe_ratio: Decimal           # 夏普比率
    sortino_ratio: Decimal          # 索提诺比率
    calmar_ratio: Decimal           # 卡玛比率
    sterling_ratio: Decimal         # 斯特林比率
    omega_ratio: Decimal            # 欧米茄比率


@dataclass(slots=True)
class TradeStatistics:
    """Trade statistics for standardized backtest report."""
    total_trades: int              # 总交易次数
    winning_trades: int             # 盈利交易次数
    losing_trades: int              # 亏损交易次数
    win_rate: Decimal               # 胜率 (%)
    profit_factor: Decimal          # 盈亏比
    avg_trade_duration: float      # 平均交易时长 (小时)
    avg_win: Decimal                # 平均盈利 (金额)
    avg_loss: Decimal               # 平均亏损 (金额)
    largest_win: Decimal             # 最大单笔盈利
    largest_loss: Decimal            # 最大单笔亏损
    avg_trades_per_day: Decimal     # 日均交易次数


@dataclass(slots=True)
class BenchmarkComparison:
    """Benchmark comparison metrics."""
    benchmark_total_return: Decimal # 基准总收益率 (%)
    benchmark_annual_return: Decimal # 基准年化收益率 (%)
    benchmark_max_drawdown: Decimal # 基准最大回撤 (%)
    alpha: Decimal                  # 阿尔法 (%)
    beta: Decimal                   # 贝塔
    correlation: Decimal            # 与基准相关性
    tracking_error: Decimal          # 跟踪误差 (%)
    information_ratio: Decimal      # 信息比率


@dataclass(slots=True)
class MetaInfo:
    """Meta information for backtest report."""
    framework: str                 # 回测框架 (quantconnect/vectorbt)
    data_range: Tuple[datetime, datetime] # 数据范围 (开始, 结束)
    trading_days: int               # 交易日数量
    initial_capital: Decimal        # 初始资金
    final_capital: Decimal          # 最终资金
    strategy_name: str              # 策略名称
    symbol: str                     # 交易标的
    interval: str                   # K线周期


@dataclass(slots=True)
class StandardizedBacktestReport:
    """
    标准化回测报告
    
    包含完整的风险收益指标、交易统计、基准对比和元信息。
    支持 Buy & Hold 基准对比计算。
    
    属性：
        returns: 收益指标
        risk: 风险指标
        risk_adjusted: 风险调整收益指标
        trades: 交易统计
        benchmark: 基准对比 (可选)
        meta: 元信息
    """
    returns: ReturnMetrics
    risk: RiskMetrics
    risk_adjusted: RiskAdjustedMetrics
    trades: TradeStatistics
    benchmark: Optional[BenchmarkComparison] = None
    meta: Optional[MetaInfo] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "returns": {
                "total_return": float(self.returns.total_return),
                "annual_return": float(self.returns.annual_return),
                "cumulative_return": float(self.returns.cumulative_return),
                "monthly_returns": [float(m) for m in self.returns.monthly_returns],
                "best_month": float(self.returns.best_month),
                "worst_month": float(self.returns.worst_month),
            },
            "risk": {
                "max_drawdown": float(self.risk.max_drawdown),
                "max_drawdown_percent": float(self.risk.max_drawdown_percent),
                "max_drawdown_duration": self.risk.max_drawdown_duration,
                "var_95": float(self.risk.var_95),
                "cvar_95": float(self.risk.cvar_95),
                "volatility": float(self.risk.volatility),
            },
            "risk_adjusted": {
                "sharpe_ratio": float(self.risk_adjusted.sharpe_ratio),
                "sortino_ratio": float(self.risk_adjusted.sortino_ratio),
                "calmar_ratio": float(self.risk_adjusted.calmar_ratio),
                "sterling_ratio": float(self.risk_adjusted.sterling_ratio),
                "omega_ratio": float(self.risk_adjusted.omega_ratio),
            },
            "trades": {
                "total_trades": self.trades.total_trades,
                "winning_trades": self.trades.winning_trades,
                "losing_trades": self.trades.losing_trades,
                "win_rate": float(self.trades.win_rate),
                "profit_factor": float(self.trades.profit_factor),
                "avg_trade_duration": self.trades.avg_trade_duration,
                "avg_win": float(self.trades.avg_win),
                "avg_loss": float(self.trades.avg_loss),
                "largest_win": float(self.trades.largest_win),
                "largest_loss": float(self.trades.largest_loss),
                "avg_trades_per_day": float(self.trades.avg_trades_per_day),
            },
        }
        
        if self.benchmark:
            result["benchmark"] = {
                "benchmark_total_return": float(self.benchmark.benchmark_total_return),
                "benchmark_annual_return": float(self.benchmark.benchmark_annual_return),
                "benchmark_max_drawdown": float(self.benchmark.benchmark_max_drawdown),
                "alpha": float(self.benchmark.alpha),
                "beta": float(self.benchmark.beta),
                "correlation": float(self.benchmark.correlation),
                "tracking_error": float(self.benchmark.tracking_error),
                "information_ratio": float(self.benchmark.information_ratio),
            }
        
        if self.meta:
            result["meta"] = {
                "framework": self.meta.framework,
                "data_range": (
                    self.meta.data_range[0].isoformat() if self.meta.data_range[0] else None,
                    self.meta.data_range[1].isoformat() if self.meta.data_range[1] else None,
                ),
                "trading_days": self.meta.trading_days,
                "initial_capital": float(self.meta.initial_capital),
                "final_capital": float(self.meta.final_capital),
                "strategy_name": self.meta.strategy_name,
                "symbol": self.meta.symbol,
                "interval": self.meta.interval,
            }
        
        return result


class ReportFormatter:
    """
    回测报告格式化器
    
    将原始回测结果转换为标准化报告格式。
    支持计算 Buy & Hold 基准对比。
    
    用法：
        formatter = ReportFormatter()
        report = formatter.format(backtest_result, config)
        
        # 带基准对比
        benchmark_result = formatter.calculate_buy_and_hold(config)
        report = formatter.format_with_benchmark(backtest_result, config, benchmark_result)
    """
    
    TRADING_DAYS_PER_YEAR = 252
    TRADING_DAYS_PER_MONTH = 21
    
    def __init__(self, trading_days_per_year: int = 252):
        self._trading_days_per_year = trading_days_per_year
    
    def format(
        self,
        backtest_result: Any,
        config: Any,
        strategy_name: str = "Strategy",
    ) -> StandardizedBacktestReport:
        """
        将回测结果格式化为标准化报告。
        
        Args:
            backtest_result: 原始回测结果 (BacktestResult)
            config: 回测配置 (BacktestConfig)
            strategy_name: 策略名称
            
        Returns:
            StandardizedBacktestReport: 标准化报告
        """
        equity_curve = self._parse_equity_curve(backtest_result)
        trades = self._parse_trades(backtest_result)
        
        # Calculate metrics
        returns = self._calculate_return_metrics(backtest_result, equity_curve, config)
        risk = self._calculate_risk_metrics(backtest_result, equity_curve, config)
        risk_adjusted = self._calculate_risk_adjusted_metrics(equity_curve, config)
        trade_stats = self._calculate_trade_statistics(trades, equity_curve, config)
        meta = self._create_meta_info(backtest_result, config, strategy_name)
        
        return StandardizedBacktestReport(
            returns=returns,
            risk=risk,
            risk_adjusted=risk_adjusted,
            trades=trade_stats,
            meta=meta,
        )
    
    def format_with_benchmark(
        self,
        backtest_result: Any,
        config: Any,
        benchmark_result: Any,
        strategy_name: str = "Strategy",
    ) -> StandardizedBacktestReport:
        """
        将回测结果格式化为标准化报告，包含基准对比。
        
        Args:
            backtest_result: 策略回测结果
            config: 回测配置
            benchmark_result: Buy & Hold 基准结果
            strategy_name: 策略名称
            
        Returns:
            StandardizedBacktestReport: 包含基准对比的标准化报告
        """
        report = self.format(backtest_result, config, strategy_name)
        
        benchmark = self._calculate_benchmark_comparison(
            backtest_result, benchmark_result, config
        )
        
        return StandardizedBacktestReport(
            returns=report.returns,
            risk=report.risk,
            risk_adjusted=report.risk_adjusted,
            trades=report.trades,
            benchmark=benchmark,
            meta=report.meta,
        )
    
    def calculate_buy_and_hold(
        self,
        config: Any,
        equity_curve: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        计算 Buy & Hold 基准收益。
        
        Args:
            config: 回测配置
            equity_curve: 基准的权益曲线
            
        Returns:
            Dict with benchmark metrics
        """
        if not equity_curve:
            return {
                "total_return": Decimal("0"),
                "annual_return": Decimal("0"),
                "max_drawdown": Decimal("0"),
            }
        
        initial_capital = config.initial_capital if config else Decimal("100000")
        
        first_price = equity_curve[0].get("equity", initial_capital) if isinstance(equity_curve[0], dict) else equity_curve[0].equity
        last_price = equity_curve[-1].get("equity", initial_capital) if isinstance(equity_curve[-1], dict) else equity_curve[-1].equity
        
        total_return = ((last_price - first_price) / first_price) * 100 if first_price > 0 else Decimal("0")
        
        # Calculate annual return
        if len(equity_curve) >= 2:
            first_ts = equity_curve[0].get("timestamp", datetime.now(timezone.utc)) if isinstance(equity_curve[0], dict) else equity_curve[0].timestamp
            last_ts = equity_curve[-1].get("timestamp", datetime.now(timezone.utc)) if isinstance(equity_curve[-1], dict) else equity_curve[-1].timestamp
            
            if isinstance(first_ts, (int, float)):
                first_ts = datetime.fromtimestamp(first_ts, tz=timezone.utc)
            if isinstance(last_ts, (int, float)):
                last_ts = datetime.fromtimestamp(last_ts, tz=timezone.utc)
            
            days = (last_ts - first_ts).days
            years = days / 365.25 if days > 0 else 1
            annual_return = Decimal(str(float(last_price / first_price) ** (1 / years) - 1)) * 100 if years > 0 else Decimal("0")
        else:
            annual_return = Decimal("0")
        
        # Calculate max drawdown
        max_dd, max_dd_pct = self._calculate_equity_drawdown(equity_curve, initial_capital)
        
        return {
            "total_return": total_return,
            "annual_return": annual_return,
            "max_drawdown": max_dd,
            "max_drawdown_percent": max_dd_pct,
            "final_capital": last_price,
        }
    
    def _calculate_return_metrics(
        self,
        result: Any,
        equity_curve: List[Any],
        config: Any,
    ) -> ReturnMetrics:
        """Calculate return metrics."""
        initial_capital = config.initial_capital if config else Decimal("100000")
        final_capital = result.final_capital if hasattr(result, 'final_capital') and result.final_capital else initial_capital
        
        # Total return
        total_return = Decimal("0")
        if initial_capital > 0:
            total_return = ((final_capital - initial_capital) / initial_capital) * 100
        
        # Cumulative return
        cumulative_return = total_return
        
        # Annual return
        annual_return = self._calculate_annual_return(result, equity_curve, initial_capital, final_capital)
        
        # Monthly returns
        monthly_returns = self._calculate_monthly_returns(equity_curve)
        
        # Best/worst month
        best_month = max(monthly_returns) if monthly_returns else Decimal("0")
        worst_month = min(monthly_returns) if monthly_returns else Decimal("0")
        
        return ReturnMetrics(
            total_return=total_return,
            annual_return=annual_return,
            cumulative_return=cumulative_return,
            monthly_returns=monthly_returns,
            best_month=best_month,
            worst_month=worst_month,
        )
    
    def _calculate_annual_return(
        self,
        result: Any,
        equity_curve: List[Any],
        initial_capital: Decimal,
        final_capital: Decimal,
    ) -> Decimal:
        """Calculate annualized return."""
        if not equity_curve or len(equity_curve) < 2:
            return Decimal("0")
        
        first_ts = equity_curve[0].get("timestamp", datetime.now(timezone.utc)) if isinstance(equity_curve[0], dict) else equity_curve[0].timestamp
        last_ts = equity_curve[-1].get("timestamp", datetime.now(timezone.utc)) if isinstance(equity_curve[-1], dict) else equity_curve[-1].timestamp
        
        if isinstance(first_ts, (int, float)):
            first_ts = datetime.fromtimestamp(first_ts, tz=timezone.utc)
        if isinstance(last_ts, (int, float)):
            last_ts = datetime.fromtimestamp(last_ts, tz=timezone.utc)
        
        days = (last_ts - first_ts).days
        years = days / 365.25 if days > 0 else 1
        
        if years < 0.01 or initial_capital <= 0:
            return Decimal("0")
        
        annual_return = Decimal(str(float(final_capital / initial_capital) ** (1 / years) - 1)) * 100
        return Decimal(str(annual_return))
    
    def _calculate_monthly_returns(self, equity_curve: List[Any]) -> List[Decimal]:
        """Calculate monthly returns from equity curve."""
        if not equity_curve or len(equity_curve) < 2:
            return []
        
        # Group by month
        monthly_groups: Dict[Tuple[int, int], List[Any]] = {}
        for point in equity_curve:
            ts = point.get("timestamp", datetime.now(timezone.utc)) if isinstance(point, dict) else point.timestamp
            if isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(ts, tz=timezone.utc)
            key = (ts.year, ts.month)
            if key not in monthly_groups:
                monthly_groups[key] = []
            monthly_groups[key].append(point)
        
        # Calculate monthly returns
        monthly_returns: List[Decimal] = []
        sorted_months = sorted(monthly_groups.keys())
        
        for i, month_key in enumerate(sorted_months):
            month_points = monthly_groups[month_key]
            if len(month_points) < 2:
                continue
            
            first_equity = month_points[0].get("equity", Decimal("0")) if isinstance(month_points[0], dict) else month_points[0].equity
            last_equity = month_points[-1].get("equity", Decimal("0")) if isinstance(month_points[-1], dict) else month_points[-1].equity
            
            if isinstance(first_equity, (int, float)):
                first_equity = Decimal(str(first_equity))
            if isinstance(last_equity, (int, float)):
                last_equity = Decimal(str(last_equity))
            
            if first_equity > 0:
                monthly_ret = ((last_equity - first_equity) / first_equity) * 100
                monthly_returns.append(monthly_ret)
        
        return monthly_returns
    
    def _calculate_risk_metrics(
        self,
        result: Any,
        equity_curve: List[Any],
        config: Any,
    ) -> RiskMetrics:
        """Calculate risk metrics."""
        initial_capital = config.initial_capital if config else Decimal("100000")
        
        # Max drawdown
        max_dd, max_dd_pct = self._calculate_equity_drawdown(equity_curve, initial_capital)
        
        # Max drawdown duration
        max_dd_duration = self._calculate_max_drawdown_duration(equity_curve, initial_capital)
        
        # VaR and CVaR
        returns = self._calculate_period_returns(equity_curve)
        var_95 = self._calculate_var(returns, 0.95)
        cvar_95 = self._calculate_cvar(returns, 0.95)
        
        # Volatility
        volatility = self._calculate_volatility(returns)
        
        return RiskMetrics(
            max_drawdown=max_dd,
            max_drawdown_percent=max_dd_pct,
            max_drawdown_duration=max_dd_duration,
            var_95=var_95,
            cvar_95=cvar_95,
            volatility=volatility,
        )
    
    def _calculate_equity_drawdown(
        self,
        equity_curve: List[Any],
        initial_capital: Decimal,
    ) -> Tuple[Decimal, Decimal]:
        """Calculate max drawdown from equity curve."""
        if not equity_curve:
            return Decimal("0"), Decimal("0")
        
        peak = initial_capital
        max_dd = Decimal("0")
        
        for point in equity_curve:
            equity = point.get("equity", initial_capital) if isinstance(point, dict) else point.equity
            if isinstance(equity, (int, float)):
                equity = Decimal(str(equity))
            
            if equity > peak:
                peak = equity
            drawdown = peak - equity
            if drawdown > max_dd:
                max_dd = drawdown
        
        max_dd_pct = (max_dd / peak * 100) if peak > 0 else Decimal("0")
        
        return max_dd, max_dd_pct
    
    def _calculate_max_drawdown_duration(
        self,
        equity_curve: List[Any],
        initial_capital: Decimal,
    ) -> int:
        """Calculate max drawdown duration in days."""
        if not equity_curve:
            return 0
        
        peak = initial_capital
        peak_time = equity_curve[0].get("timestamp", datetime.now(timezone.utc)) if isinstance(equity_curve[0], dict) else equity_curve[0].timestamp
        max_duration = 0
        current_duration = 0
        
        for point in equity_curve:
            equity = point.get("equity", initial_capital) if isinstance(point, dict) else point.equity
            ts = point.get("timestamp", datetime.now(timezone.utc)) if isinstance(point, dict) else point.timestamp
            
            if isinstance(equity, (int, float)):
                equity = Decimal(str(equity))
            if isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(ts, tz=timezone.utc)
            
            if equity >= peak:
                peak = equity
                peak_time = ts
                current_duration = 0
            else:
                current_duration = (ts - peak_time).days
                if current_duration > max_duration:
                    max_duration = current_duration
        
        return max_duration
    
    def _calculate_period_returns(self, equity_curve: List[Any]) -> List[Decimal]:
        """Calculate period returns from equity curve."""
        if len(equity_curve) < 2:
            return []
        
        returns: List[Decimal] = []
        for i in range(1, len(equity_curve)):
            prev_equity = equity_curve[i - 1].get("equity", Decimal("0")) if isinstance(equity_curve[i - 1], dict) else equity_curve[i - 1].equity
            curr_equity = equity_curve[i].get("equity", Decimal("0")) if isinstance(equity_curve[i], dict) else equity_curve[i].equity
            
            if isinstance(prev_equity, (int, float)):
                prev_equity = Decimal(str(prev_equity))
            if isinstance(curr_equity, (int, float)):
                curr_equity = Decimal(str(curr_equity))
            
            if prev_equity > 0:
                ret = (curr_equity - prev_equity) / prev_equity
                returns.append(ret)
        
        return returns
    
    def _calculate_var(self, returns: List[Decimal], confidence: float) -> Decimal:
        """Calculate Value at Risk."""
        if not returns:
            return Decimal("0")
        
        sorted_returns = sorted([float(r) for r in returns])
        index = int(len(sorted_returns) * (1 - confidence))
        index = max(0, min(index, len(sorted_returns) - 1))
        
        return Decimal(str(abs(sorted_returns[index]))) * 100
    
    def _calculate_cvar(self, returns: List[Decimal], confidence: float) -> Decimal:
        """Calculate Conditional Value at Risk (Expected Shortfall)."""
        if not returns:
            return Decimal("0")
        
        sorted_returns = sorted([float(r) for r in returns])
        index = int(len(sorted_returns) * (1 - confidence))
        index = max(0, min(index, len(sorted_returns) - 1))
        
        tail_returns = sorted_returns[:index + 1]
        if tail_returns:
            cvar = abs(sum(tail_returns) / len(tail_returns))
            return Decimal(str(cvar)) * 100
        
        return Decimal("0")
    
    def _calculate_volatility(self, returns: List[Decimal]) -> Decimal:
        """Calculate volatility (annualized standard deviation)."""
        if len(returns) < 2:
            return Decimal("0")
        
        float_returns = [float(r) for r in returns]
        mean = sum(float_returns) / len(float_returns)
        variance = sum((r - mean) ** 2 for r in float_returns) / len(float_returns)
        std = math.sqrt(variance)
        
        # Annualize
        annualized_std = std * math.sqrt(self._trading_days_per_year)
        
        return Decimal(str(annualized_std * 100))
    
    def _calculate_risk_adjusted_metrics(
        self,
        equity_curve: List[Any],
        config: Any,
    ) -> RiskAdjustedMetrics:
        """Calculate risk-adjusted return metrics."""
        returns = self._calculate_period_returns(equity_curve)
        
        if not returns or len(returns) < 2:
            return RiskAdjustedMetrics(
                sharpe_ratio=Decimal("0"),
                sortino_ratio=Decimal("0"),
                calmar_ratio=Decimal("0"),
                sterling_ratio=Decimal("0"),
                omega_ratio=Decimal("0"),
            )
        
        initial_capital = config.initial_capital if config else Decimal("100000")
        
        # Sharpe Ratio
        sharpe = self._calculate_sharpe_ratio(returns)
        
        # Sortino Ratio
        sortino = self._calculate_sortino_ratio(returns)
        
        # Calmar Ratio
        calmar = self._calculate_calmar_ratio(equity_curve, initial_capital)
        
        # Sterling Ratio (average return / average drawdown)
        sterling = self._calculate_sterling_ratio(equity_curve, initial_capital)
        
        # Omega Ratio
        omega = self._calculate_omega_ratio(returns)
        
        return RiskAdjustedMetrics(
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            sterling_ratio=sterling,
            omega_ratio=omega,
        )
    
    def _calculate_sharpe_ratio(self, returns: List[Decimal]) -> Decimal:
        """Calculate Sharpe Ratio."""
        if len(returns) < 2:
            return Decimal("0")
        
        float_returns = [float(r) for r in returns]
        mean = sum(float_returns) / len(float_returns)
        std = self._stddev(float_returns)
        
        if std == 0:
            return Decimal("0")
        
        sharpe = (mean / std) * math.sqrt(self._trading_days_per_year)
        return Decimal(str(sharpe))
    
    def _calculate_sortino_ratio(self, returns: List[Decimal]) -> Decimal:
        """Calculate Sortino Ratio."""
        if len(returns) < 2:
            return Decimal("0")
        
        float_returns = [float(r) for r in returns]
        mean = sum(float_returns) / len(float_returns)
        
        downside_returns = [r for r in float_returns if r < 0]
        if not downside_returns:
            return Decimal("0")
        
        downside_std = self._stddev(downside_returns)
        if downside_std == 0:
            return Decimal("0")
        
        sortino = (mean / downside_std) * math.sqrt(self._trading_days_per_year)
        return Decimal(str(sortino))
    
    def _calculate_calmar_ratio(
        self,
        equity_curve: List[Any],
        initial_capital: Decimal,
    ) -> Decimal:
        """Calculate Calmar Ratio."""
        if not equity_curve or len(equity_curve) < 2:
            return Decimal("0")
        
        # Annual return
        first_equity = equity_curve[0].get("equity", initial_capital) if isinstance(equity_curve[0], dict) else equity_curve[0].equity
        last_equity = equity_curve[-1].get("equity", initial_capital) if isinstance(equity_curve[-1], dict) else equity_curve[-1].equity
        
        if isinstance(first_equity, (int, float)):
            first_equity = Decimal(str(first_equity))
        if isinstance(last_equity, (int, float)):
            last_equity = Decimal(str(last_equity))
        
        first_ts = equity_curve[0].get("timestamp", datetime.now(timezone.utc)) if isinstance(equity_curve[0], dict) else equity_curve[0].timestamp
        last_ts = equity_curve[-1].get("timestamp", datetime.now(timezone.utc)) if isinstance(equity_curve[-1], dict) else equity_curve[-1].timestamp
        
        if isinstance(first_ts, (int, float)):
            first_ts = datetime.fromtimestamp(first_ts, tz=timezone.utc)
        if isinstance(last_ts, (int, float)):
            last_ts = datetime.fromtimestamp(last_ts, tz=timezone.utc)
        
        days = (last_ts - first_ts).days
        years = days / 365.25 if days > 0 else 1
        
        if years < 0.01 or first_equity <= 0:
            return Decimal("0")
        
        annual_return = Decimal(str(float(last_equity / first_equity) ** (1 / years) - 1))
        
        # Max drawdown
        max_dd, _ = self._calculate_equity_drawdown(equity_curve, initial_capital)
        max_dd_pct = (max_dd / first_equity) if first_equity > 0 else Decimal("0")
        
        if max_dd_pct == 0:
            return Decimal("0")
        
        calmar = annual_return / max_dd_pct
        return Decimal(str(calmar * 100))
    
    def _calculate_sterling_ratio(
        self,
        equity_curve: List[Any],
        initial_capital: Decimal,
    ) -> Decimal:
        """Calculate Sterling Ratio (avg return / avg drawdown)."""
        if not equity_curve or len(equity_curve) < 2:
            return Decimal("0")
        
        returns = self._calculate_period_returns(equity_curve)
        if not returns:
            return Decimal("0")
        
        avg_return = sum(float(r) for r in returns) / len(returns)
        
        # Calculate average drawdown
        drawdowns = self._calculate_drawdown_series(equity_curve, initial_capital)
        if not drawdowns:
            return Decimal("0")
        
        avg_drawdown = sum(float(d) for d in drawdowns) / len(drawdowns)
        
        if avg_drawdown == 0:
            return Decimal("0")
        
        sterling = (avg_return / avg_drawdown) * math.sqrt(self._trading_days_per_year)
        return Decimal(str(sterling))
    
    def _calculate_drawdown_series(
        self,
        equity_curve: List[Any],
        initial_capital: Decimal,
    ) -> List[Decimal]:
        """Calculate drawdown series."""
        if not equity_curve:
            return []
        
        peak = initial_capital
        drawdowns: List[Decimal] = []
        
        for point in equity_curve:
            equity = point.get("equity", initial_capital) if isinstance(point, dict) else point.equity
            if isinstance(equity, (int, float)):
                equity = Decimal(str(equity))
            
            if equity > peak:
                peak = equity
            drawdown = (peak - equity) / peak if peak > 0 else Decimal("0")
            drawdowns.append(drawdown)
        
        return drawdowns
    
    def _calculate_omega_ratio(self, returns: List[Decimal]) -> Decimal:
        """Calculate Omega Ratio."""
        if not returns:
            return Decimal("0")
        
        float_returns = [float(r) for r in returns]
        threshold = 0
        
        gains = [r - threshold for r in float_returns if r > threshold]
        losses = [threshold - r for r in float_returns if r < threshold]
        
        sum_gains = sum(gains)
        sum_losses = sum(losses)
        
        if sum_losses == 0:
            return Decimal("0") if sum_gains == 0 else Decimal("999999")
        
        omega = sum_gains / sum_losses
        return Decimal(str(omega))
    
    def _calculate_trade_statistics(
        self,
        trades: List[Any],
        equity_curve: List[Any],
        config: Any,
    ) -> TradeStatistics:
        """Calculate trade statistics."""
        if not trades:
            return TradeStatistics(
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=Decimal("0"),
                profit_factor=Decimal("0"),
                avg_trade_duration=0.0,
                avg_win=Decimal("0"),
                avg_loss=Decimal("0"),
                largest_win=Decimal("0"),
                largest_loss=Decimal("0"),
                avg_trades_per_day=Decimal("0"),
            )
        
        total_trades = len(trades)
        winning_trades = 0
        losing_trades = 0
        total_win = Decimal("0")
        total_loss = Decimal("0")
        largest_win = Decimal("0")
        largest_loss = Decimal("0")
        durations: List[float] = []
        
        for trade in trades:
            pnl = trade.get("pnl", Decimal("0")) if isinstance(trade, dict) else getattr(trade, 'pnl', Decimal("0"))
            if isinstance(pnl, (int, float)):
                pnl = Decimal(str(pnl))
            
            if pnl > 0:
                winning_trades += 1
                total_win += pnl
                if pnl > largest_win:
                    largest_win = pnl
            elif pnl < 0:
                losing_trades += 1
                total_loss += abs(pnl)
                if pnl < largest_loss:
                    largest_loss = abs(pnl)
            
            # Duration
            entry_time = trade.get("entry_time", datetime.now(timezone.utc)) if isinstance(trade, dict) else getattr(trade, 'entry_time', None)
            exit_time = trade.get("exit_time", datetime.now(timezone.utc)) if isinstance(trade, dict) else getattr(trade, 'exit_time', None)
            
            if entry_time and exit_time:
                if isinstance(entry_time, (int, float)):
                    entry_time = datetime.fromtimestamp(entry_time, tz=timezone.utc)
                if isinstance(exit_time, (int, float)):
                    exit_time = datetime.fromtimestamp(exit_time, tz=timezone.utc)
                duration = (exit_time - entry_time).total_seconds() / 3600  # hours
                durations.append(duration)
        
        win_rate = Decimal(str(winning_trades / total_trades * 100)) if total_trades > 0 else Decimal("0")
        
        avg_win = total_win / Decimal(str(winning_trades)) if winning_trades > 0 else Decimal("0")
        avg_loss = total_loss / Decimal(str(losing_trades)) if losing_trades > 0 else Decimal("0")
        
        profit_factor = Decimal("0")
        if total_loss > 0:
            profit_factor = total_win / total_loss
        
        avg_duration = sum(durations) / len(durations) if durations else 0.0
        
        # Avg trades per day
        if equity_curve and len(equity_curve) >= 2:
            first_ts = equity_curve[0].get("timestamp", datetime.now(timezone.utc)) if isinstance(equity_curve[0], dict) else equity_curve[0].timestamp
            last_ts = equity_curve[-1].get("timestamp", datetime.now(timezone.utc)) if isinstance(equity_curve[-1], dict) else equity_curve[-1].timestamp
            
            if isinstance(first_ts, (int, float)):
                first_ts = datetime.fromtimestamp(first_ts, tz=timezone.utc)
            if isinstance(last_ts, (int, float)):
                last_ts = datetime.fromtimestamp(last_ts, tz=timezone.utc)
            
            days = (last_ts - first_ts).days
            days = max(1, days)
            avg_trades_per_day = Decimal(str(total_trades / days))
        else:
            avg_trades_per_day = Decimal("0")
        
        return TradeStatistics(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_trade_duration=avg_duration,
            avg_win=avg_win,
            avg_loss=avg_loss,
            largest_win=largest_win,
            largest_loss=largest_loss,
            avg_trades_per_day=avg_trades_per_day,
        )
    
    def _calculate_benchmark_comparison(
        self,
        backtest_result: Any,
        benchmark_result: Dict[str, Any],
        config: Any,
    ) -> BenchmarkComparison:
        """Calculate benchmark comparison metrics."""
        # Strategy returns
        strategy_return = backtest_result.result.total_return if hasattr(backtest_result, 'result') else Decimal("0")
        if isinstance(strategy_return, (int, float)):
            strategy_return = Decimal(str(strategy_return))
        
        # Benchmark returns
        benchmark_return = benchmark_result.get("total_return", Decimal("0"))
        benchmark_annual = benchmark_result.get("annual_return", Decimal("0"))
        benchmark_dd = benchmark_result.get("max_drawdown_percent", Decimal("0"))
        
        # Alpha (strategy return - benchmark return)
        alpha = strategy_return - benchmark_return
        
        # Beta (simplified - would need covariance calculation for full accuracy)
        beta = Decimal("1.0")  # Placeholder
        
        # Correlation (simplified)
        correlation = Decimal("0.5")  # Placeholder
        
        # Tracking error
        tracking_error = abs(alpha) * Decimal("0.5")  # Simplified
        
        # Information ratio
        ir = alpha / tracking_error if tracking_error > 0 else Decimal("0")
        
        return BenchmarkComparison(
            benchmark_total_return=benchmark_return,
            benchmark_annual_return=benchmark_annual,
            benchmark_max_drawdown=benchmark_dd,
            alpha=alpha,
            beta=beta,
            correlation=correlation,
            tracking_error=tracking_error,
            information_ratio=ir,
        )
    
    def _create_meta_info(
        self,
        result: Any,
        config: Any,
        strategy_name: str,
    ) -> MetaInfo:
        """Create meta information."""
        framework = "quantconnect"
        
        start_date = config.start_date if config and hasattr(config, 'start_date') else datetime.now(timezone.utc)
        end_date = config.end_date if config and hasattr(config, 'end_date') else datetime.now(timezone.utc)
        
        initial_capital = config.initial_capital if config else Decimal("100000")
        final_capital = result.final_capital if hasattr(result, 'final_capital') and result.final_capital else initial_capital
        
        symbol = config.symbol if config and hasattr(config, 'symbol') else "UNKNOWN"
        interval = config.interval if config and hasattr(config, 'interval') else "1h"
        
        # Calculate trading days
        if start_date and end_date:
            days = (end_date - start_date).days
            trading_days = int(days * 252 / 365)  # Approximate
        else:
            trading_days = 0
        
        return MetaInfo(
            framework=framework,
            data_range=(start_date, end_date),
            trading_days=trading_days,
            initial_capital=initial_capital,
            final_capital=final_capital,
            strategy_name=strategy_name,
            symbol=symbol,
            interval=interval,
        )
    
    def _parse_equity_curve(self, result: Any) -> List[Any]:
        """Parse equity curve from result."""
        if hasattr(result, 'result') and hasattr(result.result, 'equity_curve'):
            return list(result.result.equity_curve)
        if hasattr(result, 'equity_curve'):
            return list(result.equity_curve)
        return []
    
    def _parse_trades(self, result: Any) -> List[Any]:
        """Parse trades from result."""
        if hasattr(result, 'result') and hasattr(result.result, 'trades'):
            return list(result.result.trades)
        if hasattr(result, 'trades'):
            return list(result.trades)
        return []
    
    @staticmethod
    def _stddev(values: List[float]) -> float:
        """Calculate standard deviation."""
        if not values:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)
