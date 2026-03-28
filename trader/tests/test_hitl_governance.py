"""
HITL Governance 单元测试
========================

测试范围：
1. 状态机转换（pending -> approved/rejected/modified）
2. 边界输入（空值、极端值）
3. 错误路径（不存在建议、无效决策）
4. 核心功能（建议生成、审批队列、审计日志）
"""
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import Mock, AsyncMock

from trader.core.application.hitl_governance import (
    HITLGovernance,
    HITLDecision,
    AISuggestion,
    HITLApprovalRecord,
    HITLProviderPort,
    HITL_TIMEOUT_SECONDS,
    SuggestionNotFoundError,
    InvalidDecisionError,
    SuggestionExpiredError,
)
from trader.core.application.risk_engine import RiskCheckResult, RiskLevel, RejectionReason
from trader.core.domain.models.signal import Signal, SignalType


# ==================== Fixtures ====================

@pytest.fixture
def governance():
    """创建HITLGovernance实例"""
    return HITLGovernance(timeout_seconds=300)


@pytest.fixture
def mock_provider():
    """创建Mock存储端口"""
    provider = Mock(spec=HITLProviderPort)
    provider.save_approval_record = AsyncMock()
    provider.get_approval_record = AsyncMock()
    provider.get_approval_records_by_suggestion = AsyncMock(return_value=[])
    provider.get_pending_approvals = AsyncMock(return_value=[])
    provider.get_approval_history = AsyncMock(return_value=[])
    return provider


@pytest.fixture
def governance_with_provider(mock_provider):
    """创建带存储端口的HITLGovernance实例"""
    return HITLGovernance(provider=mock_provider, timeout_seconds=300)


@pytest.fixture
def sample_signal():
    """创建示例交易信号"""
    return Signal(
        signal_id="test-signal-001",
        strategy_name="test_strategy",
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        price=Decimal("50000"),
        quantity=Decimal("0.1"),
        confidence=Decimal("0.9"),
        stop_loss=Decimal("49000"),
        take_profit=Decimal("55000"),
        reason="Test signal",
    )


@pytest.fixture
def sample_signal_large_trade():
    """创建大额交易信号"""
    return Signal(
        signal_id="test-signal-002",
        strategy_name="test_strategy",
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        price=Decimal("50000"),
        quantity=Decimal("1.0"),  # 价值 $50000，超过阈值
        confidence=Decimal("0.9"),
    )


@pytest.fixture
def risk_result_passed():
    """创建通过的风控结果"""
    return RiskCheckResult(
        passed=True,
        risk_level=RiskLevel.LOW,
        message="风控检查通过",
    )


@pytest.fixture
def risk_result_high_risk():
    """创建高风险风控结果"""
    return RiskCheckResult(
        passed=False,
        risk_level=RiskLevel.HIGH,
        rejection_reason=RejectionReason.MAX_POSITIONS,
        message="超过最大持仓数",
    )


@pytest.fixture
def risk_result_critical():
    """创建临界风险风控结果"""
    return RiskCheckResult(
        passed=False,
        risk_level=RiskLevel.CRITICAL,
        rejection_reason=RejectionReason.DAILY_LOSS_LIMIT,
        message="超过日损失限制",
    )


# ==================== 建议生成测试 ====================

class TestSuggestionGeneration:
    """测试建议生成"""

    def test_generate_suggestion_passed_risk(self, governance, sample_signal, risk_result_passed):
        """测试风控通过时生成建议"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_passed)

        assert suggestion.suggestion_id is not None
        assert suggestion.signal == sample_signal
        assert suggestion.risk_check_result == risk_result_passed
        assert suggestion.recommended_action == "BUY"
        assert suggestion.confidence == 0.9
        assert suggestion.requires_human_review is False

    def test_generate_suggestion_high_risk_requires_review(
        self, governance, sample_signal, risk_result_high_risk
    ):
        """测试高风险需要人工审核"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_high_risk)

        assert suggestion.requires_human_review is True
        assert suggestion.recommended_action == "HOLD"
        assert suggestion.confidence == 0.0

    def test_generate_suggestion_critical_risk(
        self, governance, sample_signal, risk_result_critical
    ):
        """测试临界风险"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)

        assert suggestion.requires_human_review is True
        assert suggestion.risk_check_result.risk_level == RiskLevel.CRITICAL

    def test_generate_suggestion_large_trade(self, governance, sample_signal_large_trade, risk_result_passed):
        """测试大额交易需要审核"""
        suggestion = governance.generate_suggestion(sample_signal_large_trade, risk_result_passed)

        assert suggestion.requires_human_review is True

    def test_generate_suggestion_sell_signal(self, governance, risk_result_passed):
        """测试卖出信号"""
        signal = Signal(
            signal_id="sell-signal",
            strategy_name="test",
            signal_type=SignalType.SELL,
            symbol="BTCUSDT",
            price=Decimal("50000"),
            quantity=Decimal("0.1"),
            confidence=Decimal("0.8"),
        )
        suggestion = governance.generate_suggestion(signal, risk_result_passed)

        assert suggestion.recommended_action == "SELL"

    def test_generate_suggestion_params(self, governance, sample_signal, risk_result_passed):
        """测试建议参数包含完整信息"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_passed)

        assert suggestion.suggested_params["symbol"] == "BTCUSDT"
        assert suggestion.suggested_params["quantity"] == "0.1"
        assert suggestion.suggested_params["stop_loss"] == "49000"
        assert suggestion.suggested_params["take_profit"] == "55000"


# ==================== 审批队列测试 ====================

class TestApprovalQueue:
    """测试审批队列管理"""

    def test_submit_for_approval(self, governance, sample_signal, risk_result_high_risk):
        """测试提交建议到审批队列"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_high_risk)
        record = governance.submit_for_approval(suggestion)

        assert record.suggestion_id == suggestion.suggestion_id
        assert record.decision == HITLDecision.PENDING
        assert record.approver is None
        assert governance.get_pending_suggestions() == [suggestion]

    def test_submit_no_review_not_queued(self, governance, sample_signal, risk_result_passed):
        """测试不需要审核的建议不入队"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_passed)
        record = governance.submit_for_approval(suggestion)

        # 不需要审核的建议不会进入待审批队列
        assert governance.get_pending_suggestions() == []
        # 但审批记录仍然存在
        assert record.decision == HITLDecision.PENDING

    def test_get_pending_suggestions(self, governance, sample_signal, risk_result_critical):
        """测试获取待审批列表"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        pending = governance.get_pending_suggestions()
        assert len(pending) == 1
        assert pending[0].suggestion_id == suggestion.suggestion_id

    def test_get_pending_approvals(self, governance, sample_signal, risk_result_critical):
        """测试获取待审批记录"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        pending = governance.get_pending_approvals()
        assert len(pending) == 1


# ==================== 审批决策测试 ====================

class TestApprovalDecisions:
    """测试审批决策"""

    def test_approve(self, governance, sample_signal, risk_result_critical):
        """测试批准建议"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        record = governance.approve(suggestion.suggestion_id, "trader@example.com", "Manual approve")

        assert record.decision == HITLDecision.APPROVED
        assert record.approver == "trader@example.com"
        assert record.reason == "Manual approve"
        assert record.decided_at is not None
        assert governance.get_pending_suggestions() == []

    def test_reject(self, governance, sample_signal, risk_result_critical):
        """测试拒绝建议"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        record = governance.reject(
            suggestion.suggestion_id, "trader@example.com", "Risk too high"
        )

        assert record.decision == HITLDecision.REJECTED
        assert record.approver == "trader@example.com"
        assert record.reason == "Risk too high"

    def test_reject_without_reason_raises(self, governance, sample_signal, risk_result_critical):
        """测试拒绝时不提供理由会抛异常"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        with pytest.raises(InvalidDecisionError):
            governance.reject(suggestion.suggestion_id, "trader@example.com", "")

    def test_modify_and_approve(self, governance, sample_signal, risk_result_critical):
        """测试修改参数后批准"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        new_params = {"quantity": "0.05", "stop_loss": "49500"}
        record = governance.modify_and_approve(
            suggestion.suggestion_id, "trader@example.com", new_params, "Reduced position size"
        )

        assert record.decision == HITLDecision.MODIFIED
        assert record.modified_params == new_params
        assert record.reason == "Reduced position size"

    def test_modify_without_params_raises(self, governance, sample_signal, risk_result_critical):
        """测试修改审批时不提供参数会抛异常"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        with pytest.raises(InvalidDecisionError):
            governance.modify_and_approve(suggestion.suggestion_id, "trader@example.com", {}, None)

    def test_approve_nonexistent_suggestion(self, governance):
        """测试批准不存在的建议"""
        with pytest.raises(SuggestionNotFoundError):
            governance.approve("nonexistent-id", "trader@example.com", "approve")

    def test_reject_nonexistent_suggestion(self, governance):
        """测试拒绝不存在的建议"""
        with pytest.raises(SuggestionNotFoundError):
            governance.reject("nonexistent-id", "trader@example.com", "reason")

    def test_decision_twice_raises(self, governance, sample_signal, risk_result_critical):
        """测试重复审批会抛异常"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        governance.approve(suggestion.suggestion_id, "trader@example.com", "approved")

        # 再次批准会抛出已审批异常
        with pytest.raises(SuggestionNotFoundError):
            governance.approve(suggestion.suggestion_id, "trader@example.com", "approved again")


# ==================== 审计日志测试 ====================

class TestAuditLog:
    """测试审计日志"""

    def test_get_approval_history(self, governance, sample_signal, risk_result_critical):
        """测试获取审批历史"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        governance.approve(suggestion.suggestion_id, "trader@example.com", "approved")

        history = governance.get_approval_history()
        assert len(history) == 1
        assert history[0].decision == HITLDecision.APPROVED

    def test_get_approval_history_pagination(self, governance):
        """测试审批历史分页"""
        # 创建多个建议和审批
        for i in range(5):
            signal = Signal(
                signal_id=f"signal-{i}",
                strategy_name="test",
                signal_type=SignalType.BUY,
                symbol="BTCUSDT",
                price=Decimal("50000"),
                quantity=Decimal("0.1"),
            )
            risk = RiskCheckResult(passed=True, risk_level=RiskLevel.LOW)
            suggestion = governance.generate_suggestion(signal, risk)
            governance.submit_for_approval(suggestion)
            governance.approve(suggestion.suggestion_id, "trader@example.com", f"approved {i}")

        history = governance.get_approval_history(limit=2, offset=0)
        assert len(history) == 2

        history_page2 = governance.get_approval_history(limit=2, offset=2)
        assert len(history_page2) == 2

    def test_get_approval_stats(self, governance, sample_signal, risk_result_critical):
        """测试审批统计"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        governance.approve(suggestion.suggestion_id, "trader@example.com", "approved")

        stats = governance.get_approval_stats()
        assert stats["total"] == 1
        assert stats["approved"] == 1
        assert stats["pending"] == 0

    def test_get_record_by_suggestion(self, governance, sample_signal, risk_result_critical):
        """测试获取某个建议的所有审批记录"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        governance.approve(suggestion.suggestion_id, "trader@example.com", "approved")

        records = governance.get_record_by_suggestion(suggestion.suggestion_id)
        assert len(records) == 1


# ==================== 边界情况测试 ====================

class TestBoundaryCases:
    """测试边界情况"""

    def test_timeout_calculation(self, governance, sample_signal, risk_result_critical):
        """测试超时判断"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        # 当前时间未超时
        current_time = datetime.now(timezone.utc)
        assert not governance._approval_records[list(governance._approval_records.keys())[0]].is_expired(current_time)

        # 未来时间已超时
        future_time = current_time + timedelta(seconds=HITL_TIMEOUT_SECONDS + 1)
        assert governance._approval_records[list(governance._approval_records.keys())[0]].is_expired(future_time)

    def test_cleanup_expired(self, governance, sample_signal, risk_result_critical):
        """测试清理超时建议"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        # 模拟超时
        future_time = datetime.now(timezone.utc) + timedelta(seconds=HITL_TIMEOUT_SECONDS + 1)
        expired_ids = governance.cleanup_expired_suggestions(future_time)

        assert len(expired_ids) == 1
        assert governance.get_pending_suggestions() == []

    def test_decision_state_checks(self):
        """测试决策状态判断方法"""
        record = HITLApprovalRecord(
            record_id="test",
            suggestion_id="test",
            decision=HITLDecision.PENDING,
            approver=None,
            reason=None,
            modified_params=None,
            created_at=datetime.now(timezone.utc),
            decided_at=None,
        )

        assert record.is_pending() is True
        assert record.is_approved() is False
        assert record.is_rejected() is False
        assert record.is_modified() is False

        record.decision = HITLDecision.APPROVED
        assert record.is_pending() is False
        assert record.is_approved() is True

    def test_large_trade_threshold(self, governance_with_provider):
        """测试大额交易阈值设置"""
        assert governance_with_provider._large_trade_threshold == Decimal("10000")

        # 创建小额定交易
        small_signal = Signal(
            signal_id="small",
            strategy_name="test",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("50000"),
            quantity=Decimal("0.01"),  # $500
            confidence=Decimal("0.9"),
        )
        risk = RiskCheckResult(passed=True, risk_level=RiskLevel.LOW)
        suggestion = governance_with_provider.generate_suggestion(small_signal, risk)

        assert suggestion.requires_human_review is False

    def test_zero_quantity_signal(self, governance):
        """测试零数量信号"""
        signal = Signal(
            signal_id="zero-qty",
            strategy_name="test",
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=Decimal("50000"),
            quantity=Decimal("0"),
            confidence=Decimal("0.5"),
        )
        risk = RiskCheckResult(passed=True, risk_level=RiskLevel.LOW)
        suggestion = governance.generate_suggestion(signal, risk)

        # 零数量交易不会被认为是大额交易
        assert suggestion.requires_human_review is False

    def test_missing_signal_fields(self, governance):
        """测试信号字段缺失"""
        signal = Signal(
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
        )
        risk = RiskCheckResult(passed=True, risk_level=RiskLevel.LOW)
        suggestion = governance.generate_suggestion(signal, risk)

        assert suggestion.suggestion_id is not None
        assert suggestion.signal == signal


# ==================== 辅助方法测试 ====================

class TestHelperMethods:
    """测试辅助方法"""

    def test_is_high_value_trade(self, governance, sample_signal_large_trade, sample_signal):
        """测试大额交易判断"""
        assert governance.is_high_value_trade(sample_signal_large_trade) is True
        assert governance.is_high_value_trade(sample_signal) is False

    def test_needs_human_review_for_risk_level(self, governance):
        """测试风险等级是否需要审核"""
        assert governance.needs_human_review_for_risk_level(RiskLevel.LOW) is False
        assert governance.needs_human_review_for_risk_level(RiskLevel.MEDIUM) is False
        assert governance.needs_human_review_for_risk_level(RiskLevel.HIGH) is True
        assert governance.needs_human_review_for_risk_level(RiskLevel.CRITICAL) is True

    def test_get_suggestion(self, governance, sample_signal, risk_result_critical):
        """测试获取建议"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        retrieved = governance.get_suggestion(suggestion.suggestion_id)
        assert retrieved == suggestion

        not_found = governance.get_suggestion("nonexistent")
        assert not_found is None

    def test_get_record(self, governance, sample_signal, risk_result_critical):
        """测试获取审批记录"""
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        record = governance.submit_for_approval(suggestion)

        retrieved = governance.get_record(record.record_id)
        assert retrieved == record

        not_found = governance.get_record("nonexistent")
        assert not_found is None


# ==================== 集成场景测试 ====================

class TestIntegrationScenarios:
    """测试集成场景"""

    def test_full_approval_workflow(self, governance, sample_signal, risk_result_critical):
        """测试完整审批工作流"""
        # 1. 生成建议
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        assert suggestion.requires_human_review is True

        # 2. 提交审批
        record = governance.submit_for_approval(suggestion)
        assert record.decision == HITLDecision.PENDING
        assert len(governance.get_pending_suggestions()) == 1

        # 3. 批准
        approved_record = governance.approve(
            suggestion.suggestion_id, "senior_trader@example.com", "Manual override approved"
        )
        assert approved_record.is_approved() is True

        # 4. 验证最终状态
        assert len(governance.get_pending_suggestions()) == 0
        history = governance.get_approval_history()
        assert len(history) == 1
        assert history[0].decision == HITLDecision.APPROVED
        assert history[0].approver == "senior_trader@example.com"

    def test_modification_workflow(self, governance, sample_signal, risk_result_critical):
        """测试修改参数工作流"""
        # 1. 生成建议
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        # 2. 修改并批准
        new_params = {
            "quantity": "0.05",  # 减少数量
            "stop_loss": "49500",  # 调整止损
        }
        record = governance.modify_and_approve(
            suggestion.suggestion_id, "risk_manager@example.com", new_params, "Reduced risk"
        )

        assert record.is_modified() is True
        assert record.modified_params == new_params
        assert record.approver == "risk_manager@example.com"

        # 3. 验证审计日志
        history = governance.get_approval_history()
        assert history[0].modified_params == new_params

    def test_rejection_workflow(self, governance, sample_signal, risk_result_critical):
        """测试拒绝工作流"""
        # 1. 生成建议
        suggestion = governance.generate_suggestion(sample_signal, risk_result_critical)
        governance.submit_for_approval(suggestion)

        # 2. 拒绝
        record = governance.reject(
            suggestion.suggestion_id, "risk_manager@example.com", "Exceeds risk appetite"
        )

        assert record.is_rejected() is True
        assert record.reason == "Exceeds risk appetite"

        # 3. 验证统计
        stats = governance.get_approval_stats()
        assert stats["rejected"] == 1
        assert stats["approved"] == 0


# ==================== 端口协议测试 ====================

class TestHITLProviderPort:
    """测试HITLProviderPort接口"""

    def test_port_is_abstract(self):
        """测试端口是抽象类"""
        with pytest.raises(TypeError):
            HITLProviderPort()

    def test_port_methods_are_abstract(self):
        """测试端口方法是抽象的"""
        class ConcreteProvider(HITLProviderPort):
            async def save_approval_record(self, record):
                pass

            async def get_approval_record(self, record_id):
                pass

            async def get_approval_records_by_suggestion(self, suggestion_id):
                pass

            async def get_pending_approvals(self):
                pass

            async def get_approval_history(self, limit=100, offset=0):
                pass

        provider = ConcreteProvider()
        assert isinstance(provider, HITLProviderPort)
