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
  size?: 'sm' | 'md' | 'lg'
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

const healthSizeClasses = {
  sm: 'px-2 py-0.5 text-xs gap-1',
  md: 'px-2.5 py-1 text-sm gap-1.5',
  lg: 'px-3 py-1.5 text-base gap-2',
}

const healthDotClasses = {
  sm: 'h-1.5 w-1.5',
  md: 'h-2 w-2',
  lg: 'h-2.5 w-2.5',
}

export function HealthBadge({ state, showLabel = true, size = 'md' }: HealthBadgeProps) {
  const config = HEALTH_CONFIG[state]

  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-full font-medium border border-white/5',
        config.bgColor,
        config.textColor,
        healthSizeClasses[size]
      )}
    >
      <span className={clsx('rounded-full', config.dotColor, healthDotClasses[size])} />
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
        'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium border border-white/5',
        config.color,
        'bg-gray-900/40'
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
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium border border-white/5',
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
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium text-white border border-white/10 shadow-sm',
        config.color
      )}
    >
      {config.label}
    </span>
  )
}
