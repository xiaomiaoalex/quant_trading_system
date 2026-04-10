import { clsx } from 'clsx'
import type { KillSwitchLevel } from '@/types'
import { KILLSWITCH_DISPLAY, KILLSWITCH_LEVELS } from '@/types'
import { KillSwitchBadge } from '@/components/ui'

interface KillSwitchIndicatorProps {
  level: KillSwitchLevel
  scope?: string
  reason?: string
  updatedAt?: string
  updatedBy?: string
  isLoading?: boolean
}

export function KillSwitchIndicator({
  level,
  scope = 'GLOBAL',
  reason,
  updatedAt,
  updatedBy,
  isLoading,
}: KillSwitchIndicatorProps) {
  const config = KILLSWITCH_DISPLAY[level]
  const isActive = level > KILLSWITCH_LEVELS.NORMAL

  if (isLoading) {
    return (
      <div className="rounded-lg border border-gray-700/50 bg-gray-800/50 p-4">
        <div className="h-6 w-32 animate-pulse rounded bg-gray-700" />
      </div>
    )
  }

  return (
    <div
      className={clsx(
        'rounded-lg border p-4',
        isActive ? 'border-red-900/50 bg-red-950/20' : 'border-gray-700/50 bg-gray-800/50'
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          {isActive ? (
            <svg
              className="h-5 w-5 text-red-500"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              aria-hidden="true"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" // prettier-ignore
              />
            </svg>
          ) : (
            <svg
              className="h-5 w-5 text-green-500"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              aria-hidden="true"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" // prettier-ignore
              />
            </svg>
          )}
          <div>
            <p className="text-sm font-medium text-gray-300">KillSwitch</p>
            <p className="text-xs text-gray-500">Scope: {scope}</p>
          </div>
        </div>
        <KillSwitchBadge level={level} />
      </div>

      {isActive && (
        <div className="mt-3 pt-3 border-t border-red-900/30">
          <p className="text-xs text-red-300">{config.description}</p>
          {reason && <p className="mt-1 text-xs text-red-400/70">Reason: {reason}</p>}
        </div>
      )}

      {updatedAt && (
        <p className="mt-2 text-xs text-gray-500">
          Last updated: {new Date(updatedAt).toLocaleString()}
          {updatedBy && ` by ${updatedBy}`}
        </p>
      )}
    </div>
  )
}
