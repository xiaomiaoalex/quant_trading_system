/**
 * Monitor API runtime contracts — Zod schemas for backend response validation.
 *
 * These schemas guard against field-name drift, type mismatches, and silent
 * data loss. Every GET response from the monitor endpoints is parsed through
 * the corresponding schema before entering the rest of the frontend.
 */
import { z } from 'zod'

// ─── Reusable primitives ─────────────────────────────────────────────────────

/** Strict string that rejects empty/whitespace-only values. */
const NonEmptyString = z.string().min(1, 'Expected non-empty string')

/** Nullable decimal string (e.g. "123.45" or null). */
const NullableDecimal = z.string().nullable()

/** ISO 8601 timestamp string. */
const ISOTimestamp = z.string().datetime()

// ─── Enums ───────────────────────────────────────────────────────────────────

export const AlertSeveritySchema = z.enum(['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'])
export type AlertSeverity = z.infer<typeof AlertSeveritySchema>

export const AdapterHealthStatusSchema = z.enum(['HEALTHY', 'DEGRADED', 'DOWN'])
export type AdapterHealthStatus = z.infer<typeof AdapterHealthStatusSchema>

export const KillSwitchLevelSchema = z.enum(['0', '1', '2', '3'])
export type KillSwitchLevel = z.infer<typeof KillSwitchLevelSchema>

// ─── Alert ───────────────────────────────────────────────────────────────────

export const AlertSchema = z.object({
  alert_id: NonEmptyString,
  rule_name: NonEmptyString,
  severity: AlertSeveritySchema,
  message: NonEmptyString,
  metric_key: NonEmptyString,
  metric_value: z.number(),
  threshold: z.number(),
  triggered_at: ISOTimestamp,
})
export type Alert = z.infer<typeof AlertSchema>

// ─── AdapterHealth ───────────────────────────────────────────────────────────

export const AdapterHealthSchema = z.object({
  adapter_name: NonEmptyString,
  status: AdapterHealthStatusSchema,
  last_heartbeat_ts_ms: z.number().int().positive().nullable(),
  error_count: z.number().int().min(0),
  message: z.string().nullable(),
})
export type AdapterHealth = z.infer<typeof AdapterHealthSchema>

// ─── PositionDetail ──────────────────────────────────────────────────────────

export const PositionDetailSchema = z.object({
  symbol: NonEmptyString,
  quantity: NonEmptyString,
  avg_cost: NullableDecimal,
  current_price: NullableDecimal,
  unrealized_pnl: NullableDecimal,
  exposure: NullableDecimal,
})
export type PositionDetail = z.infer<typeof PositionDetailSchema>

// ─── MonitorSnapshot ─────────────────────────────────────────────────────────

export const MonitorSnapshotSchema = z.object({
  timestamp: ISOTimestamp,
  total_positions: z.number().int().min(0),
  total_exposure: NonEmptyString,
  positions: z.array(PositionDetailSchema),
  open_orders_count: z.number().int().min(0),
  pending_orders_count: z.number().int().min(0),
  daily_pnl: NonEmptyString,
  daily_pnl_pct: NonEmptyString,
  realized_pnl: NonEmptyString,
  unrealized_pnl: NonEmptyString,
  killswitch_level: z.union([z.literal(0), z.literal(1), z.literal(2), z.literal(3)]),
  killswitch_scope: NonEmptyString,
  adapters: z.record(AdapterHealthSchema),
  active_alerts: z.array(AlertSchema),
  alert_count_by_severity: z.record(z.number().int().min(0)),
  // OMS observability — all optional
  tick_rate: z.number().optional(),
  tick_lag_ms: z.number().optional(),
  order_submit_ok: z.number().int().min(0).optional(),
  order_submit_reject: z.number().int().min(0).optional(),
  order_submit_error: z.number().int().min(0).optional(),
  reject_reason_counts: z.record(z.number().int().min(0)).optional(),
  fill_latency_ms_avg: z.number().optional(),
  fill_latency_count: z.number().int().min(0).optional(),
  ws_reconnect_count: z.number().int().min(0).optional(),
  cl_ord_id_dedup_hits: z.number().int().min(0).optional(),
  exec_dedup_hits: z.number().int().min(0).optional(),
  // Meta
  snapshot_source: z.string().optional(),
  freshness: ISOTimestamp.optional(),
})
export type MonitorSnapshot = z.infer<typeof MonitorSnapshotSchema>

// ─── MonitorAlertsResponse ───────────────────────────────────────────────────

export const MonitorAlertsResponseSchema = z.object({
  alerts: z.array(AlertSchema),
  total_count: z.number().int().min(0),
})
export type MonitorAlertsResponse = z.infer<typeof MonitorAlertsResponseSchema>

// ─── AlertRule ───────────────────────────────────────────────────────────────

export const AlertRuleSchema = z.object({
  rule_name: NonEmptyString,
  metric_key: NonEmptyString,
  threshold: z.number(),
  comparison: z.enum(['gt', 'lt', 'gte', 'lte', 'eq']),
  severity: AlertSeveritySchema,
  cooldown_seconds: z.number().int().min(0).nullable().optional(),
})
export type AlertRule = z.infer<typeof AlertRuleSchema>
