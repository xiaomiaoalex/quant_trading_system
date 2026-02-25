"""
Environmental Risk Events Unit Tests
===================================
测试环境风险事件模型的功能。
"""
import time
import pytest

from trader.adapters.binance.environmental_risk import (
    EnvironmentalRiskEvent,
    LocalEventLog,
    RiskSeverity,
    RiskScope,
    RecommendedLevel,
)


class TestEnvironmentalRiskEvent:
    """Environmental Risk Event 测试"""

    def test_dedup_key_generation(self):
        """测试去重键生成"""
        key1 = EnvironmentalRiskEvent.generate_dedup_key(
            reason="test_reason",
            window_end_ms=1609459200000,
            account_id="acc123",
            venue="binance"
        )

        key2 = EnvironmentalRiskEvent.generate_dedup_key(
            reason="test_reason",
            window_end_ms=1609459200000,
            account_id="acc123",
            venue="binance"
        )

        assert key1 == key2
        assert len(key1) == 16

    def test_dedup_key_unique_per_params(self):
        """测试不同参数生成不同的去重键"""
        key1 = EnvironmentalRiskEvent.generate_dedup_key(
            reason="test_reason",
            window_end_ms=1609459200000
        )

        key2 = EnvironmentalRiskEvent.generate_dedup_key(
            reason="test_reason",
            window_end_ms=1609459200001
        )

        assert key1 != key2

    def test_create_from_adapter_health(self):
        """测试从适配器健康状态创建事件"""
        health_data = {
            "public_stream_state": "CONNECTED",
            "private_stream_state": "DISCONNECTED",
            "rate_budget_state": {"is_degraded": True},
            "backoff_state": {},
            "account_id": "acc123",
            "venue": "binance",
            "metrics": {"private": {"consecutive_failures": 5}}
        }

        event = EnvironmentalRiskEvent.create_from_adapter_health(
            adapter_name="binance_adapter",
            health_data=health_data,
            scope=RiskScope.GLOBAL
        )

        assert event.adapter_name == "binance_adapter"
        assert event.severity == RiskSeverity.CRITICAL
        assert event.recommended_level == RecommendedLevel.L2_CLOSE_ONLY
        assert event.scope == RiskScope.GLOBAL
        assert "private_stream_state" in event.metrics

    def test_create_from_adapter_health_high_severity(self):
        """测试从适配器健康状态创建事件（普通降级）"""
        health_data = {
            "public_stream_state": "CONNECTED",
            "private_stream_state": "DEGRADED",
            "rate_budget_state": {"is_degraded": True},
            "backoff_state": {},
            "account_id": "",
            "venue": "",
        }

        event = EnvironmentalRiskEvent.create_from_adapter_health(
            adapter_name="test_adapter",
            health_data=health_data,
        )

        assert event.severity == RiskSeverity.HIGH
        assert event.recommended_level == RecommendedLevel.L1_NO_NEW_POS

    def test_to_dict(self):
        """测试转换为字典"""
        event = EnvironmentalRiskEvent(
            dedup_key="test_key",
            severity=RiskSeverity.HIGH,
            reason="test_reason",
            scope=RiskScope.GLOBAL,
            metrics={"test": "data"},
            recommended_level=RecommendedLevel.L1_NO_NEW_POS,
            adapter_name="test_adapter",
        )

        data = event.to_dict()

        assert data["dedup_key"] == "test_key"
        assert data["severity"] == "HIGH"
        assert data["reason"] == "test_reason"
        assert data["recommended_level"] == 1

    def test_from_dict(self):
        """测试从字典创建"""
        data = {
            "event_id": "evt_123",
            "dedup_key": "test_key",
            "severity": "HIGH",
            "reason": "test_reason",
            "scope": "GLOBAL",
            "metrics": {"test": "data"},
            "recommended_level": 1,
            "ts_ms": 1609459200000,
            "adapter_name": "test_adapter",
            "venue": "binance",
            "account_id": "acc123",
        }

        event = EnvironmentalRiskEvent.from_dict(data)

        assert event.event_id == "evt_123"
        assert event.dedup_key == "test_key"
        assert event.severity == RiskSeverity.HIGH
        assert event.recommended_level == RecommendedLevel.L1_NO_NEW_POS


class TestLocalEventLog:
    """Local Event Log 测试"""

    def test_add_event(self):
        """测试添加事件"""
        log = LocalEventLog()

        event = EnvironmentalRiskEvent(
            dedup_key="key1",
            reason="test_reason"
        )

        log.add(event)

        assert len(log.events) == 1

    def test_max_size(self):
        """测试最大大小限制"""
        log = LocalEventLog(max_size=3)

        for i in range(5):
            event = EnvironmentalRiskEvent(dedup_key=f"key{i}")
            log.add(event)

        assert len(log.events) == 3

    def test_get_unreported(self):
        """测试获取未上报事件"""
        log = LocalEventLog()

        event1 = EnvironmentalRiskEvent(dedup_key="key1", is_reported=False)
        event2 = EnvironmentalRiskEvent(dedup_key="key2", is_reported=True)

        log.add(event1)
        log.add(event2)

        unreported = log.get_unreported()

        assert len(unreported) == 1
        assert unreported[0].dedup_key == "key1"

    def test_mark_reported(self):
        """测试标记已上报"""
        log = LocalEventLog()

        event = EnvironmentalRiskEvent(dedup_key="key1", is_reported=False)
        log.add(event)

        log.mark_reported(event.event_id)

        assert log.events[0].is_reported is True

    def test_get_recent(self):
        """测试获取最近事件"""
        log = LocalEventLog()

        for i in range(10):
            event = EnvironmentalRiskEvent(dedup_key=f"key{i}")
            log.add(event)

        recent = log.get_recent(limit=3)

        assert len(recent) == 3

    def test_clear(self):
        """测试清空"""
        log = LocalEventLog()

        event = EnvironmentalRiskEvent(dedup_key="key1")
        log.add(event)

        log.clear()

        assert len(log.events) == 0


class TestRiskEnums:
    """Risk 枚举测试"""

    def test_risk_severity_values(self):
        """测试风险严重等级"""
        assert RiskSeverity.LOW.value == "LOW"
        assert RiskSeverity.MEDIUM.value == "MEDIUM"
        assert RiskSeverity.HIGH.value == "HIGH"
        assert RiskSeverity.CRITICAL.value == "CRITICAL"

    def test_risk_scope_values(self):
        """测试风险范围"""
        assert RiskScope.GLOBAL.value == "GLOBAL"
        assert RiskScope.ACCOUNT.value == "ACCOUNT"
        assert RiskScope.VENUE.value == "VENUE"
        assert RiskScope.STRATEGY.value == "STRATEGY"

    def test_recommended_level_values(self):
        """测试建议等级"""
        assert RecommendedLevel.L0_NORMAL.value == 0
        assert RecommendedLevel.L1_NO_NEW_POS.value == 1
        assert RecommendedLevel.L2_CLOSE_ONLY.value == 2
        assert RecommendedLevel.L3_FULL_STOP.value == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
