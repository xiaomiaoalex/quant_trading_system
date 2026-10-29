import type {
  BacktestDataMode,
  BacktestEngine,
  BacktestRiskMode,
  HistoryDataset,
} from '@/types'

export interface BacktestLabForm {
  strategy_id: string
  name: string
  description: string
  engine: BacktestEngine
  symbols: string
  start_ts_ms: number
  end_ts_ms: number
  venue: string
  feature_version: string
  initial_capital: number
  fee_bps: number
  slippage_bps: number
  benchmark: string
  data_mode: BacktestDataMode
  risk_mode: BacktestRiskMode
  requested_by: string
  dataset_id: string
  interval: string
}

/** 数据集是否满足回测准入（已发布 + 已持久化 + 无缺口）。 */
export function isDatasetEligible(dataset: HistoryDataset): boolean {
  return dataset.status === 'PUBLISHED' && dataset.persistence_ok && dataset.backtest_eligible
}

/**
 * 用「重新查询到的数据集」填充回测表单。
 *
 * 注意：不信任跳转 URL 里的其它参数，只使用数据集自身的元数据。
 */
export function applyDatasetToLabForm(
  form: BacktestLabForm,
  dataset: HistoryDataset,
): BacktestLabForm {
  if (!isDatasetEligible(dataset)) {
    return form
  }
  return {
    ...form,
    symbols: dataset.symbol,
    start_ts_ms: dataset.first_ts_ms ?? form.start_ts_ms,
    end_ts_ms: dataset.last_ts_ms ?? form.end_ts_ms,
    feature_version: dataset.feature_version,
    data_mode: 'real_feature_store',
    engine: 'vectorbt',
    dataset_id: dataset.dataset_id,
    interval: dataset.interval,
  }
}

/** 切回「开发样例数据」：清空数据集绑定。 */
export function clearDatasetFromLabForm(form: BacktestLabForm): BacktestLabForm {
  return {
    ...form,
    dataset_id: '',
    interval: '',
    feature_version: 'dev_smoke',
    data_mode: 'dev_smoke',
  }
}

/** 数据集下拉选项文案。 */
export function datasetOptionLabel(dataset: HistoryDataset): string {
  return `真实历史数据 · ${dataset.symbol} ${dataset.month} ${dataset.interval} (${dataset.row_count} bars)`
}
