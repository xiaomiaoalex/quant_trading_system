import { useMemo, useState } from 'react'
import { clsx } from 'clsx'
import {
  useCreateHistoryJob,
  useHistoryDatasets,
  useHistoryJobs,
  useRetryHistoryJob,
} from '@/hooks'
import { EmptyState, ErrorState, LoadingState } from '@/components/ui'
import { formatAPIError } from '@/api/client'
import type { HistoryDataset, HistoryJob } from '@/types'

const SUPPORTED_SYMBOLS = ['BTCUSDT', 'ETHUSDT']
const DEFAULT_MONTH = '2026-09'

const JOB_STATUS_DISPLAY: Record<string, { label: string; bgClass: string; textClass: string }> = {
  QUEUED: { label: '排队中', bgClass: 'bg-gray-700', textClass: 'text-gray-300' },
  RUNNING: { label: '进行中', bgClass: 'bg-blue-950/40', textClass: 'text-blue-300' },
  SUCCEEDED: { label: '成功', bgClass: 'bg-emerald-950/40', textClass: 'text-emerald-300' },
  FAILED: { label: '失败', bgClass: 'bg-red-950/40', textClass: 'text-red-300' },
}

const ERROR_CODE_LABELS: Record<string, string> = {
  ARCHIVE_NOT_FOUND: '归档不存在（该 symbol / 月份没有公开发布的归档）',
  NETWORK_ERROR: '网络错误（下载超时或远端不可达）',
  CHECKSUM_MISMATCH: '校验失败（实际 SHA-256 与官方 CHECKSUM 不一致）',
  QUALITY_ERROR: '数据质量不达标（缺口 / 数值非法 / 覆盖不足）',
  PERSISTENCE_ERROR: '持久化失败（PostgreSQL 写入或校验未通过）',
  SERVICE_INTERRUPTED: '服务中断（上次任务未完成，重启后已标记失败）',
  UNSUPPORTED_REQUEST: '请求不被支持',
  INTERNAL_ERROR: '内部错误',
}

function formatTs(ts?: number | null | string) {
  if (!ts) return '-'
  if (typeof ts === 'string') return new Date(ts).toLocaleString()
  return new Date(ts).toLocaleString()
}

function formatBytes(bytes: number) {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

function coverageLabel(dataset: HistoryDataset) {
  if (!dataset.first_ts_ms || !dataset.last_ts_ms) return '-'
  const start = new Date(dataset.first_ts_ms).toISOString().replace('.000Z', 'Z')
  const end = new Date(dataset.last_ts_ms).toISOString().replace('.000Z', 'Z')
  return `${start} → ${end}`
}

interface HistoryDataPanelProps {
  onUseForBacktest?: (datasetId: string) => void
}

export function HistoryDataPanel({ onUseForBacktest }: HistoryDataPanelProps) {
  const [symbols, setSymbols] = useState<string[]>(SUPPORTED_SYMBOLS)
  const [month, setMonth] = useState(DEFAULT_MONTH)
  const [formError, setFormError] = useState<string | null>(null)

  const jobsQuery = useHistoryJobs()
  const datasetsQuery = useHistoryDatasets()
  const createJob = useCreateHistoryJob()
  const retryJob = useRetryHistoryJob()

  const jobs = useMemo(() => jobsQuery.data ?? [], [jobsQuery.data])
  const datasets = useMemo(() => datasetsQuery.data ?? [], [datasetsQuery.data])

  const toggleSymbol = (symbol: string) => {
    setSymbols(current =>
      current.includes(symbol)
        ? current.filter(item => item !== symbol)
        : [...current, symbol],
    )
  }

  const handleDownload = async () => {
    setFormError(null)
    if (symbols.length === 0) {
      setFormError('请至少选择一个币对')
      return
    }
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month.trim())) {
      setFormError('月份格式必须是 YYYY-MM')
      return
    }
    for (const symbol of symbols) {
      const created = await createJob.create({
        symbol,
        month: month.trim(),
        requested_by: 'console_user',
      })
      if (!created) {
        setFormError(createJob.error ?? '创建任务失败')
        return
      }
    }
  }

  const handleRetry = async (job: HistoryJob) => {
    setFormError(null)
    const retried = await retryJob.retry(job.job_id)
    if (!retried) {
      setFormError(retryJob.error ?? '重试失败')
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-white">币安现货历史数据</h2>
          <span className="text-xs text-accent-3">
            真实历史数据 · 现货 · 1 分钟 · 公开归档
          </span>
        </div>

        <div className="mb-3 flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-3">
            <span className="text-xs text-gray-400">币对：</span>
            {SUPPORTED_SYMBOLS.map(symbol => (
              <label key={symbol} className="flex items-center gap-1 text-xs text-gray-200">
                <input
                  type="checkbox"
                  checked={symbols.includes(symbol)}
                  onChange={() => toggleSymbol(symbol)}
                  aria-label={`Select ${symbol}`}
                />
                {symbol}
              </label>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400">月份：</span>
            <input
              type="text"
              value={month}
              onChange={event => setMonth(event.target.value)}
              aria-label="Archive month"
              placeholder="YYYY-MM"
              className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200"
            />
          </div>
          <button
            onClick={handleDownload}
            disabled={createJob.isPending}
            className="rounded bg-blue-900/40 px-3 py-1.5 text-xs text-blue-200 hover:bg-blue-900/60 disabled:opacity-60"
          >
            {createJob.isPending ? '提交中...' : '下载'}
          </button>
        </div>

        {formError && (
          <div className="mb-3 rounded bg-red-950/20 p-2 text-sm text-red-400">{formError}</div>
        )}

        <p className="text-xs text-accent-3">
          下载来源为币安公开归档（data.binance.vision），无需 API Key。多选币对会拆分为多个任务。
        </p>
      </div>

      <div className="rounded-lg border border-gray-700 bg-gray-800/50 overflow-hidden">
        <div className="flex items-center justify-between border-b border-gray-700 px-4 py-3">
          <h2 className="text-sm font-semibold text-white">导入任务</h2>
          {jobsQuery.isFetching && <span className="text-xs text-accent-3">刷新中...</span>}
        </div>
        {jobsQuery.isLoading ? (
          <div className="p-4">
            <LoadingState message="Loading history jobs..." />
          </div>
        ) : jobsQuery.isError ? (
          <div className="p-4">
            <ErrorState
              title="加载历史任务失败"
              message={formatAPIError(jobsQuery.error)}
              onRetry={() => void jobsQuery.refetch()}
            />
          </div>
        ) : jobs.length === 0 ? (
          <EmptyState title="暂无历史任务" message="选择币对与月份后点击「下载」开始导入。" />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-700">
              <thead className="bg-gray-800">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">币对</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">月份</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">状态</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">阶段</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">已下载</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">已处理行数</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">错误</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {jobs.map(job => {
                  const statusCfg = JOB_STATUS_DISPLAY[job.status] ?? {
                    label: job.status,
                    bgClass: 'bg-gray-700',
                    textClass: 'text-gray-300',
                  }
                  return (
                    <tr key={job.job_id} className="table-row-hover">
                      <td className="px-4 py-3 text-sm text-gray-300">{job.symbol}</td>
                      <td className="px-4 py-3 text-sm text-gray-300">{job.month}</td>
                      <td className="px-4 py-3">
                        <span className={clsx('rounded px-2 py-1 text-xs font-medium', statusCfg.bgClass, statusCfg.textClass)}>
                          {statusCfg.label}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-400">{job.stage}</td>
                      <td className="px-4 py-3 text-xs text-gray-400">{formatBytes(job.bytes_downloaded)}</td>
                      <td className="px-4 py-3 text-xs text-gray-400">{job.rows_processed}</td>
                      <td className="px-4 py-3 text-xs text-red-300">
                        {job.error_code ? (
                          <span title={job.error_reason ?? ''}>
                            {ERROR_CODE_LABELS[job.error_code] ?? job.error_code}
                          </span>
                        ) : (
                          '-'
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {job.status === 'FAILED' && (
                          <button
                            onClick={() => void handleRetry(job)}
                            disabled={retryJob.isPending}
                            className="rounded bg-amber-900/40 px-2 py-1 text-xs text-amber-200 hover:bg-amber-900/60 disabled:opacity-60"
                          >
                            重试
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="rounded-lg border border-gray-700 bg-gray-800/50 overflow-hidden">
        <div className="border-b border-gray-700 px-4 py-3">
          <h2 className="text-sm font-semibold text-white">历史数据集</h2>
        </div>
        {datasetsQuery.isLoading ? (
          <div className="p-4">
            <LoadingState message="Loading datasets..." />
          </div>
        ) : datasetsQuery.isError ? (
          <div className="p-4">
            <ErrorState
              title="加载数据集失败"
              message={formatAPIError(datasetsQuery.error)}
              onRetry={() => void datasetsQuery.refetch()}
            />
          </div>
        ) : datasets.length === 0 ? (
          <EmptyState title="暂无数据集" message="完成一次历史数据导入后，数据集会显示在这里。" />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-700">
              <thead className="bg-gray-800">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">市场</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">币对</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">周期</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">覆盖范围</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">有效条数</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">缺口</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">持久化</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">可回测</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-400">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {datasets.map(dataset => (
                  <tr key={dataset.dataset_id} className="table-row-hover">
                    <td className="px-4 py-3 text-sm text-gray-300">{dataset.market}</td>
                    <td className="px-4 py-3 text-sm text-gray-300">
                      {dataset.symbol}
                      <span className="ml-1 text-xs text-gray-500">{dataset.month}</span>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-300">{dataset.interval}</td>
                    <td className="px-4 py-3 text-xs text-gray-400">{coverageLabel(dataset)}</td>
                    <td className="px-4 py-3 text-xs text-gray-400">
                      {dataset.row_count} / {dataset.expected_points}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-400">{dataset.gap_count}</td>
                    <td className="px-4 py-3 text-xs">
                      {dataset.persistence_ok ? (
                        <span className="text-emerald-300">已持久化</span>
                      ) : (
                        <span className="text-gray-400">未持久化</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs">
                      {dataset.backtest_eligible ? (
                        <span className="text-emerald-300">可回测</span>
                      ) : (
                        <span className="text-gray-400">数据未完整</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {dataset.backtest_eligible && onUseForBacktest && (
                        <button
                          onClick={() => onUseForBacktest(dataset.dataset_id)}
                          className="rounded bg-cyan-900/40 px-2 py-1 text-xs text-cyan-200 hover:bg-cyan-900/60"
                        >
                          用于回测
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {datasets.length > 0 && (
          <details className="border-t border-gray-700 px-4 py-3 text-xs text-accent-3">
            <summary className="cursor-pointer">数据集追溯信息</summary>
            <ul className="mt-2 space-y-2">
              {datasets.map(dataset => (
                <li key={`detail-${dataset.dataset_id}`}>
                  <div className="font-mono text-gray-400">{dataset.dataset_id}</div>
                  <div>feature_version: {dataset.feature_version}</div>
                  <div>source_url: {dataset.source_url}</div>
                  <div>
                    official_sha256: {dataset.official_sha256 ?? '-'} / archive_sha256:{' '}
                    {dataset.archive_sha256}
                  </div>
                  <div>published_at: {formatTs(dataset.published_at)}</div>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </div>
  )
}
