"""
Health API Routes
================
Health check endpoints for the Systematic Trader Control Plane API.

Three-level health check:
1. Liveness: Basic process health (always returns 200 if process is alive)
2. Readiness: Service can handle requests (dependencies loaded)
3. Dependency: External dependencies status (PostgreSQL, storage)
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Request, Query

from trader.api.models.schemas import (
    HealthResponse,
    HealthCheckResponse,
    ComponentHealth,
    DependencyStatus,
    HeartbeatResponse,
    ProcessHeartbeatSchema,
    ExchangeConnectivitySchema,
    FrontendConnectionSchema,
)
from trader.storage import get_storage
from trader.adapters.persistence.postgres import is_postgres_available, ASYNCPG_AVAILABLE, check_postgres_connection

logger = logging.getLogger(__name__)

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


_heartbeat_service: Optional[object] = None
_connection_manager: Optional[object] = None
_connector_getter: Optional[callable] = None


def configure_heartbeat(
    heartbeat_service: object,
    connection_manager: object,
    connector_getter: callable
) -> None:
    """配置心跳服务依赖"""
    global _heartbeat_service, _connection_manager, _connector_getter
    _heartbeat_service = heartbeat_service
    _connection_manager = connection_manager
    _connector_getter = connector_getter


@router.get("/health/heartbeat", response_model=HeartbeatResponse)
async def heartbeat_check(
    client_id: Optional[str] = Query(default=None, description="前端客户端 ID")
):
    """
    三层心跳检查
    
    - process: 后端进程健康（event loop lag, tasks count, uptime）
    - exchange: 交易所连接状态（WS states, last pong/rest success）
    - frontend: 前端轮询状态（active sessions, health）
    
    前端应在每次轮询时传入 client_id 以表明存活。
    """
    if client_id and _connection_manager:
        await _connection_manager.record_ping(client_id)

    process_hb = _get_process_heartbeat()
    exchange_status = _get_exchange_status()
    frontend_status = await _get_frontend_status()

    return HeartbeatResponse(
        timestamp=_get_utc_time(),
        process=process_hb,
        exchange=exchange_status,
        frontend=frontend_status,
    )


def _get_process_heartbeat() -> ProcessHeartbeatSchema:
    """获取进程心跳"""
    if _heartbeat_service:
        hb = _heartbeat_service.get_last_heartbeat()
        if hb:
            return ProcessHeartbeatSchema(
                event_loop_lag_ms=hb.event_loop_lag_ms,
                last_event_loop_check_ts_ms=hb.last_event_loop_check_ts_ms,
                active_tasks=hb.active_tasks,
                uptime_seconds=hb.uptime_seconds,
                memory_usage_mb=hb.memory_usage_mb,
                is_healthy=hb.is_healthy,
            )
    
    return ProcessHeartbeatSchema(
        event_loop_lag_ms=0.0,
        last_event_loop_check_ts_ms=0,
        active_tasks=0,
        uptime_seconds=0.0,
        memory_usage_mb=None,
        is_healthy=True,
    )


def _get_exchange_status() -> ExchangeConnectivitySchema:
    """获取交易所连接状态"""
    if _connector_getter:
        try:
            connector = _connector_getter()
            if connector:
                health = connector.get_health()
                rest_metrics = health.metrics.get("rest", {})
                last_pong_ts_ms = 0
                try:
                    last_pong_ts_ms = int(connector.public_stream._last_pong_ts * 1000)
                except Exception:
                    pass
                
                return ExchangeConnectivitySchema(
                    public_stream_state=health.public_stream_state.value,
                    private_stream_state=health.private_stream_state.value,
                    last_pong_ts_ms=last_pong_ts_ms if last_pong_ts_ms > 0 else None,
                    last_rest_success_ts_ms=rest_metrics.get("last_rest_success_ts_ms"),
                    overall=health.overall_health.value,
                )
        except Exception as e:
            logger.error(f"[Health] Failed to get exchange status: {e}")

    return ExchangeConnectivitySchema(
        public_stream_state="UNKNOWN",
        private_stream_state="UNKNOWN",
        last_pong_ts_ms=None,
        last_rest_success_ts_ms=None,
        overall="UNKNOWN",
    )


async def _get_frontend_status() -> FrontendConnectionSchema:
    """获取前端连接状态"""
    if _connection_manager:
        try:
            status = await _connection_manager.get_status()
            return FrontendConnectionSchema(
                active_sessions=status.active_sessions,
                last_seen_ts_ms=status.last_seen_ts_ms,
                status=status.status,
            )
        except Exception as e:
            logger.error(f"[Health] Failed to get frontend status: {e}")

    return FrontendConnectionSchema(
        active_sessions=0,
        last_seen_ts_ms=None,
        status="UNKNOWN",
    )
