"""
Capital Structure Signals - 资金结构信号计算
==============================================

Core Plane 纯函数实现，无任何IO操作。

资金结构信号用于检测市场资金流向的异常情况：
- Funding Rate Z-Score: 资金费率偏离度
- OI Change Rate Divergence: 未平仓合约与价格背离
- Long Short Ratio Anomaly: 多空比极端值检测
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, List, Any
import math


class DivergenceDirection(Enum):
    """背离方向"""
    NONE = "NONE"
    BULLISH = "BULLISH"   # 多头背离：OI下跌 + 价格上涨
    BEARISH = "BEARISH"   # 空头背离：OI上涨 + 价格下跌


# ==================== 常量定义 ====================

class SignalThresholds:
    """信号阈值常量"""
    # Z-Score 极端阈值
    ZSCORE_EXTREME = 2.0
    ZSCORE_MODERATE = 1.5
    
    # 背离得分阈值
    DIVERGENCE_THRESHOLD = 0.5  # 背离得分超过此值认为存在背离
    
    # 多空比极端阈值（百分比）
    LONG_SHORT_EXTREME_RATIO = 0.3  # 多空比偏离30%以上为极端


# ==================== 数据结构 ====================

@dataclass(frozen=True)
class FundingRateSample:
    """资金费率样本"""
    ts_ms: int
    funding_rate: float  # 资金费率 (如 0.0001)


@dataclass(frozen=True)
class OISample:
    """未平仓合约样本"""
    ts_ms: int
    open_interest: float  # 未平仓合约量
    price: float          # 价格


@dataclass(frozen=True)
class LongShortSample:
    """多空比样本"""
    ts_ms: int
    long_short_ratio: float  # 多空比 (longQty / shortQty)


@dataclass(frozen=True)
class FundingRateZScoreResult:
    """Funding Rate Z-Score 计算结果"""
    symbol: str
    z_score: Optional[float]  # None when window insufficient
    mean: float
    std: float
    window: int
    sample_count: int
    ts_ms: int


@dataclass(frozen=True)
class OIDivergenceResult:
    """OI背离检测结果"""
    symbol: str
    oi_change_rate: float           # OI变化率 (%)
    price_change_rate: float        # 价格变化率 (%)
    divergence_score: float         # 背离得分
    direction: DivergenceDirection
    is_divergence: bool
    ts_ms: int


@dataclass(frozen=True)
class LongShortAnomalyResult:
    """多空比异常检测结果"""
    symbol: str
    long_short_ratio: float
    z_score: Optional[float]        # None when window insufficient
    is_extreme: bool
    is_extreme_long: bool           # 多头极端
    is_extreme_short: bool          # 空头极端
    sample_count: int
    ts_ms: int


# ==================== 信号计算器 ====================

class FundingRateZScore:
    """
    Funding Rate Z-Score 信号计算器
    
    计算当前资金费率相对于历史滚动窗口的Z-Score。
    Z-Score > 0 表示资金费率高于均值（多头支付空头）
    Z-Score < 0 表示资金费率低于均值（空头支付多头）
    
    极端Z-Score（|z| > 2）通常预示市场情绪过热，可能反转。
    """
    
    # 类级别的极端阈值
    EXTREME_THRESHOLD: float = SignalThresholds.ZSCORE_EXTREME
    
    @staticmethod
    def compute(
        symbol: str,
        current_funding_rate: float,
        history: List[FundingRateSample],
        window: int = 20,
        min_periods: Optional[int] = None,
        ts_ms: Optional[int] = None,
    ) -> FundingRateZScoreResult:
        """
        计算资金费率Z-Score
        
        Args:
            symbol: 交易标的
            current_funding_rate: 当前资金费率
            history: 历史资金费率样本（按时间升序）
            window: 滚动窗口大小
            min_periods: 最小样本数（默认window的50%）
            ts_ms: 当前时间戳
            
        Returns:
            FundingRateZScoreResult: 计算结果
            
        Note:
            当 history 样本数 < min_periods 时，z_score 返回 None
        """
        if min_periods is None:
            min_periods = window // 2  # 默认要求至少一半样本
        
        # 确定时间戳
        if ts_ms is None:
            ts_ms = history[-1].ts_ms if history else 0
        
        # 样本不足时返回 None
        if len(history) < min_periods:
            return FundingRateZScoreResult(
                symbol=symbol,
                z_score=None,
                mean=0.0,
                std=0.0,
                window=window,
                sample_count=len(history),
                ts_ms=ts_ms,
            )
        
        # 使用最近 window 个样本
        window_history = history[-window:] if len(history) >= window else history
        
        # 计算均值和标准差
        rates = [s.funding_rate for s in window_history]
        mean = sum(rates) / len(rates)
        
        # 计算标准差（总体标准差）
        variance = sum((r - mean) ** 2 for r in rates) / len(rates)
        std = math.sqrt(variance)
        
        # 防止除零
        if std == 0:
            z_score: Optional[float] = None
        else:
            z_score = (current_funding_rate - mean) / std
        
        return FundingRateZScoreResult(
            symbol=symbol,
            z_score=z_score,
            mean=mean,
            std=std,
            window=window,
            sample_count=len(window_history),
            ts_ms=ts_ms,
        )


class OIChangeRateDivergence:
    """
    OI变化率与价格背离检测器
    
    检测OI与价格的背离情况：
    - 多头背离（BULLISH）: OI下降 + 价格上升 → 可能见底
    - 空头背离（BEARISH）: OI上升 + 价格下降 → 可能见顶
    
    原理：正常情况下，OI与价格同向变动。背离预示动能减弱。
    """
    
    DIVERGENCE_THRESHOLD: float = SignalThresholds.DIVERGENCE_THRESHOLD
    
    @staticmethod
    def compute(
        symbol: str,
        oi_current: float,
        oi_previous: float,
        price_current: float,
        price_previous: float,
        ts_ms: Optional[int] = None,
    ) -> OIDivergenceResult:
        """
        计算OI与价格背离
        
        Args:
            symbol: 交易标的
            oi_current: 当前OI
            oi_previous: 上一时刻OI
            price_current: 当前价格
            price_previous: 上一时刻价格
            ts_ms: 当前时间戳
            
        Returns:
            OIDivergenceResult: 背离检测结果
        """
        # 计算变化率
        if oi_previous != 0:
            oi_change_rate = (oi_current - oi_previous) / oi_previous * 100
        else:
            oi_change_rate = 0.0
            
        if price_previous != 0:
            price_change_rate = (price_current - price_previous) / price_previous * 100
        else:
            price_change_rate = 0.0
        
        # 检测背离方向
        oi_rising = oi_change_rate > 0
        price_rising = price_change_rate > 0
        
        # 计算背离得分
        # 得分 = |OI变化率 - 价格变化率| / 100，范围 [0, 2]
        divergence_score = abs(oi_change_rate - price_change_rate) / 100
        
        # 确定背离方向
        if not oi_rising and price_rising:
            direction = DivergenceDirection.BULLISH
        elif oi_rising and not price_rising:
            direction = DivergenceDirection.BEARISH
        else:
            direction = DivergenceDirection.NONE
        
        # 判断是否存在显著背离
        is_divergence = (
            divergence_score >= OIChangeRateDivergence.DIVERGENCE_THRESHOLD
            and direction != DivergenceDirection.NONE
        )
        
        return OIDivergenceResult(
            symbol=symbol,
            oi_change_rate=oi_change_rate,
            price_change_rate=price_change_rate,
            divergence_score=divergence_score,
            direction=direction,
            is_divergence=is_divergence,
            ts_ms=ts_ms or 0,
        )


class LongShortRatioAnomaly:
    """
    多空比异常检测器
    
    检测多空比（longQty/shortQty）的极端值情况：
    - 极端偏多：散户做多比例过高，可能被做市商对冲
    - 极端偏空：散户做空比例过高，可能遭遇逼空
    
    使用Z-Score检测相对于历史的极端偏离。
    """
    
    EXTREME_THRESHOLD: float = SignalThresholds.LONG_SHORT_EXTREME_RATIO
    
    @staticmethod
    def compute(
        symbol: str,
        current_ratio: float,
        history: List[LongShortSample],
        window: int = 20,
        min_periods: Optional[int] = None,
        ts_ms: Optional[int] = None,
    ) -> LongShortAnomalyResult:
        """
        计算多空比异常
        
        Args:
            symbol: 交易标的
            current_ratio: 当前多空比
            history: 历史多空比样本
            window: 滚动窗口大小
            min_periods: 最小样本数（默认window的50%）
            ts_ms: 当前时间戳
            
        Returns:
            LongShortAnomalyResult: 异常检测结果
        """
        if min_periods is None:
            min_periods = window // 2
        
        if ts_ms is None:
            ts_ms = history[-1].ts_ms if history else 0
        
        # 样本不足时返回
        if len(history) < min_periods:
            return LongShortAnomalyResult(
                symbol=symbol,
                long_short_ratio=current_ratio,
                z_score=None,
                is_extreme=False,
                is_extreme_long=False,
                is_extreme_short=False,
                sample_count=len(history),
                ts_ms=ts_ms,
            )
        
        # 使用最近 window 个样本
        window_history = history[-window:] if len(history) >= window else history
        ratios = [s.long_short_ratio for s in window_history]
        
        # 计算均值和标准差
        mean = sum(ratios) / len(ratios)
        variance = sum((r - mean) ** 2 for r in ratios) / len(ratios)
        std = math.sqrt(variance)
        
        # 计算Z-Score
        if std == 0:
            z_score: Optional[float] = None
        else:
            z_score = (current_ratio - mean) / std
        
        # 检测极端值
        # 极端偏多：当前多空比远高于均值（交易员做多过多）
        # 极端偏空：当前多空比远低于均值（交易员做空过多）
        
        # 使用相对偏离度判断极端
        if mean != 0:
            relative_deviation = (current_ratio - mean) / mean
        else:
            relative_deviation = 0.0
        
        is_extreme_long = relative_deviation > LongShortRatioAnomaly.EXTREME_THRESHOLD
        is_extreme_short = relative_deviation < -LongShortRatioAnomaly.EXTREME_THRESHOLD
        is_extreme = is_extreme_long or is_extreme_short
        
        return LongShortAnomalyResult(
            symbol=symbol,
            long_short_ratio=current_ratio,
            z_score=z_score,
            is_extreme=is_extreme,
            is_extreme_long=is_extreme_long,
            is_extreme_short=is_extreme_short,
            sample_count=len(window_history),
            ts_ms=ts_ms,
        )


# ==================== 复合信号计算 ====================

@dataclass(frozen=True)
class CapitalStructureSignal:
    """
    资金结构复合信号
    
    汇总三个子信号的综合判断。
    """
    symbol: str
    funding_z_score: Optional[float]
    oi_divergence: bool
    ls_anomaly: bool
    composite_score: float  # 综合得分 [-1, 1]
    ts_ms: int


def compute_composite_capital_signal(
    symbol: str,
    funding_result: FundingRateZScoreResult,
    oi_result: OIDivergenceResult,
    ls_result: LongShortAnomalyResult,
    ts_ms: Optional[int] = None,
) -> CapitalStructureSignal:
    """
    计算资金结构复合信号
    
    综合评分逻辑（负分=看空，正分=看多）：
    - funding_z_score: 极端正值=看空，负值=看多
    - oi_divergence: 多头背离=看空，空头背离=看空（反向）
    - ls_anomaly: 极端偏多=看空，极端偏空=看多
    
    Args:
        symbol: 交易标的
        funding_result: 资金费率Z-Score结果
        oi_result: OI背离结果
        ls_result: 多空比异常结果
        ts_ms: 当前时间戳
        
    Returns:
        CapitalStructureSignal: 复合信号
    """
    if ts_ms is None:
        ts_ms = max(
            funding_result.ts_ms,
            oi_result.ts_ms,
            ls_result.ts_ms,
        )
    
    # 计算综合得分
    score = 0.0
    score_count = 0
    
    # 1. Funding Rate Z-Score 加权
    # 正Z-Score = 资金费率高于均值 = 多头支付空头 = 市场过热 = 看空信号(-)
    # 负Z-Score = 资金费率低于均值 = 空头支付多头 = 可能反弹 = 看多信号(+)
    if funding_result.z_score is not None:
        z = funding_result.z_score
        if abs(z) >= SignalThresholds.ZSCORE_EXTREME:
            score += -0.3 if z > 0 else 0.3  # 极端正值看空(-)，负值看多(+)
        elif abs(z) >= SignalThresholds.ZSCORE_MODERATE:
            score += -0.15 if z > 0 else 0.15
        score_count += 1
    
    # 2. OI背离加权
    # 多头背离(BULLISH): OI跌+价格涨 = 见底信号 → score += +0.3
    # 空头背离(BEARISH): OI涨+价格跌 = 见顶信号 → score += -0.3
    if oi_result.is_divergence:
        if oi_result.direction == DivergenceDirection.BULLISH:
            score += 0.3
        else:  # BEARISH
            score += -0.3
        score_count += 1
    
    # 3. 多空比异常加权（反向指标）
    # 极端偏多(extreme_long): 做多过多 → score += -0.4
    # 极端偏空(extreme_short): 做空过多 → score += +0.4
    if ls_result.is_extreme:
        if ls_result.is_extreme_long:
            score += -0.4
        else:  # extreme_short
            score += 0.4
        score_count += 1
    
    # 归一化到 [-1, 1]
    # 最大权重 = score_count * 0.4（各信号最大绝对值之和）
    if score_count > 0:
        max_abs_score = score_count * 0.4
        composite_score = max(-1.0, min(1.0, score / max_abs_score))
    else:
        composite_score = 0.0
    
    return CapitalStructureSignal(
        symbol=symbol,
        funding_z_score=funding_result.z_score,
        oi_divergence=oi_result.is_divergence,
        ls_anomaly=ls_result.is_extreme,
        composite_score=composite_score,
        ts_ms=ts_ms,
    )
