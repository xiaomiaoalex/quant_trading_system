"""
Trend Signals - 趋势信号计算
================================

Core Plane 纯函数实现，无任何IO操作。

趋势信号用于检测市场价格走势的各类特征：
- EMA交叉信号: 快慢EMA交叉产生买卖信号（黄金交叉/死叉）
- 价格动量: 基于N周期价格变化率
- 布林带位置: 价格在布林带中的相对位置
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional, List


class TrendDirection(Enum):
    """趋势方向"""
    NONE = "NONE"
    BULLISH = "BULLISH"   # 看涨
    BEARISH = "BEARISH"   # 看跌


# ==================== 数据结构 ====================

@dataclass(frozen=True)
class PriceSample:
    """价格样本"""
    ts_ms: int
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal


@dataclass(frozen=True)
class EMACrossoverResult:
    """EMA交叉信号结果
    
    Attributes:
        symbol: 交易标的
        fast_ema: 快线EMA值
        slow_ema: 慢线EMA值
        crossover: 是否发生交叉
        direction: 交叉方向 (BULLISH=黄金交叉, BEARISH=死叉, NONE=无交叉)
        is_valid: 是否为有效信号（需要足够的历史数据）
        ts_ms: 时间戳
    """
    symbol: str
    fast_ema: Optional[Decimal]
    slow_ema: Optional[Decimal]
    crossover: bool
    direction: TrendDirection
    is_valid: bool
    ts_ms: int


@dataclass(frozen=True)
class PriceMomentumResult:
    """价格动量结果
    
    Attributes:
        symbol: 交易标的
        momentum: 动量值（价格变化率，百分比）
        direction: 动量方向
        is_strong: 是否为强势动量
        lookback_periods: 回看周期
        ts_ms: 时间戳
    """
    symbol: str
    momentum: Optional[Decimal]
    direction: TrendDirection
    is_strong: bool
    lookback_periods: int
    ts_ms: int


@dataclass(frozen=True)
class BollingerBandResult:
    """布林带位置结果
    
    Attributes:
        symbol: 交易标的
        position: 布林带位置值，范围 [-1.0, 1.0]，以标准差为单位
                  0为中心线，> 0 表示价格高于中轨（偏强），< 0 表示价格低于中轨（偏弱）
        upper_band: 上轨价格
        middle_band: 中轨价格（MA）
        lower_band: 下轨价格
        bandwidth: 布林带宽度（2倍标准差）
        ts_ms: 时间戳
    """
    symbol: str
    position: Optional[Decimal]
    upper_band: Optional[Decimal]
    middle_band: Optional[Decimal]
    lower_band: Optional[Decimal]
    bandwidth: Optional[Decimal]
    ts_ms: int


# ==================== 辅助计算函数 ====================

def _calculate_ema(prices: List[Decimal], period: int) -> Optional[Decimal]:
    """
    计算指数移动平均线 (EMA)
    
    EMA_t = (Close_t * k) + (EMA_{t-1} * (1 - k))
    其中 k = 2 / (period + 1)
    
    Args:
        prices: 价格序列（按时间升序）
        period: EMA周期
        
    Returns:
        最新时刻的EMA值，如果数据不足返回None
    """
    if len(prices) < period or period <= 0:
        return None
    
    # 计算平滑系数 k
    k = Decimal("2") / Decimal(str(period + 1))
    one_minus_k = Decimal("1") - k
    
    # 初始EMA使用简单移动平均
    sma = sum(prices[:period]) / Decimal(str(period))
    ema = sma
    
    # 迭代计算后续EMA
    for price in prices[period:]:
        ema = price * k + ema * one_minus_k
    
    return ema.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def _calculate_ema_series(prices: List[Decimal], period: int) -> List[Optional[Decimal]]:
    """
    计算EMA序列
    
    Args:
        prices: 价格序列
        period: EMA周期
        
    Returns:
        EMA序列，与输入价格等长，不足周期处为None
    """
    if len(prices) < period:
        return [None] * len(prices)
    
    result: List[Optional[Decimal]] = [None] * (period - 1)
    
    # 初始SMA
    sma = sum(prices[:period]) / Decimal(str(period))
    result.append(sma)
    
    # 计算平滑系数
    k = Decimal("2") / Decimal(str(period + 1))
    one_minus_k = Decimal("1") - k
    
    # 迭代计算
    ema = sma
    for i in range(period, len(prices)):
        ema = prices[i] * k + ema * one_minus_k
        result.append(ema.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP))
    
    return result


def _calculate_std(prices: List[Decimal], period: int) -> Optional[Decimal]:
    """
    计算标准差（样本标准差，ddof=1）
    
    Args:
        prices: 价格序列
        period: 计算周期
        
    Returns:
        标准差值
    """
    if len(prices) < period:
        return None
    
    window_prices = prices[-period:]
    mean = sum(window_prices) / Decimal(str(period))
    
    # 使用样本标准差（ddof=1），符合布林带标准计算方式
    variance = sum((p - mean) ** 2 for p in window_prices) / Decimal(str(period - 1))
    std = variance.sqrt()
    
    return std.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


# ==================== 信号计算器 ====================

class EMACrossover:
    """
    EMA交叉信号计算器
    
    检测快慢EMA的交叉情况：
    - 黄金交叉 (BULLISH): 快线从下往上穿越慢线 → 看涨信号
    - 死叉 (BEARISH): 快线从上往下穿越慢线 → 看跌信号
    
    参数:
        fast_period: 快线周期，默认10
        slow_period: 慢线周期，默认20
    """
    
    DEFAULT_FAST_PERIOD = 10
    DEFAULT_SLOW_PERIOD = 20
    
    @staticmethod
    def compute(
        symbol: str,
        prices: List[PriceSample],
        fast_period: int = DEFAULT_FAST_PERIOD,
        slow_period: int = DEFAULT_SLOW_PERIOD,
        ts_ms: Optional[int] = None,
    ) -> EMACrossoverResult:
        """
        计算EMA交叉信号
        
        Args:
            symbol: 交易标的
            prices: 价格时序数据（按时间升序，至少需要slow_period+1个样本）
            fast_period: 快线EMA周期
            slow_period: 慢线EMA周期
            ts_ms: 当前时间戳
            
        Returns:
            EMACrossoverResult: EMA交叉信号结果
        """
        # 验证输入
        if slow_period <= 0 or fast_period <= 0:
            return EMACrossoverResult(
                symbol=symbol,
                fast_ema=None,
                slow_ema=None,
                crossover=False,
                direction=TrendDirection.NONE,
                is_valid=False,
                ts_ms=ts_ms or 0,
            )
        
        if slow_period <= fast_period:
            # 慢线周期必须大于快线
            return EMACrossoverResult(
                symbol=symbol,
                fast_ema=None,
                slow_ema=None,
                crossover=False,
                direction=TrendDirection.NONE,
                is_valid=False,
                ts_ms=ts_ms or 0,
            )
        
        # 需要至少 slow_period + 1 个样本才能检测交叉
        min_required = slow_period + 1
        if len(prices) < min_required:
            # 数据不足，计算但不检测交叉
            close_prices = [p.close_price for p in prices]
            fast_ema = _calculate_ema(close_prices, fast_period)
            slow_ema = _calculate_ema(close_prices, slow_period)
            
            return EMACrossoverResult(
                symbol=symbol,
                fast_ema=fast_ema,
                slow_ema=slow_ema,
                crossover=False,
                direction=TrendDirection.NONE,
                is_valid=False,
                ts_ms=ts_ms or (prices[-1].ts_ms if prices else 0),
            )
        
        # 获取收盘价序列
        close_prices = [p.close_price for p in prices]
        
        # 计算当前EMA
        fast_ema = _calculate_ema(close_prices, fast_period)
        slow_ema = _calculate_ema(close_prices, slow_period)
        
        # 计算前一时刻EMA（用于检测交叉）
        close_prices_prev = close_prices[:-1]
        fast_ema_prev = _calculate_ema(close_prices_prev, fast_period)
        slow_ema_prev = _calculate_ema(close_prices_prev, slow_period)
        
        current_ts = ts_ms if ts_ms is not None else prices[-1].ts_ms
        
        # 检测交叉
        crossover = False
        direction = TrendDirection.NONE
        
        if fast_ema is not None and slow_ema is not None:
            if fast_ema_prev is not None and slow_ema_prev is not None:
                # 黄金交叉: 快线从下往上穿越
                if fast_ema_prev <= slow_ema_prev and fast_ema > slow_ema:
                    crossover = True
                    direction = TrendDirection.BULLISH
                # 死叉: 快线从上往下穿越
                elif fast_ema_prev >= slow_ema_prev and fast_ema < slow_ema:
                    crossover = True
                    direction = TrendDirection.BEARISH
        
        return EMACrossoverResult(
            symbol=symbol,
            fast_ema=fast_ema,
            slow_ema=slow_ema,
            crossover=crossover,
            direction=direction,
            is_valid=True,
            ts_ms=current_ts,
        )


class PriceMomentum:
    """
    价格动量计算器
    
    基于N周期价格变化率计算动量：
    - 正动量: 价格上涨趋势
    - 负动量: 价格下跌趋势
    
    参数:
        lookback_periods: 回看周期，默认14
        strong_threshold: 强势动量阈值（百分比），默认5%
    """
    
    DEFAULT_LOOKBACK_PERIODS = 14
    DEFAULT_STRONG_THRESHOLD = Decimal("5.0")  # 5%
    
    @staticmethod
    def compute(
        symbol: str,
        prices: List[PriceSample],
        lookback_periods: int = DEFAULT_LOOKBACK_PERIODS,
        strong_threshold: Optional[Decimal] = None,
        ts_ms: Optional[int] = None,
    ) -> PriceMomentumResult:
        """
        计算价格动量
        
        Args:
            symbol: 交易标的
            prices: 价格时序数据（按时间升序，至少需要lookback_periods+1个样本）
            lookback_periods: 回看周期
            strong_threshold: 强势动量阈值（百分比）
            ts_ms: 当前时间戳
            
        Returns:
            PriceMomentumResult: 价格动量结果
        """
        if strong_threshold is None:
            strong_threshold = PriceMomentum.DEFAULT_STRONG_THRESHOLD
        
        if lookback_periods <= 0:
            return PriceMomentumResult(
                symbol=symbol,
                momentum=None,
                direction=TrendDirection.NONE,
                is_strong=False,
                lookback_periods=lookback_periods,
                ts_ms=ts_ms or 0,
            )
        
        if len(prices) < lookback_periods + 1:
            return PriceMomentumResult(
                symbol=symbol,
                momentum=None,
                direction=TrendDirection.NONE,
                is_strong=False,
                lookback_periods=lookback_periods,
                ts_ms=ts_ms or (prices[-1].ts_ms if prices else 0),
            )
        
        # 获取收盘价
        current_price = prices[-1].close_price
        past_price = prices[-lookback_periods - 1].close_price
        
        # 计算动量（价格变化率，百分比）
        if past_price != Decimal("0"):
            momentum = ((current_price - past_price) / past_price * Decimal("100"))
            momentum = momentum.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        else:
            momentum = Decimal("0")
        
        # 确定方向
        if momentum > Decimal("0"):
            direction = TrendDirection.BULLISH
        elif momentum < Decimal("0"):
            direction = TrendDirection.BEARISH
        else:
            direction = TrendDirection.NONE
        
        # 判断是否为强势动量
        is_strong = abs(momentum) >= strong_threshold
        
        current_ts = ts_ms if ts_ms is not None else prices[-1].ts_ms
        
        return PriceMomentumResult(
            symbol=symbol,
            momentum=momentum,
            direction=direction,
            is_strong=is_strong,
            lookback_periods=lookback_periods,
            ts_ms=current_ts,
        )


class BollingerBandPosition:
    """
    布林带位置计算器
    
    计算价格在布林带中的相对位置：
    - position = (price - middle) / bandwidth
    - 范围 [-1.0, 1.0]，0为中心线
    - 接近 +1.0 表示价格靠近上轨（偏强，可能超买）
    - 接近 -1.0 表示价格靠近下轨（偏弱，可能超卖）
    
    参数:
        period: 布林带周期，默认20
        std_multiplier: 标准差倍数，默认2.0
    """
    
    DEFAULT_PERIOD = 20
    DEFAULT_STD_MULTIPLIER = Decimal("2.0")
    
    @staticmethod
    def compute(
        symbol: str,
        prices: List[PriceSample],
        period: int = DEFAULT_PERIOD,
        std_multiplier: Optional[Decimal] = None,
        ts_ms: Optional[int] = None,
    ) -> BollingerBandResult:
        """
        计算布林带位置
        
        Args:
            symbol: 交易标的
            prices: 价格时序数据（按时间升序，至少需要period个样本）
            period: 布林带周期
            std_multiplier: 标准差倍数
            ts_ms: 当前时间戳
            
        Returns:
            BollingerBandResult: 布林带位置结果
        """
        if std_multiplier is None:
            std_multiplier = BollingerBandPosition.DEFAULT_STD_MULTIPLIER
        
        if period <= 0:
            return BollingerBandResult(
                symbol=symbol,
                position=None,
                upper_band=None,
                middle_band=None,
                lower_band=None,
                bandwidth=None,
                ts_ms=ts_ms or 0,
            )
        
        if len(prices) < period:
            return BollingerBandResult(
                symbol=symbol,
                position=None,
                upper_band=None,
                middle_band=None,
                lower_band=None,
                bandwidth=None,
                ts_ms=ts_ms or (prices[-1].ts_ms if prices else 0),
            )
        
        # 获取收盘价
        close_prices = [p.close_price for p in prices]
        
        # 取最近period个价格计算
        window_prices = close_prices[-period:]
        
        # 计算中轨（MA）
        middle_band = sum(window_prices) / Decimal(str(period))
        middle_band = middle_band.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        
        # 计算标准差
        std_dev = _calculate_std(close_prices, period)
        
        if std_dev is None:
            return BollingerBandResult(
                symbol=symbol,
                position=None,
                upper_band=None,
                middle_band=middle_band,
                lower_band=None,
                bandwidth=None,
                ts_ms=ts_ms or prices[-1].ts_ms,
            )
        
        # 计算上下轨
        upper_band = middle_band + std_dev * std_multiplier
        lower_band = middle_band - std_dev * std_multiplier
        bandwidth = upper_band - lower_band
        
        upper_band = upper_band.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        lower_band = lower_band.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        bandwidth = bandwidth.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        
        # 计算当前位置
        current_price = close_prices[-1]
        
        if bandwidth != Decimal("0"):
            # 归一化位置 = (当前价 - 中轨) / (带宽 / 2)
            # 由于带宽 = 4 * std_dev（当 std_multiplier=2 时），除以2后得到 2 * std_dev
            # 因此 position = ±1 对应价格偏离中轨 ±2σ（约95.45%置信区间）
            # 限制在 [-1, 1] 范围是经验性截断，用于避免极端值影响
            position = ((current_price - middle_band) / (bandwidth / Decimal("2")))
            position = max(Decimal("-1.0"), min(Decimal("1.0"), position))
            position = position.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        else:
            position = Decimal("0")
        
        current_ts = ts_ms if ts_ms is not None else prices[-1].ts_ms
        
        return BollingerBandResult(
            symbol=symbol,
            position=position,
            upper_band=upper_band,
            middle_band=middle_band,
            lower_band=lower_band,
            bandwidth=bandwidth,
            ts_ms=current_ts,
        )
