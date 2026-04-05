"""
Batch Backtest Job - 批量回测任务
=================================

用于对多个 Portfolio Proposal 进行批量回测。

设计原则：
1. 并行执行多个回测任务
2. 跟踪每个任务的进度和状态
3. 汇总回测结果
4. 支持成本压测（1x/1.5x/2x）

使用方式：
    batch_job = BacktestBatchJob(backtest_engine=engine)
    results = await batch_job.run_batch(portfolios, cost_multiplier=1.5)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from insight.committee.schemas import PortfolioProposal

logger = logging.getLogger(__name__)


class BatchJobStatus(str, Enum):
    """批量任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"  # 部分成功
    FAILED = "failed"


class SingleBacktestStatus(str, Enum):
    """单个回测状态"""
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class SingleBacktestResult:
    """单个回测结果"""
    portfolio_id: str
    status: SingleBacktestStatus
    # 原始指标
    total_return: Optional[Decimal] = None
    sharpe_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    win_rate: Optional[float] = None
    # 成本压测后指标
    return_after_cost_1x: Optional[Decimal] = None
    return_after_cost_1_5x: Optional[Decimal] = None
    return_after_cost_2x: Optional[Decimal] = None
    # 错误信息
    error_message: Optional[str] = None
    # 时间戳
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


@dataclass(slots=True)
class BatchBacktestResult:
    """批量回测结果"""
    batch_id: str
    status: BatchJobStatus
    total_portfolios: int
    successful: int
    failed: int
    skipped: int
    results: List[SingleBacktestResult]
    cost_multiplier: float
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    @property
    def success_rate(self) -> float:
        """成功率"""
        if self.total_portfolios == 0:
            return 0.0
        return self.successful / self.total_portfolios

    @property
    def passed_1_5x_cost_stress(self) -> int:
        """1.5x 成本压测后通过数量"""
        return sum(
            1 for r in self.results
            if r.status == SingleBacktestStatus.PASSED
            and r.return_after_cost_1_5x is not None
            and r.return_after_cost_1_5x > 0
        )


class BacktestBatchJob:
    """
    批量回测任务执行器
    
    职责：
    1. 批量执行多个 Portfolio 的回测
    2. 支持成本压测（1x/1.5x/2x）
    3. 并行执行提高效率
    4. 跟踪进度和状态
    """

    def __init__(
        self,
        backtest_engine: Optional[Any] = None,
        max_concurrency: int = 3,
    ):
        """
        初始化批量回测任务
        
        Args:
            backtest_engine: 回测引擎（需实现 BacktestEnginePort 接口）
            max_concurrency: 最大并发数
        """
        self._engine = backtest_engine
        self._max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def run_batch(
        self,
        portfolios: List[PortfolioProposal],
        cost_multiplier: float = 1.0,
    ) -> BatchBacktestResult:
        """
        执行批量回测
        
        Args:
            portfolios: Portfolio Proposal 列表
            cost_multiplier: 成本倍数（1.0=正常, 1.5=压测, 2.0=极端压测）
            
        Returns:
            BatchBacktestResult: 批量回测结果
        """
        batch_id = str(uuid.uuid4())[:8]
        logger.info(
            f"Starting batch backtest: batch_id={batch_id}, "
            f"portfolios={len(portfolios)}, cost_multiplier={cost_multiplier}"
        )

        # 使用信号量控制并发
        async def run_with_semaphore(
            portfolio: PortfolioProposal,
        ) -> SingleBacktestResult:
            async with self._semaphore:
                return await self._run_single_backtest(portfolio, cost_multiplier)

        # 并行执行所有回测
        tasks = [
            run_with_semaphore(portfolio)
            for portfolio in portfolios
        ]
        
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理异常结果
        processed_results: List[SingleBacktestResult] = []
        for i, result in enumerate(raw_results):
            if isinstance(result, Exception):
                processed_results.append(
                    SingleBacktestResult(
                        portfolio_id=portfolios[i].proposal_id,
                        status=SingleBacktestStatus.FAILED,
                        error_message=str(result),
                    )
                )
            elif isinstance(result, SingleBacktestResult):
                processed_results.append(result)
            else:
                processed_results.append(
                    SingleBacktestResult(
                        portfolio_id=portfolios[i].proposal_id,
                        status=SingleBacktestStatus.FAILED,
                        error_message=f"Unexpected result type: {type(result)}",
                    )
                )

        # 统计结果
        successful = sum(
            1 for r in processed_results
            if r.status == SingleBacktestStatus.PASSED
        )
        failed = sum(
            1 for r in processed_results
            if r.status == SingleBacktestStatus.FAILED
        )
        skipped = sum(
            1 for r in processed_results
            if r.status == SingleBacktestStatus.SKIPPED
        )

        # 确定整体状态
        if failed == 0 and skipped == 0:
            status = BatchJobStatus.COMPLETED
        elif successful > 0:
            status = BatchJobStatus.PARTIAL
        else:
            status = BatchJobStatus.FAILED

        batch_result = BatchBacktestResult(
            batch_id=batch_id,
            status=status,
            total_portfolios=len(portfolios),
            successful=successful,
            failed=failed,
            skipped=skipped,
            results=processed_results,
            cost_multiplier=cost_multiplier,
            completed_at=datetime.now(timezone.utc),
        )

        logger.info(
            f"Batch backtest completed: batch_id={batch_id}, "
            f"successful={successful}/{len(portfolios)}, "
            f"passed_1.5x_cost_stress={batch_result.passed_1_5x_cost_stress}"
        )

        return batch_result

    async def _run_single_backtest(
        self,
        portfolio: PortfolioProposal,
        cost_multiplier: float,
    ) -> SingleBacktestResult:
        """
        执行单个 Portfolio 回测
        
        Args:
            portfolio: Portfolio Proposal
            cost_multiplier: 成本倍数
            
        Returns:
            SingleBacktestResult: 回测结果
        """
        started_at = datetime.now(timezone.utc)
        
        try:
            # 如果没有回测引擎，返回模拟结果（用于测试）
            if self._engine is None:
                return await self._run_mock_backtest(portfolio, cost_multiplier, started_at)
            
            # 执行真实回测
            # TODO: 集成 Lean 回测引擎
            raise NotImplementedError("Lean engine integration pending")
            
        except Exception as e:
            logger.error(f"Backtest failed for {portfolio.proposal_id}: {e}")
            return SingleBacktestResult(
                portfolio_id=portfolio.proposal_id,
                status=SingleBacktestStatus.FAILED,
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )

    async def _run_mock_backtest(
        self,
        portfolio: PortfolioProposal,
        cost_multiplier: float,
        started_at: datetime,
    ) -> SingleBacktestResult:
        """
        运行模拟回测（用于测试或引擎不可用时）
        
        Args:
            portfolio: Portfolio Proposal
            cost_multiplier: 成本倍数
            started_at: 开始时间
            
        Returns:
            SingleBacktestResult: 模拟回测结果
        """
        # 模拟回测延迟
        await asyncio.sleep(0.1)
        
        # 生成模拟指标
        total_return = Decimal("0.15")  # 15% 收益
        sharpe_ratio = 1.5
        max_drawdown = 0.12  # 12% 最大回撤
        win_rate = 0.55  # 55% 胜率
        
        # 计算成本压测后收益
        # 假设成本占收益的 30%
        cost_ratio = 0.30 * cost_multiplier
        return_after_cost = total_return * Decimal(str(1 - cost_ratio))
        
        # 判断是否通过
        passed = return_after_cost > 0 and sharpe_ratio >= 1.0
        
        return SingleBacktestResult(
            portfolio_id=portfolio.proposal_id,
            status=SingleBacktestStatus.PASSED if passed else SingleBacktestStatus.FAILED,
            total_return=total_return,
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            return_after_cost_1x=return_after_cost if cost_multiplier == 1.0 else None,
            return_after_cost_1_5x=return_after_cost if cost_multiplier == 1.5 else None,
            return_after_cost_2x=return_after_cost if cost_multiplier == 2.0 else None,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
        )


# ============================================================================
# 辅助函数
# ============================================================================

def summarize_batch_result(result: BatchBacktestResult) -> Dict[str, Any]:
    """
    汇总批量回测结果
    
    Args:
        result: 批量回测结果
        
    Returns:
        Dict: 汇总信息
    """
    return {
        "batch_id": result.batch_id,
        "status": result.status.value,
        "total": result.total_portfolios,
        "successful": result.successful,
        "failed": result.failed,
        "skipped": result.skipped,
        "success_rate": f"{result.success_rate:.1%}",
        "passed_1_5x_cost_stress": result.passed_1_5x_cost_stress,
        "cost_multiplier": result.cost_multiplier,
    }
