"""
Backtest API Routes
==================
Backtest run management endpoints.
"""
from fastapi import APIRouter, HTTPException, Path

from trader.api.models.schemas import BacktestRequest, BacktestRun
from trader.services import BacktestService

router = APIRouter(tags=["Backtests"])


@router.post("/v1/backtests", response_model=BacktestRun, status_code=202)
async def create_backtest(request: BacktestRequest):
    """
    Trigger a backtest run.

    Creates and starts a new backtest run.
    """
    service = BacktestService()
    return service.create_backtest(request)


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
