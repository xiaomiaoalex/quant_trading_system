import { useState, useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useReconcileReport, useDriftEvents, useTriggerReconciliation, useSSE } from '@/hooks'
import { LoadingState, ErrorState, EmptyState, ConfirmDialog } from '@/components/ui'
import { ReconcileSummaryCard, ReconcileDriftTable } from '@/components/reconcile'
import { formatAPIError, isAPIError } from '@/api/client'
import { reconcileKeys } from '@/hooks/useReconcile'

export function Reconcile() {
  const queryClient = useQueryClient()

  // SSE for real-time updates
  useSSE(
    ['reconciliation'],
    () => {
      console.log('[Reconcile] SSE update received, invalidating queries')
      queryClient.invalidateQueries({ queryKey: reconcileKeys.report() })
      queryClient.invalidateQueries({ queryKey: reconcileKeys.driftEvents() })
    },
    { debug: true }
  )

  const { data: report, isLoading, isError, error, refetch, isFetching } = useReconcileReport()
  const { data: driftEvents } = useDriftEvents()
  const { trigger, isPending: isTriggering } = useTriggerReconciliation()
  const [showTriggerConfirm, setShowTriggerConfirm] = useState(false)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  const handleTrigger = useCallback(async () => {
    const success = await trigger()
    if (success) {
      setSuccessMsg('Reconciliation triggered successfully')
      setTimeout(() => setSuccessMsg(null), 3000)
      refetch()
    }
    setShowTriggerConfirm(false)
  }, [trigger, refetch])

  const is404 = isAPIError(error) && error.code === 'HTTP_404'

  if (isLoading) return <div className="p-6"><LoadingState message="Loading reconciliation report..." /></div>
  if (isError && !is404) return <div className="p-6"><ErrorState title="Failed to load reconciliation data" message={formatAPIError(error)} onRetry={() => refetch()} /></div>
  if (!report) return <div className="p-6"><EmptyState title="No Reconciliation Data" message="Trigger a reconciliation to generate a report." action={{ label: 'Trigger', onClick: () => setShowTriggerConfirm(true) }} /></div>

  // driftEvents now returns Drift[] directly (transformed from EventEnvelope in hook)
  const drifts = driftEvents ?? report.drifts ?? []

  return (
    <div className="min-h-screen bg-gray-900">
      <div className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="flex items-center justify-between px-6 py-4">
          <div className="flex items-center gap-4">
            <h1 className="text-xl font-semibold text-white">Reconciliation</h1>
            {isFetching && <span className="text-xs text-gray-500">Refreshing...</span>}
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => setShowTriggerConfirm(true)}
              disabled={isTriggering}
              className="rounded-md bg-blue-900/30 px-4 py-2 text-sm font-medium text-blue-300 hover:bg-blue-900/50 disabled:opacity-50"
            >
              {isTriggering ? 'Triggering...' : 'Trigger Reconciliation'}
            </button>
            <button onClick={() => refetch()} className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700">Refresh</button>
          </div>
        </div>
      </div>

      <div className="p-6 space-y-6">
        {successMsg && (
          <div className="rounded-lg border border-green-900/50 bg-green-950/20 px-4 py-2">
            <p className="text-sm text-green-400">{successMsg}</p>
          </div>
        )}

        {(report.diverged_count > 0 || (report.drifts?.length ?? 0) > 0) && (
          <div className="rounded-lg border border-yellow-900/50 bg-yellow-950/20 px-4 py-3">
            <p className="text-sm text-yellow-300">
              {report.diverged_count} divergence{report.diverged_count !== 1 ? 's' : ''} detected between local and exchange orders.
            </p>
          </div>
        )}

        <ReconcileSummaryCard report={report} />
        <ReconcileDriftTable drifts={drifts} />
      </div>

      <ConfirmDialog
        isOpen={showTriggerConfirm}
        title="Trigger Reconciliation"
        message="This will trigger a reconciliation between local orders and exchange orders. Continue?"
        confirmLabel="Trigger"
        cancelLabel="Cancel"
        variant="danger"
        isLoading={isTriggering}
        onConfirm={handleTrigger}
        onCancel={() => setShowTriggerConfirm(false)}
      />
    </div>
  )
}