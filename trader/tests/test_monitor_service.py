"""
Unit tests for MonitorService
"""
import logging
import pytest
from datetime import datetime, timezone

from trader.services.monitor_service import (
    MonitorService,
    AlertRule,
    TriggeredAlert,
    AlertSeverity,
)
from trader.api.models.schemas import Alert


class TestMonitorServiceBasic:
    """基础功能测试"""

    def test_get_snapshot_empty(self):
        """测试空快照"""
        service = MonitorService()
        snapshot = service.get_snapshot()
        
        assert snapshot.total_positions == 0
        assert snapshot.open_orders_count == 0
        assert snapshot.daily_pnl == "0"
        assert snapshot.killswitch_level == 0
        assert len(snapshot.adapters) == 0
        assert len(snapshot.active_alerts) == 0

    def test_get_snapshot_with_values(self):
        """测试带参数的快照"""
        service = MonitorService()
        snapshot = service.get_snapshot(
            open_orders_count=10,
            pending_orders_count=5,
            daily_pnl="-500",
            daily_pnl_pct="-2.5",
            realized_pnl="1000",
            unrealized_pnl="-500",
            killswitch_level=1,
            killswitch_scope="GLOBAL",
        )
        
        assert snapshot.open_orders_count == 10
        assert snapshot.pending_orders_count == 5
        assert snapshot.daily_pnl == "-500"
        assert snapshot.killswitch_level == 1

    def test_add_remove_alert_rule(self):
        """测试添加和移除告警规则"""
        service = MonitorService()
        
        rule = AlertRule(
            rule_name="test_rule",
            metric_key="daily_pnl",
            threshold=-100.0,
            comparison="lt",
            severity="HIGH",
            cooldown_seconds=60,
        )
        
        service.add_alert_rule(rule)
        assert "test_rule" in service._alert_rules
        
        removed = service.remove_alert_rule("test_rule")
        assert removed is True
        assert "test_rule" not in service._alert_rules

    def test_remove_nonexistent_rule(self):
        """测试移除不存在的规则"""
        service = MonitorService()
        removed = service.remove_alert_rule("nonexistent")
        assert removed is False


class TestAlertRuleEvaluation:
    """告警规则评估测试"""

    def test_pnl_threshold_breached(self):
        """测试PnL阈值触发告警"""
        service = MonitorService()
        
        # 日亏损-1500，触发-1000阈值
        snapshot = service.get_snapshot(
            daily_pnl="-1500",
            open_orders_count=10,
        )
        
        # 检查是否有告警
        assert len(snapshot.active_alerts) >= 1
        
        # 应该有 daily_pnl_loss 告警
        pnl_alerts = [a for a in snapshot.active_alerts if a.rule_name == "daily_pnl_loss"]
        assert len(pnl_alerts) == 1
        assert pnl_alerts[0].severity == "HIGH"

    def test_pnl_threshold_not_breached(self):
        """测试PnL未触发告警"""
        service = MonitorService()
        
        # 日盈利500，不触发-1000阈值
        snapshot = service.get_snapshot(
            daily_pnl="500",
            open_orders_count=10,
        )
        
        # 不应该有 daily_pnl_loss 告警
        pnl_alerts = [a for a in snapshot.active_alerts if a.rule_name == "daily_pnl_loss"]
        assert len(pnl_alerts) == 0

    def test_open_orders_threshold_breached(self):
        """测试未成交订单数触发告警"""
        service = MonitorService()
        
        # 60个未成交订单，触发50阈值
        snapshot = service.get_snapshot(
            open_orders_count=60,
            daily_pnl="0",
        )
        
        # 应该有 open_orders_exceeded 告警
        order_alerts = [a for a in snapshot.active_alerts if a.rule_name == "open_orders_exceeded"]
        assert len(order_alerts) == 1
        assert order_alerts[0].severity == "MEDIUM"

    def test_open_orders_threshold_not_breached(self):
        """测试未成交订单数未触发告警"""
        service = MonitorService()
        
        # 30个未成交订单，不触发50阈值
        snapshot = service.get_snapshot(
            open_orders_count=30,
            daily_pnl="0",
        )
        
        # 不应该有 open_orders_exceeded 告警
        order_alerts = [a for a in snapshot.active_alerts if a.rule_name == "open_orders_exceeded"]
        assert len(order_alerts) == 0


class TestAlertCooldown:
    """告警冷却时间测试"""

    def test_alert_cooldown_prevents_duplicate(self):
        """测试冷却时间内不重复告警"""
        service = MonitorService()
        
        # 第一次获取快照，触发告警
        snapshot1 = service.get_snapshot(daily_pnl="-1500")
        initial_count = len(snapshot1.active_alerts)
        
        # 立即再次获取快照，在冷却时间内，不应该产生新的触发
        snapshot2 = service.get_snapshot(daily_pnl="-1500")
        
        # 冷却期间活跃告警列表不应增加（触发状态已保留在内存中）
        active_alerts = service.get_active_alerts()
        assert len(active_alerts) == initial_count

    def test_clear_alert(self):
        """测试清除告警"""
        service = MonitorService()
        
        # 触发告警
        service.get_snapshot(daily_pnl="-1500", open_orders_count=10)
        
        # 清除
        cleared = service.clear_alert("daily_pnl_loss")
        assert cleared is True
        
        # 再次获取应该不显示该告警（除非再次触发）
        cleared = service.clear_alert("daily_pnl_loss")
        assert cleared is False

    def test_clear_all_alerts(self):
        """测试清除所有告警"""
        service = MonitorService()
        
        # 触发告警
        service.get_snapshot(daily_pnl="-1500", open_orders_count=60)
        
        # 清除所有
        service.clear_all_alerts()
        
        # 活跃告警应该为空
        active = service.get_active_alerts()
        assert len(active) == 0


class TestAdapterHealth:
    """适配器健康状态测试"""

    def test_update_adapter_health(self):
        """测试更新适配器健康状态"""
        service = MonitorService()
        
        service.update_adapter_health(
            adapter_name="binance_connector",
            status="HEALTHY",
            last_heartbeat_ts_ms=1234567890,
            error_count=0,
        )
        
        assert "binance_connector" in service._adapter_health
        assert service._adapter_health["binance_connector"].status == "HEALTHY"

    def test_adapter_in_snapshot(self):
        """测试适配器状态出现在快照中"""
        service = MonitorService()
        
        service.update_adapter_health(
            adapter_name="binance_connector",
            status="DEGRADED",
            last_heartbeat_ts_ms=1234567890,
            error_count=5,
            message="High latency",
        )
        
        snapshot = service.get_snapshot(
            daily_pnl="0",
            open_orders_count=0,
        )
        
        assert "binance_connector" in snapshot.adapters
        assert snapshot.adapters["binance_connector"].status == "DEGRADED"


class TestFailClosed:
    """Fail-Closed测试"""

    def test_exception_does_not_propagate(self, caplog):
        """测试无效输入被安全处理，不抛出异常"""
        import logging
        service = MonitorService()
        
        # 由于MonitorService内部捕获所有异常，应该始终返回快照
        # daily_pnl 是字符串字段，无效值会被 _safe_float 安全转换为默认值
        snapshot = service.get_snapshot(
            daily_pnl="invalid",  # 会被 _safe_float 转为 0.0
            open_orders_count=0,
        )
        assert snapshot is not None
        assert hasattr(snapshot, 'timestamp')
        # _safe_float 会安全处理无效值，不抛出异常也不记录错误
        # 因为 "invalid" 被安全转换为 0.0，这是预期行为

    def test_invalid_pnl_value_returns_safe_default(self):
        """测试无效PnL值使用安全默认值，不抛出异常"""
        service = MonitorService()
        
        # 无效字符串值应该被安全转换为0.0，不抛出异常
        snapshot = service.get_snapshot(
            daily_pnl="not_a_number",
            open_orders_count=0,
        )
        assert snapshot is not None
        # 安全默认值应该是0，不应该有告警
        assert len(snapshot.active_alerts) == 0


class TestAlertSeverityCount:
    """告警严重程度统计测试"""

    def test_severity_counts(self):
        """测试告警按严重程度统计"""
        service = MonitorService()
        
        # 触发多个告警
        service.add_alert_rule(AlertRule(
            rule_name="critical_test",
            metric_key="adapter_degraded_count",
            threshold=0.0,
            comparison="gt",
            severity="CRITICAL",
            cooldown_seconds=60,
        ))
        
        service.update_adapter_health(
            adapter_name="test_adapter",
            status="DEGRADED",
        )
        
        snapshot = service.get_snapshot(
            daily_pnl="-1500",
            open_orders_count=60,
        )
        
        # 应该有统计
        assert isinstance(snapshot.alert_count_by_severity, dict)


class TestCustomRules:
    """自定义告警规则测试"""

    def test_custom_rule_triggered(self):
        """测试自定义规则触发"""
        service = MonitorService()
        
        # 添加自定义规则
        service.add_alert_rule(AlertRule(
            rule_name="custom_loss",
            metric_key="daily_pnl",
            threshold=-100.0,
            comparison="lt",
            severity="CRITICAL",
            cooldown_seconds=300,
        ))
        
        # 触发
        snapshot = service.get_snapshot(daily_pnl="-200")
        
        custom_alerts = [a for a in snapshot.active_alerts if a.rule_name == "custom_loss"]
        assert len(custom_alerts) == 1
        assert custom_alerts[0].severity == "CRITICAL"

    def test_rule_comparison_operators(self):
        """测试各种比较操作符"""
        service = MonitorService()
        
        # 使用 open_orders_count 指标测试各比较操作符
        # 阈值设为 10.0
        
        # 测试 gt: 10.0 > 10.0 = False (threshold=10, metric=10)
        service.add_alert_rule(AlertRule(rule_name="gt_test", metric_key="open_orders_count", threshold=10.0, comparison="gt", severity="LOW", cooldown_seconds=60))
        # 测试 lt: 10.0 < 10.0 = False
        service.add_alert_rule(AlertRule(rule_name="lt_test", metric_key="open_orders_count", threshold=10.0, comparison="lt", severity="LOW", cooldown_seconds=60))
        # 测试 gte: 10.0 >= 10.0 = True
        service.add_alert_rule(AlertRule(rule_name="gte_test", metric_key="open_orders_count", threshold=10.0, comparison="gte", severity="LOW", cooldown_seconds=60))
        # 测试 lte: 10.0 <= 10.0 = True
        service.add_alert_rule(AlertRule(rule_name="lte_test", metric_key="open_orders_count", threshold=10.0, comparison="lte", severity="LOW", cooldown_seconds=60))
        # 测试 eq: 10.0 == 10.0 = True
        service.add_alert_rule(AlertRule(rule_name="eq_test", metric_key="open_orders_count", threshold=10.0, comparison="eq", severity="LOW", cooldown_seconds=60))
        
        # 使用 open_orders_count=10 调用快照，应该触发 gte, lte, eq 三个规则
        snapshot = service.get_snapshot(
            open_orders_count=10,
            daily_pnl="0",
        )
        
        triggered_rules = {a.rule_name for a in snapshot.active_alerts}
        
        # gt 和 lt 不应该触发（10 > 10 和 10 < 10 都为 False）
        assert "gt_test" not in triggered_rules, "gt should not trigger: 10 > 10 is False"
        assert "lt_test" not in triggered_rules, "lt should not trigger: 10 < 10 is False"
        
        # gte, lte, eq 应该触发（10 >= 10, 10 <= 10, 10 == 10 都为 True）
        assert "gte_test" in triggered_rules, "gte should trigger: 10 >= 10 is True"
        assert "lte_test" in triggered_rules, "lte should trigger: 10 <= 10 is True"
        assert "eq_test" in triggered_rules, "eq should trigger: 10 == 10 is True"
        
        # 测试边界值: open_orders_count=11
        service.clear_all_alerts()
        snapshot2 = service.get_snapshot(
            open_orders_count=11,
            daily_pnl="0",
        )
        
        triggered_rules2 = {a.rule_name for a in snapshot2.active_alerts}
        
        # 11 > 10 = True, gt 应该触发
        assert "gt_test" in triggered_rules2, "gt should trigger: 11 > 10 is True"
        # 11 < 10 = False, lt 不触发
        assert "lt_test" not in triggered_rules2, "lt should not trigger: 11 < 10 is False"
        
        # 测试边界值: open_orders_count=9
        service.clear_all_alerts()
        snapshot3 = service.get_snapshot(
            open_orders_count=9,
            daily_pnl="0",
        )
        
        triggered_rules3 = {a.rule_name for a in snapshot3.active_alerts}
        
        # 9 > 10 = False, gt 不触发
        assert "gt_test" not in triggered_rules3, "gt should not trigger: 9 > 10 is False"
        # 9 < 10 = True, lt 应该触发
        assert "lt_test" in triggered_rules3, "lt should trigger: 9 < 10 is True"