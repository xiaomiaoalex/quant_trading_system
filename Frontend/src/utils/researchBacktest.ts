import type {
  ResearchBacktestCreateRequest,
  ResearchBacktestManifest,
  ResearchBacktestStatus,
  ResearchBacktestRun,
  ResearchDatasetSummary,
} from '@/types'

/** 表单默认值：与产品约定保持一致（USDT 计价）。 */
export const DEFAULT_INITIAL_CASH = '100000'
export const DEFAULT_FEE_BPS = '10'
export const DEFAULT_SLIPPAGE_BPS = '5'

/** 事件回测固定引擎与用途：不允许用户手填。 */
export const RESEARCH_BACKTEST_ENGINE = 'event_runtime' as const
export const RESEARCH_BACKTEST_PURPOSE = 'research_screening' as const

const TERMINAL_STATUSES: ReadonlySet<ResearchBacktestStatus> = new Set([
  'SUCCEEDED',
  'FAILED',
  'CANCELLED',
])

/** 终态（SUCCEEDED / FAILED / CANCELLED）不再轮询。 */
export function isTerminalResearchStatus(status: ResearchBacktestStatus): boolean {
  return TERMINAL_STATUSES.has(status)
}

/** 仍在推进（QUEUED / RUNNING）的运行。 */
export function isActiveResearchStatus(status: ResearchBacktestStatus): boolean {
  return !isTerminalResearchStatus(status)
}

export function isActiveResearchRun(run: Pick<ResearchBacktestRun, 'status'>): boolean {
  return isActiveResearchStatus(run.status)
}

/**
 * 只有 official_eligible === true 的 Research 数据集允许提交事件回测。
 * 后端仍是最终门禁，这里只做前端防御与阻断原因展示。
 */
export function isOfficialEligibleDataset(
  dataset: Pick<ResearchDatasetSummary, 'official_eligible'> | null | undefined,
): boolean {
  return dataset?.official_eligible === true
}

const DECIMAL_TEXT = /^\d+(?:\.\d+)?$/

/** 解析非负十进制字符串；非法返回 null（后端使用 decimal 字符串契约）。 */
export function parseDecimalText(value: string): number | null {
  const trimmed = value.trim()
  if (!DECIMAL_TEXT.test(trimmed)) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

export interface ResearchBacktestInputs {
  datasetId: string
  modulePath: string
  initialCash: string
  feeBps: string
  slippageBps: string
}

/**
 * 资金 / 费用 / 滑点校验。
 * 与后端 `BacktestApplication.submit` 的边界保持一致：
 * initial_cash > 0，0 <= fee_bps <= 100，0 <= slippage_bps <= 1000。
 */
export function validateResearchBacktestInputs(inputs: ResearchBacktestInputs): string[] {
  const reasons: string[] = []

  const initialCash = parseDecimalText(inputs.initialCash)
  if (initialCash === null) {
    reasons.push('初始资金必须是十进制数字（例如 100000）')
  } else if (initialCash <= 0) {
    reasons.push('初始资金必须大于 0')
  }

  const feeBps = parseDecimalText(inputs.feeBps)
  if (feeBps === null) {
    reasons.push('费率必须是十进制数字（bps）')
  } else if (feeBps > 100) {
    reasons.push('费率不能超过 100 bps')
  }

  const slippageBps = parseDecimalText(inputs.slippageBps)
  if (slippageBps === null) {
    reasons.push('滑点必须是十进制数字（bps）')
  } else if (slippageBps > 1000) {
    reasons.push('滑点不能超过 1000 bps')
  }

  return reasons
}

/**
 * 构造提交用 manifest。
 *
 * engine / purpose 为固定常量，module_path 只能来自后端已安装策略列表，
 * 单次只提交一个 dataset_id（单币、单月）。
 */
export function buildResearchBacktestManifest(
  inputs: ResearchBacktestInputs,
): ResearchBacktestManifest {
  return {
    dataset_ids: [inputs.datasetId],
    strategy: {
      module_path: inputs.modulePath,
    },
    initial_cash: inputs.initialCash.trim(),
    fee_bps: inputs.feeBps.trim(),
    slippage_bps: inputs.slippageBps.trim(),
    engine: RESEARCH_BACKTEST_ENGINE,
    purpose: RESEARCH_BACKTEST_PURPOSE,
  }
}

export function buildResearchBacktestRequest(
  inputs: ResearchBacktestInputs,
): ResearchBacktestCreateRequest {
  return { manifest: buildResearchBacktestManifest(inputs) }
}

/** 数据集在目录里的展示名称。 */
export function researchDatasetLabel(dataset: ResearchDatasetSummary): string {
  const symbol = dataset.symbol ?? '未知币对'
  const month = dataset.month ?? '未知月份'
  const interval = dataset.interval ?? '未知周期'
  const eligibility = dataset.official_eligible ? '官方合格' : '资格不足'
  return `${symbol} ${month} ${interval} · ${eligibility} · ${dataset.dataset_id}`
}

/** 毫秒覆盖区间格式化（UTC ISO）。 */
export function formatCoverageMs(startMs: number | null, endMs: number | null): string {
  if (startMs === null && endMs === null) return '-'
  const start = startMs === null ? '-' : new Date(startMs).toISOString()
  const end = endMs === null ? '-' : new Date(endMs).toISOString()
  return `${start} → ${end}`
}

/** 运行时间戳可能是 PG ISO 字符串或内存 store 的单调数字。 */
export function formatRunTimestamp(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'number') return `t+${value.toFixed(1)}s`
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString()
}

/** 从运行记录的 manifest 中提取数据集 ID（展示用，容忍缺字段）。 */
export function runDatasetIds(run: ResearchBacktestRun): string[] {
  const ids = run.manifest?.dataset_ids
  return Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string') : []
}

/** 从运行记录的 manifest 中提取策略 module_path（展示用）。 */
export function runStrategyModulePath(run: ResearchBacktestRun): string | null {
  const strategy = run.manifest?.strategy
  if (!strategy || typeof strategy !== 'object') return null
  const modulePath = (strategy as Record<string, unknown>).module_path
  return typeof modulePath === 'string' ? modulePath : null
}
