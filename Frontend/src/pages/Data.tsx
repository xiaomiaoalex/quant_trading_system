import { useEffect, useMemo, useState } from 'react'
import { clsx } from 'clsx'
import { researchAPI } from '@/api'
import { ErrorState, LoadingState, EmptyState } from '@/components/ui'
import { PageHeader } from '@/components/layout'
import { formatAPIError } from '@/api/client'
import type { DataCatalogResponse, DataSourceStatus, OHLCVBarInput } from '@/types'
import { DATA_SOURCE_STATUS_DISPLAY, type DataSourceStatusValue } from '@/types/research'

const DEFAULT_BARS = JSON.stringify(
  [
    { ts_ms: 1704067200000, open: 100, high: 101.5, low: 99.5, close: 101, volume: 1000 },
    { ts_ms: 1704070800000, open: 101, high: 102.5, low: 100.5, close: 102, volume: 1008 },
    { ts_ms: 1704074400000, open: 102, high: 103.5, low: 101.5, close: 103, volume: 1012 },
  ],
  null,
  2,
)

function formatTs(ts?: number | null) {
  if (!ts) return '-'
  return new Date(ts).toLocaleString()
}

export function Data() {
  const [catalog, setCatalog] = useState<DataCatalogResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [importError, setImportError] = useState<string | null>(null)
  const [importMessage, setImportMessage] = useState<string | null>(null)
  const [isImporting, setIsImporting] = useState(false)
  const [importForm, setImportForm] = useState({
    symbol: 'BTCUSDT',
    feature_version: 'research_v1',
    interval: '1h',
    source: 'manual_import',
    requested_by: 'console_user',
    barsJson: DEFAULT_BARS,
  })

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

  const ohlcvSources = useMemo(
    () => (catalog?.sources ?? []).filter(source => source.source === 'feature_store_ohlcv'),
    [catalog],
  )
  const otherSources = useMemo(
    () => (catalog?.sources ?? []).filter(source => source.source !== 'feature_store_ohlcv'),
    [catalog],
  )

  const handleImport = async () => {
    setImportError(null)
    setImportMessage(null)
    setIsImporting(true)
    try {
      const parsed = JSON.parse(importForm.barsJson) as OHLCVBarInput[]
      if (!Array.isArray(parsed)) throw new Error('bars must be a JSON array')
      const result = await researchAPI.importOHLCV({
        symbol: importForm.symbol,
        feature_version: importForm.feature_version,
        interval: importForm.interval,
        source: importForm.source,
        requested_by: importForm.requested_by,
        bars: parsed,
      })
      setImportMessage(`Imported ${result.imported}, duplicates ${result.duplicates}.`)
      await loadCatalog()
    } catch (e) {
      setImportError(formatAPIError(e))
    } finally {
      setIsImporting(false)
    }
  }

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

        <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
          <h2 className="mb-4 text-base font-semibold text-white">Import OHLCV</h2>
          {(importError || importMessage) && (
            <div
              className={clsx(
                'mb-4 rounded p-2 text-sm',
                importError ? 'bg-red-950/20 text-red-400' : 'bg-green-950/20 text-green-400',
              )}
            >
              {importError ?? importMessage}
            </div>
          )}
          <div className="mb-3 grid gap-3 md:grid-cols-5">
            <input
              value={importForm.symbol}
              onChange={(e) => setImportForm({ ...importForm, symbol: e.target.value.toUpperCase() })}
              className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
              aria-label="Symbol"
            />
            <input
              value={importForm.feature_version}
              onChange={(e) => setImportForm({ ...importForm, feature_version: e.target.value })}
              className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
              aria-label="Feature version"
            />
            <input
              value={importForm.interval}
              onChange={(e) => setImportForm({ ...importForm, interval: e.target.value })}
              className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
              aria-label="Interval"
            />
            <input
              value={importForm.source}
              onChange={(e) => setImportForm({ ...importForm, source: e.target.value })}
              className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
              aria-label="Source"
            />
            <input
              value={importForm.requested_by}
              onChange={(e) => setImportForm({ ...importForm, requested_by: e.target.value })}
              className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
              aria-label="Requested by"
            />
          </div>
          <textarea
            value={importForm.barsJson}
            onChange={(e) => setImportForm({ ...importForm, barsJson: e.target.value })}
            className="mb-3 h-44 w-full rounded border border-gray-700 bg-gray-950 p-3 font-mono text-xs text-gray-200"
            spellCheck={false}
            aria-label="OHLCV bars JSON"
          />
          <button
            onClick={handleImport}
            disabled={isImporting}
            className="rounded bg-blue-900/40 px-3 py-1.5 text-xs text-blue-200 hover:bg-blue-900/60 disabled:opacity-60"
          >
            {isImporting ? 'Importing...' : 'Import'}
          </button>
        </div>

        {ohlcvSources.length === 0 ? (
          <EmptyState
            title="No FeatureStore OHLCV"
            message="No versioned OHLCV coverage is available."
            action={{ label: 'Refresh', onClick: loadCatalog }}
          />
        ) : (
          <div className="rounded-lg border border-gray-700 bg-gray-800/50 overflow-hidden">
            <div className="border-b border-gray-700 px-4 py-3">
              <h2 className="text-sm font-semibold text-white">FeatureStore OHLCV Coverage</h2>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-700">
                <thead className="bg-gray-800">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">Symbol</th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">Version</th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">Points</th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">Quality</th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">First</th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">Latest</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-700">
                  {ohlcvSources.map((source, index) => (
                    <tr key={`${source.source}:${source.feature_version}:${source.symbols.join(',')}:${index}`} className="table-row-hover">
                      <td className="px-4 py-3 text-sm text-gray-300">{source.symbols.join(', ') || '-'}</td>
                      <td className="px-4 py-3 text-sm text-gray-300">{source.feature_version}</td>
                      <td className="px-4 py-3 text-sm text-gray-300">{source.total_points ?? '-'}</td>
                      <td className="px-4 py-3 text-sm text-gray-300">{Math.round(source.quality_score * 100)}%</td>
                      <td className="px-4 py-3 text-sm text-gray-400">{formatTs(source.first_ts_ms)}</td>
                      <td className="px-4 py-3 text-sm text-gray-400">{formatTs(source.latest_ts_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {otherSources.map((source: DataSourceStatus) => {
            const cfg = DATA_SOURCE_STATUS_DISPLAY[source.status as DataSourceStatusValue] ?? { label: source.status, bgClass: 'bg-gray-700', textClass: 'text-gray-400' }
            return (
              <div key={`${source.source}:${source.feature_version}`} className="rounded-lg border border-gray-700 bg-gray-800/40 p-4">
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-white">{source.source}</h2>
                  <span className={clsx('rounded px-2 py-1 text-xs font-medium', cfg.bgClass, cfg.textClass)}>
                    {cfg.label}
                  </span>
                </div>
                <div className="space-y-2 text-xs text-accent-3">
                  <div>symbols: <span className="text-gray-200">{source.symbols.join(', ') || '-'}</span></div>
                  <div>quality: <span className="text-gray-200">{Math.round(source.quality_score * 100)}%</span></div>
                  <div>feature: <span className="text-gray-200">{source.feature_version}</span></div>
                  {source.latest_ts_ms && <div>latest: <span className="text-gray-200">{formatTs(source.latest_ts_ms)}</span></div>}
                  {source.notes && <div className="rounded bg-yellow-950/20 px-2 py-1 text-yellow-300">{source.notes}</div>}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
