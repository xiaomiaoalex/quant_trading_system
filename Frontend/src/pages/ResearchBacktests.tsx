import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { clsx } from 'clsx'
import { PageHeader } from '@/components/layout'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui'
import {
  ResearchBacktestForm,
  ResearchBacktestReport,
  ResearchBacktestRunList,
} from '@/components/research'
import {
  useResearchBacktestRun,
  useResearchBacktestRuns,
  useResearchDatasets,
  useResearchStrategies,
  useRetryResearchBacktest,
  useSubmitResearchBacktest,
} from '@/hooks'
import { formatAPIError } from '@/api/client'
import type { ResearchBacktestCreateRequest } from '@/types'

interface Banner {
  kind: 'success' | 'error'
  text: string
}

/**
 * `/research/backtests`：选择 Research 数据集 + 已安装策略 + 资金/费用/滑点，
 * 提交事件回测并查看状态与报告；FAILED 运行可显式重试。
 *
 * 选中的 run_id 写入 URL（`?run_id=...`），刷新页面后会按 run_id 重新查询，
 * 正在运行或已完成的历史运行都能恢复。
 */
export function ResearchBacktests() {
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedRunId = searchParams.get('run_id')
  const [banner, setBanner] = useState<Banner | null>(null)
  const [retryingRunId, setRetryingRunId] = useState<string | null>(null)

  const datasetsQuery = useResearchDatasets({ query: { limit: 200 } })
  const strategiesQuery = useResearchStrategies()
  const runsQuery = useResearchBacktestRuns({ limit: 100 })
  const runQuery = useResearchBacktestRun(selectedRunId)
  const submitMutation = useSubmitResearchBacktest()
  const retryMutation = useRetryResearchBacktest()

  const selectRun = (runId: string) => {
    const next = new URLSearchParams(searchParams)
    next.set('run_id', runId)
    setSearchParams(next, { replace: true })
  }

  const handleSubmit = async (request: ResearchBacktestCreateRequest) => {
    setBanner(null)
    const run = await submitMutation.submit(request)
    if (!run) {
      setBanner({ kind: 'error', text: submitMutation.error ?? '提交事件回测失败' })
      return
    }
    setBanner({ kind: 'success', text: `已提交事件回测：${run.run_id}` })
    selectRun(run.run_id)
  }

  const handleRetry = async (runId: string) => {
    setBanner(null)
    setRetryingRunId(runId)
    const run = await retryMutation.retry(runId)
    setRetryingRunId(null)
    if (!run) {
      setBanner({ kind: 'error', text: retryMutation.error ?? '重试失败' })
      return
    }
    setBanner({ kind: 'success', text: `已重新排队：${run.run_id}` })
    selectRun(run.run_id)
  }

  return (
    <div className="min-h-screen bg-gray-900">
      <PageHeader title="Research Backtests">
        <button
          type="button"
          onClick={() => {
            void datasetsQuery.refetch()
            void runsQuery.refetch()
          }}
          className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700"
        >
          刷新
        </button>
      </PageHeader>

      <div className="p-6 space-y-6">
        {banner && (
          <div
            role={banner.kind === 'error' ? 'alert' : 'status'}
            className={clsx(
              'rounded p-2 text-sm',
              banner.kind === 'error'
                ? 'bg-red-950/20 text-red-400'
                : 'bg-emerald-950/20 text-emerald-300',
            )}
          >
            {banner.text}
          </div>
        )}

        <div className="grid gap-6 xl:grid-cols-2">
          <ResearchBacktestForm
            datasets={datasetsQuery.data ?? []}
            strategies={strategiesQuery.data ?? []}
            datasetsLoading={datasetsQuery.isLoading}
            datasetsError={
              datasetsQuery.isError ? formatAPIError(datasetsQuery.error) : null
            }
            strategiesLoading={strategiesQuery.isLoading}
            strategiesError={
              strategiesQuery.isError ? formatAPIError(strategiesQuery.error) : null
            }
            isSubmitting={submitMutation.isPending}
            submitError={submitMutation.error}
            onSubmit={request => void handleSubmit(request)}
          />

          <ResearchBacktestRunList
            runs={runsQuery.data ?? []}
            selectedRunId={selectedRunId}
            onSelect={selectRun}
            onRetry={runId => void handleRetry(runId)}
            retryingRunId={retryingRunId}
            isLoading={runsQuery.isLoading}
            isError={runsQuery.isError}
            errorMessage={runsQuery.isError ? formatAPIError(runsQuery.error) : null}
            onReload={() => void runsQuery.refetch()}
            isFetching={runsQuery.isFetching}
            lastUpdatedAt={runsQuery.dataUpdatedAt || undefined}
          />
        </div>

        <div className="rounded-lg border border-gray-700 bg-gray-800/40 p-4">
          {!selectedRunId ? (
            <EmptyState
              title="未选择运行"
              message="在运行列表中选择一条记录查看状态与报告；run_id 会写入 URL，刷新后自动按 run_id 重新查询。"
            />
          ) : runQuery.isLoading ? (
            <LoadingState message="Loading Research backtest run..." />
          ) : runQuery.isError && !runQuery.data ? (
            <ErrorState
              title="加载运行详情失败"
              message={formatAPIError(runQuery.error)}
              onRetry={() => void runQuery.refetch()}
            />
          ) : runQuery.data ? (
            <>
              {runQuery.isError && (
                <div className="mb-3 rounded bg-yellow-950/20 p-2 text-xs text-yellow-300">
                  运行详情刷新失败，展示的是可能已过期的缓存数据：
                  {formatAPIError(runQuery.error)}
                </div>
              )}
              <ResearchBacktestReport
                run={runQuery.data}
                datasetCatalog={datasetsQuery.data ?? []}
              />
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}
