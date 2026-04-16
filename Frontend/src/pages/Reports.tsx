import { useState } from 'react'
import { useBacktestList, useBacktestReport } from '@/hooks'
import { LoadingState, ErrorState } from '@/components/ui'
import { BacktestList, BacktestDetailPanel } from '@/components/backtests'
import { formatAPIError } from '@/api/client'

export function Reports() {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const { data: backtests, isLoading, isError, error, refetch, isFetching } = useBacktestList({ status: 'COMPLETED' })
  const { data: report, isLoading: isReportLoading } = useBacktestReport(selectedRunId ?? '')

  if (isLoading) return <div className="p-6"><LoadingState message="Loading reports..." /></div>
  if (isError) return <div className="p-6"><ErrorState title="Failed to load reports" message={formatAPIError(error)} onRetry={refetch} /></div>

  const completedReports = backtests ?? []

  return (
    <div className="min-h-screen bg-gray-900">
      <div className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="flex items-center justify-between px-6 py-4">
          <div className="flex items-center gap-4">
            <h1 className="text-xl font-semibold text-white">Reports</h1>
            {isFetching && <span className="text-xs text-gray-500">Refreshing...</span>}
          </div>
          <button onClick={() => refetch()} className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700">Refresh</button>
        </div>
      </div>

      <div className="p-6">
        <div className="grid gap-6 lg:grid-cols-2">
          <div>
            {completedReports.length === 0 ? (
              <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-6 text-center">
                <p className="text-sm text-gray-400">No completed backtest reports found.</p>
                <p className="text-xs text-gray-500 mt-1">Run a backtest first to generate reports.</p>
              </div>
            ) : (
              <BacktestList
                backtests={completedReports}
                onSelect={setSelectedRunId}
                selectedRunId={selectedRunId ?? undefined}
              />
            )}
          </div>
          <div>
            <BacktestDetailPanel
              report={report as any}
              isLoading={isReportLoading}
            />
          </div>
        </div>
      </div>
    </div>
  )
}