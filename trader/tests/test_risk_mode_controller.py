from datetime import datetime, timezone

import pytest

from trader.core.domain.models.risk_mode import (
    RiskMode,
    RiskModeAuditEvent,
    RiskModeState,
    create_risk_mode_event,
)
from trader.core.domain.services.risk_mode_controller import (
    RiskModeController,
    RiskModeControllerConfig,
)


class TestRiskModeEnum:
    def test_mode_allows_new_positions(self) -> None:
        assert RiskMode.NORMAL.allows_new_positions is True
        assert RiskMode.NO_NEW_POSITIONS.allows_new_positions is False
        assert RiskMode.CLOSE_ONLY.allows_new_positions is False
        assert RiskMode.CANCEL_ALL_AND_HALT.allows_new_positions is False
        assert RiskMode.LIQUIDATE_AND_DISCONNECT.allows_new_positions is False

    def test_mode_allows_open_positions(self) -> None:
        assert RiskMode.NORMAL.allows_open_positions is True
        assert RiskMode.NO_NEW_POSITIONS.allows_open_positions is False
        assert RiskMode.CLOSE_ONLY.allows_open_positions is False
        assert RiskMode.CANCEL_ALL_AND_HALT.allows_open_positions is False
        assert RiskMode.LIQUIDATE_AND_DISCONNECT.allows_open_positions is False

    def test_mode_allows_reduce_only(self) -> None:
        assert RiskMode.NORMAL.allows_reduce_only is True
        assert RiskMode.NO_NEW_POSITIONS.allows_reduce_only is True
        assert RiskMode.CLOSE_ONLY.allows_reduce_only is True
        assert RiskMode.CANCEL_ALL_AND_HALT.allows_reduce_only is False
        assert RiskMode.LIQUIDATE_AND_DISCONNECT.allows_reduce_only is False

    def test_mode_blocks_all_orders(self) -> None:
        assert RiskMode.NORMAL.blocks_all_orders is False
        assert RiskMode.NO_NEW_POSITIONS.blocks_all_orders is False
        assert RiskMode.CLOSE_ONLY.blocks_all_orders is False
        assert RiskMode.CANCEL_ALL_AND_HALT.blocks_all_orders is True
        assert RiskMode.LIQUIDATE_AND_DISCONNECT.blocks_all_orders is True

    def test_mode_can_escalate_to(self) -> None:
        assert RiskMode.NORMAL.can_escalate_to(RiskMode.NO_NEW_POSITIONS) is True
        assert RiskMode.NORMAL.can_escalate_to(RiskMode.CLOSE_ONLY) is True
        assert RiskMode.NORMAL.can_escalate_to(RiskMode.CANCEL_ALL_AND_HALT) is True
        assert RiskMode.NORMAL.can_escalate_to(RiskMode.LIQUIDATE_AND_DISCONNECT) is True

        assert RiskMode.NO_NEW_POSITIONS.can_escalate_to(RiskMode.NORMAL) is False
        assert RiskMode.NO_NEW_POSITIONS.can_escalate_to(RiskMode.CLOSE_ONLY) is True
        assert RiskMode.NO_NEW_POSITIONS.can_escalate_to(RiskMode.LIQUIDATE_AND_DISCONNECT) is True

        assert RiskMode.CLOSE_ONLY.can_escalate_to(RiskMode.NO_NEW_POSITIONS) is False
        assert RiskMode.CLOSE_ONLY.can_escalate_to(RiskMode.LIQUIDATE_AND_DISCONNECT) is True

        assert RiskMode.LIQUIDATE_AND_DISCONNECT.can_escalate_to(RiskMode.NORMAL) is False
        assert RiskMode.LIQUIDATE_AND_DISCONNECT.can_escalate_to(RiskMode.CLOSE_ONLY) is False


class TestRiskModeControllerBasics:
    def test_initial_state_is_normal(self) -> None:
        controller = RiskModeController()
        assert controller.mode == RiskMode.NORMAL
        assert controller.state.mode == RiskMode.NORMAL

    def test_can_allow_new_position_in_normal_mode(self) -> None:
        controller = RiskModeController()
        assert controller.can_open_new_position() is True
        assert controller.can_allow_position(is_reduce_only=False) is True

    def test_blocks_new_position_in_no_new_positions_mode(self) -> None:
        controller = RiskModeController()
        controller.force_mode(RiskMode.NO_NEW_POSITIONS, "test", "test")
        assert controller.can_open_new_position() is False
        assert controller.can_allow_position(is_reduce_only=False) is False
        assert controller.can_allow_position(is_reduce_only=True) is True

    def test_allows_close_in_close_only_mode(self) -> None:
        controller = RiskModeController()
        controller.force_mode(RiskMode.CLOSE_ONLY, "test", "test")
        assert controller.can_close_position() is True
        assert controller.can_allow_position(is_reduce_only=True) is True

    def test_blocks_all_orders_in_cancel_halt_mode(self) -> None:
        controller = RiskModeController()
        controller.force_mode(RiskMode.CANCEL_ALL_AND_HALT, "test", "test")
        assert controller.can_close_position() is False
        assert controller.can_open_new_position() is False


class TestRiskModeMonotonicEscalation:
    def test_escalation_is_monotonic(self) -> None:
        controller = RiskModeController()
        controller.check_and_escalate("trigger1", "reason1")
        assert controller.mode == RiskMode.CLOSE_ONLY

        controller.check_and_escalate("trigger2", "reason2")
        assert controller.mode == RiskMode.CANCEL_ALL_AND_HALT

        controller.check_and_escalate("trigger3", "reason3")
        assert controller.mode == RiskMode.LIQUIDATE_AND_DISCONNECT

        controller.check_and_escalate("trigger4", "reason4")
        assert controller.mode == RiskMode.LIQUIDATE_AND_DISCONNECT

    def test_cannot_escalate_above_liquidate(self) -> None:
        controller = RiskModeController()
        controller.force_mode(RiskMode.LIQUIDATE_AND_DISCONNECT, "test", "test")
        result = controller.check_and_escalate("trigger", "reason")
        assert result is False
        assert controller.mode == RiskMode.LIQUIDATE_AND_DISCONNECT


class TestRiskModeManualOverride:
    def test_manual_escalate(self) -> None:
        controller = RiskModeController()
        result = controller.manual_escalate(
            target=RiskMode.CANCEL_ALL_AND_HALT,
            reason="manual escalation",
            triggered_by="admin",
        )
        assert result is True
        assert controller.mode == RiskMode.CANCEL_ALL_AND_HALT
        assert controller.state.manual_override is True
        assert controller.state.manual_override_by == "admin"

    def test_manual_release(self) -> None:
        controller = RiskModeController()
        controller.force_mode(RiskMode.LIQUIDATE_AND_DISCONNECT, "test", "test")
        assert controller.mode == RiskMode.LIQUIDATE_AND_DISCONNECT

        result = controller.manual_release(
            target=RiskMode.NORMAL,
            reason="manual release",
            triggered_by="admin",
        )
        assert result is True
        assert controller.mode == RiskMode.NORMAL
        assert controller.state.manual_override is True
        assert controller.state.manual_override_by == "admin"

    def test_manual_release_from_close_only(self) -> None:
        controller = RiskModeController()
        controller.force_mode(RiskMode.CLOSE_ONLY, "test", "test")

        result = controller.manual_release(
            target=RiskMode.NORMAL,
            reason="manual release",
            triggered_by="admin",
        )
        assert result is True
        assert controller.mode == RiskMode.NORMAL

    def test_cannot_escalate_to_lower_mode(self) -> None:
        controller = RiskModeController()
        controller.force_mode(RiskMode.CANCEL_ALL_AND_HALT, "test", "test")

        result = controller.manual_escalate(
            target=RiskMode.CLOSE_ONLY,
            reason="should not work",
            triggered_by="admin",
        )
        assert result is False
        assert controller.mode == RiskMode.CANCEL_ALL_AND_HALT


class TestRiskModeAudit:
    def test_escalation_writes_audit_event(self) -> None:
        events: list[RiskModeAuditEvent] = []

        def audit_callback(event: RiskModeAuditEvent) -> None:
            events.append(event)

        controller = RiskModeController()
        controller.set_audit_callback(audit_callback)

        controller.check_and_escalate("trigger1", "reason1", trace_id="trace-123")

        assert len(events) == 1
        event = events[0]
        assert event.event_type == "risk_mode_changed"
        assert event.mode_before == RiskMode.NORMAL.value
        assert event.mode_after == RiskMode.CLOSE_ONLY.value
        assert event.trigger == "trigger1"
        assert event.reason == "reason1"
        assert event.triggered_by == "system"
        assert event.trace_id == "trace-123"

    def test_manual_escalation_writes_audit_event(self) -> None:
        events: list[RiskModeAuditEvent] = []

        def audit_callback(event: RiskModeAuditEvent) -> None:
            events.append(event)

        controller = RiskModeController()
        controller.set_audit_callback(audit_callback)

        controller.manual_escalate(
            target=RiskMode.CANCEL_ALL_AND_HALT,
            reason="emergency",
            triggered_by="operator",
            trace_id="trace-456",
        )

        assert len(events) == 1
        event = events[0]
        assert event.triggered_by == "operator"
        assert event.trace_id == "trace-456"

    def test_manual_release_writes_audit_event(self) -> None:
        events: list[RiskModeAuditEvent] = []

        def audit_callback(event: RiskModeAuditEvent) -> None:
            events.append(event)

        controller = RiskModeController()
        controller.force_mode(RiskMode.LIQUIDATE_AND_DISCONNECT, "test", "test")
        controller.set_audit_callback(audit_callback)

        controller.manual_release(
            target=RiskMode.NORMAL,
            reason="all clear",
            triggered_by="admin",
        )

        assert len(events) == 1
        event = events[0]
        assert event.trigger == "manual_release"
        assert event.mode_after == RiskMode.NORMAL.value


class TestRiskModeState:
    def test_risk_mode_state_tracks_escalation_count(self) -> None:
        controller = RiskModeController()

        controller.check_and_escalate("t1", "r1")
        assert controller.state.escalation_count == 1

        controller.check_and_escalate("t2", "r2")
        assert controller.state.escalation_count == 2

    def test_risk_mode_state_tracks_last_reason(self) -> None:
        controller = RiskModeController()

        controller.check_and_escalate("trigger", "critical failure")
        assert controller.state.last_escalation_reason == "critical failure"


class TestCreateRiskModeEvent:
    def test_create_event_with_defaults(self) -> None:
        event = create_risk_mode_event(
            from_mode=RiskMode.NORMAL,
            to_mode=RiskMode.CLOSE_ONLY,
            reason="test reason",
            trigger="test trigger",
        )

        assert event.event_type == "risk_mode_changed"
        assert event.schema_version == 1
        assert event.mode_before == RiskMode.NORMAL.value
        assert event.mode_after == RiskMode.CLOSE_ONLY.value
        assert event.reason == "test reason"
        assert event.trigger == "test trigger"
        assert event.triggered_by == "system"
        assert event.ts_ms > 0

    def test_create_event_with_metadata(self) -> None:
        event = create_risk_mode_event(
            from_mode=RiskMode.CLOSE_ONLY,
            to_mode=RiskMode.LIQUIDATE_AND_DISCONNECT,
            reason="extreme risk",
            trigger="continuous rejection",
            triggered_by="operator",
            trace_id="trace-789",
            metadata={"rejection_count": 10},
        )

        assert event.triggered_by == "operator"
        assert event.trace_id == "trace-789"
        assert event.metadata == {"rejection_count": 10}
