"""DCA BTC strategy plugin."""

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
class DcaBtcStrategy:
    """BTC 定投策略：固定节奏买入，叠加价格回撤加仓。"""

    strategy_id: str = "dca_btc"
    name: str = "DCA BTC"
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    resource_limits: StrategyResourceLimits = field(
        default_factory=lambda: StrategyResourceLimits(
            max_position_size=Decimal("0.5"),
            max_daily_loss=Decimal("200"),
            max_orders_per_minute=2,
            timeout_seconds=1.0,
        )
    )

    interval_seconds: int = 3600
    base_order_size: Decimal = Decimal("0.0005")
    dip_threshold_pct: Decimal = Decimal("0.02")
    dip_multiplier: Decimal = Decimal("1.5")
    min_buy_gap_seconds: int = 300
    max_total_buys: int = 0

    _last_buy_timestamp: datetime | None = None
    _last_buy_price: Decimal | None = None
    _buy_count: int = 0

    async def initialize(self, config: dict[str, Any]) -> None:
        self._last_buy_timestamp = None
        self._last_buy_price = None
        self._buy_count = 0
        if config:
            result = await self.update_config(config)
            if not result.is_valid:
                raise ValueError(
                    f"DCA 初始化参数无效: {[e.message for e in result.errors]}"
                )

    async def on_market_data(self, market_data: MarketData) -> Signal | None:
        if self.max_total_buys > 0 and self._buy_count >= self.max_total_buys:
            return None

        now_ts = market_data.timestamp
        price = market_data.price
        regular_due = False
        dip_due = False

        if self._last_buy_timestamp is None:
            regular_due = True
        else:
            elapsed = (now_ts - self._last_buy_timestamp).total_seconds()
            if elapsed < 0:
                return None
            regular_due = elapsed >= self.interval_seconds
            dip_due = (
                elapsed >= self.min_buy_gap_seconds
                and self._last_buy_price is not None
                and self._last_buy_price > 0
                and price <= self._last_buy_price * (Decimal("1") - self.dip_threshold_pct)
            )

        if not regular_due and not dip_due:
            return None

        quantity = self.base_order_size
        reason = "DCA regular interval"
        confidence = Decimal("0.7")
        if dip_due:
            quantity = self.base_order_size * self.dip_multiplier
            reason = f"DCA dip buy, price dropped >= {self.dip_threshold_pct * Decimal('100')}%"
            confidence = Decimal("0.85")

        self._last_buy_timestamp = now_ts
        self._last_buy_price = price
        self._buy_count += 1

        return Signal(
            strategy_name=self.strategy_id,
            signal_type=SignalType.BUY,
            symbol=market_data.symbol,
            price=price,
            quantity=quantity,
            confidence=confidence,
            reason=reason,
            metadata={
                "buy_count": self._buy_count,
                "interval_seconds": self.interval_seconds,
                "dip_threshold_pct": str(self.dip_threshold_pct),
                "dip_multiplier": str(self.dip_multiplier),
            },
        )

    async def on_fill(
        self, order_id: str, symbol: str, side: str, quantity: float, price: float
    ) -> None:
        return None

    async def on_cancel(self, order_id: str, reason: str) -> None:
        return None

    async def shutdown(self) -> None:
        self._last_buy_timestamp = None
        self._last_buy_price = None
        self._buy_count = 0

    async def update_config(self, config: dict[str, Any]) -> ValidationResult:
        snapshot = (
            self.interval_seconds,
            self.base_order_size,
            self.dip_threshold_pct,
            self.dip_multiplier,
            self.min_buy_gap_seconds,
            self.max_total_buys,
        )
        try:
            self._apply_config(config)
            result = self.validate()
            if not result.is_valid:
                (
                    self.interval_seconds,
                    self.base_order_size,
                    self.dip_threshold_pct,
                    self.dip_multiplier,
                    self.min_buy_gap_seconds,
                    self.max_total_buys,
                ) = snapshot
            return result
        except Exception as exc:
            (
                self.interval_seconds,
                self.base_order_size,
                self.dip_threshold_pct,
                self.dip_multiplier,
                self.min_buy_gap_seconds,
                self.max_total_buys,
            ) = snapshot
            return ValidationResult.invalid(
                [
                    ValidationError(
                        field="config",
                        message=f"DCA 参数更新失败: {exc}",
                        code="CONFIG_UPDATE_FAILED",
                    )
                ]
            )

    def validate(self) -> ValidationResult:
        errors: list[ValidationError] = []
        if self.interval_seconds <= 0:
            errors.append(
                ValidationError(
                    field="interval_seconds",
                    message="interval_seconds 必须 > 0",
                    code="INTERVAL_INVALID",
                )
            )
        if self.base_order_size <= 0:
            errors.append(
                ValidationError(
                    field="base_order_size",
                    message="base_order_size 必须 > 0",
                    code="ORDER_SIZE_INVALID",
                )
            )
        if self.dip_threshold_pct < 0 or self.dip_threshold_pct >= 1:
            errors.append(
                ValidationError(
                    field="dip_threshold_pct",
                    message="dip_threshold_pct 必须在 [0, 1) 区间",
                    code="DIP_THRESHOLD_INVALID",
                )
            )
        if self.dip_multiplier < 1:
            errors.append(
                ValidationError(
                    field="dip_multiplier",
                    message="dip_multiplier 必须 >= 1",
                    code="DIP_MULTIPLIER_INVALID",
                )
            )
        if self.min_buy_gap_seconds < 0:
            errors.append(
                ValidationError(
                    field="min_buy_gap_seconds",
                    message="min_buy_gap_seconds 不能为负数",
                    code="BUY_GAP_INVALID",
                )
            )
        if self.max_total_buys < 0:
            errors.append(
                ValidationError(
                    field="max_total_buys",
                    message="max_total_buys 不能为负数",
                    code="MAX_BUYS_INVALID",
                )
            )

        if errors:
            return ValidationResult.invalid(errors)
        return ValidationResult.valid()

    def _apply_config(self, config: dict[str, Any]) -> None:
        if "interval_seconds" in config:
            self.interval_seconds = int(config["interval_seconds"])
        if "base_order_size" in config:
            self.base_order_size = _to_decimal(config["base_order_size"])
        if "dip_threshold_pct" in config:
            self.dip_threshold_pct = _to_decimal(config["dip_threshold_pct"])
        if "dip_multiplier" in config:
            self.dip_multiplier = _to_decimal(config["dip_multiplier"])
        if "min_buy_gap_seconds" in config:
            self.min_buy_gap_seconds = int(config["min_buy_gap_seconds"])
        if "max_total_buys" in config:
            self.max_total_buys = int(config["max_total_buys"])


def create_plugin(**_kwargs) -> DcaBtcStrategy:
    """每次调用返回一个新的策略实例，不能缓存模块级单例。"""
    return DcaBtcStrategy()


def get_plugin() -> StrategyPlugin:
    """兼容旧 runner 的入口。必须返回新对象，不能返回单例。"""
    return create_plugin()
