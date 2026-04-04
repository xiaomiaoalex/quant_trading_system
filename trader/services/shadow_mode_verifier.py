"""
ShadowModeVerifier - 影子模式验证框架
====================================
实现回测信号 vs 影子实盘信号 vs 实际成交的三者偏差比较。

核心功能：
1. 比较回测环境产生的信号与影子实盘产生的信号
2. 比较影子实盘信号与实际成交结果
3. 监控 sizing 偏差
4. 监控成交价格偏差

使用方式：
    verifier = ShadowModeVerifier()
    
    report = await verifier.verify(
        strategy_id="strategy_A",
        lookback_period=timedelta(days=7),
    )
    
    if not report.overall_healthy:
        print(f"偏差告警: {report.recommendations}")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal, Optional


# ==================== 验证结果类型 ====================

@dataclass
class ShadowDeviationReport:
    """
    影子模式偏差报告
    
    属性：
        strategy_id: 策略ID
        lookback_period: 回溯周期
        total_signals: 总信号数
        backtest_signals: 回测信号数
        shadow_signals: 影子信号数
        execution_signals: 实际成交信号数
        
        # 偏差指标
        signal_trigger_rate_diff: 信号触发率偏差（回测 vs 影子）
        sizing_avg_diff: sizing 平均偏差
        sizing_max_diff: sizing 最大偏差
        execution_gap_avg: 成交价平均偏差
        execution_gap_max: 成交价最大偏差
        risk_block_rate_diff: 风控拦截率差异
        
        # 阈值判断
        signal_diff_threshold: 信号触发率偏差阈值 (0.2)
        sizing_diff_threshold: sizing 偏差阈值 (0.3)
        execution_gap_threshold: 成交偏差阈值 (2x 回测滑点假设)
        
        # 整体状态
        overall_healthy: 是否健康
        alerts: 告警列表
        recommendations: 建议列表
        timestamp: 报告生成时间
    """
    strategy_id: str
    lookback_period: timedelta
    
    # 数量统计
    total_signals: int = 0
    backtest_signals: int = 0
    shadow_signals: int = 0
    execution_signals: int = 0
    
    # 偏差指标
    signal_trigger_rate_diff: float = 0.0
    sizing_avg_diff: float = 0.0
    sizing_max_diff: float = 0.0
    execution_gap_avg: float = 0.0
    execution_gap_max: float = 0.0
    risk_block_rate_diff: float = 0.0
    
    # 阈值
    signal_diff_threshold: float = 0.2
    sizing_diff_threshold: float = 0.3
    
    # 状态
    overall_healthy: bool = True
    alerts: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "strategy_id": self.strategy_id,
            "lookback_period_hours": self.lookback_period.total_seconds() / 3600,
            "total_signals": self.total_signals,
            "backtest_signals": self.backtest_signals,
            "shadow_signals": self.shadow_signals,
            "execution_signals": self.execution_signals,
            "signal_trigger_rate_diff": round(self.signal_trigger_rate_diff, 4),
            "sizing_avg_diff": round(self.sizing_avg_diff, 4),
            "sizing_max_diff": round(self.sizing_max_diff, 4),
            "execution_gap_avg": round(self.execution_gap_avg, 6),
            "execution_gap_max": round(self.execution_gap_max, 6),
            "risk_block_rate_diff": round(self.risk_block_rate_diff, 4),
            "overall_healthy": self.overall_healthy,
            "alerts": self.alerts,
            "recommendations": self.recommendations,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class SignalRecord:
    """
    信号记录（用于比较）
    
    属性：
        signal_id: 信号ID
        timestamp: 信号时间
        signal_type: 信号类型 (BUY/SELL)
        symbol: 交易标的
        sizing: 建议仓位
        backtest_signal: 是否来自回测
        shadow_signal: 是否来自影子实盘
        executed_signal: 是否实际成交
        backtest_price: 回测成交价
        shadow_price: 影子成交价
        execution_price: 实际成交价
        blocked_by_risk: 是否被风控拦截
    """
    signal_id: str
    timestamp: datetime
    signal_type: str
    symbol: str
    sizing: float
    backtest_signal: bool = False
    shadow_signal: bool = False
    executed_signal: bool = False
    backtest_price: Optional[float] = None
    shadow_price: Optional[float] = None
    execution_price: Optional[float] = None
    blocked_by_risk: bool = False


# ==================== 影子模式验证器实现 ====================

class ShadowModeVerifier:
    """
    影子模式验证器
    
    用于验证：
    1. 回测信号 vs 影子实盘信号的触发率偏差
    2. 影子实盘信号 vs 实际成交的偏差
    3. sizing 偏差
    4. 风控拦截率差异
    """
    
    def __init__(
        self,
        signal_diff_threshold: float = 0.2,
        sizing_diff_threshold: float = 0.3,
    ) -> None:
        """
        初始化影子模式验证器
        
        Args:
            signal_diff_threshold: 信号触发率偏差阈值 (默认 20%)
            sizing_diff_threshold: sizing 偏差阈值 (默认 30%)
        """
        self._signal_threshold = signal_diff_threshold
        self._sizing_threshold = sizing_diff_threshold
    
    async def verify(
        self,
        strategy_id: str,
        lookback_period: timedelta = timedelta(days=7),
        signals: list[SignalRecord] | None = None,
    ) -> ShadowDeviationReport:
        """
        执行影子模式验证
        
        Args:
            strategy_id: 策略ID
            lookback_period: 回溯周期
            signals: 信号记录列表（如果为 None，从存储获取）
            
        Returns:
            影子模式偏差报告
        """
        # 如果没有提供信号，从存储获取
        if signals is None:
            signals = await self._fetch_signals(strategy_id, lookback_period)
        
        report = ShadowDeviationReport(
            strategy_id=strategy_id,
            lookback_period=lookback_period,
            total_signals=len(signals),
        )
        
        # 分类统计
        backtest_signals = [s for s in signals if s.backtest_signal]
        shadow_signals = [s for s in signals if s.shadow_signal]
        executed_signals = [s for s in signals if s.executed_signal]
        
        report.backtest_signals = len(backtest_signals)
        report.shadow_signals = len(shadow_signals)
        report.execution_signals = len(executed_signals)
        
        # 计算信号触发率偏差
        if len(backtest_signals) > 0 and len(shadow_signals) > 0:
            backtest_rate = len(backtest_signals) / report.total_signals if report.total_signals > 0 else 0
            shadow_rate = len(shadow_signals) / report.total_signals if report.total_signals > 0 else 0
            report.signal_trigger_rate_diff = abs(backtest_rate - shadow_rate)
        
        # 计算 sizing 偏差
        sizing_pairs = self._match_sizing_pairs(backtest_signals, shadow_signals)
        if sizing_pairs:
            sizing_diffs = [abs(b - s) / b if b > 0 else 0 for b, s in sizing_pairs]
            report.sizing_avg_diff = sum(sizing_diffs) / len(sizing_diffs)
            report.sizing_max_diff = max(sizing_diffs) if sizing_diffs else 0
        
        # 计算成交价格偏差
        execution_pairs = self._match_execution_pairs(shadow_signals, executed_signals)
        if execution_pairs:
            gap_avg, gap_max = self._calculate_execution_gap(execution_pairs)
            report.execution_gap_avg = gap_avg
            report.execution_gap_max = gap_max
        
        # 计算风控拦截率差异
        backtest_blocked = sum(1 for s in backtest_signals if s.blocked_by_risk)
        shadow_blocked = sum(1 for s in shadow_signals if s.blocked_by_risk)
        if len(backtest_signals) > 0:
            report.risk_block_rate_diff = abs(
                backtest_blocked / len(backtest_signals) - 
                shadow_blocked / len(shadow_signals) if len(shadow_signals) > 0 else 0
            )
        
        # 判断健康状态
        report.overall_healthy = self._evaluate_health(report)
        
        return report
    
    def _match_sizing_pairs(
        self,
        backtest_signals: list[SignalRecord],
        shadow_signals: list[SignalRecord],
    ) -> list[tuple[float, float]]:
        """匹配同一信号的 backtest sizing 和 shadow sizing"""
        # 按 symbol + timestamp 匹配
        shadow_map = {
            (s.symbol, s.timestamp): s.sizing
            for s in shadow_signals
        }
        
        pairs = []
        for b in backtest_signals:
            key = (b.symbol, b.timestamp)
            if key in shadow_map:
                pairs.append((b.sizing, shadow_map[key]))
        
        return pairs
    
    def _match_execution_pairs(
        self,
        shadow_signals: list[SignalRecord],
        executed_signals: list[SignalRecord],
    ) -> list[tuple[float, float]]:
        """匹配影子信号和实际成交"""
        # 按 signal_id 匹配
        exec_map = {
            s.signal_id: s.execution_price
            for s in executed_signals
            if s.execution_price is not None
        }
        
        pairs = []
        for s in shadow_signals:
            if s.signal_id in exec_map and s.shadow_price is not None:
                pairs.append((s.shadow_price, exec_map[s.signal_id]))
        
        return pairs
    
    def _calculate_execution_gap(
        self,
        pairs: list[tuple[float, float]],
    ) -> tuple[float, float]:
        """计算成交价格偏差"""
        if not pairs:
            return 0.0, 0.0
        
        gaps = [abs(exec - shadow) / shadow if shadow > 0 else 0 for shadow, exec in pairs]
        return sum(gaps) / len(gaps), max(gaps) if gaps else 0.0
    
    def _evaluate_health(self, report: ShadowDeviationReport) -> bool:
        """评估整体健康状态"""
        alerts = []
        recommendations = []
        
        # 检查信号触发率偏差
        if report.signal_trigger_rate_diff > self._signal_threshold:
            alerts.append(
                f"信号触发率偏差 {report.signal_trigger_rate_diff:.1%} 超过阈值 {self._signal_threshold:.1%}"
            )
            recommendations.append("检查回测和实盘的数据源是否一致")
        
        # 检查 sizing 偏差
        if report.sizing_avg_diff > self._sizing_threshold:
            alerts.append(
                f"Sizing 平均偏差 {report.sizing_avg_diff:.1%} 超过阈值 {self._sizing_threshold:.1%}"
            )
            recommendations.append("检查 sizing 模型的实盘适应性")
        
        if report.sizing_max_diff > self._sizing_threshold * 2:
            alerts.append(
                f"Sizing 最大偏差 {report.sizing_max_diff:.1%} 异常高"
            )
            recommendations.append("排查极端市场条件下的 sizing 异常")
        
        # 检查成交偏差
        if report.execution_gap_avg > 0.001:  # 10 bps
            alerts.append(f"成交价平均偏差 {report.execution_gap_avg:.4f} 较高")
            recommendations.append("检查滑点模型是否低估")
        
        # 检查风控拦截率差异
        if report.risk_block_rate_diff > 0.5:
            alerts.append(
                f"风控拦截率差异 {report.risk_block_rate_diff:.1%} 超过 50%"
            )
            recommendations.append("检查回测和实盘的风控规则是否一致")
        
        report.alerts = alerts
        report.recommendations = recommendations
        
        return len(alerts) == 0
    
    async def _fetch_signals(
        self,
        strategy_id: str,
        lookback_period: timedelta,
    ) -> list[SignalRecord]:
        """从存储获取信号记录（需要实现）"""
        # TODO: 从 event_log 或影子模式存储获取信号
        # 目前返回空列表，调用方需要提供信号列表
        return []


# ==================== 辅助函数 ====================

def create_signal_record(
    signal_id: str,
    timestamp: datetime,
    signal_type: str,
    symbol: str,
    sizing: float,
    source: Literal["backtest", "shadow", "execution"],
    price: float | None = None,
    blocked: bool = False,
) -> SignalRecord:
    """
    创建信号记录的工厂函数
    
    Args:
        signal_id: 信号ID
        timestamp: 时间戳
        signal_type: 信号类型
        symbol: 交易标的
        sizing: 仓位大小
        source: 信号来源
        price: 成交价格（可选）
        blocked: 是否被风控拦截
        
    Returns:
        SignalRecord
    """
    record = SignalRecord(
        signal_id=signal_id,
        timestamp=timestamp,
        signal_type=signal_type,
        symbol=symbol,
        sizing=sizing,
        blocked_by_risk=blocked,
    )
    
    if source == "backtest":
        record.backtest_signal = True
        record.backtest_price = price
    elif source == "shadow":
        record.shadow_signal = True
        record.shadow_price = price
    elif source == "execution":
        record.executed_signal = True
        record.execution_price = price
    
    return record


def compare_backtest_to_shadow(
    backtest_signals: list[SignalRecord],
    shadow_signals: list[SignalRecord],
) -> dict:
    """
    简单比较回测信号和影子信号
    
    Args:
        backtest_signals: 回测信号列表
        shadow_signals: 影子信号列表
        
    Returns:
        比较结果字典
    """
    if not backtest_signals:
        return {"error": "No backtest signals"}
    
    if not shadow_signals:
        return {"error": "No shadow signals"}
    
    # 计算触发率
    backtest_rate = len(backtest_signals) / len(backtest_signals)  # 相对于自身
    shadow_rate = len(shadow_signals) / len(shadow_signals)
    
    # 计算平均 sizing 差异
    backtest_sizes = [s.sizing for s in backtest_signals]
    shadow_sizes = [s.sizing for s in shadow_signals]
    
    avg_diff = sum(
        abs(b - s) / b if b > 0 else 0
        for b, s in zip(backtest_sizes, shadow_sizes[:len(backtest_sizes)])
    ) / len(backtest_sizes) if backtest_sizes else 0
    
    return {
        "backtest_count": len(backtest_signals),
        "shadow_count": len(shadow_signals),
        "trigger_rate_diff": abs(backtest_rate - shadow_rate),
        "avg_sizing_diff": avg_diff,
        "healthy": avg_diff < 0.3 and abs(backtest_rate - shadow_rate) < 0.2,
    }
