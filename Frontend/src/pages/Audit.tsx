import { useEffect, useMemo, useState } from 'react'
import { LoadingState, ErrorState } from '@/components/ui'
import { useAuditEntries, useAuditEntry } from '@/hooks'
import { formatAPIError } from '@/api/client'

function formatTime(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

export function Audit() {
  const [strategyId, setStrategyId] = useState('')
  const [status, setStatus] = useState('')
  const [eventType, setEventType] = useState('')
  const [selectedEntryId, setSelectedEntryId] = useState<string | null>(null)

  const queryFilters = useMemo(
    () => ({
      strategy_id: strategyId || undefined,
      status: status || undefined,
      event_type: eventType || undefined,
      limit: 200,
    }),
    [strategyId, status, eventType]
  )

  const { data: entries, isLoading, isError, error, refetch, isFetching } = useAuditEntries(queryFilters)
  const { data: detail, isLoading: isDetailLoading } = useAuditEntry(selectedEntryId ?? '')

  useEffect(() => {
    if (!entries || entries.length === 0) {
      setSelectedEntryId(null)
      return
    }
    if (!selectedEntryId || !entries.some(item => item.entry_id === selectedEntryId)) {
      setSelectedEntryId(entries[0]?.entry_id ?? null)
    }
  }, [entries, selectedEntryId])

  if (isLoading) return <div className="p-6"><LoadingState message="Loading audit entries..." /></div>
  if (isError) return <div className="p-6"><ErrorState title="Failed to load audit entries" message={formatAPIError(error)} onRetry={refetch} /></div>

  return (
    <div className="min-h-screen bg-gray-900">
      <div className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="flex items-center justify-between px-6 py-4">
          <div className="flex items-center gap-4">
            <h1 className="text-xl font-semibold text-white">Audit</h1>
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

      <div className="p-6 space-y-4">
        <div className="grid gap-3 md:grid-cols-3">
          <input
            value={strategyId}
            onChange={e => setStrategyId(e.target.value)}
            placeholder="Filter by strategy_id"
            className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
          />
          <input
            value={status}
            onChange={e => setStatus(e.target.value)}
            placeholder="Filter by status"
            className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
          />
          <input
            value={eventType}
            onChange={e => setEventType(e.target.value)}
            placeholder="Filter by event_type"
            className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
          />
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-lg border border-gray-700 bg-gray-800/50 overflow-hidden">
            <div className="border-b border-gray-700 px-4 py-3">
              <h2 className="text-sm font-semibold text-gray-200">
                Entries ({entries?.length ?? 0})
              </h2>
            </div>
            {!entries || entries.length === 0 ? (
              <div className="p-6 text-sm text-gray-400">No audit entries found.</div>
            ) : (
              <div className="max-h-[560px] overflow-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-gray-900/95">
                    <tr className="text-left text-xs uppercase tracking-wide text-gray-400">
                      <th className="px-3 py-2">Strategy</th>
                      <th className="px-3 py-2">Status</th>
                      <th className="px-3 py-2">Event</th>
                      <th className="px-3 py-2">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entries.map(entry => (
                      <tr
                        key={entry.entry_id}
                        onClick={() => setSelectedEntryId(entry.entry_id)}
                        className={
                          `cursor-pointer border-t border-gray-800 hover:bg-gray-800/60 ${
                            selectedEntryId === entry.entry_id ? 'bg-gray-800/80' : ''
                          }`
                        }
                      >
                        <td className="px-3 py-2 text-gray-200">
                          <div>{entry.strategy_id}</div>
                          <div className="text-xs text-gray-500">{entry.entry_id.slice(0, 8)}...</div>
                        </td>
                        <td className="px-3 py-2 text-gray-300">{entry.status}</td>
                        <td className="px-3 py-2 text-gray-300">{entry.event_type}</td>
                        <td className="px-3 py-2 text-gray-400">{formatTime(entry.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
            <h2 className="mb-3 text-sm font-semibold text-gray-200">Entry Detail</h2>
            {!selectedEntryId ? (
              <p className="text-sm text-gray-400">Select an entry to view detail.</p>
            ) : isDetailLoading ? (
              <LoadingState message="Loading entry detail..." />
            ) : !detail ? (
              <p className="text-sm text-gray-400">No detail found.</p>
            ) : (
              <div className="space-y-3 text-sm">
                <div className="grid gap-2 md:grid-cols-2">
                  <div className="text-gray-400">Entry ID</div>
                  <div className="text-gray-200 break-all">{detail.entry_id}</div>
                  <div className="text-gray-400">Strategy</div>
                  <div className="text-gray-200">{detail.strategy_id}</div>
                  <div className="text-gray-400">Status</div>
                  <div className="text-gray-200">{detail.status}</div>
                  <div className="text-gray-400">Event</div>
                  <div className="text-gray-200">{detail.event_type}</div>
                  <div className="text-gray-400">Created</div>
                  <div className="text-gray-200">{formatTime(detail.created_at)}</div>
                  <div className="text-gray-400">LLM</div>
                  <div className="text-gray-200">{detail.llm_backend ?? '-'} / {detail.llm_model ?? '-'}</div>
                </div>

                <div>
                  <div className="mb-1 text-gray-400">Prompt</div>
                  <pre className="max-h-32 overflow-auto rounded border border-gray-700 bg-gray-950 p-2 text-xs text-gray-300">
                    {detail.prompt ?? '-'}
                  </pre>
                </div>

                <div>
                  <div className="mb-1 text-gray-400">Generated Code</div>
                  <pre className="max-h-64 overflow-auto rounded border border-gray-700 bg-gray-950 p-2 text-xs text-gray-300">
                    {detail.generated_code ?? '-'}
                  </pre>
                </div>

                <div>
                  <div className="mb-1 text-gray-400">Metadata</div>
                  <pre className="max-h-40 overflow-auto rounded border border-gray-700 bg-gray-950 p-2 text-xs text-gray-300">
                    {JSON.stringify(detail.metadata ?? {}, null, 2)}
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
