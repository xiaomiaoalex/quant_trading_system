"""
Test Capital Structure Signals - 资金结构信号单元测试
======================================================

测试覆盖：
1. FundingRateZScore - 正常计算、边界情况
2. OIChangeRateDivergence - 背离检测
3. LongShortRatioAnomaly - 极端值检测
4. 复合信号计算
"""

import pytest
from decimal import Decimal

from trader.core.domain.signals.capital_structure_signals import (
    FundingRateZScore,
    FundingRateSample,
    FundingRateZScoreResult,
    OIChangeRateDivergence,
    OIDivergenceResult,
    LongShortRatioAnomaly,
    LongShortSample,
    LongShortAnomalyResult,
    DivergenceDirection,
    compute_composite_capital_signal,
    SignalThresholds,
)


# ==================== FundingRateZScore Tests ====================

class TestFundingRateZScore:
    """FundingRateZScore 单元测试"""
    
    def test_compute_with_sufficient_history(self):
        """正常情况：历史数据充足且有变化"""
        symbol = "BTCUSDT"
        current_rate = 0.00015  # 当前资金费率
        # 创建有变化的历史数据
        history = [
            FundingRateSample(ts_ms=1000 + i * 8 * 3600000, funding_rate=0.0001 + (i % 5) * 0.00001)
            for i in range(20)
        ]
        
        result = FundingRateZScore.compute(
            symbol=symbol,
            current_funding_rate=current_rate,
            history=history,
            window=20,
        )
        
        assert isinstance(result, FundingRateZScoreResult)
        assert result.symbol == symbol
        assert result.z_score is not None
        assert result.window == 20
        assert result.sample_count == 20
    
    def test_compute_insufficient_history_returns_none(self):
        """边界情况：历史数据不足，返回None"""
        symbol = "ETHUSDT"
        current_rate = 0.0002
        # 只有5个样本，不足window/2=10
        history = [
            FundingRateSample(ts_ms=1000 + i * 8 * 3600000, funding_rate=0.0001)
            for i in range(5)
        ]
        
        result = FundingRateZScore.compute(
            symbol=symbol,
            current_funding_rate=current_rate,
            history=history,
            window=20,
        )
        
        assert result.z_score is None
        assert result.sample_count == 5
    
    def test_compute_empty_history(self):
        """边界情况：空历史数据"""
        result = FundingRateZScore.compute(
            symbol="BTCUSDT",
            current_funding_rate=0.0001,
            history=[],
            window=20,
        )
        
        assert result.z_score is None
        assert result.sample_count == 0
    
    def test_compute_zero_std_returns_none(self):
        """边界情况：标准差为0（所有值相同）"""
        symbol = "BTCUSDT"
        current_rate = 0.0001
        # 所有历史值相同，标准差为0
        history = [
            FundingRateSample(ts_ms=1000 + i * 8 * 3600000, funding_rate=0.0001)
            for i in range(20)
        ]
        
        result = FundingRateZScore.compute(
            symbol=symbol,
            current_funding_rate=current_rate,
            history=history,
            window=20,
        )
        
        # 标准差为0时，z_score应为None（避免除零）
        assert result.z_score is None
        assert result.std == 0.0
    
    def test_compute_negative_zscore(self):
        """正常情况：负Z-Score（资金费率低于均值）"""
        symbol = "BTCUSDT"
        current_rate = 0.00005  # 低于均值的资金费率
        # 创建有变化的历史数据，均值约0.00012
        history = [
            FundingRateSample(ts_ms=1000 + i * 8 * 3600000, funding_rate=0.0001 + (i % 3) * 0.00002)
            for i in range(20)
        ]
        
        result = FundingRateZScore.compute(
            symbol=symbol,
            current_funding_rate=current_rate,
            history=history,
            window=20,
        )
        
        # 有变化的history，std>0
        assert result.z_score is not None
        assert result.z_score < 0  # 0.00005 < 均值
    
    def test_compute_custom_min_periods(self):
        """正常情况：自定义最小周期"""
        symbol = "BTCUSDT"
        current_rate = 0.00015
        # 创建有变化的历史
        history = [
            FundingRateSample(ts_ms=1000 + i * 8 * 3600000, funding_rate=0.0001 + (i % 5) * 0.00001)
            for i in range(15)
        ]
        
        # min_periods=8，15>=8，应有z_score
        result = FundingRateZScore.compute(
            symbol=symbol,
            current_funding_rate=current_rate,
            history=history,
            window=20,
            min_periods=8,
        )
        
        assert result.z_score is not None
        
        # min_periods=16，15<16，应返回None
        result2 = FundingRateZScore.compute(
            symbol=symbol,
            current_funding_rate=current_rate,
            history=history,
            window=20,
            min_periods=16,
        )
        
        assert result2.z_score is None


# ==================== OIChangeRateDivergence Tests ====================

class TestOIChangeRateDivergence:
    """OIChangeRateDivergence 单元测试"""
    
    def test_bullish_divergence(self):
        """看涨背离：OI下降 + 价格上升，满足阈值"""
        # OI: 10000 -> 9000 (-10%)
        # Price: 50000 -> 70000 (+40%)
        # divergence_score = |(-10) - 40| / 100 = 0.5
        result = OIChangeRateDivergence.compute(
            symbol="BTCUSDT",
            oi_current=9000,
            oi_previous=10000,  # OI下降
            price_current=70000,
            price_previous=50000,  # 价格大幅上升
            ts_ms=1000,
        )
        
        assert result.direction == DivergenceDirection.BULLISH
        assert result.divergence_score >= 0.5
        assert result.oi_change_rate < 0
        assert result.price_change_rate > 0
    
    def test_bearish_divergence(self):
        """看跌背离：OI上升 + 价格下降，满足阈值"""
        # OI: 10000 -> 12000 (+20%)
        # Price: 50000 -> 30000 (-40%)
        # divergence_score = |20 - (-40)| / 100 = 0.6
        result = OIChangeRateDivergence.compute(
            symbol="BTCUSDT",
            oi_current=12000,
            oi_previous=10000,  # OI上升
            price_current=30000,
            price_previous=50000,  # 价格大幅下降
            ts_ms=1000,
        )
        
        assert result.direction == DivergenceDirection.BEARISH
        assert result.divergence_score >= 0.5
        assert result.oi_change_rate > 0
        assert result.price_change_rate < 0
    
    def test_no_divergence_oi_and_price_rising(self):
        """无背离：OI和价格同向上涨"""
        result = OIChangeRateDivergence.compute(
            symbol="BTCUSDT",
            oi_current=11000,
            oi_previous=10000,
            price_current=51000,
            price_previous=50000,
            ts_ms=1000,
        )
        
        assert result.direction == DivergenceDirection.NONE
        assert result.is_divergence is False
    
    def test_no_divergence_oi_and_price_falling(self):
        """无背离：OI和价格同向下跌"""
        result = OIChangeRateDivergence.compute(
            symbol="BTCUSDT",
            oi_current=9000,
            oi_previous=10000,
            price_current=49000,
            price_previous=50000,
            ts_ms=1000,
        )
        
        assert result.direction == DivergenceDirection.NONE
        assert result.is_divergence is False
    
    def test_zero_previous_oi(self):
        """边界情况：前一OI为0"""
        result = OIChangeRateDivergence.compute(
            symbol="BTCUSDT",
            oi_current=10000,
            oi_previous=0,  # 避免除零
            price_current=50000,
            price_previous=50000,
            ts_ms=1000,
        )
        
        # OI变化率应为0（避免除零）
        assert result.oi_change_rate == 0.0
        assert result.direction == DivergenceDirection.NONE
    
    def test_zero_previous_price(self):
        """边界情况：前一价格为0"""
        result = OIChangeRateDivergence.compute(
            symbol="BTCUSDT",
            oi_current=10000,
            oi_previous=10000,
            price_current=50000,
            price_previous=0,  # 避免除零
            ts_ms=1000,
        )
        
        # 价格变化率应为0（避免除零）
        assert result.price_change_rate == 0.0
    
    def test_divergence_score_calculation(self):
        """验证背离得分计算"""
        # OI变化率 20%，价格变化率 -40%，背离得分 = |20 - (-40)| / 100 = 0.6
        result = OIChangeRateDivergence.compute(
            symbol="BTCUSDT",
            oi_current=12000,
            oi_previous=10000,  # +20%
            price_current=30000,
            price_previous=50000,  # -40%
            ts_ms=1000,
        )
        
        # 背离得分 = |20 - (-40)| / 100 = 0.6
        assert abs(result.divergence_score - 0.6) < 0.001


# ==================== LongShortRatioAnomaly Tests ====================

class TestLongShortRatioAnomaly:
    """LongShortRatioAnomaly 单元测试"""
    
    def test_extreme_long_position(self):
        """极端偏多：多空比远高于均值"""
        symbol = "BTCUSDT"
        current_ratio = 2.0  # 散户做多过多
        # 创建有变化的历史数据，均值约1.0
        history = [
            LongShortSample(ts_ms=1000 + i * 8 * 3600000, long_short_ratio=1.0 + (i % 3) * 0.1)
            for i in range(20)
        ]
        
        result = LongShortRatioAnomaly.compute(
            symbol=symbol,
            current_ratio=current_ratio,
            history=history,
            window=20,
        )
        
        assert result.is_extreme is True
        assert result.is_extreme_long is True
        assert result.is_extreme_short is False
        assert result.z_score is not None
        assert result.z_score > 0
    
    def test_extreme_short_position(self):
        """极端偏空：多空比远低于均值"""
        symbol = "BTCUSDT"
        current_ratio = 0.3  # 散户做空过多
        # 创建有变化的历史数据，均值约1.0
        history = [
            LongShortSample(ts_ms=1000 + i * 8 * 3600000, long_short_ratio=1.0 + (i % 3) * 0.1)
            for i in range(20)
        ]
        
        result = LongShortRatioAnomaly.compute(
            symbol=symbol,
            current_ratio=current_ratio,
            history=history,
            window=20,
        )
        
        assert result.is_extreme is True
        assert result.is_extreme_long is False
        assert result.is_extreme_short is True
        assert result.z_score is not None
        assert result.z_score < 0
    
    def test_normal_ratio(self):
        """正常情况：多空比在正常范围"""
        symbol = "BTCUSDT"
        current_ratio = 1.0  # 正常值
        history = [
            LongShortSample(ts_ms=1000 + i * 8 * 3600000, long_short_ratio=1.0)
            for i in range(20)
        ]
        
        result = LongShortRatioAnomaly.compute(
            symbol=symbol,
            current_ratio=current_ratio,
            history=history,
            window=20,
        )
        
        # 偏离度0，不构成极端
        assert result.is_extreme is False
        assert result.is_extreme_long is False
        assert result.is_extreme_short is False
    
    def test_insufficient_history(self):
        """边界情况：历史数据不足"""
        symbol = "ETHUSDT"
        current_ratio = 2.0
        history = [
            LongShortSample(ts_ms=1000 + i * 8 * 3600000, long_short_ratio=1.0)
            for i in range(5)  # 不足10个
        ]
        
        result = LongShortRatioAnomaly.compute(
            symbol=symbol,
            current_ratio=current_ratio,
            history=history,
            window=20,
        )
        
        assert result.z_score is None
        assert result.is_extreme is False
        assert result.sample_count == 5
    
    def test_empty_history(self):
        """边界情况：空历史"""
        result = LongShortRatioAnomaly.compute(
            symbol="BTCUSDT",
            current_ratio=1.5,
            history=[],
            window=20,
        )
        
        assert result.z_score is None
        assert result.sample_count == 0
    
    def test_zero_mean_ratio(self):
        """边界情况：历史均值接近0"""
        symbol = "BTCUSDT"
        current_ratio = 0.1
        # 历史值都很小，接近0
        history = [
            LongShortSample(ts_ms=1000 + i * 8 * 3600000, long_short_ratio=0.01)
            for i in range(20)
        ]
        
        result = LongShortRatioAnomaly.compute(
            symbol=symbol,
            current_ratio=current_ratio,
            history=history,
            window=20,
        )
        
        # 均值接近0，相对偏离度计算应安全处理
        assert result is not None


# ==================== Composite Signal Tests ====================

class TestCompositeCapitalSignal:
    """复合信号计算测试"""
    
    def test_composite_bearish_signal(self):
        """组合信号：看跌"""
        funding_result = FundingRateZScoreResult(
            symbol="BTCUSDT",
            z_score=2.5,  # 极端正Z-Score
            mean=0.0001,
            std=0.00002,
            window=20,
            sample_count=20,
            ts_ms=1000,
        )
        oi_result = OIDivergenceResult(
            symbol="BTCUSDT",
            oi_change_rate=20.0,
            price_change_rate=-30.0,
            divergence_score=0.5,
            direction=DivergenceDirection.BEARISH,
            is_divergence=True,
            ts_ms=1000,
        )
        ls_result = LongShortAnomalyResult(
            symbol="BTCUSDT",
            long_short_ratio=2.0,
            z_score=3.0,
            is_extreme=True,
            is_extreme_long=True,
            is_extreme_short=False,
            sample_count=20,
            ts_ms=1000,
        )
        
        result = compute_composite_capital_signal(
            symbol="BTCUSDT",
            funding_result=funding_result,
            oi_result=oi_result,
            ls_result=ls_result,
            ts_ms=1000,
        )
        
        assert result.symbol == "BTCUSDT"
        assert result.funding_z_score == 2.5
        assert result.oi_divergence is True
        assert result.ls_anomaly is True
        # 综合得分应为负（看跌）：
        # - 极端正Z-Score (z=2.5>0) → score += -0.3（市场过热，看跌）
        # - 空头背离(BEARISH) → score += -0.3（见顶信号，看跌）
        # - 极端偏多(extreme_long) → score += -0.4（做多过多，看跌）
        # score = -0.3 - 0.3 - 0.4 = -1.0
        # score_count=3, score/(3*0.4) = -1.0/1.2 = -0.833 < 0
        assert result.composite_score < 0  # 看跌信号应为负分
    
    def test_composite_bullish_signal(self):
        """组合信号：看涨"""
        funding_result = FundingRateZScoreResult(
            symbol="BTCUSDT",
            z_score=-2.5,  # 极端负Z-Score => 看多
            mean=0.0001,
            std=0.00002,
            window=20,
            sample_count=20,
            ts_ms=1000,
        )
        oi_result = OIDivergenceResult(
            symbol="BTCUSDT",
            oi_change_rate=-15.0,
            price_change_rate=25.0,
            divergence_score=0.4,
            direction=DivergenceDirection.BULLISH,
            is_divergence=True,
            ts_ms=1000,
        )
        ls_result = LongShortAnomalyResult(
            symbol="BTCUSDT",
            long_short_ratio=0.3,
            z_score=-3.0,
            is_extreme=True,
            is_extreme_long=False,
            is_extreme_short=True,
            sample_count=20,
            ts_ms=1000,
        )
        
        result = compute_composite_capital_signal(
            symbol="BTCUSDT",
            funding_result=funding_result,
            oi_result=oi_result,
            ls_result=ls_result,
            ts_ms=1000,
        )
        
        # z=-2.5 => +0.3(看多), bullish=> +0.3(看多), extreme_short=> +0.4(看多)
        # score = 0.3 + 0.3 + 0.4 = 1.0
        # score_count=3, 1.0/(3*0.4) = 0.833
        assert result.composite_score > 0  # 看涨信号应该得正分
    
    def test_composite_neutral_signal(self):
        """组合信号：中性"""
        funding_result = FundingRateZScoreResult(
            symbol="BTCUSDT",
            z_score=0.5,  # 正常范围
            mean=0.0001,
            std=0.00002,
            window=20,
            sample_count=20,
            ts_ms=1000,
        )
        oi_result = OIDivergenceResult(
            symbol="BTCUSDT",
            oi_change_rate=2.0,
            price_change_rate=1.0,
            divergence_score=0.01,
            direction=DivergenceDirection.NONE,
            is_divergence=False,
            ts_ms=1000,
        )
        ls_result = LongShortAnomalyResult(
            symbol="BTCUSDT",
            long_short_ratio=1.0,
            z_score=0.0,
            is_extreme=False,
            is_extreme_long=False,
            is_extreme_short=False,
            sample_count=20,
            ts_ms=1000,
        )
        
        result = compute_composite_capital_signal(
            symbol="BTCUSDT",
            funding_result=funding_result,
            oi_result=oi_result,
            ls_result=ls_result,
            ts_ms=1000,
        )
        
        # 综合得分应接近0
        assert abs(result.composite_score) < 0.1
    
    def test_composite_score_bounds(self):
        """验证综合得分边界 [-1, 1]"""
        # 全看跌信号
        funding_result = FundingRateZScoreResult(
            symbol="BTCUSDT", z_score=3.0, mean=0.0, std=0.01,
            window=20, sample_count=20, ts_ms=1000,
        )
        oi_result = OIDivergenceResult(
            symbol="BTCUSDT", oi_change_rate=30.0, price_change_rate=-30.0,
            divergence_score=0.6, direction=DivergenceDirection.BEARISH,
            is_divergence=True, ts_ms=1000,
        )
        ls_result = LongShortAnomalyResult(
            symbol="BTCUSDT", long_short_ratio=3.0, z_score=5.0,
            is_extreme=True, is_extreme_long=True, is_extreme_short=False,
            sample_count=20, ts_ms=1000,
        )
        
        result = compute_composite_capital_signal(
            symbol="BTCUSDT",
            funding_result=funding_result,
            oi_result=oi_result,
            ls_result=ls_result,
            ts_ms=1000,
        )
        
        # 得分必须在 [-1, 1] 范围内
        assert -1.0 <= result.composite_score <= 1.0


# ==================== Edge Cases Tests ====================

class TestEdgeCases:
    """边界条件和错误处理测试"""
    
    def test_funding_rate_with_identical_values(self):
        """所有资金费率相同"""
        history = [
            FundingRateSample(ts_ms=i, funding_rate=0.0001)
            for i in range(30)
        ]
        
        result = FundingRateZScore.compute(
            symbol="BTCUSDT",
            current_funding_rate=0.0001,
            history=history,
            window=20,
        )
        
        # std=0时应返回None而不是除零错误
        assert result.std == 0.0
        assert result.z_score is None
    
    def test_extreme_ratio_identical_history(self):
        """多空比历史全相同"""
        history = [
            LongShortSample(ts_ms=i, long_short_ratio=1.0)
            for i in range(30)
        ]
        
        result = LongShortRatioAnomaly.compute(
            symbol="BTCUSDT",
            current_ratio=3.0,
            history=history,
            window=20,
        )
        
        # std=0时相对偏离度计算应安全
        assert result.z_score is None
        # 但极端值检测基于相对偏离度
        assert result.is_extreme is True  # 偏离度200%
    
    def test_oi_zero_previous_values(self):
        """OI前一值为零的边界"""
        result = OIChangeRateDivergence.compute(
            symbol="BTCUSDT",
            oi_current=0,
            oi_previous=0,
            price_current=50000,
            price_previous=50000,
            ts_ms=1000,
        )
        
        assert result.divergence_score == 0.0
        assert result.direction == DivergenceDirection.NONE
    
    def test_large_window_vs_small_history(self):
        """窗口大于历史数据但min_periods满足"""
        history = [
            FundingRateSample(ts_ms=i, funding_rate=0.0001 + (i % 3) * 0.00002)
            for i in range(5)
        ]
        
        result = FundingRateZScore.compute(
            symbol="BTCUSDT",
            current_funding_rate=0.0002,
            history=history,
            window=100,  # 窗口100
            min_periods=3,  # 只要3个样本
        )
        
        # 5 >= 3，有足够min_periods
        assert result.z_score is not None
        assert result.sample_count == 5


# ==================== Data Classes Immutability Tests ====================

class TestDataClassImmutability:
    """验证数据类不可变性"""
    
    def test_funding_sample_is_frozen(self):
        """FundingRateSample不可变"""
        sample = FundingRateSample(ts_ms=1000, funding_rate=0.0001)
        
        with pytest.raises(AttributeError):
            sample.ts_ms = 2000
    
    def test_result_classes_are_frozen(self):
        """结果数据类不可变"""
        result = FundingRateZScoreResult(
            symbol="BTC", z_score=1.0, mean=0.0, std=0.1,
            window=20, sample_count=20, ts_ms=1000,
        )
        
        with pytest.raises(AttributeError):
            result.z_score = 2.0
    
    def test_oi_result_is_frozen(self):
        """OIDivergenceResult不可变"""
        result = OIDivergenceResult(
            symbol="BTC", oi_change_rate=5.0, price_change_rate=-5.0,
            divergence_score=0.1, direction=DivergenceDirection.BEARISH,
            is_divergence=True, ts_ms=1000,
        )
        
        with pytest.raises(AttributeError):
            result.is_divergence = False
