import { clsx } from 'clsx'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui'
import { ResearchRunStatusBadge } from './ResearchRunStatusBadge'
import type { ResearchBacktestRun } from '@/types'
import {
  formatRunTimestamp,
  isActiveResearchRun,
  runDatasetIds,
  runStrategyModulePath,
} from '@/utils/researchBacktest'

interface ResearchBacktestRunListProps {
  runs: ResearchBacktestRun[]
  selectedRunId: string | null
  onSelect: (runId: string) => void
  onRetry: (runId: string) => void
  retryingRunId?: string | null
  isLoading: boolean
  isError: boolean
  errorMessage: string | null
  onReload: () => void
  isFetching?: boolean
  lastUpdatedAt?: number
}

export function ResearchBacktestRunList({
  runs,
  selectedRunId,
  onSelect,
  onRetry,
  retryingRunId,
  isLoading,
  isError,
  errorMessage,
  onReload,
  isFetching,
  lastUpdatedAt,
}: ResearchBacktestRunListProps) {
  const hasActiveRun = runs.some(isActiveResearchRun)

  return (
    <div className="rounded-lg border border-gray-700 bg-gray-800/50 overflow-hidden">
      <div className="flex items-center justify-between border-b border-gray-700 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-white">回测运行</h2>
          <p className="mt-0.5 text-xs text-accent-3">
            {runs.length === 0
              ? '暂无可展示的运行'
              : hasActiveRun
                ? '存在排队 / 运行中任务，每 2 秒自动刷新'
                : '全部运行已终态，轮询已停止'}
          </p>
        </div>
        <div className="text-right text-xs text-accent-3">
          {isFetching && <div>刷新中…</div>}
          {lastUpdatedAt ? (
            <div>更新于 {new Date(lastUpdatedAt).toLocaleTimeString()}</div>
          ) : null}
          <button
            type="button"
            onClick={onReload}
            className="mt-1 rounded bg-gray-700 px-2 py-1 text-xs text-gray-200 hover:bg-gray-600"
          >
            刷新列表
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="p-4">
          <LoadingState message="Loading Research backtest runs..." />
        </div>
      ) : isError && runs.length === 0 ? (
        <div className="p-4">
          <ErrorState title="加载回测运行失败" message={errorMessage} onRetry={onReload} />
        </div>
      ) : runs.length === 0 ? (
        <EmptyState
          title="暂无回测运行"
          message="在左侧选择官方合格的数据集与已安装策略后提交，运行记录会显示在这里。"
        />
      ) : (
        <>
          {isError && (
            <div className="mx-4 mt-4 rounded bg-yellow-950/20 p-2 text-xs text-yellow-300">
              运行列表刷新失败，当前展示的是可能已过期的缓存数据：{errorMessage}
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-700">
              <thead className="bg-gray-800">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">运行</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">状态</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">引擎</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">数据集 / 策略</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">创建 / 更新</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {runs.map(run => {
                  const datasetIds = runDatasetIds(run)
                  const modulePath = runStrategyModulePath(run)
                  const isSelected = run.run_id === selectedRunId
                  return (
                    <tr
                      key={run.run_id}
                      className={clsx(
                        'table-row-hover align-top',
                        isSelected && 'bg-gray-700/40',
                      )}
                    >
                      <td className="px-4 py-3">
                        <button
                          type="button"
                          onClick={() => onSelect(run.run_id)}
                          className="font-mono text-xs text-cyan-300 hover:underline break-all text-left"
                        >
                          {run.run_id}
                        </button>
                      </td>
                      <td className="px-4 py-3">
                        <ResearchRunStatusBadge status={run.status} />
                        {run.error && (
                          <div className="mt-1 max-w-[18rem] text-xs text-red-300">{run.error}</div>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-400">{run.model}</td>
                      <td className="px-4 py-3 text-xs text-gray-400">
                        <div className="font-mono break-all">{datasetIds.join(', ') || '-'}</div>
                        <div className="font-mono text-gray-500">{modulePath ?? '-'}</div>
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-400">
                        <div>created: {formatRunTimestamp(run.created_at)}</div>
                        <div>updated: {formatRunTimestamp(run.updated_at)}</div>
                      </td>
                      <td className="px-4 py-3 text-xs">
                        <div className="flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() => onSelect(run.run_id)}
                            className="rounded bg-cyan-900/40 px-2 py-1 text-xs text-cyan-200 hover:bg-cyan-900/60"
                          >
                            查看报告
                          </button>
                          {run.status === 'FAILED' && (
                            <button
                              type="button"
                              onClick={() => onRetry(run.run_id)}
                              disabled={retryingRunId === run.run_id}
                              className="rounded bg-amber-900/40 px-2 py-1 text-xs text-amber-200 hover:bg-amber-900/60 disabled:opacity-60"
                            >
                              {retryingRunId === run.run_id ? '重试中…' : '重试'}
                            </button>
                          )}
                        </div>
                        {run.status === 'FAILED' && (
                          <div className="mt-1 text-gray-500">仅 FAILED 运行可显式重试</div>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
