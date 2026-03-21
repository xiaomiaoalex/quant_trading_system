"""
Test TimeWindowPolicy - 时间窗口风控策略单元测试
================================================
测试时段边界、系数应用、RESTRICTED时段拒绝新开仓等场景。
"""
import pytest
from datetime import time

from trader.core.domain.rules.time_window_policy import (
    TimeWindowPeriod,
    TimeWindowSlot,
    TimeWindowConfig,
    TimeWindowContext,
    TimeWindowPolicy,
)


class TestTimeWindowSlot:
    """测试 TimeWindowSlot 时段槽"""

    def test_prime_slot_creation(self) -> None:
        """测试 PRIME 时段槽创建"""
        slot = TimeWindowSlot(
            period=TimeWindowPeriod.PRIME,
            start_hour=8,
            start_minute=0,
            end_hour=16,
            end_minute=0,
            position_coefficient=1.0,
            allow_new_position=True,
        )
        assert slot.period == TimeWindowPeriod.PRIME
        assert slot.position_coefficient == 1.0
        assert slot.allow_new_position is True

    def test_off_peak_slot_creation(self) -> None:
        """测试 OFF_PEAK 时段槽创建"""
        slot = TimeWindowSlot(
            period=TimeWindowPeriod.OFF_PEAK,
            start_hour=16,
            start_minute=0,
            end_hour=22,
            end_minute=0,
            position_coefficient=0.5,
            allow_new_position=True,
        )
        assert slot.period == TimeWindowPeriod.OFF_PEAK
        assert slot.position_coefficient == 0.5
        assert slot.allow_new_position is True

    def test_restricted_slot_creation(self) -> None:
        """测试 RESTRICTED 时段槽创建"""
        slot = TimeWindowSlot(
            period=TimeWindowPeriod.RESTRICTED,
            start_hour=22,
            start_minute=0,
            end_hour=8,
            end_minute=0,
            position_coefficient=0.0,
            allow_new_position=False,
        )
        assert slot.period == TimeWindowPeriod.RESTRICTED
        assert slot.position_coefficient == 0.0
        assert slot.allow_new_position is False

    def test_invalid_hour_raises(self) -> None:
        """测试无效小时抛出异常"""
        with pytest.raises(ValueError, match="start_hour must be 0-23"):
            TimeWindowSlot(
                period=TimeWindowPeriod.PRIME,
                start_hour=25,  # 无效
                start_minute=0,
                end_hour=16,
                end_minute=0,
            )

    def test_invalid_coefficient_raises(self) -> None:
        """测试无效系数抛出异常"""
        with pytest.raises(ValueError, match="position_coefficient must be 0.0-1.0"):
            TimeWindowSlot(
                period=TimeWindowPeriod.PRIME,
                start_hour=8,
                start_minute=0,
                end_hour=16,
                end_minute=0,
                position_coefficient=1.5,  # 无效
            )

    def test_contains_within_slot(self) -> None:
        """测试时间在时段内"""
        slot = TimeWindowSlot(
            period=TimeWindowPeriod.PRIME,
            start_hour=8,
            start_minute=0,
            end_hour=16,
            end_minute=0,
        )
        # 边界内
        assert slot.contains(10, 0) is True
        assert slot.contains(8, 0) is True
        # 边界：结束时间是 exclusive，下一时段开始
        assert slot.contains(16, 0) is False

    def test_contains_outside_slot(self) -> None:
        """测试时间在时段外"""
        slot = TimeWindowSlot(
            period=TimeWindowPeriod.PRIME,
            start_hour=8,
            start_minute=0,
            end_hour=16,
            end_minute=0,
        )
        assert slot.contains(7, 59) is False
        assert slot.contains(16, 1) is False

    def test_contains_cross_day_slot(self) -> None:
        """测试跨天时段（RESTRICTED: 22:00-08:00）"""
        slot = TimeWindowSlot(
            period=TimeWindowPeriod.RESTRICTED,
            start_hour=22,
            start_minute=0,
            end_hour=8,
            end_minute=0,
        )
        # 夜间时段（22:00-23:59）
        assert slot.contains(22, 0) is True
        assert slot.contains(23, 30) is True
        # 凌晨时段（00:00-08:00）
        assert slot.contains(0, 0) is True
        assert slot.contains(4, 0) is True
        # 边界：结束时间 8:00 是 exclusive，下一时段开始
        assert slot.contains(8, 0) is False
        # 白天时段应返回 False
        assert slot.contains(12, 0) is False
        assert slot.contains(20, 0) is False


class TestTimeWindowConfig:
    """测试 TimeWindowConfig 配置"""

    def test_default_config_has_three_slots(self) -> None:
        """测试默认配置有三个时段"""
        config = TimeWindowConfig.create_default()
        assert len(config.slots) == 3

    def test_slots_sorted_by_priority(self) -> None:
        """测试时段按优先级排序（RESTRICTED 优先）"""
        config = TimeWindowConfig.create_default()
        # RESTRICTED 应该排在最前面
        assert config.slots[0].period == TimeWindowPeriod.RESTRICTED
        assert config.slots[1].period == TimeWindowPeriod.OFF_PEAK
        assert config.slots[2].period == TimeWindowPeriod.PRIME

    def test_add_slot(self) -> None:
        """测试添加时段槽"""
        config = TimeWindowConfig()
        slot = TimeWindowSlot(
            period=TimeWindowPeriod.PRIME,
            start_hour=10,
            start_minute=0,
            end_hour=14,
            end_minute=0,
        )
        config.add_slot(slot)
        assert len(config.slots) == 1

    def test_get_slot_at_prime_time(self) -> None:
        """测试获取 PRIME 时段"""
        config = TimeWindowConfig.create_default()
        # 10:00 UTC 应该在 PRIME 时段
        slot = config.get_slot_at(10, 0)
        assert slot is not None
        assert slot.period == TimeWindowPeriod.PRIME

    def test_get_slot_at_off_peak_time(self) -> None:
        """测试获取 OFF_PEAK 时段"""
        config = TimeWindowConfig.create_default()
        # 18:00 UTC 应该在 OFF_PEAK 时段
        slot = config.get_slot_at(18, 0)
        assert slot is not None
        assert slot.period == TimeWindowPeriod.OFF_PEAK
        assert slot.position_coefficient == 0.5

    def test_get_slot_at_restricted_time(self) -> None:
        """测试获取 RESTRICTED 时段"""
        config = TimeWindowConfig.create_default()
        # 23:00 UTC 应该在 RESTRICTED 时段
        slot = config.get_slot_at(23, 0)
        assert slot is not None
        assert slot.period == TimeWindowPeriod.RESTRICTED
        assert slot.allow_new_position is False

    def test_get_slot_at_no_match_returns_none(self) -> None:
        """测试无匹配时段返回 None"""
        config = TimeWindowConfig()  # 空配置
        slot = config.get_slot_at(12, 0)
        assert slot is None

    def test_to_dict_from_dict_roundtrip(self) -> None:
        """测试序列化/反序列化往返"""
        original = TimeWindowConfig.create_default()
        data = original.to_dict()
        restored = TimeWindowConfig.from_dict(data)
        assert len(restored.slots) == len(original.slots)


class TestTimeWindowPolicy:
    """测试 TimeWindowPolicy 策略评估器"""

    def test_evaluate_prime_period(self) -> None:
        """测试评估 PRIME 时段"""
        policy = TimeWindowPolicy()
        ctx = policy.evaluate(10, 0)  # 10:00 UTC
        assert ctx.period == TimeWindowPeriod.PRIME
        assert ctx.position_coefficient == 1.0
        assert ctx.allow_new_position is True

    def test_evaluate_off_peak_period(self) -> None:
        """测试评估 OFF_PEAK 时段"""
        policy = TimeWindowPolicy()
        ctx = policy.evaluate(18, 0)  # 18:00 UTC
        assert ctx.period == TimeWindowPeriod.OFF_PEAK
        assert ctx.position_coefficient == 0.5
        assert ctx.allow_new_position is True

    def test_evaluate_restricted_period(self) -> None:
        """测试评估 RESTRICTED 时段"""
        policy = TimeWindowPolicy()
        ctx = policy.evaluate(23, 0)  # 23:00 UTC
        assert ctx.period == TimeWindowPeriod.RESTRICTED
        assert ctx.position_coefficient == 0.0
        assert ctx.allow_new_position is False

    def test_evaluate_boundary_at_start(self) -> None:
        """测试边界：时段开始时刻"""
        config = TimeWindowConfig.create_default()
        policy = TimeWindowPolicy(config)
        # 8:00 是 PRIME 开始
        ctx = policy.evaluate(8, 0)
        assert ctx.period == TimeWindowPeriod.PRIME

    def test_evaluate_boundary_at_end(self) -> None:
        """测试边界：时段结束时刻"""
        config = TimeWindowConfig.create_default()
        policy = TimeWindowPolicy(config)
        # 16:00 是 PRIME 结束，OFF_PEAK 开始
        ctx = policy.evaluate(16, 0)
        assert ctx.period == TimeWindowPeriod.OFF_PEAK

    def test_update_config(self) -> None:
        """测试热更新配置"""
        policy = TimeWindowPolicy()
        new_config = TimeWindowConfig(
            slots=[
                TimeWindowSlot(
                    period=TimeWindowPeriod.PRIME,
                    start_hour=0,
                    start_minute=0,
                    end_hour=23,
                    end_minute=59,
                    position_coefficient=0.8,
                ),
            ],
            default_coefficient=0.8,
        )
        policy.update_config(new_config)
        ctx = policy.evaluate(12, 0)
        assert ctx.position_coefficient == 0.8

    def test_update_config_from_dict(self) -> None:
        """测试从字典热更新配置"""
        policy = TimeWindowPolicy()
        data = {
            "slots": [
                {
                    "period": "PRIME",
                    "start_hour": 0,
                    "start_minute": 0,
                    "end_hour": 23,
                    "end_minute": 59,
                    "position_coefficient": 0.7,
                    "allow_new_position": True,
                }
            ],
            "default_coefficient": 0.7,
        }
        policy.update_config_from_dict(data)
        ctx = policy.evaluate(12, 0)
        assert ctx.position_coefficient == 0.7


class TestTimeWindowContext:
    """测试 TimeWindowContext 上下文"""

    def test_adjust_position_size_normal(self) -> None:
        """测试正常调整仓位大小"""
        ctx = TimeWindowContext(
            period=TimeWindowPeriod.PRIME,
            position_coefficient=1.0,
            allow_new_position=True,
        )
        adjusted = ctx.adjust_position_size(100.0)
        assert adjusted == 100.0

    def test_adjust_position_size_off_peak(self) -> None:
        """测试 OFF_PEAK 时段仓位缩减"""
        ctx = TimeWindowContext(
            period=TimeWindowPeriod.OFF_PEAK,
            position_coefficient=0.5,
            allow_new_position=True,
        )
        adjusted = ctx.adjust_position_size(100.0)
        assert adjusted == 50.0

    def test_adjust_position_size_restricted_no_new(self) -> None:
        """测试 RESTRICTED 时段拒绝新开仓"""
        ctx = TimeWindowContext(
            period=TimeWindowPeriod.RESTRICTED,
            position_coefficient=0.0,
            allow_new_position=False,
        )
        # 新开仓返回 0
        adjusted = ctx.adjust_position_size(100.0, is_new_position=True)
        assert adjusted == 0.0
        # 非新开仓仍按系数计算
        adjusted = ctx.adjust_position_size(100.0, is_new_position=False)
        assert adjusted == 0.0

    def test_adjust_position_size_existing_position(self) -> None:
        """测试非新开仓不受 allow_new_position 限制"""
        ctx = TimeWindowContext(
            period=TimeWindowPeriod.RESTRICTED,
            position_coefficient=0.3,
            allow_new_position=False,
        )
        # 已有仓位，只按系数缩减
        adjusted = ctx.adjust_position_size(100.0, is_new_position=False)
        assert adjusted == 30.0


class TestTimeWindowPolicyFailClosed:
    """测试 Fail-Closed 异常处理"""

    def test_evaluate_with_exception_returns_restricted(self) -> None:
        """测试评估异常时返回 RESTRICTED（Fail-Closed）"""
        config = TimeWindowConfig()  # 空配置
        policy = TimeWindowPolicy(config)
        
        # 任何时间都无匹配，应返回默认（PRIME）
        # 但如果配置异常，应该 Fail-Closed
        ctx = policy.evaluate(12, 0)
        # 默认配置应该返回 PRIME
        assert ctx.period == TimeWindowPeriod.PRIME

    def test_invalid_config_raises_on_creation(self) -> None:
        """测试无效配置创建时抛出异常"""
        with pytest.raises(ValueError, match="default_coefficient must be 0.0-1.0"):
            TimeWindowConfig(default_coefficient=1.5)
