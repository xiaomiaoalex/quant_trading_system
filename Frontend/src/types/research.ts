import type { DeploymentMode } from './strategies'

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

export interface BacktestDatasetSpec {
  symbols: string[]
  start_ts_ms: number
  end_ts_ms: number
  feature_version: string
  venue: string
  initial_capital: number
  fee_bps: number
  slippage_bps: number
  benchmark?: string
  data_mode: 'real_feature_store' | 'dev_smoke'
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

export interface StrategyCandidatePromoteRequest {
  deployment_id?: string
  symbols: string[]
  account_id: string
  venue: string
  mode: DeploymentMode
  version?: string
  config?: Record<string, unknown>
}

export interface StrategyAllocationProfile {
  deployment_id: string
  strategy_id: string
  max_notional: number
  max_symbol_exposure: number
  max_portfolio_weight: number
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
  max_notional: number
  max_symbol_exposure: number
  max_portfolio_weight: number
  min_confidence?: number
  allow_short?: boolean
  priority?: number
  enabled?: boolean
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
  feature_version: string
  quality_score: number
  notes?: string | null
}

export interface DataCatalogResponse {
  feature_version: string
  sources: DataSourceStatus[]
}
