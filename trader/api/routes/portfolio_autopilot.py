from __future__ import annotations

from fastapi import APIRouter, Query

from trader.api.models.schemas import (
    PortfolioAutopilotDecision,
    PortfolioAutopilotSnapshot,
    PortfolioAutopilotTickRequest,
)
from trader.services.portfolio_autopilot import PortfolioRuntimeController
from trader.storage.in_memory import get_storage

router = APIRouter(tags=["PortfolioAutopilot"])


@router.get("/v1/portfolio-autopilot/snapshot", response_model=PortfolioAutopilotSnapshot)
async def get_autopilot_snapshot():
    return PortfolioRuntimeController().snapshot()


@router.post("/v1/portfolio-autopilot/tick", response_model=PortfolioAutopilotSnapshot)
async def tick_autopilot(request: PortfolioAutopilotTickRequest):
    return PortfolioRuntimeController().tick(request)


@router.get("/v1/portfolio-autopilot/decisions", response_model=list[PortfolioAutopilotDecision])
async def list_autopilot_decisions(limit: int = Query(100, ge=1, le=500)):
    return [
        PortfolioAutopilotDecision(**item)
        for item in get_storage().list_autopilot_decisions(limit=limit)
    ]
