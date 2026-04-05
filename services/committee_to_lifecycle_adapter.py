"""
Committee to Lifecycle Adapter - Committee 到 Lifecycle 适配器
=========================================================

将 Committee 流程接入现有的 HITL / Lifecycle 体系。

链路：
CommitteeRun -> Review -> Human Approve -> BacktestJob / StrategyDraft -> LifecycleManager

设计原则：
1. 不新建第二套审批系统
2. 直接复用现有 AI-clean / CodeSandbox / HITL / LifecycleManager
3. 所有操作必须可审计
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from insight.committee.schemas import (
    CommitteeRun,
    PortfolioProposal,
    ProposalStatus,
    ReviewReport,
)
from trader.services.strategy_lifecycle_manager import (
    StrategyLifecycleManager,
    LifecycleStatus,
)
from trader.core.application.hitl_governance import (
    HITLGovernance,
    HITLDecision,
)

logger = logging.getLogger(__name__)


@dataclass
class LifecycleAdapterConfig:
    """适配器配置"""
    # HITL 配置
    hitl_timeout_seconds: int = 300  # 5 分钟
    
    # 回测配置
    default_backtest_symbols: List[str] = None  # 默认回测标的
    default_backtest_timeframe: str = "1h"      # 默认时间框架


class CommitteeToLifecycleAdapter:
    """
    Committee 到 Lifecycle 适配器
    
    负责将 Committee 流程的结果接入现有的生命周期管理体系。
    """
    
    def __init__(
        self,
        lifecycle_manager: StrategyLifecycleManager,
        hitl_governance: HITLGovernance,
        config: Optional[LifecycleAdapterConfig] = None,
    ):
        self.lifecycle_manager = lifecycle_manager
        self.hitl_governance = hitl_governance
        self.config = config or LifecycleAdapterConfig()
    
    async def submit_for_approval(
        self,
        committee_run: CommitteeRun,
    ) -> Dict[str, Any]:
        """
        提交 Committee 结果到 HITL 审批
        
        Args:
            committee_run: Committee 运行记录
            
        Returns:
            提交结果
        """
        if not committee_run.portfolio_proposal:
            return {
                "success": False,
                "error": "No portfolio proposal to submit",
            }
        
        portfolio = committee_run.portfolio_proposal
        
        logger.info(
            f"Submitting portfolio proposal {portfolio.proposal_id} for HITL approval, "
            f"run_id={committee_run.run_id}"
        )
        
        # 构建 HITL 建议
        suggestion = self._build_hitl_suggestion(committee_run)
        
        # 提交到 HITL
        submit_result = await self.hitl_governance.submit_for_approval(
            suggestion=suggestion,
            related_run_id=committee_run.run_id,
        )
        
        if submit_result.get("success"):
            committee_run.status = ProposalStatus.IN_REVIEW
            logger.info(
                f"Portfolio proposal submitted for approval: "
                f"proposal_id={portfolio.proposal_id}"
            )
        
        return submit_result
    
    async def approve_and_create_backtest(
        self,
        committee_run: CommitteeRun,
        approver: str,
        approval_comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        审批通过并创建回测任务
        
        Args:
            committee_run: Committee 运行记录
            approver: 审批人
            approval_comment: 审批意见
            
        Returns:
            回测任务创建结果
        """
        if not committee_run.portfolio_proposal:
            return {
                "success": False,
                "error": "No portfolio proposal to approve",
            }
        
        portfolio = committee_run.portfolio_proposal
        
        logger.info(
            f"Approving portfolio proposal {portfolio.proposal_id}, "
            f"approver={approver}"
        )
        
        # 1. 记录 HITL 决策
        await self.hitl_governance.approve(
            related_run_id=committee_run.run_id,
            approver=approver,
            reason=approval_comment or "Portfolio committee approved",
        )
        
        # 2. 创建策略草案
        strategy_draft = await self._create_strategy_draft(committee_run)
        
        # 3. 创建回测任务
        backtest_job = await self._create_backtest_job(
            committee_run, strategy_draft
        )
        
        # 4. 更新 CommitteeRun
        committee_run.human_decision = "APPROVED"
        committee_run.approver = approver
        committee_run.decision_reason = approval_comment
        committee_run.backtest_job_id = backtest_job.get("job_id")
        committee_run.final_status = ProposalStatus.APPROVED
        
        logger.info(
            f"Portfolio proposal approved and backtest created: "
            f"proposal_id={portfolio.proposal_id}, "
            f"backtest_job_id={backtest_job.get('job_id')}"
        )
        
        return {
            "success": True,
            "strategy_draft_id": strategy_draft.get("strategy_id"),
            "backtest_job_id": backtest_job.get("job_id"),
        }
    
    async def reject(
        self,
        committee_run: CommitteeRun,
        rejector: str,
        reason: str,
    ) -> Dict[str, Any]:
        """
        拒绝 Committee 结果
        
        Args:
            committee_run: Committee 运行记录
            rejector: 拒绝人
            reason: 拒绝原因
            
        Returns:
            拒绝结果
        """
        logger.info(
            f"Rejecting committee run {committee_run.run_id}, "
            f"rejector={rejector}, reason={reason}"
        )
        
        # 记录 HITL 决策
        await self.hitl_governance.reject(
            related_run_id=committee_run.run_id,
            rejector=rejector,
            reason=reason,
        )
        
        # 更新 CommitteeRun
        committee_run.human_decision = "REJECTED"
        committee_run.approver = rejector
        committee_run.decision_reason = reason
        committee_run.final_status = ProposalStatus.REJECTED
        
        return {
            "success": True,
            "run_id": committee_run.run_id,
            "decision": "REJECTED",
        }
    
    def _build_hitl_suggestion(
        self,
        committee_run: CommitteeRun,
    ) -> Dict[str, Any]:
        """构建 HITL 建议"""
        portfolio = committee_run.portfolio_proposal
        
        # 构建信号描述
        signal_description = self._generate_signal_description(committee_run)
        
        # 构建风险检查结果
        risk_check_result = self._generate_risk_check_result(committee_run)
        
        return {
            "run_id": committee_run.run_id,
            "proposal_id": portfolio.proposal_id,
            "signal": signal_description,
            "risk_check_result": risk_check_result,
            "recommended_action": "APPROVE" if self._is_auto_approvable(committee_run) else "REVIEW",
            "confidence": self._calculate_confidence(committee_run),
            "sleeve_count": len(portfolio.sleeves),
            "total_capital_estimate": float(portfolio.total_capital_estimate()),
            "trace_id": committee_run.trace_id,
        }
    
    def _generate_signal_description(
        self,
        committee_run: CommitteeRun,
    ) -> str:
        """生成信号描述"""
        portfolio = committee_run.portfolio_proposal
        
        sleeve_types = [
            s.get("proposal_id", "")[:8] + "..."
            for s in portfolio.sleeves[:3]
        ]
        
        return (
            f"Portfolio Committee Multi-Agent Signal: "
            f"{len(portfolio.sleeves)} sleeves, "
            f"types: {', '.join(sleeve_types)}"
        )
    
    def _generate_risk_check_result(
        self,
        committee_run: CommitteeRun,
    ) -> Dict[str, Any]:
        """生成风险检查结果"""
        # 从 review_results 中提取风险信息
        risk_score = 0.5
        cost_score = 0.5
        
        for review_result in committee_run.review_results:
            if review_result.scores.get("risk"):
                risk_score = review_result.scores["risk"]
            if review_result.scores.get("cost"):
                cost_score = review_result.scores["cost"]
        
        return {
            "risk_level": "LOW" if risk_score > 0.7 else "MEDIUM" if risk_score > 0.4 else "HIGH",
            "risk_score": risk_score,
            "cost_score": cost_score,
            "auto_approvable": self._is_auto_approvable(committee_run),
        }
    
    def _is_auto_approvable(
        self,
        committee_run: CommitteeRun,
    ) -> bool:
        """判断是否可以自动审批"""
        # 简单规则：
        # 1. 所有 review 都 PASS
        # 2. 风险得分 >= 0.7
        # 3. 成本得分 >= 0.6
        
        from insight.committee.schemas import ReviewVerdict
        
        all_pass = all(
            r.verdict == ReviewVerdict.PASS
            for r in committee_run.review_results
        )
        
        risk_score = max(
            (r.scores.get("risk", 0) for r in committee_run.review_results),
            default=0
        )
        
        cost_score = max(
            (r.scores.get("cost", 0) for r in committee_run.review_results),
            default=0
        )
        
        return all_pass and risk_score >= 0.7 and cost_score >= 0.6
    
    def _calculate_confidence(
        self,
        committee_run: CommitteeRun,
    ) -> float:
        """计算置信度"""
        scores = []
        
        for review_result in committee_run.review_results:
            if review_result.scores.get("orthogonality"):
                scores.append(review_result.scores["orthogonality"])
            if review_result.scores.get("risk"):
                scores.append(review_result.scores["risk"])
            if review_result.scores.get("cost"):
                scores.append(review_result.scores["cost"])
        
        if not scores:
            return 0.5
        
        return sum(scores) / len(scores)
    
    async def _create_strategy_draft(
        self,
        committee_run: CommitteeRun,
    ) -> Dict[str, Any]:
        """创建策略草案"""
        portfolio = committee_run.portfolio_proposal
        
        # 构建策略代码框架（这里只是占位符）
        strategy_code = self._generate_strategy_code(committee_run)
        
        # 使用 lifecycle manager 创建策略
        result = await self.lifecycle_manager.create_strategy(
            name=f"Committee_Portfolio_{portfolio.proposal_id[:8]}",
            code=strategy_code,
            description=portfolio.risk_explanation,
        )
        
        return result
    
    def _generate_strategy_code(
        self,
        committee_run: CommitteeRun,
    ) -> str:
        """生成策略代码框架"""
        # 这是一个占位符，实际代码需要从 proposal 生成
        portfolio = committee_run.portfolio_proposal
        
        code_lines = [
            "# Auto-generated by Portfolio Committee",
            f"# proposal_id: {portfolio.proposal_id}",
            f"# trace_id: {committee_run.trace_id}",
            "",
            "from trader.core.domain.services.strategy_protocol import StrategyPlugin",
            "",
            "",
            f"class CommitteePortfolio(StrategyPlugin):",
            f"    def __init__(self):",
            f"        self.name = 'Committee Portfolio'",
            f"        self.proposal_id = '{portfolio.proposal_id}'",
            f"        self.sleeves = {len(portfolio.sleeves)}",
            f"        self.capital_caps = {str(portfolio.capital_caps)}",
            "",
            f"    def on_tick(self, tick):",
            f"        # TODO: Implement strategy logic based on committee proposals",
            f"        pass",
        ]
        
        return "\n".join(code_lines)
    
    async def _create_backtest_job(
        self,
        committee_run: CommitteeRun,
        strategy_draft: Dict[str, Any],
    ) -> Dict[str, Any]:
        """创建回测任务"""
        portfolio = committee_run.portfolio_proposal
        
        # 这里应该调用回测服务创建回测任务
        # 暂时返回占位符
        job_id = f"backtest_{committee_run.run_id}"
        
        return {
            "job_id": job_id,
            "strategy_id": strategy_draft.get("strategy_id"),
            "symbols": self.config.default_backtest_symbols or ["BTCUSDT"],
            "timeframe": self.config.default_backtest_timeframe,
            "status": "PENDING",
        }
