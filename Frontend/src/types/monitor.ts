import type { AdapterHealthStatus, AlertSeverity, KillSwitchLevel } from './api'

// Alert model
export interface Alert {
  alert_id: string
  rule_name: string
  severity: AlertSeverity
  message: string
  metric_key: string
  metric_value: number
  threshold: number
  triggered_at: string
}

// Adapter health status model
export interface AdapterHealth {
  adapter_name: string
  status: AdapterHealthStatus
  last_heartbeat_ts_ms: number | null
  error_count: number
  message: string | null
}

// Position detail for detailed position display
export interface PositionDetail {
  symbol: string
  quantity: string
  avg_cost: string | null
  current_price: string | null
  unrealized_pnl: string | null
  exposure: string | null
}

// Monitor snapshot model - true aggregation from backend (Task 9.2)
export interface MonitorSnapshot {
  timestamp: string
  total_positions: number
  total_exposure: string
  positions: PositionDetail[]
  open_orders_count: number
  pending_orders_count: number
  daily_pnl: string
  daily_pnl_pct: string
  realized_pnl: string
  unrealized_pnl: string
  killswitch_level: KillSwitchLevel
  killswitch_scope: string
  adapters: Record<string, AdapterHealth>
  active_alerts: Alert[]
  alert_count_by_severity: Partial<Record<AlertSeverity, number>>
  // Task 19: OMS 可观测性指标 (null 来自后端，表示未初始化)
  tick_rate?: number | null
  tick_lag_ms?: number | null
  order_submit_ok?: number | null
  order_submit_reject?: number | null
  order_submit_error?: number | null
  reject_reason_counts?: Record<string, number> | null
  fill_latency_ms_avg?: number | null
  fill_latency_count?: number | null
  ws_reconnect_count?: number | null
  cl_ord_id_dedup_hits?: number | null
  exec_dedup_hits?: number | null
  snapshot_source?: string
  freshness?: string | null
}

// Monitor alerts response
export interface MonitorAlertsResponse {
  alerts: Alert[]
  total_count: number
}

// Alert rule for creating/updating rules
export interface AlertRule {
  rule_name: string
  metric_key: string
  threshold: number
  comparison: 'gt' | 'lt' | 'gte' | 'lte' | 'eq'
  severity: AlertSeverity
  cooldown_seconds: number | null
}

// Clear alert request
export interface ClearAlertRequest {
  rule_name: string
  reason?: string
}

// System health derived state
export type SystemHealthState = 'healthy' | 'degraded' | 'stale' | 'down'

// Derived health state calculation
export function deriveSystemHealthState(
  snapshot: MonitorSnapshot | null,
  isStale: boolean
): SystemHealthState {
  if (!snapshot) return 'down'
  if (isStale) return 'stale'

  // Check killswitch level
  if (snapshot.killswitch_level >= 2) return 'down'

  // Check adapter statuses
  const adapters = Object.values(snapshot.adapters)
  if (adapters.some(a => a.status === 'DOWN')) return 'down'
  if (adapters.some(a => a.status === 'DEGRADED')) return 'degraded'

  // Check for critical alerts
  if ((snapshot.alert_count_by_severity['CRITICAL'] ?? 0) > 0) return 'degraded'

  return 'healthy'
}

// Snapshot freshness check (stale if older than 60 seconds)
// 优先使用 freshness 字段，回退到 timestamp（与后端同步）
export function isSnapshotStale(snapshot: MonitorSnapshot | null, thresholdMs = 60_000): boolean {
  if (!snapshot) return true
  const timeField = snapshot.freshness ?? snapshot.timestamp
  const snapshotTime = new Date(timeField).getTime()
  const now = Date.now()
  return now - snapshotTime > thresholdMs
}
