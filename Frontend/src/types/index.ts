export * from './api'
export * from './monitor'
export * from './strategies'
export * from './reconcile'
export * from './backtests'
export * from './chat'
export * from './audit'
export * from './replay'
export * from './research'
export * from './cryptoRisk'

// Unified spot trading control-plane contracts
export type CommandStatus = 'QUEUED' | 'SENDING' | 'UNKNOWN' | 'CONFIRMED' | 'FAILED'
export type OrderStatus =
  | 'PENDING_ACCEPTANCE'
  | 'OPEN'
  | 'PARTIALLY_FILLED'
  | 'FILLED'
  | 'CANCELLED'
  | 'EXPIRED'
  | 'REJECTED'

export interface CommandReceipt {
  command_id: string
  cl_ord_id: string
  kind: 'PLACE' | 'CANCEL' | string
  status: CommandStatus
  order_status: OrderStatus | ''
  error: string
}

export interface CommandRecord extends CommandReceipt {
  actor: string
}

export interface OrderSnapshot {
  cl_ord_id: string
  status: OrderStatus
  broker_order_id: string
  symbol: string
  base_asset: string
  quote_asset: string
  side: 'BUY' | 'SELL'
  qty: string
  price: string
  order_type: 'MARKET' | 'LIMIT'
  booked_qty: string
  observed_qty: string
  remaining_qty: string
  deployment_id: string
}

export interface AssetRiskView {
  total: string
  external_locked: string
  held: string
  available: string
}

export interface AccountRiskView {
  account_id: string
  status: 'RECOVERING' | 'READY' | 'BLOCKED'
  account_version: number
  generation: number
  checked_at_ms: number | null
  reasons: string[]
  assets: Record<string, AssetRiskView>
  positions: Record<string, string>
  valuation_complete: boolean
}

export interface TradingMetrics {
  status: string
  unknown_commands: number
  unbooked_fills: number
  balance_anomalies: number
  active_holds: number
  reconciliation_age_ms: number | null
  outbox_backlog: number
  unresolved: string[]
}

export interface PlaceOrderRequest {
  command_id: string
  account_id: string
  symbol: string
  base_asset: string
  quote_asset: string
  side: 'BUY' | 'SELL'
  qty: string
  price: string
  deployment_id?: string
  order_type?: 'MARKET' | 'LIMIT'
  time_in_force?: string
  decision_id?: string
}
