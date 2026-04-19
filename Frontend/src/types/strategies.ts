// Strategy status types
export type StrategyStatus = 'loaded' | 'running' | 'paused' | 'stopped' | 'error'

// Registered strategy from /v1/strategies/registry
export interface RegisteredStrategy {
  strategy_id: string
  name: string
  description?: string
  entrypoint?: string
  language?: string
  version?: string
  created_at?: string
  updated_at?: string
}

// Runtime strategy from /v1/strategies/loaded
export interface StrategyRuntimeInfo {
  strategy_id: string
  version?: string
  status: StrategyStatus
  loaded_at?: string
  started_at?: string
  last_tick_at?: string
  tick_count?: number
  signal_count?: number
  error_count?: number
  last_error?: string
  config?: Record<string, unknown>
  blocked_reason?: string
}

// Strategy event types
export type StrategyEventType = 
  | 'strategy.signal'
  | 'strategy.order.submitted'
  | 'strategy.order.filled'
  | 'strategy.order.cancelled'
  | 'strategy.order.rejected'
  | 'strategy.error'
  | 'strategy.tick'

export interface StrategyEventEnvelope {
  event_id?: number
  stream_key: string
  event_type: StrategyEventType
  schema_version: number
  trace_id?: string
  ts_ms: number
  payload: Record<string, unknown>
}

export interface SignalPayload {
  symbol?: string
  direction?: string
  signal_type?: string
  quantity?: string
  price?: string
  reason?: string
}

export interface OrderPayload {
  symbol?: string
  side?: string
  quantity?: string
  price?: string
  order_id?: string
}

export interface ErrorPayload {
  error_message?: string
  error_details?: Record<string, unknown>
}

export interface StrategyParams {
  [key: string]: unknown
}

export interface StrategySummary {
  total: number
  loaded: number
  running: number
  paused: number
  stopped: number
  error: number
}

export interface StrategyCodeVersion {
  strategy_id: string
  code_version: number
  code: string
  checksum: string
  created_at?: string
  created_by?: string
  notes?: string
}

export interface StrategyCodeCreateRequest {
  strategy_id: string
  code: string
  name?: string
  description?: string
  created_by?: string
  notes?: string
  register_if_missing?: boolean
}

export interface StrategyCodeDebugRequest {
  strategy_id?: string
  code: string
  config?: Record<string, unknown>
  sample_market_data?: Array<Record<string, unknown>>
}

export interface StrategyCodeDebugResponse {
  ok: boolean
  syntax_ok: boolean
  protocol_ok: boolean
  validation_status?: string
  checksum?: string
  signals: Array<Record<string, unknown>>
  errors: string[]
  warnings: string[]
}

// Strategy status display configuration
export const STRATEGY_STATUS_DISPLAY: Record<
  StrategyStatus,
  { label: string; color: string; bgColor: string }
> = {
  loaded: {
    label: 'Loaded',
    color: 'text-blue-400',
    bgColor: 'bg-blue-400/10',
  },
  running: {
    label: 'Running',
    color: 'text-green-400',
    bgColor: 'bg-green-400/10',
  },
  paused: {
    label: 'Paused',
    color: 'text-yellow-400',
    bgColor: 'bg-yellow-400/10',
  },
  stopped: {
    label: 'Stopped',
    color: 'text-gray-400',
    bgColor: 'bg-gray-400/10',
  },
  error: {
    label: 'Error',
    color: 'text-red-400',
    bgColor: 'bg-red-400/10',
  },
}

// Helper to derive summary from loaded strategies runtime info
export function deriveStrategySummary(strategies: StrategyRuntimeInfo[]): StrategySummary {
  return strategies.reduce(
    (acc, s) => {
      acc.total++
      if (s.status === 'loaded') acc.loaded++
      else if (s.status === 'running') acc.running++
      else if (s.status === 'paused') acc.paused++
      else if (s.status === 'stopped') acc.stopped++
      else if (s.status === 'error') acc.error++
      return acc
    },
    { total: 0, loaded: 0, running: 0, paused: 0, stopped: 0, error: 0 }
  )
}
