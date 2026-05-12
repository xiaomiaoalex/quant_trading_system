from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Optional


class RiskMode(IntEnum):
    """Risk Mode 状态枚举

    状态只能单调升级（数字越大越严格），除非人工解除。
    fail-closed 默认进入至少 NO_NEW_POSITIONS。
    """

    NORMAL = 0
    NO_NEW_POSITIONS = 1
    CLOSE_ONLY = 2
    CANCEL_ALL_AND_HALT = 3
    LIQUIDATE_AND_DISCONNECT = 4

    @property
    def allows_new_positions(self) -> bool:
        """是否允许新开仓"""
        return self == RiskMode.NORMAL

    @property
    def allows_open_positions(self) -> bool:
        """是否允许开仓（不包括减仓）"""
        return self == RiskMode.NORMAL

    @property
    def allows_reduce_only(self) -> bool:
        """是否允许只减仓订单

        - NORMAL: 允许
        - NO_NEW_POSITIONS: 允许
        - CLOSE_ONLY: 允许
        - CANCEL_ALL_AND_HALT: 不允许（blocks_all_orders=True）
        - LIQUIDATE_AND_DISCONNECT: 不允许（blocks_all_orders=True）
        """
        return self.value < RiskMode.CANCEL_ALL_AND_HALT.value

    @property
    def allows_close_only(self) -> bool:
        """是否只允许平仓（不允许开新仓）"""
        return self.value >= RiskMode.CLOSE_ONLY.value

    @property
    def blocks_all_orders(self) -> bool:
        """是否阻止所有订单（包括平仓）"""
        return self.value >= RiskMode.CANCEL_ALL_AND_HALT.value

    @property
    def requires_liquidation(self) -> bool:
        """是否需要强平"""
        return self == RiskMode.LIQUIDATE_AND_DISCONNECT

    def can_escalate_to(self, target: "RiskMode") -> bool:
        """是否可以升级到目标状态"""
        return target.value > self.value

    def can_deescalate_to(self, target: "RiskMode") -> bool:
        """是否可以降级到目标状态（仅限人工解除）"""
        return target.value < self.value


@dataclass(frozen=True, slots=True)
class RiskModeTransition:
    """Risk Mode 状态迁移记录"""

    from_mode: RiskMode
    to_mode: RiskMode
    reason: str
    trigger: str
    triggered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    triggered_by: str = "system"
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RiskModeState:
    """Risk Mode 当前状态快照"""

    mode: RiskMode
    since: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    escalation_count: int = 0
    last_escalation_reason: Optional[str] = None
    manual_override: bool = False
    manual_override_by: Optional[str] = None


@dataclass(frozen=True, slots=True)
class RiskModeAuditEvent:
    """Risk Mode 变更审计事件"""

    event_type: str = "risk_mode_changed"
    schema_version: int = 1
    trace_id: str = ""
    ts_ms: int = field(default_factory=lambda: int(datetime.now(timezone.utc).timestamp() * 1000))
    mode_before: int = 0
    mode_after: int = 0
    reason: str = ""
    trigger: str = ""
    triggered_by: str = "system"
    metadata: dict = field(default_factory=dict)


def create_risk_mode_event(
    from_mode: RiskMode,
    to_mode: RiskMode,
    reason: str,
    trigger: str,
    triggered_by: str = "system",
    trace_id: str = "",
    metadata: Optional[dict] = None,
) -> RiskModeAuditEvent:
    """创建 Risk Mode 变更审计事件"""
    return RiskModeAuditEvent(
        event_type="risk_mode_changed",
        schema_version=1,
        trace_id=trace_id,
        ts_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
        mode_before=from_mode.value,
        mode_after=to_mode.value,
        reason=reason,
        trigger=trigger,
        triggered_by=triggered_by,
        metadata=metadata or {},
    )
