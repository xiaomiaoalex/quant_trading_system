// Drift severity levels
export type DriftSeverity = 'low' | 'medium' | 'high' | 'critical'

// Drift types - must match backend DriftType (GHOST/PHANTOM/DIVERGED)
export type DriftType = 'GHOST' | 'PHANTOM' | 'DIVERGED'

// Reconciliation status
export type ReconcileStatus = 'completed' | 'in_progress' | 'failed' | 'idle'

// Backend DriftResponse structure (from /v1/reconciler/report)
export interface Drift {
  cl_ord_id: string
  drift_type: DriftType
  local_status: string | null
  exchange_status: string | null
  detected_at: string
  symbol: string | null
  quantity: string | null
  filled_quantity: string | null
  exchange_filled_quantity: string | null
  grace_period_remaining_sec: number | null
  ownership: string | null  // "OWNED" / "EXTERNAL" / "UNKNOWN"
}

// Backend ReconcileReportResponse structure
export interface ReconcileReport {
  timestamp: string
  total_orders_checked: number
  drifts: Drift[]
  ghost_count: number
  phantom_count: number
  diverged_count: number
  within_grace_period_count: number
  external_count?: number
}

// Backend EventEnvelope structure (from /v1/events?stream_key=order_drifts)
export interface DriftEvent {
  event_id: number | null
  stream_key: string
  event_type: string
  schema_version?: number
  trace_id: string | null
  ts_ms: number
  payload: {
    cl_ord_id: string
    drift_type: DriftType
    local_status: string | null
    exchange_status: string | null
    symbol: string | null
    quantity: string | null
    filled_quantity: string | null
    exchange_filled_quantity: string | null
    detected_at: string
  }
}

// Display configurations
export const DRIFT_SEVERITY_DISPLAY: Record<DriftSeverity, { label: string; color: string; bgColor: string }> = {
  low: { label: 'Low', color: 'text-gray-400', bgColor: 'bg-gray-400/10' },
  medium: { label: 'Medium', color: 'text-yellow-400', bgColor: 'bg-yellow-400/10' },
  high: { label: 'High', color: 'text-orange-400', bgColor: 'bg-orange-400/10' },
  critical: { label: 'Critical', color: 'text-red-400', bgColor: 'bg-red-400/10' },
}

export const DRIFT_TYPE_DISPLAY: Record<DriftType, { label: string; color: string }> = {
  GHOST: { label: 'Ghost', color: 'text-red-400' },
  PHANTOM: { label: 'Phantom', color: 'text-yellow-400' },
  DIVERGED: { label: 'Diverged', color: 'text-orange-400' },
}

// Note: Backend returns diverged_count, not a status field
// If diverged_count > 0, there are drifts to show
export const RECONCILE_STATUS_DISPLAY: Record<string, { label: string; color: string }> = {
  has_drifts: { label: 'Has Drifts', color: 'text-yellow-400' },
  clean: { label: 'Clean', color: 'text-green-400' },
}