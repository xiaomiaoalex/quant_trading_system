export type CryptoRiskSourceMode = 'demo' | 'live' | 'custom'
export type CryptoRiskExecutionEnv = 'demo' | 'testnet'
export type CryptoRiskProbeCheckStatus = 'passed' | 'failed'

export interface CryptoRiskBudget {
  symbol_notional_caps: Record<string, string>
  symbol_clusters: Record<string, string>
  cluster_notional_caps: Record<string, string>
  total_notional_cap: string
  max_margin_ratio: string
  min_liquidation_buffer_ratio: string
  max_abs_funding_rate_z_score: string
  max_abs_open_interest_change_rate: string
  funding_history_window: number
  oi_history_window: number
  funding_min_periods: number
  oi_min_periods: number
  max_data_age_seconds: number
}

export interface CryptoRiskRuntimeStatus {
  enabled: boolean
  wired: boolean
  fail_closed: boolean
  execution_env: CryptoRiskExecutionEnv
  futures_base_url?: string | null
  base_symbols: string[]
  risk_budget: CryptoRiskBudget
  last_error?: string | null
  updated_at?: string | null
  updated_by?: string | null
}

export interface CryptoRiskBudgetUpdateRequest {
  symbol_notional_caps?: Record<string, string>
  symbol_clusters?: Record<string, string>
  cluster_notional_caps?: Record<string, string>
  total_notional_cap?: string
  max_margin_ratio?: string
  min_liquidation_buffer_ratio?: string
  max_abs_funding_rate_z_score?: string
  max_abs_open_interest_change_rate?: string
  funding_history_window?: number
  oi_history_window?: number
  funding_min_periods?: number
  oi_min_periods?: number
  max_data_age_seconds?: number
  updated_by: string
}

export interface CryptoRiskProbeRequest {
  symbols: string[]
  requested_by: string
}

export interface CryptoRiskAuditFilters {
  event_type?: string
  trace_id?: string
  signal_id?: string
  limit?: number
}

export interface CryptoRiskProbeCheck {
  status: CryptoRiskProbeCheckStatus
  latency_ms: number
  message: string
  details: Record<string, unknown>
}

export interface CryptoRiskProbeResponse {
  ok: boolean
  read_only: boolean
  mode: CryptoRiskSourceMode
  execution_env: CryptoRiskExecutionEnv
  futures_base_url?: string | null
  symbols: string[]
  requested_by: string
  started_at: string
  finished_at: string
  duration_ms: number
  checks: Record<string, CryptoRiskProbeCheck>
}

export interface CryptoRiskEventEnvelope {
  event_id?: number | null
  stream_key: string
  event_type: string
  schema_version: number
  trace_id?: string | null
  ts_ms: number
  payload: Record<string, unknown>
}
