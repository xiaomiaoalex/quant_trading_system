"""
Portfolio Constructor - 组合构建器
================================

将多个 SleeveProposal 组合成有限个可测试的组合候选。

输出：
- active sleeves
- 每个 sleeve 的 capital cap
- regime 启停条件
- 冲突优先级
- 组合级风险说明
- 自动生成评估任务

设计原则：
1. 只负责组合构建，不直接下单
2. 必须考虑风险分散
3. 必须设置资本上限
4. 必须定义状态启停条件
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

from insight.committee.schemas import (
    ConflictResolution,
    PortfolioProposal,
    RegimeCondition,
    ReviewReport,
    SleeveAssignment,
    SleeveProposal,
    SpecialistType,
    generate_trace_id,
)

logger = logging.getLogger(__name__)


@dataclass
class CapitalAllocation:
    """资金分配结果"""
    sleeve_id: str
    specialist_type: SpecialistType
    capital_cap: Decimal
    weight: float
    max_position_size: Decimal


@dataclass
class RegimeAssignment:
    """状态机分配"""
    sleeve_id: str
    regime_name: str
    entry_conditions: List[str]
    exit_conditions: List[str]
    confidence_threshold: float


@dataclass
class ConflictResult:
    """冲突解决结果"""
    has_conflicts: bool
    resolutions: List[ConflictResolution]


@dataclass
class PortfolioConstructionResult:
    """组合构建结果"""
    portfolio_proposal: PortfolioProposal
    capital_allocations: List[CapitalAllocation]
    regime_assignments: List[RegimeAssignment]
    conflict_result: ConflictResult
    risk_explanation: str
    evaluation_task_id: str


class PortfolioConstructor:
    """
    Portfolio Constructor
    
    负责将多个通过审查的 SleeveProposal 组合成 PortfolioProposal。
    """
    
    # 默认参数
    DEFAULT_TOTAL_CAPITAL = Decimal("10000")  # 默认总资金
    MAX_SLIVES_PER_PORTFOLIO = 5             # 最大 sleeve 数量
    MIN_CAPITAL_PER_SLEEVE = Decimal("500")   # 每个 sleeve 最小资金
    
    def __init__(
        self,
        max_sleeves: int = MAX_SLIVES_PER_PORTFOLIO,
        min_capital_per_sleeve: Decimal = MIN_CAPITAL_PER_SLEEVE,
        default_total_capital: Decimal = DEFAULT_TOTAL_CAPITAL,
    ):
        self.max_sleeves = max_sleeves
        self.min_capital_per_sleeve = min_capital_per_sleeve
        self.default_total_capital = default_total_capital
    
    def construct(
        self,
        approved_proposals: List[SleeveProposal],
        review_reports: List[ReviewReport],
        total_capital: Optional[Decimal] = None,
    ) -> PortfolioConstructionResult:
        """
        构建组合
        
        Args:
            approved_proposals: 通过审查的 proposals
            review_reports: 审查报告
            total_capital: 总资金（可选，默认使用 DEFAULT_TOTAL_CAPITAL）
            
        Returns:
            PortfolioConstructionResult: 组合构建结果
        """
        total_capital = total_capital or self.default_total_capital
        trace_id = generate_trace_id()
        
        logger.info(
            f"Starting portfolio construction: {len(approved_proposals)} proposals, "
            f"total_capital={total_capital}, trace_id={trace_id}"
        )
        
        # 1. 选择要包含的 sleeves（如果超过最大数量）
        selected_proposals = self._select_proposals(approved_proposals, review_reports)
        
        # 2. 分配资金
        capital_allocations = self._allocate_capital(selected_proposals, total_capital)
        
        # 3. 分配 regime 条件
        regime_assignments = self._assign_regimes(selected_proposals)
        
        # 4. 检测并解决冲突
        conflict_result = self._resolve_conflicts(selected_proposals, capital_allocations)
        
        # 5. 生成风险说明
        risk_explanation = self._generate_risk_explanation(
            selected_proposals, capital_allocations, conflict_result
        )
        
        # 6. 生成评估任务 ID
        evaluation_task_id = f"eval_{trace_id}"
        
        # 7. 构建 PortfolioProposal
        sleeves = [
            SleeveAssignment(
                proposal_id=alloc.sleeve_id,
                capital_cap=alloc.capital_cap,
                weight=alloc.weight,
                max_position_size=alloc.max_position_size,
                regime_enabled=True,
            )
            for alloc in capital_allocations
        ]
        
        capital_caps = {
            alloc.sleeve_id: alloc.capital_cap
            for alloc in capital_allocations
        }
        
        regime_conditions = {
            ra.sleeve_id: RegimeCondition(
                regime_name=ra.regime_name,
                entry_conditions=ra.entry_conditions,
                exit_conditions=ra.exit_conditions,
                confidence_threshold=ra.confidence_threshold,
            )
            for ra in regime_assignments
        }
        
        portfolio_proposal = PortfolioProposal(
            proposal_id=f"portfolio_{trace_id}",
            sleeves=sleeves,
            capital_caps=capital_caps,
            regime_conditions=regime_conditions,
            conflict_priorities=conflict_result.resolutions,
            risk_explanation=risk_explanation,
            evaluation_task_id=evaluation_task_id,
            feature_version=selected_proposals[0].feature_version if selected_proposals else "",
            prompt_version=selected_proposals[0].prompt_version if selected_proposals else "",
            trace_id=trace_id,
        )
        
        result = PortfolioConstructionResult(
            portfolio_proposal=portfolio_proposal,
            capital_allocations=capital_allocations,
            regime_assignments=regime_assignments,
            conflict_result=conflict_result,
            risk_explanation=risk_explanation,
            evaluation_task_id=evaluation_task_id,
        )
        
        logger.info(
            f"Portfolio construction completed: {len(sleeves)} sleeves, "
            f"trace_id={trace_id}"
        )
        
        return result
    
    def _select_proposals(
        self,
        proposals: List[SleeveProposal],
        review_reports: List[ReviewReport],
    ) -> List[SleeveProposal]:
        """
        选择要包含的 proposals
        
        优先选择：
        1. 正交性得分高的
        2. 风险得分高的
        3. 不同 specialist_type 的
        """
        # 如果数量在限制内，直接返回
        if len(proposals) <= self.max_sleeves:
            return proposals
        
        # 构建评分
        scores: Dict[str, float] = {}
        for proposal in proposals:
            # 找到对应的 review
            score = 0.5  # 默认分数
            
            for report in review_reports:
                if report.proposal_id == proposal.proposal_id:
                    if report.orthogonality_score is not None:
                        score += report.orthogonality_score * 0.5
                    if report.risk_score is not None:
                        score += report.risk_score * 0.5
                    break
            
            scores[proposal.proposal_id] = score
        
        # 按分数排序
        sorted_proposals = sorted(
            proposals,
            key=lambda p: (
                # 优先不同 specialist_type
                -len([x for x in proposals if x.specialist_type == p.specialist_type]),
                # 然后按分数
                -scores.get(p.proposal_id, 0.5),
            )
        )
        
        # 返回前 N 个
        return sorted_proposals[:self.max_sleeves]
    
    def _allocate_capital(
        self,
        proposals: List[SleeveProposal],
        total_capital: Decimal,
    ) -> List[CapitalAllocation]:
        """
        分配资金
        
        策略：
        1. 平均分配作为基准
        2. 根据 specialist_type 调整权重
        """
        if not proposals:
            return []
        
        # 计算基础权重
        base_weight = Decimal("1") / Decimal(str(len(proposals)))
        
        allocations: List[CapitalAllocation] = []
        
        for proposal in proposals:
            # 根据 specialist_type 调整权重
            weight_multiplier = self._get_weight_multiplier(proposal.specialist_type)
            adjusted_weight = float(base_weight * weight_multiplier)
            
            # 计算资金上限
            capital_cap = total_capital * Decimal(str(adjusted_weight))
            
            # 确保最小资金
            if capital_cap < self.min_capital_per_sleeve:
                capital_cap = self.min_capital_per_sleeve
            
            # 计算最大持仓（使用 20% 作为最大单持仓）
            max_position = capital_cap * Decimal("0.2")
            
            allocation = CapitalAllocation(
                sleeve_id=proposal.proposal_id,
                specialist_type=proposal.specialist_type,
                capital_cap=capital_cap.quantize(Decimal("1")),
                weight=adjusted_weight,
                max_position_size=max_position.quantize(Decimal("1")),
            )
            allocations.append(allocation)
        
        # 重新归一化权重
        total_weight = sum(a.weight for a in allocations)
        if total_weight > 0:
            for allocation in allocations:
                allocation.weight = allocation.weight / total_weight
        
        return allocations
    
    def _get_weight_multiplier(self, specialist_type: SpecialistType) -> Decimal:
        """
        根据 specialist_type 获取权重乘数
        
        风险分散原则：避免过度集中于某一类策略
        """
        multipliers = {
            SpecialistType.TREND: Decimal("1.0"),
            SpecialistType.PRICE_VOLUME: Decimal("1.0"),
            SpecialistType.FUNDING_OI: Decimal("0.8"),
            SpecialistType.ONCHAIN: Decimal("0.7"),
            SpecialistType.EVENT_REGIME: Decimal("0.8"),
        }
        return multipliers.get(specialist_type, Decimal("1.0"))
    
    def _assign_regimes(
        self,
        proposals: List[SleeveProposal],
    ) -> List[RegimeAssignment]:
        """
        分配 regime 条件
        """
        assignments: List[RegimeAssignment] = []
        
        for proposal in proposals:
            # 使用 proposal 中定义的 regime
            regime_name = proposal.regime or "any_trend"
            
            # 构建 entry/exit 条件
            entry_conditions = [
                f"{regime_name} regime detected",
                f"{proposal.specialist_type.value} signal triggered",
            ]
            
            exit_conditions = [
                "Opposite signal detected",
                "Drawdown exceeds threshold",
            ]
            
            assignment = RegimeAssignment(
                sleeve_id=proposal.proposal_id,
                regime_name=regime_name,
                entry_conditions=entry_conditions,
                exit_conditions=exit_conditions,
                confidence_threshold=0.7,
            )
            assignments.append(assignment)
        
        return assignments
    
    def _resolve_conflicts(
        self,
        proposals: List[SleeveProposal],
        allocations: List[CapitalAllocation],
    ) -> ConflictResult:
        """
        检测并解决冲突
        
        冲突类型：
        1. 方向冲突：同一方向多个策略
        2. Regime 冲突：同一 regime 多个策略
        """
        resolutions: List[ConflictResolution] = []
        
        # 按 specialist_type 分组
        by_type: Dict[SpecialistType, List[SleeveProposal]] = {}
        for proposal in proposals:
            if proposal.specialist_type not in by_type:
                by_type[proposal.specialist_type] = []
            by_type[proposal.specialist_type].append(proposal)
        
        # 检查同一类型的冲突
        for specialist_type, type_proposals in by_type.items():
            if len(type_proposals) > 1:
                # 同一类型多个策略，需要优先级
                for i, proposal in enumerate(type_proposals[1:], start=1):
                    resolution = ConflictResolution(
                        conflict_type=f"same_type_{specialist_type.value}",
                        higher_priority_sleeve=type_proposals[0].proposal_id,
                        lower_priority_sleeve=proposal.proposal_id,
                        resolution_rule="lower_priority_sleeve_capital_reduced",
                        capital_adjustment=allocations[
                            next(
                                j for j, a in enumerate(allocations)
                                if a.sleeve_id == proposal.proposal_id
                            )
                        ].capital_cap * Decimal("0.5"),
                    )
                    resolutions.append(resolution)
        
        return ConflictResult(
            has_conflicts=len(resolutions) > 0,
            resolutions=resolutions,
        )
    
    def _generate_risk_explanation(
        self,
        proposals: List[SleeveProposal],
        allocations: List[CapitalAllocation],
        conflict_result: ConflictResult,
    ) -> str:
        """
        生成风险说明
        """
        lines = [
            f"Portfolio contains {len(proposals)} sleeves:",
        ]
        
        for proposal in proposals:
            alloc = next(
                a for a in allocations if a.sleeve_id == proposal.proposal_id
            )
            lines.append(
                f"- {proposal.specialist_type.value}: "
                f"capital={alloc.capital_cap}, weight={alloc.weight:.2%}"
            )
        
        if conflict_result.has_conflicts:
            lines.append("")
            lines.append("Conflict resolutions applied:")
            for resolution in conflict_result.resolutions:
                lines.append(
                    f"- {resolution.conflict_type}: "
                    f"{resolution.lower_priority_sleeve[:8]}... capital reduced by "
                    f"{resolution.capital_adjustment}"
                )
        
        return "\n".join(lines)
