"""
OrthogonalityAgent - 正交性审查 Agent
=====================================

负责判断新 sleeve 是否和旧 sleeve 重复，是否只是换皮的同一风险暴露。

核心职责：
1. 判断新 sleeve 是否和已有 sleeve 重复
2. 检查是否只是换皮的同一风险暴露
3. 评估是否能给组合带来真正增量
4. 计算正交性得分（0.0 - 1.0）

设计原则：
- 只负责否决，不负责推荐
- 必须输出具体的否决理由
- 正交性得分 < 0.7 必须否决
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from insight.committee.schemas import (
    AgentOutput,
    OutputType,
    ReviewReport,
    ReviewVerdict,
    SleeveProposal,
    ValidationResult,
    Violation,
    ViolationType,
    generate_trace_id,
)

logger = logging.getLogger(__name__)


@dataclass
class OrthogonalityResult:
    """正交性检查结果"""
    is_orthogonal: bool                          # 是否正交
    orthogonality_score: float                   # 正交性得分 (0.0 - 1.0)
    duplicate_risk_exposure: bool               # 是否重复风险暴露
    similar_proposals: List[str] = field(default_factory=list)  # 相似的 proposal IDs
    reasons: List[str] = field(default_factory=list)            # 具体理由
    suggested_changes: List[str] = field(default_factory=list)  # 建议修改


class OrthogonalityAgent:
    """
    正交性审查 Agent
    
    检查新的 SleeveProposal 是否与已有的 proposal 正交，
    防止重复研究和资源浪费。
    """
    
    # 正交性得分阈值
    MIN_ORTHOGONALITY_SCORE: float = 0.7
    
    def __init__(self, min_score: float = MIN_ORTHOGONALITY_SCORE):
        self.min_score = min_score
    
    def check_orthogonality(
        self,
        new_proposal: SleeveProposal,
        existing_proposals: List[SleeveProposal],
    ) -> OrthogonalityResult:
        """
        检查新 proposal 的正交性
        
        Args:
            new_proposal: 新的 proposal
            existing_proposals: 已有的 proposals
            
        Returns:
            OrthogonalityResult: 检查结果
        """
        if not existing_proposals:
            # 没有已有 proposal，直接通过
            return OrthogonalityResult(
                is_orthogonal=True,
                orthogonality_score=1.0,
                duplicate_risk_exposure=False,
                reasons=["No existing proposals to compare against"],
            )
        
        # 计算与每个已有 proposal 的正交性
        scores: List[float] = []
        similar_ids: List[str] = []
        all_reasons: List[str] = []
        
        for existing in existing_proposals:
            score, reason = self._compute_pairwise_orthogonality(
                new_proposal, existing
            )
            scores.append(score)
            
            if score < self.min_score:
                similar_ids.append(existing.proposal_id)
                all_reasons.append(f"Similar to {existing.proposal_id[:8]}...: {reason}")
        
        # 计算平均正交性得分
        avg_score = sum(scores) / len(scores) if scores else 1.0
        
        # 检查是否有重复风险暴露
        duplicate_exposure = self._check_duplicate_risk_exposure(
            new_proposal, existing_proposals
        )
        
        # 判断是否通过
        is_orthogonal = (
            avg_score >= self.min_score and
            not duplicate_exposure.is_duplicate
        )
        
        # 收集建议
        suggestions = []
        if avg_score < self.min_score:
            suggestions.append(
                f"Increase differentiation from similar proposals "
                f"(current score: {avg_score:.2f}, threshold: {self.min_score})"
            )
        if duplicate_exposure.is_duplicate:
            suggestions.extend(duplicate_exposure.suggestions)
        
        return OrthogonalityResult(
            is_orthogonal=is_orthogonal,
            orthogonality_score=avg_score,
            duplicate_risk_exposure=duplicate_exposure.is_duplicate,
            similar_proposals=similar_ids,
            reasons=all_reasons if all_reasons else ["Sufficient orthogonality"],
            suggested_changes=suggestions,
        )
    
    def _compute_pairwise_orthogonality(
        self,
        proposal_a: SleeveProposal,
        proposal_b: SleeveProposal,
    ) -> tuple[float, str]:
        """
        计算两个 proposal 之间的正交性得分
        
        Returns:
            (score, reason): 得分和理由
        """
        score = 1.0
        reasons: List[str] = []
        
        # 1. 检查 hypothesis 相似度
        hyp_similarity = self._compute_text_similarity(
            proposal_a.hypothesis,
            proposal_b.hypothesis,
        )
        if hyp_similarity > 0.8:
            score -= 0.3
            reasons.append(
                f"Hypothesis similarity too high ({hyp_similarity:.2f})"
            )
        elif hyp_similarity > 0.6:
            score -= 0.15
        
        # 2. 检查 regime 是否相同
        if proposal_a.regime == proposal_b.regime:
            score -= 0.2
            reasons.append("Same regime")
        
        # 3. 检查 required_features 重叠度
        features_a = set(proposal_a.required_features)
        features_b = set(proposal_b.required_features)
        overlap = len(features_a & features_b) / max(len(features_a | features_b), 1)
        if overlap > 0.8:
            score -= 0.3
            reasons.append(f"Feature overlap too high ({overlap:.2f})")
        elif overlap > 0.5:
            score -= 0.1
        
        # 4. 检查 specialist_type 是否相同
        if proposal_a.specialist_type == proposal_b.specialist_type:
            score -= 0.15
            reasons.append("Same specialist type")
        
        # 5. 检查 failure_modes 相似度
        if proposal_a.failure_modes and proposal_b.failure_modes:
            failure_similarity = self._compute_text_similarity(
                " ".join(proposal_a.failure_modes),
                " ".join(proposal_b.failure_modes),
            )
            if failure_similarity > 0.7:
                score -= 0.1
        
        # 确保得分在 [0, 1] 范围内
        score = max(0.0, min(1.0, score))
        
        reason = "; ".join(reasons) if reasons else "Sufficiently orthogonal"
        
        return score, reason
    
    def _compute_text_similarity(
        self,
        text_a: str,
        text_b: str,
    ) -> float:
        """
        计算两个文本的相似度（简单基于字符集重叠）
        
        Returns:
            0.0 - 1.0 的相似度
        """
        if not text_a or not text_b:
            return 0.0
        
        # 使用字符级 Jaccard 相似度
        set_a = set(text_a.lower())
        set_b = set(text_b.lower())
        
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        
        if union == 0:
            return 0.0
        
        return intersection / union
    
    @dataclass
    class DuplicateRiskResult:
        """重复风险暴露检查结果"""
        is_duplicate: bool
        suggestions: List[str] = field(default_factory=list)
    
    def _check_duplicate_risk_exposure(
        self,
        new_proposal: SleeveProposal,
        existing_proposals: List[SleeveProposal],
    ) -> DuplicateRiskResult:
        """
        检查是否只是换皮的同一风险暴露
        """
        # 检查方向风险
        new_direction = self._infer_direction(new_proposal)
        
        for existing in existing_proposals:
            existing_direction = self._infer_direction(existing)
            
            # 如果方向相同且特征高度重叠，可能是重复暴露
            if new_direction == existing_direction:
                features_a = set(new_proposal.required_features)
                features_b = set(existing.required_features)
                overlap = len(features_a & features_b) / max(len(features_a), 1)
                
                if overlap > 0.7 and new_direction != "neutral":
                    return self.DuplicateRiskResult(
                        is_duplicate=True,
                        suggestions=[
                            "Consider using different timeframes for features",
                            "Try different regime conditions",
                            "Use alternative data sources",
                        ],
                    )
        
        return self.DuplicateRiskResult(is_duplicate=False)
    
    def _infer_direction(self, proposal: SleeveProposal) -> str:
        """
        从 hypothesis 推断策略方向
        """
        hyp_lower = proposal.hypothesis.lower()
        
        if "做多" in hyp_lower or "long" in hyp_lower or "买入" in hyp_lower:
            return "long"
        elif "做空" in hyp_lower or "short" in hyp_lower or "卖出" in hyp_lower:
            return "short"
        else:
            return "neutral"
    
    def review(
        self,
        new_proposal: SleeveProposal,
        existing_proposals: List[SleeveProposal],
    ) -> ReviewReport:
        """
        执行完整的正交性审查
        
        Args:
            new_proposal: 要审查的新 proposal
            existing_proposals: 已有的 proposals
            
        Returns:
            ReviewReport: 审查报告
        """
        trace_id = generate_trace_id()
        
        # 执行正交性检查
        result = self.check_orthogonality(new_proposal, existing_proposals)
        
        # 确定评审结论
        if result.is_orthogonal:
            verdict = ReviewVerdict.PASS
        elif result.orthogonality_score >= 0.5:
            verdict = ReviewVerdict.CONDITIONAL
        else:
            verdict = ReviewVerdict.FAIL
        
        # 构建 ReviewReport
        report = ReviewReport(
            report_id=f"ortho_{trace_id}",
            proposal_id=new_proposal.proposal_id,
            reviewer_type="orthogonality",
            verdict=verdict,
            concerns=result.reasons if not result.is_orthogonal else [],
            suggestions=result.suggested_changes,
            orthogonality_score=result.orthogonality_score,
            feature_version=new_proposal.feature_version,
            prompt_version=new_proposal.prompt_version,
            trace_id=trace_id,
        )
        
        logger.info(
            f"Orthogonality review completed: proposal={new_proposal.proposal_id[:8]}, "
            f"score={result.orthogonality_score:.2f}, verdict={verdict.value}"
        )
        
        return report
