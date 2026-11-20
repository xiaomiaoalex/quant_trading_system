"""Observation-only strategy plugin.

Continuation segments keep the execution runtime alive (private stream,
reconciliation, coverage proof) without creating any new risk. This plugin
never emits a signal. It declares a zero-length warm-up so the runtime can
start it without replaying historical bars.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from trader.core.application.strategy_protocol import (
    MarketData,
    RiskLevel,
    StrategyResourceLimits,
    ValidationResult,
)


@dataclass(slots=True)
class ObservationOnlyStrategy:
    strategy_id: str = "observation_only"
    name: str = "Observation Only"
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    resource_limits: StrategyResourceLimits = field(
        default_factory=lambda: StrategyResourceLimits(
            max_position_size=Decimal("0"),
            max_daily_loss=Decimal("0"),
            max_orders_per_minute=0,
            timeout_seconds=1.0,
        )
    )

    @property
    def stateful_indicators(self) -> bool:
        return False

    @property
    def warmup_events(self) -> int:
        return 0

    async def initialize(self, config: dict[str, Any]) -> None:
        return None

    async def on_market_data(self, market_data: MarketData) -> None:
        # Observation-only: never emit a signal, never create a new order.
        return None

    async def on_fill(
        self, order_id: str, symbol: str, side: str, quantity: float, price: float
    ) -> None:
        return None

    async def on_cancel(self, order_id: str, reason: str) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def update_config(self, config: dict[str, Any]) -> ValidationResult:
        return ValidationResult.valid()

    def validate(self) -> ValidationResult:
        return ValidationResult.valid()

    def validate_config(self, config: dict[str, Any]) -> ValidationResult:
        return ValidationResult.valid()


def create_plugin(**_kwargs) -> ObservationOnlyStrategy:
    """Return a fresh instance; module-level singletons are never reused."""
    return ObservationOnlyStrategy()


def get_plugin() -> ObservationOnlyStrategy:
    return create_plugin()
