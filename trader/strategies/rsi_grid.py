"""RSI Grid strategy plugin."""

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
class RsiGridStrategy:
    """RSI 网格策略：超卖买入，超买卖出，网格间距过滤。"""

    strategy_id: str = "rsi_grid"
    name: str = "RSI Grid"
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.MEDIUM
    resource_limits: StrategyResourceLimits = field(
        default_factory=lambda: StrategyResourceLimits(
            max_position_size=Decimal("0.3"),
            max_daily_loss=Decimal("250"),
            max_orders_per_minute=4,
            timeout_seconds=1.0,
        )
    )

    rsi_period: int = 14
    oversold: Decimal = Decimal("30")
    overbought: Decimal = Decimal("70")
    grid_step_pct: Decimal = Decimal("0.005")
    order_size: Decimal = Decimal("0.001")

    _prices: list[Decimal] = field(default_factory=list)
    _last_signal_price: Decimal | None = None

    async def initialize(self, config: dict[str, Any]) -> None:
        self._prices.clear()
        self._last_signal_price = None
        if config:
            result = await self.update_config(config)
            if not result.is_valid:
                raise ValueError(
                    f"RSI Grid 初始化参数无效: {[e.message for e in result.errors]}"
                )

    async def on_market_data(self, market_data: MarketData) -> Signal | None:
        price = market_data.price
        self._prices.append(price)
        keep = max(self.rsi_period * 4, self.rsi_period + 2)
        if len(self._prices) > keep:
            self._prices = self._prices[-keep:]

        if len(self._prices) <= self.rsi_period:
            return None

        if not self._passes_grid_filter(price):
            return None

        rsi = self._calculate_rsi(self._prices, self.rsi_period)
        signal_type: SignalType | None = None
        confidence = Decimal("0.6")
        reason = ""

        if rsi <= self.oversold:
            signal_type = SignalType.BUY
            confidence = min(Decimal("0.95"), Decimal("0.65") + (self.oversold - rsi) / Decimal("100"))
            reason = f"RSI oversold ({rsi:.2f}) <= {self.oversold}"
        elif rsi >= self.overbought:
            signal_type = SignalType.SELL
            confidence = min(Decimal("0.95"), Decimal("0.65") + (rsi - self.overbought) / Decimal("100"))
            reason = f"RSI overbought ({rsi:.2f}) >= {self.overbought}"

        if signal_type is None:
            return None

        self._last_signal_price = price
        return Signal(
            strategy_name=self.strategy_id,
            signal_type=signal_type,
            symbol=market_data.symbol,
            price=price,
            quantity=self.order_size,
            confidence=confidence,
            reason=reason,
            metadata={
                "rsi": str(rsi),
                "rsi_period": self.rsi_period,
                "grid_step_pct": str(self.grid_step_pct),
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
        self._last_signal_price = None

    async def update_config(self, config: dict[str, Any]) -> ValidationResult:
        snapshot = (
            self.rsi_period,
            self.oversold,
            self.overbought,
            self.grid_step_pct,
            self.order_size,
        )
        try:
            self._apply_config(config)
            result = self.validate()
            if not result.is_valid:
                (
                    self.rsi_period,
                    self.oversold,
                    self.overbought,
                    self.grid_step_pct,
                    self.order_size,
                ) = snapshot
            return result
        except Exception as exc:
            (
                self.rsi_period,
                self.oversold,
                self.overbought,
                self.grid_step_pct,
                self.order_size,
            ) = snapshot
            return ValidationResult.invalid(
                [
                    ValidationError(
                        field="config",
                        message=f"RSI Grid 参数更新失败: {exc}",
                        code="CONFIG_UPDATE_FAILED",
                    )
                ]
            )

    def validate(self) -> ValidationResult:
        errors: list[ValidationError] = []
        if self.rsi_period < 2:
            errors.append(
                ValidationError(
                    field="rsi_period",
                    message="rsi_period 必须 >= 2",
                    code="RSI_PERIOD_INVALID",
                )
            )
        if self.oversold <= 0 or self.oversold >= 100:
            errors.append(
                ValidationError(
                    field="oversold",
                    message="oversold 必须在 (0, 100) 区间",
                    code="OVERSOLD_INVALID",
                )
            )
        if self.overbought <= 0 or self.overbought >= 100:
            errors.append(
                ValidationError(
                    field="overbought",
                    message="overbought 必须在 (0, 100) 区间",
                    code="OVERBOUGHT_INVALID",
                )
            )
        if self.oversold >= self.overbought:
            errors.append(
                ValidationError(
                    field="thresholds",
                    message="oversold 必须小于 overbought",
                    code="THRESHOLD_ORDER_INVALID",
                )
            )
        if self.grid_step_pct < 0:
            errors.append(
                ValidationError(
                    field="grid_step_pct",
                    message="grid_step_pct 不能为负数",
                    code="GRID_STEP_INVALID",
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

        if errors:
            return ValidationResult.invalid(errors)
        return ValidationResult.valid()

    def _apply_config(self, config: dict[str, Any]) -> None:
        if "rsi_period" in config:
            self.rsi_period = int(config["rsi_period"])
        if "oversold" in config:
            self.oversold = _to_decimal(config["oversold"])
        if "overbought" in config:
            self.overbought = _to_decimal(config["overbought"])
        if "grid_step_pct" in config:
            self.grid_step_pct = _to_decimal(config["grid_step_pct"])
        if "order_size" in config:
            self.order_size = _to_decimal(config["order_size"])

    def _passes_grid_filter(self, price: Decimal) -> bool:
        if self._last_signal_price is None:
            return True
        if self._last_signal_price <= 0:
            return False
        move = abs((price - self._last_signal_price) / self._last_signal_price)
        return move >= self.grid_step_pct

    @staticmethod
    def _calculate_rsi(prices: list[Decimal], period: int) -> Decimal:
        window = prices[-(period + 1) :]
        gains = Decimal("0")
        losses = Decimal("0")
        for idx in range(1, len(window)):
            delta = window[idx] - window[idx - 1]
            if delta > 0:
                gains += delta
            elif delta < 0:
                losses += abs(delta)

        avg_gain = gains / Decimal(period)
        avg_loss = losses / Decimal(period)
        if avg_loss == 0:
            return Decimal("100")
        if avg_gain == 0:
            return Decimal("0")
        rs = avg_gain / avg_loss
        return Decimal("100") - (Decimal("100") / (Decimal("1") + rs))


def create_plugin(**_kwargs) -> RsiGridStrategy:
    """每次调用返回一个新的策略实例，不能缓存模块级单例。"""
    return RsiGridStrategy()


def get_plugin() -> StrategyPlugin:
    """兼容旧 runner 的入口。必须返回新对象，不能返回单例。"""
    return create_plugin()
