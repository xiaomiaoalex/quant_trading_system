import { useEffect, useState } from 'react'
import { researchAPI } from '@/api'
import { ErrorState, LoadingState } from '@/components/ui'
import { formatAPIError } from '@/api/client'
import type { DataCatalogResponse } from '@/types'

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
    <div className="min-h-screen bg-gray-900 p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Data</h1>
          <p className="text-sm text-gray-400">Feature version: {catalog?.feature_version ?? '-'}</p>
        </div>
        <button onClick={loadCatalog} className="rounded-md bg-gray-800 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700">
          Refresh
        </button>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {(catalog?.sources ?? []).map(source => (
          <div key={source.source} className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-white">{source.source}</h2>
              <span className="rounded bg-gray-900 px-2 py-1 text-xs text-gray-300">{source.status}</span>
            </div>
            <div className="space-y-2 text-xs text-gray-400">
              <div>symbols: <span className="text-gray-200">{source.symbols.join(', ') || '-'}</span></div>
              <div>quality: <span className="text-gray-200">{Math.round(source.quality_score * 100)}%</span></div>
              <div>feature: <span className="text-gray-200">{source.feature_version}</span></div>
              {source.notes && <div className="text-yellow-300">{source.notes}</div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
