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

## 4. Crypto Risk 运维模型

```typescript
type CryptoRiskSourceMode = "demo" | "live" | "custom";
type CryptoRiskExecutionEnv = "demo" | "testnet";

interface CryptoRiskBudget {
  symbol_notional_caps: Record<string, string>;
  symbol_clusters: Record<string, string>;
  cluster_notional_caps: Record<string, string>;
  total_notional_cap: string;
  max_margin_ratio: string;
  min_liquidation_buffer_ratio: string;
}

interface CryptoRiskRuntimeStatus {
  enabled: boolean;
  wired: boolean;
  fail_closed: boolean;
  execution_env: CryptoRiskExecutionEnv;
  futures_base_url?: string | null;
  base_symbols: string[];
  risk_budget: CryptoRiskBudget;
  last_error?: string | null;
  updated_at?: string | null;
  updated_by?: string | null;
}

interface CryptoRiskProbeCheck {
  status: "passed" | "failed";
  latency_ms: number;
  message: string;
  details: Record<string, unknown>;
}

interface CryptoRiskProbeResponse {
  ok: boolean;
  read_only: boolean;
  mode: CryptoRiskSourceMode;
  execution_env: CryptoRiskExecutionEnv;
  futures_base_url?: string | null;
  symbols: string[];
  requested_by: string;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  checks: Record<string, CryptoRiskProbeCheck>;
}
```

## 5. 对账与事件模型

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

## 5.1 历史数据模型（币安公开归档）

```typescript
type HistoryJobStatus = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'
type HistoryDatasetStatus = 'PENDING' | 'PUBLISHED' | 'FAILED'

interface HistoryJob {
  job_id: string
  source: string
  market: string
  symbol: string
  interval: string
  month: string
  status: HistoryJobStatus
  stage: string            // QUEUED/DOWNLOAD/VERIFY/IMPORT/PUBLISH/FAILED
  dataset_id?: string | null
  feature_version?: string | null
  rows_processed: number   // 实际处理行数
  bytes_downloaded: number // 实际下载字节数
  error_code?: string | null
  error_reason?: string | null
  requested_by: string
  attempt: number
  created_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  updated_at?: string | null
}

interface HistoryDataset {
  dataset_id: string
  source: string
  market: string
  symbol: string
  interval: string
  month: string
  version: number
  feature_version: string
  archive_sha256: string
  official_sha256?: string | null
  source_url: string
  checksum_url: string
  row_count: number
  expected_points: number
  gap_count: number
  first_ts_ms?: number | null
  last_ts_ms?: number | null
  quality: Record<string, unknown>
  status: HistoryDatasetStatus
  persistence_ok: boolean
  backtest_eligible: boolean
  created_at?: string | null
  published_at?: string | null
}
```

`BacktestRequest` / `BacktestDatasetSpec` 新增可选字段：

```typescript
dataset_id?: string | null  // 已发布历史数据集 ID
interval?: string | null    // 历史数据集固定 '1m'
```

`start_ts_ms` / `end_ts_ms` 变为可选：提供 `dataset_id` 时省略即取整月区间。

## 6. 通用模型

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
