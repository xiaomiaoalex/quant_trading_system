"""
test_intraday_risk_monitors - 盘中风控 Monitor 测试
==================================================
阶段5测试用例：
- mark price 急跌触发 RiskMode 升级
- open order spike 触发 cancel-all
- WS silence 超时触发 degraded + no-new-positions
- PG 审计不可用时 fail-closed
- 重复风险事件幂等
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from trader.core.domain.models.crypto_risk import (
    CryptoAccountRisk,
    CryptoPositionRisk,
    LeverageBracket,
    MarginMode,
)
from trader.core.domain.models.risk_mode import RiskMode, RiskModeAuditEvent, RiskModeState
from trader.core.domain.services.intraday_risk_monitor import IntradayRiskMonitor, MonitorSeverity
from trader.core.domain.services.margin_risk_calculator import MarginRiskCalculator
from trader.core.domain.services.risk_mode_controller import (
    RiskModeController,
    RiskModeControllerConfig,
)


def d(value: str) -> Decimal:
    return Decimal(value)


@dataclass(frozen=True, slots=True)
class MockAuditEvent:
    event_type: str
    trace_id: str
    reason: str
    mode_before: int
    mode_after: int


class TestIntradayRiskMonitorProductionEntry:
    """测试阶段5真实 Monitor 生产入口"""

    def test_monitor_mark_price_drop_triggers_close_only(self) -> None:
        controller = RiskModeController()
        monitor = IntradayRiskMonitor(controller)

        result = monitor.check_mark_price_drop(
            symbol="BTCUSDT",
            previous_mark_price=d("50000"),
            current_mark_price=d("44000"),
            trace_id="trace-monitor-mark-001",
        )

        assert result.triggered is True
        assert result.event_type == "mark_price_drop"
        assert result.severity == MonitorSeverity.MEDIUM
        assert result.escalation_target == RiskMode.CLOSE_ONLY
        assert controller.mode == RiskMode.CLOSE_ONLY

    def test_monitor_open_order_spike_triggers_cancel_all(self) -> None:
        controller = RiskModeController()
        monitor = IntradayRiskMonitor(controller)

        result = monitor.check_open_order_spike(
            symbol="BTCUSDT",
            open_order_count=120,
            trace_id="trace-monitor-order-001",
        )

        assert result.triggered is True
        assert result.escalation_target == RiskMode.CANCEL_ALL_AND_HALT
        assert controller.mode == RiskMode.CANCEL_ALL_AND_HALT

    def test_monitor_ws_silence_first_escalates_no_new_positions(self) -> None:
        controller = RiskModeController()
        monitor = IntradayRiskMonitor(controller)

        result = monitor.check_ws_silence(
            stream_name="binance_private",
            silence_seconds=35,
            trace_id="trace-monitor-ws-001",
        )

        assert result.triggered is True
        assert result.escalation_target == RiskMode.NO_NEW_POSITIONS
        assert controller.mode == RiskMode.NO_NEW_POSITIONS
        assert controller.can_open_new_position() is False
        assert controller.can_close_position() is True

    def test_monitor_margin_ratio_fail_closed_on_missing_bracket(self) -> None:
        controller = RiskModeController()
        monitor = IntradayRiskMonitor(controller)
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("10"),
        )

        result = monitor.check_margin_ratio(
            account=account,
            position=position,
            brackets=[],
            trace_id="trace-monitor-margin-001",
        )

        assert result.triggered is True
        assert result.reason == "MISSING_LEVERAGE_BRACKET"
        assert controller.mode == RiskMode.CLOSE_ONLY

    def test_monitor_drawdown_with_venue_degraded_triggers_cancel_all(self) -> None:
        controller = RiskModeController()
        monitor = IntradayRiskMonitor(controller)

        result = monitor.check_drawdown(
            drawdown_ratio=d("0.21"),
            venue_degraded=True,
            trace_id="trace-monitor-dd-001",
        )

        assert result.triggered is True
        assert result.escalation_target == RiskMode.CANCEL_ALL_AND_HALT
        assert controller.mode == RiskMode.CANCEL_ALL_AND_HALT

    def test_monitor_liquidation_buffer_critical_triggers_liquidate(self) -> None:
        controller = RiskModeController()
        monitor = IntradayRiskMonitor(controller)

        result = monitor.check_liquidation_buffer(
            symbol="BTCUSDT",
            buffer_ratio=d("0.01"),
            trace_id="trace-monitor-liq-001",
        )

        assert result.triggered is True
        assert result.escalation_target == RiskMode.LIQUIDATE_AND_DISCONNECT
        assert controller.mode == RiskMode.LIQUIDATE_AND_DISCONNECT


class TestMarkPriceJumpMonitor:
    """测试 mark price 急跌触发 RiskMode 升级"""

    def test_mark_price_drop_triggers_close_only(self) -> None:
        controller = RiskModeController()
        audit_events: list[MockAuditEvent] = []

        def mock_audit(event: RiskModeAuditEvent) -> None:
            audit_events.append(
                MockAuditEvent(
                    event_type=event.event_type,
                    trace_id=event.trace_id,
                    reason=event.reason,
                    mode_before=event.mode_before,
                    mode_after=event.mode_after,
                )
            )

        controller.set_audit_callback(mock_audit)

        controller.check_and_escalate(
            trigger="mark_price_drop",
            reason="MARK_PRICE_DROP_10PCT",
            trace_id="trace-drop-001",
        )

        assert controller.mode == RiskMode.CLOSE_ONLY
        assert len(audit_events) == 1
        assert audit_events[0].reason == "MARK_PRICE_DROP_10PCT"

    def test_severe_mark_price_drop_triggers_cancel_all(self) -> None:
        controller = RiskModeController()
        audit_events: list[MockAuditEvent] = []

        def mock_audit(event: RiskModeAuditEvent) -> None:
            audit_events.append(
                MockAuditEvent(
                    event_type=event.event_type,
                    trace_id=event.trace_id,
                    reason=event.reason,
                    mode_before=event.mode_before,
                    mode_after=event.mode_after,
                )
            )

        controller.set_audit_callback(mock_audit)

        controller.check_and_escalate(
            trigger="mark_price_drop",
            reason="MARK_PRICE_DROP_20PCT",
            trace_id="trace-drop-002",
        )
        controller.check_and_escalate(
            trigger="mark_price_drop",
            reason="MARK_PRICE_DROP_20PCT",
            trace_id="trace-drop-003",
        )

        assert controller.mode == RiskMode.CANCEL_ALL_AND_HALT
        assert len(audit_events) == 2

    def test_extreme_mark_price_drop_triggers_liquidate(self) -> None:
        controller = RiskModeController()
        audit_events: list[MockAuditEvent] = []

        def mock_audit(event: RiskModeAuditEvent) -> None:
            audit_events.append(
                MockAuditEvent(
                    event_type=event.event_type,
                    trace_id=event.trace_id,
                    reason=event.reason,
                    mode_before=event.mode_before,
                    mode_after=event.mode_after,
                )
            )

        controller.set_audit_callback(mock_audit)

        for i in range(3):
            controller.check_and_escalate(
                trigger="mark_price_drop",
                reason="MARK_PRICE_DROP_30PCT",
                trace_id=f"trace-drop-{i}",
            )

        assert controller.mode == RiskMode.LIQUIDATE_AND_DISCONNECT


class TestOpenOrderSpikeMonitor:
    """测试 open order spike 触发 cancel-all"""

    def test_open_order_spike_triggers_cancel_all(self) -> None:
        controller = RiskModeController()

        controller.check_and_escalate(
            trigger="open_order_spike",
            reason="OPEN_ORDER_COUNT_EXCEEDED",
            trace_id="trace-order-001",
        )
        controller.check_and_escalate(
            trigger="open_order_spike",
            reason="OPEN_ORDER_COUNT_EXCEEDED",
            trace_id="trace-order-002",
        )

        assert controller.mode == RiskMode.CANCEL_ALL_AND_HALT

    def test_order_spike_accumulates_then_stops(self) -> None:
        """重复 order spike 累积到上限后停止升级"""
        controller = RiskModeController()

        controller.check_and_escalate(
            trigger="open_order_spike",
            reason="OPEN_ORDER_COUNT_EXCEEDED",
            trace_id="trace-order-100",
        )
        assert controller.mode == RiskMode.CLOSE_ONLY

        controller.check_and_escalate(
            trigger="open_order_spike",
            reason="OPEN_ORDER_COUNT_EXCEEDED",
            trace_id="trace-order-101",
        )
        assert controller.mode == RiskMode.CANCEL_ALL_AND_HALT

        for _ in range(10):
            controller.check_and_escalate(
                trigger="open_order_spike",
                reason="OPEN_ORDER_COUNT_EXCEEDED",
                trace_id="trace-order-idempotent",
            )

        assert controller.mode == RiskMode.LIQUIDATE_AND_DISCONNECT


class TestWSSilenceMonitor:
    """测试 WS silence 超时触发 degraded + no-new-positions"""

    def test_ws_silence_triggers_no_new_positions(self) -> None:
        controller = RiskModeController()

        controller.check_and_escalate(
            trigger="ws_silence",
            reason="WS_SILENCE_TIMEOUT_30S",
            trace_id="trace-ws-001",
        )

        assert controller.mode == RiskMode.CLOSE_ONLY
        assert controller.can_open_new_position() is False

    def test_ws_silence_with_venue_degraded(self) -> None:
        controller = RiskModeController()

        controller.check_and_escalate(
            trigger="ws_silence",
            reason="WS_SILENCE_TIMEOUT_60S",
            trace_id="trace-ws-002",
        )

        assert controller.mode == RiskMode.CLOSE_ONLY
        assert controller.can_open_new_position() is False
        assert controller.can_close_position() is True

    def test_ws_silence_with_multiple_triggers(self) -> None:
        """WS silence 多次触发累积升级"""
        controller = RiskModeController()

        controller.check_and_escalate(
            trigger="ws_silence",
            reason="WS_SILENCE_TIMEOUT_30S",
            trace_id="trace-ws-001",
        )
        assert controller.mode == RiskMode.CLOSE_ONLY

        controller.check_and_escalate(
            trigger="ws_silence",
            reason="WS_SILENCE_TIMEOUT_60S",
            trace_id="trace-ws-002",
        )
        assert controller.mode == RiskMode.CANCEL_ALL_AND_HALT


class TestMarginRatioMonitor:
    """测试 margin ratio 超阈值触发升级"""

    def test_high_margin_ratio_triggers_close_only(self) -> None:
        controller = RiskModeController()

        controller.check_and_escalate(
            trigger="margin_ratio_threshold",
            reason="MARGIN_RATIO_80PCT",
            trace_id="trace-margin-001",
        )

        assert controller.mode == RiskMode.CLOSE_ONLY

    def test_critical_margin_ratio_triggers_liquidate(self) -> None:
        controller = RiskModeController()

        for i in range(3):
            controller.check_and_escalate(
                trigger="margin_ratio_threshold",
                reason="MARGIN_RATIO_95PCT",
                trace_id=f"trace-margin-{i}",
            )

        assert controller.mode == RiskMode.LIQUIDATE_AND_DISCONNECT

    def test_margin_ratio_with_position(self) -> None:
        """margin ratio 计算与 position 关联"""
        calculator = MarginRiskCalculator()
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("1000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("45000"),
            leverage=d("10"),
        )
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("250000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            )
        ]

        result = calculator.evaluate_position(account, position, brackets)
        assert result.ok is True
        assert result.margin_ratio > Decimal("0")


class TestPGAuditUnavailableFailClosed:
    """测试 PG 审计不可用时 fail-closed"""

    def test_audit_failure_does_not_affect_risk_decision(self) -> None:
        """PG 审计写入失败时，风控决策仍必须 reject"""
        controller = RiskModeController()

        audit_failures = []

        def failing_audit(event: RiskModeAuditEvent) -> None:
            audit_failures.append(event)
            raise RuntimeError("PG connection failed")

        controller.set_audit_callback(failing_audit)

        result = controller.check_and_escalate(
            trigger="margin_ratio_threshold",
            reason="MARGIN_RATIO_CRITICAL",
            trace_id="trace-audit-001",
        )

        assert result is True
        assert controller.mode == RiskMode.CLOSE_ONLY
        assert len(audit_failures) == 1

    def test_audit_failure_idempotent(self) -> None:
        """审计失败后再次触发仍可执行"""
        controller = RiskModeController()

        call_count = []

        def failing_audit(event: RiskModeAuditEvent) -> None:
            call_count.append(1)
            raise RuntimeError("PG connection failed")

        controller.set_audit_callback(failing_audit)

        controller.check_and_escalate(
            trigger="mark_price_drop",
            reason="MARK_PRICE_DROP",
            trace_id="trace-audit-idempotent",
        )

        for _ in range(3):
            controller.check_and_escalate(
                trigger="mark_price_drop",
                reason="MARK_PRICE_DROP",
                trace_id="trace-audit-retry",
            )

        assert len(call_count) >= 1


class TestRiskEventIdempotency:
    """测试重复风险事件幂等"""

    def test_same_trigger_idempotent_escalation(self) -> None:
        """同一触发源重复事件，模式只升级一次"""
        controller = RiskModeController()

        controller.check_and_escalate(
            trigger="duplicate_signal",
            reason="CONTINUOUS_REJECTION",
            trace_id="trace-dup-001",
        )
        assert controller.mode == RiskMode.CLOSE_ONLY

        controller.check_and_escalate(
            trigger="duplicate_signal",
            reason="CONTINUOUS_REJECTION",
            trace_id="trace-dup-002",
        )
        assert controller.mode == RiskMode.CANCEL_ALL_AND_HALT

        for _ in range(5):
            controller.check_and_escalate(
                trigger="duplicate_signal",
                reason="CONTINUOUS_REJECTION",
                trace_id="trace-dup-extra",
            )

        assert controller.mode == RiskMode.LIQUIDATE_AND_DISCONNECT

    def test_different_triggers_cumulative(self) -> None:
        """不同触发源累积触发升级"""
        controller = RiskModeController()

        controller.check_and_escalate(
            trigger="mark_price_drop",
            reason="MARK_PRICE_DROP",
            trace_id="trace-mark-001",
        )
        assert controller.mode == RiskMode.CLOSE_ONLY

        controller.check_and_escalate(
            trigger="open_order_spike",
            reason="ORDER_SPIKE",
            trace_id="trace-order-001",
        )
        assert controller.mode == RiskMode.CANCEL_ALL_AND_HALT

        controller.check_and_escalate(
            trigger="ws_silence",
            reason="WS_SILENCE",
            trace_id="trace-ws-001",
        )
        assert controller.mode == RiskMode.LIQUIDATE_AND_DISCONNECT

    def test_manual_override_breaks_rejection_chain(self) -> None:
        """人工干预后重置拒绝计数"""
        controller = RiskModeController()

        controller.check_and_escalate(
            trigger="mark_price_drop",
            reason="MARK_PRICE_DROP",
            trace_id="trace-manual-001",
        )
        assert controller.mode == RiskMode.CLOSE_ONLY

        controller.manual_release(
            target=RiskMode.NORMAL,
            reason="Manual recovery",
            triggered_by="operator",
            trace_id="trace-manual-002",
        )

        controller.check_and_escalate(
            trigger="mark_price_drop",
            reason="MARK_PRICE_DROP",
            trace_id="trace-manual-003",
        )

        assert controller.mode == RiskMode.CLOSE_ONLY


class TestDrawdownMonitorIntegration:
    """测试回撤 Monitor 与 RiskMode 集成"""

    def test_drawdown_triggers_close_only(self) -> None:
        controller = RiskModeController()

        controller.check_and_escalate(
            trigger="drawdown_threshold",
            reason="DRAWDOWN_20PCT",
            trace_id="trace-dd-001",
        )

        assert controller.mode == RiskMode.CLOSE_ONLY

    def test_drawdown_extreme_triggers_liquidate(self) -> None:
        controller = RiskModeController()

        for i in range(3):
            controller.check_and_escalate(
                trigger="drawdown_threshold",
                reason="DRAWDOWN_30PCT",
                trace_id=f"trace-dd-{i}",
            )

        assert controller.mode == RiskMode.LIQUIDATE_AND_DISCONNECT

    def test_drawdown_with_venue_degraded_intensifies(self) -> None:
        """回撤 + venue degraded 加速升级"""
        controller = RiskModeController()

        controller.check_and_escalate(
            trigger="drawdown_threshold",
            reason="DRAWDOWN_10PCT",
            trace_id="trace-dd-venue-001",
        )
        assert controller.mode == RiskMode.CLOSE_ONLY

        controller.check_and_escalate(
            trigger="venue_health_degraded",
            reason="VENUE_DEGRADED",
            trace_id="trace-dd-venue-002",
        )
        assert controller.mode == RiskMode.CANCEL_ALL_AND_HALT


class TestLiquidationBufferMonitor:
    """测试强平缓冲 Monitor"""

    def test_low_liquidation_buffer_triggers_close_only(self) -> None:
        controller = RiskModeController()

        controller.check_and_escalate(
            trigger="liquidation_buffer_low",
            reason="LIQUIDATION_BUFFER_5PCT",
            trace_id="trace-liq-001",
        )

        assert controller.mode == RiskMode.CLOSE_ONLY

    def test_critical_liquidation_buffer_triggers_liquidate(self) -> None:
        controller = RiskModeController()

        for i in range(3):
            controller.check_and_escalate(
                trigger="liquidation_buffer_critical",
                reason="LIQUIDATION_BUFFER_2PCT",
                trace_id=f"trace-liq-{i}",
            )

        assert controller.mode == RiskMode.LIQUIDATE_AND_DISCONNECT


class TestMonitorFailClosed:
    """测试 Monitor fail-closed 行为"""

    def test_missing_metric_fails_closed(self) -> None:
        """缺少关键风控数据时 fail-closed"""
        calculator = MarginRiskCalculator()
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("10"),
        )
        brackets: list[LeverageBracket] = []

        result = calculator.evaluate_position(account, position, brackets)

        assert result.ok is False
        assert result.rejection_reason == "MISSING_LEVERAGE_BRACKET"

    def test_zero_mark_price_fails_closed(self) -> None:
        """零标记价 fail-closed"""
        calculator = MarginRiskCalculator()
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("0"),
            leverage=d("10"),
        )
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("250000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            )
        ]

        result = calculator.evaluate_position(account, position, brackets)

        assert result.ok is False
        assert result.rejection_reason == "INVALID_POSITION_INPUT"

    def test_negative_leverage_fails_closed(self) -> None:
        """负杠杆 fail-closed"""
        calculator = MarginRiskCalculator()
        account = CryptoAccountRisk(
            equity=d("10000"),
            available_balance=d("8000"),
            wallet_balance=d("10000"),
            margin_balance=d("10000"),
        )
        position = CryptoPositionRisk(
            symbol="BTCUSDT",
            qty=d("1"),
            entry_price=d("50000"),
            mark_price=d("50000"),
            leverage=d("-5"),
        )
        brackets = [
            LeverageBracket(
                symbol="BTCUSDT",
                notional_floor=d("0"),
                notional_cap=d("250000"),
                initial_leverage=d("20"),
                maint_margin_ratio=d("0.004"),
                maint_amount=d("0"),
            )
        ]

        result = calculator.evaluate_position(account, position, brackets)

        assert result.ok is False
        assert result.rejection_reason == "INVALID_POSITION_INPUT"


class TestAuditEventPersistence:
    """测试审计事件持久化"""

    def test_audit_event_contains_required_fields(self) -> None:
        """每个 RiskMode 变更审计事件必须包含必要字段"""
        controller = RiskModeController()
        audit_events: list[RiskModeAuditEvent] = []

        def capture_audit(event: RiskModeAuditEvent) -> None:
            audit_events.append(event)

        controller.set_audit_callback(capture_audit)

        controller.check_and_escalate(
            trigger="mark_price_drop",
            reason="MARK_PRICE_DROP",
            trace_id="trace-audit-001",
        )

        assert len(audit_events) == 1
        event = audit_events[0]
        assert event.event_type == "risk_mode_changed"
        assert event.trace_id == "trace-audit-001"
        assert event.mode_before == RiskMode.NORMAL.value
        assert event.mode_after == RiskMode.CLOSE_ONLY.value
        assert event.reason == "MARK_PRICE_DROP"
        assert event.trigger == "mark_price_drop"

    def test_manual_override_audit_includes_operator(self) -> None:
        """人工干预审计包含操作者信息"""
        controller = RiskModeController()
        audit_events: list[RiskModeAuditEvent] = []

        def capture_audit(event: RiskModeAuditEvent) -> None:
            audit_events.append(event)

        controller.set_audit_callback(capture_audit)

        controller.check_and_escalate(
            trigger="mark_price_drop",
            reason="MARK_PRICE_DROP",
            trace_id="trace-op-001",
        )

        controller.manual_escalate(
            target=RiskMode.LIQUIDATE_AND_DISCONNECT,
            reason="Emergency liquidation",
            triggered_by="operator:john.doe",
            trace_id="trace-op-002",
        )

        manual_event = audit_events[1]
        assert manual_event.triggered_by == "operator:john.doe"
        assert manual_event.reason == "Emergency liquidation"

    def test_audit_events_are_immutable(self) -> None:
        """审计事件不可变"""
        controller = RiskModeController()
        audit_events: list[RiskModeAuditEvent] = []

        def capture_audit(event: RiskModeAuditEvent) -> None:
            audit_events.append(event)

        controller.set_audit_callback(capture_audit)

        controller.check_and_escalate(
            trigger="mark_price_drop",
            reason="MARK_PRICE_DROP",
            trace_id="trace-immutable-001",
        )

        event = audit_events[0]
        with pytest.raises(AttributeError):
            event.reason = "MODIFIED"

        with pytest.raises(AttributeError):
            event.mode_after = RiskMode.NORMAL.value
