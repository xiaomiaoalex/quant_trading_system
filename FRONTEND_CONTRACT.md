# 前端数据契约 (Frontend Data Contract)

本文档定义前后端之间的数据契约，用于生成 TypeScript 类型定义和 API 调用代码。

---

## 1. 核心数据模型 (Core Data Models)

### 1.1 订单状态枚举 (OrderStatus)

```python
# 文件: trader/core/domain/models/order.py

class OrderStatus(Enum):
    PENDING = "PENDING"           # 待提交（刚创建，还未发送到券商）
    SUBMITTED = "SUBMITTED"       # 已提交（已发送到券商，等待成交）
    PARTIALLY_FILLED = "PARTIALLY_FILLED"  # 部分成交
    FILLED = "FILLED"            # 完全成交
    CANCELLED = "CANCELLED"       # 已撤销
    REJECTED = "REJECTED"        # 已拒绝（被券商或风控拒绝）
    CANCEL_PENDING = "CANCEL_PENDING"  # 撤销待确认
```

**TypeScript 定义：**
```typescript
type OrderStatus =
  | "PENDING"
  | "SUBMITTED"
  | "PARTIALLY_FILLED"
  | "FILLED"
  | "CANCELLED"
  | "REJECTED"
  | "CANCEL_PENDING";
```

### 1.2 订单方向枚举 (OrderSide)

```python
class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"
```

**TypeScript 定义：**
```typescript
type OrderSide = "BUY" | "SELL";
```

### 1.3 订单类型枚举 (OrderType)

```python
class OrderType(Enum):
    MARKET = "MARKET"     # 市价单
    LIMIT = "LIMIT"       # 限价单
```

**TypeScript 定义：**
```typescript
type OrderType = "MARKET" | "LIMIT";
```

### 1.4 订单时效枚举 (OrderTimeInForce)

```python
class OrderTimeInForce(Enum):
    GTC = "GTC"          # Good Till Cancel - 取消前有效
    IOC = "IOC"          # Immediate Or Cancel - 立即成交否则取消
    FOK = "FOK"          # Fill Or Kill - 全部成交否则取消
```

**TypeScript 定义：**
```typescript
type OrderTimeInForce = "GTC" | "IOC" | "FOK";
```

---

## 2. API 响应模型 (API Response Models)

### 2.1 订单视图 (OrderView)

```python
# 文件: trader/api/models/schemas.py

class OrderView(BaseModel):
    cl_ord_id: str                              # 客户端订单ID
    trace_id: Optional[str] = None              # 追踪ID
    account_id: str                             # 账户ID
    strategy_id: str                            # 策略ID
    deployment_id: Optional[str] = None         # 部署ID
    venue: str                                  # 交易所
    instrument: str                             # 交易标的
    side: str                                   # 方向 (BUY/SELL)
    order_type: str                             # 订单类型 (MARKET/LIMIT)
    qty: str                                    # 委托数量
    limit_price: Optional[str] = None           # 限价
    tif: str                                    # 时效 (GTC/IOC/FOK)
    status: str                                 # 状态 (OrderStatus)
    broker_order_id: Optional[str] = None       # 券商订单ID
    filled_qty: str = "0"                       # 已成交数量
    avg_price: Optional[str] = None             # 成交均价
    created_ts_ms: Optional[int] = None         # 创建时间戳(ms)
    updated_ts_ms: Optional[int] = None         # 更新时间戳(ms)
    reject_code: Optional[str] = None           # 拒绝代码
    reject_msg: Optional[str] = None            # 拒绝消息
```

**TypeScript 定义：**
```typescript
interface OrderView {
  cl_ord_id: string;
  trace_id?: string;
  account_id: string;
  strategy_id: string;
  deployment_id?: string;
  venue: string;
  instrument: string;
  side: "BUY" | "SELL";
  order_type: "MARKET" | "LIMIT";
  qty: string;
  limit_price?: string;
  tif: "GTC" | "IOC" | "FOK";
  status: OrderStatus;
  broker_order_id?: string;
  filled_qty: string;
  avg_price?: string;
  created_ts_ms?: number;
  updated_ts_ms?: number;
  reject_code?: string;
  reject_msg?: string;
}
```

### 2.2 成交视图 (ExecutionView)

```python
class ExecutionView(BaseModel):
    cl_ord_id: str                    # 客户端订单ID
    exec_id: str                      # 成交ID
    ts_ms: int                        # 成交时间戳(ms)
    fill_qty: str                     # 成交数量
    fill_price: str                   # 成交价格
    fee: Optional[str] = None         # 手续费
    fee_currency: Optional[str] = None # 手续费币种
```

**TypeScript 定义：**
```typescript
interface ExecutionView {
  cl_ord_id: string;
  exec_id: string;
  ts_ms: number;
  fill_qty: string;
  fill_price: string;
  fee?: string;
  fee_currency?: string;
}
```

### 2.3 持仓视图 (PositionView)

```python
class PositionView(BaseModel):
    account_id: str
    venue: str
    instrument: str
    qty: str
    avg_cost: Optional[str] = None
    mark_price: Optional[str] = None
    unrealized_pnl: Optional[str] = None
    realized_pnl: Optional[str] = None
    updated_ts_ms: Optional[int] = None
```

**TypeScript 定义：**
```typescript
interface PositionView {
  account_id: string;
  venue: string;
  instrument: string;
  qty: string;
  avg_cost?: string;
  mark_price?: string;
  unrealized_pnl?: string;
  realized_pnl?: string;
  updated_ts_ms?: number;
}
```

### 2.4 盈亏视图 (PnlView)

```python
class PnlView(BaseModel):
    account_id: str
    venue: str
    realized_pnl: str
    unrealized_pnl: str
    total_pnl: str
    updated_ts_ms: Optional[int] = None
```

**TypeScript 定义：**
```typescript
interface PnlView {
  account_id: string;
  venue: string;
  realized_pnl: string;
  unrealized_pnl: string;
  total_pnl: string;
  updated_ts_ms?: number;
}
```

---

## 3. 风控模型 (Risk Models)

### 3.1 熔断状态 (KillSwitchState)

```python
class KillSwitchState(BaseModel):
    scope: str = "GLOBAL"              # 范围 (GLOBAL 或账户级)
    level: int                         # 级别 0-3
    reason: Optional[str] = None       # 原因
    updated_at: Optional[str] = None   # 更新时间
    updated_by: Optional[str] = None   # 更新人
```

**熔断级别说明：**
| Level | 名称 | 含义 |
|-------|------|------|
| 0 | L0_NORMAL | 正常运行 |
| 1 | L1_NO_NEW_POS | 禁止新开仓 |
| 2 | L2_CLOSE_ONLY | 只允许平仓 |
| 3 | L3_FULL_STOP | 完全停止 |

**TypeScript 定义：**
```typescript
interface KillSwitchState {
  scope: string;
  level: 0 | 1 | 2 | 3;
  reason?: string;
  updated_at?: string;
  updated_by?: string;
}
```

### 3.2 风险事件上报请求 (RiskEventIngestRequest)

```python
class RiskEventIngestRequest(BaseModel):
    dedup_key: str                                          # 去重键
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]  # 严重程度
    reason: str                                             # 原因
    metrics: Dict[str, Any] = {}                            # 指标
    recommended_level: int                                  # 推荐熔断级别 (0-3)
    scope: str = "GLOBAL"                                   # 范围
    ts_ms: int                                              # 时间戳(ms)
    adapter_name: Optional[str] = None                      # 适配器名
    venue: Optional[str] = None                             # 交易所
    account_id: Optional[str] = None                        # 账户ID
```

**TypeScript 定义：**
```typescript
interface RiskEventIngestRequest {
  dedup_key: string;
  severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  reason: string;
  metrics?: Record<string, unknown>;
  recommended_level: 0 | 1 | 2 | 3;
  scope: string;
  ts_ms: number;
  adapter_name?: string;
  venue?: string;
  account_id?: string;
}
```

### 3.3 告警模型 (Alert)

```python
AlertSeverity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

class Alert(BaseModel):
    alert_id: str
    rule_name: str
    severity: AlertSeverity
    message: str
    metric_key: str
    metric_value: float
    threshold: float
    triggered_at: str
```

**TypeScript 定义：**
```typescript
type AlertSeverity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

interface Alert {
  alert_id: string;
  rule_name: string;
  severity: AlertSeverity;
  message: string;
  metric_key: string;
  metric_value: number;
  threshold: number;
  triggered_at: string;
}
```

---

## 4. 对账模型 (Reconciliation Models)

### 4.1 差异类型枚举 (DriftType)

```python
# 文件: trader/core/application/reconciler.py

class DriftType(str, Enum):
    GHOST = "GHOST"         # 本地有，交易所无
    PHANTOM = "PHANTOM"     # 本地无，交易所有
    DIVERGED = "DIVERGED"   # 状态/数量不一致
```

**TypeScript 定义：**
```typescript
type DriftType = "GHOST" | "PHANTOM" | "DIVERGED";
```

### 4.2 订单差异 (OrderDrift)

```python
@dataclass
class OrderDrift:
    cl_ord_id: str
    drift_type: DriftType
    local_status: Optional[str]
    exchange_status: Optional[str]
    detected_at: datetime
    local_updated_at: Optional[datetime] = None
    exchange_updated_at: Optional[datetime] = None
    grace_period_remaining_sec: Optional[float] = None
    symbol: Optional[str] = None
    quantity: Optional[str] = None
    filled_quantity: Optional[str] = None
    exchange_filled_quantity: Optional[str] = None
```

**TypeScript 定义：**
```typescript
interface OrderDrift {
  cl_ord_id: string;
  drift_type: DriftType;
  local_status?: string;
  exchange_status?: string;
  detected_at: string;  // ISO 8601
  local_updated_at?: string;
  exchange_updated_at?: string;
  grace_period_remaining_sec?: number;
  symbol?: string;
  quantity?: string;
  filled_quantity?: string;
  exchange_filled_quantity?: string;
}
```

### 4.3 对账报告 (ReconcileReport)

```python
@dataclass
class ReconcileReport:
    timestamp: datetime
    total_orders_checked: int
    drifts: List[OrderDrift]
    ghost_count: int = 0
    phantom_count: int = 0
    diverged_count: int = 0
    within_grace_period_count: int = 0
```

**TypeScript 定义：**
```typescript
interface ReconcileReport {
  timestamp: string;  // ISO 8601
  total_orders_checked: number;
  drifts: OrderDrift[];
  ghost_count: number;
  phantom_count: number;
  diverged_count: number;
  within_grace_period_count: number;
}
```

---

## 5. 事件与快照模型 (Event & Snapshot Models)

### 5.1 事件包装 (EventEnvelope)

```python
class EventEnvelope(BaseModel):
    event_id: Optional[int] = None
    stream_key: str
    event_type: str
    schema_version: int = 1
    trace_id: Optional[str] = None
    ts_ms: int
    payload: Dict[str, Any]
```

**TypeScript 定义：**
```typescript
interface EventEnvelope {
  event_id?: number;
  stream_key: string;
  event_type: string;
  schema_version: number;
  trace_id?: string;
  ts_ms: number;
  payload: Record<string, unknown>;
}
```

### 5.2 快照包装 (SnapshotEnvelope)

```python
class SnapshotEnvelope(BaseModel):
    snapshot_id: Optional[int] = None
    stream_key: str
    snapshot_type: str
    ts_ms: int
    payload: Dict[str, Any]
    created_at: Optional[str] = None
```

**TypeScript 定义：**
```typescript
interface SnapshotEnvelope {
  snapshot_id?: number;
  stream_key: string;
  snapshot_type: string;
  ts_ms: number;
  payload: Record<string, unknown>;
  created_at?: string;
}
```

---

## 6. 监控快照模型 (MonitorSnapshot)

```python
class MonitorSnapshot(BaseModel):
    timestamp: str                          # ISO 8601 时间戳
    
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
    adapters: Dict[str, AdapterHealthStatus] = {}
    
    # 告警信息
    active_alerts: List[Alert] = []
    alert_count_by_severity: Dict[str, int] = {}
```

**TypeScript 定义：**
```typescript
interface AdapterHealthStatus {
  adapter_name: string;
  status: "HEALTHY" | "DEGRADED" | "DOWN";
  last_heartbeat_ts_ms?: number;
  error_count: number;
  message?: string;
}

interface MonitorSnapshot {
  timestamp: string;
  total_positions: number;
  total_exposure: string;
  open_orders_count: number;
  pending_orders_count: number;
  daily_pnl: string;
  daily_pnl_pct: string;
  realized_pnl: string;
  unrealized_pnl: string;
  killswitch_level: number;
  killswitch_scope: string;
  adapters: Record<string, AdapterHealthStatus>;
  active_alerts: Alert[];
  alert_count_by_severity: Record<string, number>;
}
```

---

## 7. API 端点定义 (API Endpoints)

### 基础信息
- **Base URL**: `http://localhost:8080`
- **OpenAPI 文档**: `http://localhost:8080/docs`
- **ReDoc**: `http://localhost:8080/redoc`

### 7.1 订单接口

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/v1/orders` | 查询订单列表 |
| GET | `/v1/orders/{cl_ord_id}` | 获取单个订单 |
| POST | `/v1/orders/{cl_ord_id}/cancel` | 撤销订单 |
| GET | `/v1/executions` | 查询成交列表 |

**GET /v1/orders 查询参数：**
```typescript
interface ListOrdersParams {
  account_id?: string;
  strategy_id?: string;
  deployment_id?: string;
  venue?: string;
  status?: OrderStatus;
  instrument?: string;
  since_ts_ms?: number;
  limit?: number;  // 默认 200, 最大 2000
}
```

### 7.2 持仓接口

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/v1/portfolio/positions` | 查询持仓列表 |
| GET | `/v1/portfolio/pnl` | 查询盈亏汇总 |

### 7.3 风控接口

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/v1/killswitch` | 获取熔断状态 |
| POST | `/v1/killswitch` | 设置熔断级别 |
| GET | `/v1/risk/limits` | 获取风控限额 |
| POST | `/v1/risk/limits` | 设置风控限额 |
| POST | `/v1/risk/events` | 上报风险事件 |
| POST | `/v1/risk/recover` | 恢复待处理效果 |

### 7.4 对账接口

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/v1/reconciler/report` | 获取最新对账报告 |
| POST | `/v1/reconciler/trigger` | 触发对账检查 |

**POST /v1/reconciler/trigger 请求体：**
```typescript
interface TriggerReconcileRequest {
  local_orders: Array<{
    client_order_id: string;
    status: string;
    symbol?: string;
    quantity?: string | number;
    filled_quantity?: string | number;
    created_at?: string;
    updated_at?: string;
  }>;
  exchange_orders: Array<{
    client_order_id: string;
    status: string;
    symbol?: string;
    quantity?: string | number;
    filled_quantity?: string | number;
    updated_at?: string;
  }>;
}
```

### 7.5 事件与快照接口

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/v1/events` | 查询事件日志 |
| GET | `/v1/snapshots/latest` | 获取最新快照 |
| POST | `/v1/replay` | 触发事件重放 |

**GET /v1/snapshots/latest 查询参数：**
```typescript
interface GetLatestSnapshotParams {
  stream_key: string;  // 必填
}
```

### 7.6 监控接口

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/v1/monitor/snapshot` | 获取系统监控快照 |
| GET | `/v1/monitor/alerts` | 获取活跃告警列表 |
| POST | `/v1/monitor/rules` | 添加告警规则 |
| DELETE | `/v1/monitor/rules/{rule_name}` | 删除告警规则 |
| POST | `/v1/monitor/alerts/{rule_name}/clear` | 清除指定告警 |
| POST | `/v1/monitor/alerts/clear-all` | 清除所有告警 |

### 7.7 健康检查

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/` | API 根信息 |
| GET | `/health` | 简单健康检查 |
| GET | `/health/live` | 存活探针 |
| GET | `/health/ready` | 就绪探针 |

---

## 8. WebSocket 接口

### 8.1 当前实现说明

当前系统 WebSocket 主要用于 **Binance 交易所数据流**，而非前端推送。包括：

1. **PublicStreamManager** - 公有流（行情、K线、深度）
2. **PrivateStreamManager** - 私有流（订单、成交）

### 8.2 市场事件格式 (MarketEvent)

```python
@dataclass
class MarketEvent:
    stream: str                    # 流名称 (如 "btcusdt@trade")
    event_type: str                # 事件类型 (如 "trade", "kline")
    data: Dict[str, Any]           # 原始数据
    exchange_ts_ms: int            # 交易所时间戳
    local_receive_ts_ms: int       # 本地接收时间戳
    source: str = "WS"             # 来源
```

**TypeScript 定义：**
```typescript
interface MarketEvent {
  stream: string;
  event_type: string;
  data: Record<string, unknown>;
  exchange_ts_ms: number;
  local_receive_ts_ms: number;
  source: "WS";
}
```

### 8.3 订单更新格式 (RawOrderUpdate)

```python
@dataclass
class RawOrderUpdate:
    cl_ord_id: Optional[str]
    broker_order_id: Optional[str]
    status: str
    filled_qty: float
    avg_price: Optional[float]
    exchange_ts_ms: int
    local_receive_ts_ms: int
    source: str = "WS"
```

**TypeScript 定义：**
```typescript
interface RawOrderUpdate {
  cl_ord_id?: string;
  broker_order_id?: string;
  status: string;
  filled_qty: number;
  avg_price?: number;
  exchange_ts_ms: number;
  local_receive_ts_ms: number;
  source: "WS";
}
```

### 8.4 成交更新格式 (RawFillUpdate)

```python
@dataclass
class RawFillUpdate:
    cl_ord_id: str
    trade_id: int
    exec_type: str
    side: str
    price: float
    qty: float
    commission: float
    exchange_ts_ms: int
    local_receive_ts_ms: int
    source: str = "WS"
```

**TypeScript 定义：**
```typescript
interface RawFillUpdate {
  cl_ord_id: string;
  trade_id: number;
  exec_type: string;
  side: string;
  price: number;
  qty: number;
  commission: number;
  exchange_ts_ms: number;
  local_receive_ts_ms: number;
  source: "WS";
}
```

### 8.5 前端实时数据建议

**当前系统未实现前端 WebSocket 推送服务**。建议前端采用以下方案：

1. **轮询方式**：定期调用 `/v1/monitor/snapshot`、`/v1/orders` 等接口
2. **后续扩展**：可基于 FastAPI WebSocket 实现前端推送服务

---

## 9. 跨域与鉴权配置 (CORS & Auth)

### 9.1 CORS 配置

**当前状态：未配置 CORS**

需要在 `trader/api/main.py` 中添加：

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite 默认端口
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 9.2 鉴权配置

**当前状态：无 API Token 鉴权**

所有接口目前都是公开的。如需添加鉴权，建议：

1. 使用 API Key Header：`X-API-Key: your-api-key`
2. 或使用 JWT Bearer Token：`Authorization: Bearer <token>`

---

## 10. 通用响应模型

### 10.1 操作结果 (ActionResult)

```python
class ActionResult(BaseModel):
    ok: bool
    message: Optional[str] = None
```

**TypeScript 定义：**
```typescript
interface ActionResult {
  ok: boolean;
  message?: string;
}
```

### 10.2 健康检查响应 (HealthResponse)

```python
class HealthResponse(BaseModel):
    status: str = "ok"
    time: str  # RFC3339 格式
```

**TypeScript 定义：**
```typescript
interface HealthResponse {
  status: "ok";
  time: string;  // ISO 8601 / RFC3339
}
```

---

## 11. 前端开发建议

### 11.1 API 客户端生成

推荐使用以下工具从 OpenAPI 规范生成 TypeScript 客户端：

1. **openapi-typescript-codegen** - 生成类型安全的 API 客户端
2. **orval** - 支持 React Query / SWR 集成
3. **openapi-typescript** - 仅生成类型定义

```bash
# 示例：使用 openapi-typescript 生成类型
npx openapi-typescript http://localhost:8080/openapi.json -o src/types/api.ts
```

### 11.2 数值处理注意

**所有数值字段均为字符串类型**（如 `qty: str`, `price: str`），这是为了避免 JavaScript 浮点精度问题。

前端处理时建议：
- 使用 `Decimal.js` 或 `big.js` 进行精确计算
- 显示时使用 `toLocaleString()` 或 `toFixed()`

### 11.3 时间戳格式

- `ts_ms`: 毫秒级 Unix 时间戳 (number)
- `created_at`, `updated_at`: ISO 8601 字符串 (string)

---

## 12. 完整 TypeScript 类型汇总

```typescript
// ==================== Enums ====================
type OrderStatus =
  | "PENDING"
  | "SUBMITTED"
  | "PARTIALLY_FILLED"
  | "FILLED"
  | "CANCELLED"
  | "REJECTED"
  | "CANCEL_PENDING";

type OrderSide = "BUY" | "SELL";
type OrderType = "MARKET" | "LIMIT";
type OrderTimeInForce = "GTC" | "IOC" | "FOK";
type DriftType = "GHOST" | "PHANTOM" | "DIVERGED";
type AlertSeverity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

// ==================== Order Models ====================
interface OrderView {
  cl_ord_id: string;
  trace_id?: string;
  account_id: string;
  strategy_id: string;
  deployment_id?: string;
  venue: string;
  instrument: string;
  side: OrderSide;
  order_type: OrderType;
  qty: string;
  limit_price?: string;
  tif: OrderTimeInForce;
  status: OrderStatus;
  broker_order_id?: string;
  filled_qty: string;
  avg_price?: string;
  created_ts_ms?: number;
  updated_ts_ms?: number;
  reject_code?: string;
  reject_msg?: string;
}

interface ExecutionView {
  cl_ord_id: string;
  exec_id: string;
  ts_ms: number;
  fill_qty: string;
  fill_price: string;
  fee?: string;
  fee_currency?: string;
}

// ==================== Portfolio Models ====================
interface PositionView {
  account_id: string;
  venue: string;
  instrument: string;
  qty: string;
  avg_cost?: string;
  mark_price?: string;
  unrealized_pnl?: string;
  realized_pnl?: string;
  updated_ts_ms?: number;
}

interface PnlView {
  account_id: string;
  venue: string;
  realized_pnl: string;
  unrealized_pnl: string;
  total_pnl: string;
  updated_ts_ms?: number;
}

// ==================== Risk Models ====================
interface KillSwitchState {
  scope: string;
  level: 0 | 1 | 2 | 3;
  reason?: string;
  updated_at?: string;
  updated_by?: string;
}

interface Alert {
  alert_id: string;
  rule_name: string;
  severity: AlertSeverity;
  message: string;
  metric_key: string;
  metric_value: number;
  threshold: number;
  triggered_at: string;
}

// ==================== Reconciliation Models ====================
interface OrderDrift {
  cl_ord_id: string;
  drift_type: DriftType;
  local_status?: string;
  exchange_status?: string;
  detected_at: string;
  grace_period_remaining_sec?: number;
  symbol?: string;
  quantity?: string;
  filled_quantity?: string;
  exchange_filled_quantity?: string;
}

interface ReconcileReport {
  timestamp: string;
  total_orders_checked: number;
  drifts: OrderDrift[];
  ghost_count: number;
  phantom_count: number;
  diverged_count: number;
  within_grace_period_count: number;
}

// ==================== Event Models ====================
interface EventEnvelope {
  event_id?: number;
  stream_key: string;
  event_type: string;
  schema_version: number;
  trace_id?: string;
  ts_ms: number;
  payload: Record<string, unknown>;
}

interface SnapshotEnvelope {
  snapshot_id?: number;
  stream_key: string;
  snapshot_type: string;
  ts_ms: number;
  payload: Record<string, unknown>;
  created_at?: string;
}

// ==================== Monitor Models ====================
interface AdapterHealthStatus {
  adapter_name: string;
  status: "HEALTHY" | "DEGRADED" | "DOWN";
  last_heartbeat_ts_ms?: number;
  error_count: number;
  message?: string;
}

interface MonitorSnapshot {
  timestamp: string;
  total_positions: number;
  total_exposure: string;
  open_orders_count: number;
  pending_orders_count: number;
  daily_pnl: string;
  daily_pnl_pct: string;
  realized_pnl: string;
  unrealized_pnl: string;
  killswitch_level: number;
  killswitch_scope: string;
  adapters: Record<string, AdapterHealthStatus>;
  active_alerts: Alert[];
  alert_count_by_severity: Record<string, number>;
}

// ==================== Common Models ====================
interface ActionResult {
  ok: boolean;
  message?: string;
}

interface HealthResponse {
  status: "ok";
  time: string;
}
```

---

## 附录：OpenAPI 规范获取

启动后端服务后，可通过以下地址获取完整的 OpenAPI 规范：

- **JSON 格式**: `http://localhost:8080/openapi.json`
- **Swagger UI**: `http://localhost:8080/docs`
- **ReDoc**: `http://localhost:8080/redoc`

```bash
# 下载 OpenAPI JSON
curl http://localhost:8080/openapi.json -o openapi.json
```
