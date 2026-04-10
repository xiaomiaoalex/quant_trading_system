import { clsx } from 'clsx'
import type {
  SystemHealthState,
  AdapterHealthStatus,
  AlertSeverity,
  KillSwitchLevel,
} from '@/types'
import { KILLSWITCH_DISPLAY, ADAPTER_HEALTH_DISPLAY, ALERT_SEVERITY_DISPLAY } from '@/types'

// Health state badge
interface HealthBadgeProps {
  state: SystemHealthState
  showLabel?: boolean
  size?: 'sm' | 'md'
}

const HEALTH_CONFIG: Record<
  SystemHealthState,
  { label: string; dotColor: string; textColor: string; bgColor: string }
> = {
  healthy: {
    label: 'Healthy',
    dotColor: 'bg-status-healthy',
    textColor: 'text-status-healthy',
    bgColor: 'bg-status-healthy/10',
  },
  degraded: {
    label: 'Degraded',
    dotColor: 'bg-status-degraded',
    textColor: 'text-status-degraded',
    bgColor: 'bg-status-degraded/10',
  },
  stale: {
    label: 'Stale',
    dotColor: 'bg-status-stale',
    textColor: 'text-status-stale',
    bgColor: 'bg-status-stale/10',
  },
  down: {
    label: 'Down',
    dotColor: 'bg-status-down',
    textColor: 'text-status-down',
    bgColor: 'bg-status-down/10',
  },
}

export function HealthBadge({ state, showLabel = true, size = 'md' }: HealthBadgeProps) {
  const config = HEALTH_CONFIG[state]

  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-full font-medium',
        config.bgColor,
        config.textColor,
        size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-sm'
      )}
    >
      <span
        className={clsx('rounded-full', config.dotColor, size === 'sm' ? 'h-1.5 w-1.5' : 'h-2 w-2')}
      />
      {showLabel && config.label}
    </span>
  )
}

// Adapter health status badge
interface AdapterStatusBadgeProps {
  status: AdapterHealthStatus
}

export function AdapterStatusBadge({ status }: AdapterStatusBadgeProps) {
  const config = ADAPTER_HEALTH_DISPLAY[status]

  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium',
        config.color,
        'bg-current/10'
      )}
    >
      <span className={clsx('rounded-full h-1.5 w-1.5', config.dotColor)} />
      {config.label}
    </span>
  )
}

// Alert severity badge
interface SeverityBadgeProps {
  severity: AlertSeverity
}

export function SeverityBadge({ severity }: SeverityBadgeProps) {
  const config = ALERT_SEVERITY_DISPLAY[severity]

  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
        config.color,
        config.bgColor
      )}
    >
      {config.label}
    </span>
  )
}

// KillSwitch level badge
interface KillSwitchBadgeProps {
  level: KillSwitchLevel
}

export function KillSwitchBadge({ level }: KillSwitchBadgeProps) {
  const config = KILLSWITCH_DISPLAY[level]

  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium text-white',
        config.color
      )}
    >
      {config.label}
    </span>
  )
}
