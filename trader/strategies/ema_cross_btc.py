"""EMA Cross BTC strategy plugin."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from trader.core.application.strategy_protocol import (
    MarketData,
    RiskLevel,
    StrategyPlugin,
    StrategyResourceLimits,
    ValidationError,
    ValidationResult,
)
from trader.core.domain.models.signal import Signal, SignalType


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(slots=True)
class EmaCrossBtcStrategy:
    """BTC EMA 交叉策略：快线上穿慢线买入，下穿卖出。"""

    strategy_id: str = "ema_cross_btc"
    name: str = "EMA Cross BTC"
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.MEDIUM
    resource_limits: StrategyResourceLimits = field(
        default_factory=lambda: StrategyResourceLimits(
            max_position_size=Decimal("0.2"),
            max_daily_loss=Decimal("300"),
            max_orders_per_minute=6,
            timeout_seconds=1.0,
        )
    )

    fast_period: int = 12
    slow_period: int = 26
    order_size: Decimal = Decimal("0.001")
    min_confidence: Decimal = Decimal("0.65")

    _prices: list[Decimal] = field(default_factory=list)
    _last_regime: str | None = None

    async def initialize(self, config: dict[str, Any]) -> None:
        self._prices.clear()
        self._last_regime = None
        if config:
            result = await self.update_config(config)
            if not result.is_valid:
                raise ValueError(
                    f"EMA Cross 初始化参数无效: {[e.message for e in result.errors]}"
                )

    async def on_market_data(self, market_data: MarketData) -> Signal | None:
        self._prices.append(market_data.price)
        keep = max(self.slow_period * 3, self.slow_period + 2)
        if len(self._prices) > keep:
            self._prices = self._prices[-keep:]

        if len(self._prices) < self.slow_period:
            return None

        fast_ema = self._calculate_ema(self._prices, self.fast_period)
        slow_ema = self._calculate_ema(self._prices, self.slow_period)
        regime = "above" if fast_ema > slow_ema else "below"

        if self._last_regime is None:
            self._last_regime = regime
            return None

        if regime == self._last_regime:
            return None

        self._last_regime = regime
        signal_type = SignalType.BUY if regime == "above" else SignalType.SELL
        diff_ratio = abs(fast_ema - slow_ema) / market_data.price if market_data.price > 0 else Decimal("0")
        confidence = min(Decimal("0.95"), max(self.min_confidence, diff_ratio * Decimal("20")))

        return Signal(
            strategy_name=self.strategy_id,
            signal_type=signal_type,
            symbol=market_data.symbol,
            price=market_data.price,
            quantity=self.order_size,
            confidence=confidence,
            reason=f"EMA crossover: fast={fast_ema:.2f}, slow={slow_ema:.2f}",
            metadata={
                "fast_period": self.fast_period,
                "slow_period": self.slow_period,
                "fast_ema": str(fast_ema),
                "slow_ema": str(slow_ema),
            },
        )

    async def on_fill(
        self, order_id: str, symbol: str, side: str, quantity: float, price: float
    ) -> None:
        return None

    async def on_cancel(self, order_id: str, reason: str) -> None:
        return None

    async def shutdown(self) -> None:
        self._prices.clear()
        self._last_regime = None

    async def update_config(self, config: dict[str, Any]) -> ValidationResult:
        snapshot = (
            self.fast_period,
            self.slow_period,
            self.order_size,
            self.min_confidence,
        )
        try:
            self._apply_config(config)
            result = self.validate()
            if not result.is_valid:
                self.fast_period, self.slow_period, self.order_size, self.min_confidence = snapshot
            return result
        except Exception as exc:
            self.fast_period, self.slow_period, self.order_size, self.min_confidence = snapshot
            return ValidationResult.invalid(
                [
                    ValidationError(
                        field="config",
                        message=f"EMA Cross 参数更新失败: {exc}",
                        code="CONFIG_UPDATE_FAILED",
                    )
                ]
            )

    def validate(self) -> ValidationResult:
        errors: list[ValidationError] = []
        if self.fast_period < 2:
            errors.append(
                ValidationError(
                    field="fast_period",
                    message="fast_period 必须 >= 2",
                    code="FAST_PERIOD_INVALID",
                )
            )
        if self.slow_period <= self.fast_period:
            errors.append(
                ValidationError(
                    field="slow_period",
                    message="slow_period 必须大于 fast_period",
                    code="SLOW_PERIOD_INVALID",
                )
            )
        if self.order_size <= 0:
            errors.append(
                ValidationError(
                    field="order_size",
                    message="order_size 必须 > 0",
                    code="ORDER_SIZE_INVALID",
                )
            )
        if self.min_confidence <= 0 or self.min_confidence > 1:
            errors.append(
                ValidationError(
                    field="min_confidence",
                    message="min_confidence 必须在 (0, 1] 区间",
                    code="CONFIDENCE_INVALID",
                )
            )

        if errors:
            return ValidationResult.invalid(errors)
        return ValidationResult.valid()

    def _apply_config(self, config: dict[str, Any]) -> None:
        if "fast_period" in config:
            self.fast_period = int(config["fast_period"])
        if "slow_period" in config:
            self.slow_period = int(config["slow_period"])
        if "order_size" in config:
            self.order_size = _to_decimal(config["order_size"])
        if "min_confidence" in config:
            self.min_confidence = _to_decimal(config["min_confidence"])

    @staticmethod
    def _calculate_ema(values: list[Decimal], period: int) -> Decimal:
        window = values[-max(len(values), period):]
        multiplier = Decimal("2") / (Decimal(period) + Decimal("1"))
        ema = window[0]
        for price in window[1:]:
            ema = (price - ema) * multiplier + ema
        return ema


def create_plugin(**_kwargs) -> EmaCrossBtcStrategy:
    """每次调用返回一个新的策略实例，不能缓存模块级单例。"""
    return EmaCrossBtcStrategy()


def get_plugin() -> StrategyPlugin:
    """兼容旧 runner 的入口。必须返回新对象，不能返回单例。"""
    return create_plugin()
