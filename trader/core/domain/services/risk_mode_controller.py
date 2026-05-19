from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from trader.core.domain.models.risk_mode import (
    RiskMode,
    RiskModeAuditEvent,
    RiskModeState,
    create_risk_mode_event,
)

logger = logging.getLogger(__name__)


@dataclass
class RiskModeControllerConfig:
    """Risk Mode Controller 配置

    P6 暂不实现启动 fail-closed，初始模式默认为 NORMAL。
    fail-closed 启动策略由 Control/Runtime 集成时决定。
    """

    default_mode_on_startup: RiskMode = RiskMode.NORMAL


class RiskModeController:
    """Risk Mode 状态机控制器

    职责：
    - 管理 Risk Mode 状态
    - 执行单调升级（只能升不能降，除非人工解除）
    - 触发来源包括：
      - 连续 pre-trade rejection
      - Funding/OI 极端
      - mark price 缺失
      - venue health degraded
      - margin ratio 超阈值
      - liquidation buffer 过低
      - PG audit 写入持续失败

    注意：fail-closed 启动策略由 Control/Runtime 层决定，不在本层实现。
    """

    def __init__(
        self,
        config: Optional[RiskModeControllerConfig] = None,
    ) -> None:
        self._config = config or RiskModeControllerConfig()
        self._state = RiskModeState(
            mode=self._config.default_mode_on_startup,
            since=datetime.now(timezone.utc),
            escalation_count=0,
            manual_override=False,
        )
        self._rejection_count = 0
        self._audit_callback: Optional[Callable[[RiskModeAuditEvent], None]] = None

    @property
    def state(self) -> RiskModeState:
        """获取当前状态"""
        return self._state

    @property
    def mode(self) -> RiskMode:
        """获取当前模式"""
        return self._state.mode

    def set_audit_callback(self, callback: Callable[[RiskModeAuditEvent], None]) -> None:
        """设置审计回调"""
        self._audit_callback = callback

    def can_allow_position(self, is_reduce_only: bool = False) -> bool:
        """检查是否可以允许仓位操作"""
        if is_reduce_only and self._state.mode.allows_reduce_only:
            return True
        return self._state.mode.allows_new_positions

    def can_open_new_position(self) -> bool:
        """检查是否允许新开仓"""
        return self._state.mode.allows_new_positions

    def can_close_position(self) -> bool:
        """检查是否允许平仓"""
        return not self._state.mode.blocks_all_orders

    def check_and_escalate(
        self,
        trigger: str,
        reason: str,
        trace_id: str = "",
        metadata: Optional[dict] = None,
    ) -> bool:
        """检查并执行升级（如果需要）

        Returns:
            True if escalation happened, False otherwise
        """
        if self._state.mode == RiskMode.LIQUIDATE_AND_DISCONNECT:
            return False

        self._rejection_count += 1

        target_mode = self._determine_escalation_target()

        if target_mode is None or not self._state.mode.can_escalate_to(target_mode):
            return False

        return self._escalate_to(target_mode, reason, trigger, trace_id, metadata)

    def escalate_to(
        self,
        target: RiskMode,
        reason: str,
        trigger: str,
        trace_id: str = "",
        metadata: Optional[dict] = None,
    ) -> bool:
        """Escalate directly to a monitor-selected target mode."""
        return self._escalate_to(target, reason, trigger, trace_id, metadata)

    def _determine_escalation_target(self) -> RiskMode | None:
        """根据当前模式和拒绝次数确定目标模式

        规则：
        - 1个拒绝：升级1级（跳过NO_NEW_POSITIONS）
        - 2个拒绝：升级2级
        - 3个拒绝：升级3级
        """
        current_value = self._state.mode.value
        if self._rejection_count >= 3:
            return RiskMode.LIQUIDATE_AND_DISCONNECT
        elif self._rejection_count >= 2:
            return RiskMode.CANCEL_ALL_AND_HALT
        elif self._rejection_count >= 1:
            return RiskMode.CLOSE_ONLY
        return None

    def _escalate_to(
        self,
        target: RiskMode,
        reason: str,
        trigger: str,
        trace_id: str = "",
        metadata: Optional[dict] = None,
    ) -> bool:
        """执行升级到目标模式"""
        if not self._state.mode.can_escalate_to(target):
            return False

        from_mode = self._state.mode

        self._state = RiskModeState(
            mode=target,
            since=datetime.now(timezone.utc),
            escalation_count=self._state.escalation_count + 1,
            last_escalation_reason=reason,
            manual_override=False,
        )

        event = create_risk_mode_event(
            from_mode=from_mode,
            to_mode=target,
            reason=reason,
            trigger=trigger,
            triggered_by="system",
            trace_id=trace_id,
            metadata=metadata,
        )

        if self._audit_callback:
            try:
                self._audit_callback(event)
            except Exception as exc:
                logger.warning(
                    "RiskMode audit callback failed for trigger=%s trace_id=%s: %s",
                    trigger,
                    trace_id,
                    exc,
                )

        return True

    def reset_rejection_count(self) -> None:
        """重置拒绝计数"""
        self._rejection_count = 0

    def manual_escalate(
        self,
        target: RiskMode,
        reason: str,
        triggered_by: str = "operator",
        trace_id: str = "",
        metadata: Optional[dict] = None,
    ) -> bool:
        """人工升级模式

        只有在当前模式可以升级到目标模式时才能执行。
        人工升级不受自动升级计数限制。
        """
        if not self._state.mode.can_escalate_to(target):
            return False

        from_mode = self._state.mode

        self._state = RiskModeState(
            mode=target,
            since=datetime.now(timezone.utc),
            escalation_count=self._state.escalation_count + 1,
            last_escalation_reason=reason,
            manual_override=True,
            manual_override_by=triggered_by,
        )

        self._rejection_count = 0

        event = create_risk_mode_event(
            from_mode=from_mode,
            to_mode=target,
            reason=reason,
            trigger="manual_override",
            triggered_by=triggered_by,
            trace_id=trace_id,
            metadata=metadata,
        )

        if self._audit_callback:
            try:
                self._audit_callback(event)
            except Exception as exc:
                logger.warning(
                    "RiskMode audit callback failed for manual escalation trace_id=%s: %s",
                    trace_id,
                    exc,
                )

        return True

    def manual_release(
        self,
        target: RiskMode,
        reason: str,
        triggered_by: str = "operator",
        trace_id: str = "",
        metadata: Optional[dict] = None,
    ) -> bool:
        """人工解除/降级模式

        人工解除是唯一允许降级的情况。
        默认降级到 NORMAL。
        """
        if target == RiskMode.NORMAL and self._state.mode != RiskMode.NORMAL:
            from_mode = self._state.mode

            self._state = RiskModeState(
                mode=RiskMode.NORMAL,
                since=datetime.now(timezone.utc),
                escalation_count=0,
                last_escalation_reason=None,
                manual_override=True,
                manual_override_by=triggered_by,
            )

            self._rejection_count = 0

            event = create_risk_mode_event(
                from_mode=from_mode,
                to_mode=RiskMode.NORMAL,
                reason=reason,
                trigger="manual_release",
                triggered_by=triggered_by,
                trace_id=trace_id,
                metadata=metadata,
            )

            if self._audit_callback:
                try:
                    self._audit_callback(event)
                except Exception as exc:
                    logger.warning(
                        "RiskMode audit callback failed for manual release trace_id=%s: %s",
                        trace_id,
                        exc,
                    )

            return True

        return False

    def force_mode(
        self,
        target: RiskMode,
        reason: str,
        triggered_by: str = "operator",
        trace_id: str = "",
        metadata: Optional[dict] = None,
    ) -> bool:
        """强制设置模式（不受单调性限制）

        仅用于测试或极端情况。
        """
        from_mode = self._state.mode

        self._state = RiskModeState(
            mode=target,
            since=datetime.now(timezone.utc),
            escalation_count=self._state.escalation_count + 1,
            last_escalation_reason=reason,
            manual_override=True,
            manual_override_by=triggered_by,
        )

        event = create_risk_mode_event(
            from_mode=from_mode,
            to_mode=target,
            reason=reason,
            trigger="force_override",
            triggered_by=triggered_by,
            trace_id=trace_id,
            metadata=metadata,
        )

        if self._audit_callback:
            try:
                self._audit_callback(event)
            except Exception as exc:
                logger.warning(
                    "RiskMode audit callback failed for force override trace_id=%s: %s",
                    trace_id,
                    exc,
                )

        return True
