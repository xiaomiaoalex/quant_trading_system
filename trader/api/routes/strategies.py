"""
Strategy API Routes
==================
Strategy registry and version management endpoints.
"""
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Path

from trader.api.models.schemas import (
    Strategy, StrategyRegisterRequest,
    StrategyVersion, StrategyVersionCreateRequest,
    VersionedConfig, VersionedConfigUpsertRequest,
)
from trader.services import StrategyService

router = APIRouter(tags=["Strategies"])


@router.get("/v1/strategies/registry", response_model=List[Strategy])
async def list_strategies():
    """
    List registered strategies.

    Returns a list of all registered strategies.
    """
    service = StrategyService()
    return service.list_strategies()


@router.post("/v1/strategies/registry", response_model=Strategy, status_code=201)
async def register_strategy(request: StrategyRegisterRequest):
    """
    Register a new strategy.

    Registers a strategy with metadata and entrypoint.
    """
    service = StrategyService()
    return service.register_strategy(request)


@router.get("/v1/strategies/registry/{strategy_id}", response_model=Strategy)
async def get_strategy(strategy_id: str = Path(..., description="Strategy ID")):
    """
    Get strategy metadata.

    Returns the strategy metadata by ID.
    """
    service = StrategyService()
    strategy = service.get_strategy(strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} not found")
    return strategy


@router.get("/v1/strategies/{strategy_id}/versions", response_model=List[StrategyVersion])
async def list_strategy_versions(strategy_id: str = Path(..., description="Strategy ID")):
    """
    List strategy versions.

    Returns all versions of a strategy.
    """
    service = StrategyService()
    return service.list_versions(strategy_id)


@router.post("/v1/strategies/{strategy_id}/versions", response_model=StrategyVersion, status_code=201)
async def create_strategy_version(
    strategy_id: str = Path(..., description="Strategy ID"),
    request: StrategyVersionCreateRequest = None,
):
    """
    Create a new strategy version.

    Creates a new version with code reference and parameter schema.
    """
    if request is None:
        request = StrategyVersionCreateRequest(
            version=1,
            code_ref="git:initial",
            param_schema={}
        )
    service = StrategyService()
    return service.create_version(strategy_id, request)


@router.get("/v1/strategies/{strategy_id}/versions/{version}", response_model=StrategyVersion)
async def get_strategy_version(
    strategy_id: str = Path(..., description="Strategy ID"),
    version: int = Path(..., description="Version number"),
):
    """
    Get strategy version details.

    Returns a specific version of a strategy.
    """
    service = StrategyService()
    version_obj = service.get_version(strategy_id, version)
    if not version_obj:
        raise HTTPException(
            status_code=404,
            detail=f"Version {version} of strategy {strategy_id} not found"
        )
    return version_obj


@router.get("/v1/strategies/{strategy_id}/params", response_model=Optional[VersionedConfig])
async def get_strategy_params(strategy_id: str = Path(..., description="Strategy ID")):
    """
    Get latest strategy params.

    Returns the latest parameter configuration for a strategy.
    """
    service = StrategyService()
    return service.get_latest_params(strategy_id)


@router.post("/v1/strategies/{strategy_id}/params", response_model=VersionedConfig)
async def create_strategy_params(
    strategy_id: str = Path(..., description="Strategy ID"),
    request: VersionedConfigUpsertRequest = None,
):
    """
    Create new strategy params version.

    Creates a new version of strategy parameters.
    """
    if request is None:
        request = VersionedConfigUpsertRequest(
            scope=strategy_id,
            config={},
            created_by="system"
        )
    service = StrategyService()
    return service.create_params(strategy_id, request)
