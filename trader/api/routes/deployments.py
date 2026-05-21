"""
Deployment API Routes
====================
Deployment (run instance) management endpoints.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Path, Query

from trader.api.models.schemas import (
    ActionResult,
    Deployment,
    DeploymentCreateRequest,
    NAVPointSchema,
)
from trader.services import DeploymentService

router = APIRouter(tags=["Deployments"])


@router.get("/v1/deployments", response_model=List[Deployment])
async def list_deployments(
    status: Optional[str] = Query(None, description="Filter by status"),
    strategy_id: Optional[str] = Query(None, description="Filter by strategy ID"),
    account_id: Optional[str] = Query(None, description="Filter by account ID"),
    venue: Optional[str] = Query(None, description="Filter by venue"),
):
    """
    List deployments.

    Returns a list of deployments with optional filters.
    """
    service = DeploymentService()
    return service.list_deployments(status, strategy_id, account_id, venue)


@router.post("/v1/deployments", response_model=Deployment, status_code=201)
async def create_deployment(request: DeploymentCreateRequest):
    """
    Create a deployment.

    Creates a new deployment instance.
    """
    service = DeploymentService()
    return service.create_deployment(request)


@router.get("/v1/deployments/{deployment_id}", response_model=Deployment)
async def get_deployment(deployment_id: str = Path(..., description="Deployment ID")):
    """
    Get deployment.

    Returns a deployment by ID.
    """
    service = DeploymentService()
    deployment = service.get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail=f"Deployment {deployment_id} not found")
    return deployment


@router.post("/v1/deployments/{deployment_id}/start", response_model=ActionResult)
async def start_deployment(deployment_id: str = Path(..., description="Deployment ID")):
    """
    Start a deployment.

    Starts a deployment runner.
    """
    service = DeploymentService()
    return service.start_deployment(deployment_id)


@router.post("/v1/deployments/{deployment_id}/stop", response_model=ActionResult)
async def stop_deployment(deployment_id: str = Path(..., description="Deployment ID")):
    """
    Stop a deployment.

    Stops a running deployment.
    """
    service = DeploymentService()
    return service.stop_deployment(deployment_id)


@router.get("/v1/deployments/{deployment_id}/nav", response_model=List[NAVPointSchema])
async def get_deployment_nav(
    deployment_id: str = Path(..., description="Deployment ID"),
    since_ms: Optional[int] = Query(
        None, description="Filter points with timestamp_ms >= since_ms"
    ),
    limit: int = Query(500, ge=1, le=5000, description="Maximum number of points to return"),
):
    """
    Get NAV history for a deployment.

    Returns NAV time series from in-memory cache (falling back to PostgreSQL if empty).
    """
    from trader.storage.in_memory import get_storage

    storage = get_storage()
    series = storage.get_nav_series(deployment_id, since_ms=since_ms)

    # fallback: 若 deployment_id 与 strategy_id 不同，尝试用 strategy_id 查内存
    if not series:
        deployment = DeploymentService().get_deployment(deployment_id)
        if deployment and deployment.strategy_id != deployment_id:
            series = storage.get_nav_series(deployment.strategy_id, since_ms=since_ms)

    if not series:
        from trader.storage.nav_store import get_nav_series_pg

        series = await get_nav_series_pg(deployment_id, since_ms=since_ms, limit=limit)
        # PG 同样 fallback 到 strategy_id
        if not series:
            deployment = DeploymentService().get_deployment(deployment_id)
            if deployment and deployment.strategy_id != deployment_id:
                series = await get_nav_series_pg(
                    deployment.strategy_id, since_ms=since_ms, limit=limit
                )

    if len(series) > limit:
        series = series[-limit:]

    return series


@router.post("/v1/deployments/{deployment_id}/params", response_model=Deployment)
async def update_deployment_params(
    deployment_id: str = Path(..., description="Deployment ID"),
    params: dict = None,
):
    """
    Update deployment params.

    Updates deployment parameters and creates a new params version.
    """
    if params is None:
        params = {}
    service = DeploymentService()
    deployment = service.update_params(deployment_id, params)
    if not deployment:
        raise HTTPException(status_code=404, detail=f"Deployment {deployment_id} not found")
    return deployment
