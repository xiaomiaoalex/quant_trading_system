"""
Portfolio Research API Routes
============================

Committee 相关的 API 端点。

端点：
- POST /api/portfolio-research/run - 运行研究工作流
- GET /api/portfolio-research/runs - 列出 Committee runs
- GET /api/portfolio-research/runs/{run_id} - 获取特定 run
- POST /api/portfolio-research/runs/{run_id}/submit - 提交到 HITL
- POST /api/portfolio-research/runs/{run_id}/approve - 审批通过
- POST /api/portfolio-research/runs/{run_id}/reject - 拒绝
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from insight.committee.schemas import CommitteeRun, ProposalStatus
from services.portfolio_research_workflow import (
    PortfolioResearchWorkflow,
    WorkflowConfig,
    WorkflowResult,
)
from services.committee_to_lifecycle_adapter import (
    CommitteeToLifecycleAdapter,
    LifecycleAdapterConfig,
)
from trader.services.strategy_lifecycle_manager import StrategyLifecycleManager
from trader.core.application.hitl_governance import HITLGovernance

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/portfolio-research", tags=["PortfolioResearch"])


# =============================================================================
# Request/Response Models
# =============================================================================

class RunResearchRequest(BaseModel):
    """运行研究请求"""
    research_request: str
    context: Optional[Dict[str, Any]] = None
    total_capital: Optional[float] = None


class RunResearchResponse(BaseModel):
    """运行研究响应"""
    success: bool
    run_id: str
    trace_id: str
    sleeve_count: int
    approved_count: int
    portfolio_proposal_id: Optional[str] = None
    execution_time_seconds: float
    error_message: Optional[str] = None


class CommitteeRunSummary(BaseModel):
    """Committee Run 摘要"""
    run_id: str
    research_request: str
    status: str
    sleeve_count: int
    final_status: Optional[str] = None
    created_at: str


class SubmitForApprovalResponse(BaseModel):
    """提交审批响应"""
    success: bool
    run_id: str
    message: str


class ApprovalResponse(BaseModel):
    """审批响应"""
    success: bool
    run_id: str
    decision: str
    strategy_draft_id: Optional[str] = None
    backtest_job_id: Optional[str] = None


# =============================================================================
# Dependencies
# =============================================================================

_workflow: Optional[PortfolioResearchWorkflow] = None
_adapter: Optional[CommitteeToLifecycleAdapter] = None


def get_workflow() -> PortfolioResearchWorkflow:
    """获取工作流实例"""
    global _workflow
    if _workflow is None:
        config = WorkflowConfig()
        _workflow = PortfolioResearchWorkflow(config)
    return _workflow


def get_adapter() -> CommitteeToLifecycleAdapter:
    """获取适配器实例"""
    global _adapter
    if _adapter is None:
        lifecycle_manager = StrategyLifecycleManager()
        hitl_governance = HITLGovernance()
        config = LifecycleAdapterConfig()
        _adapter = CommitteeToLifecycleAdapter(
            lifecycle_manager,
            hitl_governance,
            config,
        )
    return _adapter


# =============================================================================
# Endpoints
# =============================================================================

@router.post("/run", response_model=RunResearchResponse)
async def run_research(request: RunResearchRequest):
    """
    运行 Portfolio Committee 研究工作流
    
    流程：
    1. 接收研究请求
    2. 并行运行 Specialist Agents
    3. 运行 Red Team Agents 审查
    4. 构建 PortfolioProposal
    """
    try:
        workflow = get_workflow()
        
        # 转换 total_capital
        total_capital = (
            Decimal(str(request.total_capital))
            if request.total_capital is not None
            else None
        )
        
        # 运行工作流
        result = await workflow.run(
            research_request=request.research_request,
            context=request.context,
            total_capital=total_capital,
        )
        
        # 构建响应
        response = RunResearchResponse(
            success=result.success,
            run_id=result.committee_run.run_id,
            trace_id=result.committee_run.trace_id,
            sleeve_count=len(result.committee_run.sleeve_proposals),
            approved_count=(
                len(result.committee_run.portfolio_proposal.sleeves)
                if result.committee_run.portfolio_proposal
                else 0
            ),
            portfolio_proposal_id=(
                result.committee_run.portfolio_proposal.proposal_id
                if result.committee_run.portfolio_proposal
                else None
            ),
            execution_time_seconds=result.execution_time_seconds,
            error_message=result.error_message,
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Failed to run research workflow: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs", response_model=List[CommitteeRunSummary])
async def list_runs(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """
    列出 Committee Runs
    """
    try:
        from trader.adapters.persistence.portfolio_proposal_store import PortfolioProposalStore
        
        store = PortfolioProposalStore()
        runs = await store.list_committee_runs(status, limit, offset)
        
        return [
            CommitteeRunSummary(
                run_id=r.get("run_id"),
                research_request=r.get("research_request", "")[:100],
                status=r.get("status"),
                sleeve_count=len(r.get("sleeve_proposals", [])),
                final_status=r.get("final_status"),
                created_at=r.get("created_at"),
            )
            for r in runs
        ]
        
    except Exception as e:
        logger.error(f"Failed to list runs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    """
    获取特定 Committee Run 的详细信息
    """
    try:
        from trader.adapters.persistence.portfolio_proposal_store import PortfolioProposalStore
        
        store = PortfolioProposalStore()
        run = await store.get_committee_run(run_id)
        
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        
        return run
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/runs/{run_id}/submit", response_model=SubmitForApprovalResponse)
async def submit_for_approval(run_id: str):
    """
    提交 Committee Run 到 HITL 审批
    """
    try:
        from trader.adapters.persistence.portfolio_proposal_store import PortfolioProposalStore
        
        store = PortfolioProposalStore()
        run_data = await store.get_committee_run(run_id)
        
        if not run_data:
            raise HTTPException(status_code=404, detail="Run not found")
        
        # 转换为 CommitteeRun 对象
        run = _dict_to_committee_run(run_data)
        
        # 提交到 HITL
        adapter = get_adapter()
        result = await adapter.submit_for_approval(run)
        
        # 更新存储
        await store.save_committee_run(run.to_dict())
        
        return SubmitForApprovalResponse(
            success=result.get("success", False),
            run_id=run_id,
            message=result.get("message", ""),
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit for approval: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/runs/{run_id}/approve", response_model=ApprovalResponse)
async def approve_run(
    run_id: str,
    approver: str = Query(..., description="Approver ID"),
    comment: Optional[str] = Query(None, description="Approval comment"),
):
    """
    审批通过 Committee Run
    
    流程：
    1. 记录 HITL 决策
    2. 创建策略草案
    3. 创建回测任务
    """
    try:
        from trader.adapters.persistence.portfolio_proposal_store import PortfolioProposalStore
        
        store = PortfolioProposalStore()
        run_data = await store.get_committee_run(run_id)
        
        if not run_data:
            raise HTTPException(status_code=404, detail="Run not found")
        
        # 转换为 CommitteeRun 对象
        run = _dict_to_committee_run(run_data)
        
        # 审批通过
        adapter = get_adapter()
        result = await adapter.approve_and_create_backtest(
            run,
            approver=approver,
            approval_comment=comment,
        )
        
        # 更新存储
        await store.save_committee_run(run.to_dict())
        
        return ApprovalResponse(
            success=result.get("success", False),
            run_id=run_id,
            decision="APPROVED",
            strategy_draft_id=result.get("strategy_draft_id"),
            backtest_job_id=result.get("backtest_job_id"),
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to approve run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/runs/{run_id}/reject", response_model=ApprovalResponse)
async def reject_run(
    run_id: str,
    rejector: str = Query(..., description="Rejector ID"),
    reason: str = Query(..., description="Rejection reason"),
):
    """
    拒绝 Committee Run
    """
    try:
        from trader.adapters.persistence.portfolio_proposal_store import PortfolioProposalStore
        
        store = PortfolioProposalStore()
        run_data = await store.get_committee_run(run_id)
        
        if not run_data:
            raise HTTPException(status_code=404, detail="Run not found")
        
        # 转换为 CommitteeRun 对象
        run = _dict_to_committee_run(run_data)
        
        # 拒绝
        adapter = get_adapter()
        result = await adapter.reject(run, rejector, reason)
        
        # 更新存储
        await store.save_committee_run(run.to_dict())
        
        return ApprovalResponse(
            success=result.get("success", False),
            run_id=run_id,
            decision="REJECTED",
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reject run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Helper Functions
# =============================================================================

def _dict_to_committee_run(data: Dict[str, Any]) -> CommitteeRun:
    """将字典转换为 CommitteeRun 对象"""
    from insight.committee.schemas import (
        CommitteeRunStatus,
        ReviewResult,
        ReviewVerdict,
        SleeveProposal,
    )
    from insight.committee.specialists.base import SpecialistType
    
    # 转换 sleeve_proposals
    sleeves = []
    for sleeve_data in data.get("sleeve_proposals", []):
        if isinstance(sleeve_data, dict):
            sleeve_data["specialist_type"] = SpecialistType(sleeve_data.get("specialist_type", "trend"))
            sleeves.append(SleeveProposal(**sleeve_data))
    
    # 转换 review_results
    reviews = []
    for review_data in data.get("review_results", []):
        if isinstance(review_data, dict):
            review_data["verdict"] = ReviewVerdict(review_data.get("verdict", "skip"))
            reviews.append(ReviewResult(**review_data))
    
    # 转换 portfolio_proposal
    portfolio = None
    if data.get("portfolio_proposal"):
        portfolio_data = data["portfolio_proposal"]
        from insight.committee.schemas import PortfolioProposal
        portfolio = PortfolioProposal(**portfolio_data)
    
    return CommitteeRun(
        run_id=data.get("run_id", ""),
        research_request=data.get("research_request", ""),
        context_package_version=data.get("context_package_version", ""),
        sleeve_proposals=sleeves,
        portfolio_proposal=portfolio,
        review_results=reviews,
        human_decision=data.get("human_decision"),
        approver=data.get("approver"),
        decision_reason=data.get("decision_reason"),
        backtest_job_id=data.get("backtest_job_id"),
        final_status=ProposalStatus(data.get("final_status", "pending")),
        feature_version=data.get("feature_version", ""),
        prompt_version=data.get("prompt_version", ""),
        trace_id=data.get("trace_id", ""),
        status=CommitteeRunStatus(data.get("status", "pending")),
    )
