"""
Portfolio API Routes
===================
Position and PnL query endpoints.
"""
from typing import Optional
from fastapi import APIRouter, Query, Path

from trader.api.models.schemas import (
    PositionView, PnlView,
    StrategyPositionView, LotView, PositionBreakdown,
)
from trader.services import PortfolioService

router = APIRouter(tags=["Portfolio"])


@router.get("/v1/portfolio/positions", response_model=list[PositionView])
async def list_positions(
    account_id: Optional[str] = Query(None, description="Filter by account ID"),
    venue: Optional[str] = Query(None, description="Filter by venue"),
):
    """
    Get positions.

    Returns the projected positions for the specified filters.
    """
    service = PortfolioService()
    return service.list_positions(account_id, venue)


@router.get("/v1/portfolio/pnl", response_model=PnlView)
async def get_pnl(
    account_id: Optional[str] = Query(None, description="Filter by account ID"),
    venue: Optional[str] = Query(None, description="Filter by venue"),
):
    """
    Get PnL summary.

    Returns the PnL summary for the specified filters.
    """
    service = PortfolioService()
    return service.get_pnl(account_id, venue)


# ==================== 策略级持仓（Batch 3 新增） ====================

@router.get("/v1/portfolio/strategy-positions", response_model=list[StrategyPositionView])
async def get_strategy_positions(
    strategy_id: Optional[str] = Query(None, description="Filter by strategy ID"),
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
):
    """
    获取策略级持仓列表。

    返回每个 (strategy_id, symbol) 的持仓汇总，含 Lot 数量、avg_cost、PnL。
    """
    service = PortfolioService()
    return service.get_strategy_positions(strategy_id=strategy_id, symbol=symbol)


@router.get("/v1/portfolio/positions/{symbol}/breakdown", response_model=PositionBreakdown)
async def get_position_breakdown(
    symbol: str = Path(..., description="Trading symbol, e.g. BTCUSDT"),
):
    """
    获取单个标的的三层持仓分解。

    返回账户总持仓 + 策略分解 + 对账状态。
    """
    service = PortfolioService()
    return service.get_position_breakdown(symbol)


@router.get("/v1/portfolio/positions/{symbol}/lots", response_model=list[LotView])
async def get_position_lots(
    symbol: str = Path(..., description="Trading symbol"),
    strategy_id: str = Query(..., description="Strategy ID"),
):
    """
    获取某策略某标的的所有 Lot 明细（含 open 和 closed）。

    展示每笔买入的 original_qty、remaining_qty、fill_price、fee、realized_pnl。
    """
    service = PortfolioService()
    return service.get_lots(strategy_id=strategy_id, symbol=symbol)
