"""
Fake Strategy Plugin - 用于测试
================================

提供一个可配置的 Mock 策略插件，用于 StrategyRunner 的单元测试。
"""
from typing import Any, Dict, Optional

from trader.core.domain.models.signal import Signal, SignalType
from trader.core.application.strategy_protocol import (
    MarketData,
    RiskLevel,
    StrategyPlugin,
    StrategyResourceLimits,
    ValidationResult,
)

# 全局配置，测试时可以修改
_config = {
    "return_signal": True,
    "raise_on_tick": False,
    "raise_on_initialize": False,
}

# 全局计数器
_counters = {
    "initialize": 0,
    "tick": 0,
    "fill": 0,
    "cancel": 0,
    "shutdown": 0,
}


def reset_counters():
    """重置计数器"""
    _counters["initialize"] = 0
    _counters["tick"] = 0
    _counters["fill"] = 0
    _config["return_signal"] = True
    _config["raise_on_tick"] = False
    _config["raise_on_initialize"] = False


def set_return_signal(value: bool):
    """设置是否返回信号"""
    _config["return_signal"] = value


def set_raise_on_tick(value: bool):
    """设置是否在 tick 时抛出异常"""
    _config["raise_on_tick"] = value


class FakeStrategyPlugin:
    """Fake 策略插件 - 实现 StrategyPlugin 协议"""

    def __init__(self):
        self.strategy_id = ""
        self.name = "FakeStrategy"
        self.version = "1.0.0"
        self.risk_level = RiskLevel.LOW
        self.resource_limits = StrategyResourceLimits()
        self._valid = True

    def validate(self) -> ValidationResult:
        """策略有效性验证"""
        if self._valid:
            return ValidationResult.valid()
        return ValidationResult.invalid(errors=[])

    async def initialize(self, config: Dict[str, Any]) -> None:
        _counters["initialize"] += 1
        if _config["raise_on_initialize"]:
            raise RuntimeError("Fake initialize error")

    async def on_market_data(self, market_data: MarketData) -> Optional[Signal]:
        _counters["tick"] += 1

        if _config["raise_on_tick"]:
            raise RuntimeError("Fake tick error")

        if _config["return_signal"]:
            return Signal(
                strategy_name=self.strategy_id or self.name,
                signal_type=SignalType.BUY,
                symbol=market_data.symbol,
                price=market_data.price,
                quantity=0.1,
                confidence=0.8,
                reason="Fake signal",
            )
        return None

    async def on_fill(
        self, order_id: str, symbol: str, side: str, quantity: float, price: float
    ) -> None:
        _counters["fill"] += 1

    async def on_cancel(self, order_id: str, reason: str) -> None:
        _counters["cancel"] += 1

    async def shutdown(self) -> None:
        _counters["shutdown"] += 1

    async def update_config(self, config: Dict[str, Any]) -> ValidationResult:
        """更新策略配置（Task 4.7 动态参数调整）"""
        return ValidationResult.valid()


_plugin_instance = FakeStrategyPlugin()


def get_plugin() -> StrategyPlugin:
    """获取插件实例（模块级工厂函数）"""
    return _plugin_instance
