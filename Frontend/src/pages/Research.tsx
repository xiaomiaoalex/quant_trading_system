import { useEffect, useState } from 'react'
import { clsx } from 'clsx'
import { researchAPI } from '@/api'
import { ErrorState, LoadingState, EmptyState } from '@/components/ui'
import { PageHeader } from '@/components/layout'
import { formatAPIError } from '@/api/client'
import type { StrategyCandidate } from '@/types'
import { CANDIDATE_STATUS_DISPLAY } from '@/types/research'

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
    <div className="min-h-screen bg-gray-900">
      <PageHeader title="Research">
        <button onClick={loadCandidates} className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700">
          Refresh
        </button>
      </PageHeader>

      <div className="p-6 space-y-6">
        {(error || message) && (
          <div className={`rounded p-2 text-sm ${error ? 'bg-red-950/20 text-red-400' : 'bg-green-950/20 text-green-400'}`}>
            {error ?? message}
          </div>
        )}

        <div className="rounded-lg border border-gray-700 bg-gray-800/40 p-4">
          <div className="mb-3 grid gap-3 md:grid-cols-2">
            <input
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
              className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
              placeholder="strategy_id"
            />
            <button onClick={createCandidate} className="rounded bg-blue-900/40 px-3 py-2 text-sm text-blue-200 hover:bg-blue-900/60 transition-colors">
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

        {candidates.length === 0 ? (
          <EmptyState
            title="No Research Candidates"
            message="No candidates found. Create one to begin research."
            action={{ label: 'Refresh', onClick: loadCandidates }}
          />
        ) : (
          <div className="space-y-3">
            {candidates.map(candidate => (
              <div key={candidate.candidate_id} className="rounded-lg border border-gray-700 bg-gray-800/40 p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold text-white">{candidate.strategy_id}</h2>
                    <p className="text-xs font-mono text-accent-3">{candidate.candidate_id}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    {(() => {
                      const cfg = CANDIDATE_STATUS_DISPLAY[candidate.status] ?? { label: candidate.status, bgClass: 'bg-gray-700', textClass: 'text-gray-400' }
                      return (
                        <span className={clsx('rounded px-2 py-1 text-xs font-medium', cfg.bgClass, cfg.textClass)}>
                          {cfg.label}
                        </span>
                      )
                    })()}
                    <button
                      onClick={() => void deleteCandidate(candidate)}
                      disabled={deletingCandidateId === candidate.candidate_id || ['APPROVED_FOR_PAPER', 'PAPER_RUNNING', 'PAUSED_BY_RISK'].includes(candidate.status)}
                      className="rounded bg-red-900/40 px-2 py-1 text-xs text-red-200 hover:bg-red-900/60 disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
                    >
                      {deletingCandidateId === candidate.candidate_id ? 'Deleting...' : 'Delete'}
                    </button>
                  </div>
                </div>
                <div className="mt-3 grid gap-2 text-xs text-accent-3 md:grid-cols-4">
                  <div>code: <span className="text-gray-200">{candidate.code_version ?? '-'}</span></div>
                  <div>backtest: <span className="text-gray-200">{candidate.backtest_run_id ?? '-'}</span></div>
                  <div>feature: <span className="text-gray-200">{candidate.feature_version}</span></div>
                  <div>deployment: <span className="text-gray-200">{candidate.deployment_id ?? '-'}</span></div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
