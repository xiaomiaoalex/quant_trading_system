/**
 * useStrategyPositions — 策略级持仓查询 Hook（Batch 3）
 */
import { useQuery } from '@tanstack/react-query'
import { portfolioAPI } from '@/api'
import {
  StrategyPositionDetailSchema,
  PositionBreakdownSchema,
  LotDetailSchema,
} from '@/contracts/portfolio'

// Query keys
export const portfolioKeys = {
  all: ['portfolio'] as const,
  strategyPositions: (filters?: { strategy_id?: string; symbol?: string }) =>
    [...portfolioKeys.all, 'strategyPositions', filters ?? {}] as const,
  breakdown: (symbol: string) =>
    [...portfolioKeys.all, 'breakdown', symbol] as const,
  lots: (symbol: string, strategy_id: string) =>
    [...portfolioKeys.all, 'lots', symbol, strategy_id] as const,
}

// ─── Strategy Positions ──────────────────────────────────────────────────────

export function useStrategyPositions(filters?: {
  strategy_id?: string
  symbol?: string
}) {
  return useQuery({
    queryKey: portfolioKeys.strategyPositions(filters),
    queryFn: async () => {
      const raw = await portfolioAPI.getStrategyPositions(filters)
      const parsed = StrategyPositionDetailSchema.array().safeParse(raw)
      if (!parsed.success) {
        console.error('StrategyPosition validation failed:', parsed.error.flatten())
        throw new Error(
          `strategy_positions schema mismatch: ${parsed.error.errors.map((e) => e.message).join(', ')}`
        )
      }
      return parsed.data
    },
    staleTime: 5_000,
  })
}

// ─── Position Breakdown ──────────────────────────────────────────────────────

export function usePositionBreakdown(symbol: string) {
  return useQuery({
    queryKey: portfolioKeys.breakdown(symbol),
    queryFn: async () => {
      const raw = await portfolioAPI.getPositionBreakdown(symbol)
      const parsed = PositionBreakdownSchema.safeParse(raw)
      if (!parsed.success) {
        console.error('PositionBreakdown validation failed:', parsed.error.flatten())
        throw new Error(
          `position_breakdown schema mismatch: ${parsed.error.errors.map((e) => e.message).join(', ')}`
        )
      }
      return parsed.data
    },
    enabled: !!symbol,
    staleTime: 5_000,
  })
}

// ─── Lot Details ─────────────────────────────────────────────────────────────

export function usePositionLots(symbol: string, strategy_id: string) {
  return useQuery({
    queryKey: portfolioKeys.lots(symbol, strategy_id),
    queryFn: async () => {
      const raw = await portfolioAPI.getPositionLots(symbol, strategy_id)
      const parsed = LotDetailSchema.array().safeParse(raw)
      if (!parsed.success) {
        console.error('LotDetail validation failed:', parsed.error.flatten())
        throw new Error(
          `lots schema mismatch: ${parsed.error.errors.map((e) => e.message).join(', ')}`
        )
      }
      return parsed.data
    },
    enabled: !!symbol && !!strategy_id,
    staleTime: 5_000,
  })
}
