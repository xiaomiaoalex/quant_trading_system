import { useState, useCallback } from 'react'
import { useMonitorSnapshot, useMonitorAlerts, useClearAllAlerts, useSSE } from '@/hooks'
import { useQueryClient } from '@tanstack/react-query'
import { LoadingState, ErrorState, EmptyState, ConfirmDialog, HealthBadge } from '@/components/ui'
import {
  MetricCard,
  AdapterHealthTable,
  AlertList,
  KillSwitchIndicator,
  StaleBanner,
} from '@/components/monitor'
import { monitorKeys } from '@/hooks/useMonitorSnapshot'

export function Monitor() {
  const queryClient = useQueryClient()

  // SSE for real-time updates - invalidates queries when server pushes updates
  useSSE(
    ['monitor'],
    () => {
      // Invalidate monitor queries when SSE update is received
      console.log('[Monitor] SSE update received, invalidating queries')
      queryClient.invalidateQueries({ queryKey: monitorKeys.snapshot() })
      queryClient.invalidateQueries({ queryKey: monitorKeys.alerts() })
    },
    { debug: true }
  )

  // Data hooks
  const { snapshot, isLoading, isError, error, isStale, healthState, refetch } =
    useMonitorSnapshot()
  const { alerts } = useMonitorAlerts()
  const { clearAllAlerts, isPending: isClearingAlerts } = useClearAllAlerts()

  // UI state
  const [showClearAllDialog, setShowClearAllDialog] = useState(false)
  const [clearSuccess, setClearSuccess] = useState<string | null>(null)

  // Handle clear all alerts
  const handleClearAllAlerts = useCallback(async () => {
    const success = await clearAllAlerts('Manually cleared by user')
    if (success) {
      setClearSuccess('All alerts cleared successfully')
      setTimeout(() => setClearSuccess(null), 3000)
    }
    setShowClearAllDialog(false)
  }, [clearAllAlerts])

  // Loading state
  if (isLoading) {
    return (
      <div className="p-6">
        <LoadingState message="Loading monitor snapshot..." />
      </div>
    )
  }

  // Error state
  if (isError) {
    return (
      <div className="p-6">
        <ErrorState title="Failed to load monitor data" message={error} onRetry={refetch} />
      </div>
    )
  }

  // No snapshot data
  if (!snapshot) {
    return (
      <div className="p-6">
        <EmptyState
          title="No Monitor Data"
          message="Unable to load system monitor snapshot. Please check system connectivity."
          action={{ label: 'Retry', onClick: refetch }}
        />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-900">
      {/* Header */}
      <div className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="flex items-center justify-between px-6 py-4">
          <div className="flex items-center gap-4">
            <h1 className="text-xl font-semibold text-white">System Monitor</h1>
            <HealthBadge state={healthState} />
          </div>
          <div className="flex items-center gap-3">
            {snapshot && (
              <p className="text-sm text-gray-500">
                Last update: {new Date(snapshot.timestamp).toLocaleTimeString()}
              </p>
            )}
            <button
              type="button"
              onClick={refetch}
              className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300
                transition-colors hover:bg-gray-700 focus:outline-none focus:ring-2
                focus:ring-gray-500"
            >
              Refresh
            </button>
          </div>
        </div>
      </div>

      <div className="p-6 space-y-6">
        {/* Stale Banner */}
        {isStale && <StaleBanner lastUpdate={snapshot.timestamp} onRefresh={refetch} />}

        {/* Clear Success Toast */}
        {clearSuccess && (
          <div className="rounded-lg border border-green-900/50 bg-green-950/20 px-4 py-2">
            <p className="text-sm text-green-400">{clearSuccess}</p>
          </div>
        )}

        {/* Degraded/Blocked Warning Banner */}
        {healthState === 'degraded' && (
          <div className="rounded-lg border border-yellow-900/50 bg-yellow-950/20 px-4 py-3">
            <p className="text-sm text-yellow-300">
              System is operating in degraded mode. Some adapters may be experiencing issues.
            </p>
          </div>
        )}
        {healthState === 'down' && (
          <div className="rounded-lg border border-red-900/50 bg-red-950/20 px-4 py-3">
            <p className="text-sm text-red-300">
              System is not fully operational. Trading may be restricted. Check adapter status
              below.
            </p>
          </div>
        )}

        {/* KillSwitch Status */}
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          <KillSwitchIndicator
            level={snapshot.killswitch_level}
            scope={snapshot.killswitch_scope}
          />
        </div>

        {/* Key Metrics Grid */}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <MetricCard
            title="Open Orders"
            value={snapshot.open_orders_count}
            subValue={`${snapshot.pending_orders_count} pending`}
          />
          <MetricCard
            title="Daily P&L"
            value={snapshot.daily_pnl}
            subValue={`${snapshot.daily_pnl_pct}%`}
          />
          <MetricCard
            title="Positions"
            value={snapshot.total_positions}
            subValue={`Exposure: ${snapshot.total_exposure}`}
          />
          <MetricCard
            title="Unrealized P&L"
            value={snapshot.unrealized_pnl}
            subValue={`Realized: ${snapshot.realized_pnl}`}
          />
        </div>

        {/* Alert Summary by Severity */}
        {Object.keys(snapshot.alert_count_by_severity).length > 0 && (
          <div className="rounded-lg border border-gray-700/50 bg-gray-800/50 p-4">
            <h3 className="text-sm font-medium text-gray-300 mb-3">Alert Summary</h3>
            <div className="flex flex-wrap gap-3">
              {Object.entries(snapshot.alert_count_by_severity)
                .filter(([, count]) => count > 0)
                .map(([severity, count]) => (
                  <div
                    key={severity}
                    className="flex items-center gap-2 rounded-full bg-gray-700/50 px-3 py-1"
                  >
                    <span className="text-xs font-medium text-gray-400 uppercase">{severity}</span>
                    <span className="rounded-full bg-gray-600 px-2 py-0.5 text-xs font-medium text-white">
                      {count}
                    </span>
                  </div>
                ))}
            </div>
          </div>
        )}

        {/* Two Column Layout: Adapters & Alerts */}
        <div className="grid gap-6 lg:grid-cols-2">
          <AdapterHealthTable adapters={snapshot.adapters} />
          <AlertList alerts={snapshot.active_alerts} onClearAlert={undefined} />
        </div>

        {/* Danger Zone - Clear All Alerts */}
        {alerts.length > 0 && (
          <div className="rounded-lg border border-red-900/30 bg-red-950/10 p-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-medium text-red-300">Clear All Alerts</h3>
                <p className="text-xs text-red-400/70 mt-1">
                  This will clear {alerts.length} active alert{alerts.length !== 1 ? 's' : ''}. This
                  action requires confirmation.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowClearAllDialog(true)}
                className="rounded-md bg-red-900/30 px-4 py-2 text-sm font-medium text-red-300
                  transition-colors hover:bg-red-900/50 focus:outline-none focus:ring-2
                  focus:ring-red-500"
              >
                Clear All Alerts
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Confirm Dialog */}
      <ConfirmDialog
        isOpen={showClearAllDialog}
        title="Clear All Alerts"
        message={`Are you sure you want to clear all ${alerts.length} active alerts?
          This action cannot be undone and will remove all current alert states from the system.`}
        confirmLabel="Clear All Alerts"
        cancelLabel="Cancel"
        variant="danger"
        isLoading={isClearingAlerts}
        onConfirm={handleClearAllAlerts}
        onCancel={() => setShowClearAllDialog(false)}
      />
    </div>
  )
}
