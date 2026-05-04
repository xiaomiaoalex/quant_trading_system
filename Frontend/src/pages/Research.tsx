import { useEffect, useState } from 'react'
import { researchAPI } from '@/api'
import { ErrorState, LoadingState } from '@/components/ui'
import { formatAPIError } from '@/api/client'
import type { StrategyCandidate } from '@/types'

const DEFAULT_CODE = `def get_plugin():
    return None
`

export function Research() {
  const [candidates, setCandidates] = useState<StrategyCandidate[]>([])
  const [strategyId, setStrategyId] = useState('research_strategy')
  const [code, setCode] = useState(DEFAULT_CODE)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [deletingCandidateId, setDeletingCandidateId] = useState<string | null>(null)

  const loadCandidates = async () => {
    setIsLoading(true)
    setError(null)
    try {
      setCandidates(await researchAPI.listCandidates())
    } catch (e) {
      setError(formatAPIError(e))
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    void loadCandidates()
  }, [])

  const createCandidate = async () => {
    setError(null)
    setMessage(null)
    try {
      const candidate = await researchAPI.createCandidate({
        strategy_id: strategyId,
        name: strategyId,
        code,
        created_by: 'console_user',
      })
      setMessage(`Candidate created: ${candidate.candidate_id}`)
      await loadCandidates()
    } catch (e) {
      setError(formatAPIError(e))
    }
  }

  const deleteCandidate = async (candidate: StrategyCandidate) => {
    setError(null)
    setMessage(null)
    setDeletingCandidateId(candidate.candidate_id)
    try {
      await researchAPI.deleteCandidate(candidate.candidate_id)
      setMessage(`Candidate deleted: ${candidate.candidate_id}`)
      await loadCandidates()
    } catch (e) {
      setError(formatAPIError(e))
    } finally {
      setDeletingCandidateId(null)
    }
  }

  if (isLoading) return <div className="p-6"><LoadingState message="Loading research candidates..." /></div>
  if (error && candidates.length === 0) return <div className="p-6"><ErrorState title="Failed to load candidates" message={error} onRetry={loadCandidates} /></div>

  return (
    <div className="min-h-screen bg-gray-900 p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold text-white">Research</h1>
        <button onClick={loadCandidates} className="rounded-md bg-gray-800 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700">
          Refresh
        </button>
      </div>

      {(error || message) && (
        <div className={`mb-4 rounded p-2 text-sm ${error ? 'bg-red-950/20 text-red-400' : 'bg-green-950/20 text-green-400'}`}>
          {error ?? message}
        </div>
      )}

      <div className="mb-6 rounded-lg border border-gray-700 bg-gray-800/50 p-4">
        <div className="mb-3 grid gap-3 md:grid-cols-2">
          <input
            value={strategyId}
            onChange={(e) => setStrategyId(e.target.value)}
            className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
            placeholder="strategy_id"
          />
          <button onClick={createCandidate} className="rounded bg-blue-900/40 px-3 py-2 text-sm text-blue-200 hover:bg-blue-900/60">
            Create Candidate
          </button>
        </div>
        <textarea
          value={code}
          onChange={(e) => setCode(e.target.value)}
          className="h-32 w-full rounded bg-gray-950 border border-gray-700 p-3 text-xs text-gray-200 font-mono"
          spellCheck={false}
        />
      </div>

      <div className="space-y-3">
        {candidates.map(candidate => (
          <div key={candidate.candidate_id} className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-white">{candidate.strategy_id}</h2>
                <p className="text-xs text-gray-400">{candidate.candidate_id}</p>
              </div>
              <div className="flex items-center gap-2">
                <span className="rounded bg-gray-900 px-2 py-1 text-xs text-gray-300">{candidate.status}</span>
                <button
                  onClick={() => void deleteCandidate(candidate)}
                  disabled={deletingCandidateId === candidate.candidate_id || ['APPROVED_FOR_PAPER', 'PAPER_RUNNING', 'PAUSED_BY_RISK'].includes(candidate.status)}
                  className="rounded bg-red-900/40 px-2 py-1 text-xs text-red-200 hover:bg-red-900/60 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {deletingCandidateId === candidate.candidate_id ? 'Deleting...' : 'Delete'}
                </button>
              </div>
            </div>
            <div className="mt-3 grid gap-2 text-xs text-gray-400 md:grid-cols-4">
              <span>code: {candidate.code_version ?? '-'}</span>
              <span>backtest: {candidate.backtest_run_id ?? '-'}</span>
              <span>feature: {candidate.feature_version}</span>
              <span>deployment: {candidate.deployment_id ?? '-'}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
