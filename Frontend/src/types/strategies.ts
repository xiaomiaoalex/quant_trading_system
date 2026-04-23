// Target path: Frontend/src/types/strategies.ts

export type StrategyStatus = 'loaded' | 'running' | 'paused' | 'stopped' | 'error'
export type DeploymentMode = 'paper' | 'demo' | 'live' | 'shadow'

// Registered strategy template from /v1/strategies/registry
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

// Loaded runtime deployment from /v1/strategies/loaded
export interface StrategyRuntimeInfo {
  deployment_id: string
  strategy_id: string
  version: string
  status: StrategyStatus
  symbols: string[]
  account_id: string
  venue: string
  mode: DeploymentMode
  loaded_at: string | null
  started_at: string | null
  last_tick_at: string | null
  tick_count: number
  signal_count: number
  error_count: number
  last_error: string | null
  stop_reason: string | null
  config: Record<string, unknown>
  blocked_reason: string | null
}

export interface LoadStrategyPayload {
  deployment_id?: string
  module_path?: string
  code?: string
  code_version?: number
  version?: string
  config?: Record<string, unknown>
  symbols: string[]
  account_id: string
  venue: string
  mode: DeploymentMode
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
  totalTemplates: number
  totalDeployments: number
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

export interface TradingPairInfo {
  symbol: string
  base_asset: string
  quote_asset: string
  status: string
  min_notional: number
  min_qty: number
  max_qty: number
  step_size: number
  tick_size: number
}

export interface TradingPairsResponse {
  pairs: TradingPairInfo[]
  total: number
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

export function deriveStrategySummary(
  strategies: StrategyRuntimeInfo[],
  templateCount = 0,
): StrategySummary {
  return strategies.reduce<StrategySummary>(
    (acc, s) => {
      acc.totalDeployments++
      if (s.status === 'loaded') acc.loaded++
      else if (s.status === 'running') acc.running++
      else if (s.status === 'paused') acc.paused++
      else if (s.status === 'stopped') acc.stopped++
      else if (s.status === 'error') acc.error++
      return acc
    },
    {
      totalTemplates: templateCount,
      totalDeployments: 0,
      loaded: 0,
      running: 0,
      paused: 0,
      stopped: 0,
      error: 0,
    },
  )
}

export function groupRuntimeByStrategy(
  runtimes: StrategyRuntimeInfo[],
): Map<string, StrategyRuntimeInfo[]> {
  const grouped = new Map<string, StrategyRuntimeInfo[]>()
  for (const runtime of runtimes) {
    const current = grouped.get(runtime.strategy_id) ?? []
    current.push(runtime)
    grouped.set(runtime.strategy_id, current)
  }
  return grouped
}

export function buildDeploymentId(
  strategyId: string,
  symbol: string,
  mode: DeploymentMode,
  accountId: string,
): string {
  return `${strategyId}__${symbol.toLowerCase()}__${mode}__${accountId.toLowerCase()}`
}