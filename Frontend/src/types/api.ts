// API Error structure from backend
export interface APIError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  request_id?: string;
}

// Standard API response wrapper
export interface APIResponse<T> {
  data: T;
  ok: boolean;
  message?: string;
}

// Action result from backend
export interface ActionResult {
  ok: boolean;
  message?: string;
}

// Health check response
export interface HealthResponse {
  status: 'ok';
  time: string; // ISO 8601
}

// Health dependency item
export interface HealthDependency {
  name: string;
  status: 'ok' | 'degraded' | 'down';
  latency_ms?: number;
  message?: string;
}

// KillSwitch levels
export const KILLSWITCH_LEVELS = {
  NORMAL: 0,
  NO_NEW_POSITIONS: 1,
  CLOSE_ONLY: 2,
  FULL_STOP: 3,
} as const;

export type KillSwitchLevel = (typeof KILLSWITCH_LEVELS)[keyof typeof KILLSWITCH_LEVELS];

export interface KillSwitchState {
  scope: string;
  level: KillSwitchLevel;
  reason?: string;
  updated_at?: string;
  updated_by?: string;
}

// KillSwitch display info
export const KILLSWITCH_DISPLAY: Record<KillSwitchLevel, { label: string; color: string; description: string }> = {
  [KILLSWITCH_LEVELS.NORMAL]: {
    label: 'Normal',
    color: 'bg-status-healthy',
    description: 'All operations normal',
  },
  [KILLSWITCH_LEVELS.NO_NEW_POSITIONS]: {
    label: 'No New Positions',
    color: 'bg-status-degraded',
    description: 'New position entry blocked',
  },
  [KILLSWITCH_LEVELS.CLOSE_ONLY]: {
    label: 'Close Only',
    color: 'bg-status-degraded',
    description: 'Only position close allowed',
  },
  [KILLSWITCH_LEVELS.FULL_STOP]: {
    label: 'Full Stop',
    color: 'bg-status-blocked',
    description: 'All trading halted',
  },
};

// Alert severity levels
export type AlertSeverity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export const ALERT_SEVERITY_ORDER: AlertSeverity[] = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];

export const ALERT_SEVERITY_DISPLAY: Record<AlertSeverity, { label: string; color: string; bgColor: string }> = {
  LOW: { label: 'Low', color: 'text-severity-low', bgColor: 'bg-severity-low/10' },
  MEDIUM: { label: 'Medium', color: 'text-severity-medium', bgColor: 'bg-severity-medium/10' },
  HIGH: { label: 'High', color: 'text-severity-high', bgColor: 'bg-severity-high/10' },
  CRITICAL: { label: 'Critical', color: 'text-severity-critical', bgColor: 'bg-severity-critical/10' },
};

// Adapter health status
export type AdapterHealthStatus = 'HEALTHY' | 'DEGRADED' | 'DOWN';

export const ADAPTER_HEALTH_DISPLAY: Record<AdapterHealthStatus, { label: string; color: string; dotColor: string }> = {
  HEALTHY: { label: 'Healthy', color: 'text-status-healthy', dotColor: 'bg-status-healthy' },
  DEGRADED: { label: 'Degraded', color: 'text-status-degraded', dotColor: 'bg-status-degraded' },
  DOWN: { label: 'Down', color: 'text-status-down', dotColor: 'bg-status-down' },
};
