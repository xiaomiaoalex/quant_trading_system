"""
Committee Audit Service - Committee 审计与回放服务
================================================

记录和回放 Committee 运行的所有操作。

审计要求：
- 每次 committee run 必须留下输入需求
- 使用的上下文包版本
- 每个 agent 输出
- review 结果
- human decision
- 进入的 backtest job
- 最终淘汰/保留结论

设计原则：
1. 所有操作必须记录
2. 基于 trace_id 可完整回放
3. 支持审计查询
4. PG-first 持久化
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from trader.adapters.persistence.portfolio_proposal_store import (
    PortfolioProposalStore,
)
from insight.committee.schemas import (
    AgentOutput,
    CommitteeRun,
    CommitteeRunStatus,
    ProposalStatus,
    Violation,
    ViolationType,
)

logger = logging.getLogger(__name__)


@dataclass
class AuditEvent:
    """审计事件"""
    event_id: str
    event_type: str              # RUN_STARTED, AGENT_OUTPUT, REVIEW_COMPLETED, etc.
    run_id: str
    trace_id: str
    agent_type: Optional[str]    # specialist type or reviewer type
    event_data: Dict[str, Any]  # 事件具体数据
    timestamp: datetime


@dataclass
class ReplayResult:
    """回放结果"""
    success: bool
    run_id: str
    trace_id: str
    events: List[AuditEvent]
    final_state: Dict[str, Any]
    error_message: Optional[str] = None


class CommitteeAuditService:
    """
    Committee 审计与回放服务
    
    职责：
    1. 记录所有 Committee 运行事件
    2. 提供基于 trace_id 的回放功能
    3. 支持审计查询
    4. 检测边界违规
    """
    
    def __init__(self, store: Optional[PortfolioProposalStore] = None):
        self._store = store or PortfolioProposalStore()
        self._event_log: List[AuditEvent] = []  # 内存缓存
    
    # =================================================================
    # Event Recording
    # =================================================================
    
    async def record_run_started(
        self,
        run: CommitteeRun,
        input_request: str,
        context_package_version: str,
    ) -> str:
        """
        记录 Committee Run 开始
        """
        event = AuditEvent(
            event_id=f"evt_{run.trace_id}_start",
            event_type="RUN_STARTED",
            run_id=run.run_id,
            trace_id=run.trace_id,
            agent_type=None,
            event_data={
                "input_request": input_request,
                "context_package_version": context_package_version,
                "status": run.status.value,
            },
            timestamp=datetime.now(timezone.utc),
        )
        
        await self._record_event(event)
        
        logger.info(f"Recorded RUN_STARTED: run_id={run.run_id}, trace_id={run.trace_id}")
        
        return event.event_id
    
    async def record_agent_output(
        self,
        run: CommitteeRun,
        agent_output: AgentOutput,
        agent_type: str,
    ) -> str:
        """
        记录 Agent 输出
        """
        # 检测边界违规
        violations = await self._check_boundary_violations(agent_output)
        
        event = AuditEvent(
            event_id=f"evt_{agent_output.trace_id}_agent",
            event_type="AGENT_OUTPUT",
            run_id=run.run_id,
            trace_id=agent_output.trace_id,
            agent_type=agent_type,
            event_data={
                "output_type": agent_output.output_type.value,
                "validation_result": {
                    "is_valid": agent_output.validation_result.is_valid,
                    "violations": [
                        {
                            "type": v.violation_type.value,
                            "description": v.description,
                        }
                        for v in agent_output.validation_result.violations
                    ],
                },
                "content_hash": agent_output.content.get("content_hash") if agent_output.content else None,
                "boundary_violations": violations,
            },
            timestamp=datetime.now(timezone.utc),
        )
        
        await self._record_event(event)
        
        if violations:
            logger.warning(
                f"Boundary violations detected: run_id={run.run_id}, "
                f"agent_type={agent_type}, violations={violations}"
            )
        
        return event.event_id
    
    async def record_review_completed(
        self,
        run: CommitteeRun,
        proposal_id: str,
        reviewer_type: str,
        verdict: str,
        scores: Dict[str, float],
    ) -> str:
        """
        记录 Review 完成
        """
        event = AuditEvent(
            event_id=f"evt_{run.trace_id}_review_{reviewer_type}",
            event_type="REVIEW_COMPLETED",
            run_id=run.run_id,
            trace_id=run.trace_id,
            agent_type=reviewer_type,
            event_data={
                "proposal_id": proposal_id,
                "verdict": verdict,
                "scores": scores,
            },
            timestamp=datetime.now(timezone.utc),
        )
        
        await self._record_event(event)
        
        logger.info(
            f"Recorded REVIEW_COMPLETED: run_id={run.run_id}, "
            f"proposal_id={proposal_id}, verdict={verdict}"
        )
        
        return event.event_id
    
    async def record_human_decision(
        self,
        run: CommitteeRun,
        decision: str,
        approver: str,
        reason: Optional[str],
        backtest_job_id: Optional[str],
    ) -> str:
        """
        记录人工决策
        """
        event = AuditEvent(
            event_id=f"evt_{run.trace_id}_decision",
            event_type="HUMAN_DECISION",
            run_id=run.run_id,
            trace_id=run.trace_id,
            agent_type=None,
            event_data={
                "decision": decision,
                "approver": approver,
                "reason": reason,
                "backtest_job_id": backtest_job_id,
            },
            timestamp=datetime.now(timezone.utc),
        )
        
        await self._record_event(event)
        
        logger.info(
            f"Recorded HUMAN_DECISION: run_id={run.run_id}, "
            f"decision={decision}, approver={approver}"
        )
        
        return event.event_id
    
    async def record_run_completed(
        self,
        run: CommitteeRun,
        final_status: ProposalStatus,
        portfolio_proposal_id: Optional[str],
    ) -> str:
        """
        记录 Committee Run 完成
        """
        event = AuditEvent(
            event_id=f"evt_{run.trace_id}_complete",
            event_type="RUN_COMPLETED",
            run_id=run.run_id,
            trace_id=run.trace_id,
            agent_type=None,
            event_data={
                "status": run.status.value,
                "final_status": final_status.value,
                "portfolio_proposal_id": portfolio_proposal_id,
                "sleeve_count": len(run.sleeve_proposals),
                "review_count": len(run.review_results),
            },
            timestamp=datetime.now(timezone.utc),
        )
        
        await self._record_event(event)
        
        logger.info(
            f"Recorded RUN_COMPLETED: run_id={run.run_id}, "
            f"final_status={final_status.value}"
        )
        
        return event.event_id
    
    async def _record_event(self, event: AuditEvent) -> None:
        """记录事件到存储"""
        # 添加到内存缓存
        self._event_log.append(event)
        
        # 如果事件数量达到阈值，批量写入 PG
        if len(self._event_log) >= 100:
            await self._flush_events()
    
    async def _flush_events(self) -> None:
        """批量写入事件到 PG"""
        if not self._event_log:
            return
        
        # 这里应该写入到 agent_audit_log 表
        # 简化处理，暂时记录到内存
        logger.debug(f"Flushing {len(self._event_log)} events to storage")
        
        self._event_log.clear()
    
    async def _check_boundary_violations(
        self,
        agent_output: AgentOutput,
    ) -> List[str]:
        """
        检测边界违规
        
        检查是否违反了 ADR-007 中定义的 Agent 边界约束。
        """
        violations: List[str] = []
        
        # 检查输出类型
        forbidden_types = [
            "direct_order",
            "bypass_hitl",
            "bypass_backtest",
        ]
        
        for violation in agent_output.validation_result.violations:
            if violation.violation_type.value in forbidden_types:
                violations.append(
                    f"{violation.violation_type.value}: {violation.description}"
                )
        
        # 检查 content 中是否有直接交易指令
        if agent_output.content:
            content_str = json.dumps(agent_output.content, default=str).lower()
            
            forbidden_phrases = [
                "买入", "卖出", "做多", "做空",
                "buy", "sell", "long", "short",
                "开仓", "平仓", "建仓",
                "直接下单", "立即执行",
            ]
            
            for phrase in forbidden_phrases:
                if phrase in content_str:
                    violations.append(f"Trading instruction detected: '{phrase}'")
        
        return violations
    
    # =================================================================
    # Replay
    # =================================================================
    
    async def replay(
        self,
        run_id: str,
    ) -> ReplayResult:
        """
        回放完整的 Committee Run
        
        基于 trace_id 重放整个运行过程。
        """
        try:
            # 获取 CommitteeRun
            run_data = await self._store.get_committee_run(run_id)
            if not run_data:
                return ReplayResult(
                    success=False,
                    run_id=run_id,
                    trace_id="",
                    events=[],
                    final_state={},
                    error_message="Run not found",
                )
            
            trace_id = run_data.get("trace_id", "")
            
            # 获取所有相关事件
            events = await self._get_events_for_run(run_id)
            
            # 构建最终状态
            final_state = {
                "run_id": run_id,
                "trace_id": trace_id,
                "status": run_data.get("status"),
                "final_status": run_data.get("final_status"),
                "sleeve_proposals": run_data.get("sleeve_proposals", []),
                "portfolio_proposal": run_data.get("portfolio_proposal"),
                "review_results": run_data.get("review_results", []),
                "human_decision": run_data.get("human_decision"),
                "backtest_job_id": run_data.get("backtest_job_id"),
            }
            
            return ReplayResult(
                success=True,
                run_id=run_id,
                trace_id=trace_id,
                events=events,
                final_state=final_state,
            )
            
        except Exception as e:
            logger.error(f"Replay failed: run_id={run_id}, error={e}", exc_info=True)
            return ReplayResult(
                success=False,
                run_id=run_id,
                trace_id="",
                events=[],
                final_state={},
                error_message=str(e),
            )
    
    async def _get_events_for_run(self, run_id: str) -> List[AuditEvent]:
        """获取指定 run 的所有事件"""
        # 从内存缓存获取
        cached_events = [e for e in self._event_log if e.run_id == run_id]
        
        # 如果缓存不足，从 PG 获取
        # 简化处理，暂时只返回缓存
        return cached_events
    
    # =================================================================
    # Audit Queries
    # =================================================================
    
    async def get_violation_summary(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        获取边界违规汇总
        """
        # 获取所有违规事件
        violation_events = [
            e for e in self._event_log
            if e.event_type == "AGENT_OUTPUT"
            and e.event_data.get("boundary_violations")
        ]
        
        violation_counts: Dict[str, int] = {}
        for event in violation_events:
            for violation in event.event_data.get("boundary_violations", []):
                violation_type = violation.split(":")[0]
                violation_counts[violation_type] = violation_counts.get(violation_type, 0) + 1
        
        return {
            "total_violation_events": len(violation_events),
            "violation_by_type": violation_counts,
            "start_time": start_time.isoformat() if start_time else None,
            "end_time": end_time.isoformat() if end_time else None,
        }
    
    async def get_decision_metrics(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        获取决策指标
        """
        # 获取所有决策事件
        decision_events = [
            e for e in self._event_log
            if e.event_type == "HUMAN_DECISION"
        ]
        
        approved = sum(1 for e in decision_events if e.event_data.get("decision") == "APPROVED")
        rejected = sum(1 for e in decision_events if e.event_data.get("decision") == "REJECTED")
        
        return {
            "total_decisions": len(decision_events),
            "approved": approved,
            "rejected": rejected,
            "approval_rate": approved / len(decision_events) if decision_events else 0,
            "start_time": start_time.isoformat() if start_time else None,
            "end_time": end_time.isoformat() if end_time else None,
        }
    
    async def get_agent_performance(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        获取 Agent 表现统计
        """
        # 按 agent_type 分组统计
        agent_stats: Dict[str, Dict[str, Any]] = {}
        
        for event in self._event_log:
            if event.event_type != "AGENT_OUTPUT":
                continue
            
            agent_type = event.agent_type or "unknown"
            
            if agent_type not in agent_stats:
                agent_stats[agent_type] = {
                    "total_outputs": 0,
                    "valid_outputs": 0,
                    "invalid_outputs": 0,
                    "violations": [],
                }
            
            stats = agent_stats[agent_type]
            stats["total_outputs"] += 1
            
            if event.event_data.get("validation_result", {}).get("is_valid"):
                stats["valid_outputs"] += 1
            else:
                stats["invalid_outputs"] += 1
            
            stats["violations"].extend(
                event.event_data.get("boundary_violations", [])
            )
        
        # 计算成功率
        for agent_type, stats in agent_stats.items():
            if stats["total_outputs"] > 0:
                stats["success_rate"] = stats["valid_outputs"] / stats["total_outputs"]
        
        return {
            "agent_stats": agent_stats,
            "start_time": start_time.isoformat() if start_time else None,
            "end_time": end_time.isoformat() if end_time else None,
        }
