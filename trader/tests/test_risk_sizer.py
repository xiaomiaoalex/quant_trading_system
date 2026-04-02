"""
RiskSizer 单元测试
==================
测试统一仓位大小计算器功能。

测试覆盖：
1. 基本 sizing 逻辑
2. 系数独立影响
3. 限制因子识别
4. Fail-Closed 行为
5. 边界值
6. 边缘情况
7. 配置验证
"""
import math
import pytest

from trader.core.domain.services.risk_sizer import (
    RiskSizer,
    SizerConfig,
    SizerInputs,
    SizerResult,
)


def _make_inputs(
    size_by_stop: float = 100.0,
    strategy_cap: float = 200.0,
    symbol_exposure_cap: float = 150.0,
    total_exposure_cap: float = 500.0,
    liquidity_cap: float = 300.0,
    time_coef: float = 1.0,
    drawdown_coef: float = 1.0,
    venue_health_coef: float = 1.0,
    regime_coef: float = 1.0,
) -> SizerInputs:
    return SizerInputs(
        size_by_stop=size_by_stop,
        strategy_cap=strategy_cap,
        symbol_exposure_cap=symbol_exposure_cap,
        total_exposure_cap=total_exposure_cap,
        liquidity_cap=liquidity_cap,
        time_coef=time_coef,
        drawdown_coef=drawdown_coef,
        venue_health_coef=venue_health_coef,
        regime_coef=regime_coef,
    )


# ==================== Basic Sizing Tests ====================


class TestRiskSizerBasic:
    """基本 sizing 测试"""

    def test_all_valid_inputs_returns_approved(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs()
        result = sizer.compute(inputs)
        assert not result.is_rejected
        assert result.approved_size == 100.0

    def test_size_by_stop_is_limiting_factor(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=50.0, strategy_cap=200.0)
        result = sizer.compute(inputs)
        assert result.limiting_factor == "size_by_stop"
        assert result.approved_size == 50.0

    def test_strategy_cap_is_limiting_factor(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=200.0, strategy_cap=80.0)
        result = sizer.compute(inputs)
        assert result.limiting_factor == "strategy_cap"
        assert result.approved_size == 80.0

    def test_symbol_exposure_cap_is_limiting_factor(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=200.0, symbol_exposure_cap=60.0)
        result = sizer.compute(inputs)
        assert result.limiting_factor == "symbol_exposure_cap"
        assert result.approved_size == 60.0

    def test_total_exposure_cap_is_limiting_factor(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=200.0, total_exposure_cap=40.0)
        result = sizer.compute(inputs)
        assert result.limiting_factor == "total_exposure_cap"
        assert result.approved_size == 40.0

    def test_liquidity_cap_is_limiting_factor(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=200.0, liquidity_cap=30.0)
        result = sizer.compute(inputs)
        assert result.limiting_factor == "liquidity_cap"
        assert result.approved_size == 30.0


# ==================== Coefficient Tests ====================


class TestRiskSizerCoefficients:
    """系数独立影响测试"""

    def test_time_coef_halves_result(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=100.0, time_coef=0.5)
        result = sizer.compute(inputs)
        assert not result.is_rejected
        assert result.approved_size == 50.0

    def test_drawdown_coef_halves_result(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=100.0, drawdown_coef=0.5)
        result = sizer.compute(inputs)
        assert not result.is_rejected
        assert result.approved_size == 50.0

    def test_venue_health_coef_halves_result(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=100.0, venue_health_coef=0.5)
        result = sizer.compute(inputs)
        assert not result.is_rejected
        assert result.approved_size == 50.0

    def test_regime_coef_halves_result(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=100.0, regime_coef=0.5)
        result = sizer.compute(inputs)
        assert not result.is_rejected
        assert result.approved_size == 50.0

    def test_all_coefs_combined(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(
            size_by_stop=100.0,
            time_coef=0.5,
            drawdown_coef=0.5,
            venue_health_coef=0.5,
            regime_coef=0.5,
        )
        result = sizer.compute(inputs)
        assert not result.is_rejected
        assert result.approved_size == 100.0 * 0.5**4

    def test_coefficients_in_result_metadata(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(time_coef=0.8, drawdown_coef=0.9, venue_health_coef=0.7, regime_coef=0.6)
        result = sizer.compute(inputs)
        assert result.coefficients["time_coef"] == 0.8
        assert result.coefficients["drawdown_coef"] == 0.9
        assert result.coefficients["venue_health_coef"] == 0.7
        assert result.coefficients["regime_coef"] == 0.6


# ==================== Limiting Factor Tests ====================


class TestRiskSizerLimitingFactor:
    """限制因子识别测试"""

    def test_smallest_cap_identified(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(
            size_by_stop=500.0,
            strategy_cap=400.0,
            symbol_exposure_cap=300.0,
            total_exposure_cap=200.0,
            liquidity_cap=100.0,
        )
        result = sizer.compute(inputs)
        assert result.limiting_factor == "liquidity_cap"
        assert result.approved_size == 100.0

    def test_tie_picks_first_by_dict_order(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(
            size_by_stop=100.0,
            strategy_cap=100.0,
            symbol_exposure_cap=100.0,
            total_exposure_cap=100.0,
            liquidity_cap=100.0,
        )
        result = sizer.compute(inputs)
        assert result.limiting_factor in {
            "size_by_stop",
            "strategy_cap",
            "symbol_exposure_cap",
            "total_exposure_cap",
            "liquidity_cap",
        }
        assert result.approved_size == 100.0

    def test_limiting_factor_with_coefficients(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=100.0, strategy_cap=200.0, time_coef=0.5)
        result = sizer.compute(inputs)
        assert result.limiting_factor == "size_by_stop"
        assert result.approved_size == 50.0


# ==================== Fail-Closed Tests ====================


class TestRiskSizerFailClosed:
    """Fail-Closed 行为测试"""

    def test_nan_size_by_stop_rejected(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=float("nan"))
        result = sizer.compute(inputs)
        assert result.is_rejected
        assert "not finite" in result.rejection_reason

    def test_inf_size_by_stop_rejected(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=float("inf"))
        result = sizer.compute(inputs)
        assert result.is_rejected
        assert "not finite" in result.rejection_reason

    def test_negative_size_by_stop_rejected(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=-10.0)
        result = sizer.compute(inputs)
        assert result.is_rejected
        assert "negative" in result.rejection_reason or "must be > 0" in result.rejection_reason

    def test_zero_size_by_stop_rejected(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=0.0)
        result = sizer.compute(inputs)
        assert result.is_rejected
        assert "must be > 0" in result.rejection_reason

    def test_negative_strategy_cap_rejected(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(strategy_cap=-5.0)
        result = sizer.compute(inputs)
        assert result.is_rejected

    def test_nan_coefficient_rejected(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(time_coef=float("nan"))
        result = sizer.compute(inputs)
        assert result.is_rejected
        assert "not finite" in result.rejection_reason

    def test_inf_coefficient_rejected(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(drawdown_coef=float("inf"))
        result = sizer.compute(inputs)
        assert result.is_rejected

    def test_coefficient_above_one_rejected(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(regime_coef=1.5)
        result = sizer.compute(inputs)
        assert result.is_rejected
        assert "out of range" in result.rejection_reason

    def test_coefficient_below_zero_rejected(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(venue_health_coef=-0.1)
        result = sizer.compute(inputs)
        assert result.is_rejected
        assert "out of range" in result.rejection_reason

    def test_zero_time_coef_rejected_fail_closed(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(time_coef=0.0)
        result = sizer.compute(inputs)
        assert result.is_rejected
        assert "Zero coefficient" in result.rejection_reason

    def test_zero_drawdown_coef_rejected_fail_closed(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(drawdown_coef=0.0)
        result = sizer.compute(inputs)
        assert result.is_rejected

    def test_zero_venue_health_coef_rejected_fail_closed(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(venue_health_coef=0.0)
        result = sizer.compute(inputs)
        assert result.is_rejected

    def test_zero_regime_coef_rejected_fail_closed(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(regime_coef=0.0)
        result = sizer.compute(inputs)
        assert result.is_rejected

    def test_negative_liquidity_cap_rejected(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(liquidity_cap=-1.0)
        result = sizer.compute(inputs)
        assert result.is_rejected

    def test_negative_total_exposure_cap_rejected(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(total_exposure_cap=-1.0)
        result = sizer.compute(inputs)
        assert result.is_rejected

    def test_negative_symbol_exposure_cap_rejected(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(symbol_exposure_cap=-1.0)
        result = sizer.compute(inputs)
        assert result.is_rejected


# ==================== Boundary Value Tests ====================


class TestRiskSizerBoundaryValues:
    """边界值测试"""

    def test_zero_cap_rejects(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=100.0, strategy_cap=0.0)
        result = sizer.compute(inputs)
        assert result.limiting_factor == "strategy_cap"
        assert result.approved_size == 0.0

    def test_coefficient_at_zero_rejects(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(time_coef=0.0)
        result = sizer.compute(inputs)
        assert result.is_rejected

    def test_coefficient_at_one_passes(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(time_coef=1.0, drawdown_coef=1.0, venue_health_coef=1.0, regime_coef=1.0)
        result = sizer.compute(inputs)
        assert not result.is_rejected

    def test_minimum_size_threshold_rejects(self) -> None:
        config = SizerConfig(min_size=10.0)
        sizer = RiskSizer(config=config)
        inputs = _make_inputs(size_by_stop=5.0)
        result = sizer.compute(inputs)
        assert result.is_rejected
        assert result.limiting_factor == "min_size"

    def test_minimum_size_threshold_passes(self) -> None:
        config = SizerConfig(min_size=10.0)
        sizer = RiskSizer(config=config)
        inputs = _make_inputs(size_by_stop=20.0)
        result = sizer.compute(inputs)
        assert not result.is_rejected
        assert result.approved_size == 20.0

    def test_very_small_positive_size(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=1e-10)
        result = sizer.compute(inputs)
        assert not result.is_rejected
        assert result.approved_size == 1e-10


# ==================== Edge Case Tests ====================


class TestRiskSizerEdgeCases:
    """边缘情况测试"""

    def test_all_caps_equal(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(
            size_by_stop=100.0,
            strategy_cap=100.0,
            symbol_exposure_cap=100.0,
            total_exposure_cap=100.0,
            liquidity_cap=100.0,
        )
        result = sizer.compute(inputs)
        assert not result.is_rejected
        assert result.approved_size == 100.0

    def test_all_coefficients_at_one(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(
            size_by_stop=100.0,
            time_coef=1.0,
            drawdown_coef=1.0,
            venue_health_coef=1.0,
            regime_coef=1.0,
        )
        result = sizer.compute(inputs)
        assert not result.is_rejected
        assert result.approved_size == 100.0

    def test_extreme_large_values(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(
            size_by_stop=1e15,
            strategy_cap=1e15,
            symbol_exposure_cap=1e15,
            total_exposure_cap=1e15,
            liquidity_cap=1e15,
        )
        result = sizer.compute(inputs)
        assert not result.is_rejected
        assert result.approved_size == 1e15

    def test_deterministic_output(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=123.45, time_coef=0.7, drawdown_coef=0.8)
        r1 = sizer.compute(inputs)
        r2 = sizer.compute(inputs)
        assert r1.approved_size == r2.approved_size
        assert r1.limiting_factor == r2.limiting_factor
        assert r1.is_rejected == r2.is_rejected

    def test_multiple_sizers_same_result(self) -> None:
        inputs = _make_inputs(size_by_stop=50.0, venue_health_coef=0.6)
        r1 = RiskSizer().compute(inputs)
        r2 = RiskSizer().compute(inputs)
        assert r1.approved_size == r2.approved_size

    def test_rejection_reason_is_none_when_approved(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs()
        result = sizer.compute(inputs)
        assert not result.is_rejected
        assert result.rejection_reason is None

    def test_rejection_reason_present_when_rejected(self) -> None:
        sizer = RiskSizer()
        inputs = _make_inputs(size_by_stop=float("nan"))
        result = sizer.compute(inputs)
        assert result.is_rejected
        assert result.rejection_reason is not None
        assert len(result.rejection_reason) > 0


# ==================== Config Tests ====================


class TestSizerConfig:
    """配置验证测试"""

    def test_default_config_values(self) -> None:
        config = SizerConfig()
        assert config.min_size == 0.0
        assert config.fail_closed is True

    def test_custom_config_values(self) -> None:
        config = SizerConfig(min_size=5.0, fail_closed=False)
        assert config.min_size == 5.0
        assert config.fail_closed is False

    def test_fail_closed_false_allows_zero_coef(self) -> None:
        config = SizerConfig(fail_closed=False)
        sizer = RiskSizer(config=config)
        inputs = _make_inputs(time_coef=0.0)
        result = sizer.compute(inputs)
        assert not result.is_rejected
        assert result.approved_size == 0.0

    def test_config_property_accessible(self) -> None:
        config = SizerConfig(min_size=1.0)
        sizer = RiskSizer(config=config)
        assert sizer.config.min_size == 1.0

    def test_default_config_when_none_passed(self) -> None:
        sizer = RiskSizer(config=None)
        assert sizer.config.min_size == 0.0
        assert sizer.config.fail_closed is True

    def test_min_size_with_coefficients(self) -> None:
        config = SizerConfig(min_size=30.0)
        sizer = RiskSizer(config=config)
        inputs = _make_inputs(size_by_stop=100.0, time_coef=0.2)
        result = sizer.compute(inputs)
        assert result.is_rejected
        assert result.limiting_factor == "min_size"

    def test_min_size_boundary_exactly(self) -> None:
        config = SizerConfig(min_size=50.0)
        sizer = RiskSizer(config=config)
        inputs = _make_inputs(size_by_stop=50.0)
        result = sizer.compute(inputs)
        assert not result.is_rejected
        assert result.approved_size == 50.0

    def test_min_size_just_above_boundary(self) -> None:
        config = SizerConfig(min_size=49.99)
        sizer = RiskSizer(config=config)
        inputs = _make_inputs(size_by_stop=50.0)
        result = sizer.compute(inputs)
        assert not result.is_rejected
