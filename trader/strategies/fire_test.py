"""Fire Test strategy plugin."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
class FireTestStrategy:
    """
    开火测试策略：
    - 固定节奏发出 BUY / SELL 信号
    - 用于验证策略 -> OMS -> 交易所的真实下单链路
    """

    strategy_id: str = "fire_test"
    name: str = "Fire Test"
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.MEDIUM
    resource_limits: StrategyResourceLimits = field(
        default_factory=lambda: StrategyResourceLimits(
            max_position_size=Decimal("0.02"),
            max_daily_loss=Decimal("50"),
            max_orders_per_minute=2,
            timeout_seconds=1.0,
        )
    )

    # BUY / SELL / ALTERNATE
    mode: str = "ALTERNATE"
    # ALTERNATE 模式首次方向：BUY / SELL
    start_with: str = "BUY"
    # 发信号最小间隔（秒）
    interval_seconds: int = 30
    # 每次建议下单数量（0.01 满足大多数交易对最低名义金额要求）
    order_size: Decimal = Decimal("0.01")
    # 信号置信度
    min_confidence: Decimal = Decimal("0.95")
    # 最大发信号次数（0 表示不限制）
    max_signals: int = 2

    _last_emit_ts: datetime | None = None
    _last_signal_type: SignalType | None = None
    _emit_count: int = 0

    async def initialize(self, config: dict[str, Any]) -> None:
        self._last_emit_ts = None
        self._last_signal_type = None
        self._emit_count = 0
        if config:
            result = await self.update_config(config)
            if not result.is_valid:
                raise ValueError(
                    f"Fire Test 初始化参数无效: {[e.message for e in result.errors]}"
                )

    async def on_market_data(self, market_data: MarketData) -> Signal | None:
        if self.max_signals > 0 and self._emit_count >= self.max_signals:
            return None

        if self._last_emit_ts is not None:
            elapsed = (market_data.timestamp - self._last_emit_ts).total_seconds()
            if elapsed < self.interval_seconds:
                return None

        signal_type = self._next_signal_type()
        self._last_emit_ts = market_data.timestamp
        self._last_signal_type = signal_type
        self._emit_count += 1

        # 动态调整交易量：确保买卖数量匹配，避免余额不足
        if self._emit_count == 1 and signal_type == SignalType.BUY:
            # 第一次买入：使用满足最小名义金额的交易量
            adjusted_size = Decimal("0.0001")  # 约 7.5 USDT
        elif self._emit_count == 2 and signal_type == SignalType.SELL:
            # 第二次卖出：使用与第一次买入相同的数量，避免余额不足
            adjusted_size = Decimal("0.0001")
        else:
            adjusted_size = self.order_size

        return Signal(
            strategy_name=self.strategy_id,
            signal_type=signal_type,
            symbol=market_data.symbol,
            price=market_data.price,
            quantity=adjusted_size,
            confidence=self.min_confidence,
            reason=(
                f"Fire test emit #{self._emit_count}: mode={self.mode}, "
                f"signal={signal_type.value}, size={adjusted_size}"
            ),
            metadata={
                "emit_count": self._emit_count,
                "mode": self.mode,
                "start_with": self.start_with,
                "interval_seconds": self.interval_seconds,
                "max_signals": self.max_signals,
                "adjusted_size": str(adjusted_size),
            },
        )

    async def on_fill(
        self, order_id: str, symbol: str, side: str, quantity: float, price: float
    ) -> None:
        return None

    async def on_cancel(self, order_id: str, reason: str) -> None:
        return None

    async def shutdown(self) -> None:
        self._last_emit_ts = None
        self._last_signal_type = None
        self._emit_count = 0

    async def update_config(self, config: dict[str, Any]) -> ValidationResult:
        snapshot = (
            self.mode,
            self.start_with,
            self.interval_seconds,
            self.order_size,
            self.min_confidence,
            self.max_signals,
        )
        try:
            self._apply_config(config)
            result = self.validate()
            if not result.is_valid:
                (
                    self.mode,
                    self.start_with,
                    self.interval_seconds,
                    self.order_size,
                    self.min_confidence,
                    self.max_signals,
                ) = snapshot
            return result
        except Exception as exc:
            (
                self.mode,
                self.start_with,
                self.interval_seconds,
                self.order_size,
                self.min_confidence,
                self.max_signals,
            ) = snapshot
            return ValidationResult.invalid(
                [
                    ValidationError(
                        field="config",
                        message=f"Fire Test 参数更新失败: {exc}",
                        code="CONFIG_UPDATE_FAILED",
                    )
                ]
            )

    def validate(self) -> ValidationResult:
        errors: list[ValidationError] = []

        if self.mode not in {"BUY", "SELL", "ALTERNATE"}:
            errors.append(
                ValidationError(
                    field="mode",
                    message="mode 必须是 BUY / SELL / ALTERNATE 之一",
                    code="MODE_INVALID",
                )
            )
        if self.start_with not in {"BUY", "SELL"}:
            errors.append(
                ValidationError(
                    field="start_with",
                    message="start_with 必须是 BUY 或 SELL",
                    code="START_WITH_INVALID",
                )
            )
        if self.interval_seconds <= 0:
            errors.append(
                ValidationError(
                    field="interval_seconds",
                    message="interval_seconds 必须 > 0",
                    code="INTERVAL_INVALID",
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
        if self.max_signals < 0:
            errors.append(
                ValidationError(
                    field="max_signals",
                    message="max_signals 不能为负数",
                    code="MAX_SIGNALS_INVALID",
                )
            )

        if errors:
            return ValidationResult.invalid(errors)
        return ValidationResult.valid()

    def _apply_config(self, config: dict[str, Any]) -> None:
        if "mode" in config:
            self.mode = str(config["mode"]).upper()
        if "start_with" in config:
            self.start_with = str(config["start_with"]).upper()
        if "interval_seconds" in config:
            self.interval_seconds = int(config["interval_seconds"])
        if "order_size" in config:
            self.order_size = _to_decimal(config["order_size"])
        if "min_confidence" in config:
            self.min_confidence = _to_decimal(config["min_confidence"])
        if "max_signals" in config:
            self.max_signals = int(config["max_signals"])

    def _next_signal_type(self) -> SignalType:
        if self.mode == "BUY":
            return SignalType.BUY
        if self.mode == "SELL":
            return SignalType.SELL

        if self._last_signal_type is None:
            return SignalType.BUY if self.start_with == "BUY" else SignalType.SELL

        return SignalType.SELL if self._last_signal_type == SignalType.BUY else SignalType.BUY


def create_plugin(**_kwargs) -> FireTestStrategy:
    """每次调用返回一个新的策略实例，不能缓存模块级单例。"""
    return FireTestStrategy()


def get_plugin() -> StrategyPlugin:
    """兼容旧 runner 的入口。必须返回新对象，不能返回单例。"""
    return create_plugin()

