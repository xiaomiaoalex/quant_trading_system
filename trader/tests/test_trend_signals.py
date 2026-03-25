"""
Trend Signals Tests - 趋势信号单元测试
======================================

测试趋势信号计算器的功能：
- EMACrossover: EMA交叉信号
- PriceMomentum: 价格动量
- BollingerBandPosition: 布林带位置
"""

import pytest
from decimal import Decimal
from trader.core.domain.signals.trend_signals import (
    TrendDirection,
    PriceSample,
    EMACrossover,
    EMACrossoverResult,
    PriceMomentum,
    PriceMomentumResult,
    BollingerBandPosition,
    BollingerBandResult,
)


# ==================== 测试数据生成辅助函数 ====================

def make_price_sample(
    ts_ms: int,
    close_price: float,
    open_price: float = None,
    high_price: float = None,
    low_price: float = None,
) -> PriceSample:
    """创建价格样本"""
    if open_price is None:
        open_price = close_price
    if high_price is None:
        high_price = close_price * 1.01
    if low_price is None:
        low_price = close_price * 0.99
    return PriceSample(
        ts_ms=ts_ms,
        open_price=Decimal(str(open_price)),
        high_price=Decimal(str(high_price)),
        low_price=Decimal(str(low_price)),
        close_price=Decimal(str(close_price)),
    )


def make_price_samples(
    prices: list[float],
    start_ts_ms: int = 1000000000000,
    interval_ms: int = 60000,
) -> list[PriceSample]:
    """创建价格样本序列"""
    return [
        make_price_sample(ts_ms=start_ts_ms + i * interval_ms, close_price=p)
        for i, p in enumerate(prices)
    ]


# ==================== TestEMACrossover ====================

class TestEMACrossover:
    """EMACrossover 单元测试"""

    def test_golden_cross_bullish(self):
        """测试黄金交叉（看涨信号）
        
        黄金交叉的形成：
        - 下跌趋势中，快线EMA在下方
        - 行情反转，快线从下往上穿越慢线
        """
        # 构建一个先跌后涨的序列
        # 前半部分下跌并震荡筑底，使得快线EMA在慢线下方
        # 最后大幅上涨，使得快线EMA从下往上穿越
        prices = make_price_samples([
            100, 95, 90, 85, 80,  # 快速下跌
            75, 70, 65, 60, 55,  # 继续下跌
            50, 48, 46, 44, 42,  # 底部震荡筑底
            200                    # 强势反弹 - 快线穿越慢线向上
        ])
        
        result = EMACrossover.compute(
            symbol="BTCUSDT",
            prices=prices,
            fast_period=5,
            slow_period=10,
        )
        
        assert result.symbol == "BTCUSDT"
        assert result.is_valid is True
        assert result.fast_ema is not None
        assert result.slow_ema is not None
        # 验证EMA计算结果存在且合理
        assert result.fast_ema > Decimal("0")
        assert result.slow_ema > Decimal("0")
        # 验证黄金交叉发生
        assert result.crossover is True, "应该发生黄金交叉"
        assert result.direction == TrendDirection.BULLISH, "交叉方向应该是看涨"

    def test_death_cross_bearish(self):
        """测试死叉（看跌信号）
        
        死叉的形成：
        - 上涨趋势中，快线EMA在上方
        - 行情反转，快线从上往下穿越慢线
        """
        # 构建一个先涨后跌的序列
        # 前半部分上涨并高位震荡，使得快线EMA在慢线上方
        # 最后大幅下跌，使得快线EMA从上往下穿越
        prices = make_price_samples([
            50, 55, 60, 65, 70,  # 上涨
            75, 80, 85, 90, 95,  # 继续上涨
            100, 102, 104, 106, 108,  # 高位震荡
            5                       # 闪崩 - 快线穿越慢线向下
        ])
        
        result = EMACrossover.compute(
            symbol="BTCUSDT",
            prices=prices,
            fast_period=5,
            slow_period=10,
        )
        
        assert result.symbol == "BTCUSDT"
        assert result.is_valid is True
        assert result.fast_ema is not None
        assert result.slow_ema is not None
        # 验证EMA计算结果存在且合理
        assert result.fast_ema > Decimal("0")
        assert result.slow_ema > Decimal("0")
        # 验证死叉发生
        assert result.crossover is True, "应该发生死叉"
        assert result.direction == TrendDirection.BEARISH, "交叉方向应该是看跌"

    def test_no_crossover(self):
        """测试无交叉情况"""
        # 构建平稳上涨趋势（无明显交叉）
        prices = make_price_samples([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113])
        
        result = EMACrossover.compute(
            symbol="BTCUSDT",
            prices=prices,
            fast_period=5,
            slow_period=10,
        )
        
        assert result.symbol == "BTCUSDT"
        assert result.is_valid is True
        assert result.fast_ema is not None
        assert result.slow_ema is not None
        assert result.crossover is False
        assert result.direction == TrendDirection.NONE

    def test_insufficient_data(self):
        """测试数据不足情况"""
        # 数据少于 slow_period + 1
        prices = make_price_samples([100, 102, 105, 107, 110])
        
        result = EMACrossover.compute(
            symbol="BTCUSDT",
            prices=prices,
            fast_period=5,
            slow_period=10,
        )
        
        assert result.is_valid is False
        assert result.crossover is False
        assert result.direction == TrendDirection.NONE

    def test_invalid_parameters(self):
        """测试无效参数"""
        prices = make_price_samples([100, 102, 105, 107, 110, 115, 120])
        
        # slow_period <= fast_period
        result = EMACrossover.compute(
            symbol="BTCUSDT",
            prices=prices,
            fast_period=10,
            slow_period=5,
        )
        
        assert result.is_valid is False
        assert result.fast_ema is None
        assert result.slow_ema is None

    def test_ema_values_correctness(self):
        """测试EMA计算正确性"""
        # 固定价格序列
        prices = make_price_samples([100.0] * 30)
        
        result = EMACrossover.compute(
            symbol="BTCUSDT",
            prices=prices,
            fast_period=10,
            slow_period=20,
        )
        
        assert result.is_valid is True
        # 固定价格时，EMA应该等于该价格
        assert result.fast_ema == Decimal("100")
        assert result.slow_ema == Decimal("100")


# ==================== TestPriceMomentum ====================

class TestPriceMomentum:
    """PriceMomentum 单元测试"""

    def test_bullish_momentum(self):
        """测试看涨动量"""
        # 价格从90上涨到115（超过27%涨幅），需要 lookback_periods+1 = 15个样本
        prices = make_price_samples([90, 92, 94, 96, 98, 100, 102, 104, 106, 108, 110, 112, 114, 115, 118])
        
        result = PriceMomentum.compute(
            symbol="BTCUSDT",
            prices=prices,
            lookback_periods=14,
        )
        
        assert result.symbol == "BTCUSDT"
        assert result.momentum is not None
        assert result.direction == TrendDirection.BULLISH
        assert result.momentum > Decimal("0")
        # 14周期从90到115的变化
        assert result.momentum > Decimal("10")  # 至少10%涨幅

    def test_bearish_momentum(self):
        """测试看跌动量"""
        # 价格从115下跌到90（超过21%跌幅），需要 lookback_periods+1 = 15个样本
        prices = make_price_samples([115, 113, 111, 109, 107, 105, 103, 101, 99, 97, 95, 93, 91, 90, 88])
        
        result = PriceMomentum.compute(
            symbol="BTCUSDT",
            prices=prices,
            lookback_periods=14,
        )
        
        assert result.symbol == "BTCUSDT"
        assert result.momentum is not None
        assert result.direction == TrendDirection.BEARISH
        assert result.momentum < Decimal("0")

    def test_strong_momentum(self):
        """测试强势动量"""
        # 价格大幅上涨（超过5%阈值），需要 lookback_periods+1 = 15个样本
        prices = make_price_samples([85, 87, 89, 91, 93, 95, 97, 99, 101, 103, 105, 107, 109, 112, 115])
        
        result = PriceMomentum.compute(
            symbol="BTCUSDT",
            prices=prices,
            lookback_periods=14,
            strong_threshold=Decimal("5.0"),
        )
        
        assert result.is_strong is True
        assert abs(result.momentum) >= Decimal("5.0")

    def test_weak_momentum(self):
        """测试弱势动量"""
        # 价格小幅波动，需要 lookback_periods+1 = 15个样本
        prices = make_price_samples([100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 102])
        
        result = PriceMomentum.compute(
            symbol="BTCUSDT",
            prices=prices,
            lookback_periods=14,
            strong_threshold=Decimal("5.0"),
        )
        
        assert result.is_strong is False
        assert abs(result.momentum) < Decimal("5.0")

    def test_no_momentum(self):
        """测试无动量（价格不变）"""
        prices = make_price_samples([100.0] * 15)
        
        result = PriceMomentum.compute(
            symbol="BTCUSDT",
            prices=prices,
            lookback_periods=14,
        )
        
        assert result.momentum == Decimal("0")
        assert result.direction == TrendDirection.NONE

    def test_insufficient_data(self):
        """测试数据不足"""
        prices = make_price_samples([100, 102, 105, 107, 110])
        
        result = PriceMomentum.compute(
            symbol="BTCUSDT",
            prices=prices,
            lookback_periods=14,
        )
        
        assert result.momentum is None
        assert result.direction == TrendDirection.NONE

    def test_invalid_period(self):
        """测试无效周期"""
        prices = make_price_samples([100, 102, 105, 107, 110, 115, 120])
        
        result = PriceMomentum.compute(
            symbol="BTCUSDT",
            prices=prices,
            lookback_periods=0,
        )
        
        assert result.momentum is None


# ==================== TestBollingerBandPosition ====================

class TestBollingerBandPosition:
    """BollingerBandPosition 单元测试"""

    def test_position_at_upper_band(self):
        """测试价格接近上轨"""
        # 构建价格上涨到接近布林带上轨
        # 价格为100，波动率低，所以上下轨接近价格
        prices = make_price_samples([100] * 25)
        # 最后几根价格上涨
        prices[-3] = make_price_sample(ts_ms=prices[-3].ts_ms, close_price=105)
        prices[-2] = make_price_sample(ts_ms=prices[-2].ts_ms, close_price=107)
        prices[-1] = make_price_sample(ts_ms=prices[-1].ts_ms, close_price=110)
        
        result = BollingerBandPosition.compute(
            symbol="BTCUSDT",
            prices=prices,
            period=20,
            std_multiplier=Decimal("2.0"),
        )
        
        assert result.symbol == "BTCUSDT"
        assert result.upper_band is not None
        assert result.middle_band is not None
        assert result.lower_band is not None
        assert result.bandwidth is not None
        # 价格在上轨附近时，position应该接近+1
        if result.position is not None:
            assert result.position > Decimal("0")

    def test_position_at_lower_band(self):
        """测试价格接近下轨"""
        # 构建价格下跌到接近布林带下轨
        prices = make_price_samples([100] * 25)
        # 最后几根价格下跌
        prices[-3] = make_price_sample(ts_ms=prices[-3].ts_ms, close_price=95)
        prices[-2] = make_price_sample(ts_ms=prices[-2].ts_ms, close_price=93)
        prices[-1] = make_price_sample(ts_ms=prices[-1].ts_ms, close_price=90)
        
        result = BollingerBandPosition.compute(
            symbol="BTCUSDT",
            prices=prices,
            period=20,
            std_multiplier=Decimal("2.0"),
        )
        
        assert result.symbol == "BTCUSDT"
        if result.position is not None:
            assert result.position < Decimal("0")

    def test_position_at_center(self):
        """测试价格在中心位置"""
        # 固定价格时，中轨等于价格
        prices = make_price_samples([100.0] * 25)
        
        result = BollingerBandPosition.compute(
            symbol="BTCUSDT",
            prices=prices,
            period=20,
            std_multiplier=Decimal("2.0"),
        )
        
        assert result.symbol == "BTCUSDT"
        # 固定价格，标准差为0，带宽为0，position应为0
        assert result.position == Decimal("0")

    def test_position_range(self):
        """测试position范围约束"""
        # 大幅波动价格
        prices = make_price_samples([100, 110, 90, 105, 95, 115, 85, 120, 80, 125, 75, 130, 70, 135, 65, 140, 60, 145, 55, 150, 50, 155, 45, 160, 40])
        
        result = BollingerBandPosition.compute(
            symbol="BTCUSDT",
            prices=prices,
            period=20,
            std_multiplier=Decimal("2.0"),
        )
        
        if result.position is not None:
            # Position应该在 [-1, 1] 范围内
            assert result.position >= Decimal("-1.0")
            assert result.position <= Decimal("1.0")

    def test_insufficient_data(self):
        """测试数据不足"""
        prices = make_price_samples([100, 102, 105, 107, 110])
        
        result = BollingerBandPosition.compute(
            symbol="BTCUSDT",
            prices=prices,
            period=20,
        )
        
        # 数据不足时，所有值都为None
        assert result.position is None
        assert result.middle_band is None
        assert result.upper_band is None
        assert result.lower_band is None

    def test_invalid_period(self):
        """测试无效周期"""
        prices = make_price_samples([100, 102, 105, 107, 110, 115, 120])
        
        result = BollingerBandPosition.compute(
            symbol="BTCUSDT",
            prices=prices,
            period=0,
        )
        
        assert result.position is None

    def test_bandwidth_positive(self):
        """测试带宽为正"""
        prices = make_price_samples([100, 102, 105, 107, 110, 115, 120, 118, 116, 114, 112, 110, 108, 106, 104, 102, 100, 98, 96, 94, 92, 90, 88, 86, 84])
        
        result = BollingerBandPosition.compute(
            symbol="BTCUSDT",
            prices=prices,
            period=20,
            std_multiplier=Decimal("2.0"),
        )
        
        assert result.bandwidth is not None
        assert result.bandwidth > Decimal("0")
        assert result.upper_band > result.middle_band
        assert result.middle_band > result.lower_band


# ==================== TestDataClassImmutability ====================

class TestTrendSignalDataClassImmutability:
    """验证趋势信号数据类的不可变性"""

    def test_price_sample_immutable(self):
        """PriceSample应是不可变的"""
        sample = make_price_sample(ts_ms=1000, close_price=100.0)
        with pytest.raises(AttributeError):
            sample.close_price = Decimal("200")

    def test_ema_result_immutable(self):
        """EMACrossoverResult应是不可变的"""
        prices = make_price_samples([100, 102, 105, 107, 110, 115, 120, 125, 130, 135, 140, 145, 150, 155, 160])
        result = EMACrossover.compute("BTCUSDT", prices, fast_period=5, slow_period=10)
        with pytest.raises(AttributeError):
            result.crossover = False

    def test_momentum_result_immutable(self):
        """PriceMomentumResult应是不可变的"""
        prices = make_price_samples([85, 87, 89, 91, 93, 95, 97, 99, 101, 103, 105, 107, 109, 115])
        result = PriceMomentum.compute("BTCUSDT", prices, lookback_periods=14)
        with pytest.raises(AttributeError):
            result.momentum = Decimal("0")

    def test_bollinger_result_immutable(self):
        """BollingerBandResult应是不可变的"""
        prices = make_price_samples([100] * 25)
        result = BollingerBandPosition.compute("BTCUSDT", prices, period=20)
        with pytest.raises(AttributeError):
            result.position = Decimal("0")


# ==================== TestEdgeCases ====================

class TestTrendSignalEdgeCases:
    """边界条件和错误处理测试"""

    def test_empty_prices_list(self):
        """测试空价格列表"""
        result = EMACrossover.compute("BTCUSDT", [], fast_period=5, slow_period=10)
        assert result.is_valid is False

    def test_single_price(self):
        """测试单个价格"""
        prices = [make_price_sample(ts_ms=1000, close_price=100.0)]
        result = EMACrossover.compute("BTCUSDT", prices, fast_period=5, slow_period=10)
        assert result.is_valid is False

    def test_zero_prices(self):
        """测试价格序列包含零"""
        prices = make_price_samples([100, 0, 105, 107, 110, 115, 120, 125, 130, 135, 140, 145, 150, 155, 160])
        result = EMACrossover.compute("BTCUSDT", prices, fast_period=5, slow_period=10)
        # 零价格可能导致计算问题，应该优雅处理
        assert result is not None

    def test_negative_prices(self):
        """测试负价格"""
        prices = make_price_samples([-100, -102, -105, -107, -110, -115, -120, -125, -130, -135, -140, -145, -150, -155, -160])
        result = EMACrossover.compute("BTCUSDT", prices, fast_period=5, slow_period=10)
        # 负价格应该被处理
        assert result is not None

    def test_decimal_precision(self):
        """测试Decimal精度"""
        prices = make_price_samples([100.123456789, 100.234567891, 100.345678912])
        result = PriceMomentum.compute("BTCUSDT", prices, lookback_periods=2)
        assert result.momentum is not None
        # 验证Decimal精度保持
        if result.momentum is not None:
            # 应该有合理的精度
            assert result.momentum == result.momentum.quantize(Decimal("0.00000001"))

    def test_custom_ts_ms(self):
        """测试自定义时间戳"""
        prices = make_price_samples([100, 102, 105, 107, 110, 115, 120, 125, 130, 135, 140, 145, 150, 155, 160])
        custom_ts = 9999999999999
        result = EMACrossover.compute("BTCUSDT", prices, fast_period=5, slow_period=10, ts_ms=custom_ts)
        assert result.ts_ms == custom_ts


# ==================== TestEMACalculation ====================

class TestEMACalculation:
    """EMA计算专项测试"""

    def test_ema_convergence(self):
        """测试EMA收敛性"""
        # 固定价格应该使EMA收敛到该价格
        prices = make_price_samples([100.0] * 100)
        
        result = EMACrossover.compute(
            symbol="BTCUSDT",
            prices=prices,
            fast_period=10,
            slow_period=20,
        )
        
        assert abs(result.fast_ema - Decimal("100")) < Decimal("0.0001")
        assert abs(result.slow_ema - Decimal("100")) < Decimal("0.0001")

    def test_ema_with_uptrend(self):
        """测试上涨趋势中的EMA"""
        # 指数增长价格
        prices = make_price_samples([100 * (1.02 ** i) for i in range(30)])
        
        result = EMACrossover.compute(
            symbol="BTCUSDT",
            prices=prices,
            fast_period=10,
            slow_period=20,
        )
        
        assert result.fast_ema > result.slow_ema  # 上涨趋势中快线应该高于慢线

    def test_ema_with_downtrend(self):
        """测试下跌趋势中的EMA"""
        # 指数下跌价格
        prices = make_price_samples([100 * (0.98 ** i) for i in range(30)])
        
        result = EMACrossover.compute(
            symbol="BTCUSDT",
            prices=prices,
            fast_period=10,
            slow_period=20,
        )
        
        assert result.fast_ema < result.slow_ema  # 下跌趋势中快线应该低于慢线
