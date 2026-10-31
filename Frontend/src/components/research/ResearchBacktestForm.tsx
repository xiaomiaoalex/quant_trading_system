import { useMemo, useState, type FormEvent } from 'react'
import { clsx } from 'clsx'
import type {
  ResearchBacktestCreateRequest,
  ResearchDatasetSummary,
  ResearchStrategySummary,
} from '@/types'
import {
  DEFAULT_FEE_BPS,
  DEFAULT_INITIAL_CASH,
  DEFAULT_SLIPPAGE_BPS,
  buildResearchBacktestRequest,
  researchDatasetLabel,
  validateResearchBacktestInputs,
} from '@/utils/researchBacktest'

interface ResearchBacktestFormProps {
  datasets: ResearchDatasetSummary[]
  strategies: ResearchStrategySummary[]
  datasetsLoading: boolean
  datasetsError: string | null
  strategiesLoading: boolean
  strategiesError: string | null
  isSubmitting: boolean
  submitError: string | null
  onSubmit: (request: ResearchBacktestCreateRequest) => void
  initialDatasetId?: string | null
}

function inputClass() {
  return 'rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200'
}

export function ResearchBacktestForm({
  datasets,
  strategies,
  datasetsLoading,
  datasetsError,
  strategiesLoading,
  strategiesError,
  isSubmitting,
  submitError,
  onSubmit,
  initialDatasetId,
}: ResearchBacktestFormProps) {
  const [datasetId, setDatasetId] = useState(initialDatasetId ?? '')
  const [modulePath, setModulePath] = useState('')
  const [initialCash, setInitialCash] = useState(DEFAULT_INITIAL_CASH)
  const [feeBps, setFeeBps] = useState(DEFAULT_FEE_BPS)
  const [slippageBps, setSlippageBps] = useState(DEFAULT_SLIPPAGE_BPS)

  const selectedDataset = datasets.find(dataset => dataset.dataset_id === datasetId) ?? null
  const selectedStrategy =
    strategies.find(strategy => strategy.module_path === modulePath) ?? null

  const blockedReasons = useMemo(() => {
    const reasons: string[] = []

    if (datasetsLoading) {
      reasons.push('正在加载 Research 数据集目录，暂不能确认官方资格…')
    } else if (datasetsError) {
      reasons.push(`数据集资格查询失败，无法确认官方资格：${datasetsError}`)
    } else if (!datasetId) {
      reasons.push('请选择一个 Research 数据集')
    } else if (!selectedDataset) {
      reasons.push('所选数据集不在当前目录中，请刷新后重试')
    } else if (!selectedDataset.official_eligible) {
      reasons.push(
        `数据集 ${selectedDataset.dataset_id} 不具备官方资格（official_eligible=false），已禁止提交`,
      )
    }

    if (strategiesLoading) {
      reasons.push('正在加载已安装策略列表…')
    } else if (strategiesError) {
      reasons.push(`已安装策略查询失败：${strategiesError}`)
    } else if (!modulePath) {
      reasons.push('请选择一个已安装策略')
    } else if (!selectedStrategy) {
      reasons.push('所选策略不在已安装列表中，请刷新后重试')
    }

    reasons.push(
      ...validateResearchBacktestInputs({
        datasetId,
        modulePath,
        initialCash,
        feeBps,
        slippageBps,
      }),
    )

    return reasons
  }, [
    datasetsLoading,
    datasetsError,
    datasetId,
    selectedDataset,
    strategiesLoading,
    strategiesError,
    modulePath,
    selectedStrategy,
    initialCash,
    feeBps,
    slippageBps,
  ])

  const isBlocked = blockedReasons.length > 0

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isBlocked || isSubmitting) return
    onSubmit(
      buildResearchBacktestRequest({
        datasetId,
        modulePath,
        initialCash,
        feeBps,
        slippageBps,
      }),
    )
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-lg border border-gray-700 bg-gray-800/50 p-4 space-y-4"
      aria-label="Research event backtest form"
    >
      <div>
        <h2 className="text-sm font-semibold text-white">提交事件回测</h2>
        <p className="mt-1 text-xs text-accent-3">
          单币、单月、单一数据集；engine=event_runtime，purpose=research_screening 由系统固定写入。
          策略只能从后端已安装列表中选择，不会上传或执行任何手填代码。
        </p>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <label className="flex flex-col gap-1 text-xs text-gray-400">
          Research 数据集（单币 / 单月）
          <select
            aria-label="Research dataset"
            value={datasetId}
            onChange={event => setDatasetId(event.target.value)}
            className={inputClass()}
          >
            <option value="">请选择数据集</option>
            {datasets.map(dataset => (
              <option key={dataset.dataset_id} value={dataset.dataset_id}>
                {researchDatasetLabel(dataset)}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-xs text-gray-400">
          已安装策略
          <select
            aria-label="Installed strategy"
            value={modulePath}
            onChange={event => setModulePath(event.target.value)}
            className={inputClass()}
          >
            <option value="">请选择策略</option>
            {strategies.map(strategy => (
              <option key={strategy.module_path} value={strategy.module_path}>
                {`${strategy.name} · ${strategy.module_path} · v${strategy.version}`}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        <label className="flex flex-col gap-1 text-xs text-gray-400">
          初始资金（USDT）
          <input
            aria-label="Initial cash"
            value={initialCash}
            onChange={event => setInitialCash(event.target.value)}
            inputMode="decimal"
            className={inputClass()}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-400">
          费率（bps）
          <input
            aria-label="Fee bps"
            value={feeBps}
            onChange={event => setFeeBps(event.target.value)}
            inputMode="decimal"
            className={inputClass()}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-400">
          滑点（bps）
          <input
            aria-label="Slippage bps"
            value={slippageBps}
            onChange={event => setSlippageBps(event.target.value)}
            inputMode="decimal"
            className={inputClass()}
          />
        </label>
      </div>

      {selectedDataset && (
        <div className="rounded border border-gray-700 bg-gray-900/50 p-3 text-xs text-gray-400">
          <div>
            数据集：<span className="font-mono text-gray-200">{selectedDataset.dataset_id}</span>
          </div>
          <div>
            身份：{selectedDataset.symbol ?? '-'} {selectedDataset.month ?? '-'}{' '}
            {selectedDataset.interval ?? '-'} · {selectedDataset.market_type ?? '-'} ·{' '}
            {selectedDataset.venue ?? '-'}
          </div>
          <div>
            官方资格：
            {selectedDataset.official_eligible ? (
              <span className="text-emerald-300">official_eligible=true</span>
            ) : (
              <span className="text-red-300">official_eligible=false（禁止提交）</span>
            )}
          </div>
        </div>
      )}

      {selectedStrategy && selectedStrategy.params.length > 0 && (
        <div className="rounded border border-gray-700 bg-gray-900/50 p-3 text-xs text-gray-400">
          <div className="mb-1 text-gray-300">策略参数（使用安装默认值，只读）</div>
          <div className="flex flex-wrap gap-3">
            {selectedStrategy.params.map(param => (
              <span key={param.name} className="font-mono text-gray-400">
                {param.name}={param.default ?? '(必填)'}
              </span>
            ))}
          </div>
          <div className="mt-1 text-gray-500">
            freeze code_hash: <span className="font-mono">{selectedStrategy.code_hash}</span>
          </div>
        </div>
      )}

      {isBlocked && (
        <div
          role="alert"
          className="rounded border border-red-900/50 bg-red-950/20 p-3 text-xs text-red-300"
        >
          <div className="mb-1 font-medium">无法提交（阻塞原因）</div>
          <ul className="list-disc space-y-1 pl-4">
            {blockedReasons.map(reason => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}

      {submitError && (
        <div className="rounded border border-red-900/50 bg-red-950/20 p-2 text-sm text-red-300">
          提交失败：{submitError}
        </div>
      )}

      <button
        type="submit"
        disabled={isBlocked || isSubmitting}
        className={clsx(
          'rounded px-3 py-2 text-sm font-medium',
          isBlocked || isSubmitting
            ? 'cursor-not-allowed bg-gray-700 text-gray-400'
            : 'bg-blue-900/40 text-blue-200 hover:bg-blue-900/60',
        )}
      >
        {isSubmitting ? '提交中…' : '提交事件回测'}
      </button>
    </form>
  )
}
