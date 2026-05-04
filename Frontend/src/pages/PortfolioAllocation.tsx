import { useEffect, useState } from 'react'
import { researchAPI } from '@/api'
import { ErrorState, LoadingState } from '@/components/ui'
import { formatAPIError } from '@/api/client'
import type { StrategyAllocationProfile } from '@/types'

export function PortfolioAllocation() {
  const [profiles, setProfiles] = useState<StrategyAllocationProfile[]>([])
  const [deploymentId, setDeploymentId] = useState('lab_strategy__btcusdt__paper__binance_demo')
  const [strategyId, setStrategyId] = useState('lab_strategy')
  const [maxNotional, setMaxNotional] = useState(10000)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const loadProfiles = async () => {
    setIsLoading(true)
    setError(null)
    try {
      setProfiles(await researchAPI.listAllocations())
    } catch (e) {
      setError(formatAPIError(e))
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    void loadProfiles()
  }, [])

  const saveProfile = async () => {
    setError(null)
    try {
      await researchAPI.upsertAllocation(deploymentId, {
        strategy_id: strategyId,
        max_notional: maxNotional,
        max_symbol_exposure: maxNotional,
        max_portfolio_weight: 0.2,
        min_confidence: 0.5,
        priority: 100,
        enabled: true,
      })
      await loadProfiles()
    } catch (e) {
      setError(formatAPIError(e))
    }
  }

  if (isLoading) return <div className="p-6"><LoadingState message="Loading allocation profiles..." /></div>
  if (error && profiles.length === 0) return <div className="p-6"><ErrorState title="Failed to load allocations" message={error} onRetry={loadProfiles} /></div>

  return (
    <div className="min-h-screen bg-gray-900 p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold text-white">Portfolio Allocation</h1>
        <button onClick={loadProfiles} className="rounded-md bg-gray-800 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700">
          Refresh
        </button>
      </div>

      {error && <div className="mb-4 rounded bg-red-950/20 p-2 text-sm text-red-400">{error}</div>}

      <div className="mb-6 grid gap-3 rounded-lg border border-gray-700 bg-gray-800/50 p-4 md:grid-cols-4">
        <input value={deploymentId} onChange={(e) => setDeploymentId(e.target.value)} className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200" />
        <input value={strategyId} onChange={(e) => setStrategyId(e.target.value)} className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200" />
        <input type="number" value={maxNotional} onChange={(e) => setMaxNotional(Number(e.target.value))} className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200" />
        <button onClick={saveProfile} className="rounded bg-emerald-900/40 px-3 py-2 text-sm text-emerald-200 hover:bg-emerald-900/60">
          Save Budget
        </button>
      </div>

      <div className="overflow-hidden rounded-lg border border-gray-700">
        <table className="min-w-full divide-y divide-gray-700">
          <thead className="bg-gray-800">
            <tr>
              {['Deployment', 'Strategy', 'Max', 'Remaining', 'Priority', 'Enabled'].map(header => (
                <th key={header} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-400">{header}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800 bg-gray-900">
            {profiles.map(profile => (
              <tr key={profile.deployment_id}>
                <td className="px-4 py-3 text-xs text-gray-200">{profile.deployment_id}</td>
                <td className="px-4 py-3 text-xs text-gray-300">{profile.strategy_id}</td>
                <td className="px-4 py-3 text-xs text-gray-300">{profile.max_notional}</td>
                <td className="px-4 py-3 text-xs text-gray-300">{profile.remaining_notional}</td>
                <td className="px-4 py-3 text-xs text-gray-300">{profile.priority}</td>
                <td className="px-4 py-3 text-xs text-gray-300">{profile.enabled ? 'yes' : 'no'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
