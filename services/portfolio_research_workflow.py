"""
Portfolio Research Workflow - 组合研究工作流
==========================================

整合 Committee 各组件的研究工作流。

流程：
1. 接收研究请求
2. 并行运行 Specialist Agents
3. 对每个 proposal 运行 Red Team Agents
4. 选择通过审查的 proposals
5. 构建 PortfolioProposal
6. 进入 HITL 审批流程

设计原则：
1. 所有步骤必须可追踪（trace_id）
2. 所有操作必须记录审计日志
3. 失败时必须 Fail-Closed
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from insight.committee.orthogonality import OrthogonalityAgent
from insight.committee.portfolio_constructor import PortfolioConstructor
from insight.committee.red_team import RiskCostRedTeamAgent
from insight.committee.router import CommitteeRouter
from insight.committee.schemas import (
    AgentOutput,
    CommitteeRun,
    CommitteeRunStatus,
    PortfolioProposal,
    ProposalStatus,
    ReviewReport,
    ReviewResult,
    ReviewVerdict,
    SleeveProposal,
    SpecialistType,
    generate_trace_id,
)
from insight.committee.specialists import SpecialistConfig

logger = logging.getLogger(__name__)


@dataclass
class WorkflowConfig:
    """工作流配置"""
    # Specialist 配置
    specialist_feature_version: str = "v1.0.0"
    specialist_prompt_version: str = "v1.0.0"
    
    # 正交性阈值
    min_orthogonality_score: float = 0.7
    
    # 风险成本阈值
    min_risk_score: float = 0.5
    min_cost_score: float = 0.5
    
    # 组合构建
    max_sleeves_per_portfolio: int = 5
    default_total_capital: Decimal = Decimal("10000")
    
    # 并行执行
    max_parallel_specialists: int = 5
    specialist_timeout_seconds: float = 60.0


@dataclass
class WorkflowResult:
    """工作流结果"""
    success: bool
    committee_run: CommitteeRun
    portfolio_proposal: Optional[PortfolioProposal]
    error_message: Optional[str]
    execution_time_seconds: float


class PortfolioResearchWorkflow:
    """
    Portfolio Research Workflow
    
    整合 Committee 各组件的完整研究工作流。
    """
    
    def __init__(self, config: Optional[WorkflowConfig] = None):
        self.config = config or WorkflowConfig()
        
        # 初始化组件
        specialist_config = SpecialistConfig(
            feature_version=self.config.specialist_feature_version,
            prompt_version=self.config.specialist_prompt_version,
        )
        self.router = CommitteeRouter(specialist_config)
        self.orthogonality_agent = OrthogonalityAgent(
            min_score=self.config.min_orthogonality_score
        )
        self.risk_cost_agent = RiskCostRedTeamAgent(
            min_cost_score=self.config.min_cost_score,
        )
        self.portfolio_constructor = PortfolioConstructor(
            max_sleeves=self.config.max_sleeves_per_portfolio,
            default_total_capital=self.config.default_total_capital,
        )
        
        # 状态
        self._active_runs: Dict[str, CommitteeRun] = {}
    
    async def run(
        self,
        research_request: str,
        context: Optional[Dict[str, Any]] = None,
        total_capital: Optional[Decimal] = None,
    ) -> WorkflowResult:
        """
        运行完整的研究工作流
        
        Args:
            research_request: 研究请求
            context: 上下文信息（可选）
            total_capital: 总资金（可选）
            
        Returns:
            WorkflowResult: 工作流结果
        """
        start_time = datetime.now(timezone.utc)
        trace_id = generate_trace_id()
        
        logger.info(f"Starting portfolio research workflow: trace_id={trace_id}")
        
        # 创建 CommitteeRun
        run = CommitteeRun(
            run_id=f"run_{trace_id}",
            research_request=research_request,
            context_package_version=self.config.specialist_feature_version,
            status=CommitteeRunStatus.RUNNING,
            trace_id=trace_id,
        )
        self._active_runs[run.run_id] = run
        
        try:
            # Step 1: 运行 Specialist Agents（并行）
            specialist_outputs = await self._run_specialists(
                research_request, context
            )
            run.sleeve_proposals = [
                SleeveProposal(**output.content)
                for output in specialist_outputs
                if output.validation_result.is_valid
            ]
            
            if not run.sleeve_proposals:
                return self._create_error_result(
                    run, start_time,
                    "No valid sleeve proposals generated"
                )
            
            # Step 2: 运行 Red Team Agents（串行，每个 proposal）
            review_results = await self._run_red_teams(run.sleeve_proposals, context)
            run.review_results = review_results
            
            # Step 3: 选择通过审查的 proposals
            approved_proposals = self._select_approved_proposals(
                run.sleeve_proposals, review_results
            )
            
            if not approved_proposals:
                return self._create_error_result(
                    run, start_time,
                    "No proposals passed review"
                )
            
            # Step 4: 构建 PortfolioProposal
            construction_result = self.portfolio_constructor.construct(
                approved_proposals,
                self._get_review_reports_for_proposals(review_results, approved_proposals),
                total_capital,
            )
            run.portfolio_proposal = construction_result.portfolio_proposal
            
            # Step 5: 完成
            run.status = CommitteeRunStatus.COMPLETED
            run.final_status = ProposalStatus.PASSED
            
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            
            result = WorkflowResult(
                success=True,
                committee_run=run,
                portfolio_proposal=run.portfolio_proposal,
                error_message=None,
                execution_time_seconds=execution_time,
            )
            
            logger.info(
                f"Portfolio research workflow completed: "
                f"run_id={run.run_id}, sleeves={len(run.sleeve_proposals)}, "
                f"approved={len(approved_proposals)}, "
                f"execution_time={execution_time:.2f}s"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Portfolio research workflow failed: {e}", exc_info=True)
            run.status = CommitteeRunStatus.FAILED
            run.final_status = ProposalStatus.REJECTED
            
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            
            return WorkflowResult(
                success=False,
                committee_run=run,
                portfolio_proposal=None,
                error_message=str(e),
                execution_time_seconds=execution_time,
            )
        finally:
            # 清理活跃运行
            if run.run_id in self._active_runs:
                del self._active_runs[run.run_id]
    
    async def _run_specialists(
        self,
        research_request: str,
        context: Optional[Dict[str, Any]],
    ) -> List[AgentOutput]:
        """
        运行 Specialist Agents
        """
        # 路由到合适的 specialist
        specialist_types = self.router.route(research_request)
        
        logger.info(
            f"Routed to specialists: {[t.value for t in specialist_types]}"
        )
        
        # 异步并行运行
        tasks = [
            self.router.get_agent(st).research(research_request, context)
            for st in specialist_types
        ]
        
        outputs = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理异常
        valid_outputs: List[AgentOutput] = []
        for i, output in enumerate(outputs):
            if isinstance(output, Exception):
                logger.warning(
                    f"Specialist {list(specialist_types)[i].value} failed: {output}"
                )
            else:
                valid_outputs.append(output)
        
        return valid_outputs
    
    async def _run_red_teams(
        self,
        proposals: List[SleeveProposal],
        context: Optional[Dict[str, Any]],
    ) -> List[ReviewResult]:
        """
        运行 Red Team Agents
        """
        review_results: List[ReviewResult] = []
        
        for proposal in proposals:
            # 1. Orthogonality 检查
            ortho_report = self.orthogonality_agent.review(
                proposal,
                []  # 暂时不传 existing proposals
            )
            
            # 2. Risk/Cost 检查
            risk_report = self.risk_cost_agent.review(proposal, context)
            
            # 合并结果
            review_results.append(ReviewResult(
                reviewer_type="orthogonality",
                verdict=ortho_report.verdict,
                concerns=ortho_report.concerns,
                suggestions=ortho_report.suggestions,
                scores={"orthogonality": ortho_report.orthogonality_score or 0.0},
            ))
            
            review_results.append(ReviewResult(
                reviewer_type="risk_cost",
                verdict=risk_report.verdict,
                concerns=risk_report.concerns,
                suggestions=risk_report.suggestions,
                scores={
                    "risk": risk_report.risk_score or 0.0,
                    "cost": risk_report.cost_score or 0.0,
                },
            ))
        
        return review_results
    
    def _select_approved_proposals(
        self,
        proposals: List[SleeveProposal],
        review_results: List[ReviewResult],
    ) -> List[SleeveProposal]:
        """
        选择通过审查的 proposals
        """
        approved: List[SleeveProposal] = []
        
        for proposal in proposals:
            # 获取该 proposal 的 review results
            proposal_reviews = [
                r for r in review_results
                # 这里需要通过其他方式关联，暂时简化处理
            ]
            
            # 检查是否所有 review 都通过
            # 简化：只要有一个 review 是 FAIL 就排除
            all_passed = True
            for result in review_results:
                if result.verdict == ReviewVerdict.FAIL:
                    all_passed = False
                    break
            
            if all_passed:
                approved.append(proposal)
        
        return approved
    
    def _get_review_reports_for_proposals(
        self,
        review_results: List[ReviewResult],
        proposals: List[SleeveProposal],
    ) -> List[ReviewReport]:
        """
        将 ReviewResult 转换为 ReviewReport
        """
        reports: List[ReviewReport] = []
        
        for proposal in proposals:
            for result in review_results:
                report = ReviewReport(
                    report_id=f"report_{generate_trace_id()}",
                    proposal_id=proposal.proposal_id,
                    reviewer_type=result.reviewer_type,
                    verdict=result.verdict,
                    concerns=result.concerns,
                    suggestions=result.suggestions,
                    orthogonality_score=result.scores.get("orthogonality"),
                    risk_score=result.scores.get("risk"),
                    cost_score=result.scores.get("cost"),
                )
                reports.append(report)
        
        return reports
    
    def _create_error_result(
        self,
        run: CommitteeRun,
        start_time: datetime,
        error_message: str,
    ) -> WorkflowResult:
        """创建错误结果"""
        run.status = CommitteeRunStatus.FAILED
        run.final_status = ProposalStatus.REJECTED
        
        execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()
        
        return WorkflowResult(
            success=False,
            committee_run=run,
            portfolio_proposal=None,
            error_message=error_message,
            execution_time_seconds=execution_time,
        )
    
    def get_active_run(self, run_id: str) -> Optional[CommitteeRun]:
        """获取活跃的运行"""
        return self._active_runs.get(run_id)
