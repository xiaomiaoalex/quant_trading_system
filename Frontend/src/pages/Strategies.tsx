// Target path: Frontend/src/pages/Strategies.tsx

import { useMemo, useState } from 'react'
import { clsx } from 'clsx'
import { useQueryClient } from '@tanstack/react-query'
import {
  useLoadStrategy,
  useLoadedStrategies,
  usePauseStrategy,
  useResumeStrategy,
  useSSE,
  useStartStrategy,
  useStopStrategy,
  useStrategyRegistry,
  useTradingPairs,
  useUnloadStrategy,
} from '@/hooks'
import type {
  DeploymentMode,
  LoadStrategyPayload,
  RegisteredStrategy,
  StrategyRuntimeInfo,
} from '@/types'
import {
  STRATEGY_STATUS_DISPLAY,
  buildDeploymentId,
  deriveStrategySummary,
  groupRuntimeByStrategy,
} from '@/types'
import { LoadingState, ErrorState, EmptyState } from '@/components/ui'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { MetricCard } from '@/components/monitor'
import { StrategyDetailModal } from '@/components/strategies'
import { formatAPIError } from '@/api/client'
import { strategyKeys } from '@/hooks/useStrategies'

function normalizeStatus(status: string): StrategyRuntimeInfo['status'] {
  const lower = status.toLowerCase()
  if (lower === 'loaded') return 'loaded'
  if (lower === 'running') return 'running'
  if (lower === 'paused') return 'paused'
  if (lower === 'stopped') return 'stopped'
  if (lower === 'error') return 'error'
  return 'stopped'
}

function RuntimeActions(props: {
  runtime: StrategyRuntimeInfo
  onMessage: (message: string) => void
  onError: (message: string) => void
}) {
  const { runtime, onMessage, onError } = props
  const startMutation = useStartStrategy(runtime.deployment_id)
  const stopMutation = useStopStrategy(runtime.deployment_id)
  const pauseMutation = usePauseStrategy(runtime.deployment_id)
  const resumeMutation = useResumeStrategy(runtime.deployment_id)
  const unloadMutation = useUnloadStrategy(runtime.deployment_id)

  const handle = async (action: 'start' | 'stop' | 'pause' | 'resume' | 'unload') => {
    let ok = false
    if (action === 'start') ok = await startMutation.mutateAsync()
    if (action === 'stop') ok = await stopMutation.mutateAsync()
    if (action === 'pause') ok = await pauseMutation.mutateAsync()
    if (action === 'resume') ok = await resumeMutation.mutateAsync()
    if (action === 'unload') ok = await unloadMutation.mutateAsync()

    if (ok) {
      onMessage(`${action} succeeded: ${runtime.deployment_id}`)
      return
    }

    const error =
      startMutation.error ??
      stopMutation.error ??
      pauseMutation.error ??
      resumeMutation.error ??
      unloadMutation.error ??
      'Unknown error'

    onError(typeof error === 'string' ? error : String(error))
  }

  const isPending =
    startMutation.isPending ||
    stopMutation.isPending ||
    pauseMutation.isPending ||
    resumeMutation.isPending ||
    unloadMutation.isPending

  return (
    <div className="flex flex-wrap items-center gap-2">
      {(runtime.status === 'loaded' || runtime.status === 'stopped') && (
        <button
          disabled={isPending}
          onClick={() => handle('start')}
          className="rounded bg-green-900/30 px-2 py-1 text-xs text-green-300 hover:bg-green-900/50 disabled:opacity-50"
        >
          Start
        </button>
      )}
      {runtime.status === 'running' && (
        <>
          <button
            disabled={isPending}
            onClick={() => handle('pause')}
            className="rounded bg-yellow-900/30 px-2 py-1 text-xs text-yellow-300 hover:bg-yellow-900/50 disabled:opacity-50"
          >
            Pause
          </button>
          <button
            disabled={isPending}
            onClick={() => handle('stop')}
            className="rounded bg-red-900/30 px-2 py-1 text-xs text-red-300 hover:bg-red-900/50 disabled:opacity-50"
          >
            Stop
          </button>
        </>
      )}
      {runtime.status === 'paused' && (
        <>
          <button
            disabled={isPending}
            onClick={() => handle('resume')}
            className="rounded bg-green-900/30 px-2 py-1 text-xs text-green-300 hover:bg-green-900/50 disabled:opacity-50"
          >
            Resume
          </button>
          <button
            disabled={isPending}
            onClick={() => handle('stop')}
            className="rounded bg-red-900/30 px-2 py-1 text-xs text-red-300 hover:bg-red-900/50 disabled:opacity-50"
          >
            Stop
          </button>
        </>
      )}
      {(runtime.status === 'loaded' || runtime.status === 'stopped' || runtime.status === 'error') && (
        <button
          disabled={isPending}
          onClick={() => handle('unload')}
          className="rounded bg-gray-700 px-2 py-1 text-xs text-gray-200 hover:bg-gray-600 disabled:opacity-50"
        >
          Unload
        </button>
      )}
    </div>
  )
}

export function Strategies() {
  const queryClient = useQueryClient()

  useSSE(
    ['strategies', 'orders'],
    () => {
      queryClient.invalidateQueries({
        queryKey: strategyKeys.loaded(),
        exact: true,
        refetchType: 'active',
      })
      queryClient.invalidateQueries({
        queryKey: strategyKeys.registry(),
        exact: true,
        refetchType: 'active',
      })
    },
    { debug: true },
  )

  const {
    data: registeredStrategies,
    isLoading,
    isError,
    error,
    refetch,
  } = useStrategyRegistry()
  const { data: loadedStrategies = [], refetch: refetchLoaded } = useLoadedStrategies()
  const { data: tradingPairsData, isLoading: isLoadingPairs } = useTradingPairs()
  const loadMutation = useLoadStrategy()

  const [detailStrategy, setDetailStrategy] = useState<RegisteredStrategy | null>(null)
  const [showDeployDialog, setShowDeployDialog] = useState(false)
  const [deployTarget, setDeployTarget] = useState<RegisteredStrategy | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [draftSymbol, setDraftSymbol] = useState('BTCUSDT')
  const [draftAccountId, setDraftAccountId] = useState('binance_demo')
  const [draftMode, setDraftMode] = useState<DeploymentMode>('demo')
  const [draftDeploymentId, setDraftDeploymentId] = useState('')

  const normalizedLoaded = useMemo(
    () => loadedStrategies.map((info) => ({ ...info, status: normalizeStatus(info.status) })),
    [loadedStrategies],
  )
  const deploymentsByStrategy = useMemo(
    () => groupRuntimeByStrategy(normalizedLoaded),
    [normalizedLoaded],
  )
  const summary = useMemo(
    () => deriveStrategySummary(normalizedLoaded, registeredStrategies?.length ?? 0),
    [normalizedLoaded, registeredStrategies],
  )

  const openDeployDialog = (strategy: RegisteredStrategy) => {
    const defaultSymbol = tradingPairsData?.pairs[0]?.symbol ?? 'BTCUSDT'
    const nextDeploymentId = buildDeploymentId(
      strategy.strategy_id,
      defaultSymbol,
      draftMode,
      draftAccountId,
    )
    setDeployTarget(strategy)
    setDraftSymbol(defaultSymbol)
    setDraftDeploymentId(nextDeploymentId)
    setShowDeployDialog(true)
  }

  const closeDeployDialog = () => {
    setShowDeployDialog(false)
    setDeployTarget(null)
  }

  const handleDraftSymbolChange = (nextSymbol: string) => {
    const normalized = nextSymbol.toUpperCase()
    setDraftSymbol(normalized)
    if (deployTarget) {
      setDraftDeploymentId(buildDeploymentId(deployTarget.strategy_id, normalized, draftMode, draftAccountId))
    }
  }

  const handleDraftModeChange = (nextMode: DeploymentMode) => {
    setDraftMode(nextMode)
    if (deployTarget) {
      setDraftDeploymentId(buildDeploymentId(deployTarget.strategy_id, draftSymbol, nextMode, draftAccountId))
    }
  }

  const handleDraftAccountChange = (nextAccountId: string) => {
    setDraftAccountId(nextAccountId)
    if (deployTarget) {
      setDraftDeploymentId(buildDeploymentId(deployTarget.strategy_id, draftSymbol, draftMode, nextAccountId))
    }
  }

  const handleCreateDeployment = async () => {
    if (!deployTarget) return

    const payload: LoadStrategyPayload = {
      deployment_id: draftDeploymentId.trim() || undefined,
      module_path: deployTarget.entrypoint,
      version: deployTarget.version ?? 'v1',
      config: {},
      symbols: [draftSymbol],
      account_id: draftAccountId,
      venue: 'BINANCE',
      mode: draftMode,
    }

    const ok = await loadMutation.mutateAsync({
      strategyId: deployTarget.strategy_id,
      payload,
    })

    await refetch()
    await refetchLoaded()

    if (ok) {
      setSuccessMsg(`Deployment loaded: ${draftDeploymentId}`)
      closeDeployDialog()
      return
    }

    setErrorMsg(loadMutation.error ?? 'Failed to create deployment')
  }

  const handleMutationMessage = (message: string) => {
    setSuccessMsg(message)
    setErrorMsg(null)
    window.setTimeout(() => setSuccessMsg(null), 3000)
  }

  const handleMutationError = (message: string) => {
    setErrorMsg(message)
    setSuccessMsg(null)
    window.setTimeout(() => setErrorMsg(null), 5000)
  }

  if (isLoading) {
    return (
      <div className="p-6">
        <LoadingState message="Loading strategies..." />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="p-6">
        <ErrorState
          title="Failed to load strategies"
          message={formatAPIError(error)}
          onRetry={() => refetch()}
        />
      </div>
    )
  }

  if (!registeredStrategies || registeredStrategies.length === 0) {
    return (
      <div className="p-6">
        <EmptyState
          title="No Strategies"
          message="No strategies registered in the system."
          action={{ label: 'Retry', onClick: () => refetch() }}
        />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-900">
      <div className="sticky top-0 z-10 border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm">
        <div className="flex items-center justify-between px-6 py-4">
          <h1 className="text-xl font-semibold text-white">Strategy Management</h1>
          <button
            onClick={() => refetch()}
            className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700"
          >
            Refresh
          </button>
        </div>
      </div>

      <div className="space-y-6 p-6">
        {successMsg && (
          <div className="rounded-lg border border-green-900/50 bg-green-950/20 px-4 py-2">
            <p className="text-sm text-green-400">{successMsg}</p>
          </div>
        )}
        {errorMsg && (
          <div className="rounded-lg border border-red-900/50 bg-red-950/20 px-4 py-2">
            <p className="text-sm text-red-400">{errorMsg}</p>
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <MetricCard title="Templates" value={summary.totalTemplates} />
          <MetricCard title="Deployments" value={summary.totalDeployments} />
          <MetricCard title="Running" value={summary.running} />
          <MetricCard title="Paused" value={summary.paused} />
          <MetricCard title="Stopped/Error" value={summary.stopped + summary.error} />
        </div>

        <div className="rounded-lg border border-gray-700 bg-gray-800/50 overflow-hidden">
          <table className="min-w-full divide-y divide-gray-700">
            <thead className="bg-gray-800">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">Name</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">Template ID</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">Description</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">Deployments</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {registeredStrategies.map((strategy) => {
                const runtimes = deploymentsByStrategy.get(strategy.strategy_id) ?? []
                return (
                  <tr key={strategy.strategy_id} className="align-top hover:bg-gray-700/20">
                    <td className="px-4 py-3">
                      <button
                        onClick={() => setDetailStrategy(strategy)}
                        className="text-sm font-medium text-white hover:text-blue-400 hover:underline"
                      >
                        {strategy.name}
                      </button>
                    </td>
                    <td className="px-4 py-3 text-sm font-mono text-gray-400">{strategy.strategy_id}</td>
                    <td className="max-w-xs px-4 py-3 text-sm text-gray-400">{strategy.description ?? '—'}</td>
                    <td className="px-4 py-3">
                      {runtimes.length === 0 ? (
                        <span className="text-sm text-gray-500">No deployments</span>
                      ) : (
                        <div className="space-y-3">
                          {runtimes.map((runtime) => {
                            const statusConfig = STRATEGY_STATUS_DISPLAY[runtime.status]
                            return (
                              <div key={runtime.deployment_id} className="rounded-lg border border-gray-700 bg-gray-900/60 p-3">
                                <div className="flex flex-wrap items-start justify-between gap-3">
                                  <div>
                                    <div className="text-xs font-mono text-gray-300">{runtime.deployment_id}</div>
                                    <div className="mt-1 text-xs text-gray-400">
                                      {runtime.symbols.join(', ') || '—'} · {runtime.mode} · {runtime.account_id}
                                    </div>
                                    <div className="mt-1 text-xs text-gray-500">
                                      ticks {runtime.tick_count} · signals {runtime.signal_count} · errors {runtime.error_count}
                                    </div>
                                    {runtime.blocked_reason && (
                                      <div className="mt-1 text-xs text-red-400">Blocked: {runtime.blocked_reason}</div>
                                    )}
                                    {runtime.stop_reason && (
                                      <div className="mt-1 text-xs text-gray-500">Stop reason: {runtime.stop_reason}</div>
                                    )}
                                  </div>
                                  <span
                                    className={clsx(
                                      'inline-flex rounded-full px-2 py-0.5 text-xs font-medium',
                                      statusConfig.color,
                                      statusConfig.bgColor,
                                    )}
                                  >
                                    {statusConfig.label}
                                  </span>
                                </div>
                                <div className="mt-3">
                                  <RuntimeActions
                                    runtime={runtime}
                                    onMessage={handleMutationMessage}
                                    onError={handleMutationError}
                                  />
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => openDeployDialog(strategy)}
                        className="rounded bg-blue-900/30 px-2 py-1 text-xs text-blue-300 hover:bg-blue-900/50"
                      >
                        Create Deployment
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {showDeployDialog && deployTarget && (
        <div className="dialog-backdrop" onClick={closeDeployDialog}>
          <div
            className="dialog-panel max-w-2xl"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <div className="flex items-start gap-4">
              <div className="flex-1">
                <h2 className="text-lg font-semibold text-white">Create Deployment</h2>
                <p className="mt-2 text-sm text-gray-400">
                  交易对、账户、模式属于 deployment 配置，而不是 start 时的临时参数。
                </p>

                <div className="mt-6 grid gap-4 md:grid-cols-2">
                  <div>
                    <label className="mb-1 block text-sm text-gray-400">Strategy</label>
                    <div className="rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white">
                      {deployTarget.strategy_id}
                    </div>
                  </div>

                  <div>
                    <label className="mb-1 block text-sm text-gray-400">Deployment ID</label>
                    <input
                      type="text"
                      value={draftDeploymentId}
                      onChange={(e) => setDraftDeploymentId(e.target.value)}
                      className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                  </div>

                  <div>
                    <label className="mb-1 block text-sm text-gray-400">Mode</label>
                    <select
                      value={draftMode}
                      onChange={(e) => handleDraftModeChange(e.target.value as DeploymentMode)}
                      className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                    >
                      <option value="demo">demo</option>
                      <option value="paper">paper</option>
                      <option value="shadow">shadow</option>
                      <option value="live">live</option>
                    </select>
                  </div>

                  <div>
                    <label className="mb-1 block text-sm text-gray-400">Account ID</label>
                    <input
                      type="text"
                      value={draftAccountId}
                      onChange={(e) => handleDraftAccountChange(e.target.value)}
                      className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                  </div>
                </div>

                <div className="mt-6">
                  <label className="mb-2 block text-sm text-gray-400">Primary Symbol</label>
                  <div className="max-h-64 space-y-2 overflow-y-auto rounded-lg border border-gray-700 bg-gray-900/60 p-3">
                    {isLoadingPairs ? (
                      <div className="flex items-center justify-center py-8">
                        <LoadingSpinner size="md" />
                        <span className="ml-3 text-sm text-gray-400">Loading trading pairs...</span>
                      </div>
                    ) : (
                      tradingPairsData?.pairs.map((pair) => (
                        <button
                          key={pair.symbol}
                          onClick={() => handleDraftSymbolChange(pair.symbol)}
                          className={clsx(
                            'w-full rounded-lg border px-4 py-3 text-left transition-colors',
                            draftSymbol === pair.symbol
                              ? 'border-blue-500 bg-blue-500/10'
                              : 'border-gray-700 bg-gray-800 hover:border-gray-600',
                          )}
                        >
                          <div className="flex items-center justify-between gap-3">
                            <div>
                              <span className="text-sm font-medium text-white">{pair.symbol}</span>
                              <span className="ml-2 text-xs text-gray-400">
                                {pair.base_asset}/{pair.quote_asset}
                              </span>
                            </div>
                            <div className="text-xs text-gray-500">Min: {pair.min_notional} {pair.quote_asset}</div>
                          </div>
                        </button>
                      ))
                    )}
                  </div>

                  <div className="mt-3 flex items-center gap-2">
                    <label className="text-sm text-gray-400">Custom Symbol:</label>
                    <input
                      type="text"
                      value={draftSymbol}
                      onChange={(e) => handleDraftSymbolChange(e.target.value)}
                      className="flex-1 rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                      placeholder="e.g. ETHUSDT"
                    />
                  </div>
                </div>
              </div>
            </div>

            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button"
                onClick={closeDeployDialog}
                className="rounded-md bg-gray-700 px-4 py-2 text-sm font-medium text-gray-200 transition-colors hover:bg-gray-600"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleCreateDeployment}
                disabled={loadMutation.isPending}
                className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
              >
                {loadMutation.isPending ? 'Loading...' : 'Create Deployment'}
              </button>
            </div>
          </div>
        </div>
      )}

      <StrategyDetailModal
        strategy={detailStrategy!}
        runtime={detailStrategy ? (deploymentsByStrategy.get(detailStrategy.strategy_id)?.[0] ?? null) : null}
        isOpen={!!detailStrategy}
        onClose={() => setDetailStrategy(null)}
      />
    </div>
  )
}
