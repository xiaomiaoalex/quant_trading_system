import { useLocation } from 'react-router-dom'
import { HealthBadge, KillSwitchBadge } from '@/components/ui'
import { useMonitorSnapshot } from '@/hooks'
import { clsx } from 'clsx'

const pageTitles: Record<string, string> = {
  '/monitor': 'System Monitor',
  '/data': 'Data Management',
  '/research': 'Research',
  '/strategies': 'Strategy Management',
  '/reconcile': 'Reconciliation',
  '/chat': 'AI Strategy Chat',
  '/backtests': 'Backtests',
  '/portfolio-allocation': 'Portfolio Allocation',
  '/portfolio-autopilot': 'Portfolio Autopilot',
  '/crypto-risk': 'Crypto Risk',
  '/reports': 'Reports',
  '/audit': 'Audit Log',
  '/replay': 'Event Replay',
}

interface TopbarProps {
  sidebarCollapsed?: boolean
  onToggleSidebar?: () => void
}

function formatLastUpdate(ts: string | undefined): string {
  if (!ts) return '—'
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ts
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffSec = Math.floor(diffMs / 1000)
  if (diffSec < 5) return 'just now'
  if (diffSec < 60) return `${diffSec}s ago`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  return d.toLocaleTimeString()
}

export function Topbar({ sidebarCollapsed = false, onToggleSidebar }: TopbarProps) {
  const location = useLocation()
  const { snapshot, healthState, isStale } = useMonitorSnapshot()
  const pageTitle = pageTitles[location.pathname] ?? 'Trading Console'

  const killswitchLevel = snapshot?.killswitch_level ?? 0
  const lastUpdate = formatLastUpdate(snapshot?.timestamp)
  const source = snapshot?.snapshot_source ?? 'unknown'

  return (
    <header
      className={clsx(
        'fixed top-0 right-0 h-16 bg-gray-800/80 backdrop-blur-sm border-b border-gray-700 z-30 transition-all duration-200',
        sidebarCollapsed ? 'left-16' : 'left-64'
      )}
    >
      <div className="flex items-center justify-between h-full px-4">
        <div className="flex items-center gap-3">
          {onToggleSidebar && (
            <button
              type="button"
              onClick={onToggleSidebar}
              aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              className="rounded-md p-1.5 text-gray-400 hover:text-white hover:bg-gray-700/50 transition-colors focus-visible:ring-2 focus-visible:ring-blue-500"
            >
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                {sidebarCollapsed ? (
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" />
                ) : (
                  <path strokeLinecap="round" strokeLinejoin="round" d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
                )}
              </svg>
            </button>
          )}
          <h1 className="text-lg font-semibold text-white tracking-tight">{pageTitle}</h1>
        </div>

        <div className="flex items-center gap-4">
          {/* Env indicator */}
          <span className="hidden sm:inline-flex items-center rounded-full bg-surface-3 px-2 py-0.5 text-xs font-medium text-accent-4 border border-accent-1/40">
            {source.toUpperCase()}
          </span>

          {/* Connection status */}
          <span
            className="hidden md:inline-flex items-center gap-1.5 text-xs"
            title={isStale ? 'Snapshot stale' : healthState}
          >
            <span className={clsx(
              'h-2 w-2 rounded-full',
              healthState === 'healthy' && !isStale && 'bg-status-healthy',
              healthState === 'degraded' && !isStale && 'bg-status-degraded',
              healthState === 'down' && 'bg-status-down',
              isStale && 'bg-status-stale status-pulse'
            )} />
            <span className={clsx(
              'font-medium',
              healthState === 'healthy' && !isStale && 'text-status-healthy',
              healthState === 'degraded' && !isStale && 'text-status-degraded',
              healthState === 'down' && 'text-status-down',
              isStale && 'text-status-stale'
            )}>
              {isStale ? 'Stale' : healthState.charAt(0).toUpperCase() + healthState.slice(1)}
            </span>
          </span>

          {/* Last update */}
          <span
            className="hidden lg:inline-flex text-xs text-accent-3 tabular-nums"
            title={snapshot?.timestamp ?? 'No snapshot'}
          >
            {lastUpdate}
          </span>

          {/* KillSwitch mini badge */}
          {killswitchLevel >= 1 && (
            <KillSwitchBadge level={killswitchLevel} />
          )}

          <HealthBadge state={healthState} size="sm" />
        </div>
      </div>
    </header>
  )
}
