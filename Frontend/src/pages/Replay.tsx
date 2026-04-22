import { useEffect, useMemo, useState } from 'react'
import { LoadingState, ErrorState } from '@/components/ui'
import { useReplayJob, useReplayJobs, useTriggerReplay } from '@/hooks'
import { formatAPIError } from '@/api/client'

function formatTime(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

export function Replay() {
  const [streamKey, setStreamKey] = useState('orders')
  const [requestedBy, setRequestedBy] = useState('console_user')
  const [fromTs, setFromTs] = useState('')
  const [toTs, setToTs] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)

  const queryFilters = useMemo(
    () => ({
      status: statusFilter || undefined,
      stream_key: streamKey || undefined,
      limit: 200,
    }),
    [statusFilter, streamKey]
  )

  const { data: jobs, isLoading, isError, error, refetch, isFetching } = useReplayJobs(queryFilters)
  const { data: detail, isLoading: isDetailLoading } = useReplayJob(selectedJobId ?? '')
  const { trigger, isPending: isTriggering, error: triggerError } = useTriggerReplay()

  useEffect(() => {
    if (!jobs || jobs.length === 0) {
      setSelectedJobId(null)
      return
    }
    if (!selectedJobId || !jobs.some(item => item.job_id === selectedJobId)) {
      setSelectedJobId(jobs[0]?.job_id ?? null)
    }
  }, [jobs, selectedJobId])

  useEffect(() => {
    if (!triggerError) return
    setFormError(triggerError)
  }, [triggerError])

  const handleTrigger = async () => {
    setMessage(null)
    setFormError(null)
    const fromTsMs = fromTs ? Number(fromTs) : undefined
    const toTsMs = toTs ? Number(toTs) : undefined
    if (fromTs && Number.isNaN(fromTsMs)) {
      setFormError('from_ts_ms must be a valid number')
      return
    }
    if (toTs && Number.isNaN(toTsMs)) {
      setFormError('to_ts_ms must be a valid number')
      return
    }
    if (fromTsMs !== undefined && toTsMs !== undefined && fromTsMs > toTsMs) {
      setFormError('from_ts_ms must be <= to_ts_ms')
      return
    }
    const jobId = await trigger({
      stream_key: streamKey,
      requested_by: requestedBy,
      from_ts_ms: fromTsMs,
      to_ts_ms: toTsMs,
    })
    if (!jobId) return
    setSelectedJobId(jobId)
    setMessage(`Replay job submitted: ${jobId}`)
    refetch()
  }

  if (isLoading) {
    return (
      <div className="p-6">
        <LoadingState message="Loading replay jobs..." />
      </div>
    )
  }
  if (isError) {
    return (
      <div className="p-6">
        <ErrorState
          title="Failed to load replay jobs"
          message={formatAPIError(error)}
          onRetry={refetch}
        />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-900">
      <div className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="flex items-center justify-between px-6 py-4">
          <div className="flex items-center gap-4">
            <h1 className="text-xl font-semibold text-white">Replay</h1>
            {isFetching && <span className="text-xs text-gray-500">Refreshing...</span>}
          </div>
          <button
            onClick={() => refetch()}
            className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700"
          >
            Refresh
          </button>
        </div>
      </div>

      <div className="space-y-4 p-6">
        <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
          <h2 className="mb-3 text-sm font-semibold text-gray-200">Trigger Replay Job</h2>
          <div className="grid gap-3 md:grid-cols-4">
            <input
              value={streamKey}
              onChange={e => setStreamKey(e.target.value)}
              placeholder="stream_key"
              className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
            />
            <input
              value={requestedBy}
              onChange={e => setRequestedBy(e.target.value)}
              placeholder="requested_by"
              className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
            />
            <input
              value={fromTs}
              onChange={e => setFromTs(e.target.value)}
              placeholder="from_ts_ms (optional)"
              className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
            />
            <input
              value={toTs}
              onChange={e => setToTs(e.target.value)}
              placeholder="to_ts_ms (optional)"
              className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
            />
          </div>
          <div className="mt-3 flex items-center gap-3">
            <button
              onClick={handleTrigger}
              disabled={isTriggering}
              className="rounded bg-indigo-900/40 px-3 py-1.5 text-xs text-indigo-200 hover:bg-indigo-900/60 disabled:opacity-60"
            >
              {isTriggering ? 'Submitting...' : 'Trigger Replay'}
            </button>
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value)}
              className="rounded bg-gray-900 border border-gray-700 px-3 py-1.5 text-xs text-gray-300"
            >
              <option value="">All Status</option>
              <option value="PENDING">PENDING</option>
              <option value="RUNNING">RUNNING</option>
              <option value="COMPLETED">COMPLETED</option>
              <option value="FAILED">FAILED</option>
            </select>
          </div>
          {message && <div className="mt-3 rounded bg-green-950/20 p-2 text-sm text-green-400">{message}</div>}
          {formError && <div className="mt-3 rounded bg-red-950/20 p-2 text-sm text-red-400">{formError}</div>}
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-lg border border-gray-700 bg-gray-800/50 overflow-hidden">
            <div className="border-b border-gray-700 px-4 py-3">
              <h2 className="text-sm font-semibold text-gray-200">Jobs ({jobs?.length ?? 0})</h2>
            </div>
            {!jobs || jobs.length === 0 ? (
              <div className="p-6 text-sm text-gray-400">No replay jobs found.</div>
            ) : (
              <div className="max-h-[560px] overflow-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-gray-900/95">
                    <tr className="text-left text-xs uppercase tracking-wide text-gray-400">
                      <th className="px-3 py-2">Job</th>
                      <th className="px-3 py-2">Status</th>
                      <th className="px-3 py-2">Stream</th>
                      <th className="px-3 py-2">Requested</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map(job => (
                      <tr
                        key={job.job_id}
                        onClick={() => setSelectedJobId(job.job_id)}
                        className={
                          `cursor-pointer border-t border-gray-800 hover:bg-gray-800/60 ${
                            selectedJobId === job.job_id ? 'bg-gray-800/80' : ''
                          }`
                        }
                      >
                        <td className="px-3 py-2 text-gray-200">
                          <div>{job.job_id.slice(0, 8)}...</div>
                          <div className="text-xs text-gray-500">{job.requested_by}</div>
                        </td>
                        <td className="px-3 py-2 text-gray-300">{job.status}</td>
                        <td className="px-3 py-2 text-gray-300">{job.stream_key}</td>
                        <td className="px-3 py-2 text-gray-400">{formatTime(job.requested_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
            <h2 className="mb-3 text-sm font-semibold text-gray-200">Job Detail</h2>
            {!selectedJobId ? (
              <p className="text-sm text-gray-400">Select a job to view detail.</p>
            ) : isDetailLoading ? (
              <LoadingState message="Loading replay job detail..." />
            ) : !detail ? (
              <p className="text-sm text-gray-400">No detail found.</p>
            ) : (
              <div className="space-y-3 text-sm">
                <div className="grid gap-2 md:grid-cols-2">
                  <div className="text-gray-400">Job ID</div>
                  <div className="text-gray-200 break-all">{detail.job_id}</div>
                  <div className="text-gray-400">Status</div>
                  <div className="text-gray-200">{detail.status}</div>
                  <div className="text-gray-400">Stream</div>
                  <div className="text-gray-200">{detail.stream_key}</div>
                  <div className="text-gray-400">Requested By</div>
                  <div className="text-gray-200">{detail.requested_by}</div>
                  <div className="text-gray-400">Requested At</div>
                  <div className="text-gray-200">{formatTime(detail.requested_at)}</div>
                  <div className="text-gray-400">Started At</div>
                  <div className="text-gray-200">{formatTime(detail.started_at)}</div>
                  <div className="text-gray-400">Finished At</div>
                  <div className="text-gray-200">{formatTime(detail.finished_at)}</div>
                </div>

                <div>
                  <div className="mb-1 text-gray-400">Result Summary</div>
                  <pre className="max-h-52 overflow-auto rounded border border-gray-700 bg-gray-950 p-2 text-xs text-gray-300">
                    {JSON.stringify(detail.result_summary ?? {}, null, 2)}
                  </pre>
                </div>

                <div>
                  <div className="mb-1 text-gray-400">Error</div>
                  <pre className="max-h-32 overflow-auto rounded border border-gray-700 bg-gray-950 p-2 text-xs text-red-300">
                    {detail.error ?? '-'}
                  </pre>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

