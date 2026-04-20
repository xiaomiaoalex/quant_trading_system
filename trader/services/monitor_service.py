"""
Monitor Service - 系统监控与告警服务
===================================
负责采集系统指标、检测告警规则、触发告警。

职责：
- 指标采集：持仓数量、未成交订单数、当日PnL、KillSwitch级别、Adapter健康状态
- 告警规则引擎：PnL超日亏损阈值、未成交订单堆积超阈值、Adapter进入DEGRADED
- 告警输出：结构化日志（JSON格式，含trace_id）

约束：
- Fail-Closed：监控服务故障不得影响交易执行
- 所有异常必须被捕获并记录，不向上抛出
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Any, Literal

from trader.api.models.schemas import (
    MonitorSnapshot,
    Alert,
    AdapterHealthStatus,
    AlertRule,
    AlertSeverity,
)


# Re-export AlertRule from schemas for backwards compatibility
# AlertRule is now defined in schemas.py to avoid duplication
__all__ = ["MonitorService", "AlertRule", "TriggeredAlert", "AlertSeverity"]


logger = logging.getLogger(__name__)


def _safe_float(value: str, default: float = 0.0) -> float:
    """
    安全地将字符串转换为浮点数。
    
    Args:
        value: 待转换的字符串值
        default: 转换失败时的默认值
        
    Returns:
        转换后的浮点数，失败时返回默认值
    """
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


@dataclass
class TriggeredAlert:
    """已触发的告警（带去重信息）"""
    alert: Alert
    triggered_at: datetime
    cooldown_until: datetime


class MonitorService:
    """
    系统监控服务
    
    采集系统指标并根据告警规则进行检测。
    采用Fail-Closed设计：任何异常都被捕获，不影响交易执行。
    """

    # 默认告警规则
    DEFAULT_ALERT_RULES: List[AlertRule] = [
        AlertRule(
            rule_name="daily_pnl_loss",
            metric_key="daily_pnl",
            threshold=-1000.0,  # 日亏损超过1000U
            comparison="lt",
            severity="HIGH",
            cooldown_seconds=300,
        ),
        AlertRule(
            rule_name="open_orders_exceeded",
            metric_key="open_orders_count",
            threshold=50.0,  # 未成交订单超过50个
            comparison="gt",
            severity="MEDIUM",
            cooldown_seconds=60,
        ),
        AlertRule(
            rule_name="adapter_degraded",
            metric_key="adapter_degraded_count",
            threshold=0.0,  # 任何适配器DEGRADED
            comparison="gt",
            severity="CRITICAL",
            cooldown_seconds=120,
        ),
        # Task 19: 运行时阈值告警
        AlertRule(
            rule_name="tick_lag_high",
            metric_key="tick_lag_ms",
            threshold=1000.0,  # Tick延迟超过1000ms
            comparison="gt",
            severity="HIGH",
            cooldown_seconds=60,
        ),
        AlertRule(
            rule_name="order_reject_rate_high",
            metric_key="order_submit_reject",
            threshold=10.0,  # 拒单数超过10个
            comparison="gt",
            severity="MEDIUM",
            cooldown_seconds=120,
        ),
        AlertRule(
            rule_name="ws_reconnect_high",
            metric_key="ws_reconnect_count",
            threshold=5.0,  # WS重连超过5次
            comparison="gt",
            severity="MEDIUM",
            cooldown_seconds=300,
        ),
        AlertRule(
            rule_name="fill_latency_high",
            metric_key="fill_latency_ms_avg",
            threshold=500.0,  # 平均成交延迟超过500ms
            comparison="gt",
            severity="HIGH",
            cooldown_seconds=60,
        ),
    ]

    def __init__(self):
        self._alert_rules: Dict[str, AlertRule] = {
            rule.rule_name: rule for rule in self.DEFAULT_ALERT_RULES
        }
        self._triggered_alerts: Dict[str, TriggeredAlert] = {}  # rule_name -> TriggeredAlert
        self._adapter_health: Dict[str, AdapterHealthStatus] = {}

    def add_alert_rule(self, rule: AlertRule) -> None:
        """添加或更新告警规则"""
        self._alert_rules[rule.rule_name] = rule

    def remove_alert_rule(self, rule_name: str) -> bool:
        """移除告警规则"""
        return self._alert_rules.pop(rule_name, None) is not None

    def update_adapter_health(
        self,
        adapter_name: str,
        status: str,
        last_heartbeat_ts_ms: Optional[int] = None,
        error_count: int = 0,
        message: Optional[str] = None,
    ) -> None:
        """更新适配器健康状态"""
        self._adapter_health[adapter_name] = AdapterHealthStatus(
            adapter_name=adapter_name,
            status=status,
            last_heartbeat_ts_ms=last_heartbeat_ts_ms,
            error_count=error_count,
            message=message,
        )

    def _evaluate_rule(
        self,
        rule: AlertRule,
        metric_value: float,
        now: datetime,
    ) -> Optional[Alert]:
        """评估单个告警规则"""
        # 检查冷却时间
        if rule.rule_name in self._triggered_alerts:
            triggered = self._triggered_alerts[rule.rule_name]
            if now < triggered.cooldown_until:
                return None  # 在冷却期内，不重复告警

        # 比较
        passed = False
        if rule.comparison == "gt":
            passed = metric_value > rule.threshold
        elif rule.comparison == "lt":
            passed = metric_value < rule.threshold
        elif rule.comparison == "gte":
            passed = metric_value >= rule.threshold
        elif rule.comparison == "lte":
            passed = metric_value <= rule.threshold
        elif rule.comparison == "eq":
            passed = metric_value == rule.threshold
        else:
            logger.warning(
                "INVALID_COMPARISON_OPERATOR",
                extra={
                    "rule_name": rule.rule_name,
                    "comparison": rule.comparison,
                    "metric_key": rule.metric_key,
                },
            )

        if passed:
            alert = Alert(
                alert_id=str(uuid.uuid4()),
                rule_name=rule.rule_name,
                severity=rule.severity,
                message=self._format_alert_message(rule, metric_value),
                metric_key=rule.metric_key,
                metric_value=metric_value,
                threshold=rule.threshold,
                triggered_at=now.isoformat(),
            )
            
            # 记录触发状态
            self._triggered_alerts[rule.rule_name] = TriggeredAlert(
                alert=alert,
                triggered_at=now,
                cooldown_until=now + timedelta(seconds=rule.cooldown_seconds),
            )
            
            return alert
        
        return None

    def _format_alert_message(self, rule: AlertRule, metric_value: float) -> str:
        """格式化告警消息"""
        comparison_symbols = {
            "gt": ">",
            "lt": "<",
            "gte": ">=",
            "lte": "<=",
            "eq": "==",
        }
        symbol = comparison_symbols.get(rule.comparison, rule.comparison)
        return (
            f"Alert: {rule.rule_name} triggered. "
            f"{rule.metric_key}={metric_value} {symbol} threshold={rule.threshold}"
        )

    def _check_rules(
        self,
        snapshot: MonitorSnapshot,
        now: datetime,
    ) -> List[Alert]:
        """检查所有告警规则"""
        alerts: List[Alert] = []
        
        # 构建指标映射
        metrics: Dict[str, float] = {
            "daily_pnl": _safe_float(snapshot.daily_pnl),
            "open_orders_count": float(snapshot.open_orders_count),
            "pending_orders_count": float(snapshot.pending_orders_count),
            "total_positions": float(snapshot.total_positions),
            "killswitch_level": float(snapshot.killswitch_level),
            "adapter_degraded_count": sum(
                1 for a in snapshot.adapters.values() if a.status == "DEGRADED"
            ),
            "adapter_down_count": sum(
                1 for a in snapshot.adapters.values() if a.status == "DOWN"
            ),
        }

        for rule in self._alert_rules.values():
            if rule.metric_key not in metrics:
                continue
            
            alert = self._evaluate_rule(rule, metrics[rule.metric_key], now)
            if alert:
                alerts.append(alert)
                # 结构化日志输出
                self._log_alert(alert)

        return alerts

    # Severity to log level mapping
    _SEVERITY_TO_LOG_LEVEL = {
        "CRITICAL": logging.CRITICAL,
        "HIGH": logging.ERROR,
        "MEDIUM": logging.WARNING,
        "LOW": logging.INFO,
    }

    def _log_alert(self, alert: Alert) -> None:
        """输出结构化告警日志，按严重程度映射日志级别"""
        log_level = self._SEVERITY_TO_LOG_LEVEL.get(alert.severity, logging.WARNING)
        logger.log(
            log_level,
            "ALERT_TRIGGERED",
            extra={
                "alert_id": alert.alert_id,
                "rule_name": alert.rule_name,
                "severity": alert.severity,
                "msg_text": alert.message,
                "metric_key": alert.metric_key,
                "metric_value": alert.metric_value,
                "threshold": alert.threshold,
                "triggered_at": alert.triggered_at,
                "trace_id": f"alert-{alert.alert_id}",
            },
        )

    def get_snapshot(
        self,
        positions: List[Any] = None,
        open_orders_count: int = 0,
        pending_orders_count: int = 0,
        daily_pnl: str = "0",
        daily_pnl_pct: str = "0",
        realized_pnl: str = "0",
        unrealized_pnl: str = "0",
        killswitch_level: int = 0,
        killswitch_scope: str = "GLOBAL",
    ) -> MonitorSnapshot:
        """
        获取系统监控快照
        
        Args:
            positions: 持仓列表
            open_orders_count: 未成交订单数
            pending_orders_count: 待处理订单数
            daily_pnl: 当日盈亏
            daily_pnl_pct: 当日盈亏百分比
            realized_pnl: 已实现盈亏
            unrealized_pnl: 未实现盈亏
            killswitch_level: KillSwitch级别
            killswitch_scope: KillSwitch范围
            
        Returns:
            MonitorSnapshot: 系统监控快照
        """
        try:
            now = datetime.now(timezone.utc)
            
            # 计算总敞口
            total_exposure = "0"
            if positions:
                exposure = sum(
                    float(getattr(p, "quantity", 0) or 0) * 
                    float(getattr(p, "current_price", 0) or 0)
                    for p in positions
                )
                total_exposure = str(exposure)

            # 构建快照
            snapshot = MonitorSnapshot(
                timestamp=now.isoformat().replace("+00:00", "Z"),
                total_positions=len(positions) if positions else 0,
                total_exposure=total_exposure,
                open_orders_count=open_orders_count,
                pending_orders_count=pending_orders_count,
                daily_pnl=daily_pnl,
                daily_pnl_pct=daily_pnl_pct,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl,
                killswitch_level=killswitch_level,
                killswitch_scope=killswitch_scope,
                adapters=dict(self._adapter_health),
                active_alerts=[],
                alert_count_by_severity={},
            )

            # 检查告警规则
            alerts = self._check_rules(snapshot, now)
            snapshot.active_alerts = alerts
            
            # 统计各严重程度告警数
            severity_counts: Dict[str, int] = {}
            for alert in alerts:
                severity_counts[alert.severity] = severity_counts.get(alert.severity, 0) + 1
            snapshot.alert_count_by_severity = severity_counts

            return snapshot

        except Exception as e:
            # Fail-Closed: 监控服务异常不影响交易执行
            logger.error(
                "MONITOR_SNAPSHOT_ERROR",
                extra={
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            # 返回最小可用快照
            return MonitorSnapshot(
                timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )

    def _cleanup_expired_alerts(self, now: datetime) -> None:
        """清理已过期且未触发的告警，防止内存无限增长"""
        expired = [
            name for name, triggered in self._triggered_alerts.items()
            if now >= triggered.cooldown_until
        ]
        for name in expired:
            del self._triggered_alerts[name]

    def get_active_alerts(self) -> List[Alert]:
        """获取当前活跃告警列表"""
        now = datetime.now(timezone.utc)
        
        # 清理过期告警
        self._cleanup_expired_alerts(now)
        
        active = []
        for triggered in self._triggered_alerts.values():
            if now < triggered.cooldown_until:
                active.append(triggered.alert)
        
        return active

    def clear_alert(self, rule_name: str) -> bool:
        """清除指定告警规则的触发状态"""
        return self._triggered_alerts.pop(rule_name, None) is not None

    def clear_all_alerts(self) -> None:
        """清除所有告警状态"""
        self._triggered_alerts.clear()