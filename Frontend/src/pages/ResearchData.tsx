import { useEffect, useRef, useState } from 'react'
import { PageHeader } from '@/components/layout'
import { HistoryDataPanel, ResearchDatasetCatalog } from '@/components'
import { useHistoryJobs, useResearchDatasets } from '@/hooks'
import { formatAPIError } from '@/api/client'

const ARCHIVE_SYMBOLS = ['BTCUSDT', 'ETHUSDT']
const MONTH_PATTERN = /^\d{4}-(0[1-9]|1[0-2])$/

/**
 * `/research/data`：Research 数据集目录（只读元数据）+ 复用历史归档导入能力。
 *
 * 归档导入由既有历史归档管线完成（下载 → SHA-256 校验 → 发布），发布成功后
 * Research 数据集出现在目录中，并带有 official_eligible 标记。
 */
export function ResearchData() {
  const [symbol, setSymbol] = useState('')
  const [month, setMonth] = useState('')
  const [officialOnly, setOfficialOnly] = useState(false)

  const monthFilter = MONTH_PATTERN.test(month.trim()) ? month.trim() : undefined

  const datasetsQuery = useResearchDatasets({
    query: {
      symbol: symbol || undefined,
      month: monthFilter,
      official_only: officialOnly,
      limit: 200,
    },
  })
  const jobsQuery = useHistoryJobs()

  // 归档任务发布出 Research 数据集后刷新目录。
  const refetchDatasets = datasetsQuery.refetch
  const seenSucceeded = useRef<Set<string>>(new Set())
  useEffect(() => {
    const jobs = jobsQuery.data ?? []
    const newlyPublished = jobs.filter(
      job =>
        job.status === 'SUCCEEDED' &&
        !!job.research_dataset_id &&
        !seenSucceeded.current.has(job.job_id),
    )
    if (newlyPublished.length === 0) return
    for (const job of newlyPublished) seenSucceeded.current.add(job.job_id)
    void refetchDatasets()
  }, [jobsQuery.data, refetchDatasets])

  return (
    <div className="min-h-screen bg-gray-900">
      <PageHeader title="Research Data">
        <button
          type="button"
          onClick={() => void datasetsQuery.refetch()}
          className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700"
        >
          刷新目录
        </button>
      </PageHeader>

      <div className="p-6 space-y-6">
        <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
          <div className="flex flex-wrap items-end gap-4">
            <label className="flex flex-col gap-1 text-xs text-gray-400">
              币对
              <select
                aria-label="Catalog symbol filter"
                value={symbol}
                onChange={event => setSymbol(event.target.value)}
                className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
              >
                <option value="">全部</option>
                {ARCHIVE_SYMBOLS.map(item => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-gray-400">
              月份（YYYY-MM）
              <input
                aria-label="Catalog month filter"
                value={month}
                onChange={event => setMonth(event.target.value)}
                placeholder="2026-09"
                className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
              />
            </label>
            <label className="flex items-center gap-2 text-xs text-gray-300">
              <input
                type="checkbox"
                checked={officialOnly}
                onChange={event => setOfficialOnly(event.target.checked)}
                aria-label="Official only filter"
              />
              仅显示官方合格
            </label>
            {month.trim() !== '' && !monthFilter && (
              <span className="text-xs text-amber-300">月份格式无效，已忽略该过滤条件</span>
            )}
          </div>
        </div>

        <ResearchDatasetCatalog
          datasets={datasetsQuery.data ?? []}
          isLoading={datasetsQuery.isLoading}
          isError={datasetsQuery.isError}
          errorMessage={datasetsQuery.isError ? formatAPIError(datasetsQuery.error) : null}
          onRetry={() => void datasetsQuery.refetch()}
          isFetching={datasetsQuery.isFetching}
          lastUpdatedAt={datasetsQuery.dataUpdatedAt || undefined}
        />

        <div>
          <div className="mb-3">
            <h2 className="text-sm font-semibold text-white">历史归档导入</h2>
            <p className="mt-1 text-xs text-accent-3">
              复用既有历史归档导入能力：下载 Binance 官方整月归档 → SHA-256 校验 → 发布为 Research
              数据集（BTCUSDT / ETHUSDT · 1m）。发布完成的任务会携带 research_dataset_id。
            </p>
          </div>
          <HistoryDataPanel />
        </div>
      </div>
    </div>
  )
}
