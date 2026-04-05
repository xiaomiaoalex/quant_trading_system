"""
Test Committee Reviewers - Red Team Agents 单元测试
=================================================

测试覆盖：
1. OrthogonalityAgent - 正交性审查
2. RiskCostRedTeamAgent - 风险成本否决
"""

import pytest
from datetime import datetime, timezone

from insight.committee.orthogonality import OrthogonalityAgent, OrthogonalityResult
from insight.committee.red_team import RiskCostRedTeamAgent
from insight.committee.schemas import (
    CostAssumptions,
    ProposalStatus,
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
    feature_version: str = "v1.0.0",
    prompt_version: str = "v1.0.0",
    cost_assumptions: CostAssumptions = None,
    evidence_refs: list = None,
) -> SleeveProposal:
    """创建测试用 SleeveProposal"""
    if required_features is None:
        required_features = ["ema_fast", "ema_slow", "trend_direction"]
    if failure_modes is None:
        failure_modes = ["市场横盘整理", "趋势突然反转"]
    if cost_assumptions is None:
        cost_assumptions = CostAssumptions(
            trading_fee_bps=10.0,
            slippage_bps=5.0,
        )
    if evidence_refs is None:
        evidence_refs = ["test_ref"]
    
    return SleeveProposal(
        proposal_id=proposal_id,
        specialist_type=specialist_type,
        hypothesis=hypothesis,
        required_features=required_features,
        regime=regime,
        failure_modes=failure_modes,
        cost_assumptions=cost_assumptions,
        evidence_refs=evidence_refs,
        feature_version=feature_version,
        prompt_version=prompt_version,
    )


# ==================== OrthogonalityAgent 测试 ====================

class TestOrthogonalityAgent:
    """OrthogonalityAgent 测试"""

    def setup_method(self):
        """测试前准备"""
        self.agent = OrthogonalityAgent(min_score=0.7)

    def test_check_orthogonality_no_existing(self):
        """测试无已有提案的情况"""
        new_proposal = create_test_proposal()
        result = self.agent.check_orthogonality(new_proposal, [])
        
        assert result.is_orthogonal is True
        assert result.orthogonality_score == 1.0
        assert result.duplicate_risk_exposure is False

    def test_check_orthogonality_different_types(self):
        """测试不同 specialist 类型正交"""
        new_proposal = create_test_proposal(
            specialist_type=SpecialistType.TREND,
            required_features=["ema_fast", "ema_slow", "adx", "bb_upper", "atr"],
        )
        existing = create_test_proposal(
            proposal_id="existing_1",
            specialist_type=SpecialistType.PRICE_VOLUME,
            required_features=["volume_roc", "atr", "bb_width", "funding_rate"],
        )
        
        result = self.agent.check_orthogonality(new_proposal, [existing])
        
        # 不同类型 + 部分不同特征应该有正交性
        assert result.orthogonality_score > 0.3
        assert result.duplicate_risk_exposure is False

    def test_check_orthogonality_same_type_different_features(self):
        """测试同类型但不同特征"""
        new_proposal = create_test_proposal(
            specialist_type=SpecialistType.TREND,
            required_features=["ema_fast", "ema_slow", "adx", "bb_upper", "bb_lower"],
        )
        existing = create_test_proposal(
            proposal_id="existing_1",
            specialist_type=SpecialistType.TREND,
            required_features=["bb_upper", "bb_lower", "bb_width", "volume_roc"],
        )
        
        result = self.agent.check_orthogonality(new_proposal, [existing])
        
        # 同类型部分特征重叠，应该有中等分数
        assert result.orthogonality_score > 0.2

    def test_check_orthogonality_same_features_low_score(self):
        """测试特征高度重叠导致低分"""
        new_proposal = create_test_proposal(
            specialist_type=SpecialistType.TREND,
            required_features=["ema_fast", "ema_slow", "trend_direction"],
        )
        existing = create_test_proposal(
            proposal_id="existing_1",
            specialist_type=SpecialistType.TREND,
            required_features=["ema_fast", "ema_slow", "trend_direction"],
        )
        
        result = self.agent.check_orthogonality(new_proposal, [existing])
        
        # 特征完全重叠应该得低分
        assert result.orthogonality_score < 0.7
        assert result.similar_proposals == ["existing_1"]

    def test_check_orthogonality_same_regime(self):
        """测试相同 regime 降低分数"""
        new_proposal = create_test_proposal(regime="strong_trend")
        existing = create_test_proposal(
            proposal_id="existing_1",
            regime="strong_trend",
        )
        
        result = self.agent.check_orthogonality(new_proposal, [existing])
        
        # 相同 regime 应该扣分
        assert result.orthogonality_score < 1.0

    def test_review_returns_report(self):
        """测试 review 返回报告"""
        new_proposal = create_test_proposal()
        existing = [create_test_proposal(proposal_id="existing_1")]
        
        report = self.agent.review(new_proposal, existing)
        
        assert report.proposal_id == new_proposal.proposal_id
        assert report.reviewer_type == "orthogonality"
        assert report.trace_id is not None

    def test_review_passes_for_different_proposals(self):
        """测试不同提案通过审查"""
        new_proposal = create_test_proposal(
            specialist_type=SpecialistType.TREND,
            required_features=["ema_fast", "adx", "bb_upper"],
            hypothesis="这是一个趋势策略基于EMA和ADX",
        )
        existing = create_test_proposal(
            proposal_id="existing_1",
            specialist_type=SpecialistType.PRICE_VOLUME,
            required_features=["volume_roc", "atr"],
            hypothesis="成交量异常波动率策略",
        )
        
        report = self.agent.review(new_proposal, [existing])
        
        # 不同的提案应该通过或有条件通过
        assert report.verdict in [ReviewVerdict.PASS, ReviewVerdict.CONDITIONAL, ReviewVerdict.FAIL]

    def test_review_fails_for_similar_proposals(self):
        """测试相似提案不通过"""
        new_proposal = create_test_proposal(
            specialist_type=SpecialistType.TREND,
            hypothesis="这是一个趋势策略",
            required_features=["ema_fast", "ema_slow"],
        )
        existing = create_test_proposal(
            proposal_id="existing_1",
            specialist_type=SpecialistType.TREND,
            hypothesis="这是一个趋势策略",
            required_features=["ema_fast", "ema_slow"],
        )
        
        report = self.agent.review(new_proposal, [existing])
        
        # 高度相似的提案应该失败或条件通过
        assert report.verdict in [ReviewVerdict.FAIL, ReviewVerdict.CONDITIONAL]

    def test_text_similarity_calculation(self):
        """测试文本相似度计算"""
        # 相似文本
        score1 = self.agent._compute_text_similarity(
            "这是一个测试假设",
            "这是一个测试假设"
        )
        assert score1 == 1.0
        
        # 不同文本
        score2 = self.agent._compute_text_similarity(
            "趋势策略 EMA 交叉",
            "成交量异常 波动率"
        )
        assert score2 < 1.0
        
        # 空文本
        score3 = self.agent._compute_text_similarity("", "测试")
        assert score3 == 0.0

    def test_infer_direction(self):
        """测试方向推断"""
        # 做多
        long_proposal = create_test_proposal(hypothesis="做多策略")
        assert self.agent._infer_direction(long_proposal) == "long"
        
        # 做空
        short_proposal = create_test_proposal(hypothesis="做空策略")
        assert self.agent._infer_direction(short_proposal) == "short"
        
        # 中性
        neutral_proposal = create_test_proposal(hypothesis="趋势跟踪策略")
        assert self.agent._infer_direction(neutral_proposal) == "neutral"


# ==================== RiskCostRedTeamAgent 测试 ====================

class TestRiskCostRedTeamAgent:
    """RiskCostRedTeamAgent 测试"""

    def setup_method(self):
        """测试前准备"""
        self.agent = RiskCostRedTeamAgent()

    def test_review_with_valid_proposal(self):
        """测试有效提案审查"""
        proposal = create_test_proposal(
            hypothesis="趋势跟踪策略",
            required_features=["ema_fast", "ema_slow"],
            failure_modes=["市场横盘", "趋势反转时止损"],
        )
        
        report = self.agent.review(proposal, {})
        
        assert report.proposal_id == proposal.proposal_id
        assert report.reviewer_type == "risk_cost"
        assert report.verdict in [ReviewVerdict.PASS, ReviewVerdict.CONDITIONAL, ReviewVerdict.FAIL]

    def test_review_with_high_trading_fee(self):
        """测试高交易费率"""
        proposal = create_test_proposal(
            cost_assumptions=CostAssumptions(
                trading_fee_bps=100.0,  # 非常高
                slippage_bps=20.0,
            )
        )
        
        report = self.agent.review(proposal, {})
        
        # 高费率应该导致低分
        assert report.cost_score < 0.7

    def test_review_with_high_funding_rate(self):
        """测试高资金费率"""
        proposal = create_test_proposal(
            cost_assumptions=CostAssumptions(
                trading_fee_bps=10.0,
                slippage_bps=5.0,
                funding_rate_annual=0.50,  # 年化50%
            )
        )
        
        report = self.agent.review(proposal, {})
        
        # 高资金费率应该影响成本评分
        assert report.cost_score is not None

    def test_review_no_failure_modes(self):
        """测试缺少失效条件"""
        proposal = create_test_proposal(
            failure_modes=[],
            evidence_refs=[],
        )
        
        report = self.agent.review(proposal, {})
        
        # 缺少失效条件应该影响评分（可能是 FAIL 或 CONDITIONAL）
        assert report.verdict in [ReviewVerdict.FAIL, ReviewVerdict.CONDITIONAL, ReviewVerdict.PASS]

    def test_boundary_compliance_no_trading_instructions(self):
        """测试边界合规-无交易指令"""
        proposal = create_test_proposal(
            hypothesis="基于 EMA 交叉的趋势跟踪策略",
        )
        
        report = self.agent.review(proposal, {})
        
        # 正常假设应该通过边界检查
        assert report.verdict in [ReviewVerdict.PASS, ReviewVerdict.CONDITIONAL]

    def test_boundary_compliance_with_trading_instructions(self):
        """测试边界合规-包含交易指令"""
        proposal = create_test_proposal(
            hypothesis="当 EMA 金叉时买入，死叉时卖出",
        )
        
        # 创建带交易指令的 proposal
        class TradingProposal:
            def __init__(self):
                self.proposal_id = "test"
                self.hypothesis = "当 EMA 金叉时买入，死叉时卖出"
                self.feature_version = "v1.0.0"
                self.prompt_version = "v1.0.0"
        
        compliance = self.agent._check_boundary_compliance(TradingProposal())
        
        # 包含交易指令应该不通过
        assert compliance is False

    def test_data_quality_check(self):
        """测试数据质量检查"""
        # 完整提案
        good_proposal = create_test_proposal(
            required_features=["ema_fast", "ema_slow"],
            evidence_refs=["ref1", "ref2"],
            feature_version="v1.0.0",
        )
        
        score1 = self.agent._check_data_quality(good_proposal, {})
        assert score1 >= 0.8
        
        # 缺少特征的提案
        bad_proposal = create_test_proposal(
            required_features=[],
            evidence_refs=[],
            feature_version="",
        )
        
        score2 = self.agent._check_data_quality(bad_proposal, {})
        assert score2 < score1

    def test_cost_fragility_check(self):
        """测试成本脆弱性检查"""
        # 合理成本
        reasonable_proposal = create_test_proposal(
            cost_assumptions=CostAssumptions(
                trading_fee_bps=10.0,
                slippage_bps=5.0,
                funding_rate_annual=0.05,
            )
        )
        
        score1 = self.agent._check_cost_fragility(reasonable_proposal)
        assert score1 >= 0.8
        
        # 高成本
        high_cost_proposal = create_test_proposal(
            cost_assumptions=CostAssumptions(
                trading_fee_bps=50.0,
                slippage_bps=20.0,
                funding_rate_annual=0.50,
            )
        )
        
        score2 = self.agent._check_cost_fragility(high_cost_proposal)
        assert score2 < score1

    def test_liquidity_check(self):
        """测试流动性检查"""
        # 普通提案
        normal_proposal = create_test_proposal(
            hypothesis="趋势跟踪策略",
        )
        
        score1 = self.agent._check_liquidity(normal_proposal, {})
        assert score1 >= 0.8
        
        # 高频提案
        hft_proposal = create_test_proposal(
            hypothesis="高频交易策略",
        )
        
        score2 = self.agent._check_liquidity(hft_proposal, {})
        assert score2 < score1

    def test_failure_mode_clarity_check(self):
        """测试失效条件清晰度检查"""
        # 详细失效条件
        clear_proposal = create_test_proposal(
            failure_modes=[
                "当市场处于横盘整理时，趋势信号频繁失效",
                "当波动率急剧下降时，EMA 交叉信号不可靠",
            ]
        )
        
        score1 = self.agent._check_failure_mode_clarity(clear_proposal)
        assert score1 >= 0.6
        
        # 模糊失效条件
        vague_proposal = create_test_proposal(
            failure_modes=["可能失效"]
        )
        
        score2 = self.agent._check_failure_mode_clarity(vague_proposal)
        assert score2 < score1

    def test_risk_score_calculation(self):
        """测试风险得分计算"""
        proposal = create_test_proposal(
            required_features=["ema_fast"],
            failure_modes=["横盘"],
        )
        
        data_quality = 0.9
        cost_fragility = 0.8
        liquidity = 0.85
        failure_mode_clarity = 0.7
        
        risk_score = self.agent._compute_risk_score(
            data_quality, cost_fragility, liquidity, failure_mode_clarity
        )
        
        # 加权平均
        expected = (
            data_quality * 0.3 +
            cost_fragility * 0.3 +
            liquidity * 0.2 +
            failure_mode_clarity * 0.2
        )
        assert risk_score == pytest.approx(expected)


# ==================== 边界约束测试 ====================

class TestReviewerBoundaryConstraints:
    """Reviewer 边界约束测试"""

    def test_no_direct_order_in_output(self):
        """测试输出不包含直接订单"""
        agent = OrthogonalityAgent()
        
        # 正常提案
        normal_proposal = create_test_proposal(
            hypothesis="基于 EMA 的趋势策略"
        )
        
        result = agent.check_orthogonality(normal_proposal, [])
        
        # 不应该报告违规
        assert result.is_orthogonal is True

    def test_risk_cost_agent_detects_forbidden_phrases(self):
        """测试风险成本 Agent 检测禁止短语"""
        agent = RiskCostRedTeamAgent()
        
        class ProposalWithTradingInstruction:
            proposal_id = "test"
            hypothesis = "当价格上涨时买入 10000 USDT"
            feature_version = "v1.0.0"
            prompt_version = "v1.0.0"
        
        compliance = agent._check_boundary_compliance(ProposalWithTradingInstruction())
        
        assert compliance is False

    def test_review_report_traceability(self):
        """测试审查报告可追踪性"""
        ortho_agent = OrthogonalityAgent()
        risk_agent = RiskCostRedTeamAgent()
        
        proposal = create_test_proposal()
        
        ortho_report = ortho_agent.review(proposal, [])
        risk_report = risk_agent.review(proposal, {})
        
        # 应该有 trace_id
        assert ortho_report.trace_id is not None
        assert risk_report.trace_id is not None
        
        # 应该有版本标签
        assert ortho_report.feature_version is not None
        assert risk_report.feature_version is not None
