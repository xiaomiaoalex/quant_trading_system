"""
API Models - Pydantic schemas for the Systematic Trader Control Plane API
===========================================================================
Based on OpenAPI 3.0.3 specification v0.2.0

This module defines all request/response models for the API endpoints.
"""
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, Field


# ==================== Common Models ====================

class ActionResult(BaseModel):
    """通用操作结果"""
    ok: bool
    message: Optional[str] = None


# ==================== Strategy Models ====================

class Strategy(BaseModel):
    """策略元数据"""
    strategy_id: str
    name: str
    description: Optional[str] = None
    entrypoint: str
    language: str = "python"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class StrategyRegisterRequest(BaseModel):
    """注册策略请求"""
    strategy_id: str
    name: str
    description: Optional[str] = None
    entrypoint: str = Field(..., json_schema_extra={"example": "strategies.mean_reversion:Strategy"})
    language: str = "python"


class StrategyVersion(BaseModel):
    """策略版本"""
    strategy_id: str
    version: int
    code_ref: str = Field(..., json_schema_extra={"example": "git:abcd1234"})
    requirements: Optional[Dict[str, Any]] = None
    param_schema: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    created_by: Optional[str] = None


class StrategyVersionCreateRequest(BaseModel):
    """创建策略版本请求"""
    version: int
    code_ref: str
    requirements: Optional[Dict[str, Any]] = None
    param_schema: Optional[Dict[str, Any]] = None
    created_by: Optional[str] = None


class VersionedConfig(BaseModel):
    """版本化配置"""
    scope: str = Field(..., json_schema_extra={"example": "GLOBAL"})
    version: int
    config: Dict[str, Any]
    created_at: Optional[str] = None
    created_by: Optional[str] = None


class VersionedConfigUpsertRequest(BaseModel):
    """版本化配置更新请求"""
    scope: str
    config: Dict[str, Any]
    created_by: str


class RiskEventIngestRequest(BaseModel):
    """风险事件上报请求"""
    dedup_key: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    reason: str
    metrics: Dict[str, Any] = Field(default_factory=dict)
    recommended_level: int = Field(..., ge=0, le=3)
    scope: str = Field(..., json_schema_extra={"example": "GLOBAL"})
    ts_ms: int
    adapter_name: Optional[str] = None
    venue: Optional[str] = None
    account_id: Optional[str] = None


# ==================== Deployment Models ====================

class Deployment(BaseModel):
    """部署实例"""
    deployment_id: str
    strategy_id: str
    version: int
    account_id: str
    venue: str = Field(..., json_schema_extra={"example": "BINANCE"})
    symbols: List[str]
    status: str = Field(..., json_schema_extra={"example": "STOPPED"})
    params_version: Optional[int] = None
    risk_profile_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DeploymentCreateRequest(BaseModel):
    """创建部署请求"""
    deployment_id: str
    strategy_id: str
    version: int
    account_id: str
    venue: str = Field(..., json_schema_extra={"example": "BINANCE"})
    symbols: List[str]
    params_version: Optional[int] = None
    risk_profile_id: Optional[str] = None
    created_by: str


# ==================== Backtest Models ====================

class BacktestRequest(BaseModel):
    """回测请求"""
    strategy_id: str
    version: int
    params: Optional[Dict[str, Any]] = None
    symbols: List[str]
    start_ts_ms: int = Field(..., description="Start timestamp in milliseconds")
    end_ts_ms: int = Field(..., description="End timestamp in milliseconds")
    venue: str = Field(..., json_schema_extra={"example": "BINANCE"})
    requested_by: str


class BacktestRun(BaseModel):
    """回测运行"""
    run_id: str
    status: str = Field(..., json_schema_extra={"example": "RUNNING"})
    strategy_id: str
    version: int
    symbols: List[str]
    start_ts_ms: int
    end_ts_ms: int
    metrics: Optional[Dict[str, Any]] = None
    artifact_ref: Optional[str] = None
    created_at: Optional[str] = None


# ==================== Order & Execution Models ====================

class OrderView(BaseModel):
    """订单视图"""
    cl_ord_id: str
    trace_id: Optional[str] = None
    account_id: str
    strategy_id: str
    deployment_id: Optional[str] = None
    venue: str
    instrument: str
    side: str
    order_type: str
    qty: str
    limit_price: Optional[str] = None
    tif: str
    status: str
    broker_order_id: Optional[str] = None
    filled_qty: str = "0"
    avg_price: Optional[str] = None
    created_ts_ms: Optional[int] = None
    updated_ts_ms: Optional[int] = None
    reject_code: Optional[str] = None
    reject_msg: Optional[str] = None


class ExecutionView(BaseModel):
    """成交视图"""
    cl_ord_id: str
    exec_id: str
    ts_ms: int
    fill_qty: str
    fill_price: str
    fee: Optional[str] = None
    fee_currency: Optional[str] = None


# ==================== Portfolio Models ====================

class PositionView(BaseModel):
    """持仓视图"""
    account_id: str
    venue: str
    instrument: str
    qty: str
    avg_cost: Optional[str] = None
    mark_price: Optional[str] = None
    unrealized_pnl: Optional[str] = None
    realized_pnl: Optional[str] = None
    updated_ts_ms: Optional[int] = None


class PnlView(BaseModel):
    """盈亏视图"""
    account_id: str
    venue: str
    realized_pnl: str
    unrealized_pnl: str
    total_pnl: str
    updated_ts_ms: Optional[int] = None


# ==================== Event & Snapshot Models ====================

class EventEnvelope(BaseModel):
    """事件包装"""
    event_id: Optional[int] = None
    stream_key: str
    event_type: str
    schema_version: int = 1
    trace_id: Optional[str] = None
    ts_ms: int
    payload: Dict[str, Any]


class SnapshotEnvelope(BaseModel):
    """快照包装"""
    snapshot_id: Optional[int] = None
    stream_key: str
    snapshot_type: str
    ts_ms: int
    payload: Dict[str, Any]
    created_at: Optional[str] = None


class ReplayRequest(BaseModel):
    """重放请求"""
    stream_key: str
    from_ts_ms: Optional[int] = None
    to_ts_ms: Optional[int] = None
    requested_by: str


# ==================== KillSwitch Models ====================

class KillSwitchState(BaseModel):
    """熔断状态"""
    scope: str = Field(..., json_schema_extra={"example": "GLOBAL"})
    level: int = Field(..., ge=0, le=3)
    reason: Optional[str] = None
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None


class KillSwitchSetRequest(BaseModel):
    """设置熔断请求"""
    scope: str = Field(..., json_schema_extra={"example": "GLOBAL"})
    level: int = Field(..., ge=0, le=3)
    reason: Optional[str] = None
    updated_by: str


# ==================== Broker Models ====================

class BrokerAccount(BaseModel):
    """券商账户"""
    account_id: str
    venue: str
    broker_type: str = Field(..., json_schema_extra={"example": "BINANCE"})
    status: str = Field(..., json_schema_extra={"example": "READY"})
    capabilities: Optional[Dict[str, Any]] = None


class BrokerStatus(BaseModel):
    """券商状态"""
    account_id: str
    connected: bool
    last_heartbeat_ts_ms: Optional[int] = None
    last_error: Optional[str] = None


# ==================== Health Models ====================

def _utc_time() -> str:
    """Get current UTC time in ISO format (RFC3339 compliant)"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = "ok"
    time: str = Field(default_factory=_utc_time)


class ComponentHealth(BaseModel):
    """组件健康状态"""
    status: str = "healthy"
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class DependencyStatus(BaseModel):
    """依赖项状态"""
    postgresql: ComponentHealth
    storage: ComponentHealth


class HealthCheckResponse(BaseModel):
    """三级健康检查响应"""
    status: str = "ok"
    time: str = Field(default_factory=_utc_time)
    checks: Dict[str, ComponentHealth] = Field(default_factory=dict)
    dependencies: Optional[DependencyStatus] = None


# ==================== Monitor Models ====================

AlertSeverity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class AlertRule(BaseModel):
    """告警规则"""
    rule_name: str
    metric_key: str
    threshold: float
    comparison: Literal["gt", "lt", "gte", "lte", "eq"] = Field(..., description="Comparison operator")
    severity: AlertSeverity
    cooldown_seconds: int = Field(default=60, description="告警冷却时间（秒）")


class Alert(BaseModel):
    """告警实例"""
    alert_id: str
    rule_name: str
    severity: AlertSeverity
    message: str
    metric_key: str
    metric_value: float
    threshold: float
    triggered_at: str


class AdapterHealthStatus(BaseModel):
    """适配器健康状态"""
    adapter_name: str
    status: str = Field(..., description="HEALTHY, DEGRADED, DOWN")
    last_heartbeat_ts_ms: Optional[int] = None
    error_count: int = 0
    message: Optional[str] = None


class MonitorSnapshot(BaseModel):
    """系统监控快照"""
    timestamp: str = Field(default_factory=_utc_time)
    
    # 持仓信息
    total_positions: int = 0
    total_exposure: str = "0"
    
    # 订单信息
    open_orders_count: int = 0
    pending_orders_count: int = 0
    
    # PnL信息
    daily_pnl: str = "0"
    daily_pnl_pct: str = "0"
    realized_pnl: str = "0"
    unrealized_pnl: str = "0"
    
    # KillSwitch状态
    killswitch_level: int = 0
    killswitch_scope: str = "GLOBAL"
    
    # 适配器状态
    adapters: Dict[str, AdapterHealthStatus] = Field(default_factory=dict)
    
    # 告警信息
    active_alerts: List[Alert] = Field(default_factory=list)
    alert_count_by_severity: Dict[str, int] = Field(default_factory=dict)
