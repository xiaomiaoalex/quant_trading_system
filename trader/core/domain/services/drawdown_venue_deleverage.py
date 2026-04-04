"""
Drawdown / Venue Health 联动去杠杆服务
=======================================

Core Plane 的"先缩再停"个人生存风险控制模块。

功能：
- 根据当前回撤确定仓位收缩系数
- 根据交易所健康状态调整系数
- KillSwitch L2+ 强制 HARD_HALT
- Fail-Closed 处理

本模块完全无 IO，属于 Core Plane 的确定性计算逻辑。
"""
from enum import Enum
from dataclasses import dataclass
import math

from trader.adapters.binance.degraded_cascade import CascadeState
from trader.core.application.risk_engine import KillSwitchLevel


class DeLeverageAction(Enum):
    NORMAL = "normal"
    HALF_POSITION = "half_position"
    CLOSE_ONLY = "close_only"
    REDUCE_TO_QUARTER = "reduce_to_quarter"
    HARD_HALT = "hard_halt"


@dataclass(frozen=True, slots=True)
class DrawdownThresholds:
    mild_threshold: float = 0.05
    medium_threshold: float = 0.10
    severe_threshold: float = 0.20
    critical_threshold: float = 0.30


@dataclass(frozen=True, slots=True)
class VenueHealthThresholds:
    degraded_coef: float = 0.5
    self_protection_coef: float = 0.0
    recovering_coef: float = 0.8
    alignment_unhealthy_coef: float = 0.5  # Penalty when alignment is unhealthy


@dataclass(frozen=True, slots=True)
class DeLeverageConfig:
    drawdown_thresholds: DrawdownThresholds
    venue_health_thresholds: VenueHealthThresholds
    enable_venue_linkage: bool = True
    fail_closed: bool = True


@dataclass(frozen=True, slots=True)
class DeLeverageResult:
    action: DeLeverageAction
    drawdown_coef: float
    venue_health_coef: float
    combined_coef: float
    is_blocked: bool
    reason: str


class DrawdownVenueDeleverage:
    def __init__(self, config: DeLeverageConfig | None = None):
        if config is None:
            config = DeLeverageConfig(
                drawdown_thresholds=DrawdownThresholds(),
                venue_health_thresholds=VenueHealthThresholds(),
                enable_venue_linkage=True,
                fail_closed=True,
            )
        self._config = config

    def evaluate(
        self,
        current_drawdown: float,
        cascade_state: CascadeState,
        alignment_health: bool,
        killswitch_level: KillSwitchLevel,
    ) -> DeLeverageResult:
        if not self._is_valid_drawdown(current_drawdown):
            if self._config.fail_closed:
                return DeLeverageResult(
                    action=DeLeverageAction.HARD_HALT,
                    drawdown_coef=0.0,
                    venue_health_coef=0.0,
                    combined_coef=0.0,
                    is_blocked=True,
                    reason="Fail-closed: invalid drawdown (NaN or negative)",
                )
            else:
                # Non-fail-closed mode: still reject NaN/invalid drawdown as conservative measure
                # because returning NORMAL with invalid input is dangerous
                return DeLeverageResult(
                    action=DeLeverageAction.HARD_HALT,
                    drawdown_coef=0.0,
                    venue_health_coef=0.0,
                    combined_coef=0.0,
                    is_blocked=True,
                    reason="Invalid drawdown (NaN or negative), conservative reject",
                )

        if cascade_state is None:
            if self._config.fail_closed:
                return DeLeverageResult(
                    action=DeLeverageAction.HARD_HALT,
                    drawdown_coef=0.0,
                    venue_health_coef=0.0,
                    combined_coef=0.0,
                    is_blocked=True,
                    reason="Fail-closed: None cascade_state",
                )
            else:
                return DeLeverageResult(
                    action=DeLeverageAction.HARD_HALT,
                    drawdown_coef=0.0,
                    venue_health_coef=0.0,
                    combined_coef=0.0,
                    is_blocked=True,
                    reason="None cascade_state, conservative reject",
                )

        if killswitch_level >= KillSwitchLevel.L2_CANCEL_ALL_AND_HALT:
            return DeLeverageResult(
                action=DeLeverageAction.HARD_HALT,
                drawdown_coef=0.0,
                venue_health_coef=0.0,
                combined_coef=0.0,
                is_blocked=True,
                reason=f"KillSwitch L{killswitch_level.value} override: HARD_HALT",
            )

        drawdown_coef = self._compute_drawdown_coef(current_drawdown)

        if self._config.enable_venue_linkage:
            venue_health_coef = self._compute_venue_health_coef(cascade_state)
            if not alignment_health:
                venue_health_coef *= self._config.venue_health_thresholds.alignment_unhealthy_coef
        else:
            venue_health_coef = 1.0

        combined_coef = drawdown_coef * venue_health_coef

        action = self._determine_action(
            current_drawdown=current_drawdown,
            cascade_state=cascade_state,
            drawdown_coef=drawdown_coef,
            venue_health_coef=venue_health_coef,
        )

        is_blocked = self._determine_is_blocked(action, venue_health_coef)

        reason = self._build_reason(
            current_drawdown=current_drawdown,
            cascade_state=cascade_state,
            alignment_health=alignment_health,
            drawdown_coef=drawdown_coef,
            venue_health_coef=venue_health_coef,
        )

        return DeLeverageResult(
            action=action,
            drawdown_coef=drawdown_coef,
            venue_health_coef=venue_health_coef,
            combined_coef=combined_coef,
            is_blocked=is_blocked,
            reason=reason,
        )

    def _is_valid_drawdown(self, drawdown: float) -> bool:
        if drawdown is None:
            return False
        if isinstance(drawdown, float) and math.isnan(drawdown):
            return False
        if drawdown < 0:
            return False
        return True

    def _compute_drawdown_coef(self, drawdown: float) -> float:
        thresholds = self._config.drawdown_thresholds
        if drawdown < thresholds.mild_threshold:
            return 1.0
        elif drawdown < thresholds.medium_threshold:
            return 0.5
        elif drawdown < thresholds.severe_threshold:
            return 0.25
        elif drawdown < thresholds.critical_threshold:
            return 0.1
        else:
            return 0.0

    def _compute_venue_health_coef(self, cascade_state: CascadeState) -> float:
        venue_thresholds = self._config.venue_health_thresholds
        if cascade_state == CascadeState.NORMAL:
            return 1.0
        elif cascade_state == CascadeState.DEGRADED:
            return venue_thresholds.degraded_coef
        elif cascade_state == CascadeState.SELF_PROTECTION:
            return venue_thresholds.self_protection_coef
        elif cascade_state == CascadeState.RECOVERING:
            return venue_thresholds.recovering_coef
        return 0.0

    def _determine_action(
        self,
        current_drawdown: float,
        cascade_state: CascadeState,
        drawdown_coef: float,
        venue_health_coef: float,
    ) -> DeLeverageAction:
        if cascade_state == CascadeState.SELF_PROTECTION:
            return DeLeverageAction.HARD_HALT

        thresholds = self._config.drawdown_thresholds
        if current_drawdown >= thresholds.critical_threshold:
            return DeLeverageAction.HARD_HALT

        if drawdown_coef <= 0.1 and venue_health_coef < 0.5:
            return DeLeverageAction.REDUCE_TO_QUARTER

        if drawdown_coef <= 0.25:
            return DeLeverageAction.CLOSE_ONLY

        if drawdown_coef <= 0.5:
            return DeLeverageAction.HALF_POSITION

        return DeLeverageAction.NORMAL

    def _determine_is_blocked(
        self,
        action: DeLeverageAction,
        venue_health_coef: float,
    ) -> bool:
        if action in (DeLeverageAction.CLOSE_ONLY, DeLeverageAction.REDUCE_TO_QUARTER, DeLeverageAction.HARD_HALT):
            return True
        if venue_health_coef == 0.0:
            return True
        return False

    def _build_reason(
        self,
        current_drawdown: float,
        cascade_state: CascadeState,
        alignment_health: bool,
        drawdown_coef: float,
        venue_health_coef: float,
    ) -> str:
        parts = []
        parts.append(f"drawdown={current_drawdown:.2%}")
        parts.append(f"cascade={cascade_state.value}")
        parts.append(f"alignment={'healthy' if alignment_health else 'unhealthy'}")
        parts.append(f"drawdown_coef={drawdown_coef:.2f}")
        parts.append(f"venue_health_coef={venue_health_coef:.2f}")
        return ", ".join(parts)
