"""
Portfolio API Routes
===================
Position and PnL query endpoints.
"""
from typing import Optional
from fastapi import APIRouter, Query

from trader.api.models.schemas import PositionView, PnlView
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
