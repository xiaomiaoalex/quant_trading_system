import { z } from 'zod'

/**
 * Research 平面运行时契约。
 *
 * 这里只描述「后端已实现」的形状；任何字段漂移都会在运行时被 Zod 拦住，
 * 避免把不可信响应直接渲染到控制台。
 */

// ─── 数据集目录（只读元数据） ────────────────────────────────────────────────

export const ResearchDatasetSummarySchema = z.object({
  dataset_id: z.string(),
  symbol: z.string().nullable(),
  interval: z.string().nullable(),
  month: z.string().nullable(),
  venue: z.string().nullable(),
  market_type: z.string().nullable(),
  base_asset: z.string().nullable(),
  quote_asset: z.string().nullable(),
  source: z.string().nullable(),
  version: z.string().nullable(),
  bar_count: z.number().nullable(),
  coverage_start_ms: z.number().nullable(),
  coverage_end_ms: z.number().nullable(),
  complete: z.boolean(),
  history_dataset_id: z.string().nullable(),
  official_eligible: z.boolean(),
  archive_sha256: z.string().nullable(),
  official_sha256: z.string().nullable(),
  source_url: z.string().nullable(),
  checksum_url: z.string().nullable(),
  provenance_hash: z.string().nullable(),
  conversion_version: z.string().nullable(),
  published_at: z.string().nullable(),
  created_at: z.string().nullable(),
})

export const ResearchDatasetListSchema = z.array(ResearchDatasetSummarySchema)

// ─── 已安装策略 ─────────────────────────────────────────────────────────────

export const ResearchStrategyParamSchema = z.object({
  name: z.string(),
  type: z.string(),
  default: z.string().nullable(),
  required: z.boolean(),
})

export const ResearchStrategySummarySchema = z.object({
  module_path: z.string(),
  strategy_id: z.string(),
  name: z.string(),
  version: z.string(),
  code_hash: z.string(),
  params: z.array(ResearchStrategyParamSchema),
})

export const ResearchStrategyListSchema = z.array(ResearchStrategySummarySchema)

// ─── 回测运行记录 ───────────────────────────────────────────────────────────

export const ResearchBacktestStatusSchema = z.enum([
  'QUEUED',
  'RUNNING',
  'SUCCEEDED',
  'FAILED',
  'CANCELLED',
])

export const ResearchBacktestModelSchema = z.enum(['event_runtime', 'legacy'])

export const ResearchEquityPointSchema = z.object({
  at_ms: z.number(),
  equity: z.string(),
})

export const ResearchBacktestReportSchema = z.object({
  datasets: z.array(
    z.object({
      dataset_id: z.string(),
      identity: z.record(z.string()),
      quality: z.record(z.unknown()),
    }),
  ),
  provenance: z.record(z.string()),
  strategy: z.object({
    module_path: z.string(),
    params: z.record(z.unknown()),
    version: z.string().nullable(),
    code_hash: z.string().nullable(),
  }),
  runtime: z.object({
    engine: z.string(),
    runtime_version: z.string(),
    manifest_hash: z.string(),
    runtime_manifest_hash: z.string(),
  }),
  costs: z.object({
    initial_cash: z.string(),
    fee_bps: z.string(),
    slippage_bps: z.string(),
    total_fees: z.string(),
  }),
  performance: z.object({
    net_value: z.string(),
    total_return: z.string(),
    max_drawdown_pct: z.string(),
    max_drawdown_amount: z.string(),
    win_rate: z.string(),
  }),
  executions: z.object({
    fill_count: z.number(),
    rejections: z.record(z.number()),
    execution_rejections: z.record(z.number()),
  }),
  limitations: z.array(z.string()),
})

/**
 * 成功运行的结果。字段全部可选：老版本（legacy）运行没有这些字段，
 * 但字段存在时仍必须满足类型契约。
 */
export const ResearchBacktestResultSchema = z.object({
  engine: z.string().optional(),
  runtime_version: z.string().optional(),
  admissible: z.boolean().optional(),
  manifest_hash: z.string().optional(),
  runtime_manifest_hash: z.string().optional(),
  limitations: z.array(z.string()).optional(),
  net_value: z.string().optional(),
  total_return: z.string().optional(),
  total_fees: z.string().optional(),
  max_drawdown: z.string().optional(),
  max_drawdown_pct: z.string().optional(),
  max_drawdown_amount: z.string().optional(),
  win_rate: z.string().optional(),
  equity_curve: z.array(z.string()).optional(),
  equity_points: z.array(ResearchEquityPointSchema).optional(),
  positions: z.record(z.string()).optional(),
  fills: z.array(z.record(z.unknown())).optional(),
  rejections: z.record(z.number()).optional(),
  execution_rejections: z.record(z.number()).optional(),
  closed_trades: z.array(z.string()).optional(),
  errors: z.array(z.string()).optional(),
  report: ResearchBacktestReportSchema.optional(),
})

export const ResearchBacktestRunSchema = z.object({
  run_id: z.string(),
  status: ResearchBacktestStatusSchema,
  model: ResearchBacktestModelSchema,
  manifest: z.record(z.unknown()).nullable().optional(),
  result: ResearchBacktestResultSchema.nullable().optional(),
  error: z.string().nullable().optional(),
  created_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
})

export const ResearchBacktestRunListSchema = z.array(ResearchBacktestRunSchema)
