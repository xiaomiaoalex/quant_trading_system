import { clsx } from 'clsx'
import type { Drift, DriftType } from '@/types'
import { DRIFT_TYPE_DISPLAY } from '@/types'

interface ReconcileDriftTableProps {
  drifts: Drift[]
}

// Derive severity from drift_type
function getSeverityInfo(driftType: DriftType): { label: string; color: string; bgColor: string } {
  switch (driftType) {
    case 'GHOST':
      return { label: 'High', color: 'text-red-400', bgColor: 'bg-red-400/10' }
    case 'PHANTOM':
      return { label: 'Medium', color: 'text-yellow-400', bgColor: 'bg-yellow-400/10' }
    case 'DIVERGED':
      return { label: 'High', color: 'text-orange-400', bgColor: 'bg-orange-400/10' }
    default:
      return { label: 'Unknown', color: 'text-gray-400', bgColor: 'bg-gray-400/10' }
  }
}

export function ReconcileDriftTable({ drifts }: ReconcileDriftTableProps) {
  if (!drifts || drifts.length === 0) {
    return (
      <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-6 text-center">
        <p className="text-sm text-gray-400">No drifts detected.</p>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-gray-700 bg-gray-800/50 overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-700">
        <h3 className="text-sm font-medium text-gray-300">Drift Details ({drifts.length})</h3>
      </div>
      <table className="min-w-full divide-y divide-gray-700">
        <thead className="bg-gray-800">
          <tr>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Order ID</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Type</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Severity</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Local Status</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Exchange Status</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Grace Remaining</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Detected</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-700">
          {drifts.map(drift => {
            const severityConfig = getSeverityInfo(drift.drift_type)
            const typeConfig = DRIFT_TYPE_DISPLAY[drift.drift_type] || { label: drift.drift_type, color: 'text-gray-400' }
            return (
              <tr key={drift.cl_ord_id} className="hover:bg-gray-700/30">
                <td className="px-4 py-2 text-sm text-gray-300 font-mono">{drift.cl_ord_id}</td>
                <td className="px-4 py-2 text-sm" style={{ color: typeConfig.color }}>{typeConfig.label}</td>
                <td className="px-4 py-2">
                  <span className={clsx('inline-flex rounded-full px-2 py-0.5 text-xs font-medium', severityConfig.color, severityConfig.bgColor)}>
                    {severityConfig.label}
                  </span>
                </td>
                <td className="px-4 py-2 text-sm text-gray-400">{drift.local_status || '-'}</td>
                <td className="px-4 py-2 text-sm text-gray-400">{drift.exchange_status || '-'}</td>
                <td className="px-4 py-2 text-sm text-gray-400">
                  {drift.grace_period_remaining_sec !== null ? `${drift.grace_period_remaining_sec.toFixed(1)}s` : '-'}
                </td>
                <td className="px-4 py-2 text-xs text-gray-500">{new Date(drift.detected_at).toLocaleString()}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}