"""
Health API Routes
================
Health check endpoints for the Systematic Trader Control Plane API.

Three-level health check:
1. Liveness: Basic process health (always returns 200 if process is alive)
2. Readiness: Service can handle requests (dependencies loaded)
3. Dependency: External dependencies status (PostgreSQL, storage)
"""
from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter

from trader.api.models.schemas import (
    HealthResponse,
    HealthCheckResponse,
    ComponentHealth,
    DependencyStatus,
)
from trader.storage import get_storage
from trader.adapters.persistence.postgres import is_postgres_available, ASYNCPG_AVAILABLE, check_postgres_connection

router = APIRouter(tags=["Health"])


def _get_utc_time() -> str:
    """Get current UTC time in ISO format"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _check_storage_health() -> ComponentHealth:
    """Check storage accessibility"""
    try:
        get_storage()
        return ComponentHealth(
            status="healthy",
            message="In-memory storage accessible"
        )
    except Exception as e:
        return ComponentHealth(
            status="unhealthy",
            message=f"Storage error: {str(e)}"
        )


async def _check_postgresql_health() -> ComponentHealth:
    """Check PostgreSQL availability with actual connection test"""
    if not ASYNCPG_AVAILABLE:
        return ComponentHealth(
            status="not_configured",
            message="asyncpg not installed"
        )
    
    if not is_postgres_available():
        return ComponentHealth(
            status="not_configured",
            message="PostgreSQL not configured"
        )
    
    is_reachable, message = await check_postgres_connection(timeout=2.0)
    
    if is_reachable:
        return ComponentHealth(
            status="healthy",
            message="PostgreSQL connection successful"
        )
    else:
        return ComponentHealth(
            status="unhealthy",
            message=message
        )


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint (legacy).

    Returns the current health status of the API.
    """
    return HealthResponse(
        status="ok",
        time=_get_utc_time()
    )


@router.get("/health/live", response_model=HealthResponse)
async def liveness_check():
    """
    Liveness probe.

    Returns 200 if the process is alive.
    Used by Kubernetes to know when to restart the container.
    """
    return HealthResponse(
        status="ok",
        time=_get_utc_time()
    )


@router.get("/health/ready", response_model=HealthCheckResponse)
async def readiness_check():
    """
    Readiness probe.

    Returns 200 if the service can handle requests.
    Checks that required dependencies (storage) are loaded.
    PostgreSQL is optional and not checked here - use /health/dependency for full status.
    """
    checks: Dict[str, ComponentHealth] = {}
    overall_status = "ok"
    
    storage_health = _check_storage_health()
    checks["storage"] = storage_health
    
    if storage_health.status == "unhealthy":
        overall_status = "degraded"
    
    return HealthCheckResponse(
        status=overall_status,
        time=_get_utc_time(),
        checks=checks
    )


@router.get("/health/dependency", response_model=HealthCheckResponse)
async def dependency_check():
    """
    Dependency probe.

    Returns detailed status of all external dependencies.
    Includes PostgreSQL connection status and storage health.
    """
    checks: Dict[str, ComponentHealth] = {}
    overall_status = "ok"
    
    checks["postgresql"] = await _check_postgresql_health()
    checks["storage"] = _check_storage_health()
    
    for component, health in checks.items():
        if health.status in ["unhealthy", "degraded"]:
            overall_status = "degraded"
            break
    
    dependency_status = DependencyStatus(
        postgresql=checks["postgresql"],
        storage=checks["storage"]
    )
    
    return HealthCheckResponse(
        status=overall_status,
        time=_get_utc_time(),
        checks=checks,
        dependencies=dependency_status
    )
