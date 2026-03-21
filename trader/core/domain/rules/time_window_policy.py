"""
TimeWindowPolicy - 时间窗口风控策略
===================================
根据 UTC 时段动态调整仓位系数的领域模型。

时段定义：
- PRIME：主力时段，仓位系数 1.0
- OFF_PEAK：低流动性时段，仓位系数可配置（默认 0.5）
- RESTRICTED：禁止新开仓时段

约束：
- Core Plane 禁止 IO
- Fail-Closed 异常处理
- 支持热更新配置
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from enum import Enum
from typing import Optional, Dict, Any


class TimeWindowPeriod(Enum):
    """时间窗口时段枚举"""
    PRIME = "PRIME"           # 主力时段
    OFF_PEAK = "OFF_PEAK"     # 低流动性时段
    RESTRICTED = "RESTRICTED" # 禁止新开仓时段


@dataclass(frozen=True)
class TimeWindowSlot:
    """
    单个时段槽定义
    
    Attributes:
        period: 时段类型
        start_hour: 开始小时 (UTC)
        start_minute: 开始分钟
        end_hour: 结束小时 (UTC)
        end_minute: 结束分钟
        position_coefficient: 仓位系数 (0.0-1.0)
        allow_new_position: 是否允许新开仓
    """
    period: TimeWindowPeriod
    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int
    position_coefficient: float = 1.0
    allow_new_position: bool = True

    def __post_init__(self) -> None:
        """验证时段配置"""
        if not 0 <= self.start_hour <= 23:
            raise ValueError(f"start_hour must be 0-23, got {self.start_hour}")
        if not 0 <= self.start_minute <= 59:
            raise ValueError(f"start_minute must be 0-59, got {self.start_minute}")
        if not 0 <= self.end_hour <= 23:
            raise ValueError(f"end_hour must be 0-23, got {self.end_hour}")
        if not 0 <= self.end_minute <= 59:
            raise ValueError(f"end_minute must be 0-59, got {self.end_minute}")
        if not 0.0 <= self.position_coefficient <= 1.0:
            raise ValueError(
                f"position_coefficient must be 0.0-1.0, got {self.position_coefficient}"
            )

    def contains(self, hour: int, minute: int) -> bool:
        """
        检查给定时间是否在此时段内
        
        边界处理：
        - 普通时段 [start, end)：开始时间 inclusive，结束时间 exclusive
        - 跨天时段 [start, 24:00) ∪ [00:00, end)：开始时间 inclusive，结束时间 exclusive
        
        Args:
            hour: 小时 (UTC)
            minute: 分钟
            
        Returns:
            bool: 是否在时段内
        """
        start = self.start_hour * 60 + self.start_minute
        end = self.end_hour * 60 + self.end_minute
        current = hour * 60 + minute
        
        if start <= end:
            # 普通时段：例如 9:00-17:00
            return start <= current < end
        else:
            # 跨天时段：例如 22:00-06:00
            # 例如 RESTRICTED 22:00-8:00 表示 22:00-24:00 和 00:00-08:00
            return current >= start or current < end

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "period": self.period.value,
            "start_hour": self.start_hour,
            "start_minute": self.start_minute,
            "end_hour": self.end_hour,
            "end_minute": self.end_minute,
            "position_coefficient": self.position_coefficient,
            "allow_new_position": self.allow_new_position,
        }


@dataclass
class TimeWindowConfig:
    """
    时间窗口配置
    
    支持热更新，可通过 Control Plane API 动态调整。
    
    Attributes:
        slots: 时段槽列表（按优先级排序，RESTRICTED 优先）
        default_coefficient: 未匹配时段的默认系数
    """
    slots: list[TimeWindowSlot] = field(default_factory=list)
    default_coefficient: float = 1.0

    def __post_init__(self) -> None:
        """验证配置"""
        if not 0.0 <= self.default_coefficient <= 1.0:
            raise ValueError(
                f"default_coefficient must be 0.0-1.0, got {self.default_coefficient}"
            )
        # 按优先级排序：RESTRICTED > OFF_PEAK > PRIME
        self._sort_slots()

    def _sort_slots(self) -> None:
        """按优先级排序时段槽"""
        priority = {
            TimeWindowPeriod.RESTRICTED: 0,
            TimeWindowPeriod.OFF_PEAK: 1,
            TimeWindowPeriod.PRIME: 2,
        }
        self.slots = sorted(
            self.slots, 
            key=lambda s: priority.get(s.period, 99)
        )

    def add_slot(self, slot: TimeWindowSlot) -> None:
        """
        添加时段槽
        
        Args:
            slot: 时段槽
        """
        self.slots.append(slot)
        self._sort_slots()

    def get_slot_at(self, hour: int, minute: int) -> Optional[TimeWindowSlot]:
        """
        获取指定时间对应的时段槽
        
        Args:
            hour: 小时 (UTC)
            minute: 分钟
            
        Returns:
            Optional[TimeWindowSlot]: 匹配的时段槽，未匹配返回 None
        """
        for slot in self.slots:
            if slot.contains(hour, minute):
                return slot
        return None

    def get_default_slot(self) -> TimeWindowSlot:
        """
        获取默认时段槽（用于未匹配时段）
        
        Returns:
            TimeWindowSlot: 默认时段槽
        """
        return TimeWindowSlot(
            period=TimeWindowPeriod.PRIME,
            start_hour=0,
            start_minute=0,
            end_hour=23,
            end_minute=59,
            position_coefficient=self.default_coefficient,
            allow_new_position=True,
        )

    @classmethod
    def create_default(cls) -> TimeWindowConfig:
        """
        创建默认配置（币圈 24h 交易场景）
        
        Returns:
            TimeWindowConfig: 默认配置
        """
        # 币圈默认配置：
        # - PRIME: 8:00-16:00 UTC (亚洲+欧洲主力时段)
        # - OFF_PEAK: 16:00-22:00 UTC (美国时段开始前)
        # - RESTRICTED: 22:00-8:00 UTC (深度可能不足的时段)
        return cls(
            slots=[
                TimeWindowSlot(
                    period=TimeWindowPeriod.PRIME,
                    start_hour=8,
                    start_minute=0,
                    end_hour=16,
                    end_minute=0,
                    position_coefficient=1.0,
                    allow_new_position=True,
                ),
                TimeWindowSlot(
                    period=TimeWindowPeriod.OFF_PEAK,
                    start_hour=16,
                    start_minute=0,
                    end_hour=22,
                    end_minute=0,
                    position_coefficient=0.5,
                    allow_new_position=True,
                ),
                TimeWindowSlot(
                    period=TimeWindowPeriod.RESTRICTED,
                    start_hour=22,
                    start_minute=0,
                    end_hour=8,
                    end_minute=0,
                    position_coefficient=0.0,
                    allow_new_position=False,
                ),
            ],
            default_coefficient=1.0,
        )

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "slots": [s.to_dict() for s in self.slots],
            "default_coefficient": self.default_coefficient,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TimeWindowConfig:
        """
        从字典反序列化
        
        Args:
            data: 配置字典
            
        Returns:
            TimeWindowConfig: 时间窗口配置
        """
        slots = [
            TimeWindowSlot(
                period=TimeWindowPeriod(s["period"]),
                start_hour=s["start_hour"],
                start_minute=s["start_minute"],
                end_hour=s["end_hour"],
                end_minute=s["end_minute"],
                position_coefficient=s.get("position_coefficient", 1.0),
                allow_new_position=s.get("allow_new_position", True),
            )
            for s in data.get("slots", [])
        ]
        return cls(
            slots=slots,
            default_coefficient=data.get("default_coefficient", 1.0),
        )


@dataclass(frozen=True)
class TimeWindowContext:
    """
    时间窗口上下文
    
    由 TimeWindowPolicy.evaluate() 返回，作为 RiskEngine 的输入。
    
    Attributes:
        period: 当前时段
        position_coefficient: 仓位系数
        allow_new_position: 是否允许新开仓
    """
    period: TimeWindowPeriod
    position_coefficient: float
    allow_new_position: bool

    def adjust_position_size(
        self, 
        base_size: float,
        is_new_position: bool = False
    ) -> float:
        """
        根据时间窗口调整仓位大小
        
        Args:
            base_size: 基础仓位大小
            is_new_position: 是否为新开仓
            
        Returns:
            float: 调整后的仓位大小
        """
        if is_new_position and not self.allow_new_position:
            return 0.0
        return base_size * self.position_coefficient

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "period": self.period.value,
            "position_coefficient": self.position_coefficient,
            "allow_new_position": self.allow_new_position,
        }


class TimeWindowPolicy:
    """
    时间窗口策略评估器
    
    根据当前 UTC 时间评估时段上下文。
    
    约束：
    - Core Plane 禁止 IO
    - Fail-Closed：配置异常时默认拒绝新开仓
    """

    def __init__(
        self,
        config: Optional[TimeWindowConfig] = None,
    ) -> None:
        """
        初始化时间窗口策略
        
        Args:
            config: 时间窗口配置，None 则使用默认配置
        """
        self._config = config or TimeWindowConfig.create_default()

    @property
    def config(self) -> TimeWindowConfig:
        """获取配置（只读）"""
        return self._config

    def update_config(self, config: TimeWindowConfig) -> None:
        """
        热更新配置
        
        Args:
            config: 新的时间窗口配置
        """
        if not isinstance(config, TimeWindowConfig):
            raise TypeError(f"Expected TimeWindowConfig, got {type(config)}")
        self._config = config

    def update_config_from_dict(self, data: Dict[str, Any]) -> None:
        """
        从字典热更新配置
        
        Args:
            data: 配置字典
        """
        self._config = TimeWindowConfig.from_dict(data)

    def evaluate(self, hour: int, minute: int) -> TimeWindowContext:
        """
        评估指定时间的时段上下文
        
        Args:
            hour: 小时 (UTC)
            minute: 分钟
            
        Returns:
            TimeWindowContext: 时间窗口上下文
        """
        try:
            slot = self._config.get_slot_at(hour, minute)
            
            if slot is None:
                # 未匹配到时段，使用默认
                default_slot = self._config.get_default_slot()
                return TimeWindowContext(
                    period=TimeWindowPeriod.PRIME,
                    position_coefficient=default_slot.position_coefficient,
                    allow_new_position=True,
                )
            
            return TimeWindowContext(
                period=slot.period,
                position_coefficient=slot.position_coefficient,
                allow_new_position=slot.allow_new_position,
            )
        except Exception:
            # Fail-Closed：异常时默认拒绝新开仓
            return TimeWindowContext(
                period=TimeWindowPeriod.RESTRICTED,
                position_coefficient=0.0,
                allow_new_position=False,
            )

    def evaluate_now(self) -> TimeWindowContext:
        """
        评估当前时刻的时段上下文
        
        Returns:
            TimeWindowContext: 当前时间窗口上下文
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        return self.evaluate(now.hour, now.minute)
