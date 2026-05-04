import { useEffect, useState } from 'react'
import { researchAPI } from '@/api'
import { ErrorState, LoadingState } from '@/components/ui'
import { formatAPIError } from '@/api/client'
import type { PortfolioAutopilotSnapshot } from '@/types'

export function PortfolioAutopilot() {
  const [snapshot, setSnapshot] = useState<PortfolioAutopilotSnapshot | null>(null)
  const [dataStale, setDataStale] = useState(false)
  const [portfolioExposure, setPortfolioExposure] = useState(0)
  const [maxPortfolioExposure, setMaxPortfolioExposure] = useState(100000)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const loadSnapshot = async () => {
    setIsLoading(true)
    setError(null)
    try {
      setSnapshot(await researchAPI.getAutopilotSnapshot())
    } catch (e) {
      setError(formatAPIError(e))
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    void loadSnapshot()
  }, [])

  const runTick = async () => {
    setError(null)
    try {
      setSnapshot(await researchAPI.tickAutopilot({
        data_stale: dataStale,
        portfolio_exposure: portfolioExposure,
        max_portfolio_exposure: maxPortfolioExposure,
      }))
    } catch (e) {
      setError(formatAPIError(e))
    }
  }

  if (isLoading) return <div className="p-6"><LoadingState message="Loading autopilot snapshot..." /></div>
  if (error && snapshot === null) return <div className="p-6"><ErrorState title="Failed to load autopilot" message={error} onRetry={loadSnapshot} /></div>

  return (
    <div className="min-h-screen bg-gray-900 p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold text-white">Portfolio Autopilot</h1>
        <button onClick={loadSnapshot} className="rounded-md bg-gray-800 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700">
          Refresh
        </button>
      </div>

      {error && <div className="mb-4 rounded bg-red-950/20 p-2 text-sm text-red-400">{error}</div>}

      <div className="mb-6 grid gap-3 rounded-lg border border-gray-700 bg-gray-800/50 p-4 md:grid-cols-4">
        <label className="flex items-center gap-2 text-sm text-gray-300">
          <input type="checkbox" checked={dataStale} onChange={(e) => setDataStale(e.target.checked)} />
          data stale
        </label>
        <input type="number" value={portfolioExposure} onChange={(e) => setPortfolioExposure(Number(e.target.value))} className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200" />
        <input type="number" value={maxPortfolioExposure} onChange={(e) => setMaxPortfolioExposure(Number(e.target.value))} className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200" />
        <button onClick={runTick} className="rounded bg-purple-900/40 px-3 py-2 text-sm text-purple-200 hover:bg-purple-900/60">
          Run Tick
        </button>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
          <h2 className="mb-3 text-sm font-semibold text-white">Snapshot</h2>
          <div className="space-y-2 text-xs text-gray-400">
            <div>profiles: <span className="text-gray-200">{snapshot?.profiles.length ?? 0}</span></div>
            <div>data stale: <span className="text-gray-200">{String(snapshot?.data_stale ?? false)}</span></div>
            <div>exposure: <span className="text-gray-200">{snapshot?.portfolio_exposure ?? 0}</span></div>
          </div>
        </div>
        <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
          <h2 className="mb-3 text-sm font-semibold text-white">Decisions</h2>
          <div className="space-y-2">
            {(snapshot?.decisions ?? []).map(decision => (
              <div key={decision.decision_id} className="rounded bg-gray-900 p-3 text-xs text-gray-300">
                <div className="font-semibold text-white">{decision.action} {decision.deployment_id ?? ''}</div>
                <div>{decision.reason}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
