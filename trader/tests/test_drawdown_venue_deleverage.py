"""
Test DrawdownVenueDeleverage - Drawdown / Venue Health 联动去杠杆测试
=====================================================================
"""
import math
import pytest

from trader.adapters.binance.degraded_cascade import CascadeState
from trader.core.application.risk_engine import KillSwitchLevel
from trader.core.domain.services.drawdown_venue_deleverage import (
    DrawdownVenueDeleverage,
    DeLeverageAction,
    DeLeverageConfig,
    DrawdownThresholds,
    VenueHealthThresholds,
    DeLeverageResult,
)


class TestDrawdownVenueDeleverage:
    """测试 DrawdownVenueDeleverage"""

    def test_normal_state_no_drawdown_normal_cascade_healthy_alignment(self):
        """Normal state (no drawdown, NORMAL cascade, healthy alignment) -> NORMAL action"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.0,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.NORMAL
        assert result.drawdown_coef == 1.0
        assert result.venue_health_coef == 1.0
        assert result.combined_coef == 1.0
        assert result.is_blocked is False

    def test_normal_state_small_drawdown(self):
        """Small drawdown < mild_threshold -> NORMAL"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.03,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.NORMAL
        assert result.drawdown_coef == 1.0

    def test_mild_drawdown(self):
        """Mild drawdown (5-10%) -> HALF_POSITION"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.07,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.HALF_POSITION
        assert result.drawdown_coef == 0.5
        assert result.venue_health_coef == 1.0
        assert result.combined_coef == 0.5
        assert result.is_blocked is False

    def test_medium_drawdown(self):
        """Medium drawdown (10-20%) -> CLOSE_ONLY"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.15,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.CLOSE_ONLY
        assert result.drawdown_coef == 0.25
        assert result.is_blocked is True

    def test_severe_drawdown(self):
        """Severe drawdown (20-30%) -> REDUCE_TO_QUARTER"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.25,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.REDUCE_TO_QUARTER
        assert result.drawdown_coef == 0.1
        assert result.is_blocked is True

    def test_critical_drawdown(self):
        """Critical drawdown (>=30%) -> HARD_HALT"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.35,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.HARD_HALT
        assert result.drawdown_coef == 0.0
        assert result.is_blocked is True

    def test_medium_drawdown_plus_degraded_cascade(self):
        """Medium drawdown + DEGRADED cascade -> CLOSE_ONLY"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.12,
            cascade_state=CascadeState.DEGRADED,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.CLOSE_ONLY
        assert result.venue_health_coef == 0.5

    def test_severe_drawdown_plus_alignment_unhealthy(self):
        """Severe drawdown + alignment unhealthy -> REDUCE_TO_QUARTER"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.22,
            cascade_state=CascadeState.NORMAL,
            alignment_health=False,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.REDUCE_TO_QUARTER
        assert result.venue_health_coef == 0.5  # 0.5x penalty for unhealthy alignment

    def test_self_protection_cascade(self):
        """SELF_PROTECTION cascade -> HARD_HALT regardless of drawdown"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.01,
            cascade_state=CascadeState.SELF_PROTECTION,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.HARD_HALT
        assert result.venue_health_coef == 0.0
        assert result.is_blocked is True

    def test_recovering_cascade(self):
        """RECOVERING cascade -> recovering_coef applied"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.05,
            cascade_state=CascadeState.RECOVERING,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.HALF_POSITION
        assert result.venue_health_coef == 0.8

    def test_degraded_cascade_with_alignment_unhealthy(self):
        """DEGRADED cascade + alignment unhealthy -> additional penalty"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.03,
            cascade_state=CascadeState.DEGRADED,
            alignment_health=False,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.venue_health_coef == 0.25  # 0.5 * 0.5

    def test_killswitch_l1(self):
        """KillSwitch L1 (NO_NEW_POSITIONS) does NOT force HARD_HALT"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.03,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L1_NO_NEW_POSITIONS,
        )
        assert result.action == DeLeverageAction.NORMAL

    def test_killswitch_l2_override(self):
        """KillSwitch L2 (CANCEL_ALL_AND_HALT) -> HARD_HALT"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.01,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L2_CANCEL_ALL_AND_HALT,
        )
        assert result.action == DeLeverageAction.HARD_HALT
        assert "KillSwitch L2" in result.reason

    def test_killswitch_l3_override(self):
        """KillSwitch L3 (LIQUIDATE_AND_DISCONNECT) -> HARD_HALT"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.01,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L3_LIQUIDATE_AND_DISCONNECT,
        )
        assert result.action == DeLeverageAction.HARD_HALT
        assert "KillSwitch L3" in result.reason

    def test_fail_closed_nan_drawdown(self):
        """Fail-closed: NaN drawdown -> HARD_HALT"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=float("nan"),
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.HARD_HALT
        assert "Fail-closed" in result.reason

    def test_fail_closed_negative_drawdown(self):
        """Fail-closed: negative drawdown -> HARD_HALT"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=-0.05,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.HARD_HALT
        assert "Fail-closed" in result.reason

    def test_fail_closed_none_state(self):
        """Fail-closed: None cascade_state -> HARD_HALT"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.0,
            cascade_state=None,  # type: ignore
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.HARD_HALT
        assert "Fail-closed" in result.reason

    def test_threshold_boundary_mild(self):
        """Threshold boundary: drawdown at mild_threshold (0.05)"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.05,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.drawdown_coef == 0.5  # >= mild -> 0.5

    def test_threshold_boundary_medium(self):
        """Threshold boundary: drawdown at medium_threshold (0.10)"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.10,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.drawdown_coef == 0.25

    def test_threshold_boundary_severe(self):
        """Threshold boundary: drawdown at severe_threshold (0.20)"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.20,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.drawdown_coef == 0.1

    def test_threshold_boundary_critical(self):
        """Threshold boundary: drawdown at critical_threshold (0.30)"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.30,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.drawdown_coef == 0.0
        assert result.action == DeLeverageAction.HARD_HALT

    def test_combined_coef_calculation(self):
        """Combined coef = drawdown_coef * venue_health_coef"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.07,  # drawdown_coef = 0.5
            cascade_state=CascadeState.DEGRADED,  # venue_health_coef = 0.5
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.drawdown_coef == 0.5
        assert result.venue_health_coef == 0.5
        assert result.combined_coef == 0.25

    def test_custom_thresholds(self):
        """Custom thresholds work correctly"""
        config = DeLeverageConfig(
            drawdown_thresholds=DrawdownThresholds(
                mild_threshold=0.03,
                medium_threshold=0.08,
                severe_threshold=0.15,
                critical_threshold=0.25,
            ),
            venue_health_thresholds=VenueHealthThresholds(),
        )
        service = DrawdownVenueDeleverage(config)
        result = service.evaluate(
            current_drawdown=0.04,  # Between custom mild (0.03) and medium (0.08)
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.drawdown_coef == 0.5

    def test_venue_linkage_disabled(self):
        """Venue linkage disabled -> venue_health_coef = 1.0"""
        config = DeLeverageConfig(
            drawdown_thresholds=DrawdownThresholds(),
            venue_health_thresholds=VenueHealthThresholds(),
            enable_venue_linkage=False,
        )
        service = DrawdownVenueDeleverage(config)
        result = service.evaluate(
            current_drawdown=0.07,
            cascade_state=CascadeState.SELF_PROTECTION,
            alignment_health=False,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.venue_health_coef == 1.0
        assert result.action == DeLeverageAction.HALF_POSITION  # Not HARD_HALT since venue linkage disabled

    def test_is_blocked_for_close_only(self):
        """CLOSE_ONLY action -> is_blocked = True"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.15,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.CLOSE_ONLY
        assert result.is_blocked is True

    def test_is_blocked_for_reduce_to_quarter(self):
        """REDUCE_TO_QUARTER action -> is_blocked = True"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.25,
            cascade_state=CascadeState.NORMAL,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.REDUCE_TO_QUARTER
        assert result.is_blocked is True

    def test_venue_health_coef_zero_blocks(self):
        """venue_health_coef == 0.0 -> is_blocked = True"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.03,
            cascade_state=CascadeState.SELF_PROTECTION,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.venue_health_coef == 0.0
        assert result.is_blocked is True

    def test_result_reason_contains_key_info(self):
        """Result reason contains key information"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.07,
            cascade_state=CascadeState.DEGRADED,
            alignment_health=False,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert "drawdown=7.00%" in result.reason
        assert "cascade=DEGRADED" in result.reason
        assert "alignment=unhealthy" in result.reason

    def test_half_position_combined_with_degraded(self):
        """HALF_POSITION with DEGRADED cascade"""
        service = DrawdownVenueDeleverage()
        result = service.evaluate(
            current_drawdown=0.08,
            cascade_state=CascadeState.DEGRADED,
            alignment_health=True,
            killswitch_level=KillSwitchLevel.L0_NORMAL,
        )
        assert result.action == DeLeverageAction.CLOSE_ONLY  # 0.5 * 0.5 = 0.25 -> close only
