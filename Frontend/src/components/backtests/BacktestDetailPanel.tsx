import type { BacktestReport } from '@/types'
import { BacktestStatusBadge } from './BacktestStatusBadge'

interface BacktestDetailPanelProps {
  report: BacktestReport | undefined
  isLoading: boolean
}

export function BacktestDetailPanel({ report, isLoading }: BacktestDetailPanelProps) {
  if (isLoading) {
    return (
      <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-6">
        <div className="animate-pulse space-y-4">
          <div className="h-4 bg-gray-700 rounded w-1/4" />
          <div className="h-4 bg-gray-700 rounded w-1/2" />
          <div className="h-4 bg-gray-700 rounded w-3/4" />
        </div>
      </div>
    )
  }

  if (!report) {
    return (
      <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-6 text-center">
        <p className="text-sm text-gray-400">Select a backtest run to view details.</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-medium text-white">Backtest Report</h3>
          <BacktestStatusBadge status={report.status} />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <p className="text-xs text-gray-500">Run ID</p>
            <p className="text-sm text-gray-300 font-mono">{report.run_id}</p>
          </div>
          <div>
            <p className="text-xs text-gray-500">Strategy</p>
            <p className="text-sm text-gray-300">{report.strategy_id} (v{report.version})</p>
          </div>
          <div>
            <p className="text-xs text-gray-500">Symbols</p>
            <p className="text-sm text-gray-300">{report.symbols.join(', ')}</p>
          </div>
          <div>
            <p className="text-xs text-gray-500">Period</p>
            <p className="text-sm text-gray-300">
              {new Date(report.start_ts_ms).toLocaleDateString()} - {new Date(report.end_ts_ms).toLocaleDateString()}
            </p>
          </div>
        </div>
      </div>

      {/* Returns */}
      {report.returns && (
        <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
          <h4 className="text-sm font-medium text-gray-300 mb-3">Returns</h4>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <p className="text-xs text-gray-500">Total Return</p>
              <p className="text-sm text-green-400 font-medium">
                {report.returns.total_return_pct !== undefined ? `${report.returns.total_return_pct.toFixed(2)}%` : '-'}
              </p>
            </div>
            <div>
              <p className="text-xs text-gray-500">Sharpe Ratio</p>
              <p className="text-sm text-gray-300">
                {report.returns.sharpe_ratio !== undefined ? report.returns.sharpe_ratio.toFixed(2) : '-'}
              </p>
            </div>
            <div>
              <p className="text-xs text-gray-500">Annualized Return</p>
              <p className="text-sm text-gray-300">
                {report.returns.annualized_return !== undefined ? `${report.returns.annualized_return.toFixed(2)}%` : '-'}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Risk */}
      {report.risk && (
        <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
          <h4 className="text-sm font-medium text-gray-300 mb-3">Risk</h4>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <p className="text-xs text-gray-500">Max Drawdown</p>
              <p className="text-sm text-red-400 font-medium">
                {report.risk.max_drawdown_pct !== undefined ? `${report.risk.max_drawdown_pct.toFixed(2)}%` : '-'}
              </p>
            </div>
            <div>
              <p className="text-xs text-gray-500">Volatility</p>
              <p className="text-sm text-gray-300">
                {report.risk.volatility !== undefined ? `${report.risk.volatility.toFixed(2)}%` : '-'}
              </p>
            </div>
            <div>
              <p className="text-xs text-gray-500">VaR 95%</p>
              <p className="text-sm text-gray-300">
                {report.risk.var_95 !== undefined ? `${report.risk.var_95.toFixed(2)}%` : '-'}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Trades */}
      {report.trades && report.trades.length > 0 && (
        <div className="rounded-lg border border-gray-700 bg-gray-800/50 overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-700">
            <h4 className="text-sm font-medium text-gray-300">Trades ({report.trades.length})</h4>
          </div>
          <table className="min-w-full divide-y divide-gray-700">
            <thead className="bg-gray-800">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-400">Symbol</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-400">Side</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-400">Price</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-400">Quantity</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-gray-400">Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {report.trades.slice(0, 20).map((trade, i) => (
                <tr key={i} className="hover:bg-gray-700/30">
                  <td className="px-4 py-2 text-sm text-gray-300">{trade.symbol}</td>
                  <td className="px-4 py-2 text-sm">
                    <span className={trade.side === 'BUY' ? 'text-green-400' : 'text-red-400'}>
                      {trade.side}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-sm text-gray-300">${trade.price.toFixed(2)}</td>
                  <td className="px-4 py-2 text-sm text-gray-300">{trade.quantity}</td>
                  <td className="px-4 py-2 text-xs text-gray-500">{new Date(trade.timestamp).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {report.trades.length > 20 && (
            <div className="px-4 py-2 text-xs text-gray-500 text-center">
              Showing 20 of {report.trades.length} trades
            </div>
          )}
        </div>
      )}

      {/* Error */}
      {report.error && (
        <div className="rounded-lg border border-red-900/50 bg-red-950/20 p-4">
          <h4 className="text-sm font-medium text-red-300 mb-2">Error</h4>
          <p className="text-sm text-red-400">{report.error}</p>
        </div>
      )}
    </div>
  )
}