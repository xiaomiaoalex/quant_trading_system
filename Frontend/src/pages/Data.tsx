import { useEffect, useState } from 'react'
import { clsx } from 'clsx'
import { researchAPI } from '@/api'
import { ErrorState, LoadingState, EmptyState } from '@/components/ui'
import { PageHeader } from '@/components/layout'
import { formatAPIError } from '@/api/client'
import type { DataCatalogResponse } from '@/types'
import { DATA_SOURCE_STATUS_DISPLAY, type DataSourceStatusValue } from '@/types/research'

export function Data() {
  const [catalog, setCatalog] = useState<DataCatalogResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const loadCatalog = async () => {
    setIsLoading(true)
    setError(null)
    try {
      setCatalog(await researchAPI.getDataCatalog())
    } catch (e) {
      setError(formatAPIError(e))
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    void loadCatalog()
  }, [])

  if (isLoading) return <div className="p-6"><LoadingState message="Loading data catalog..." /></div>
  if (error) return <div className="p-6"><ErrorState title="Failed to load data catalog" message={error} onRetry={loadCatalog} /></div>

  return (
    <div className="min-h-screen bg-gray-900">
      <PageHeader title="Data">
        <button onClick={loadCatalog} className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700">
          Refresh
        </button>
      </PageHeader>

      <div className="p-6 space-y-6">
        <div className="text-sm text-accent-3">Feature version: {catalog?.feature_version ?? '-'}</div>

        {!catalog?.sources || catalog.sources.length === 0 ? (
          <EmptyState
            title="No Data Sources"
            message="No data sources available. Check connectivity."
            action={{ label: 'Refresh', onClick: loadCatalog }}
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {catalog.sources.map(source => (
              <div key={source.source} className="rounded-lg border border-gray-700 bg-gray-800/40 p-4">
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-white">{source.source}</h2>
                  {(() => {
                    const cfg = DATA_SOURCE_STATUS_DISPLAY[source.status as DataSourceStatusValue] ?? { label: source.status, bgClass: 'bg-gray-700', textClass: 'text-gray-400' }
                    return (
                      <span className={clsx('rounded px-2 py-1 text-xs font-medium', cfg.bgClass, cfg.textClass)}>
                        {cfg.label}
                      </span>
                    )
                  })()}
                </div>
                <div className="space-y-2 text-xs text-accent-3">
                  <div>symbols: <span className="text-gray-200">{source.symbols.join(', ') || '-'}</span></div>
                  <div>quality: <span className="text-gray-200">{Math.round(source.quality_score * 100)}%</span></div>
                  <div>feature: <span className="text-gray-200">{source.feature_version}</span></div>
                  {source.notes && <div className="rounded bg-yellow-950/20 px-2 py-1 text-yellow-300">{source.notes}</div>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
