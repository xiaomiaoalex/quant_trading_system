/**
 * Portfolio types — strategy-level position tracking (Batch 3)
 */

// 策略级持仓视图
export interface StrategyPositionDetail {
  strategy_id: string
  symbol: string
  qty: string
  avg_cost: string
  realized_pnl: string
  unrealized_pnl: string
  total_cost: string | null
  status: 'ACTIVE' | 'CLOSED' | 'HISTORICAL'
  lot_count: number
  cost_basis_method: string
  updated_at: string | null
}

// Lot 明细
export interface LotDetail {
  lot_id: string
  strategy_id: string
  symbol: string
  original_qty: string
  remaining_qty: string
  fill_price: string
  fee_qty: string | null
  fee_asset: string | null
  realized_pnl: string
  is_closed: boolean
  filled_at: string
}

// 持仓分解（三层视图）
export interface PositionBreakdown {
  symbol: string
  account_qty: string
  account_avg_cost: string | null
  strategy_positions: StrategyPositionDetail[]
  historical: Record<string, unknown> | null
  is_reconciled: boolean
  difference: string | null
  tolerance: string
}

// 对账结果
export interface ReconciliationResult {
  symbol: string
  broker_qty: string
  oms_total_qty: string
  historical_qty: string
  difference: string
  tolerance: string
  status: 'CONSISTENT' | 'DISCREPANCY' | 'HISTORICAL_GAP'
  action_taken: string | null
}
