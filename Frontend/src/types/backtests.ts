// Backtest status types
export type BacktestStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED'

// Backtest run from GET /v1/backtests and GET /v1/backtests/{run_id}
export interface BacktestRun {
  run_id: string
  status: BacktestStatus
  strategy_id: string
  version: number
  strategy_code_version?: number
  symbols: string[]
  start_ts_ms: number
  end_ts_ms: number
  metrics?: Record<string, unknown>
  artifact_ref?: string
  created_at?: string
  progress?: number
  started_at?: string
  finished_at?: string
  error?: string
}

// Backtest request for POST /v1/backtests
export interface BacktestRequest {
  strategy_id: string
  version: number
  strategy_code_version?: number
  params?: Record<string, unknown>
  symbols: string[]
  start_ts_ms: number
  end_ts_ms: number
  venue: string
  requested_by: string
}

// Backtest report from GET /v1/backtests/{run_id}/report
export interface BacktestReport {
  run_id: string
  status: BacktestStatus
  strategy_id: string
  version: number
  symbols: string[]
  start_ts_ms: number
  end_ts_ms: number
  created_at?: string
  started_at?: string
  finished_at?: string
  error?: string
  returns?: {
    total_return?: number
    total_return_pct?: number
    annualized_return?: number
    sharpe_ratio?: number
  }
  risk?: {
    max_drawdown?: number
    max_drawdown_pct?: number
    volatility?: number
    var_95?: number
  }
  trades?: Array<{
    trade_id: string
    symbol: string
    side: string
    price: number
    quantity: number
    timestamp: string
  }>
  equity_curve?: Array<{
    timestamp: number
    equity: number
  }>
  metrics?: Record<string, unknown>
  artifact_ref?: string
}

// Display configuration for status
export const BACKTEST_STATUS_DISPLAY: Record<BacktestStatus, { label: string; color: string; bgColor: string }> = {
  PENDING: { label: 'Pending', color: 'text-gray-400', bgColor: 'bg-gray-400/10' },
  RUNNING: { label: 'Running', color: 'text-blue-400', bgColor: 'bg-blue-400/10' },
  COMPLETED: { label: 'Completed', color: 'text-green-400', bgColor: 'bg-green-400/10' },
  FAILED: { label: 'Failed', color: 'text-red-400', bgColor: 'bg-red-400/10' },
}
