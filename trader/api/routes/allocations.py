from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query

from trader.api.models.schemas import (
    AllocationTrace,
    AllocationTraceCreateRequest,
    StrategyAllocationProfile,
    StrategyAllocationProfileUpdateRequest,
)
from trader.services.allocation_management import AllocationManagementService

router = APIRouter(tags=["Allocations"])


@router.get("/v1/allocations", response_model=list[StrategyAllocationProfile])
async def list_allocations():
    return AllocationManagementService().list_profiles()


@router.put("/v1/allocations/{deployment_id}", response_model=StrategyAllocationProfile)
async def upsert_allocation(
    request: StrategyAllocationProfileUpdateRequest,
    deployment_id: str = Path(...),
):
    try:
        return AllocationManagementService().upsert_profile(deployment_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/v1/allocations/{deployment_id}/traces",
    response_model=AllocationTrace,
    status_code=201,
)
async def append_allocation_trace(
    request: AllocationTraceCreateRequest,
    deployment_id: str = Path(...),
):
    return AllocationManagementService().append_trace(deployment_id, request)


@router.get("/v1/allocations/{deployment_id}/traces", response_model=list[AllocationTrace])
async def list_allocation_traces(
    deployment_id: str = Path(...),
    limit: int = Query(100, ge=1, le=500),
):
    return AllocationManagementService().list_traces(deployment_id, limit=limit)
