"""
Portfolio Research Workflow - 研究工作流编排器
============================================

负责协调 Portfolio Committee 的完整研究流程。

流程：
1. 接收研究请求
2. 通过 Router 路由到合适的 Specialist Agents
3. Specialist Agents 并行生成 SleeveProposal
4. Red Team Agents 审查 proposals
5. Portfolio Constructor 构建组合
6. 保存 CommitteeRun 到存储

设计原则：
1. 所有 Agent 只做研究与 proposal，不直接下单
2. 必须经过 HITL 审批才能进入 backtest
3. 所有操作可审计回放
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from insight.committee import (
    CommitteeRun,
    CommitteeRunStatus,
    ProposalStatus,
    ReviewReport,
    ReviewResult,
    ReviewVerdict,
    SleeveProposal,
)
from insight.committee.orthogonality import OrthogonalityAgent
from insight.committee.portfolio_constructor import PortfolioConstructor
from insight.committee.red_team import RiskCostRedTeamAgent
from insight.committee.router import CommitteeRouter
from insight.committee.schemas import SpecialistType

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkflowConfig:
    """工作流配置"""
    feature_version: str = "v1.0.0"
    prompt_version: str = "v1.0.0"
    context_package_version: str = "v1.0.0"
    max_parallel_specialists: int = 5
    enable_red_team: bool = True
    enable_orthogonality_check: bool = True
    store_results: bool = True


@dataclass(slots=True)
class WorkflowResult:
    """工作流执行结果"""
    success: bool
    committee_run: CommitteeRun
    execution_time_seconds: float
    error_message: Optional[str] = None


class PortfolioResearchWorkflow:
    """
    Portfolio Committee 研究工作流编排器

    负责协调完整的委员会研究流程。
    """

    def __init__(self, config: Optional[WorkflowConfig] = None):
        self.config = config or WorkflowConfig()
        self._router = CommitteeRouter()
        self._portfolio_constructor = PortfolioConstructor()
        self._risk_cost_red_team = RiskCostRedTeamAgent()
        self._orthogonality_agent = OrthogonalityAgent()
        self._initialized_at = datetime.now(timezone.utc)

    async def run(
        self,
        research_request: str,
        context: Optional[Dict[str, Any]] = None,
        total_capital: Optional[Decimal] = None,
    ) -> WorkflowResult:
        """
        执行完整的研究工作流

        Args:
            research_request: 研究请求
            context: 上下文信息（市场数据、特征等）
            total_capital: 总资金

        Returns:
            WorkflowResult: 工作流执行结果
        """
        run_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        start_time = time.time()

        committee_run = CommitteeRun(
            run_id=run_id,
            research_request=research_request,
            context_package_version=self.config.context_package_version,
            feature_version=self.config.feature_version,
            prompt_version=self.config.prompt_version,
            trace_id=trace_id,
            status=CommitteeRunStatus.RUNNING,
        )

        try:
            context = context or {}

            logger.info(
                f"Starting portfolio research workflow: run_id={run_id}, "
                f"request={research_request[:50]}..., trace_id={trace_id}"
            )

            specialist_outputs = self._run_specialists(
                research_request, context
            )

            proposals = self._extract_proposals(specialist_outputs)

            if not proposals:
                raise ValueError("No valid proposals generated from specialists")

            committee_run.sleeve_proposals = proposals

            review_reports = self._run_red_team(proposals, context)
            committee_run.review_results = [
                ReviewResult(
                    reviewer_type=r.reviewer_type,
                    verdict=r.verdict,
                    concerns=r.concerns,
                    suggestions=r.suggestions,
                    scores={
                        "risk_score": r.risk_score or 0.0,
                        "cost_score": r.cost_score or 0.0,
                        "orthogonality_score": getattr(r, 'orthogonality_score', None) or 0.0,
                    },
                )
                for r in review_reports
            ]

            valid_proposals = self._filter_valid_proposals(proposals, review_reports)

            if valid_proposals:
                construction_result = self._portfolio_constructor.construct(
                    approved_proposals=valid_proposals,
                    review_reports=review_reports,
                    total_capital=total_capital,
                )
                committee_run.portfolio_proposal = construction_result.portfolio_proposal

            committee_run.status = CommitteeRunStatus.COMPLETED
            committee_run.final_status = ProposalStatus.PASSED

            execution_time = time.time() - start_time

            if self.config.store_results:
                await self._save_committee_run(committee_run)

            logger.info(
                f"Portfolio research workflow completed: run_id={run_id}, "
                f"sleeves={len(proposals)}, execution_time={execution_time:.2f}s"
            )

            return WorkflowResult(
                success=True,
                committee_run=committee_run,
                execution_time_seconds=execution_time,
            )

        except Exception as e:
            logger.error(f"Portfolio research workflow failed: run_id={run_id}, error={e}", exc_info=True)

            committee_run.status = CommitteeRunStatus.FAILED
            committee_run.final_status = ProposalStatus.REJECTED

            execution_time = time.time() - start_time

            return WorkflowResult(
                success=False,
                committee_run=committee_run,
                execution_time_seconds=execution_time,
                error_message=str(e),
            )

    def _run_specialists(
        self,
        research_request: str,
        context: Dict[str, Any],
    ) -> List[Any]:
        """运行 Specialist Agents - agent.research() 是同步方法"""

        matched_types = self._router.route(research_request)

        logger.info(
            f"Routing to specialists: {[t.value for t in matched_types]}"
        )

        outputs = []
        for specialist_type in matched_types:
            agent = self._router.get_agent(specialist_type)
            try:
                output = agent.research(research_request, context)
                outputs.append(output)
            except Exception as e:
                logger.error(f"Specialist {specialist_type} failed: {e}")

        return outputs

    def _extract_proposals(self, outputs: List[Any]) -> List[SleeveProposal]:
        """从 Agent 输出中提取有效的 SleeveProposal"""
        proposals = []

        for output in outputs:
            if not hasattr(output, 'validation_result') or not output.validation_result.is_valid:
                logger.warning(f"Skipping invalid output: {output.trace_id if hasattr(output, 'trace_id') else 'unknown'}")
                continue

            if not output.content:
                continue

            try:
                proposal_data = output.content

                if isinstance(proposal_data, dict):
                    proposal = SleeveProposal(
                        proposal_id=proposal_data.get("proposal_id", str(uuid.uuid4())),
                        specialist_type=SpecialistType(proposal_data.get("specialist_type", "trend")),
                        hypothesis=proposal_data.get("hypothesis", ""),
                        required_features=proposal_data.get("required_features", []),
                        regime=proposal_data.get("regime", ""),
                        failure_modes=proposal_data.get("failure_modes", []),
                        evidence_refs=proposal_data.get("evidence_refs", []),
                        feature_version=output.feature_version,
                        prompt_version=output.prompt_version,
                        trace_id=output.trace_id,
                    )
                    proposals.append(proposal)
                elif isinstance(proposal_data, SleeveProposal):
                    proposals.append(proposal_data)
            except Exception as e:
                logger.error(f"Failed to extract proposal: {e}")

        return proposals

    def _run_red_team(
        self,
        proposals: List[SleeveProposal],
        context: Dict[str, Any],
    ) -> List[ReviewReport]:
        """运行 Red Team Agents 审查 - review() 是同步方法"""
        if not self.config.enable_red_team:
            return []

        review_reports = []

        for proposal in proposals:
            risk_cost_report = self._risk_cost_red_team.review(proposal, context)
            review_reports.append(risk_cost_report)

            if self.config.enable_orthogonality_check and len(proposals) > 1:
                other_proposals = [p for p in proposals if p.proposal_id != proposal.proposal_id]
                ortho_report = self._orthogonality_agent.review(proposal, other_proposals)
                review_reports.append(ortho_report)

        return review_reports

    def _filter_valid_proposals(
        self,
        proposals: List[SleeveProposal],
        review_reports: List[ReviewReport],
    ) -> List[SleeveProposal]:
        """根据审查结果过滤有效的 proposals"""
        valid_proposals = []

        for proposal in proposals:
            proposal_reviews = [r for r in review_reports if r.proposal_id == proposal.proposal_id]

            all_passed = all(
                r.verdict in (ReviewVerdict.PASS, ReviewVerdict.SKIP, ReviewVerdict.CONDITIONAL)
                for r in proposal_reviews
            )

            if all_passed or not proposal_reviews:
                valid_proposals.append(proposal)
            else:
                logger.info(
                    f"Proposal {proposal.proposal_id[:8]} rejected by red team: "
                    f"verdicts={[r.verdict.value for r in proposal_reviews]}"
                )

        return valid_proposals

    async def _save_committee_run(self, run: CommitteeRun) -> None:
        """保存 CommitteeRun 到存储"""
        try:
            from trader.adapters.persistence.portfolio_proposal_store import PortfolioProposalStore

            store = PortfolioProposalStore()
            await store.save_committee_run(run.to_dict())

            logger.info(f"CommitteeRun saved: run_id={run.run_id}")
        except Exception as e:
            logger.error(f"Failed to save CommitteeRun: {e}")

    async def get_run(self, run_id: str) -> Optional[WorkflowResult]:
        """获取指定 run 的结果"""
        try:
            from trader.adapters.persistence.portfolio_proposal_store import PortfolioProposalStore

            store = PortfolioProposalStore()
            run_data = await store.get_committee_run(run_id)

            if not run_data:
                return None

            from insight.committee.schemas import SpecialistType

            sleeves = []
            for sleeve_data in run_data.get("sleeve_proposals", []):
                sleeve_data["specialist_type"] = SpecialistType(sleeve_data.get("specialist_type", "trend"))
                sleeves.append(SleeveProposal(**sleeve_data))

            portfolio = None
            if run_data.get("portfolio_proposal"):
                from insight.committee.schemas import PortfolioProposal
                portfolio = PortfolioProposal(**run_data["portfolio_proposal"])

            reviews = []
            for review_data in run_data.get("review_results", []):
                review_data["verdict"] = ReviewVerdict(review_data.get("verdict", "skip"))
                reviews.append(ReviewResult(**review_data))

            run = CommitteeRun(
                run_id=run_data.get("run_id", ""),
                research_request=run_data.get("research_request", ""),
                context_package_version=run_data.get("context_package_version", ""),
                sleeve_proposals=sleeves,
                portfolio_proposal=portfolio,
                review_results=reviews,
                human_decision=run_data.get("human_decision"),
                approver=run_data.get("approver"),
                decision_reason=run_data.get("decision_reason"),
                backtest_job_id=run_data.get("backtest_job_id"),
                final_status=ProposalStatus(run_data.get("final_status", "pending")),
                feature_version=run_data.get("feature_version", ""),
                prompt_version=run_data.get("prompt_version", ""),
                trace_id=run_data.get("trace_id", ""),
                status=CommitteeRunStatus(run_data.get("status", "pending")),
            )

            return WorkflowResult(
                success=run.status == CommitteeRunStatus.COMPLETED,
                committee_run=run,
                execution_time_seconds=0.0,
            )

        except Exception as e:
            logger.error(f"Failed to get run: {e}")
            return None
