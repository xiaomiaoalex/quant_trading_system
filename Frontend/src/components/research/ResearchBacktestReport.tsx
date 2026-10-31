import { clsx } from 'clsx'
import type { ReactNode } from 'react'
import { ResearchRunStatusBadge } from './ResearchRunStatusBadge'
import type {
  ResearchBacktestReport as ResearchBacktestReportData,
  ResearchBacktestRun,
  ResearchDatasetSummary,
} from '@/types'
import { formatCoverageMs, formatRunTimestamp } from '@/utils/researchBacktest'

interface ResearchBacktestReportProps {
  run: ResearchBacktestRun
  /** 数据集目录，用于补充报告外的月份 / 归档 SHA 信息。 */
  datasetCatalog?: ResearchDatasetSummary[]
}

function text(value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function formatCountMap(map: Record<string, number> | undefined | null): string {
  if (!map) return '-'
  const entries = Object.entries(map)
  if (entries.length === 0) return '-'
  return entries.map(([code, count]) => `${code}: ${count}`).join(', ')
}

function formatParams(params: Record<string, unknown> | undefined): string {
  if (!params || Object.keys(params).length === 0) return '{}'
  return JSON.stringify(params)
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800/40 p-4">
      <h3 className="mb-3 text-sm font-semibold text-white">{title}</h3>
      <div className="space-y-2 text-xs text-gray-300">{children}</div>
    </section>
  )
}

function Field({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-gray-500">{label}</span>
      <span className={clsx('text-sm text-gray-200 break-all', mono && 'font-mono text-xs')}>
        {value}
      </span>
    </div>
  )
}

function DatasetCards({
  report,
  catalogById,
}: {
  report: ResearchBacktestReportData
  catalogById: Map<string, ResearchDatasetSummary>
}) {
  return (
    <div className="space-y-3">
      {report.datasets.map(dataset => (
        <div
          key={dataset.dataset_id}
          className="rounded border border-gray-700 bg-gray-900/50 p-3"
        >
          <div className="mb-2 font-mono text-xs text-cyan-300 break-all">
            {dataset.dataset_id}
          </div>
          <dl className="grid gap-3 md:grid-cols-3">
            <Field label="币对" value={text(dataset.identity.symbol)} />
            <Field label="身份月份" value={catalogById.get(dataset.dataset_id)?.month ?? '-'} />
            <Field label="周期" value={text(dataset.identity.interval)} />
            <Field
              label="市场 / 交易所"
              value={`${text(dataset.identity.market_type)} / ${text(dataset.identity.venue)}`}
            />
            <Field
              label="基础 / 计价"
              value={`${text(dataset.identity.base_asset)} / ${text(dataset.identity.quote_asset)}`}
            />
            <Field label="版本" value={text(dataset.identity.version)} mono />
            <Field
              label="月覆盖"
              value={formatCoverageMs(
                numberOrNull(dataset.quality.coverage_start_ms),
                numberOrNull(dataset.quality.coverage_end_ms),
              )}
            />
            <Field label="Bar 数" value={text(dataset.quality.bar_count)} />
            <Field
              label="完整性"
              value={dataset.quality.complete === true ? '完整' : text(dataset.quality.complete)}
            />
          </dl>
        </div>
      ))}
    </div>
  )
}

function SourceCards({
  report,
  catalogById,
}: {
  report: ResearchBacktestReportData
  catalogById: Map<string, ResearchDatasetSummary>
}) {
  return (
    <div className="space-y-3">
      {report.datasets.map(dataset => {
        const catalog = catalogById.get(dataset.dataset_id)
        return (
          <div
            key={dataset.dataset_id}
            className="rounded border border-gray-700 bg-gray-900/50 p-3 space-y-2"
          >
            <div className="font-mono text-xs text-cyan-300 break-all">
              {dataset.dataset_id}
            </div>
            <dl className="grid gap-3 md:grid-cols-2">
              <Field label="来源" value={text(dataset.identity.source)} />
              <Field label="来源 URL" value={catalog?.source_url ?? '-'} mono />
              <Field label="archive_sha256" value={catalog?.archive_sha256 ?? '-'} mono />
              <Field label="official_sha256" value={catalog?.official_sha256 ?? '-'} mono />
              <Field
                label="provenance_hash"
                value={report.provenance[dataset.dataset_id] ?? catalog?.provenance_hash ?? '-'}
                mono
              />
              <Field label="checksum URL" value={catalog?.checksum_url ?? '-'} mono />
            </dl>
          </div>
        )
      })}
    </div>
  )
}

function QualityCards({ report }: { report: ResearchBacktestReportData }) {
  return (
    <div className="space-y-2">
      {report.datasets.map(dataset => {
        const entries = Object.entries(dataset.quality)
        return (
          <div key={dataset.dataset_id} className="rounded border border-gray-700 bg-gray-900/50 p-3">
            <div className="mb-2 font-mono text-xs text-gray-400 break-all">
              {dataset.dataset_id}
            </div>
            {entries.length === 0 ? (
              <div className="text-gray-500">后端未返回质量明细</div>
            ) : (
              <dl className="grid gap-2 md:grid-cols-3">
                {entries.map(([key, value]) => (
                  <Field key={key} label={key} value={text(value)} />
                ))}
              </dl>
            )}
          </div>
        )
      })}
    </div>
  )
}

export function ResearchBacktestReport({ run, datasetCatalog = [] }: ResearchBacktestReportProps) {
  const catalogById = new Map(datasetCatalog.map(dataset => [dataset.dataset_id, dataset]))
  const result = run.result ?? null
  const report = result?.report ?? null

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-white">运行报告</h2>
            <div className="mt-1 font-mono text-xs text-cyan-300 break-all">{run.run_id}</div>
          </div>
          <div className="flex items-center gap-2">
            <ResearchRunStatusBadge status={run.status} />
            <span className="rounded bg-gray-700 px-2 py-1 text-xs text-gray-300">
              model: {run.model}
            </span>
            {result?.admissible === true && (
              <span className="rounded bg-emerald-950/40 px-2 py-1 text-xs text-emerald-300">
                admissible
              </span>
            )}
            {result?.admissible === false && (
              <span className="rounded bg-yellow-950/40 px-2 py-1 text-xs text-yellow-300">
                不可作为准入证据
              </span>
            )}
          </div>
        </div>
        <dl className="mt-3 grid gap-3 md:grid-cols-4">
          <Field label="created_at" value={formatRunTimestamp(run.created_at)} />
          <Field label="updated_at" value={formatRunTimestamp(run.updated_at)} />
          <Field label="engine" value={result?.engine ?? report?.runtime.engine ?? '-'} />
          <Field
            label="runtime_version"
            value={result?.runtime_version ?? report?.runtime.runtime_version ?? '-'}
          />
        </dl>
      </div>

      {run.status === 'QUEUED' && (
        <div className="rounded-lg border border-gray-700 bg-gray-800/40 p-4 text-sm text-gray-300">
          运行已排队，等待 worker 领取。页面会自动刷新，直到进入终态。
        </div>
      )}

      {run.status === 'RUNNING' && (
        <div className="rounded-lg border border-blue-900/40 bg-blue-950/20 p-4 text-sm text-blue-200">
          运行中：正在按事件时间回放冻结数据集，完成前不会展示报告。
        </div>
      )}

      {run.status === 'CANCELLED' && (
        <div className="rounded-lg border border-gray-700 bg-gray-800/40 p-4 text-sm text-gray-300">
          运行已取消，不产生报告。
        </div>
      )}

      {run.status === 'FAILED' && (
        <div
          role="alert"
          className="rounded-lg border border-red-900/50 bg-red-950/20 p-4 text-sm text-red-300"
        >
          <div className="font-medium">运行失败</div>
          <div className="mt-1 break-all font-mono text-xs">{run.error ?? '后端未返回错误原因'}</div>
          <div className="mt-2 text-xs text-red-400/80">
            可在运行列表中点击「重试」重新排队；冻结输入保持不变，非 FAILED 运行不允许重试。
          </div>
        </div>
      )}

      {run.status === 'SUCCEEDED' && !report && (
        <div className="rounded-lg border border-yellow-900/40 bg-yellow-950/20 p-4 text-sm text-yellow-200">
          该运行缺少事件回测报告（legacy 结果不能作为准入证据）。
          {result && (
            <div className="mt-2 grid gap-2 text-xs md:grid-cols-4">
              <Field label="net_value" value={result.net_value ?? '-'} />
              <Field label="total_return" value={result.total_return ?? '-'} />
              <Field label="max_drawdown_pct" value={result.max_drawdown_pct ?? '-'} />
              <Field label="win_rate" value={result.win_rate ?? '-'} />
            </div>
          )}
        </div>
      )}

      {report && (
        <div className="space-y-4">
          <Section title="数据集身份与月覆盖">
            <DatasetCards report={report} catalogById={catalogById} />
          </Section>

          <Section title="来源与 SHA">
            <SourceCards report={report} catalogById={catalogById} />
          </Section>

          <Section title="数据质量">
            <QualityCards report={report} />
          </Section>

          <Section title="策略（源码 hash 冻结）">
            <dl className="grid gap-3 md:grid-cols-2">
              <Field label="module_path" value={report.strategy.module_path} mono />
              <Field label="strategy.version" value={text(report.strategy.version)} />
              <Field
                label="code_hash"
                value={report.strategy.code_hash ?? '-'}
                mono
              />
              <Field label="params" value={formatParams(report.strategy.params)} mono />
            </dl>
          </Section>

          <Section title="Runtime 版本与 manifest">
            <dl className="grid gap-3 md:grid-cols-2">
              <Field label="engine" value={report.runtime.engine} />
              <Field label="runtime_version" value={report.runtime.runtime_version} mono />
              <Field label="manifest_hash" value={report.runtime.manifest_hash} mono />
              <Field
                label="runtime_manifest_hash"
                value={report.runtime.runtime_manifest_hash}
                mono
              />
            </dl>
          </Section>

          <Section title="成本">
            <dl className="grid gap-3 md:grid-cols-4">
              <Field label="initial_cash" value={report.costs.initial_cash} />
              <Field label="fee_bps" value={report.costs.fee_bps} />
              <Field label="slippage_bps" value={report.costs.slippage_bps} />
              <Field label="total_fees" value={report.costs.total_fees} />
            </dl>
          </Section>

          <Section title="收益与回撤">
            <dl className="grid gap-3 md:grid-cols-5">
              <Field label="net_value" value={report.performance.net_value} />
              <Field label="total_return" value={report.performance.total_return} />
              <Field label="max_drawdown_pct" value={report.performance.max_drawdown_pct} />
              <Field
                label="max_drawdown_amount"
                value={report.performance.max_drawdown_amount}
              />
              <Field label="win_rate" value={report.performance.win_rate} />
            </dl>
            {result?.equity_points && result.equity_points.length > 0 && (
              <div className="text-xs text-gray-400">
                权益点数量：{result.equity_points.length}（首点{' '}
                {result.equity_points[0]?.equity ?? '-'} @ {result.equity_points[0]?.at_ms ?? '-'}）
              </div>
            )}
          </Section>

          <Section title="成交与拒绝统计">
            <dl className="grid gap-3 md:grid-cols-2">
              <Field label="fill_count" value={report.executions.fill_count} />
              <Field label="closed_trades" value={result?.closed_trades?.length ?? '-'} />
              <Field
                label="rejections"
                value={formatCountMap(report.executions.rejections)}
                mono
              />
              <Field
                label="execution_rejections"
                value={formatCountMap(report.executions.execution_rejections)}
                mono
              />
            </dl>
            {result?.errors && result.errors.length > 0 && (
              <div className="text-xs text-red-300">
                errors: {result.errors.join('; ')}
              </div>
            )}
          </Section>

          <Section title="限制说明">
            {report.limitations.length === 0 && (!result?.limitations || result.limitations.length === 0) ? (
              <div className="text-gray-500">后端未返回限制说明</div>
            ) : (
              <ul className="list-disc space-y-1 pl-4">
                {report.limitations.map(item => (
                  <li key={item}>{item}</li>
                ))}
                {(result?.limitations ?? [])
                  .filter(item => !report.limitations.includes(item))
                  .map(item => (
                    <li key={item}>{item}</li>
                  ))}
              </ul>
            )}
          </Section>
        </div>
      )}
    </div>
  )
}
