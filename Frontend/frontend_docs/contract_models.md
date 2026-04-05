# Contract Models

## 1. 核心状态枚举

```typescript
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
```

## 2. 交易与持仓模型

```typescript
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
```

## 3. 风控与监控模型

```typescript
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

## 4. 对账与事件模型

```typescript
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
```

## 5. 通用模型

```typescript
interface ActionResult {
  ok: boolean;
  message?: string;
}

interface HealthResponse {
  status: string;
  time: string;
}
```

