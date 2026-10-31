import { clsx } from 'clsx'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui'
import type { ResearchDatasetSummary } from '@/types'
import { formatCoverageMs } from '@/utils/researchBacktest'

interface ResearchDatasetCatalogProps {
  datasets: ResearchDatasetSummary[]
  isLoading: boolean
  isError: boolean
  errorMessage: string | null
  onRetry: () => void
  isFetching?: boolean
  lastUpdatedAt?: number
}

function eligibilityReason(dataset: ResearchDatasetSummary): string {
  if (dataset.official_eligible) return '已关联官方归档并通过资格校验'
  if (!dataset.history_dataset_id) return '未关联历史归档（尚未由归档任务发布）'
  if (!dataset.complete) return '数据不完整（质量校验未通过）'
  return '后端未标记官方资格（official_eligible=false）'
}

function shortSha(value: string | null): string {
  if (!value) return '-'
  return value.length > 16 ? `${value.slice(0, 16)}…` : value
}

export function ResearchDatasetCatalog({
  datasets,
  isLoading,
  isError,
  errorMessage,
  onRetry,
  isFetching,
  lastUpdatedAt,
}: ResearchDatasetCatalogProps) {
  return (
    <div className="rounded-lg border border-gray-700 bg-gray-800/50 overflow-hidden">
      <div className="flex items-center justify-between border-b border-gray-700 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-white">Research 数据集目录</h2>
          <p className="mt-0.5 text-xs text-accent-3">
            只读元数据；只有 official_eligible=true 的数据集允许提交事件回测。
          </p>
        </div>
        <div className="text-right text-xs text-accent-3">
          {isFetching && <div>刷新中…</div>}
          {lastUpdatedAt ? (
            <div>更新于 {new Date(lastUpdatedAt).toLocaleTimeString()}</div>
          ) : null}
        </div>
      </div>

      {isLoading ? (
        <div className="p-4">
          <LoadingState message="Loading Research datasets..." />
        </div>
      ) : isError && datasets.length === 0 ? (
        <div className="p-4">
          <ErrorState
            title="加载 Research 数据集失败"
            message={errorMessage}
            onRetry={onRetry}
          />
        </div>
      ) : datasets.length === 0 ? (
        <EmptyState
          title="暂无 Research 数据集"
          message="请先在下方「历史归档导入」中下载、校验并发布 Binance 官方整月数据。"
        />
      ) : (
        <>
          {isError && (
            <div className="mx-4 mt-4 rounded bg-yellow-950/20 p-2 text-xs text-yellow-300">
              数据集目录刷新失败，当前展示的是可能已过期的缓存数据：{errorMessage}
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-700">
              <thead className="bg-gray-800">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">数据集</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">币对</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">月份</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">周期</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">覆盖范围 (UTC)</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">条数</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">完整性</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">官方资格</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">来源 / SHA</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">发布</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {datasets.map(dataset => (
                  <tr key={dataset.dataset_id} className="table-row-hover align-top">
                    <td className="px-4 py-3">
                      <div className="font-mono text-xs text-gray-300 break-all">{dataset.dataset_id}</div>
                      <div className="mt-1 text-xs text-gray-500">
                        history: {dataset.history_dataset_id ?? '-'}
                      </div>
                      <div className="text-xs text-gray-500">
                        provenance: {shortSha(dataset.provenance_hash)}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-300">{dataset.symbol ?? '-'}</td>
                    <td className="px-4 py-3 text-sm text-gray-300">{dataset.month ?? '-'}</td>
                    <td className="px-4 py-3 text-sm text-gray-300">{dataset.interval ?? '-'}</td>
                    <td className="px-4 py-3 text-xs text-gray-400">
                      {formatCoverageMs(dataset.coverage_start_ms, dataset.coverage_end_ms)}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-300">{dataset.bar_count ?? '-'}</td>
                    <td className="px-4 py-3 text-xs">
                      {dataset.complete ? (
                        <span className="text-emerald-300">完整</span>
                      ) : (
                        <span className="text-yellow-300">不完整</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs">
                      <span
                        className={clsx(
                          'rounded px-2 py-1 font-medium',
                          dataset.official_eligible
                            ? 'bg-emerald-950/40 text-emerald-300'
                            : 'bg-red-950/40 text-red-300',
                        )}
                      >
                        {dataset.official_eligible ? '官方合格' : '资格不足'}
                      </span>
                      <div className="mt-1 max-w-[16rem] text-gray-500">
                        {eligibilityReason(dataset)}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-400">
                      <div>{dataset.source ?? '-'}</div>
                      <div className="font-mono text-gray-500">
                        archive: {shortSha(dataset.archive_sha256)}
                      </div>
                      <div className="font-mono text-gray-500">
                        official: {shortSha(dataset.official_sha256)}
                      </div>
                      <div className="text-gray-500">
                        conversion: {dataset.conversion_version ?? '-'}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-400">
                      {dataset.published_at
                        ? new Date(dataset.published_at).toLocaleString()
                        : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
