"""
Backtest API Routes
===================
Backtest run management endpoints (Task 9.4, 9.5).
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Path, Query

from trader.api.models.schemas import BacktestRequest, BacktestRun, BacktestReport
from trader.services import BacktestService

router = APIRouter(tags=["Backtests"])


@router.get("/v1/backtests", response_model=list[BacktestRun])
async def list_backtests(
    status: Optional[str] = Query(None, description="Filter by status (RUNNING/COMPLETED/FAILED)"),
    strategy_id: Optional[str] = Query(None, description="Filter by strategy ID"),
    limit: int = Query(100, le=500, description="Max results"),
):
    """
    List backtest runs (Task 9.4)。
    
    支持按 status 和 strategy_id 筛选。
    """
    service = BacktestService()
    backtests = service.list_backtests(status=status, strategy_id=strategy_id, limit=limit)
    return backtests


@router.post("/v1/backtests", response_model=BacktestRun, status_code=202)
async def create_backtest(request: BacktestRequest):
    """
    Trigger a backtest run.

    Creates and starts a new backtest run.
    """
    service = BacktestService()
    try:
        return service.create_backtest(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/v1/backtests/{run_id}", response_model=BacktestRun)
async def get_backtest(run_id: str = Path(..., description="Backtest run ID")):
    """
    Get backtest status/results.

    Returns the status and results of a backtest run.
    """
    service = BacktestService()
    backtest = service.get_backtest(run_id)
    if not backtest:
        raise HTTPException(status_code=404, detail=f"Backtest run {run_id} not found")
    return backtest


@router.get("/v1/backtests/{run_id}/report", response_model=BacktestReport)
async def get_backtest_report(run_id: str = Path(..., description="Backtest run ID")):
    """
    Get backtest report details (Task 9.5)。
    
    返回标准化的回测报告，包含 returns/risk/trades/equity_curve。
    数据来源优先级：
    1. Artifact 存储（如果 backtest 有 artifact_ref）
    2. metrics 字段（如果包含完整数据）
    """
    service = BacktestService()
    backtest = service.get_backtest(run_id)
    if not backtest:
        raise HTTPException(status_code=404, detail=f"Backtest run {run_id} not found")
    
    # 尝试从 artifact 存储获取完整报告数据
    returns = None
    risk = None
    trades = None
    equity_curve = None
    
    if backtest.artifact_ref and backtest.artifact_ref.startswith("backtest_report:"):
        try:
            from trader.storage.artifact_storage import get_artifact_storage
            artifact_storage = get_artifact_storage()
            report_data = artifact_storage.load_report(run_id)
            if report_data:
                returns = report_data.get("returns")
                risk = report_data.get("risk")
                trades = report_data.get("trades")
                equity_curve = report_data.get("equity_curve")
        except Exception:
            # Artifact 存储不可用，继续尝试 metrics
            pass
    
    # 从 metrics 中提取报告详情（降级方案）
    if returns is None and backtest.metrics:
        metrics = backtest.metrics
        if "returns" in metrics:
            returns = metrics["returns"]
        elif "total_return" in metrics or "sharpe_ratio" in metrics:
            returns = {
                "total_return": metrics.get("total_return"),
                "total_return_pct": metrics.get("total_return_pct"),
                "annualized_return": metrics.get("annualized_return"),
                "sharpe_ratio": metrics.get("sharpe_ratio"),
            }
        
        if "risk" in metrics:
            risk = metrics["risk"]
        elif "max_drawdown" in metrics or "volatility" in metrics:
            risk = {
                "max_drawdown": metrics.get("max_drawdown"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "volatility": metrics.get("volatility"),
                "var_95": metrics.get("var_95"),
            }
        
        if "trades" in metrics:
            trades = metrics["trades"]
        
        if "equity_curve" in metrics:
            equity_curve = metrics["equity_curve"]
    
    # 构建报告
    return BacktestReport(
        run_id=backtest.run_id,
        status=backtest.status,
        strategy_id=backtest.strategy_id,
        version=backtest.version,
        symbols=backtest.symbols,
        start_ts_ms=backtest.start_ts_ms,
        end_ts_ms=backtest.end_ts_ms,
        created_at=backtest.created_at,
        started_at=backtest.started_at,
        finished_at=backtest.finished_at,
        error=backtest.error,
        metrics=backtest.metrics,
        artifact_ref=backtest.artifact_ref,
        returns=returns,
        risk=risk,
        trades=trades,
        equity_curve=equity_curve,
    )
