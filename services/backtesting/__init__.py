"""
Backtesting Services - 回测服务模块
=================================

提供批量回测和相关功能。
"""

from services.backtesting.backtest_batch_job import (
    BacktestBatchJob,
    BatchBacktestResult,
    BatchJobStatus,
    SingleBacktestResult,
    SingleBacktestStatus,
    summarize_batch_result,
)

__all__ = [
    "BacktestBatchJob",
    "BatchBacktestResult",
    "BatchJobStatus", 
    "SingleBacktestResult",
    "SingleBacktestStatus",
    "summarize_batch_result",
]
