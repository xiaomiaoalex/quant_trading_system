export type StrategyCandidateStatus =
  | 'DRAFT'
  | 'DEBUG_PASSED'
  | 'BACKTEST_RUNNING'
  | 'BACKTEST_PASSED'
  | 'VALIDATION_PASSED'
  | 'APPROVED_FOR_PAPER'
  | 'PAPER_RUNNING'
  | 'PAUSED_BY_RISK'
  | 'STOPPED'
  | 'REJECTED'

export const CANDIDATE_STATUS_DISPLAY: Record<StrategyCandidateStatus, { label: string; bgClass: string; textClass: string }> = {
  DRAFT:             { label: 'Draft',             bgClass: 'bg-gray-700',       textClass: 'text-gray-400' },
  DEBUG_PASSED:      { label: 'Debug Passed',      bgClass: 'bg-gray-700',       textClass: 'text-gray-400' },
  BACKTEST_RUNNING:  { label: 'Backtest Running',  bgClass: 'bg-blue-950/40',    textClass: 'text-blue-300' },
  BACKTEST_PASSED:   { label: 'Backtest Passed',    bgClass: 'bg-blue-950/40',    textClass: 'text-blue-300' },
  VALIDATION_PASSED: { label: 'Validated',        bgClass: 'bg-blue-950/40',    textClass: 'text-blue-300' },
  APPROVED_FOR_PAPER:{ label: 'Approved',          bgClass: 'bg-emerald-950/40', textClass: 'text-emerald-300' },
  PAPER_RUNNING:     { label: 'Paper Running',      bgClass: 'bg-blue-950/40',    textClass: 'text-blue-300' },
  PAUSED_BY_RISK:    { label: 'Paused by Risk',    bgClass: 'bg-yellow-950/40',  textClass: 'text-yellow-300' },
  STOPPED:           { label: 'Stopped',           bgClass: 'bg-gray-700',       textClass: 'text-gray-400' },
  REJECTED:          { label: 'Rejected',          bgClass: 'bg-red-950/40',     textClass: 'text-red-300' },
}

export type DataSourceStatusValue = 'available' | 'stub' | 'missing'

export const DATA_SOURCE_STATUS_DISPLAY: Record<DataSourceStatusValue, { label: string; bgClass: string; textClass: string }> = {
  available: { label: 'Available', bgClass: 'bg-emerald-950/40', textClass: 'text-emerald-300' },
  stub:     { label: 'Stub',      bgClass: 'bg-gray-700',       textClass: 'text-gray-400' },
  missing:  { label: 'Missing',  bgClass: 'bg-red-950/40',     textClass: 'text-red-300' },
}

export type BacktestRiskMode = 'raw_only' | 'risk_adjusted' | 'event_replay'

export interface BacktestDatasetSpec {
  symbols: string[]
  start_ts_ms?: number | null
  end_ts_ms?: number | null
  feature_version: string
  venue: string
  initial_capital: number
  fee_bps: number
  slippage_bps: number
  benchmark?: string
  data_mode: 'real_feature_store' | 'dev_smoke'
  risk_mode: BacktestRiskMode
  dataset_id?: string | null
  interval?: string | null
}

// ==================== History data (Binance Vision archives) ====================

export type HistoryJobStatus = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'
export type HistoryDatasetStatus = 'PENDING' | 'PUBLISHED' | 'FAILED'

export interface HistoryJob {
  job_id: string
  source: string
  market: string
  symbol: string
  interval: string
  month: string
  status: HistoryJobStatus
  stage: string
  dataset_id?: string | null
  /** 发布后指向 Research 平面的 `ds_*` 数据集；尚未发布时为 null。 */
  research_dataset_id?: string | null
  feature_version?: string | null
  rows_processed: number
  bytes_downloaded: number
  error_code?: string | null
  error_reason?: string | null
  requested_by: string
  attempt: number
  created_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  updated_at?: string | null
}

export interface HistoryDataset {
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
  /** 对应的 Research 平面数据集 ID（`ds_*`），未桥接时为 null。 */
  research_dataset_id?: string | null
  created_at?: string | null
  published_at?: string | null
}

// ==================== Research plane (datasets / strategies / event backtests) ====================

/**
 * Research 数据集目录条目（只读元数据，不含行情载荷）。
 * 运行时由 `@/contracts/research` 的 Zod schema 校验。
 */
export interface ResearchDatasetSummary {
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

export interface ResearchStrategyParam {
  name: string
  type: string
  default: string | null
  required: boolean
}

/** 后端允许提交事件回测的已安装策略。 */
export interface ResearchStrategySummary {
  module_path: string
  strategy_id: string
  name: string
  version: string
  code_hash: string
  params: ResearchStrategyParam[]
}

export type ResearchBacktestStatus = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED'
export type ResearchBacktestModel = 'event_runtime' | 'legacy'

export interface ResearchEquityPoint {
  at_ms: number
  equity: string
}

export interface ResearchBacktestReportDataset {
  dataset_id: string
  identity: Record<string, unknown>
  quality: Record<string, unknown>
}

export interface ResearchBacktestReport {
  datasets: ResearchBacktestReportDataset[]
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

export interface ResearchBacktestResult {
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
  equity_points?: ResearchEquityPoint[]
  positions?: Record<string, string>
  fills?: Array<Record<string, unknown>>
  rejections?: Record<string, number>
  execution_rejections?: Record<string, number>
  closed_trades?: string[]
  errors?: string[]
  report?: ResearchBacktestReport
}

/** Research 事件回测运行记录（GET /v1/research/backtests*）。 */
export interface ResearchBacktestRun {
  run_id: string
  status: ResearchBacktestStatus
  model: ResearchBacktestModel
  manifest?: Record<string, unknown> | null
  result?: ResearchBacktestResult | null
  error?: string | null
  created_at?: string | number | null
  updated_at?: string | number | null
}

/**
 * 提交事件回测的冻结 manifest。
 *
 * engine / purpose 固定为 event_runtime / research_screening，由前端常量写入；
 * module_path 只能取自后端已安装策略列表。
 */
export interface ResearchBacktestManifest {
  dataset_ids: string[]
  strategy: {
    module_path: string
    params?: Record<string, unknown>
  }
  initial_cash: string
  fee_bps: string
  slippage_bps: string
  engine: 'event_runtime'
  purpose: 'research_screening'
}

export interface ResearchBacktestCreateRequest {
  manifest: ResearchBacktestManifest
}

/** 数据集目录查询参数。 */
export interface ResearchDatasetQuery {
  symbol?: string
  month?: string
  official_only?: boolean
  limit?: number
  offset?: number
}

export interface HistoryJobCreateRequest {
  symbol: string
  month: string
  source?: 'binance_vision'
  market?: 'spot'
  interval?: '1m'
  requested_by?: string
}

export interface BacktestGateResult {
  passed: boolean
  failed_rules: string[]
  metrics: Record<string, unknown>
  evidence_refs: Record<string, string>
}

export interface StrategyCandidate {
  candidate_id: string
  strategy_id: string
  status: StrategyCandidateStatus
  name?: string
  description?: string
  code?: string
  code_version?: number
  config: Record<string, unknown>
  dataset?: BacktestDatasetSpec | null
  feature_version: string
  backtest_run_id?: string | null
  deployment_id?: string | null
  validation?: BacktestGateResult | null
  events: Array<Record<string, unknown>>
  created_at?: string
  updated_at?: string
}

export interface StrategyCandidateCreateRequest {
  strategy_id: string
  name?: string
  description?: string
  code?: string
  code_version?: number
  config?: Record<string, unknown>
  dataset?: BacktestDatasetSpec
  created_by?: string
}

export interface StrategyCandidateDebugRequest {
  code?: string
  config?: Record<string, unknown>
}

export interface StrategyCandidateDebugResponse {
  ok: boolean
  syntax_ok: boolean
  protocol_ok: boolean
  validation_status?: string | null
  checksum?: string | null
  signals: Array<Record<string, unknown>>
  errors: string[]
  warnings: string[]
  candidate?: StrategyCandidate | null
}

export interface StrategyCandidateBacktestRequest {
  dataset: BacktestDatasetSpec
  requested_by?: string
}

export type PromotePaperErrorCode = 'INVALID_STATE' | 'PROMOTE_LOAD_FAILED' | 'PROMOTE_CONFLICT'

export interface PromotePaperResponse {
  candidate_id: string
  strategy_id: string
  deployment_id: string
  code_version?: number | null
  status: 'APPROVED_FOR_PAPER'
  promoted_at: string
}

export interface PromotePaperError {
  error_code: PromotePaperErrorCode
  current_state?: string | null
  required_state?: string | null
  detail: string
  candidate_id: string
}

export interface StrategyAllocationProfile {
  deployment_id: string
  strategy_id: string
  max_notional: number
  max_symbol_exposure: number
  max_portfolio_weight: number
  allocation_mode: 'ABSOLUTE_NOTIONAL' | 'PERCENT_OF_NAV'
  target_weight?: number | null
  hard_cap_notional?: number | null
  nav_source: 'account_equity' | 'paper_nav' | 'manual'
  manual_nav?: number | null
  basis_nav?: number | null
  configured_notional: number
  effective_max_notional: number
  min_confidence: number
  allow_short: boolean
  priority: number
  enabled: boolean
  current_notional: number
  remaining_notional: number
  updated_at?: string
}

export interface StrategyAllocationProfileUpdateRequest {
  strategy_id: string
  max_notional?: number | null
  max_symbol_exposure: number
  max_portfolio_weight: number
  allocation_mode?: 'ABSOLUTE_NOTIONAL' | 'PERCENT_OF_NAV'
  target_weight?: number | null
  hard_cap_notional?: number | null
  nav_source?: 'account_equity' | 'paper_nav' | 'manual'
  manual_nav?: number | null
  min_confidence?: number
  allow_short?: boolean
  priority?: number
  enabled?: boolean
  updated_by?: string
}

export interface AllocationTrace {
  trace_id: string
  deployment_id: string
  strategy_id: string
  symbol: string
  raw_requested_size: number
  risk_sized_qty: number
  allocated_qty: number
  final_order_qty: number
  allocation_decision: 'approved' | 'clipped' | 'rejected'
  reject_or_clip_reason?: string | null
  created_at?: string
}

export interface PortfolioAutopilotDecision {
  decision_id: string
  action: 'START' | 'PAUSE' | 'RESUME' | 'STOP' | 'REDUCE_ALLOCATION' | 'DISABLE_ALLOCATION'
  deployment_id?: string | null
  reason: string
  input_snapshot: Record<string, unknown>
  created_at?: string
  mode: 'paper' | 'shadow' | 'live'
}

export interface PortfolioAutopilotTickRequest {
  kill_switch_level?: number
  data_stale?: boolean
  portfolio_exposure?: number
  max_portfolio_exposure?: number
  deployment_errors?: Record<string, number>
}

export interface PortfolioAutopilotSnapshot {
  ts_ms: number
  kill_switch_level: number
  portfolio_exposure: number
  max_portfolio_exposure: number
  data_stale: boolean
  profiles: StrategyAllocationProfile[]
  decisions: PortfolioAutopilotDecision[]
}

export interface DataSourceStatus {
  source: string
  status: 'available' | 'stub' | 'missing'
  symbols: string[]
  latest_ts_ms?: number | null
  first_ts_ms?: number | null
  feature_version: string
  quality_score: number
  total_points?: number | null
  expected_points?: number | null
  coverage_percent?: number | null
  interval?: string | null
  notes?: string | null
}

export interface DataCatalogResponse {
  feature_version: string
  sources: DataSourceStatus[]
}

export interface OHLCVBarInput {
  ts_ms: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface OHLCVImportRequest {
  symbol: string
  feature_version: string
  interval?: string
  source?: string
  requested_by?: string
  bars: OHLCVBarInput[]
}

export interface OHLCVImportResponse {
  symbol: string
  feature_version: string
  interval: string
  imported: number
  duplicates: number
  first_ts_ms?: number | null
  latest_ts_ms?: number | null
  total_points: number
}

export interface BinanceOHLCVIngestionRequest {
  symbols: string[]
  feature_version: string
  interval?: string
  start_ts_ms?: number | null
  end_ts_ms?: number | null
  lookback_hours?: number
  poll_interval_seconds?: number
  limit?: number
  requested_by?: string
}

export interface BinanceOHLCVSymbolIngestionResult {
  symbol: string
  imported: number
  duplicates: number
  conflicts: number
  first_ts_ms?: number | null
  latest_ts_ms?: number | null
  error?: string | null
}

export interface BinanceOHLCVIngestionResult {
  running: boolean
  feature_version: string
  interval: string
  symbols: string[]
  started_at: string
  finished_at: string
  total_imported: number
  total_duplicates: number
  total_conflicts: number
  last_error?: string | null
  symbol_results: BinanceOHLCVSymbolIngestionResult[]
}

export interface BinanceOHLCVWorkerStatus {
  running: boolean
  feature_version?: string | null
  interval?: string | null
  symbols: string[]
  poll_interval_seconds?: number | null
  last_started_at?: string | null
  last_finished_at?: string | null
  last_error?: string | null
  total_imported: number
  total_duplicates: number
  total_conflicts: number
  last_result?: BinanceOHLCVIngestionResult | null
}
