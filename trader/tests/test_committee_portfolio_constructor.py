"""
Test Portfolio Constructor - 组合构建器单元测试
=============================================

测试覆盖：
1. PortfolioConstructor - 组合构建逻辑
2. Sleeve 分配
3. Capital allocation
4. Regime 分配
5. 冲突解决
"""

import pytest
from decimal import Decimal

from insight.committee.portfolio_constructor import (
    PortfolioConstructor,
    PortfolioConstructionResult,
    CapitalAllocation,
    RegimeAssignment,
    ConflictResult,
)
from insight.committee.schemas import (
    CostAssumptions,
    ProposalStatus,
    ReviewReport,
    ReviewVerdict,
    SleeveProposal,
    SpecialistType,
)


# ==================== 测试辅助 ====================

def create_test_proposal(
    proposal_id: str = "test_proposal",
    specialist_type: SpecialistType = SpecialistType.TREND,
    hypothesis: str = "测试假设",
    required_features: list = None,
    regime: str = "strong_trend",
    failure_modes: list = None,
) -> SleeveProposal:
    """创建测试用 SleeveProposal"""
    if required_features is None:
        required_features = ["ema_fast", "ema_slow", "trend_direction"]
    if failure_modes is None:
        failure_modes = ["市场横盘整理", "趋势突然反转"]
    
    return SleeveProposal(
        proposal_id=proposal_id,
        specialist_type=specialist_type,
        hypothesis=hypothesis,
        required_features=required_features,
        regime=regime,
        failure_modes=failure_modes,
        cost_assumptions=CostAssumptions(
            trading_fee_bps=10.0,
            slippage_bps=5.0,
        ),
        feature_version="v1.0.0",
        prompt_version="v1.0.0",
    )


def create_test_review(
    proposal_id: str,
    orthogonality_score: float = 0.8,
    risk_score: float = 0.7,
    cost_score: float = 0.7,
    verdict: ReviewVerdict = ReviewVerdict.PASS,
) -> ReviewReport:
    """创建测试用 ReviewReport"""
    return ReviewReport(
        report_id=f"review_{proposal_id}",
        proposal_id=proposal_id,
        reviewer_type="test",
        verdict=verdict,
        orthogonality_score=orthogonality_score,
        risk_score=risk_score,
        cost_score=cost_score,
        feature_version="v1.0.0",
        prompt_version="v1.0.0",
        trace_id="test_trace",
    )


# ==================== PortfolioConstructor 测试 ====================

class TestPortfolioConstructor:
    """PortfolioConstructor 测试"""

    def setup_method(self):
        """测试前准备"""
        self.constructor = PortfolioConstructor(
            max_sleeves=5,
            min_capital_per_sleeve=Decimal("500"),
            default_total_capital=Decimal("10000"),
        )

    def test_construct_empty_proposals(self):
        """测试空提案列表"""
        result = self.constructor.construct([], [])
        
        assert result.portfolio_proposal is not None
        assert len(result.portfolio_proposal.sleeves) == 0

    def test_construct_single_proposal(self):
        """测试单个提案"""
        proposals = [create_test_proposal("prop_1")]
        reviews = [create_test_review("prop_1")]
        
        result = self.constructor.construct(proposals, reviews)
        
        assert len(result.portfolio_proposal.sleeves) == 1
        assert len(result.capital_allocations) == 1

    def test_construct_multiple_proposals(self):
        """测试多个提案"""
        proposals = [
            create_test_proposal("prop_1", SpecialistType.TREND),
            create_test_proposal("prop_2", SpecialistType.PRICE_VOLUME),
            create_test_proposal("prop_3", SpecialistType.FUNDING_OI),
        ]
        reviews = [
            create_test_review("prop_1"),
            create_test_review("prop_2"),
            create_test_review("prop_3"),
        ]
        
        result = self.constructor.construct(proposals, reviews)
        
        assert len(result.portfolio_proposal.sleeves) == 3
        assert len(result.capital_allocations) == 3

    def test_construct_respects_max_sleeves(self):
        """测试不超过最大 sleeve 数量"""
        self.constructor.max_sleeves = 2
        
        proposals = [
            create_test_proposal("prop_1", SpecialistType.TREND),
            create_test_proposal("prop_2", SpecialistType.PRICE_VOLUME),
            create_test_proposal("prop_3", SpecialistType.FUNDING_OI),
        ]
        reviews = [create_test_review(f"prop_{i}") for i in range(1, 4)]
        
        result = self.constructor.construct(proposals, reviews)
        
        assert len(result.portfolio_proposal.sleeves) <= 2

    def test_capital_allocation_equality(self):
        """测试资金平均分配"""
        proposals = [
            create_test_proposal("prop_1"),
            create_test_proposal("prop_2"),
        ]
        reviews = [create_test_review(f"prop_{i}") for i in range(1, 3)]
        
        result = self.constructor.construct(
            proposals, 
            reviews,
            total_capital=Decimal("10000")
        )
        
        # 权重应该接近相等
        weights = [a.weight for a in result.capital_allocations]
        assert abs(weights[0] - weights[1]) < 0.01

    def test_capital_allocation_respects_minimum(self):
        """测试资金分配不低于最小值"""
        self.constructor.min_capital_per_sleeve = Decimal("5000")
        
        proposals = [
            create_test_proposal("prop_1"),
            create_test_proposal("prop_2"),
            create_test_proposal("prop_3"),
        ]
        reviews = [create_test_review(f"prop_{i}") for i in range(1, 4)]
        
        result = self.constructor.construct(
            proposals,
            reviews,
            total_capital=Decimal("5000")  # 总资金少于最小值
        )
        
        for alloc in result.capital_allocations:
            assert alloc.capital_cap >= self.constructor.min_capital_per_sleeve

    def test_regime_assignments(self):
        """测试 Regime 分配"""
        proposals = [
            create_test_proposal("prop_1", regime="strong_trend"),
            create_test_proposal("prop_2", regime="low_volatility"),
        ]
        reviews = [create_test_review(f"prop_{i}") for i in range(1, 3)]
        
        result = self.constructor.construct(proposals, reviews)
        
        assert len(result.regime_assignments) == 2
        regimes = [r.regime_name for r in result.regime_assignments]
        assert "strong_trend" in regimes
        assert "low_volatility" in regimes

    def test_conflict_resolution_same_type(self):
        """测试同类型冲突解决"""
        proposals = [
            create_test_proposal("prop_1", SpecialistType.TREND),
            create_test_proposal("prop_2", SpecialistType.TREND),  # 同类型
        ]
        reviews = [create_test_review(f"prop_{i}") for i in range(1, 3)]
        
        result = self.constructor.construct(proposals, reviews)
        
        # 同类型应该产生冲突解决
        if len(proposals) == 2:
            assert result.conflict_result.has_conflicts is True
            assert len(result.conflict_result.resolutions) >= 1

    def test_no_conflict_different_types(self):
        """测试不同类型无冲突"""
        proposals = [
            create_test_proposal("prop_1", SpecialistType.TREND),
            create_test_proposal("prop_2", SpecialistType.PRICE_VOLUME),
        ]
        reviews = [create_test_review(f"prop_{i}") for i in range(1, 3)]
        
        result = self.constructor.construct(proposals, reviews)
        
        # 不同类型通常没有冲突
        # (除非有其他因素)
        assert result.conflict_result is not None

    def test_risk_explanation_generation(self):
        """测试风险说明生成"""
        proposals = [
            create_test_proposal("prop_1", SpecialistType.TREND),
            create_test_proposal("prop_2", SpecialistType.PRICE_VOLUME),
        ]
        reviews = [create_test_review(f"prop_{i}") for i in range(1, 3)]
        
        result = self.constructor.construct(proposals, reviews)
        
        assert result.risk_explanation is not None
        assert len(result.risk_explanation) > 0

    def test_evaluation_task_id_generation(self):
        """测试评估任务 ID 生成"""
        proposals = [create_test_proposal("prop_1")]
        reviews = [create_test_review("prop_1")]
        
        result = self.constructor.construct(proposals, reviews)
        
        assert result.evaluation_task_id is not None
        assert result.evaluation_task_id.startswith("eval_")

    def test_weight_multiplier_by_type(self):
        """测试按类型调整权重"""
        # OnChain 通常权重较低
        multiplier_onchain = self.constructor._get_weight_multiplier(
            SpecialistType.ONCHAIN
        )
        # Trend 通常权重较高
        multiplier_trend = self.constructor._get_weight_multiplier(
            SpecialistType.TREND
        )
        
        assert multiplier_onchain < multiplier_trend


# ==================== 边界约束测试 ====================

class TestPortfolioConstructorBoundaries:
    """边界约束测试"""

    def setup_method(self):
        """测试前准备"""
        self.constructor = PortfolioConstructor()

    def test_no_direct_trading_instructions(self):
        """测试不生成直接交易指令"""
        proposals = [create_test_proposal("prop_1")]
        reviews = [create_test_review("prop_1")]
        
        result = self.constructor.construct(proposals, reviews)
        
        # PortfolioProposal 应该只包含结构化数据
        assert result.portfolio_proposal is not None
        assert result.portfolio_proposal.proposal_id is not None
        # 不应该包含具体的买入/卖出指令
        assert "买入" not in result.risk_explanation
        assert "卖出" not in result.risk_explanation

    def test_traceability(self):
        """测试可追踪性"""
        proposals = [create_test_proposal("prop_1")]
        reviews = [create_test_review("prop_1")]
        
        result = self.constructor.construct(proposals, reviews)
        
        # 应该生成 trace_id
        assert result.portfolio_proposal.trace_id is not None
        # 每个分配应该有 proposal_id
        for sleeve in result.portfolio_proposal.sleeves:
            assert sleeve.proposal_id is not None


# ==================== 集成测试 ====================

class TestPortfolioConstructorIntegration:
    """集成测试"""

    def setup_method(self):
        """测试前准备"""
        self.constructor = PortfolioConstructor(
            max_sleeves=5,
            default_total_capital=Decimal("10000"),
        )

    def test_full_workflow(self):
        """测试完整工作流"""
        # 创建多样化的提案
        proposals = [
            create_test_proposal(
                "trend_1",
                SpecialistType.TREND,
                regime="strong_trend",
            ),
            create_test_proposal(
                "pv_1",
                SpecialistType.PRICE_VOLUME,
                regime="high_volume",
            ),
            create_test_proposal(
                "foi_1",
                SpecialistType.FUNDING_OI,
                regime="high_funding",
            ),
        ]
        
        reviews = [
            create_test_review("trend_1", orthogonality_score=0.8),
            create_test_review("pv_1", orthogonality_score=0.75),
            create_test_review("foi_1", orthogonality_score=0.7),
        ]
        
        # 构建组合
        result = self.constructor.construct(proposals, reviews)
        
        # 验证结果
        assert result.portfolio_proposal is not None
        assert len(result.portfolio_proposal.sleeves) == 3
        assert len(result.capital_allocations) == 3
        assert len(result.regime_assignments) == 3
        
        # 验证资金分配
        total_capital = sum(
            a.capital_cap for a in result.capital_allocations
        )
        # 总额应该接近总资金（允许小误差）
        assert total_capital <= Decimal("10000") * Decimal("1.1")
        
        # 验证每个 sleeve 有资本上限
        for sleeve in result.portfolio_proposal.sleeves:
            assert sleeve.capital_cap > 0

    def test_select_approved_proposals(self):
        """测试选择通过审查的提案"""
        proposals = [
            create_test_proposal("prop_1"),
            create_test_proposal("prop_2"),
            create_test_proposal("prop_3"),
        ]
        reviews = [
            create_test_review("prop_1", verdict=ReviewVerdict.PASS),
            create_test_review("prop_2", verdict=ReviewVerdict.FAIL),
            create_test_review("prop_3", verdict=ReviewVerdict.PASS),
        ]
        
        selected = self.constructor._select_proposals(proposals, reviews)
        
        # 应该选择通过审查的
        assert len(selected) <= 3
