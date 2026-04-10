// Drift severity levels
export type DriftSeverity = 'low' | 'medium' | 'high' | 'critical'

// Drift types
export type DriftType = 'price' | 'quantity' | 'side' | 'missing'

// Reconciliation status
export type ReconcileStatus = 'completed' | 'in_progress' | 'failed' | 'idle'

export interface Drift {
  drift_id: string
  order_id: string
  local_side?: string
  exchange_side?: string
  local_price?: string
  exchange_price?: string
  local_quantity?: string
  exchange_quantity?: string
  drift_type: DriftType
  severity: DriftSeverity
  detected_at: string
}

// Backend response for /v1/reconciler/report
export interface ReconcileReport {
  timestamp: string
  total_orders_checked: number
  drifts: Drift[]
  ghost_count: number
  phantom_count: number
  diverged_count: number
  within_grace_period_count: number
}

export interface DriftEvent {
  event_id: string
  stream_key: string
  trace_id: string
  event_type: string
  timestamp: string
  data: Drift
}

// Display configurations
export const DRIFT_SEVERITY_DISPLAY: Record<DriftSeverity, { label: string; color: string; bgColor: string }> = {
  low: { label: 'Low', color: 'text-gray-400', bgColor: 'bg-gray-400/10' },
  medium: { label: 'Medium', color: 'text-yellow-400', bgColor: 'bg-yellow-400/10' },
  high: { label: 'High', color: 'text-orange-400', bgColor: 'bg-orange-400/10' },
  critical: { label: 'Critical', color: 'text-red-400', bgColor: 'bg-red-400/10' },
}

export const DRIFT_TYPE_DISPLAY: Record<DriftType, { label: string; color: string }> = {
  price: { label: 'Price', color: 'text-blue-400' },
  quantity: { label: 'Quantity', color: 'text-purple-400' },
  side: { label: 'Side', color: 'text-yellow-400' },
  missing: { label: 'Missing', color: 'text-red-400' },
}

// Note: Backend returns diverged_count, not a status field
// If diverged_count > 0, there are drifts to show
export const RECONCILE_STATUS_DISPLAY: Record<string, { label: string; color: string }> = {
  has_drifts: { label: 'Has Drifts', color: 'text-yellow-400' },
  clean: { label: 'Clean', color: 'text-green-400' },
}