"""
RiskCostRedTeamAgent - 风险成本否决 Agent
==========================================

负责审查数据质量、成本假设、流动性、失效条件和 AI/HITL 边界。

核心职责：
1. 检查数据是否干净
2. 检查成本是否脆弱
3. 检查流动性是否充足
4. 检查失效条件是否清楚
5. 检查是否越过 AI-clean / HITL 边界

设计原则：
- 只负责否决，不负责推荐
- 必须输出具体的否决理由
- 遇到无法验证的情况必须否决（Fail-Closed）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from insight.committee.schemas import (
    CostAssumptions,
    ReviewReport,
    ReviewVerdict,
    SleeveProposal,
    ViolationType,
    generate_trace_id,
)

logger = logging.getLogger(__name__)


@dataclass
class RiskCostCheckResult:
    """风险成本检查结果"""
    is_acceptable: bool                         # 是否可接受
    data_quality_score: float                   # 数据质量得分 (0.0 - 1.0)
    cost_fragility_score: float                 # 成本脆弱性得分 (0.0 - 1.0)
    liquidity_score: float                      # 流动性得分 (0.0 - 1.0)
    failure_mode_clarity: float                  # 失效条件清晰度 (0.0 - 1.0)
    boundary_compliance: bool                   # 是否符合边界约束
    
    violations: List[str] = field(default_factory=list)  # 违规详情
    suggestions: List[str] = field(default_factory=list)  # 修改建议


class RiskCostRedTeamAgent:
    """
    风险成本否决 Agent
    
    从数据、成本、流动性、失效条件和边界合规五个维度
    审查 SleeveProposal 的可行性。
    """
    
    # 最低得分阈值
    MIN_DATA_QUALITY_SCORE: float = 0.6
    MIN_COST_SCORE: float = 0.5
    MIN_LIQUIDITY_SCORE: float = 0.6
    MIN_FAILURE_MODE_CLARITY: float = 0.5
    
    def __init__(
        self,
        min_data_quality: float = MIN_DATA_QUALITY_SCORE,
        min_cost_score: float = MIN_COST_SCORE,
        min_liquidity_score: float = MIN_LIQUIDITY_SCORE,
        min_failure_mode_clarity: float = MIN_FAILURE_MODE_CLARITY,
    ):
        self.min_data_quality = min_data_quality
        self.min_cost_score = min_cost_score
        self.min_liquidity_score = min_liquidity_score
        self.min_failure_mode_clarity = min_failure_mode_clarity
    
    def review(
        self,
        proposal: SleeveProposal,
        context: Optional[Dict[str, Any]] = None,
    ) -> ReviewReport:
        """
        执行完整的风险成本审查
        
        Args:
            proposal: 要审查的 proposal
            context: 上下文信息（可选，包含市场数据等）
            
        Returns:
            ReviewReport: 审查报告
        """
        trace_id = generate_trace_id()
        context = context or {}
        
        # 执行各项检查
        data_quality = self._check_data_quality(proposal, context)
        cost_fragility = self._check_cost_fragility(proposal)
        liquidity = self._check_liquidity(proposal, context)
        failure_mode_clarity = self._check_failure_mode_clarity(proposal)
        boundary_compliance = self._check_boundary_compliance(proposal)
        
        # 收集所有违规
        all_violations = []
        if data_quality < self.min_data_quality:
            all_violations.append(
                f"Data quality score too low: {data_quality:.2f} < {self.min_data_quality}"
            )
        if cost_fragility < self.min_cost_score:
            all_violations.append(
                f"Cost fragility score too low: {cost_fragility:.2f} < {self.min_cost_score}"
            )
        if liquidity < self.min_liquidity_score:
            all_violations.append(
                f"Liquidity score too low: {liquidity:.2f} < {self.min_liquidity_score}"
            )
        if failure_mode_clarity < self.min_failure_mode_clarity:
            all_violations.append(
                f"Failure mode clarity too low: {failure_mode_clarity:.2f} < {self.min_failure_mode_clarity}"
            )
        if not boundary_compliance:
            all_violations.append("Boundary compliance check failed")
        
        # 计算综合得分
        risk_score = self._compute_risk_score(
            data_quality, cost_fragility, liquidity, failure_mode_clarity
        )
        cost_score = cost_fragility
        
        # 确定评审结论
        is_acceptable = (
            data_quality >= self.min_data_quality and
            cost_fragility >= self.min_cost_score and
            liquidity >= self.min_liquidity_score and
            failure_mode_clarity >= self.min_failure_mode_clarity and
            boundary_compliance
        )
        
        if is_acceptable:
            verdict = ReviewVerdict.PASS
        elif risk_score >= 0.4:
            verdict = ReviewVerdict.CONDITIONAL
        else:
            verdict = ReviewVerdict.FAIL
        
        # 构建建议
        suggestions = self._generate_suggestions(
            data_quality, cost_fragility, liquidity, failure_mode_clarity,
            boundary_compliance, proposal
        )
        
        # 构建 ReviewReport
        report = ReviewReport(
            report_id=f"riskcost_{trace_id}",
            proposal_id=proposal.proposal_id,
            reviewer_type="risk_cost",
            verdict=verdict,
            concerns=all_violations,
            suggestions=suggestions,
            risk_score=risk_score,
            cost_score=cost_score,
            feature_version=proposal.feature_version,
            prompt_version=proposal.prompt_version,
            trace_id=trace_id,
        )
        
        logger.info(
            f"RiskCost review completed: proposal={proposal.proposal_id[:8]}, "
            f"risk_score={risk_score:.2f}, verdict={verdict.value}"
        )
        
        return report
    
    def _check_data_quality(
        self,
        proposal: SleeveProposal,
        context: Dict[str, Any],
    ) -> float:
        """
        检查数据质量
        
        评估：
        1. required_features 是否都可以获取
        2. 特征版本是否有效
        3. 数据源是否可靠
        """
        score = 1.0
        reasons: List[str] = []
        
        # 检查 required_features 是否为空
        if not proposal.required_features:
            score -= 0.3
            reasons.append("No required features specified")
        
        # 检查 feature_version 是否有效
        if not proposal.feature_version or proposal.feature_version == "":
            score -= 0.2
            reasons.append("No feature version specified")
        
        # 检查 evidence_refs 是否有引用
        if not proposal.evidence_refs:
            score -= 0.1
            reasons.append("No evidence references provided")
        
        # 检查数据源（如果有上下文）
        if "data_sources" in context:
            data_sources = context["data_sources"]
            if not data_sources or len(data_sources) == 0:
                score -= 0.2
                reasons.append("No data sources available")
        
        return max(0.0, score)
    
    def _check_cost_fragility(self, proposal: SleeveProposal) -> float:
        """
        检查成本脆弱性
        
        评估：
        1. 成本假设是否完整
        2. 成本假设是否合理
        3. 成本对策略表现的影响
        """
        score = 1.0
        reasons: List[str] = []
        
        cost = proposal.cost_assumptions
        
        # 检查成本假设是否存在
        if not cost:
            score -= 0.4
            reasons.append("No cost assumptions provided")
            return max(0.0, score)
        
        # 检查交易费率
        if cost.trading_fee_bps > 20:
            score -= 0.2
            reasons.append(f"Trading fee too high: {cost.trading_fee_bps} bps")
        elif cost.trading_fee_bps < 0:
            score -= 0.3
            reasons.append("Negative trading fee is unrealistic")
        
        # 检查滑点
        if cost.slippage_bps > 10:
            score -= 0.15
            reasons.append(f"Slippage assumption too high: {cost.slippage_bps} bps")
        
        # 检查资金费率
        if cost.funding_rate_annual > 0.20:  # 年化 20%
            score -= 0.15
            reasons.append(f"Funding rate too high: {cost.funding_rate_annual*100:.1f}%")
        
        # 检查清算风险
        if cost.liquidation_risk_bps > 50:
            score -= 0.2
            reasons.append(f"Liquidation risk too high: {cost.liquidation_risk_bps} bps")
        
        # 检查日换手率
        if cost.estimated_turnover_per_day > 5:
            score -= 0.1
            reasons.append(f"Turnover assumption very high: {cost.estimated_turnover_per_day}x")
        
        return max(0.0, score)
    
    def _check_liquidity(
        self,
        proposal: SleeveProposal,
        context: Dict[str, Any],
    ) -> float:
        """
        检查流动性
        
        评估：
        1. 策略交易频率
        2. 目标交易资产流动性
        3. 头寸大小是否合理
        """
        score = 1.0
        reasons: List[str] = []
        
        # 从 hypothesis 推断交易频率
        hyp_lower = proposal.hypothesis.lower()
        
        if "高频" in hyp_lower or "high frequency" in hyp_lower or "hft" in hyp_lower:
            score -= 0.3
            reasons.append("High frequency strategy requires high liquidity")
        
        if "做市" in hyp_lower or "market making" in hyp_lower:
            score -= 0.2
            reasons.append("Market making requires deep liquidity")
        
        # 从 failure_modes 检查是否有流动性相关问题
        for failure in proposal.failure_modes:
            if "流动性" in failure or "liquidity" in failure.lower():
                score -= 0.1
        
        return max(0.0, score)
    
    def _check_failure_mode_clarity(self, proposal: SleeveProposal) -> float:
        """
        检查失效条件清晰度
        
        评估：
        1. 是否有失效条件描述
        2. 失效条件是否具体
        3. 是否有明确的触发条件
        """
        score = 1.0
        
        # 检查是否有失效条件
        if not proposal.failure_modes or len(proposal.failure_modes) == 0:
            score -= 0.4
            return score
        
        # 检查失效条件是否过于简单（字数过少）
        total_length = sum(len(f) for f in proposal.failure_modes)
        avg_length = total_length / len(proposal.failure_modes)
        
        if avg_length < 20:
            score -= 0.3
        elif avg_length < 40:
            score -= 0.1
        
        # 检查是否包含具体的触发条件
        specific_triggers = ["时", "当", "when", "if", " условиях"]
        has_specific = any(
            any(trigger in f.lower() for trigger in specific_triggers)
            for f in proposal.failure_modes
        )
        
        if not has_specific:
            score -= 0.1
        
        return max(0.0, score)
    
    def _check_boundary_compliance(self, proposal: SleeveProposal) -> bool:
        """
        检查边界合规
        
        检查是否违反 ADR-007 中定义的 Agent 边界约束。
        """
        # 检查 hypothesis 是否包含直接交易指令
        forbidden_phrases = [
            "买入", "卖出", "做多", "做空",
            "buy", "sell", "long", "short",
            "开仓", "平仓", "建仓", "止损",
            "直接下单", "立即执行",
        ]
        
        hyp_lower = proposal.hypothesis.lower()
        
        for phrase in forbidden_phrases:
            if phrase in hyp_lower:
                # 找到上下文
                idx = hyp_lower.find(phrase)
                context_start = max(0, idx - 20)
                context_end = min(len(hyp_lower), idx + len(phrase) + 20)
                context = proposal.hypothesis[context_start:context_end]
                
                logger.warning(
                    f"Potential boundary violation in proposal {proposal.proposal_id[:8]}: "
                    f"Found forbidden phrase '{phrase}' in context: ...{context}..."
                )
                return False
        
        return True
    
    def _compute_risk_score(
        self,
        data_quality: float,
        cost_fragility: float,
        liquidity: float,
        failure_mode_clarity: float,
    ) -> float:
        """
        计算综合风险得分
        """
        weights = {
            "data_quality": 0.3,
            "cost_fragility": 0.3,
            "liquidity": 0.2,
            "failure_mode_clarity": 0.2,
        }
        
        return (
            data_quality * weights["data_quality"] +
            cost_fragility * weights["cost_fragility"] +
            liquidity * weights["liquidity"] +
            failure_mode_clarity * weights["failure_mode_clarity"]
        )
    
    def _generate_suggestions(
        self,
        data_quality: float,
        cost_fragility: float,
        liquidity: float,
        failure_mode_clarity: float,
        boundary_compliance: bool,
        proposal: SleeveProposal,
    ) -> List[str]:
        """生成修改建议"""
        suggestions = []
        
        if data_quality < self.min_data_quality:
            suggestions.append(
                "Review required features and ensure all can be reliably sourced"
            )
        
        if cost_fragility < self.min_cost_score:
            suggestions.append(
                "Re-examine cost assumptions and ensure they reflect realistic trading conditions"
            )
        
        if liquidity < self.min_liquidity_score:
            suggestions.append(
                "Consider liquidity constraints when sizing positions"
            )
        
        if failure_mode_clarity < self.min_failure_mode_clarity:
            suggestions.append(
                "Provide more specific failure mode descriptions with trigger conditions"
            )
        
        if not boundary_compliance:
            suggestions.append(
                "Remove any direct trading instructions from hypothesis. "
                "Focus on research and pattern analysis only."
            )
        
        return suggestions
