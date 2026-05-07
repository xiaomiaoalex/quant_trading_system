import { useEffect, useState } from 'react'
import { researchAPI } from '@/api'
import { ErrorState, LoadingState } from '@/components/ui'
import { PageHeader } from '@/components/layout'
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
    <div className="min-h-screen bg-gray-900">
      <PageHeader title="Portfolio Autopilot">
        <button onClick={loadSnapshot} className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700">
          Refresh
        </button>
      </PageHeader>

      <div className="p-6 space-y-6">
        {error && <div className="rounded bg-red-950/20 p-2 text-sm text-red-400">{error}</div>}

        <div className="grid gap-3 rounded-lg border border-gray-700 bg-gray-800/50 p-4 md:grid-cols-4">
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input type="checkbox" checked={dataStale} onChange={(e) => setDataStale(e.target.checked)} aria-label="Data stale flag" />
            data stale
          </label>
          <input type="number" value={portfolioExposure} onChange={(e) => setPortfolioExposure(Number(e.target.value))} aria-label="Current portfolio exposure" className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200" placeholder="portfolio_exposure" />
          <input type="number" value={maxPortfolioExposure} onChange={(e) => setMaxPortfolioExposure(Number(e.target.value))} aria-label="Maximum portfolio exposure" className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200" placeholder="max_portfolio_exposure" />
          <button onClick={runTick} className="rounded bg-purple-900/40 px-3 py-2 text-sm text-purple-200 hover:bg-purple-900/60">
            Run Tick
          </button>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
            <h2 className="mb-3 text-sm font-semibold text-accent-5">Snapshot</h2>
            <div className="space-y-2 text-xs text-accent-3">
              <div>profiles: <span className="text-gray-200">{snapshot?.profiles.length ?? 0}</span></div>
              <div>data stale: <span className="text-gray-200">{String(snapshot?.data_stale ?? false)}</span></div>
              <div>exposure: <span className="text-gray-200">{snapshot?.portfolio_exposure ?? 0}</span></div>
            </div>
          </div>
          <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
            <h2 className="mb-3 text-sm font-semibold text-accent-5">Decisions</h2>
            {(!snapshot?.decisions || snapshot.decisions.length === 0) ? (
              <p className="text-xs text-gray-500">No decisions recorded.</p>
            ) : (
              <div className="space-y-2">
                {snapshot.decisions.map(decision => (
                  <div key={decision.decision_id} className="rounded bg-gray-900/60 p-3 text-xs">
                    <div className="font-medium text-white">{decision.action} {decision.deployment_id ?? ''}</div>
                    <div className="text-accent-3 mt-1">{decision.reason}</div>
                    <div className="text-xs text-accent-1 mt-1">{decision.decision_id.slice(0, 8)}...</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
