import { clsx } from 'clsx'
import type { Drift } from '@/types'
import { DRIFT_SEVERITY_DISPLAY, DRIFT_TYPE_DISPLAY } from '@/types'

interface ReconcileDriftTableProps {
  drifts: Drift[]
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
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Local</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Exchange</th>
            <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Detected</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-700">
          {drifts.map(drift => {
            const severityConfig = DRIFT_SEVERITY_DISPLAY[drift.severity]
            const typeConfig = DRIFT_TYPE_DISPLAY[drift.drift_type]
            return (
              <tr key={drift.drift_id} className="hover:bg-gray-700/30">
                <td className="px-4 py-2 text-sm text-gray-300 font-mono">{drift.order_id}</td>
                <td className="px-4 py-2 text-sm" style={{ color: typeConfig.color }}>{typeConfig.label}</td>
                <td className="px-4 py-2">
                  <span className={clsx('inline-flex rounded-full px-2 py-0.5 text-xs font-medium', severityConfig.color, severityConfig.bgColor)}>
                    {severityConfig.label}
                  </span>
                </td>
                <td className="px-4 py-2 text-sm text-gray-400">
                  {drift.local_side} {drift.local_price && `@ ${drift.local_price}`} {drift.local_quantity && `x ${drift.local_quantity}`}
                </td>
                <td className="px-4 py-2 text-sm text-gray-400">
                  {drift.exchange_side} {drift.exchange_price && `@ ${drift.exchange_price}`} {drift.exchange_quantity && `x ${drift.exchange_quantity}`}
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