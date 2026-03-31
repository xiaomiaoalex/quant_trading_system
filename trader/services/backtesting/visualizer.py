"""
Backtest Result Visualizer
==========================

Provides visualization functions for backtest results:
- Equity curve
- Drawdown chart
- Monthly heatmap
- Trade markers
- Returns distribution

Requires: matplotlib, seaborn (optional for styling)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple
import math


# Visualization is optional - matplotlib may not be installed in all environments
try:
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend for server environments
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import matplotlib.patches as mpatches
    import seaborn as sns
    _HAS_MATPLOTLIB = True
except ImportError:
    _HAS_MATPLOTLIB = False


@dataclass(slots=True)
class PlotConfig:
    """Configuration for backtest visualizations."""
    figsize: Tuple[int, int] = (12, 6)
    dpi: int = 100
    style: str = "seaborn-v0_8-darkgrid"
    title_fontsize: int = 14
    label_fontsize: int = 11
    tick_fontsize: int = 9
    color_scheme: Dict[str, str] = None  # Custom color scheme
    
    def __post_init__(self):
        if self.color_scheme is None:
            self.color_scheme = {
                "equity": "#2E86AB",
                "benchmark": "#A23B72",
                "drawdown": "#E94F37",
                "profit": "#44AF69",
                "loss": "#E94F37",
                "grid": "#E5E5E5",
                "text": "#333333",
            }


class BacktestVisualizer:
    """
    回测结果可视化器
    
    提供回测结果的可视化功能：
    - 资金曲线
    - 回撤曲线
    - 月度收益热力图
    - 交易标记图
    - 收益分布图
    
    使用方式：
        viz = BacktestVisualizer()
        fig = viz.plot_equity_curve(report)
        plt.savefig("equity.png")
        
        # 组合图表
        fig = viz.plot_combined(report)
        plt.savefig("combined.png")
    """
    
    def __init__(self, config: Optional[PlotConfig] = None):
        self.config = config or PlotConfig()
        self._check_matplotlib()
    
    def _check_matplotlib(self) -> None:
        """Check if matplotlib is available."""
        if not _HAS_MATPLOTLIB:
            raise ImportError(
                "matplotlib is required for visualization. "
                "Install with: pip install matplotlib"
            )
    
    def _parse_timestamps(self, timestamps: List[Any]) -> List[datetime]:
        """Parse timestamps to datetime objects."""
        result = []
        for ts in timestamps:
            if isinstance(ts, (int, float)):
                result.append(datetime.fromtimestamp(ts, tz=timezone.utc))
            elif isinstance(ts, str):
                result.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
            elif isinstance(ts, datetime):
                result.append(ts)
            else:
                result.append(datetime.now(timezone.utc))
        return result
    
    def _parse_equities(self, equities: List[Any]) -> List[float]:
        """Parse equity values to floats."""
        result = []
        for eq in equities:
            if isinstance(eq, (int, float)):
                result.append(float(eq))
            elif isinstance(eq, Decimal):
                result.append(float(eq))
            elif isinstance(eq, str):
                result.append(float(eq))
            else:
                result.append(0.0)
        return result
    
    def plot_equity_curve(
        self,
        report: Any,
        benchmark: Optional[Dict[str, Any]] = None,
        title: str = "Equity Curve",
        show_cagr: bool = True,
        show_max_drawdown: bool = True,
    ) -> Any:
        """
        绘制资金曲线
        
        Args:
            report: BacktestReport 或 StandardizedBacktestReport
            benchmark: 可选的基准权益曲线数据 {"timestamps": [...], "equities": [...]}
            title: 图表标题
            show_cagr: 是否显示年化收益率
            show_max_drawdown: 是否显示最大回撤
            
        Returns:
            matplotlib Figure 对象
        """
        plt.style.use(self.config.style)
        fig, ax = plt.subplots(figsize=self.config.figsize, dpi=self.config.dpi)
        
        # Extract equity curve
        equity_curve = self._get_equity_curve(report)
        if not equity_curve:
            ax.text(0.5, 0.5, "No equity data available", ha='center', va='center')
            return fig
        
        timestamps = self._parse_timestamps([p.get("timestamp") if isinstance(p, dict) else getattr(p, 'timestamp', None) for p in equity_curve])
        equities = self._parse_equities([p.get("equity") if isinstance(p, dict) else getattr(p, 'equity', 0) for p in equity_curve])
        
        # Plot equity curve
        ax.plot(timestamps, equities, color=self.config.color_scheme["equity"], linewidth=2, label="Strategy")
        
        # Plot benchmark if provided
        if benchmark:
            bench_ts = self._parse_timestamps(benchmark.get("timestamps", []))
            bench_eq = self._parse_equities(benchmark.get("equities", []))
            if bench_ts and bench_eq:
                ax.plot(bench_ts, bench_eq, color=self.config.color_scheme["benchmark"], linewidth=1.5, linestyle="--", label="Buy & Hold")
        
        # Add annotations
        if show_cagr and hasattr(report, 'returns'):
            cagr = getattr(report.returns, 'annual_return', None) or getattr(report, 'result', None) and getattr(report.result, 'total_return', None)
            if cagr is not None:
                if isinstance(cagr, Decimal):
                    cagr = float(cagr)
                ax.annotate(f"CAGR: {cagr:.2f}%", xy=(0.02, 0.95), xycoords="axes fraction",
                           fontsize=self.config.label_fontsize, color=self.config.color_scheme["text"])
        
        if show_max_drawdown and hasattr(report, 'risk'):
            max_dd = getattr(report.risk, 'max_drawdown_percent', None)
            if max_dd is not None:
                if isinstance(max_dd, Decimal):
                    max_dd = float(max_dd)
                ax.annotate(f"Max DD: {max_dd:.2f}%", xy=(0.02, 0.88), xycoords="axes fraction",
                           fontsize=self.config.label_fontsize, color=self.config.color_scheme["drawdown"])
        
        # Formatting
        ax.set_title(title, fontsize=self.config.title_fontsize, pad=20)
        ax.set_xlabel("Date", fontsize=self.config.label_fontsize)
        ax.set_ylabel("Equity", fontsize=self.config.label_fontsize)
        ax.legend(loc="upper left", fontsize=self.config.tick_fontsize)
        ax.grid(True, alpha=0.3)
        
        # Format x-axis dates
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        plt.xticks(rotation=45)
        
        plt.tight_layout()
        return fig
    
    def plot_drawdown(
        self,
        report: Any,
        title: str = "Drawdown",
    ) -> Any:
        """
        绘制回撤曲线
        
        Args:
            report: BacktestReport
            title: 图表标题
            
        Returns:
            matplotlib Figure 对象
        """
        plt.style.use(self.config.style)
        fig, ax = plt.subplots(figsize=self.config.figsize, dpi=self.config.dpi)
        
        equity_curve = self._get_equity_curve(report)
        if not equity_curve:
            ax.text(0.5, 0.5, "No equity data available", ha='center', va='center')
            return fig
        
        timestamps = self._parse_timestamps([p.get("timestamp") if isinstance(p, dict) else getattr(p, 'timestamp', None) for p in equity_curve])
        equities = self._parse_equities([p.get("equity") if isinstance(p, dict) else getattr(p, 'equity', 0) for p in equity_curve])
        
        # Calculate drawdown series
        peak = equities[0]
        drawdowns = []
        for eq in equities:
            if eq > peak:
                peak = eq
            dd = ((peak - eq) / peak * 100) if peak > 0 else 0
            drawdowns.append(-dd)  # Negative for visualization
        
        ax.fill_between(timestamps, drawdowns, 0, color=self.config.color_scheme["drawdown"], alpha=0.5)
        ax.plot(timestamps, drawdowns, color=self.config.color_scheme["drawdown"], linewidth=1.5)
        
        # Formatting
        ax.set_title(title, fontsize=self.config.title_fontsize, pad=20)
        ax.set_xlabel("Date", fontsize=self.config.label_fontsize)
        ax.set_ylabel("Drawdown (%)", fontsize=self.config.label_fontsize)
        ax.grid(True, alpha=0.3)
        
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        plt.xticks(rotation=45)
        
        plt.tight_layout()
        return fig
    
    def plot_monthly_heatmap(
        self,
        report: Any,
        title: str = "Monthly Returns Heatmap",
    ) -> Any:
        """
        绘制月度收益热力图
        
        Args:
            report: BacktestReport
            title: 图表标题
            
        Returns:
            matplotlib Figure 对象
        """
        plt.style.use(self.config.style)
        
        monthly_returns = self._get_monthly_returns(report)
        if not monthly_returns:
            # Create empty figure with message
            fig, ax = plt.subplots(figsize=self.config.figsize, dpi=self.config.dpi)
            ax.text(0.5, 0.5, "No monthly return data available", ha='center', va='center')
            return fig
        
        # Create monthly returns matrix (12 months x years)
        returns_by_year_month: Dict[int, Dict[int, float]] = {}
        for ret_dict in monthly_returns:
            if isinstance(ret_dict, dict):
                year = ret_dict.get("year")
                month = ret_dict.get("month")
                ret = ret_dict.get("return", 0)
            else:
                continue
            if year and month:
                if year not in returns_by_year_month:
                    returns_by_year_month[year] = {}
                returns_by_year_month[year][month] = float(ret) if isinstance(ret, Decimal) else ret
        
        if not returns_by_year_month:
            fig, ax = plt.subplots(figsize=self.config.figsize, dpi=self.config.dpi)
            ax.text(0.5, 0.5, "No monthly return data available", ha='center', va='center')
            return fig
        
        years = sorted(returns_by_year_month.keys())
        if len(years) == 0:
            fig, ax = plt.subplots(figsize=self.config.figsize, dpi=self.config.dpi)
            ax.text(0.5, 0.5, "No monthly return data available", ha='center', va='center')
            return fig
        
        # Build matrix (months as rows, years as columns)
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        data_matrix = []
        for month_idx in range(1, 13):
            row = []
            for year in years:
                ret = returns_by_year_month[year].get(month_idx, 0)
                row.append(ret)
            data_matrix.append(row)
        
        fig, ax = plt.subplots(figsize=(max(8, len(years) * 1.5), 8), dpi=self.config.dpi)
        
        # Create heatmap
        im = ax.imshow(data_matrix, cmap='RdYlGn', aspect='auto', vmin=-20, vmax=20)
        
        # Set ticks
        ax.set_xticks(range(len(years)))
        ax.set_xticklabels(years, fontsize=self.config.tick_fontsize)
        ax.set_yticks(range(12))
        ax.set_yticklabels(month_names, fontsize=self.config.tick_fontsize)
        
        # Add colorbar
        cbar = plt.colorbar(im, ax=ax)
        cbar.set_label("Return (%)", fontsize=self.config.label_fontsize)
        
        # Add value annotations
        for i in range(12):
            for j in range(len(years)):
                val = data_matrix[i][j]
                color = 'white' if abs(val) > 10 else 'black'
                ax.text(j, i, f'{val:.1f}', ha='center', va='center', color=color, fontsize=8)
        
        ax.set_title(title, fontsize=self.config.title_fontsize, pad=20)
        ax.set_xlabel("Year", fontsize=self.config.label_fontsize)
        ax.set_ylabel("Month", fontsize=self.config.label_fontsize)
        
        plt.tight_layout()
        return fig
    
    def plot_trade_markers(
        self,
        report: Any,
        title: str = "Trade Markers",
        show_equity: bool = True,
    ) -> Any:
        """
        绘制交易标记图
        
        Args:
            report: BacktestReport
            title: 图表标题
            show_equity: 是否显示权益曲线
            
        Returns:
            matplotlib Figure 对象
        """
        plt.style.use(self.config.style)
        
        equity_curve = self._get_equity_curve(report)
        trades = self._get_trades(report)
        
        if not equity_curve:
            fig, ax = plt.subplots(figsize=self.config.figsize, dpi=self.config.dpi)
            ax.text(0.5, 0.5, "No data available", ha='center', va='center')
            return fig
        
        fig, ax = plt.subplots(figsize=self.config.figsize, dpi=self.config.dpi)
        
        # Plot equity curve if requested
        if show_equity:
            timestamps = self._parse_timestamps([p.get("timestamp") if isinstance(p, dict) else getattr(p, 'timestamp', None) for p in equity_curve])
            equities = self._parse_equities([p.get("equity") if isinstance(p, dict) else getattr(p, 'equity', 0) for p in equity_curve])
            ax.plot(timestamps, equities, color=self.config.color_scheme["equity"], linewidth=1.5, alpha=0.7)
        
        # Plot trade markers
        if trades:
            for trade in trades:
                entry_time = trade.get("entry_time") if isinstance(trade, dict) else getattr(trade, 'entry_time', None)
                exit_time = trade.get("exit_time") if isinstance(trade, dict) else getattr(trade, 'exit_time', None)
                entry_price = trade.get("entry_price", 0) if isinstance(trade, dict) else getattr(trade, 'entry_price', 0)
                exit_price = trade.get("exit_price", 0) if isinstance(trade, dict) else getattr(trade, 'exit_price', 0)
                direction = trade.get("direction", "UNKNOWN") if isinstance(trade, dict) else getattr(trade, 'direction', "UNKNOWN")
                pnl = trade.get("pnl", 0) if isinstance(trade, dict) else getattr(trade, 'pnl', 0)
                
                if isinstance(entry_time, (int, float)):
                    entry_time = datetime.fromtimestamp(entry_time, tz=timezone.utc)
                if isinstance(exit_time, (int, float)):
                    exit_time = datetime.fromtimestamp(exit_time, tz=timezone.utc)
                if isinstance(entry_price, Decimal):
                    entry_price = float(entry_price)
                if isinstance(exit_price, Decimal):
                    exit_price = float(exit_price)
                if isinstance(pnl, Decimal):
                    pnl = float(pnl)
                
                # Entry marker (triangle up)
                if entry_time:
                    ax.scatter([entry_time], [entry_price], marker='^', color='green' if direction.upper() == "BUY" else 'red', s=100, zorder=5)
                
                # Exit marker (triangle down)
                if exit_time:
                    color = self.config.color_scheme["profit"] if pnl > 0 else self.config.color_scheme["loss"]
                    ax.scatter([exit_time], [exit_price], marker='v', color=color, s=100, zorder=5)
        
        ax.set_title(title, fontsize=self.config.title_fontsize, pad=20)
        ax.set_xlabel("Date", fontsize=self.config.label_fontsize)
        ax.set_ylabel("Price", fontsize=self.config.label_fontsize)
        ax.grid(True, alpha=0.3)
        
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        plt.xticks(rotation=45)
        
        # Legend
        entry_patch = mpatches.Patch(color='green', label='Entry (Long)')
        exit_profit = mpatches.Patch(color=self.config.color_scheme["profit"], label='Exit (Profit)')
        exit_loss = mpatches.Patch(color=self.config.color_scheme["loss"], label='Exit (Loss)')
        ax.legend(handles=[entry_patch, exit_profit, exit_loss], loc="upper left", fontsize=self.config.tick_fontsize)
        
        plt.tight_layout()
        return fig
    
    def plot_returns_distribution(
        self,
        report: Any,
        title: str = "Returns Distribution",
        bins: int = 50,
    ) -> Any:
        """
        绘制收益分布图
        
        Args:
            report: BacktestReport
            title: 图表标题
            bins: 直方图箱数
            
        Returns:
            matplotlib Figure 对象
        """
        plt.style.use(self.config.style)
        
        returns = self._get_returns(report)
        if not returns:
            fig, ax = plt.subplots(figsize=self.config.figsize, dpi=self.config.dpi)
            ax.text(0.5, 0.5, "No return data available", ha='center', va='center')
            return fig
        
        fig, (ax_hist, ax_box) = plt.subplots(2, 1, figsize=(self.config.figsize[0], self.config.figsize[1] * 1.2), gridspec_kw={'height_ratios': [3, 1]})
        
        # Histogram
        ax_hist.hist(returns, bins=bins, color=self.config.color_scheme["equity"], alpha=0.7, edgecolor='white')
        ax_hist.axvline(x=0, color='red', linestyle='--', linewidth=1.5, label='Zero')
        ax_hist.axvline(x=sum(returns) / len(returns), color='orange', linestyle='-', linewidth=1.5, label=f'Mean: {sum(returns)/len(returns):.4f}')
        
        ax_hist.set_title(title, fontsize=self.config.title_fontsize, pad=10)
        ax_hist.set_xlabel("Return", fontsize=self.config.label_fontsize)
        ax_hist.set_ylabel("Frequency", fontsize=self.config.label_fontsize)
        ax_hist.legend(loc="upper right", fontsize=self.config.tick_fontsize)
        ax_hist.grid(True, alpha=0.3)
        
        # Box plot
        ax_box.boxplot(returns, vert=False, patch_artist=True,
                      boxprops=dict(facecolor=self.config.color_scheme["equity"], alpha=0.7))
        ax_box.set_xlabel("Return", fontsize=self.config.label_fontsize)
        ax_box.set_yticks([])
        ax_box.grid(True, alpha=0.3)
        
        plt.tight_layout()
        return fig
    
    def plot_combined(
        self,
        report: Any,
        benchmark: Optional[Dict[str, Any]] = None,
        title: str = "Backtest Analysis",
    ) -> Any:
        """
        绘制组合图表（资金曲线 + 回撤 + 月度收益）
        
        Args:
            report: BacktestReport
            benchmark: 可选的基准数据
            title: 图表标题
            
        Returns:
            matplotlib Figure 对象
        """
        plt.style.use(self.config.style)
        
        fig = plt.figure(figsize=(self.config.figsize[0], self.config.figsize[1] * 2), dpi=self.config.dpi)
        
        # Create subplot grid
        gs = fig.add_gridspec(3, 2, height_ratios=[2, 1.5, 1.5], hspace=0.3, wspace=0.3)
        
        # 1. Equity Curve
        ax1 = fig.add_subplot(gs[0, :])
        equity_curve = self._get_equity_curve(report)
        if equity_curve:
            timestamps = self._parse_timestamps([p.get("timestamp") if isinstance(p, dict) else getattr(p, 'timestamp', None) for p in equity_curve])
            equities = self._parse_equities([p.get("equity") if isinstance(p, dict) else getattr(p, 'equity', 0) for p in equity_curve])
            ax1.plot(timestamps, equities, color=self.config.color_scheme["equity"], linewidth=2, label="Strategy")
            
            if benchmark:
                bench_ts = self._parse_timestamps(benchmark.get("timestamps", []))
                bench_eq = self._parse_equities(benchmark.get("equities", []))
                if bench_ts and bench_eq:
                    ax1.plot(bench_ts, bench_eq, color=self.config.color_scheme["benchmark"], linewidth=1.5, linestyle="--", label="Buy & Hold")
            
            ax1.set_title(f"{title} - Equity Curve", fontsize=self.config.title_fontsize)
            ax1.set_ylabel("Equity", fontsize=self.config.label_fontsize)
            ax1.legend(loc="upper left")
            ax1.grid(True, alpha=0.3)
            ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
            plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45)
        else:
            ax1.text(0.5, 0.5, "No equity data", ha='center', va='center')
        
        # 2. Drawdown
        ax2 = fig.add_subplot(gs[1, 0])
        if equity_curve:
            peak = equities[0]
            drawdowns = []
            for eq in equities:
                if eq > peak:
                    peak = eq
                dd = ((peak - eq) / peak * 100) if peak > 0 else 0
                drawdowns.append(-dd)
            ax2.fill_between(timestamps, drawdowns, 0, color=self.config.color_scheme["drawdown"], alpha=0.5)
            ax2.plot(timestamps, drawdowns, color=self.config.color_scheme["drawdown"], linewidth=1)
            ax2.set_title("Drawdown", fontsize=self.config.title_fontsize)
            ax2.set_ylabel("Drawdown (%)", fontsize=self.config.label_fontsize)
            ax2.grid(True, alpha=0.3)
            ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
            plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45)
        
        # 3. Returns Distribution
        ax3 = fig.add_subplot(gs[1, 1])
        returns = self._get_returns(report)
        if returns:
            ax3.hist(returns, bins=30, color=self.config.color_scheme["equity"], alpha=0.7, edgecolor='white')
            ax3.axvline(x=0, color='red', linestyle='--', linewidth=1.5)
            ax3.set_title("Returns Distribution", fontsize=self.config.title_fontsize)
            ax3.set_xlabel("Return", fontsize=self.config.label_fontsize)
            ax3.set_ylabel("Frequency", fontsize=self.config.label_fontsize)
            ax3.grid(True, alpha=0.3)
        
        # 4. Monthly Heatmap (simplified as bar chart if heatmap not available)
        ax4 = fig.add_subplot(gs[2, :])
        monthly_returns = self._get_monthly_returns(report)
        if monthly_returns:
            # Use bar chart instead of heatmap for combined view
            months = []
            values = []
            for ret_dict in monthly_returns:
                if isinstance(ret_dict, dict):
                    year = ret_dict.get("year", "")
                    month = ret_dict.get("month", 0)
                    ret = ret_dict.get("return", 0)
                    months.append(f"{year}-{month:02d}")
                    values.append(float(ret) if isinstance(ret, Decimal) else ret)
            
            colors = [self.config.color_scheme["profit"] if v >= 0 else self.config.color_scheme["loss"] for v in values]
            ax4.bar(months, values, color=colors, alpha=0.7)
            ax4.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
            ax4.set_title("Monthly Returns", fontsize=self.config.title_fontsize)
            ax4.set_xlabel("Month", fontsize=self.config.label_fontsize)
            ax4.set_ylabel("Return (%)", fontsize=self.config.label_fontsize)
            ax4.grid(True, alpha=0.3, axis='y')
            plt.setp(ax4.xaxis.get_majorticklabels(), rotation=45, fontsize=8)
        
        plt.tight_layout()
        return fig
    
    def _get_equity_curve(self, report: Any) -> List[Any]:
        """Extract equity curve from report."""
        if hasattr(report, 'result') and hasattr(report.result, 'equity_curve'):
            return list(report.result.equity_curve)
        if hasattr(report, 'equity_curve'):
            return list(report.equity_curve)
        return []
    
    def _get_trades(self, report: Any) -> List[Any]:
        """Extract trades from report."""
        if hasattr(report, 'result') and hasattr(report.result, 'trades'):
            return list(report.result.trades)
        if hasattr(report, 'trades'):
            return list(report.trades)
        return []
    
    def _get_returns(self, report: Any) -> List[float]:
        """Extract returns from equity curve."""
        equity_curve = self._get_equity_curve(report)
        if len(equity_curve) < 2:
            return []
        
        equities = self._parse_equities([p.get("equity") if isinstance(p, dict) else getattr(p, 'equity', 0) for p in equity_curve])
        
        returns = []
        for i in range(1, len(equities)):
            if equities[i-1] != 0:
                ret = (equities[i] - equities[i-1]) / equities[i-1]
                returns.append(ret * 100)  # Convert to percentage
        
        return returns
    
    def _get_monthly_returns(self, report: Any) -> List[Dict[str, Any]]:
        """Extract monthly returns from report or compute them."""
        # Try to get from report first
        if hasattr(report, 'returns') and hasattr(report.returns, 'monthly_returns'):
            monthly_rets = report.returns.monthly_returns
            if monthly_rets:
                # Need year/month info - compute from equity curve
                equity_curve = self._get_equity_curve(report)
                if equity_curve:
                    timestamps = self._parse_timestamps([p.get("timestamp") if isinstance(p, dict) else getattr(p, 'timestamp', None) for p in equity_curve])
                    equities = self._parse_equities([p.get("equity") if isinstance(p, dict) else getattr(p, 'equity', 0) for p in equity_curve])
                    
                    # Group by year-month
                    monthly_groups: Dict[Tuple[int, int], List[float]] = {}
                    for i, ts in enumerate(timestamps):
                        if i > 0:
                            key = (ts.year, ts.month)
                            if key not in monthly_groups:
                                monthly_groups[key] = []
                            if equities[i-1] > 0:
                                monthly_groups[key].append((equities[i] - equities[i-1]) / equities[i-1] * 100)
                    
                    result = []
                    for (year, month), rets in sorted(monthly_groups.items()):
                        if rets:
                            avg_ret = sum(rets) / len(rets)
                            result.append({"year": year, "month": month, "return": avg_ret})
                    return result
        
        return []


# Convenience functions for quick plotting
def plot_equity_curve(report: Any, **kwargs) -> Any:
    """Quick plot equity curve."""
    viz = BacktestVisualizer()
    return viz.plot_equity_curve(report, **kwargs)


def plot_drawdown(report: Any, **kwargs) -> Any:
    """Quick plot drawdown."""
    viz = BacktestVisualizer()
    return viz.plot_drawdown(report, **kwargs)


def plot_monthly_heatmap(report: Any, **kwargs) -> Any:
    """Quick plot monthly heatmap."""
    viz = BacktestVisualizer()
    return viz.plot_monthly_heatmap(report, **kwargs)


def plot_trade_markers(report: Any, **kwargs) -> Any:
    """Quick plot trade markers."""
    viz = BacktestVisualizer()
    return viz.plot_trade_markers(report, **kwargs)


def plot_returns_distribution(report: Any, **kwargs) -> Any:
    """Quick plot returns distribution."""
    viz = BacktestVisualizer()
    return viz.plot_returns_distribution(report, **kwargs)


def plot_combined(report: Any, **kwargs) -> Any:
    """Quick plot combined analysis."""
    viz = BacktestVisualizer()
    return viz.plot_combined(report, **kwargs)
