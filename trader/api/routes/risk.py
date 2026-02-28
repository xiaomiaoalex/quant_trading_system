"""
Risk API Routes
==============
Risk limits management endpoints (versioned).
"""
from typing import Optional
from fastapi import APIRouter, Query, Response

from trader.api.models.schemas import (
    VersionedConfig,
    VersionedConfigUpsertRequest,
    RiskEventIngestRequest,
    ActionResult,
)
from trader.services import RiskService

router = APIRouter(tags=["Risk"])


@router.get("/v1/risk/limits", response_model=Optional[VersionedConfig])
async def get_risk_limits(scope: str = Query("GLOBAL", description="Risk scope: GLOBAL or per account/strategy")):
    """
    Get latest risk limits.

    Returns the latest risk limits for the specified scope.
    """
    service = RiskService()
    return service.get_limits(scope)


@router.post("/v1/risk/limits", response_model=VersionedConfig)
async def set_risk_limits(request: VersionedConfigUpsertRequest):
    """
    Set new risk limits.

    Creates a new version of risk limits.
    """
    service = RiskService()
    return service.set_limits(request)


@router.post("/v1/risk/events", response_model=ActionResult)
async def ingest_risk_event(request: RiskEventIngestRequest, response: Response):
    """
    Ingest risk event with dedup_key idempotency.

    - Returns 201 when a new dedup_key is accepted.
    - Returns 409 when dedup_key already exists (idempotent duplicate).
    """
    service = RiskService()
    created = service.ingest_event(request)
    if created:
        response.status_code = 201
        return ActionResult(ok=True, message="risk event accepted")

    response.status_code = 409
    return ActionResult(ok=True, message="risk event duplicate")
