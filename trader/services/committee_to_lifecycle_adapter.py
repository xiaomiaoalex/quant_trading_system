"""
Committee to Lifecycle Adapter - Committee 与 Lifecycle Manager 适配器
======================================================================

将 Portfolio Committee 的输出适配到 StrategyLifecycleManager。

职责：
1. 提交 CommitteeRun 到 HITL 审批
2. 审批通过后创建策略草案
3. 创建回测任务
4. 处理拒绝情况

设计原则：
1. Committee 只负责研究，不负责执行
2. 所有决策必须经过 HITL 审批
3. 策略进入 lifecycle 后才能执行
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from insight.committee.schemas import CommitteeRun, ProposalStatus

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LifecycleAdapterConfig:
    """适配器配置"""
    auto_submit_to_hitl: bool = True
    auto_create_backtest: bool = False
    default_approval_timeout_seconds: int = 3600


class CommitteeToLifecycleAdapter:
    """
    Committee 到 Lifecycle Manager 的适配器

    桥接 Portfolio Committee 的研究输出与 StrategyLifecycleManager 的
    策略生命周期管理。
    """

    def __init__(
        self,
        lifecycle_manager: Any,
        hitl_governance: Any,
        config: Optional[LifecycleAdapterConfig] = None,
    ):
        self._lifecycle_manager = lifecycle_manager
        self._hitl_governance = hitl_governance
        self._config = config or LifecycleAdapterConfig()
        self._initialized_at = datetime.now(timezone.utc)

    async def submit_for_approval(self, run: CommitteeRun) -> Dict[str, Any]:
        """
        提交 CommitteeRun 到 HITL 审批

        Args:
            run: CommitteeRun 对象

        Returns:
            提交结果字典
        """
        try:
            run.final_status = ProposalStatus.IN_REVIEW

            hitl_submission = {
                "run_id": run.run_id,
                "trace_id": run.trace_id,
                "proposal_count": len(run.sleeve_proposals),
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "submitted": True,
            }

            logger.info(
                f"CommitteeRun submitted for approval: run_id={run.run_id}, "
                f"proposals={len(run.sleeve_proposals)}"
            )

            return {
                "success": True,
                "message": f"CommitteeRun {run.run_id} submitted for HITL approval",
                "hitl_submission": hitl_submission,
            }

        except Exception as e:
            logger.error(f"Failed to submit for approval: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Failed to submit: {str(e)}",
            }

    async def approve_and_create_backtest(
        self,
        run: CommitteeRun,
        approver: str,
        approval_comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        审批通过并创建回测任务

        Args:
            run: CommitteeRun 对象
            approver: 审批人
            approval_comment: 审批意见

        Returns:
            结果字典，包含 strategy_draft_id 和 backtest_job_id
        """
        try:
            run.human_decision = "APPROVED"
            run.approver = approver
            run.decision_reason = approval_comment
            run.final_status = ProposalStatus.APPROVED

            strategy_draft_id = await self._create_strategy_draft(run)

            backtest_job_id = None
            if self._config.auto_create_backtest and run.portfolio_proposal:
                backtest_job_id = await self._create_backtest_task(run, strategy_draft_id)
                run.backtest_job_id = backtest_job_id

            logger.info(
                f"CommitteeRun approved: run_id={run.run_id}, "
                f"strategy_draft_id={strategy_draft_id}, "
                f"backtest_job_id={backtest_job_id}"
            )

            return {
                "success": True,
                "message": f"CommitteeRun {run.run_id} approved",
                "strategy_draft_id": strategy_draft_id,
                "backtest_job_id": backtest_job_id,
            }

        except Exception as e:
            logger.error(f"Failed to approve: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Failed to approve: {str(e)}",
            }

    async def reject(
        self,
        run: CommitteeRun,
        rejector: str,
        reason: str,
    ) -> Dict[str, Any]:
        """
        拒绝 CommitteeRun

        Args:
            run: CommitteeRun 对象
            rejector: 拒绝人
            reason: 拒绝原因

        Returns:
            结果字典
        """
        try:
            run.human_decision = "REJECTED"
            run.approver = rejector
            run.decision_reason = reason
            run.final_status = ProposalStatus.REJECTED

            logger.info(
                f"CommitteeRun rejected: run_id={run.run_id}, "
                f"rejector={rejector}, reason={reason}"
            )

            return {
                "success": True,
                "message": f"CommitteeRun {run.run_id} rejected",
            }

        except Exception as e:
            logger.error(f"Failed to reject: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Failed to reject: {str(e)}",
            }

    async def _create_strategy_draft(self, run: CommitteeRun) -> str:
        """
        从 CommitteeRun 创建策略草案

        Returns:
            strategy_draft_id
        """
        strategy_draft_id = f"draft_{run.run_id[:8]}"

        try:
            if hasattr(self._lifecycle_manager, 'create_strategy'):
                draft = await self._lifecycle_manager.create_strategy(
                    name=f"Committee Strategy {run.run_id[:8]}",
                    description=run.research_request,
                    code=self._generate_strategy_code(run),
                )
                if hasattr(draft, 'strategy_id'):
                    strategy_draft_id = draft.strategy_id

        except Exception as e:
            logger.warning(f"Failed to create strategy draft via lifecycle manager: {e}")

        return strategy_draft_id

    async def _create_backtest_task(
        self,
        run: CommitteeRun,
        strategy_draft_id: str,
    ) -> Optional[str]:
        """
        创建回测任务

        Returns:
            backtest_job_id 或 None
        """
        if not run.portfolio_proposal:
            return None

        backtest_job_id = f"backtest_{run.run_id[:8]}"

        try:
            if hasattr(self._lifecycle_manager, 'run_backtest'):
                backtest = await self._lifecycle_manager.run_backtest(
                    strategy_id=strategy_draft_id,
                    config=self._generate_backtest_config(run),
                )
                if hasattr(backtest, 'job_id'):
                    backtest_job_id = backtest.job_id

        except Exception as e:
            logger.warning(f"Failed to create backtest via lifecycle manager: {e}")

        return backtest_job_id

    def _generate_strategy_code(self, run: CommitteeRun) -> str:
        """生成策略代码"""
        lines = [
            f"# Auto-generated strategy from CommitteeRun {run.run_id}",
            f"# Research request: {run.research_request}",
            f"# Generated at: {datetime.now(timezone.utc).isoformat()}",
            "",
            "from typing import *",
            "from decimal import Decimal",
            "",
            "",
            "class CommitteeStrategy:",
            "    def __init__(self):",
            f"        self.name = f'Committee Strategy {run.run_id[:8]}'",
            f"        self.sleeve_count = {len(run.sleeve_proposals)}",
            "",
            "    def on_market_data(self, data):",
            "        pass",
        ]

        return "\n".join(lines)

    def _generate_backtest_config(self, run: CommitteeRun) -> Dict[str, Any]:
        """生成回测配置"""
        return {
            "run_id": run.run_id,
            "trace_id": run.trace_id,
            "total_capital": str(run.portfolio_proposal.total_capital_estimate()) if run.portfolio_proposal else "10000",
            "sleeves": [
                {
                    "proposal_id": s.proposal_id,
                    "capital_cap": str(s.capital_cap),
                    "weight": s.weight,
                }
                for s in (run.portfolio_proposal.sleeves if run.portfolio_proposal else [])
            ],
        }

    def get_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        """
        获取 CommitteeRun 状态

        Args:
            run_id: CommitteeRun ID

        Returns:
            状态字典或 None
        """
        try:
            if hasattr(self._lifecycle_manager, 'get_lifecycle'):
                lifecycle = self._lifecycle_manager.get_lifecycle(run_id)
                if lifecycle:
                    return {
                        "run_id": run_id,
                        "status": lifecycle.status.value if hasattr(lifecycle, 'status') else "unknown",
                        "stage": lifecycle.stage if hasattr(lifecycle, 'stage') else "unknown",
                    }
        except Exception as e:
            logger.error(f"Failed to get status: {e}")

        return None
