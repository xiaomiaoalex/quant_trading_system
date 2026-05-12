import { z } from 'zod'

const StringMapSchema = z.record(z.string())
const EventPayloadSchema = z.record(z.unknown())

export const CryptoRiskExecutionEnvSchema = z.enum(['demo', 'testnet'])
export const CryptoRiskSourceModeSchema = z.enum(['demo', 'live', 'custom'])
export const CryptoRiskProbeCheckStatusSchema = z.enum(['passed', 'failed'])

export const CryptoRiskBudgetSchema = z.object({
  symbol_notional_caps: StringMapSchema,
  symbol_clusters: StringMapSchema,
  cluster_notional_caps: StringMapSchema,
  total_notional_cap: z.string(),
  max_margin_ratio: z.string(),
  min_liquidation_buffer_ratio: z.string(),
  max_abs_funding_rate_z_score: z.string(),
  max_abs_open_interest_change_rate: z.string(),
  funding_history_window: z.number().int(),
  oi_history_window: z.number().int(),
  funding_min_periods: z.number().int(),
  oi_min_periods: z.number().int(),
  max_data_age_seconds: z.number().int(),
})

export const CryptoRiskRuntimeStatusSchema = z.object({
  enabled: z.boolean(),
  wired: z.boolean(),
  fail_closed: z.boolean(),
  execution_env: CryptoRiskExecutionEnvSchema,
  futures_base_url: z.string().nullable().optional(),
  base_symbols: z.array(z.string()),
  risk_budget: CryptoRiskBudgetSchema,
  last_error: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
  updated_by: z.string().nullable().optional(),
})

export const CryptoRiskProbeCheckSchema = z.object({
  status: CryptoRiskProbeCheckStatusSchema,
  latency_ms: z.number(),
  message: z.string(),
  details: z.record(z.unknown()),
})

export const CryptoRiskProbeResponseSchema = z.object({
  ok: z.boolean(),
  read_only: z.boolean(),
  mode: CryptoRiskSourceModeSchema,
  execution_env: CryptoRiskExecutionEnvSchema,
  futures_base_url: z.string().nullable().optional(),
  symbols: z.array(z.string()),
  requested_by: z.string(),
  started_at: z.string(),
  finished_at: z.string(),
  duration_ms: z.number(),
  checks: z.record(CryptoRiskProbeCheckSchema),
})

export const CryptoRiskEventEnvelopeSchema = z.object({
  event_id: z.number().int().nullable().optional(),
  stream_key: z.string(),
  event_type: z.string(),
  schema_version: z.number().int(),
  trace_id: z.string().nullable().optional(),
  ts_ms: z.number().int(),
  payload: EventPayloadSchema,
})

export const CryptoRiskEventEnvelopeListSchema = z.array(CryptoRiskEventEnvelopeSchema)
