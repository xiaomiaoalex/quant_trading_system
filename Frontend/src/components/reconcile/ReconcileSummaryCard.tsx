import { clsx } from 'clsx'
import type { ReconcileReport } from '@/types'
import { MetricCard } from '@/components/monitor'

interface ReconcileSummaryCardProps {
  report: ReconcileReport | undefined
}

export function ReconcileSummaryCard({ report }: ReconcileSummaryCardProps) {
  if (!report) return null

  const hasDrifts = (report.drifts?.length ?? 0) > 0 || report.diverged_count > 0
  const statusConfig = hasDrifts
    ? { label: 'Has Drifts', color: 'text-yellow-400' }
    : { label: 'Clean', color: 'text-green-400' }

  return (
    <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-gray-300">Reconciliation Summary</h3>
        <span className={clsx('text-sm font-medium', statusConfig.color)}>
          {statusConfig.label}
        </span>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard title="Total Checked" value={report.total_orders_checked} />
        <MetricCard title="Ghost Orders" value={report.ghost_count} subValue="local only" />
        <MetricCard title="Phantom Orders" value={report.phantom_count} subValue="exchange only" />
        <MetricCard title="Diverged" value={report.diverged_count} subValue="mismatched" />
      </div>
      {report.timestamp && (
        <p className="text-xs text-gray-500 mt-3">
          Last check: {new Date(report.timestamp).toLocaleString()}
        </p>
      )}
    </div>
  )
}