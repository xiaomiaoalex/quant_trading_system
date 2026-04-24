/**
 * Portfolio API runtime contracts — Zod schemas for backend response validation.
 */
import { z } from 'zod'

const NonEmptyString = z.string().min(1)
const NullableDecimal = z.string().nullable()

// ─── StrategyPositionDetail ───────────────────────────────────────────────────

export const StrategyPositionDetailSchema = z.object({
  strategy_id: NonEmptyString,
  symbol: NonEmptyString,
  qty: NonEmptyString,
  avg_cost: NonEmptyString,
  realized_pnl: NonEmptyString,
  unrealized_pnl: NonEmptyString,
  total_cost: NullableDecimal,
  status: z.enum(['ACTIVE', 'CLOSED', 'HISTORICAL']),
  lot_count: z.number().int().min(0),
  cost_basis_method: NonEmptyString,
  updated_at: z.string().datetime().nullable(),
})
export type StrategyPositionDetail = z.infer<typeof StrategyPositionDetailSchema>

// ─── LotDetail ────────────────────────────────────────────────────────────────

export const LotDetailSchema = z.object({
  lot_id: NonEmptyString,
  strategy_id: NonEmptyString,
  symbol: NonEmptyString,
  original_qty: NonEmptyString,
  remaining_qty: NonEmptyString,
  fill_price: NonEmptyString,
  fee_qty: NullableDecimal,
  fee_asset: z.string().nullable(),
  realized_pnl: NonEmptyString,
  is_closed: z.boolean(),
  filled_at: z.string().datetime(),
})
export type LotDetail = z.infer<typeof LotDetailSchema>

// ─── PositionBreakdown ────────────────────────────────────────────────────────

export const PositionBreakdownSchema = z.object({
  symbol: NonEmptyString,
  account_qty: NonEmptyString,
  account_avg_cost: NullableDecimal,
  strategy_positions: z.array(StrategyPositionDetailSchema),
  historical: z.record(z.unknown()).nullable(),
  is_reconciled: z.boolean(),
  difference: NullableDecimal,
  tolerance: NonEmptyString,
})
export type PositionBreakdown = z.infer<typeof PositionBreakdownSchema>

// ─── ReconciliationResult ────────────────────────────────────────────────────

export const ReconciliationResultSchema = z.object({
  symbol: NonEmptyString,
  broker_qty: NonEmptyString,
  oms_total_qty: NonEmptyString,
  historical_qty: NonEmptyString,
  difference: NonEmptyString,
  tolerance: NonEmptyString,
  status: z.enum(['CONSISTENT', 'DISCREPANCY', 'HISTORICAL_GAP']),
  action_taken: z.string().nullable(),
})
export type ReconciliationResult = z.infer<typeof ReconciliationResultSchema>
