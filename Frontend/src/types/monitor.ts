import type { AdapterHealthStatus, AlertSeverity, KillSwitchLevel } from './api';

// Alert model
export interface Alert {
  alert_id: string;
  rule_name: string;
  severity: AlertSeverity;
  message: string;
  metric_key: string;
  metric_value: number;
  threshold: number;
  triggered_at: string;
}

// Adapter health status model
export interface AdapterHealth {
  adapter_name: string;
  status: AdapterHealthStatus;
  last_heartbeat_ts_ms?: number;
  error_count: number;
  message?: string;
}

// Monitor snapshot model - true aggregation from backend (Task 9.2)
export interface MonitorSnapshot {
  timestamp: string;
  total_positions: number;
  total_exposure: string;
  open_orders_count: number;
  pending_orders_count: number;
  daily_pnl: string;
  daily_pnl_pct: string;
  realized_pnl: string;
  unrealized_pnl: string;
  killswitch_level: KillSwitchLevel;
  killswitch_scope: string;
  adapters: Record<string, AdapterHealth>;
  active_alerts: Alert[];
  alert_count_by_severity: Record<AlertSeverity, number>;
}

// Monitor alerts response
export interface MonitorAlertsResponse {
  alerts: Alert[];
  total_count: number;
}

// Alert rule for creating/updating rules
export interface AlertRule {
  rule_name: string;
  metric_key: string;
  threshold: number;
  comparison: 'gt' | 'lt' | 'gte' | 'lte' | 'eq';
  severity: AlertSeverity;
  cooldown_seconds?: number;
}

// Clear alert request
export interface ClearAlertRequest {
  rule_name: string;
  reason?: string;
}

// System health derived state
export type SystemHealthState = 'healthy' | 'degraded' | 'stale' | 'down';

// Derived health state calculation
export function deriveSystemHealthState(snapshot: MonitorSnapshot | null, isStale: boolean): SystemHealthState {
  if (!snapshot) return 'down';
  if (isStale) return 'stale';

  // Check killswitch level
  if (snapshot.killswitch_level >= 2) return 'down';

  // Check adapter statuses
  const adapters = Object.values(snapshot.adapters);
  if (adapters.some((a) => a.status === 'DOWN')) return 'down';
  if (adapters.some((a) => a.status === 'DEGRADED')) return 'degraded';

  // Check for critical alerts
  if ((snapshot.alert_count_by_severity['CRITICAL'] ?? 0) > 0) return 'degraded';

  return 'healthy';
}

// Snapshot freshness check (stale if older than 60 seconds)
export function isSnapshotStale(snapshot: MonitorSnapshot | null, thresholdMs = 60_000): boolean {
  if (!snapshot) return true;
  const snapshotTime = new Date(snapshot.timestamp).getTime();
  const now = Date.now();
  return now - snapshotTime > thresholdMs;
}
