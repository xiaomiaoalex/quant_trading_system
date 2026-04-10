import type { AdapterHealth } from '@/types'
import { AdapterStatusBadge } from '@/components/ui'
import { formatTsMs } from '@/utils'

interface AdapterHealthTableProps {
  adapters: Record<string, AdapterHealth>
  isLoading?: boolean
}

export function AdapterHealthTable({ adapters, isLoading }: AdapterHealthTableProps) {
  const adapterList = Object.values(adapters)

  if (isLoading) {
    return (
      <div className="rounded-lg border border-gray-700/50 bg-gray-800/50">
        <div className="border-b border-gray-700/50 px-4 py-3">
          <h3 className="text-sm font-medium text-gray-300">Adapter Health</h3>
        </div>
        <div className="p-4">
          <div className="space-y-3">
            {[1, 2, 3].map(i => (
              <div key={i} className="h-12 animate-pulse rounded bg-gray-700/50" />
            ))}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-gray-700/50 bg-gray-800/50 overflow-hidden">
      <div className="border-b border-gray-700/50 px-4 py-3">
        <h3 className="text-sm font-medium text-gray-300">Adapter Health</h3>
      </div>
      {adapterList.length === 0 ? (
        <div className="p-8 text-center text-gray-500">
          <p>No adapters configured</p>
        </div>
      ) : (
        <table className="w-full">
          <thead>
            <tr
              className="border-b border-gray-700/50 text-left text-xs text-gray-500
              uppercase"
            >
              <th className="px-4 py-2 font-medium">Adapter</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Last Heartbeat</th>
              <th className="px-4 py-2 font-medium">Errors</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-700/30">
            {adapterList.map(adapter => (
              <tr key={adapter.adapter_name} className="table-row-hover">
                <td className="px-4 py-3 text-sm font-medium text-white">{adapter.adapter_name}</td>
                <td className="px-4 py-3">
                  <AdapterStatusBadge status={adapter.status} />
                </td>
                <td className="px-4 py-3 text-sm text-gray-400">
                  {formatTsMs(adapter.last_heartbeat_ts_ms)}
                </td>
                <td className="px-4 py-3">
                  {adapter.error_count > 0 ? (
                    <span
                      className="inline-flex items-center rounded-full bg-red-950/50 px-2 py-0.5
                      text-xs font-medium text-red-400"
                    >
                      {adapter.error_count}
                    </span>
                  ) : (
                    <span className="text-sm text-gray-600">0</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
