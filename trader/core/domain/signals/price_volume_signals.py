"""
Price Volume Signals - 价量信号计算
======================================

Core Plane 纯函数实现，无任何IO操作。

价量信号用于检测成交量与价格关系的各类特征：
- 成交量扩张检测: 检测成交量异常放大
- 波动率压缩检测: 检测波动率异常收缩
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional, List


class VolumeDirection(Enum):
    """成交量方向"""
    EXPANSION = "EXPANSION"       # 放量
    CONTRACTION = "CONTRACTION"   # 缩量
    NORMAL = "NORMAL"             # 正常


# ==================== 数据结构 ====================

@dataclass(frozen=True)
class VolumeSample:
    """成交量样本"""
    ts_ms: int
    volume: Decimal           # 成交量
    quote_volume: Decimal     # 成交额（成交量 * 价格）
    trade_count: int          # 成交笔数


@dataclass(frozen=True)
class PriceVolumeSample:
    """价量样本"""
    ts_ms: int
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal           # 成交量
    quote_volume: Decimal     # 成交额


@dataclass(frozen=True)
class VolumeExpansionResult:
    """成交量扩张检测结果
    
    Attributes:
        symbol: 交易标的
        is_expansion: 是否为异常扩张
        expansion_ratio: 扩张比率（当前/均值）
        intensity: 扩张强度 (0-1)，超过threshold倍均值为1.0
        direction: 扩张方向
        mean_volume: 历史平均成交量
        current_volume: 当前成交量
        lookback_periods: 回看周期
        ts_ms: 时间戳
    """
    symbol: str
    is_expansion: bool
    expansion_ratio: Optional[Decimal]
    intensity: Decimal
    direction: VolumeDirection
    mean_volume: Optional[Decimal]
    current_volume: Optional[Decimal]
    lookback_periods: int
    ts_ms: int


@dataclass(frozen=True)
class VolatilityCompressionResult:
    """波动率压缩检测结果
    
    Attributes:
        symbol: 交易标的
        is_compression: 是否为异常压缩
        compression_ratio: 压缩比率（当前波动率/历史均值）
        breakout_direction: 预期突破方向（基于成交量和价格趋势综合判断）
                          - EXPANSION: 向上突破概率大（量价齐升）
                          - CONTRACTION: 向下突破概率大（价跌量缩）
                          - NORMAL: 趋势不明
        current_atr: 当前ATR值
        mean_atr: 历史平均ATR值
        lookback_periods: 回看周期
        ts_ms: 时间戳
    """
    symbol: str
    is_compression: bool
    compression_ratio: Optional[Decimal]
    breakout_direction: VolumeDirection  # 预期突破方向
    current_atr: Optional[Decimal]
    mean_atr: Optional[Decimal]
    lookback_periods: int
    ts_ms: int


# ==================== 辅助计算函数 ====================

def _calculate_atr(
    high_prices: List[Decimal],
    low_prices: List[Decimal],
    close_prices: List[Decimal],
    period: int
) -> Optional[Decimal]:
    """
    计算平均真实波幅 (ATR)
    
    ATR = (1/n) * Σ TR_t
    其中 TR_t = max(H_t - L_t, |H_t - C_{t-1}|, |L_t - C_{t-1}|)
    
    Args:
        high_prices: 最高价序列
        low_prices: 最低价序列
        close_prices: 收盘价序列
        period: ATR周期
        
    Returns:
        ATR值
    """
    if len(high_prices) < period + 1 or len(low_prices) < period + 1 or len(close_prices) < period + 1:
        return None
    
    if len(high_prices) != len(low_prices) or len(high_prices) != len(close_prices):
        return None
    
    true_ranges: List[Decimal] = []
    
    for i in range(1, len(high_prices)):
        high = high_prices[i]
        low = low_prices[i]
        prev_close = close_prices[i - 1]
        
        tr1 = high - low
        tr2 = abs(high - prev_close)
        tr3 = abs(low - prev_close)
        
        true_range = max(tr1, tr2, tr3)
        true_ranges.append(true_range)
    
    if len(true_ranges) < period:
        return None
    
    # 取最近period个TR计算均值
    recent_tr = true_ranges[-period:]
    atr = sum(recent_tr) / Decimal(str(period))
    
    return atr.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def _calculate_volume_stats(
    volumes: List[Decimal],
    period: int
) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """
    计算成交量统计信息
    
    Args:
        volumes: 成交量序列
        period: 统计周期
        
    Returns:
        (均值, 标准差)
    """
    if len(volumes) < period:
        return None, None
    
    recent_volumes = volumes[-period:]
    mean_vol = sum(recent_volumes) / Decimal(str(period))
    
    variance = sum((v - mean_vol) ** 2 for v in recent_volumes) / Decimal(str(period))
    std_vol = variance.sqrt()
    
    return (
        mean_vol.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP),
        std_vol.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP),
    )


# ==================== 信号计算器 ====================

class VolumeExpansion:
    """
    成交量扩张检测器
    
    检测成交量异常放大情况：
    - 计算历史成交量均值
    - 当前成交量超过threshold倍均值为异常扩张
    - 扩张强度 = min(expansion_ratio / threshold, 1.0)
    
    参数:
        lookback_periods: 回看周期，默认20
        threshold: 扩张阈值（倍均值的倍数），默认2.0
    """
    
    DEFAULT_LOOKBACK_PERIODS = 20
    DEFAULT_THRESHOLD = Decimal("2.0")
    
    @staticmethod
    def compute(
        symbol: str,
        volume_samples: List[VolumeSample],
        lookback_periods: int = DEFAULT_LOOKBACK_PERIODS,
        threshold: Optional[Decimal] = None,
        ts_ms: Optional[int] = None,
    ) -> VolumeExpansionResult:
        """
        检测成交量扩张
        
        Args:
            symbol: 交易标的
            volume_samples: 成交量样本序列（按时间升序，至少需要lookback_periods个样本）
            lookback_periods: 回看周期
            threshold: 扩张阈值（当前成交量超过均值的threshold倍认为异常）
            ts_ms: 当前时间戳
            
        Returns:
            VolumeExpansionResult: 成交量扩张检测结果
        """
        if threshold is None:
            threshold = VolumeExpansion.DEFAULT_THRESHOLD
        
        if lookback_periods <= 0 or threshold <= Decimal("0"):
            return VolumeExpansionResult(
                symbol=symbol,
                is_expansion=False,
                expansion_ratio=None,
                intensity=Decimal("0"),
                direction=VolumeDirection.NORMAL,
                mean_volume=None,
                current_volume=None,
                lookback_periods=lookback_periods,
                ts_ms=ts_ms or 0,
            )
        
        if len(volume_samples) < lookback_periods:
            return VolumeExpansionResult(
                symbol=symbol,
                is_expansion=False,
                expansion_ratio=None,
                intensity=Decimal("0"),
                direction=VolumeDirection.NORMAL,
                mean_volume=None,
                current_volume=None,
                lookback_periods=lookback_periods,
                ts_ms=ts_ms or (volume_samples[-1].ts_ms if volume_samples else 0),
            )
        
        # 获取成交量序列
        volumes = [s.volume for s in volume_samples]
        
        # 计算历史统计
        mean_volume, std_volume = _calculate_volume_stats(volumes, lookback_periods)
        
        if mean_volume is None or mean_volume == Decimal("0"):
            return VolumeExpansionResult(
                symbol=symbol,
                is_expansion=False,
                expansion_ratio=None,
                intensity=Decimal("0"),
                direction=VolumeDirection.NORMAL,
                mean_volume=None,
                current_volume=volumes[-1],
                lookback_periods=lookback_periods,
                ts_ms=ts_ms or volume_samples[-1].ts_ms,
            )
        
        # 当前成交量
        current_volume = volumes[-1]
        
        # 计算扩张比率
        expansion_ratio = (current_volume / mean_volume).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        )
        
        # 判断是否异常扩张
        is_expansion = current_volume >= mean_volume * threshold
        
        # 计算扩张强度
        if is_expansion:
            # 强度 = min(expansion_ratio / threshold, 1.0)
            intensity = min(expansion_ratio / threshold, Decimal("1.0"))
            intensity = intensity.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        else:
            intensity = Decimal("0")
        
        # 确定方向（放量/缩量/正常）
        if expansion_ratio > threshold:
            direction = VolumeDirection.EXPANSION
        elif expansion_ratio < Decimal("1.0") / threshold:
            direction = VolumeDirection.CONTRACTION
        else:
            direction = VolumeDirection.NORMAL
        
        current_ts = ts_ms if ts_ms is not None else volume_samples[-1].ts_ms
        
        return VolumeExpansionResult(
            symbol=symbol,
            is_expansion=is_expansion,
            expansion_ratio=expansion_ratio,
            intensity=intensity,
            direction=direction,
            mean_volume=mean_volume,
            current_volume=current_volume,
            lookback_periods=lookback_periods,
            ts_ms=current_ts,
        )


class VolatilityCompression:
    """
    波动率压缩检测器
    
    检测波动率异常收缩情况：
    - 使用ATR作为波动率指标
    - 当前ATR低于compression_threshold倍历史均值为异常压缩
    - 压缩后通常伴随剧烈突破
    
    参数:
        lookback_periods: 回看周期，默认20
        compression_threshold: 压缩阈值（倍均值的倍数），默认0.5（50%）
    """
    
    DEFAULT_LOOKBACK_PERIODS = 20
    DEFAULT_COMPRESSION_THRESHOLD = Decimal("0.5")  # 50%
    
    @staticmethod
    def compute(
        symbol: str,
        price_volume_samples: List[PriceVolumeSample],
        lookback_periods: int = DEFAULT_LOOKBACK_PERIODS,
        compression_threshold: Optional[Decimal] = None,
        ts_ms: Optional[int] = None,
    ) -> VolatilityCompressionResult:
        """
        检测波动率压缩
        
        Args:
            symbol: 交易标的
            price_volume_samples: 价量样本序列（按时间升序，至少需要lookback_periods+1个样本）
            lookback_periods: 回看周期
            compression_threshold: 压缩阈值（当前ATR低于均值的threshold倍认为异常）
            ts_ms: 当前时间戳
            
        Returns:
            VolatilityCompressionResult: 波动率压缩检测结果
        """
        if compression_threshold is None:
            compression_threshold = VolatilityCompression.DEFAULT_COMPRESSION_THRESHOLD
        
        if lookback_periods <= 0 or compression_threshold <= Decimal("0"):
            return VolatilityCompressionResult(
                symbol=symbol,
                is_compression=False,
                compression_ratio=None,
                breakout_direction=VolumeDirection.NORMAL,
                current_atr=None,
                mean_atr=None,
                lookback_periods=lookback_periods,
                ts_ms=ts_ms or 0,
            )
        
        # 需要至少 lookback_periods + 1 个样本来计算ATR变化
        if len(price_volume_samples) < lookback_periods + 1:
            return VolatilityCompressionResult(
                symbol=symbol,
                is_compression=False,
                compression_ratio=None,
                breakout_direction=VolumeDirection.NORMAL,
                current_atr=None,
                mean_atr=None,
                lookback_periods=lookback_periods,
                ts_ms=ts_ms or (price_volume_samples[-1].ts_ms if price_volume_samples else 0),
            )
        
        # 提取价格序列
        high_prices = [p.high_price for p in price_volume_samples]
        low_prices = [p.low_price for p in price_volume_samples]
        close_prices = [p.close_price for p in price_volume_samples]
        
        # 计算当前ATR（使用最近lookback_periods个样本）
        current_atr = _calculate_atr(high_prices, low_prices, close_prices, lookback_periods)
        
        if current_atr is None:
            return VolatilityCompressionResult(
                symbol=symbol,
                is_compression=False,
                compression_ratio=None,
                breakout_direction=VolumeDirection.NORMAL,
                current_atr=None,
                mean_atr=None,
                lookback_periods=lookback_periods,
                ts_ms=ts_ms or price_volume_samples[-1].ts_ms,
            )
        
        # 计算历史ATR序列
        atr_values: List[Decimal] = []
        for i in range(lookback_periods, len(price_volume_samples) + 1):
            segment_high = high_prices[:i]
            segment_low = low_prices[:i]
            segment_close = close_prices[:i]
            atr = _calculate_atr(segment_high, segment_low, segment_close, lookback_periods)
            if atr is not None:
                atr_values.append(atr)
        
        if len(atr_values) < 2:
            # 无法计算历史ATR比较
            mean_atr = current_atr
        else:
            # 使用历史ATR（不包括当前）计算均值
            historical_atr = atr_values[:-1]
            mean_atr = sum(historical_atr) / Decimal(str(len(historical_atr)))
            mean_atr = mean_atr.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        
        if mean_atr == Decimal("0"):
            return VolatilityCompressionResult(
                symbol=symbol,
                is_compression=False,
                compression_ratio=None,
                breakout_direction=VolumeDirection.NORMAL,
                current_atr=current_atr,
                mean_atr=mean_atr,
                lookback_periods=lookback_periods,
                ts_ms=ts_ms or price_volume_samples[-1].ts_ms,
            )
        
        # 计算压缩比率
        compression_ratio = (current_atr / mean_atr).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        )
        
        # 判断是否异常压缩
        is_compression = current_atr <= mean_atr * compression_threshold
        
        # 确定预期突破方向（基于价格趋势）
        breakout_direction = VolumeDirection.NORMAL
        
        if is_compression:
            # 检测最近5根K线的价格趋势来预测突破方向
            recent_samples = price_volume_samples[-5:]
            if len(recent_samples) >= 2:
                price_changes = [
                    recent_samples[i].close_price - recent_samples[i - 1].close_price
                    for i in range(1, len(recent_samples))
                ]
                avg_change = sum(price_changes) / Decimal(str(len(price_changes)))
                
                if avg_change > Decimal("0"):
                    breakout_direction = VolumeDirection.EXPANSION  # 向上突破概率大（量价齐升）
                else:
                    breakout_direction = VolumeDirection.CONTRACTION  # 向下突破概率大（价跌量缩）
        
        current_ts = ts_ms if ts_ms is not None else price_volume_samples[-1].ts_ms
        
        return VolatilityCompressionResult(
            symbol=symbol,
            is_compression=is_compression,
            compression_ratio=compression_ratio,
            breakout_direction=breakout_direction,
            current_atr=current_atr,
            mean_atr=mean_atr,
            lookback_periods=lookback_periods,
            ts_ms=current_ts,
        )
