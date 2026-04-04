"""
RiskInterventionTracker 单元测试
================================
测试风控干预追踪器的记录、指标计算和查询功能。
"""
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from trader.core.domain.services.risk_intervention_tracker import (
    RiskInterventionTracker,
    RiskInterventionRecord,
    RiskInterventionMetrics,
    calculate_expectancy,
    calculate_sharpe_decay,
)


# ==================== Fixtures ====================

@pytest.fixture
def tracker() -> RiskInterventionTracker:
    """创建空的追踪器"""
    return RiskInterventionTracker()


@pytest.fixture
def sample_records() -> list[RiskInterventionRecord]:
    """创建示例记录"""
    now = datetime.now(timezone.utc)
    return [
        RiskInterventionRecord(
            signal_id="sig-001",
            strategy_id="strategy_A",
            rule_name="depth_check",
            action="PASS",
            original_size=1.0,
            approved_size=1.0,
            trace_id="trace-001",
            timestamp=now - timedelta(hours=2),
        ),
        RiskInterventionRecord(
            signal_id="sig-002",
            strategy_id="strategy_A",
            rule_name="depth_check",
            action="REJECT",
            original_size=1.0,
            approved_size=0.0,
            trace_id="trace-002",
            timestamp=now - timedelta(hours=1),
        ),
        RiskInterventionRecord(
            signal_id="sig-003",
            strategy_id="strategy_A",
            rule_name="time_window",
            action="REDUCE",
            original_size=1.0,
            approved_size=0.5,
            trace_id="trace-003",
            timestamp=now,
        ),
        RiskInterventionRecord(
            signal_id="sig-004",
            strategy_id="strategy_B",
            rule_name="killswitch",
            action="HALT",
            original_size=1.0,
            approved_size=0.0,
            trace_id="trace-004",
            timestamp=now,
        ),
    ]


# ==================== 记录测试 ====================

class TestRecordCreation:
    """测试记录创建"""
    
    def test_record_creation(self):
        """创建基本记录"""
        record = RiskInterventionRecord(
            signal_id="sig-001",
            strategy_id="strategy_A",
            rule_name="depth_check",
            action="PASS",
            original_size=1.0,
            approved_size=1.0,
        )
        
        assert record.signal_id == "sig-001"
        assert record.strategy_id == "strategy_A"
        assert record.rule_name == "depth_check"
        assert record.action == "PASS"
        assert record.original_size == 1.0
        assert record.approved_size == 1.0
        assert record.trace_id != ""
        assert record.timestamp is not None
    
    def test_record_with_market_state_ref(self):
        """创建带市场状态引用的记录"""
        record = RiskInterventionRecord(
            signal_id="sig-001",
            strategy_id="strategy_A",
            rule_name="depth_check",
            action="REJECT",
            original_size=1.0,
            approved_size=0.0,
            market_state_ref="orderbook_hash_abc123",
        )
        
        assert record.market_state_ref == "orderbook_hash_abc123"
    
    def test_record_negative_original_size_raises(self):
        """负数 original_size 应抛出异常"""
        with pytest.raises(ValueError, match="original_size must be non-negative"):
            RiskInterventionRecord(
                signal_id="sig-001",
                strategy_id="strategy_A",
                rule_name="test",
                action="PASS",
                original_size=-1.0,
                approved_size=1.0,
            )
    
    def test_record_negative_approved_size_raises(self):
        """负数 approved_size 应抛出异常"""
        with pytest.raises(ValueError, match="approved_size must be non-negative"):
            RiskInterventionRecord(
                signal_id="sig-001",
                strategy_id="strategy_A",
                rule_name="test",
                action="PASS",
                original_size=1.0,
                approved_size=-1.0,
            )


# ==================== 记录属性测试 ====================

class TestRecordProperties:
    """测试记录属性"""
    
    def test_size_change_ratio_full_reject(self):
        """完全拒绝时 size_change_ratio = 1.0"""
        record = RiskInterventionRecord(
            signal_id="sig-001",
            strategy_id="strategy_A",
            rule_name="test",
            action="REJECT",
            original_size=1.0,
            approved_size=0.0,
        )
        
        assert record.size_change_ratio == 1.0
    
    def test_size_change_ratio_half_reduce(self):
        """缩减一半时 size_change_ratio = 0.5"""
        record = RiskInterventionRecord(
            signal_id="sig-001",
            strategy_id="strategy_A",
            rule_name="test",
            action="REDUCE",
            original_size=1.0,
            approved_size=0.5,
        )
        
        assert record.size_change_ratio == 0.5
    
    def test_size_change_ratio_pass(self):
        """通过时 size_change_ratio = 0"""
        record = RiskInterventionRecord(
            signal_id="sig-001",
            strategy_id="strategy_A",
            rule_name="test",
            action="PASS",
            original_size=1.0,
            approved_size=1.0,
        )
        
        assert record.size_change_ratio == 0.0
    
    def test_size_change_ratio_zero_original(self):
        """original_size 为零时 size_change_ratio = 0"""
        record = RiskInterventionRecord(
            signal_id="sig-001",
            strategy_id="strategy_A",
            rule_name="test",
            action="PASS",
            original_size=0.0,
            approved_size=0.0,
        )
        
        assert record.size_change_ratio == 0.0
    
    def test_was_intervened_true_for_reject(self):
        """REJECT 视为被干预"""
        record = RiskInterventionRecord(
            signal_id="sig-001",
            strategy_id="strategy_A",
            rule_name="test",
            action="REJECT",
            original_size=1.0,
            approved_size=0.0,
        )
        
        assert record.was_intervened is True
    
    def test_was_intervened_true_for_reduce(self):
        """REDUCE 视为被干预"""
        record = RiskInterventionRecord(
            signal_id="sig-001",
            strategy_id="strategy_A",
            rule_name="test",
            action="REDUCE",
            original_size=1.0,
            approved_size=0.5,
        )
        
        assert record.was_intervened is True
    
    def test_was_intervened_false_for_pass(self):
        """PASS 视为未干预"""
        record = RiskInterventionRecord(
            signal_id="sig-001",
            strategy_id="strategy_A",
            rule_name="test",
            action="PASS",
            original_size=1.0,
            approved_size=1.0,
        )
        
        assert record.was_intervened is False


# ==================== 追踪器记录测试 ====================

class TestTrackerRecord:
    """测试追踪器记录功能"""
    
    def test_record_increases_count(self, tracker: RiskInterventionTracker):
        """记录后总数增加"""
        assert len(tracker) == 0
        
        tracker.record(
            signal_id="sig-001",
            strategy_id="strategy_A",
            rule_name="depth_check",
            action="PASS",
            original_size=1.0,
            approved_size=1.0,
        )
        
        assert len(tracker) == 1
    
    def test_record_returns_record(self, tracker: RiskInterventionTracker):
        """record() 返回创建的记录"""
        result = tracker.record(
            signal_id="sig-001",
            strategy_id="strategy_A",
            rule_name="depth_check",
            action="PASS",
            original_size=1.0,
            approved_size=1.0,
        )
        
        assert isinstance(result, RiskInterventionRecord)
        assert result.signal_id == "sig-001"
    
    def test_clear_resets_count(self, tracker: RiskInterventionTracker):
        """clear() 重置记录数"""
        tracker.record(
            signal_id="sig-001",
            strategy_id="strategy_A",
            rule_name="test",
            action="PASS",
            original_size=1.0,
            approved_size=1.0,
        )
        
        assert len(tracker) == 1
        
        tracker.clear()
        
        assert len(tracker) == 0


# ==================== 追踪器指标测试 ====================

class TestTrackerMetrics:
    """测试追踪器指标计算"""
    
    def test_metrics_empty_tracker(self, tracker: RiskInterventionTracker):
        """空追踪器返回零指标"""
        metrics = tracker.get_metrics()
        
        assert metrics.total_signals == 0
        assert metrics.passed_signals == 0
        assert metrics.rejected_signals == 0
        assert metrics.reduced_signals == 0
        assert metrics.halted_signals == 0
        assert metrics.reject_rate == 0.0
        assert metrics.intervention_rate == 0.0
    
    def test_metrics_single_record(self, tracker: RiskInterventionTracker):
        """单条记录"""
        tracker.record(
            signal_id="sig-001",
            strategy_id="strategy_A",
            rule_name="depth_check",
            action="PASS",
            original_size=1.0,
            approved_size=1.0,
        )
        
        metrics = tracker.get_metrics()
        
        assert metrics.total_signals == 1
        assert metrics.passed_signals == 1
        assert metrics.reject_rate == 0.0
        assert metrics.intervention_rate == 0.0
    
    def test_metrics_mixed_actions(self, tracker: RiskInterventionTracker):
        """混合动作"""
        tracker.record("sig-001", "strategy_A", "rule1", "PASS", 1.0, 1.0)
        tracker.record("sig-002", "strategy_A", "rule2", "PASS", 1.0, 1.0)
        tracker.record("sig-003", "strategy_A", "rule3", "REDUCE", 1.0, 0.5)
        tracker.record("sig-004", "strategy_A", "rule4", "REJECT", 1.0, 0.0)
        tracker.record("sig-005", "strategy_A", "rule5", "HALT", 1.0, 0.0)
        
        metrics = tracker.get_metrics()
        
        assert metrics.total_signals == 5
        assert metrics.passed_signals == 2
        assert metrics.reduced_signals == 1
        assert metrics.rejected_signals == 1
        assert metrics.halted_signals == 1
        assert metrics.reject_rate == pytest.approx(0.2)
        assert metrics.size_reduction_rate == pytest.approx(0.2)
        assert metrics.killswitch_block_rate == pytest.approx(0.2)
        assert metrics.intervention_rate == pytest.approx(0.6)
    
    def test_metrics_by_strategy(self, tracker: RiskInterventionTracker):
        """按策略过滤"""
        tracker.record("sig-001", "strategy_A", "rule1", "PASS", 1.0, 1.0)
        tracker.record("sig-002", "strategy_A", "rule2", "REJECT", 1.0, 0.0)
        tracker.record("sig-003", "strategy_B", "rule3", "PASS", 1.0, 1.0)
        
        metrics_a = tracker.get_metrics(strategy_id="strategy_A")
        metrics_b = tracker.get_metrics(strategy_id="strategy_B")
        
        assert metrics_a.total_signals == 2
        assert metrics_a.rejected_signals == 1
        
        assert metrics_b.total_signals == 1
        assert metrics_b.rejected_signals == 0
    
    def test_metrics_by_rule(self, tracker: RiskInterventionTracker):
        """按规则名过滤"""
        tracker.record("sig-001", "strategy_A", "depth_check", "PASS", 1.0, 1.0)
        tracker.record("sig-002", "strategy_A", "depth_check", "REJECT", 1.0, 0.0)
        tracker.record("sig-003", "strategy_A", "time_window", "PASS", 1.0, 1.0)
        
        metrics = tracker.get_metrics(rule_name="depth_check")
        
        assert metrics.total_signals == 2
        assert metrics.rejected_signals == 1
    
    def test_metrics_lookback_hours(self, tracker: RiskInterventionTracker):
        """按时间回溯"""
        now = datetime.now(timezone.utc)
        
        # 3 小时前
        tracker.record("sig-001", "strategy_A", "rule1", "PASS", 1.0, 1.0)
        
        # 使用内部方法直接添加旧记录
        old_record = RiskInterventionRecord(
            signal_id="sig-old",
            strategy_id="strategy_A",
            rule_name="rule_old",
            action="REJECT",
            original_size=1.0,
            approved_size=0.0,
            timestamp=now - timedelta(hours=5),
        )
        tracker._records.append(old_record)
        
        metrics_2h = tracker.get_metrics(lookback_hours=2)
        metrics_4h = tracker.get_metrics(lookback_hours=4)
        metrics_all = tracker.get_metrics()
        
        assert metrics_2h.total_signals == 1  # 只有最近的
        assert metrics_4h.total_signals == 1  # 只有最近的
        assert metrics_all.total_signals == 2  # 全部


# ==================== 追踪器查询测试 ====================

class TestTrackerQuery:
    """测试追踪器查询功能"""
    
    def test_get_records_all(self, tracker: RiskInterventionTracker):
        """查询所有记录"""
        tracker.record("sig-001", "strategy_A", "rule1", "PASS", 1.0, 1.0)
        tracker.record("sig-002", "strategy_A", "rule2", "REJECT", 1.0, 0.0)
        
        records = tracker.get_records()
        
        assert len(records) == 2
    
    def test_get_records_by_strategy(self, tracker: RiskInterventionTracker):
        """按策略查询"""
        tracker.record("sig-001", "strategy_A", "rule1", "PASS", 1.0, 1.0)
        tracker.record("sig-002", "strategy_A", "rule2", "REJECT", 1.0, 0.0)
        tracker.record("sig-003", "strategy_B", "rule3", "PASS", 1.0, 1.0)
        
        records = tracker.get_records(strategy_id="strategy_A")
        
        assert len(records) == 2
        assert all(r.strategy_id == "strategy_A" for r in records)
    
    def test_get_records_by_action(self, tracker: RiskInterventionTracker):
        """按动作查询"""
        tracker.record("sig-001", "strategy_A", "rule1", "PASS", 1.0, 1.0)
        tracker.record("sig-002", "strategy_A", "rule2", "REJECT", 1.0, 0.0)
        tracker.record("sig-003", "strategy_A", "rule3", "REJECT", 1.0, 0.0)
        
        records = tracker.get_records(action="REJECT")
        
        assert len(records) == 2
        assert all(r.action == "REJECT" for r in records)
    
    def test_get_records_sorted_by_time_desc(self, tracker: RiskInterventionTracker):
        """记录按时间倒序"""
        tracker.record("sig-001", "strategy_A", "rule1", "PASS", 1.0, 1.0)
        tracker.record("sig-002", "strategy_A", "rule2", "PASS", 1.0, 1.0)
        tracker.record("sig-003", "strategy_A", "rule3", "PASS", 1.0, 1.0)
        
        records = tracker.get_records()
        
        # 最新的在前
        for i in range(len(records) - 1):
            assert records[i].timestamp >= records[i + 1].timestamp
    
    def test_get_records_limit(self, tracker: RiskInterventionTracker):
        """限制返回数量"""
        for i in range(10):
            tracker.record(f"sig-{i:03d}", "strategy_A", "rule1", "PASS", 1.0, 1.0)
        
        records = tracker.get_records(limit=5)
        
        assert len(records) == 5
    
    def test_get_records_offset(self, tracker: RiskInterventionTracker):
        """分页偏移"""
        for i in range(10):
            tracker.record(f"sig-{i:03d}", "strategy_A", "rule1", "PASS", 1.0, 1.0)
        
        all_records = tracker.get_records()
        paged_records = tracker.get_records(offset=5, limit=5)
        
        assert len(paged_records) == 5
        assert paged_records[0].signal_id == all_records[5].signal_id


# ==================== 辅助函数测试 ====================

class TestHelperFunctions:
    """测试辅助函数"""
    
    def test_calculate_expectancy_positive(self):
        """正期望"""
        # 胜率 50%, avg_win 100, avg_loss 50, avg_cost 10
        # expectancy = 100 * 0.5 - 50 * 0.5 - 10 = 50 - 25 - 10 = 15
        result = calculate_expectancy(
            win_rate=0.5,
            avg_win=100.0,
            loss_rate=0.5,
            avg_loss=50.0,
            avg_cost=10.0,
        )
        
        assert result == 15.0
    
    def test_calculate_expectancy_negative(self):
        """负期望"""
        # 胜率 30%, avg_win 50, avg_loss 100, avg_cost 20
        # expectancy = 50 * 0.3 - 100 * 0.7 - 20 = 15 - 70 - 20 = -75
        result = calculate_expectancy(
            win_rate=0.3,
            avg_win=50.0,
            loss_rate=0.7,
            avg_loss=100.0,
            avg_cost=20.0,
        )
        
        assert result == -75.0
    
    def test_calculate_expectancy_zero(self):
        """零期望"""
        result = calculate_expectancy(
            win_rate=0.5,
            avg_win=100.0,
            loss_rate=0.5,
            avg_loss=100.0,
            avg_cost=0.0,
        )
        
        assert result == 0.0
    
    def test_calculate_sharpe_decay_no_decay(self):
        """无衰减"""
        result = calculate_sharpe_decay(
            in_sample_sharpe=1.0,
            out_of_sample_sharpe=1.0,
        )
        
        assert result == 1.0
    
    def test_calculate_sharpe_decay_full_decay(self):
        """完全衰减"""
        result = calculate_sharpe_decay(
            in_sample_sharpe=1.0,
            out_of_sample_sharpe=0.0,
        )
        
        assert result == 0.0
    
    def test_calculate_sharpe_decay_partial_decay(self):
        """部分衰减"""
        result = calculate_sharpe_decay(
            in_sample_sharpe=2.0,
            out_of_sample_sharpe=1.0,
        )
        
        assert result == 0.5
    
    def test_calculate_sharpe_decay_zero_in_sample(self):
        """样本内为零时返回0"""
        result = calculate_sharpe_decay(
            in_sample_sharpe=0.0,
            out_of_sample_sharpe=1.0,
        )
        
        assert result == 0.0
    
    def test_calculate_sharpe_decay_negative_out(self):
        """样本外为负时返回0"""
        result = calculate_sharpe_decay(
            in_sample_sharpe=1.0,
            out_of_sample_sharpe=-0.5,
        )
        
        assert result == 0.0


# ==================== 序列化测试 ====================

class TestSerialization:
    """测试序列化"""
    
    def test_record_to_dict(self):
        """记录转字典"""
        record = RiskInterventionRecord(
            signal_id="sig-001",
            strategy_id="strategy_A",
            rule_name="depth_check",
            action="PASS",
            original_size=1.0,
            approved_size=1.0,
            market_state_ref="hash123",
            trace_id="trace-001",
        )
        
        d = record.to_dict()
        
        assert d["signal_id"] == "sig-001"
        assert d["strategy_id"] == "strategy_A"
        assert d["rule_name"] == "depth_check"
        assert d["action"] == "PASS"
        assert d["original_size"] == 1.0
        assert d["approved_size"] == 1.0
        assert d["market_state_ref"] == "hash123"
        assert d["trace_id"] == "trace-001"
        assert "timestamp" in d
    
    def test_metrics_to_dict(self):
        """指标转字典"""
        metrics = RiskInterventionMetrics(
            total_signals=10,
            passed_signals=5,
            rejected_signals=3,
            reduced_signals=1,
            halted_signals=1,
            reject_rate=0.3,
            size_reduction_rate=0.1,
            killswitch_block_rate=0.1,
            intervention_rate=0.5,
        )
        
        d = metrics.to_dict()
        
        assert d["total_signals"] == 10
        assert d["passed_signals"] == 5
        assert d["reject_rate"] == 0.3
        assert d["intervention_rate"] == 0.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
