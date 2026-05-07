import { useState } from 'react'
import { useBacktestList, useBacktestReport } from '@/hooks'
import { LoadingState, ErrorState, EmptyState } from '@/components/ui'
import { BacktestList, BacktestDetailPanel } from '@/components/backtests'
import { PageHeader } from '@/components/layout'
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
      <PageHeader title="Reports">
        {isFetching && <span className="text-xs text-accent-3">Refreshing...</span>}
        <button onClick={() => refetch()} className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700">Refresh</button>
      </PageHeader>

      <div className="p-6">
        <div className="grid gap-6 lg:grid-cols-2">
          <div>
            {completedReports.length === 0 ? (
              <EmptyState
                title="No Reports"
                message="No completed backtest reports found. Run a backtest first to generate reports."
                action={{ label: 'Refresh', onClick: () => refetch() }}
              />
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