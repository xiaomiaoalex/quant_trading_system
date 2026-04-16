import { useState, useCallback } from 'react'
import { useBacktestList, useBacktestReport, useCreateBacktest } from '@/hooks'
import { LoadingState, ErrorState, ConfirmDialog } from '@/components/ui'
import { BacktestList, BacktestDetailPanel } from '@/components/backtests'
import { formatAPIError } from '@/api/client'

export function Backtests() {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [showCreateDialog, setShowCreateDialog] = useState(false)
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined)

  const { data: backtests, isLoading, isError, error, refetch, isFetching } = useBacktestList(
    statusFilter ? { status: statusFilter } : undefined
  )
  const { data: report, isLoading: isReportLoading } = useBacktestReport(selectedRunId ?? '')
  const { create, isPending: isCreating, error: createError } = useCreateBacktest()

  // Create form state
  const [formData, setFormData] = useState({
    strategy_id: '',
    version: 1,
    symbols: 'BTCUSDT',
    start_ts_ms: Date.now() - 30 * 24 * 60 * 60 * 1000,
    end_ts_ms: Date.now(),
    venue: 'BINANCE',
    requested_by: 'console_user',
  })

  const handleCreate = useCallback(async () => {
    const result = await create({
      ...formData,
      symbols: formData.symbols.split(',').map(s => s.trim()),
      version: Number(formData.version),
    })
    if (result) {
      setShowCreateDialog(false)
      setSelectedRunId(result.run_id)
    }
  }, [create, formData])

  if (isLoading) return <div className="p-6"><LoadingState message="Loading backtests..." /></div>
  if (isError) return <div className="p-6"><ErrorState title="Failed to load backtests" message={formatAPIError(error)} onRetry={refetch} /></div>

  return (
    <div className="min-h-screen bg-gray-900">
      <div className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="flex items-center justify-between px-6 py-4">
          <div className="flex items-center gap-4">
            <h1 className="text-xl font-semibold text-white">Backtests</h1>
            {isFetching && <span className="text-xs text-gray-500">Refreshing...</span>}
          </div>
          <div className="flex items-center gap-3">
            <select
              value={statusFilter ?? ''}
              onChange={(e) => setStatusFilter(e.target.value || undefined)}
              className="rounded bg-gray-800 px-3 py-1.5 text-sm text-gray-300 border border-gray-700"
            >
              <option value="">All Status</option>
              <option value="RUNNING">Running</option>
              <option value="COMPLETED">Completed</option>
              <option value="FAILED">Failed</option>
            </select>
            <button onClick={() => refetch()} className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700">Refresh</button>
            <button
              onClick={() => setShowCreateDialog(true)}
              className="rounded-md bg-blue-900/30 px-4 py-2 text-sm font-medium text-blue-300 hover:bg-blue-900/50"
            >
              New Backtest
            </button>
          </div>
        </div>
      </div>

      <div className="p-6">
        <div className="grid gap-6 lg:grid-cols-2">
          <div>
            <BacktestList
              backtests={backtests ?? []}
              onSelect={setSelectedRunId}
              selectedRunId={selectedRunId ?? undefined}
            />
          </div>
          <div>
            <BacktestDetailPanel
              report={report as any}
              isLoading={isReportLoading}
            />
          </div>
        </div>
      </div>

      <ConfirmDialog
        isOpen={showCreateDialog}
        title="New Backtest"
        message={
          <div className="space-y-4">
            {createError && (
              <div className="rounded bg-red-950/20 p-2 text-sm text-red-400">
                {createError}
              </div>
            )}
            <div>
              <label className="block text-xs text-gray-400 mb-1">Strategy ID</label>
              <input
                type="text"
                value={formData.strategy_id}
                onChange={(e) => setFormData({ ...formData, strategy_id: e.target.value })}
                className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-300"
                placeholder="ema_cross_btc"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Version</label>
              <input
                type="number"
                value={formData.version}
                onChange={(e) => setFormData({ ...formData, version: Number(e.target.value) })}
                className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-300"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Symbols (comma-separated)</label>
              <input
                type="text"
                value={formData.symbols}
                onChange={(e) => setFormData({ ...formData, symbols: e.target.value })}
                className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-300"
                placeholder="BTCUSDT"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Venue</label>
              <select
                value={formData.venue}
                onChange={(e) => setFormData({ ...formData, venue: e.target.value })}
                className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-300"
              >
                <option value="BINANCE">BINANCE</option>
                <option value="OKX">OKX</option>
              </select>
            </div>
          </div>
        }
        confirmLabel={isCreating ? 'Creating...' : 'Create'}
        cancelLabel="Cancel"
        variant="danger"
        isLoading={isCreating}
        onConfirm={handleCreate}
        onCancel={() => setShowCreateDialog(false)}
      />
    </div>
  )
}