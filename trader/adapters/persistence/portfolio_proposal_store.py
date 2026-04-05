"""
Portfolio Proposal Store - 组合提案持久化适配器
=============================================

此模块负责组合提案的持久化存储，采用 PG-first 策略。

设计原则：
1. PostgreSQL 优先，内存回退
2. 所有操作幂等
3. 完整审计追踪
4. Event Sourcing 模式
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from trader.adapters.persistence.postgres import (
    PostgreSQLStorage,
    is_postgres_available,
)

logger = logging.getLogger(__name__)


class DecimalEncoder(json.JSONEncoder):
    """JSON 编码器，支持 Decimal 类型"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def decimal_decoder(dct: Dict[str, Any]) -> Dict[str, Any]:
    """JSON 解码器，将字符串转回 Decimal（对于特定字段）"""
    decimal_fields = {
        'capital_cap', 'max_position_size', 'total_capital_estimate'
    }
    for key in list(dct.keys()):
        if key in decimal_fields and isinstance(dct[key], str):
            dct[key] = Decimal(dct[key])
        elif isinstance(dct[key], dict):
            dct[key] = decimal_decoder(dct[key])
    return dct


def parse_datetime(value: Any) -> Optional[datetime]:
    """
    将字符串或 datetime 转换为 datetime 对象
    
    Args:
        value: 字符串或 datetime 对象
        
    Returns:
        datetime 对象或 None
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


class PortfolioProposalStore:
    """
    组合提案持久化存储
    
    职责：
    - 存储 CommitteeRun 记录
    - 存储 SleeveProposal 记录
    - 存储 PortfolioProposal 记录
    - 存储 ReviewReport 记录
    
    存储策略：
    - PostgreSQL 优先
    - 内存回退（仅用于开发/测试）
    """

    def __init__(self, postgres_storage: Optional[PostgreSQLStorage] = None):
        self._postgres: Optional[PostgreSQLStorage] = postgres_storage
        self._use_postgres = False
        self._init_lock: Optional[asyncio.Lock] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def _ensure_postgres(self) -> bool:
        """确保 PostgreSQL 可用"""
        if self._postgres is not None:
            return True

        current_loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not current_loop:
            self._init_lock = asyncio.Lock()
            self._loop = current_loop

        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

        async with self._init_lock:
            if self._postgres is None:
                if is_postgres_available():
                    try:
                        self._postgres = PostgreSQLStorage()
                        await self._postgres.connect()
                        self._use_postgres = True
                        logger.info("PostgreSQL storage initialized for portfolio_proposal_store")
                        return True
                    except Exception as e:
                        logger.warning(f"Failed to initialize PostgreSQL storage: {e}")
                        self._use_postgres = False
                        return False
        return self._use_postgres

    # =========================================================================
    # CommitteeRun 操作
    # =========================================================================

    async def save_committee_run(self, run: Dict[str, Any]) -> str:
        """
        保存 CommitteeRun 记录
        
        Args:
            run: CommitteeRun 字典
            
        Returns:
            run_id
        """
        if await self._ensure_postgres():
            return await self._save_committee_run_pg(run)
        else:
            return await self._save_committee_run_memory(run)

    async def _save_committee_run_pg(self, run: Dict[str, Any]) -> str:
        """PostgreSQL 保存 CommitteeRun"""
        run_id = run.get('run_id')
        
        query = """
            INSERT INTO committee_runs (
                run_id, research_request, context_package_version,
                sleeve_proposals_json, portfolio_proposal_json,
                review_results_json, human_decision, approver,
                decision_reason, backtest_job_id, final_status,
                feature_version, prompt_version, trace_id, status,
                created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
            ON CONFLICT (run_id) DO UPDATE SET
                research_request = EXCLUDED.research_request,
                context_package_version = EXCLUDED.context_package_version,
                sleeve_proposals_json = EXCLUDED.sleeve_proposals_json,
                portfolio_proposal_json = EXCLUDED.portfolio_proposal_json,
                review_results_json = EXCLUDED.review_results_json,
                human_decision = EXCLUDED.human_decision,
                approver = EXCLUDED.approver,
                decision_reason = EXCLUDED.decision_reason,
                backtest_job_id = EXCLUDED.backtest_job_id,
                final_status = EXCLUDED.final_status,
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
        """
        
        async with self._postgres.acquire() as conn:
            await conn.execute(
                query,
                run_id,
                run.get('research_request'),
                run.get('context_package_version'),
                json.dumps(run.get('sleeve_proposals', []), cls=DecimalEncoder),
                json.dumps(run.get('portfolio_proposal'), cls=DecimalEncoder) if run.get('portfolio_proposal') else None,
                json.dumps(run.get('review_results', []), cls=DecimalEncoder),
                run.get('human_decision'),
                run.get('approver'),
                run.get('decision_reason'),
                run.get('backtest_job_id'),
                run.get('final_status'),
                run.get('feature_version'),
                run.get('prompt_version'),
                run.get('trace_id'),
                run.get('status'),
                parse_datetime(run.get('created_at')),
                datetime.now(timezone.utc),
            )
        
        return run_id

    async def _save_committee_run_memory(self, run: Dict[str, Any]) -> str:
        """内存保存 CommitteeRun（仅开发/测试用）"""
        # 简单存储到模块级变量
        if not hasattr(self, '_memory_runs'):
            self._memory_runs: Dict[str, Dict[str, Any]] = {}
        
        self._memory_runs[run.get('run_id')] = run
        return run.get('run_id')

    async def get_committee_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """获取 CommitteeRun 记录"""
        if await self._ensure_postgres():
            return await self._get_committee_run_pg(run_id)
        else:
            return await self._get_committee_run_memory(run_id)

    async def _get_committee_run_pg(self, run_id: str) -> Optional[Dict[str, Any]]:
        """PostgreSQL 获取 CommitteeRun"""
        query = """
            SELECT run_id, research_request, context_package_version,
                   sleeve_proposals_json, portfolio_proposal_json,
                   review_results_json, human_decision, approver,
                   decision_reason, backtest_job_id, final_status,
                   feature_version, prompt_version, trace_id, status,
                   created_at, updated_at
            FROM committee_runs
            WHERE run_id = $1
        """
        
        async with self._postgres.acquire() as conn:
            row = await conn.fetchrow(query, run_id)
        
        if row is None:
            return None
        
        return self._row_to_committee_run(row)

    def _row_to_committee_run(self, row: Any) -> Dict[str, Any]:
        """将数据库行转换为 CommitteeRun 字典"""
        run = {
            'run_id': row[0],
            'research_request': row[1],
            'context_package_version': row[2],
            'sleeve_proposals': json.loads(row[3]) if row[3] else [],
            'portfolio_proposal': json.loads(row[4]) if row[4] else None,
            'review_results': json.loads(row[5]) if row[5] else [],
            'human_decision': row[6],
            'approver': row[7],
            'decision_reason': row[8],
            'backtest_job_id': row[9],
            'final_status': row[10],
            'feature_version': row[11],
            'prompt_version': row[12],
            'trace_id': row[13],
            'status': row[14],
            'created_at': row[15].isoformat() if row[15] else None,
            'updated_at': row[16].isoformat() if row[16] else None,
        }
        return decimal_decoder(run)

    async def _get_committee_run_memory(self, run_id: str) -> Optional[Dict[str, Any]]:
        """内存获取 CommitteeRun"""
        if not hasattr(self, '_memory_runs'):
            self._memory_runs: Dict[str, Dict[str, Any]] = {}
        return self._memory_runs.get(run_id)

    async def list_committee_runs(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """列出 CommitteeRun 记录"""
        if await self._ensure_postgres():
            return await self._list_committee_runs_pg(status, limit, offset)
        else:
            return await self._list_committee_runs_memory(status, limit, offset)

    async def _list_committee_runs_pg(
        self,
        status: Optional[str],
        limit: int,
        offset: int
    ) -> List[Dict[str, Any]]:
        """PostgreSQL 列出 CommitteeRun"""
        async with self._postgres.acquire() as conn:
            if status:
                query = """
                    SELECT run_id, research_request, context_package_version,
                           sleeve_proposals_json, portfolio_proposal_json,
                           review_results_json, human_decision, approver,
                           decision_reason, backtest_job_id, final_status,
                           feature_version, prompt_version, trace_id, status,
                           created_at, updated_at
                    FROM committee_runs
                    WHERE status = $1
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3
                """
                rows = await conn.fetch(query, status, limit, offset)
            else:
                query = """
                    SELECT run_id, research_request, context_package_version,
                           sleeve_proposals_json, portfolio_proposal_json,
                           review_results_json, human_decision, approver,
                           decision_reason, backtest_job_id, final_status,
                           feature_version, prompt_version, trace_id, status,
                           created_at, updated_at
                    FROM committee_runs
                    ORDER BY created_at DESC
                    LIMIT $1 OFFSET $2
                """
                rows = await conn.fetch(query, limit, offset)
        
        return [self._row_to_committee_run(row) for row in rows]

    async def _list_committee_runs_memory(
        self,
        status: Optional[str],
        limit: int,
        offset: int
    ) -> List[Dict[str, Any]]:
        """内存列出 CommitteeRun"""
        if not hasattr(self, '_memory_runs'):
            self._memory_runs: Dict[str, Dict[str, Any]] = {}
        
        runs = list(self._memory_runs.values())
        if status:
            runs = [r for r in runs if r.get('status') == status]
        
        runs.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        return runs[offset:offset + limit]

    # =========================================================================
    # SleeveProposal 操作
    # =========================================================================

    async def save_sleeve_proposal(self, proposal: Dict[str, Any]) -> str:
        """保存 SleeveProposal"""
        if await self._ensure_postgres():
            return await self._save_sleeve_proposal_pg(proposal)
        else:
            return await self._save_sleeve_proposal_memory(proposal)

    async def _save_sleeve_proposal_pg(self, proposal: Dict[str, Any]) -> str:
        """PostgreSQL 保存 SleeveProposal"""
        proposal_id = proposal.get('proposal_id')
        
        query = """
            INSERT INTO sleeve_proposals (
                proposal_id, specialist_type, hypothesis,
                required_features, regime, failure_modes,
                cost_assumptions_json, evidence_refs,
                feature_version, prompt_version, trace_id,
                status, content_hash, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            ON CONFLICT (proposal_id) DO UPDATE SET
                specialist_type = EXCLUDED.specialist_type,
                hypothesis = EXCLUDED.hypothesis,
                required_features = EXCLUDED.required_features,
                regime = EXCLUDED.regime,
                failure_modes = EXCLUDED.failure_modes,
                cost_assumptions_json = EXCLUDED.cost_assumptions_json,
                evidence_refs = EXCLUDED.evidence_refs,
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
        """
        
        async with self._postgres.acquire() as conn:
            await conn.execute(
                query,
                proposal_id,
                proposal.get('specialist_type'),
                proposal.get('hypothesis'),
                json.dumps(proposal.get('required_features', [])),
                proposal.get('regime'),
                json.dumps(proposal.get('failure_modes', [])),
                json.dumps(proposal.get('cost_assumptions', {}), cls=DecimalEncoder),
                json.dumps(proposal.get('evidence_refs', [])),
                proposal.get('feature_version'),
                proposal.get('prompt_version'),
                proposal.get('trace_id'),
                proposal.get('status'),
                proposal.get('content_hash'),
                parse_datetime(proposal.get('created_at')),
                datetime.now(timezone.utc),
            )
        
        return proposal_id

    async def _save_sleeve_proposal_memory(self, proposal: Dict[str, Any]) -> str:
        """内存保存 SleeveProposal"""
        if not hasattr(self, '_memory_sleeves'):
            self._memory_sleeves: Dict[str, Dict[str, Any]] = {}
        
        self._memory_sleeves[proposal.get('proposal_id')] = proposal
        return proposal.get('proposal_id')

    async def get_sleeve_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """获取 SleeveProposal"""
        if await self._ensure_postgres():
            return await self._get_sleeve_proposal_pg(proposal_id)
        else:
            return await self._get_sleeve_proposal_memory(proposal_id)

    async def _get_sleeve_proposal_pg(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """PostgreSQL 获取 SleeveProposal"""
        query = """
            SELECT proposal_id, specialist_type, hypothesis,
                   required_features, regime, failure_modes,
                   cost_assumptions_json, evidence_refs,
                   feature_version, prompt_version, trace_id,
                   status, content_hash, created_at, updated_at
            FROM sleeve_proposals
            WHERE proposal_id = $1
        """
        
        async with self._postgres.acquire() as conn:
            row = await conn.fetchrow(query, proposal_id)
        
        if row is None:
            return None
        
        return {
            'proposal_id': row[0],
            'specialist_type': row[1],
            'hypothesis': row[2],
            'required_features': json.loads(row[3]) if row[3] else [],
            'regime': row[4],
            'failure_modes': json.loads(row[5]) if row[5] else [],
            'cost_assumptions': json.loads(row[6]) if row[6] else {},
            'evidence_refs': json.loads(row[7]) if row[7] else [],
            'feature_version': row[8],
            'prompt_version': row[9],
            'trace_id': row[10],
            'status': row[11],
            'content_hash': row[12],
            'created_at': row[13].isoformat() if row[13] else None,
            'updated_at': row[14].isoformat() if row[14] else None,
        }

    async def _get_sleeve_proposal_memory(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """内存获取 SleeveProposal"""
        if not hasattr(self, '_memory_sleeves'):
            self._memory_sleeves: Dict[str, Dict[str, Any]] = {}
        return self._memory_sleeves.get(proposal_id)

    # =========================================================================
    # PortfolioProposal 操作
    # =========================================================================

    async def save_portfolio_proposal(self, proposal: Dict[str, Any]) -> str:
        """保存 PortfolioProposal"""
        if await self._ensure_postgres():
            return await self._save_portfolio_proposal_pg(proposal)
        else:
            return await self._save_portfolio_proposal_memory(proposal)

    async def _save_portfolio_proposal_pg(self, proposal: Dict[str, Any]) -> str:
        """PostgreSQL 保存 PortfolioProposal"""
        proposal_id = proposal.get('proposal_id')
        
        query = """
            INSERT INTO portfolio_proposals (
                proposal_id, sleeves_json, capital_caps_json,
                regime_conditions_json, conflict_priorities_json,
                risk_explanation, evaluation_task_id,
                feature_version, prompt_version, trace_id,
                status, content_hash, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            ON CONFLICT (proposal_id) DO UPDATE SET
                sleeves_json = EXCLUDED.sleeves_json,
                capital_caps_json = EXCLUDED.capital_caps_json,
                regime_conditions_json = EXCLUDED.regime_conditions_json,
                conflict_priorities_json = EXCLUDED.conflict_priorities_json,
                risk_explanation = EXCLUDED.risk_explanation,
                evaluation_task_id = EXCLUDED.evaluation_task_id,
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
        """
        
        async with self._postgres.acquire() as conn:
            await conn.execute(
                query,
                proposal_id,
                json.dumps(proposal.get('sleeves', []), cls=DecimalEncoder),
                json.dumps(proposal.get('capital_caps', {}), cls=DecimalEncoder),
                json.dumps(proposal.get('regime_conditions', {}), cls=DecimalEncoder),
                json.dumps(proposal.get('conflict_priorities', []), cls=DecimalEncoder),
                proposal.get('risk_explanation'),
                proposal.get('evaluation_task_id'),
                proposal.get('feature_version'),
                proposal.get('prompt_version'),
                proposal.get('trace_id'),
                proposal.get('status'),
                proposal.get('content_hash'),
                parse_datetime(proposal.get('created_at')),
                datetime.now(timezone.utc),
            )
        
        return proposal_id

    async def _save_portfolio_proposal_memory(self, proposal: Dict[str, Any]) -> str:
        """内存保存 PortfolioProposal"""
        if not hasattr(self, '_memory_portfolios'):
            self._memory_portfolios: Dict[str, Dict[str, Any]] = {}
        
        self._memory_portfolios[proposal.get('proposal_id')] = proposal
        return proposal.get('proposal_id')

    # =========================================================================
    # ReviewReport 操作
    # =========================================================================

    async def save_review_report(self, report: Dict[str, Any]) -> str:
        """保存 ReviewReport"""
        if await self._ensure_postgres():
            return await self._save_review_report_pg(report)
        else:
            return await self._save_review_report_memory(report)

    async def _save_review_report_pg(self, report: Dict[str, Any]) -> str:
        """PostgreSQL 保存 ReviewReport"""
        report_id = report.get('report_id')
        
        query = """
            INSERT INTO review_reports (
                report_id, proposal_id, reviewer_type,
                verdict, concerns, suggestions,
                orthogonality_score, risk_score, cost_score,
                feature_version, prompt_version, trace_id,
                created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (report_id) DO UPDATE SET
                verdict = EXCLUDED.verdict,
                concerns = EXCLUDED.concerns,
                suggestions = EXCLUDED.suggestions,
                orthogonality_score = EXCLUDED.orthogonality_score,
                risk_score = EXCLUDED.risk_score,
                cost_score = EXCLUDED.cost_score
        """
        
        async with self._postgres.acquire() as conn:
            await conn.execute(
                query,
                report_id,
                report.get('proposal_id'),
                report.get('reviewer_type'),
                report.get('verdict'),
                json.dumps(report.get('concerns', [])),
                json.dumps(report.get('suggestions', [])),
                report.get('orthogonality_score'),
                report.get('risk_score'),
                report.get('cost_score'),
                report.get('feature_version'),
                report.get('prompt_version'),
                report.get('trace_id'),
                parse_datetime(report.get('created_at')),
            )
        
        return report_id

    async def _save_review_report_memory(self, report: Dict[str, Any]) -> str:
        """内存保存 ReviewReport"""
        if not hasattr(self, '_memory_reviews'):
            self._memory_reviews: Dict[str, Dict[str, Any]] = {}
        
        self._memory_reviews[report.get('report_id')] = report
        return report.get('report_id')

    async def get_review_reports_for_proposal(self, proposal_id: str) -> List[Dict[str, Any]]:
        """获取某个 proposal 的所有 ReviewReport"""
        if await self._ensure_postgres():
            return await self._get_review_reports_pg(proposal_id)
        else:
            return await self._get_review_reports_memory(proposal_id)

    async def _get_review_reports_pg(self, proposal_id: str) -> List[Dict[str, Any]]:
        """PostgreSQL 获取 ReviewReport"""
        query = """
            SELECT report_id, proposal_id, reviewer_type,
                   verdict, concerns, suggestions,
                   orthogonality_score, risk_score, cost_score,
                   feature_version, prompt_version, trace_id,
                   created_at
            FROM review_reports
            WHERE proposal_id = $1
            ORDER BY created_at DESC
        """
        
        async with self._postgres.acquire() as conn:
            rows = await conn.fetch(query, proposal_id)
        
        return [
            {
                'report_id': row[0],
                'proposal_id': row[1],
                'reviewer_type': row[2],
                'verdict': row[3],
                'concerns': json.loads(row[4]) if row[4] else [],
                'suggestions': json.loads(row[5]) if row[5] else [],
                'orthogonality_score': row[6],
                'risk_score': row[7],
                'cost_score': row[8],
                'feature_version': row[9],
                'prompt_version': row[10],
                'trace_id': row[11],
                'created_at': row[12].isoformat() if row[12] else None,
            }
            for row in rows
        ]

    async def _get_review_reports_memory(self, proposal_id: str) -> List[Dict[str, Any]]:
        """内存获取 ReviewReport"""
        if not hasattr(self, '_memory_reviews'):
            self._memory_reviews: Dict[str, Dict[str, Any]] = {}
        
        return [
            r for r in self._memory_reviews.values()
            if r.get('proposal_id') == proposal_id
        ]
