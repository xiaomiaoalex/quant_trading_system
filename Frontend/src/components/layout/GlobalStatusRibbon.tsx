import { useMonitorSnapshot } from '@/hooks'
import { KillSwitchBadge } from '@/components/ui'

export function GlobalStatusRibbon() {
  const { snapshot, isStale, healthState } = useMonitorSnapshot()

  const killswitchLevel = snapshot?.killswitch_level ?? 0
  const showKillSwitch = killswitchLevel >= 1
  const showStale = isStale
  const showDegraded = healthState === 'degraded' && !isStale
  const showDown = healthState === 'down' && !isStale

  if (!showKillSwitch && !showStale && !showDegraded && !showDown) {
    return null
  }

  return (
    <div className="fixed top-16 right-0 left-0 z-10 flex items-center gap-3 px-4 py-1.5 text-xs font-medium border-b border-white/5 bg-gray-800/90 backdrop-blur-sm">
      {showDown && (
        <span className="inline-flex items-center gap-1.5 text-red-400">
          <span className="h-2 w-2 rounded-full bg-red-500 status-pulse" />
          Connection Down
        </span>
      )}
      {showStale && !showDown && (
        <span className="inline-flex items-center gap-1.5 text-gray-400">
          <span className="h-2 w-2 rounded-full bg-gray-500 status-pulse" />
          Data Stale
        </span>
      )}
      {showDegraded && !showDown && !showStale && (
        <span className="inline-flex items-center gap-1.5 text-yellow-400">
          <span className="h-2 w-2 rounded-full bg-yellow-500 status-pulse" />
          System Degraded
        </span>
      )}
      {showKillSwitch && (
        <div className="ml-auto">
          <KillSwitchBadge level={killswitchLevel} />
        </div>
      )}
    </div>
  )
}
