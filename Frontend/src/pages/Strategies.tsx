import { useState, useCallback } from 'react'
import { clsx } from 'clsx'
import { useQueryClient } from '@tanstack/react-query'
import { useStrategyRegistry, useLoadedStrategies, useLoadStrategy, useUnloadStrategy, useStartStrategy, useStopStrategy, usePauseStrategy, useResumeStrategy, useSSE } from '@/hooks'
import type { RegisteredStrategy, StrategyRuntimeInfo } from '@/types'
import { STRATEGY_STATUS_DISPLAY } from '@/types'
import { LoadingState, ErrorState, EmptyState, ConfirmDialog } from '@/components/ui'
import { MetricCard } from '@/components/monitor'
import { StrategyDetailModal } from '@/components/strategies'
import { formatAPIError } from '@/api/client'
import { strategyKeys } from '@/hooks/useStrategies'

// Normalize backend status to lowercase
function normalizeStatus(status: string): StrategyRuntimeInfo['status'] {
  const lower = status.toLowerCase()
  if (lower === 'loaded') return 'loaded'
  if (lower === 'running') return 'running'
  if (lower === 'paused') return 'paused'
  if (lower === 'stopped') return 'stopped'
  if (lower === 'error') return 'error'
  return 'stopped'
}

export function Strategies() {
  const queryClient = useQueryClient()

  // SSE for real-time updates
  useSSE(
    ['strategies', 'orders'],
    () => {
      // Invalidate strategy queries when SSE update is received
      queryClient.invalidateQueries({ queryKey: strategyKeys.all })
    },
    { debug: false }
  )

  const { data: registeredStrategies, isLoading, isError, error, refetch } = useStrategyRegistry()
  const { data: loadedStrategies, refetch: refetchLoaded } = useLoadedStrategies()
  const [showConfirm, setShowConfirm] = useState<string | null>(null)
  const [actionType, setActionType] = useState<string | null>(null)
  const [selectedStrategy, setSelectedStrategy] = useState<RegisteredStrategy | null>(null)
  const [detailStrategy, setDetailStrategy] = useState<RegisteredStrategy | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  // Build runtime status map (normalize status to lowercase)
  const runtimeMap = new Map<string, StrategyRuntimeInfo>()
  loadedStrategies?.forEach(info => {
    const normalized: StrategyRuntimeInfo = {
      ...info,
      status: normalizeStatus(info.status),
    }
    runtimeMap.set(info.strategy_id, normalized)
  })

  // Get status for a strategy - returns null if not loaded
  const getStatus = (strategyId: string) => runtimeMap.get(strategyId)?.status ?? null
  const getBlockedReason = (strategyId: string) => runtimeMap.get(strategyId)?.blocked_reason
  const getRuntimeInfo = (strategyId: string) => runtimeMap.get(strategyId)

  // Mutation hooks
  const loadMutation = useLoadStrategy(selectedStrategy?.strategy_id ?? '', selectedStrategy?.entrypoint ?? 'strategies.default')
  const unloadMutation = useUnloadStrategy(selectedStrategy?.strategy_id ?? '')
  const startMutation = useStartStrategy(selectedStrategy?.strategy_id ?? '')
  const stopMutation = useStopStrategy(selectedStrategy?.strategy_id ?? '')
  const pauseMutation = usePauseStrategy(selectedStrategy?.strategy_id ?? '')
  const resumeMutation = useResumeStrategy(selectedStrategy?.strategy_id ?? '')

  const handleAction = useCallback((strategy: RegisteredStrategy, action: string) => {
    setSelectedStrategy(strategy)
    setShowConfirm(strategy.strategy_id)
    setActionType(action)
  }, [])

  const confirmAction = useCallback(async () => {
    if (!selectedStrategy || !actionType) return
    setErrorMsg(null)
    let success = false
    let mutationError: string | null = null
    try {
      switch (actionType) {
        case 'load': success = await loadMutation.mutateAsync(); break
        case 'unload': success = await unloadMutation.mutateAsync(); break
        case 'start': success = await startMutation.mutateAsync(); break
        case 'stop': success = await stopMutation.mutateAsync(); break
        case 'pause': success = await pauseMutation.mutateAsync(); break
        case 'resume': success = await resumeMutation.mutateAsync(); break
      }
    } catch (e) {
      mutationError = formatAPIError(e)
    }
    // Always refetch after mutation (success or failure)
    refetch()
    refetchLoaded()
    if (success) {
      setSuccessMsg(`${actionType} successful`)
      setTimeout(() => setSuccessMsg(null), 3000)
    } else if (mutationError || loadMutation.error || unloadMutation.error || startMutation.error || stopMutation.error || pauseMutation.error || resumeMutation.error) {
      setErrorMsg(mutationError ?? loadMutation.error ?? unloadMutation.error ?? startMutation.error ?? stopMutation.error ?? pauseMutation.error ?? resumeMutation.error)
      setTimeout(() => setErrorMsg(null), 5000)
    }
    setShowConfirm(null)
    setActionType(null)
    setSelectedStrategy(null)
  }, [selectedStrategy, actionType, loadMutation, unloadMutation, startMutation, stopMutation, pauseMutation, resumeMutation, refetch, refetchLoaded])

  const getActionLabel = (action: string) => {
    const labels: Record<string, string> = {
      load: 'Load Strategy', unload: 'Unload Strategy',
      start: 'Start Strategy', stop: 'Stop Strategy',
      pause: 'Pause Strategy', resume: 'Resume Strategy'
    }
    return labels[action] ?? action
  }

  if (isLoading) return <div className="p-6"><LoadingState message="Loading strategies..." /></div>
  if (isError) return <div className="p-6"><ErrorState title="Failed to load strategies" message={formatAPIError(error)} onRetry={() => refetch()} /></div>
  if (!registeredStrategies || registeredStrategies.length === 0) {
    return <div className="p-6"><EmptyState title="No Strategies" message="No strategies registered in the system." action={{ label: 'Retry', onClick: () => refetch() }} /></div>
  }

  // Calculate summary from loaded strategies
  const loadedCount = loadedStrategies?.length ?? 0
  const runningCount = loadedStrategies?.filter(s => normalizeStatus(s.status) === 'running').length ?? 0
  const pausedCount = loadedStrategies?.filter(s => normalizeStatus(s.status) === 'paused').length ?? 0

  return (
    <div className="min-h-screen bg-gray-900">
      <div className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="flex items-center justify-between px-6 py-4">
          <h1 className="text-xl font-semibold text-white">Strategy Management</h1>
          <button onClick={() => refetch()} className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700">Refresh</button>
        </div>
      </div>

      <div className="p-6 space-y-6">
        {successMsg && <div className="rounded-lg border border-green-900/50 bg-green-950/20 px-4 py-2"><p className="text-sm text-green-400">{successMsg}</p></div>}
        {(errorMsg || loadMutation.error || unloadMutation.error || startMutation.error || stopMutation.error || pauseMutation.error || resumeMutation.error) && (
          <div className="rounded-lg border border-red-900/50 bg-red-950/20 px-4 py-2">
            <p className="text-sm text-red-400">
              {errorMsg ?? loadMutation.error ?? unloadMutation.error ?? startMutation.error ?? stopMutation.error ?? pauseMutation.error ?? resumeMutation.error}
            </p>
          </div>
        )}

        {/* Summary Cards */}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <MetricCard title="Total Registered" value={registeredStrategies.length} />
          <MetricCard title="Loaded" value={loadedCount} subValue={runningCount > 0 ? `${runningCount} running` : undefined} />
          <MetricCard title="Paused" value={pausedCount} />
          <MetricCard title="Not Loaded" value={registeredStrategies.length - loadedCount} />
        </div>

        {/* Strategy Table */}
        <div className="rounded-lg border border-gray-700 bg-gray-800/50 overflow-hidden">
          <table className="min-w-full divide-y divide-gray-700">
            <thead className="bg-gray-800">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Name</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">ID</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Status</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Ticks</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Signals</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Errors</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Blocked</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {registeredStrategies.map(strategy => {
                const runtime = getRuntimeInfo(strategy.strategy_id)
                const status = getStatus(strategy.strategy_id)
                const statusConfig = status ? (STRATEGY_STATUS_DISPLAY[status] ?? STRATEGY_STATUS_DISPLAY.stopped) : STRATEGY_STATUS_DISPLAY.stopped
                const blockedReason = getBlockedReason(strategy.strategy_id)
                return (
                  <tr key={strategy.strategy_id} className="hover:bg-gray-700/30">
                    <td className="px-4 py-3">
                      <button
                        onClick={() => setDetailStrategy(strategy)}
                        className="text-sm text-white font-medium hover:text-blue-400 hover:underline"
                      >
                        {strategy.name}
                      </button>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-400 font-mono">{strategy.strategy_id}</td>
                    <td className="px-4 py-3">
                      <span className={clsx('inline-flex rounded-full px-2 py-0.5 text-xs font-medium', statusConfig.color, statusConfig.bgColor)}>
                        {statusConfig.label}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-300">{runtime?.tick_count ?? '-'}</td>
                    <td className="px-4 py-3 text-sm text-gray-300">{runtime?.signal_count ?? '-'}</td>
                    <td className="px-4 py-3">
                      {runtime?.error_count && runtime.error_count > 0 ? (
                        <span className="text-sm text-red-400">{runtime.error_count}</span>
                      ) : (
                        <span className="text-sm text-gray-500">0</span>
                      )}
                    </td>
                      <td className="px-4 py-3 text-sm text-red-400 max-w-[150px] truncate">{blockedReason ?? '-'}</td>
                      <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        {status === null && (
                          <button onClick={() => handleAction(strategy, 'load')} className="rounded bg-blue-900/30 px-2 py-1 text-xs text-blue-300 hover:bg-blue-900/50">Load</button>
                        )}
                        {(status === 'loaded' || status === 'stopped') && (
                          <>
                            <button onClick={() => handleAction(strategy, 'start')} className="rounded bg-green-900/30 px-2 py-1 text-xs text-green-300 hover:bg-green-900/50">Start</button>
                            <button onClick={() => handleAction(strategy, 'unload')} className="rounded bg-red-900/30 px-2 py-1 text-xs text-red-300 hover:bg-red-900/50">Unload</button>
                          </>
                        )}
                        {status === 'running' && (
                          <>
                            <button onClick={() => handleAction(strategy, 'pause')} className="rounded bg-yellow-900/30 px-2 py-1 text-xs text-yellow-300 hover:bg-yellow-900/50">Pause</button>
                            <button onClick={() => handleAction(strategy, 'stop')} className="rounded bg-red-900/30 px-2 py-1 text-xs text-red-300 hover:bg-red-900/50">Stop</button>
                          </>
                        )}
                        {status === 'paused' && (
                          <>
                            <button onClick={() => handleAction(strategy, 'resume')} className="rounded bg-green-900/30 px-2 py-1 text-xs text-green-300 hover:bg-green-900/50">Resume</button>
                            <button onClick={() => handleAction(strategy, 'stop')} className="rounded bg-red-900/30 px-2 py-1 text-xs text-red-300 hover:bg-red-900/50">Stop</button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      <ConfirmDialog
        isOpen={!!showConfirm}
        title={actionType ? getActionLabel(actionType) : ''}
        message={`Are you sure you want to ${actionType} this strategy? This action may affect active trading.`}
        confirmLabel={actionType ? getActionLabel(actionType) : 'Confirm'}
        cancelLabel="Cancel"
        variant="danger"
        isLoading={loadMutation.isPending || unloadMutation.isPending || startMutation.isPending || stopMutation.isPending || pauseMutation.isPending || resumeMutation.isPending}
        onConfirm={confirmAction}
        onCancel={() => { setShowConfirm(null); setActionType(null); setSelectedStrategy(null) }}
      />

      <StrategyDetailModal
        strategy={detailStrategy!}
        runtime={detailStrategy ? (getRuntimeInfo(detailStrategy.strategy_id) ?? null) : null}
        isOpen={!!detailStrategy}
        onClose={() => setDetailStrategy(null)}
      />
    </div>
  )
}