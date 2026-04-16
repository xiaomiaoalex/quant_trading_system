import { clsx } from 'clsx'
import type { BacktestRun } from '@/types'
import { BacktestStatusBadge } from './BacktestStatusBadge'

interface BacktestListProps {
  backtests: BacktestRun[]
  onSelect: (runId: string) => void
  selectedRunId?: string
}

export function BacktestList({ backtests, onSelect, selectedRunId }: BacktestListProps) {
  if (!backtests || backtests.length === 0) {
    return (
      <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-6 text-center">
        <p className="text-sm text-gray-400">No backtest runs found.</p>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-gray-700 bg-gray-800/50 overflow-hidden">
      <table className="min-w-full divide-y divide-gray-700">
        <thead className="bg-gray-800">
          <tr>
            <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Run ID</th>
            <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Strategy</th>
            <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Status</th>
            <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Progress</th>
            <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Created</th>
            <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-700">
          {backtests.map(backtest => (
            <tr
              key={backtest.run_id}
              className={clsx(
                'hover:bg-gray-700/30 cursor-pointer',
                selectedRunId === backtest.run_id && 'bg-gray-700/50'
              )}
              onClick={() => onSelect(backtest.run_id)}
            >
              <td className="px-4 py-3 text-sm text-gray-300 font-mono">{backtest.run_id.slice(0, 8)}...</td>
              <td className="px-4 py-3 text-sm text-white">{backtest.strategy_id}</td>
              <td className="px-4 py-3">
                <BacktestStatusBadge status={backtest.status} />
              </td>
              <td className="px-4 py-3">
                {backtest.status === 'RUNNING' && backtest.progress !== undefined ? (
                  <div className="flex items-center gap-2">
                    <div className="w-20 h-2 bg-gray-700 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-blue-500 rounded-full transition-all"
                        style={{ width: `${(backtest.progress * 100).toFixed(0)}%` }}
                      />
                    </div>
                    <span className="text-xs text-gray-400">{(backtest.progress * 100).toFixed(0)}%</span>
                  </div>
                ) : (
                  <span className="text-xs text-gray-500">-</span>
                )}
              </td>
              <td className="px-4 py-3 text-sm text-gray-400">
                {backtest.created_at ? new Date(backtest.created_at).toLocaleString() : '-'}
              </td>
              <td className="px-4 py-3">
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    onSelect(backtest.run_id)
                  }}
                  className="rounded bg-blue-900/30 px-2 py-1 text-xs text-blue-300 hover:bg-blue-900/50"
                >
                  View
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}