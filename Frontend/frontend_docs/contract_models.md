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
  research_dataset_id?: string | null  // 发布后指向 Research 平面 ds_*
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
  research_dataset_id?: string | null  // 对应 Research 平面 ds_*
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

## 5.2 Research 数据集 / 已安装策略 / 事件回测模型

运行时由 `src/contracts/research.ts` 的 Zod schema 校验；金额与指标均为十进制字符串。

```typescript
type ResearchBacktestStatus = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED'
type ResearchBacktestModel = 'event_runtime' | 'legacy'

interface ResearchDatasetSummary {
  dataset_id: string
  symbol: string | null
  interval: string | null
  month: string | null
  venue: string | null
  market_type: string | null
  base_asset: string | null
  quote_asset: string | null
  source: string | null
  version: string | null
  bar_count: number | null
  coverage_start_ms: number | null
  coverage_end_ms: number | null
  complete: boolean
  history_dataset_id: string | null
  official_eligible: boolean
  archive_sha256: string | null
  official_sha256: string | null
  source_url: string | null
  checksum_url: string | null
  provenance_hash: string | null
  conversion_version: string | null
  published_at: string | null
  created_at: string | null
}

interface ResearchStrategyParam {
  name: string
  type: string
  default: string | null
  required: boolean
}

interface ResearchStrategySummary {
  module_path: string     // trader.strategies.*
  strategy_id: string
  name: string
  version: string
  code_hash: string
  params: ResearchStrategyParam[]
}

interface ResearchBacktestManifest {
  dataset_ids: string[]   // 单次只允许一个 ds_*（单币、单月）
  strategy: { module_path: string; params?: Record<string, unknown> }
  initial_cash: string
  fee_bps: string
  slippage_bps: string
  engine: 'event_runtime'
  purpose: 'research_screening'
}

interface ResearchBacktestRun {
  run_id: string
  status: ResearchBacktestStatus
  model: ResearchBacktestModel
  manifest?: Record<string, unknown> | null
  result?: ResearchBacktestResult | null
  error?: string | null
  created_at?: string | number | null
  updated_at?: string | number | null
}

interface ResearchBacktestResult {
  engine?: string
  runtime_version?: string
  admissible?: boolean
  manifest_hash?: string
  runtime_manifest_hash?: string
  limitations?: string[]
  net_value?: string
  total_return?: string
  total_fees?: string
  max_drawdown?: string
  max_drawdown_pct?: string
  max_drawdown_amount?: string
  win_rate?: string
  equity_curve?: string[]
  equity_points?: Array<{ at_ms: number; equity: string }>
  positions?: Record<string, string>
  fills?: Array<Record<string, unknown>>
  rejections?: Record<string, number>
  execution_rejections?: Record<string, number>
  closed_trades?: string[]
  errors?: string[]
  report?: ResearchBacktestReport
}

interface ResearchBacktestReport {
  datasets: Array<{
    dataset_id: string
    identity: Record<string, unknown>
    quality: Record<string, unknown>
  }>
  provenance: Record<string, string>
  strategy: {
    module_path: string
    params: Record<string, unknown>
    version: string | null
    code_hash: string | null
  }
  runtime: {
    engine: string
    runtime_version: string
    manifest_hash: string
    runtime_manifest_hash: string
  }
  costs: {
    initial_cash: string
    fee_bps: string
    slippage_bps: string
    total_fees: string
  }
  performance: {
    net_value: string
    total_return: string
    max_drawdown_pct: string
    max_drawdown_amount: string
    win_rate: string
  }
  executions: {
    fill_count: number
    rejections: Record<string, number>
    execution_rejections: Record<string, number>
  }
  limitations: string[]
}
```

补充约定：

- `report.datasets[].identity` 来自冻结数据集，除字符串身份字段外还包含 `schema_version` /
  `interval_ms` / `time_semantics` 等字段，前端按宽松 record 渲染。
- `legacy` 运行的 `result` 结构与事件运行不同，不能作为准入证据；前端只显示「缺少事件回测报告」提示。
- `created_at` / `updated_at` 在 PostgreSQL 部署下为 ISO 字符串；内存 store 返回单调时钟数字，
  展示层统一格式化。

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
