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


class StrategyCodeVersion(BaseModel):
    """策略代码版本"""
    strategy_id: str
    code_version: int
    code: str
    checksum: str
    created_at: Optional[str] = None
    created_by: Optional[str] = None
    notes: Optional[str] = None


class StrategyCodeCreateRequest(BaseModel):
    """策略代码新建/保存请求"""
    strategy_id: str
    code: str = Field(..., min_length=1)
    name: Optional[str] = None
    description: Optional[str] = None
    created_by: str = "console_user"
    notes: Optional[str] = None
    register_if_missing: bool = True


class StrategyCodeDebugRequest(BaseModel):
    """策略代码调试请求"""
    strategy_id: Optional[str] = None
    code: str = Field(..., min_length=1)
    config: Dict[str, Any] = Field(default_factory=dict)
    sample_market_data: Optional[List[Dict[str, Any]]] = None


class StrategyCodeDebugResponse(BaseModel):
    """策略代码调试响应"""
    ok: bool
    syntax_ok: bool
    protocol_ok: bool
    validation_status: Optional[str] = None
    checksum: Optional[str] = None
    signals: List[Dict[str, Any]] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


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


class TimeWindowSlotSchema(BaseModel):
    """时间窗口时段槽"""
    period: Literal["PRIME", "OFF_PEAK", "RESTRICTED"]
    start_hour: int = Field(..., ge=0, le=23)
    start_minute: int = Field(..., ge=0, le=59)
    end_hour: int = Field(..., ge=0, le=23)
    end_minute: int = Field(..., ge=0, le=59)
    position_coefficient: float = Field(default=1.0, ge=0.0, le=1.0)
    allow_new_position: bool = Field(default=True)


class TimeWindowConfigSchema(BaseModel):
    """时间窗口配置"""
    slots: List[TimeWindowSlotSchema] = Field(default_factory=list)
    default_coefficient: float = Field(default=1.0, ge=0.0, le=1.0)


class TimeWindowConfigUpdateRequest(BaseModel):
    """时间窗口配置更新请求"""
    slots: List[TimeWindowSlotSchema]
    default_coefficient: float = Field(default=1.0, ge=0.0, le=1.0)
    updated_by: str = Field(..., min_length=1)


# ==================== Deployment Models ====================

DeploymentMode = Literal["paper", "demo", "live", "shadow"]

class Deployment(BaseModel):
    """部署实例"""
    deployment_id: str
    strategy_id: str
    version: str
    account_id: str
    venue: str = Field(..., json_schema_extra={"example": "BINANCE"})
    symbols: List[str]
    mode: DeploymentMode = "demo"
    module_path: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    status: str = Field(..., json_schema_extra={"example": "STOPPED"})
    params_version: Optional[int] = None
    risk_profile_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DeploymentCreateRequest(BaseModel):
    """创建部署请求"""
    deployment_id: Optional[str] = None
    strategy_id: str
    version: str = "v1"
    account_id: str
    venue: str = Field(..., json_schema_extra={"example": "BINANCE"})
    symbols: List[str]
    mode: DeploymentMode = "demo"
    module_path: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    params_version: Optional[int] = None
    risk_profile_id: Optional[str] = None
    created_by: str


class DeploymentRuntime(BaseModel):
    """运行中 deployment 视图。"""

    deployment_id: str
    strategy_id: str
    version: str
    status: Literal["loaded", "running", "paused", "stopped", "error"]
    symbols: List[str] = Field(default_factory=list)
    account_id: str
    venue: str
    mode: DeploymentMode
    loaded_at: Optional[str] = None
    started_at: Optional[str] = None
    last_tick_at: Optional[str] = None
    tick_count: int = 0
    signal_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None
    stop_reason: Optional[str] = None
    blocked_reason: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)


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
    strategy_code_version: Optional[int] = None


class BacktestRun(BaseModel):
    """回测运行"""
    run_id: str
    status: str = Field(..., json_schema_extra={"example": "RUNNING"})
    strategy_id: str
    version: int
    symbols: List[str]
    start_ts_ms: int
    end_ts_ms: int
    strategy_code_version: Optional[int] = None
    metrics: Optional[Dict[str, Any]] = None
    artifact_ref: Optional[str] = None
    created_at: Optional[str] = None
    # 进度追踪字段 (Task 9.4)
    progress: Optional[float] = Field(default=0.0, ge=0.0, le=1.0, description="完成进度 0.0-1.0")
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None


class BacktestReport(BaseModel):
    """回测报告详情 (Task 9.5)"""
    run_id: str
    status: str
    strategy_id: str
    version: int
    symbols: List[str]
    start_ts_ms: int
    end_ts_ms: int
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    # 报告详情
    returns: Optional[Dict[str, Any]] = None
    risk: Optional[Dict[str, Any]] = None
    trades: Optional[List[Dict[str, Any]]] = None
    equity_curve: Optional[List[Dict[str, Any]]] = None
    metrics: Optional[Dict[str, Any]] = None
    artifact_ref: Optional[str] = None


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


class PositionDetail(BaseModel):
    """Monitor 页面使用的详细持仓信息"""
    symbol: str = Field(description="交易对，如 BTCUSDT")
    quantity: str = Field(description="持仓数量")
    avg_cost: Optional[str] = Field(default=None, description="平均成本价")
    current_price: Optional[str] = Field(default=None, description="当前价格（标记价）")
    unrealized_pnl: Optional[str] = Field(default=None, description="未实现盈亏")
    exposure: Optional[str] = Field(default=None, description="敞口价值（数量 * 当前价格）")


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


class ReplayJob(BaseModel):
    """Replay 任务状态 (Task 9.7)"""
    job_id: str
    stream_key: str
    status: str = Field(..., description="PENDING/RUNNING/COMPLETED/FAILED")
    requested_by: str
    requested_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result_summary: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


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
    positions: List["PositionDetail"] = Field(default_factory=list, description="详细持仓列表")
    
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
    
    # Task 19: 运行时可观测性指标
    tick_rate: Optional[float] = Field(default=None, description="每秒Tick数")
    tick_lag_ms: Optional[float] = Field(default=None, description="Tick处理延迟（毫秒）")
    order_submit_ok: int = Field(default=0, description="成功提交订单数")
    order_submit_reject: int = Field(default=0, description="被拒绝订单数")
    order_submit_error: int = Field(default=0, description="错误订单数")
    reject_reason_counts: Dict[str, int] = Field(default_factory=dict, description="按原因统计的拒单数")
    fill_latency_ms_avg: Optional[float] = Field(default=None, description="平均成交延迟（毫秒）")
    fill_latency_count: int = Field(default=0, description="成交次数")
    ws_reconnect_count: int = Field(default=0, description="WebSocket重连次数")
    cl_ord_id_dedup_hits: int = Field(default=0, description="cl_ord_id重复去重次数")
    exec_dedup_hits: int = Field(default=0, description="exec_id重复去重次数")
    
    # 元信息 (Task 9.2 - 真聚合化)
    snapshot_source: Optional[str] = Field(default="aggregated", description="数据来源: aggregated/query")
    freshness: Optional[str] = Field(default=None, description="数据新鲜度时间戳")


class MonitorAlertsResponse(BaseModel):
    """告警列表响应包装器"""
    alerts: List[Alert] = Field(default_factory=list)
    total_count: int = Field(default=0, description="告警总数")


# ==================== Heartbeat Models ====================

class ProcessHeartbeatSchema(BaseModel):
    """进程心跳"""
    event_loop_lag_ms: float
    last_event_loop_check_ts_ms: int
    active_tasks: int
    uptime_seconds: float
    memory_usage_mb: Optional[float] = None
    is_healthy: bool = True


class ExchangeConnectivitySchema(BaseModel):
    """交易所连接状态"""
    public_stream_state: str
    private_stream_state: str
    last_pong_ts_ms: Optional[int] = None
    last_rest_success_ts_ms: Optional[int] = None
    overall: str = Field(description="HEALTHY/DEGRADED/UNHEALTHY")


class FrontendConnectionSchema(BaseModel):
    """前端连接状态"""
    active_sessions: int = 0
    last_seen_ts_ms: Optional[int] = None
    status: str = Field(description="IDLE/HEALTHY/DEGRADED")


class HeartbeatResponse(BaseModel):
    """三层心跳响应"""
    timestamp: str = Field(default_factory=_utc_time)
    process: ProcessHeartbeatSchema
    exchange: ExchangeConnectivitySchema
    frontend: FrontendConnectionSchema


# ==================== Audit Models (Task 9.6) ====================

class AuditEntry(BaseModel):
    """AI 审计条目"""
    entry_id: str
    strategy_id: str
    strategy_name: Optional[str] = None
    version: Optional[str] = None
    event_type: str
    status: str
    prompt: Optional[str] = None
    generated_code: Optional[str] = None
    code_hash: Optional[str] = None
    llm_backend: Optional[str] = None
    llm_model: Optional[str] = None
    execution_result: Optional[Dict[str, Any]] = None
    approver: Optional[str] = None
    approval_comment: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ==================== Position Tracking Models（Batch 1 新增） ====================

class StrategyPositionView(BaseModel):
    """策略级持仓视图"""
    strategy_id: str = Field(description="策略ID")
    symbol: str = Field(description="交易对")
    qty: str = Field(description="持仓数量")
    avg_cost: str = Field(description="平均成本价")
    realized_pnl: str = Field(description="已实现盈亏")
    unrealized_pnl: str = Field(description="未实现盈亏")
    total_cost: Optional[str] = Field(default=None, description="总成本 = qty * avg_cost")
    status: str = Field(description="持仓状态: ACTIVE / CLOSED / HISTORICAL")
    lot_count: int = Field(default=0, description="当前 open lot 数量")
    cost_basis_method: str = Field(default="average_cost", description="成本计算方法")
    updated_at: Optional[str] = None


class LotView(BaseModel):
    """批次（Lot）视图"""
    lot_id: str = Field(description="批次ID")
    strategy_id: str = Field(description="策略ID")
    symbol: str = Field(description="交易对")
    original_qty: str = Field(description="原始成交数量")
    remaining_qty: str = Field(description="剩余可平仓数量")
    fill_price: str = Field(description="成交价格")
    fee_qty: Optional[str] = Field(default=None, description="手续费数量（base asset）")
    fee_asset: Optional[str] = Field(default=None, description="手续费币种")
    realized_pnl: str = Field(default="0", description="该批次已实现盈亏")
    is_closed: bool = Field(default=False, description="是否已完全平仓")
    filled_at: str = Field(description="成交时间")


class PositionBreakdown(BaseModel):
    """持仓分解视图（单个标的的三层展示）"""
    symbol: str = Field(description="交易对")
    account_qty: str = Field(description="账户总持仓（Broker API）")
    account_avg_cost: Optional[str] = Field(default=None, description="账户平均成本（Broker 提供）")
    strategy_positions: List[StrategyPositionView] = Field(
        default_factory=list,
        description="策略级持仓列表"
    )
    historical: Optional[Dict[str, Any]] = Field(
        default=None,
        description="历史持仓信息（若有）"
    )
    is_reconciled: bool = Field(default=True, description="对账是否一致")
    difference: Optional[str] = Field(default=None, description="账户 vs OMS 差异数量")
    tolerance: str = Field(default="0.001", description="对账容忍度（相对比例）")


class ReconciliationLogEntry(BaseModel):
    """对账日志条目"""
    id: int
    symbol: str = Field(description="交易对")
    broker_qty: str = Field(description="Broker 持仓数量")
    oms_total_qty: str = Field(description="OMS 策略持仓合计")
    historical_qty: str = Field(default="0", description="历史持仓数量")
    difference: str = Field(description="差异 = broker - oms - historical")
    tolerance: str = Field(description="容忍度")
    status: str = Field(description="CONSISTENT / DISCREPANCY / HISTORICAL_GAP")
    resolution: Optional[str] = Field(default=None, description="处理方式: NONE / AUTO_ALIGNED / ALERTED / KILLSWITCH_L1")
    details: Dict[str, Any] = Field(default_factory=dict, description="附加详情")
    created_at: str = Field(description="对账时间")


class ReconciliationResult(BaseModel):
    """手动触发对账的返回结果"""
    symbol: str
    broker_qty: str
    oms_total_qty: str
    historical_qty: str = "0"
    difference: str
    tolerance: str
    status: str  # CONSISTENT / DISCREPANCY / HISTORICAL_GAP
    action_taken: Optional[str] = None  # NONE / AUTO_ALIGNED / ALERTED / KILLSWITCH_L1
