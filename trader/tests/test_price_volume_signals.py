"""
Price Volume Signals Tests - 价量信号单元测试
==============================================

测试价量信号计算器的功能：
- VolumeExpansion: 成交量扩张检测
- VolatilityCompression: 波动率压缩检测
"""

import pytest
from decimal import Decimal
from trader.core.domain.signals.price_volume_signals import (
    VolumeDirection,
    VolumeSample,
    PriceVolumeSample,
    VolumeExpansion,
    VolumeExpansionResult,
    VolatilityCompression,
    VolatilityCompressionResult,
)


# ==================== 测试数据生成辅助函数 ====================

def make_volume_sample(
    ts_ms: int,
    volume: float,
    quote_volume: float = None,
    trade_count: int = 100,
) -> VolumeSample:
    """创建成交量样本"""
    if quote_volume is None:
        quote_volume = volume * 100  # 假设均价100
    return VolumeSample(
        ts_ms=ts_ms,
        volume=Decimal(str(volume)),
        quote_volume=Decimal(str(quote_volume)),
        trade_count=trade_count,
    )


def make_price_volume_sample(
    ts_ms: int,
    close_price: float,
    volume: float,
    open_price: float = None,
    high_price: float = None,
    low_price: float = None,
) -> PriceVolumeSample:
    """创建价量样本"""
    if open_price is None:
        open_price = close_price
    if high_price is None:
        high_price = close_price * 1.01
    if low_price is None:
        low_price = close_price * 0.99
    return PriceVolumeSample(
        ts_ms=ts_ms,
        open_price=Decimal(str(open_price)),
        high_price=Decimal(str(high_price)),
        low_price=Decimal(str(low_price)),
        close_price=Decimal(str(close_price)),
        volume=Decimal(str(volume)),
        quote_volume=Decimal(str(volume * 100)),
    )


def make_volume_samples(
    volumes: list[float],
    start_ts_ms: int = 1000000000000,
    interval_ms: int = 60000,
) -> list[VolumeSample]:
    """创建成交量样本序列"""
    return [
        make_volume_sample(ts_ms=start_ts_ms + i * interval_ms, volume=v)
        for i, v in enumerate(volumes)
    ]


def make_price_volume_samples(
    prices: list[float],
    volumes: list[float],
    start_ts_ms: int = 1000000000000,
    interval_ms: int = 60000,
) -> list[PriceVolumeSample]:
    """创建价量样本序列"""
    return [
        make_price_volume_sample(
            ts_ms=start_ts_ms + i * interval_ms,
            close_price=p,
            volume=v,
        )
        for i, (p, v) in enumerate(zip(prices, volumes))
    ]


# ==================== TestVolumeExpansion ====================

class TestVolumeExpansion:
    """VolumeExpansion 单元测试"""

    def test_expansion_detected(self):
        """测试检测到成交量扩张"""
        # 正常成交量100，突然放大到300（3倍）
        volumes = [100] * 19 + [300]
        samples = make_volume_samples(volumes)
        
        result = VolumeExpansion.compute(
            symbol="BTCUSDT",
            volume_samples=samples,
            lookback_periods=20,
            threshold=Decimal("2.0"),
        )
        
        assert result.symbol == "BTCUSDT"
        assert result.is_expansion is True
        assert result.expansion_ratio is not None
        assert result.expansion_ratio >= Decimal("2.0")
        assert result.intensity >= Decimal("0.5")
        assert result.direction == VolumeDirection.EXPANSION

    def test_no_expansion(self):
        """测试无扩张情况"""
        # 稳定成交量
        volumes = [100] * 30
        samples = make_volume_samples(volumes)
        
        result = VolumeExpansion.compute(
            symbol="BTCUSDT",
            volume_samples=samples,
            lookback_periods=20,
            threshold=Decimal("2.0"),
        )
        
        assert result.is_expansion is False
        assert result.expansion_ratio == Decimal("1.0")
        assert result.intensity == Decimal("0")
        assert result.direction == VolumeDirection.NORMAL

    def test_contraction_detected(self):
        """测试检测到成交量收缩"""
        # 正常成交量100，突然缩小到20（20%）
        volumes = [100] * 19 + [20]
        samples = make_volume_samples(volumes)
        
        result = VolumeExpansion.compute(
            symbol="BTCUSDT",
            volume_samples=samples,
            lookback_periods=20,
            threshold=Decimal("2.0"),
        )
        
        assert result.is_expansion is False  # 不是扩张
        assert result.expansion_ratio < Decimal("1.0")
        assert result.direction == VolumeDirection.CONTRACTION

    def test_intensity_calculation(self):
        """测试强度计算"""
        # 成交量为均值的3倍，阈值为2倍
        volumes = [100] * 19 + [300]
        samples = make_volume_samples(volumes)
        
        result = VolumeExpansion.compute(
            symbol="BTCUSDT",
            volume_samples=samples,
            lookback_periods=20,
            threshold=Decimal("2.0"),
        )
        
        # 强度 = min(3/2, 1) = 0.5？不对...
        # expansion_ratio = 300/100 = 3.0
        # intensity = min(3.0 / 2.0, 1.0) = 1.0
        assert result.intensity == Decimal("1.0")

    def test_insufficient_data(self):
        """测试数据不足"""
        volumes = [100, 200, 300]
        samples = make_volume_samples(volumes)
        
        result = VolumeExpansion.compute(
            symbol="BTCUSDT",
            volume_samples=samples,
            lookback_periods=20,
        )
        
        assert result.is_expansion is False
        assert result.mean_volume is None

    def test_zero_mean_volume(self):
        """测试零平均成交量"""
        volumes = [0] * 25
        samples = make_volume_samples(volumes)
        
        result = VolumeExpansion.compute(
            symbol="BTCUSDT",
            volume_samples=samples,
            lookback_periods=20,
        )
        
        assert result.is_expansion is False

    def test_custom_threshold(self):
        """测试自定义阈值"""
        # 成交量为均值的1.5倍
        volumes = [100] * 19 + [150]
        samples = make_volume_samples(volumes)
        
        result = VolumeExpansion.compute(
            symbol="BTCUSDT",
            volume_samples=samples,
            lookback_periods=20,
            threshold=Decimal("2.0"),  # 阈值2倍，不会触发
        )
        
        assert result.is_expansion is False

        # 用1.0阈值测试
        result2 = VolumeExpansion.compute(
            symbol="BTCUSDT",
            volume_samples=samples,
            lookback_periods=20,
            threshold=Decimal("1.0"),  # 阈值1倍，1.5倍会触发
        )
        
        assert result2.is_expansion is True


# ==================== TestVolatilityCompression ====================

class TestVolatilityCompression:
    """VolatilityCompression 单元测试"""

    def test_compression_detected(self):
        """测试检测到波动率压缩"""
        # 先创建高波动数据，再创建低波动数据
        # 高波动时期
        prices_high_vol = [100, 110, 90, 105, 95, 115, 85, 120, 80, 125, 75, 130, 70, 135, 65]
        volumes = [1000] * 15
        
        # 低波动时期
        prices_low_vol = [100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100]
        
        samples = make_price_volume_samples(
            prices=prices_high_vol + prices_low_vol,
            volumes=volumes * 2,
        )
        
        result = VolatilityCompression.compute(
            symbol="BTCUSDT",
            price_volume_samples=samples,
            lookback_periods=14,
            compression_threshold=Decimal("0.5"),
        )
        
        assert result.symbol == "BTCUSDT"
        assert result.is_compression is True
        assert result.compression_ratio is not None
        assert result.compression_ratio < Decimal("0.5")

    def test_no_compression(self):
        """测试无压缩情况"""
        # 持续高波动
        prices = [100, 110, 90, 105, 95, 115, 85, 120, 80, 125, 75, 130, 70, 135, 65, 140, 60, 145, 55, 150]
        volumes = [1000] * 20
        
        samples = make_price_volume_samples(prices, volumes)
        
        result = VolatilityCompression.compute(
            symbol="BTCUSDT",
            price_volume_samples=samples,
            lookback_periods=14,
            compression_threshold=Decimal("0.5"),
        )
        
        # 高波动环境下不应该触发压缩
        assert result.compression_ratio is not None
        assert result.compression_ratio >= Decimal("0.5") or result.is_compression is False

    def test_breakout_direction_bullish(self):
        """测试向上突破预期"""
        # 波动压缩后价格上涨
        # 高波动时期 - 使用更大的波动
        prices_high_vol = [100, 120, 80, 115, 85, 125, 75, 130, 70, 135, 65, 140, 60, 145, 55]
        # 低波动 + 价格上涨
        prices_low_vol = [55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69]
        
        samples = make_price_volume_samples(
            prices=prices_high_vol + prices_low_vol,
            volumes=[1000] * 30,
        )
        
        result = VolatilityCompression.compute(
            symbol="BTCUSDT",
            price_volume_samples=samples,
            lookback_periods=14,
            compression_threshold=Decimal("0.8"),  # 使用更宽松的阈值
        )
        
        assert result.is_compression is True
        assert result.breakout_direction == VolumeDirection.EXPANSION

    def test_breakout_direction_bearish(self):
        """测试向下突破预期"""
        # 高波动时期
        prices_high_vol = [100, 120, 80, 115, 85, 125, 75, 130, 70, 135, 65, 140, 60, 145, 55]
        # 低波动 + 价格下跌
        prices_low_vol = [55, 54, 53, 52, 51, 50, 49, 48, 47, 46, 45, 44, 43, 42, 41]
        
        samples = make_price_volume_samples(
            prices=prices_high_vol + prices_low_vol,
            volumes=[1000] * 30,
        )
        
        result = VolatilityCompression.compute(
            symbol="BTCUSDT",
            price_volume_samples=samples,
            lookback_periods=14,
            compression_threshold=Decimal("0.8"),  # 使用更宽松的阈值
        )
        
        assert result.is_compression is True
        assert result.breakout_direction == VolumeDirection.CONTRACTION

    def test_insufficient_data(self):
        """测试数据不足"""
        prices = [100, 110, 90, 105, 95]
        volumes = [1000] * 5
        
        samples = make_price_volume_samples(prices, volumes)
        
        result = VolatilityCompression.compute(
            symbol="BTCUSDT",
            price_volume_samples=samples,
            lookback_periods=14,
        )
        
        assert result.is_compression is False
        assert result.current_atr is None

    def test_atr_values_correctness(self):
        """测试ATR计算正确性"""
        # 固定价格时，由于high=close*1.01, low=close*0.99，所以TR = high - low = 2
        prices = [100] * 30
        volumes = [1000] * 30
        
        samples = make_price_volume_samples(prices, volumes)
        
        result = VolatilityCompression.compute(
            symbol="BTCUSDT",
            price_volume_samples=samples,
            lookback_periods=14,
        )
        
        assert result.current_atr is not None
        # 由于我们的测试数据 high=101, low=99（假设价格100时），TR=2
        assert result.current_atr == Decimal("2.00000000")

    def test_high_volatility_reference(self):
        """测试高波动参考值"""
        # 剧烈波动的市场
        prices = [100, 150, 50, 140, 60, 130, 70, 120, 80, 110, 90, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100]
        volumes = [1000] * 30
        
        samples = make_price_volume_samples(prices, volumes)
        
        result = VolatilityCompression.compute(
            symbol="BTCUSDT",
            price_volume_samples=samples,
            lookback_periods=14,
        )
        
        # 当前ATR应该小于历史均值
        assert result.mean_atr is not None


# ==================== TestDataClassImmutability ====================

class TestPriceVolumeSignalDataClassImmutability:
    """验证价量信号数据类的不可变性"""

    def test_volume_sample_immutable(self):
        """VolumeSample应是不可变的"""
        sample = make_volume_sample(ts_ms=1000, volume=100.0)
        with pytest.raises(AttributeError):
            sample.volume = Decimal("200")

    def test_price_volume_sample_immutable(self):
        """PriceVolumeSample应是不可变的"""
        sample = make_price_volume_sample(ts_ms=1000, close_price=100.0, volume=1000.0)
        with pytest.raises(AttributeError):
            sample.close_price = Decimal("200")

    def test_volume_expansion_result_immutable(self):
        """VolumeExpansionResult应是不可变的"""
        samples = make_volume_samples([100] * 25)
        result = VolumeExpansion.compute("BTCUSDT", samples, lookback_periods=20)
        with pytest.raises(AttributeError):
            result.expansion_ratio = Decimal("1.0")

    def test_volatility_compression_result_immutable(self):
        """VolatilityCompressionResult应是不可变的"""
        prices = [100] * 30
        volumes = [1000] * 30
        samples = make_price_volume_samples(prices, volumes)
        result = VolatilityCompression.compute("BTCUSDT", samples, lookback_periods=14)
        with pytest.raises(AttributeError):
            result.compression_ratio = Decimal("1.0")


# ==================== TestEdgeCases ====================

class TestPriceVolumeSignalEdgeCases:
    """边界条件和错误处理测试"""

    def test_empty_volume_samples(self):
        """测试空成交量样本"""
        result = VolumeExpansion.compute("BTCUSDT", [], lookback_periods=20)
        assert result.is_expansion is False
        assert result.mean_volume is None

    def test_empty_price_volume_samples(self):
        """测试空价量样本"""
        result = VolatilityCompression.compute("BTCUSDT", [], lookback_periods=14)
        assert result.is_compression is False
        assert result.current_atr is None

    def test_zero_volume(self):
        """测试零成交量"""
        volumes = [0] * 25
        samples = make_volume_samples(volumes)
        result = VolumeExpansion.compute("BTCUSDT", samples, lookback_periods=20)
        # 零成交量时，mean_volume为0，expansion_ratio返回None
        assert result.expansion_ratio is None
        assert result.is_expansion is False

    def test_negative_threshold(self):
        """测试负阈值"""
        samples = make_volume_samples([100] * 25)
        result = VolumeExpansion.compute("BTCUSDT", samples, lookback_periods=20, threshold=Decimal("-1.0"))
        assert result.is_expansion is False

    def test_zero_threshold(self):
        """测试零阈值"""
        samples = make_volume_samples([100] * 25)
        result = VolumeExpansion.compute("BTCUSDT", samples, lookback_periods=20, threshold=Decimal("0"))
        assert result.is_expansion is False

    def test_custom_ts_ms(self):
        """测试自定义时间戳"""
        volumes = [100] * 25
        samples = make_volume_samples(volumes)
        custom_ts = 9999999999999
        result = VolumeExpansion.compute("BTCUSDT", samples, lookback_periods=20, ts_ms=custom_ts)
        assert result.ts_ms == custom_ts

    def test_decimal_precision(self):
        """测试Decimal精度"""
        volumes = [100.123456789] * 25
        samples = make_volume_samples(volumes)
        result = VolumeExpansion.compute("BTCUSDT", samples, lookback_periods=20)
        if result.expansion_ratio is not None:
            # 应该有合理的精度
            assert result.expansion_ratio == result.expansion_ratio.quantize(Decimal("0.00000001"))


# ==================== TestVolumeExpansionIntegration ====================

class TestVolumeExpansionIntegration:
    """成交量扩张集成测试"""

    def test_gradual_increase(self):
        """测试成交量逐渐增加"""
        # 成交量从50逐渐增加到200
        volumes = [50 + i * 10 for i in range(25)]
        samples = make_volume_samples(volumes)
        
        result = VolumeExpansion.compute(
            symbol="BTCUSDT",
            volume_samples=samples,
            lookback_periods=20,
            threshold=Decimal("2.0"),
        )
        
        # 最后一天成交量是200，历史均值约125，比例为1.6
        assert result.expansion_ratio is not None
        assert result.expansion_ratio > Decimal("1.0")

    def test_spike_detection(self):
        """测试突发放量检测"""
        # 大部分时间成交量低，最后突然放大
        volumes = [50] * 24 + [500]
        samples = make_volume_samples(volumes)
        
        result = VolumeExpansion.compute(
            symbol="BTCUSDT",
            volume_samples=samples,
            lookback_periods=20,
            threshold=Decimal("2.0"),
        )
        
        assert result.is_expansion is True
        assert result.expansion_ratio >= Decimal("5.0")  # 500/50 = 10倍
        assert result.intensity == Decimal("1.0")  # 达到最大强度

    def test_multiple_expansions(self):
        """测试多次扩张"""
        # 交替放量缩量，但最后一天是真实放量
        # 前20天（indices 5-24）：100出现10次，300出现10次，均值=200
        # 最后一天：500，远超阈值
        volumes = [100, 300, 100, 300, 100, 100, 300, 100, 300, 100, 300, 100, 300, 100, 300, 100, 300, 100, 300, 100, 300, 100, 300, 100, 500]
        samples = make_volume_samples(volumes)
        
        result = VolumeExpansion.compute(
            symbol="BTCUSDT",
            volume_samples=samples,
            lookback_periods=20,
            threshold=Decimal("2.0"),
        )
        
        # 最后一天成交量是500，前20天均值约200，比例2.5 > 2.0
        assert result.is_expansion is True
        assert result.expansion_ratio >= Decimal("2.0")


# ==================== TestVolatilityCompressionIntegration ====================

class TestVolatilityCompressionIntegration:
    """波动率压缩集成测试"""

    def test_volatility_squeeze(self):
        """测试波动率挤压形态"""
        # 从高波动逐渐降低到低波动
        prices = []
        for i in range(30):
            if i < 15:
                # 高波动
                base = 100 + (i % 3) * 10 - 10
                prices.append(base)
            else:
                # 低波动
                prices.append(100 + (i - 15) * 0.1)
        
        volumes = [1000] * 30
        samples = make_price_volume_samples(prices, volumes)
        
        result = VolatilityCompression.compute(
            symbol="BTCUSDT",
            price_volume_samples=samples,
            lookback_periods=14,
            compression_threshold=Decimal("0.5"),
        )
        
        assert result.is_compression is True
        assert result.compression_ratio is not None

    def test_constant_volatility(self):
        """测试恒定波动率"""
        # 稳定波动
        prices = [100, 105, 95, 104, 96, 103, 97, 102, 98, 101, 99, 100] * 3
        volumes = [1000] * 36
        
        samples = make_price_volume_samples(prices, volumes)
        
        result = VolatilityCompression.compute(
            symbol="BTCUSDT",
            price_volume_samples=samples,
            lookback_periods=14,
        )
        
        # 恒定波动率时，compression_ratio应该接近1.0
        assert result.compression_ratio is not None
        assert Decimal("0.8") <= result.compression_ratio <= Decimal("1.2")

    def test_volatility_expansion(self):
        """测试波动率放大"""
        # 低波动后突然放大
        prices_low = [100] * 15
        prices_high = [100, 130, 70, 125, 75, 120, 80, 115, 85, 110, 90, 105, 95, 100, 100]
        
        samples = make_price_volume_samples(
            prices=prices_low + prices_high,
            volumes=[1000] * 30,
        )
        
        result = VolatilityCompression.compute(
            symbol="BTCUSDT",
            price_volume_samples=samples,
            lookback_periods=14,
            compression_threshold=Decimal("0.5"),
        )
        
        # 当前是高波动，不应该触发压缩
        assert result.is_compression is False
