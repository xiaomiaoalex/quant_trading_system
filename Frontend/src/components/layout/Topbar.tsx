import { useLocation } from 'react-router-dom'
import { HealthBadge } from '@/components/ui'
import { useMonitorSnapshot } from '@/hooks'
import { clsx } from 'clsx'

const pageTitles: Record<string, string> = {
  '/monitor': 'System Monitor',
  '/strategies': 'Strategy Management',
  '/reconcile': 'Reconciliation',
  '/chat': 'AI Strategy Chat',
  '/backtests': 'Backtests',
  '/reports': 'Reports',
  '/audit': 'Audit Log',
  '/replay': 'Event Replay',
}

interface TopbarProps {
  sidebarCollapsed?: boolean
}

export function Topbar({ sidebarCollapsed = false }: TopbarProps) {
  const location = useLocation()
  const { healthState } = useMonitorSnapshot()
  const pageTitle = pageTitles[location.pathname] ?? 'Trading Console'

  return (
    <header
      className={clsx(
        'fixed top-0 right-0 h-16 bg-gray-800/80 backdrop-blur-sm border-b border-gray-700 z-20 transition-all duration-200',
        sidebarCollapsed ? 'left-16' : 'left-64'
      )}
    >
      <div className="flex items-center justify-between h-full px-6">
        <h1 className="text-lg font-semibold text-white">{pageTitle}</h1>
        <div className="flex items-center gap-4">
          <HealthBadge state={healthState} size="sm" />
        </div>
      </div>
    </header>
  )
}