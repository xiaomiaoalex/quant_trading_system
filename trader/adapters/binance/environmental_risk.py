"""
Environmental Risk Events - 环境风险事件模型
=============================================
定义适配器级联失败时产生的环境风险事件。

当底层网络崩溃导致无法向 Control Plane 上报时，
系统在本地记录此事件并触发自保机制。
"""
import time
import uuid
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Any, List
from datetime import datetime


class RiskSeverity(Enum):
    """风险严重等级"""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskScope(Enum):
    """风险范围"""
    GLOBAL = "GLOBAL"
    ACCOUNT = "ACCOUNT"
    VENUE = "VENUE"
    STRATEGY = "STRATEGY"


class RecommendedLevel(Enum):
    """建议的风控等级"""
    L0_NORMAL = 0    # 正常运行
    L1_NO_NEW_POS = 1  # 禁止新开仓
    L2_CLOSE_ONLY = 2  # 只允许平仓
    L3_FULL_STOP = 3   # 完全停止


@dataclass
class EnvironmentalRiskEvent:
    """
    环境风险事件

    当检测到适配器进入 DEGRADED_MODE 且无法向 Control Plane 上报时，
    本地记录此事件作为审计日志。
    """
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    dedup_key: str = ""

    severity: RiskSeverity = RiskSeverity.MEDIUM
    reason: str = ""
    scope: RiskScope = RiskScope.GLOBAL

    metrics: Dict[str, Any] = field(default_factory=dict)
    recommended_level: RecommendedLevel = RecommendedLevel.L1_NO_NEW_POS

    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    adapter_name: str = ""
    venue: str = ""
    account_id: str = ""

    is_reported: bool = False
    report_attempts: int = 0
    last_report_error: Optional[str] = None

    @staticmethod
    def generate_dedup_key(
        reason: str,
        window_end_ms: int,
        account_id: str = "",
        venue: str = ""
    ) -> str:
        """
        生成幂等去重键

        Args:
            reason: 风险原因
            window_end_ms: 时间窗口结束时间戳（毫秒）
            account_id: 账户 ID
            venue: 交易场所

        Returns:
            唯一的 dedup_key
        """
        raw = f"{reason}:{window_end_ms}:{account_id}:{venue}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def create_from_adapter_health(
        adapter_name: str,
        health_data: Dict[str, Any],
        scope: RiskScope = RiskScope.GLOBAL
    ) -> "EnvironmentalRiskEvent":
        """
        从适配器健康状态创建风险事件

        Args:
            adapter_name: 适配器名称
            health_data: 健康状态数据
            scope: 风险范围

        Returns:
            EnvironmentalRiskEvent
        """
        reason = f"ENV_RISK:AdapterDegraded:{adapter_name}"

        window_end_ms = int(time.time() * 1000) + 60000

        severity = RiskSeverity.HIGH
        recommended = RecommendedLevel.L1_NO_NEW_POS

        if health_data.get("private_stream_state") == "DISCONNECTED":
            severity = RiskSeverity.CRITICAL
            recommended = RecommendedLevel.L2_CLOSE_ONLY

        metrics = {
            "public_stream_state": health_data.get("public_stream_state", "UNKNOWN"),
            "private_stream_state": health_data.get("private_stream_state", "UNKNOWN"),
            "rate_budget": health_data.get("rate_budget_state", {}),
            "backoff": health_data.get("backoff_state", {}),
            "consecutive_failures": health_data.get("metrics", {}).get("private", {}).get("consecutive_failures", 0),
        }

        return EnvironmentalRiskEvent(
            dedup_key=EnvironmentalRiskEvent.generate_dedup_key(
                reason=reason,
                window_end_ms=window_end_ms,
                account_id=health_data.get("account_id", ""),
                venue=health_data.get("venue", "")
            ),
            severity=severity,
            reason=reason,
            scope=scope,
            metrics=metrics,
            recommended_level=recommended,
            adapter_name=adapter_name,
            venue=health_data.get("venue", ""),
            account_id=health_data.get("account_id", ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "dedup_key": self.dedup_key,
            "severity": self.severity.value,
            "reason": self.reason,
            "metrics": self.metrics,
            "recommended_level": self.recommended_level.value,
            "scope": self.scope.value,
            "ts_ms": self.ts_ms,
            "adapter_name": self.adapter_name,
            "venue": self.venue,
            "account_id": self.account_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EnvironmentalRiskEvent":
        """从字典创建"""
        return cls(
            event_id=data.get("event_id", str(uuid.uuid4())),
            dedup_key=data.get("dedup_key", ""),
            severity=RiskSeverity(data.get("severity", "MEDIUM")),
            reason=data.get("reason", ""),
            scope=RiskScope(data.get("scope", "GLOBAL")),
            metrics=data.get("metrics", {}),
            recommended_level=RecommendedLevel(data.get("recommended_level", 1)),
            ts_ms=data.get("ts_ms", int(time.time() * 1000)),
            adapter_name=data.get("adapter_name", ""),
            venue=data.get("venue", ""),
            account_id=data.get("account_id", ""),
            is_reported=data.get("is_reported", False),
            report_attempts=data.get("report_attempts", 0),
            last_report_error=data.get("last_report_error"),
        )


@dataclass
class LocalEventLog:
    """
    本地事件日志

    存储无法上报到 Control Plane 的环境风险事件。
    """
    events: List[EnvironmentalRiskEvent] = field(default_factory=list)
    max_size: int = 1000

    def add(self, event: EnvironmentalRiskEvent) -> None:
        """添加事件"""
        if len(self.events) >= self.max_size:
            self.events.pop(0)
        self.events.append(event)

    def get_unreported(self) -> List[EnvironmentalRiskEvent]:
        """获取未上报的事件"""
        return [e for e in self.events if not e.is_reported]

    def mark_reported(self, event_id: str) -> None:
        """标记为已上报"""
        for event in self.events:
            if event.event_id == event_id:
                event.is_reported = True
                break

    def get_recent(self, limit: int = 100) -> List[EnvironmentalRiskEvent]:
        """获取最近的事件"""
        return self.events[-limit:]

    def clear(self) -> None:
        """清空日志"""
        self.events.clear()
