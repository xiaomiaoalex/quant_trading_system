from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from trader.core.domain.models.crypto_risk import (
    CryptoAccountRisk,
    CryptoPositionRisk,
    LeverageBracket,
)
from trader.core.domain.models.risk_mode import RiskMode
from trader.core.domain.services.margin_risk_calculator import MarginRiskCalculator
from trader.core.domain.services.risk_mode_controller import RiskModeController


class MonitorSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class MonitorResult:
    event_type: str
    severity: MonitorSeverity
    triggered: bool
    current_mode: RiskMode
    escalation_target: RiskMode | None = None
    trace_id: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IntradayRiskMonitorConfig:
    mark_price_drop_close_only: Decimal = Decimal("0.10")
    mark_price_drop_cancel_all: Decimal = Decimal("0.20")
    mark_price_drop_liquidate: Decimal = Decimal("0.30")
    open_order_spike_threshold: int = 50
    open_order_critical_threshold: int = 100
    ws_silence_close_only_seconds: int = 30
    ws_silence_cancel_all_seconds: int = 60
    margin_ratio_close_only: Decimal = Decimal("0.80")
    margin_ratio_liquidate: Decimal = Decimal("0.95")
    drawdown_close_only: Decimal = Decimal("0.20")
    drawdown_liquidate: Decimal = Decimal("0.30")
    liquidation_buffer_close_only: Decimal = Decimal("0.05")
    liquidation_buffer_liquidate: Decimal = Decimal("0.02")


class IntradayRiskMonitor:
    """Core, deterministic intraday risk monitor.

    The monitor has no IO. Runtime layers provide explicit metrics, and this service
    translates those metrics into RiskMode escalation requests.
    """

    def __init__(
        self,
        controller: RiskModeController,
        config: IntradayRiskMonitorConfig | None = None,
        margin_calculator: MarginRiskCalculator | None = None,
    ) -> None:
        self._controller = controller
        self._config = config or IntradayRiskMonitorConfig()
        self._margin_calculator = margin_calculator or MarginRiskCalculator()

    def check_mark_price_drop(
        self,
        symbol: str,
        previous_mark_price: Decimal | None,
        current_mark_price: Decimal | None,
        trace_id: str = "",
    ) -> MonitorResult:
        if previous_mark_price is None or current_mark_price is None or previous_mark_price <= 0:
            return self._escalate(
                event_type="mark_price_drop",
                severity=MonitorSeverity.HIGH,
                target=RiskMode.CLOSE_ONLY,
                reason="MARK_PRICE_MISSING",
                trace_id=trace_id,
                metadata={"symbol": symbol},
            )
        if current_mark_price <= 0:
            return self._escalate(
                event_type="mark_price_drop",
                severity=MonitorSeverity.CRITICAL,
                target=RiskMode.LIQUIDATE_AND_DISCONNECT,
                reason="INVALID_MARK_PRICE",
                trace_id=trace_id,
                metadata={"symbol": symbol},
            )

        drop_ratio = (previous_mark_price - current_mark_price) / previous_mark_price
        metadata = {
            "symbol": symbol,
            "previous_mark_price": previous_mark_price,
            "current_mark_price": current_mark_price,
            "drop_ratio": drop_ratio,
        }
        if drop_ratio >= self._config.mark_price_drop_liquidate:
            return self._escalate(
                "mark_price_drop",
                MonitorSeverity.CRITICAL,
                RiskMode.LIQUIDATE_AND_DISCONNECT,
                "MARK_PRICE_DROP_EXTREME",
                trace_id,
                metadata,
            )
        if drop_ratio >= self._config.mark_price_drop_cancel_all:
            return self._escalate(
                "mark_price_drop",
                MonitorSeverity.HIGH,
                RiskMode.CANCEL_ALL_AND_HALT,
                "MARK_PRICE_DROP_SEVERE",
                trace_id,
                metadata,
            )
        if drop_ratio >= self._config.mark_price_drop_close_only:
            return self._escalate(
                "mark_price_drop",
                MonitorSeverity.MEDIUM,
                RiskMode.CLOSE_ONLY,
                "MARK_PRICE_DROP",
                trace_id,
                metadata,
            )
        return self._ok("mark_price_drop", trace_id, metadata)

    def check_open_order_spike(
        self,
        symbol: str,
        open_order_count: int | None,
        trace_id: str = "",
    ) -> MonitorResult:
        metadata = {"symbol": symbol, "open_order_count": open_order_count}
        if open_order_count is None or open_order_count < 0:
            return self._escalate(
                "open_order_spike",
                MonitorSeverity.HIGH,
                RiskMode.CLOSE_ONLY,
                "OPEN_ORDER_COUNT_MISSING",
                trace_id,
                metadata,
            )
        if open_order_count >= self._config.open_order_critical_threshold:
            return self._escalate(
                "open_order_spike",
                MonitorSeverity.CRITICAL,
                RiskMode.CANCEL_ALL_AND_HALT,
                "OPEN_ORDER_COUNT_CRITICAL",
                trace_id,
                metadata,
            )
        if open_order_count >= self._config.open_order_spike_threshold:
            return self._escalate(
                "open_order_spike",
                MonitorSeverity.HIGH,
                RiskMode.CANCEL_ALL_AND_HALT,
                "OPEN_ORDER_COUNT_EXCEEDED",
                trace_id,
                metadata,
            )
        return self._ok("open_order_spike", trace_id, metadata)

    def check_ws_silence(
        self,
        stream_name: str,
        silence_seconds: int | None,
        venue_degraded: bool = False,
        trace_id: str = "",
    ) -> MonitorResult:
        metadata = {
            "stream_name": stream_name,
            "silence_seconds": silence_seconds,
            "venue_degraded": venue_degraded,
        }
        if silence_seconds is None or silence_seconds < 0:
            return self._escalate(
                "ws_silence",
                MonitorSeverity.HIGH,
                RiskMode.CLOSE_ONLY,
                "WS_SILENCE_MISSING",
                trace_id,
                metadata,
            )
        if silence_seconds >= self._config.ws_silence_cancel_all_seconds:
            return self._escalate(
                "ws_silence",
                MonitorSeverity.HIGH,
                RiskMode.CANCEL_ALL_AND_HALT,
                "WS_SILENCE_TIMEOUT_60S",
                trace_id,
                metadata,
            )
        if silence_seconds >= self._config.ws_silence_close_only_seconds or venue_degraded:
            target = RiskMode.CLOSE_ONLY if venue_degraded else RiskMode.NO_NEW_POSITIONS
            return self._escalate(
                "ws_silence",
                MonitorSeverity.MEDIUM,
                target,
                "WS_SILENCE_TIMEOUT_30S",
                trace_id,
                metadata,
            )
        return self._ok("ws_silence", trace_id, metadata)

    def check_margin_ratio(
        self,
        account: CryptoAccountRisk,
        position: CryptoPositionRisk,
        brackets: list[LeverageBracket],
        trace_id: str = "",
    ) -> MonitorResult:
        result = self._margin_calculator.evaluate_position(account, position, brackets)
        metadata = {
            "symbol": position.symbol,
            "margin_ratio": result.margin_ratio,
            "rejection_reason": result.rejection_reason,
        }
        if not result.ok:
            return self._escalate(
                "margin_ratio",
                MonitorSeverity.CRITICAL,
                RiskMode.CLOSE_ONLY,
                result.rejection_reason or "MARGIN_RATIO_UNAVAILABLE",
                trace_id,
                metadata,
            )
        if result.margin_ratio >= self._config.margin_ratio_liquidate:
            return self._escalate(
                "margin_ratio",
                MonitorSeverity.CRITICAL,
                RiskMode.LIQUIDATE_AND_DISCONNECT,
                "MARGIN_RATIO_CRITICAL",
                trace_id,
                metadata,
            )
        if result.margin_ratio >= self._config.margin_ratio_close_only:
            return self._escalate(
                "margin_ratio",
                MonitorSeverity.HIGH,
                RiskMode.CLOSE_ONLY,
                "MARGIN_RATIO_HIGH",
                trace_id,
                metadata,
            )
        return self._ok("margin_ratio", trace_id, metadata)

    def check_drawdown(
        self,
        drawdown_ratio: Decimal | None,
        venue_degraded: bool = False,
        trace_id: str = "",
    ) -> MonitorResult:
        metadata = {"drawdown_ratio": drawdown_ratio, "venue_degraded": venue_degraded}
        if drawdown_ratio is None or drawdown_ratio < 0:
            return self._escalate(
                "drawdown",
                MonitorSeverity.HIGH,
                RiskMode.CLOSE_ONLY,
                "DRAWDOWN_MISSING",
                trace_id,
                metadata,
            )
        if drawdown_ratio >= self._config.drawdown_liquidate:
            return self._escalate(
                "drawdown",
                MonitorSeverity.CRITICAL,
                RiskMode.LIQUIDATE_AND_DISCONNECT,
                "DRAWDOWN_CRITICAL",
                trace_id,
                metadata,
            )
        if drawdown_ratio >= self._config.drawdown_close_only:
            target = RiskMode.CANCEL_ALL_AND_HALT if venue_degraded else RiskMode.CLOSE_ONLY
            return self._escalate(
                "drawdown",
                MonitorSeverity.HIGH,
                target,
                "DRAWDOWN_HIGH",
                trace_id,
                metadata,
            )
        return self._ok("drawdown", trace_id, metadata)

    def check_liquidation_buffer(
        self,
        symbol: str,
        buffer_ratio: Decimal | None,
        trace_id: str = "",
    ) -> MonitorResult:
        metadata = {"symbol": symbol, "buffer_ratio": buffer_ratio}
        if buffer_ratio is None:
            return self._escalate(
                "liquidation_buffer",
                MonitorSeverity.HIGH,
                RiskMode.CLOSE_ONLY,
                "LIQUIDATION_BUFFER_MISSING",
                trace_id,
                metadata,
            )
        if buffer_ratio <= self._config.liquidation_buffer_liquidate:
            return self._escalate(
                "liquidation_buffer",
                MonitorSeverity.CRITICAL,
                RiskMode.LIQUIDATE_AND_DISCONNECT,
                "LIQUIDATION_BUFFER_CRITICAL",
                trace_id,
                metadata,
            )
        if buffer_ratio <= self._config.liquidation_buffer_close_only:
            return self._escalate(
                "liquidation_buffer",
                MonitorSeverity.HIGH,
                RiskMode.CLOSE_ONLY,
                "LIQUIDATION_BUFFER_LOW",
                trace_id,
                metadata,
            )
        return self._ok("liquidation_buffer", trace_id, metadata)

    def _escalate(
        self,
        event_type: str,
        severity: MonitorSeverity,
        target: RiskMode,
        reason: str,
        trace_id: str,
        metadata: dict[str, Any],
    ) -> MonitorResult:
        self._controller.escalate_to(
            target=target,
            reason=reason,
            trigger=event_type,
            trace_id=trace_id,
            metadata={**metadata, "severity": severity.value},
        )
        return MonitorResult(
            event_type=event_type,
            severity=severity,
            triggered=True,
            current_mode=self._controller.mode,
            escalation_target=target,
            trace_id=trace_id,
            reason=reason,
            metadata=metadata,
        )

    def _ok(
        self,
        event_type: str,
        trace_id: str,
        metadata: dict[str, Any],
    ) -> MonitorResult:
        return MonitorResult(
            event_type=event_type,
            severity=MonitorSeverity.LOW,
            triggered=False,
            current_mode=self._controller.mode,
            trace_id=trace_id,
            metadata=metadata,
        )
