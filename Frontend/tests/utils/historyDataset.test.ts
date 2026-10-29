import { describe, it, expect } from 'vitest'
import {
  applyDatasetToLabForm,
  clearDatasetFromLabForm,
  datasetOptionLabel,
  isDatasetEligible,
  type BacktestLabForm,
} from '@/utils/historyDataset'
import type { HistoryDataset } from '@/types'

const baseForm = (): BacktestLabForm => ({
  strategy_id: 'lab_strategy',
  name: 'Lab Strategy',
  description: '',
  engine: 'vectorbt',
  symbols: 'BTCUSDT',
  start_ts_ms: 1,
  end_ts_ms: 2,
  venue: 'BINANCE',
  feature_version: 'dev_smoke',
  initial_capital: 100000,
  fee_bps: 10,
  slippage_bps: 5,
  benchmark: 'BTCUSDT',
  data_mode: 'dev_smoke',
  risk_mode: 'risk_adjusted',
  requested_by: 'console_user',
  dataset_id: '',
  interval: '',
})

const dataset = (overrides: Partial<HistoryDataset> = {}): HistoryDataset => ({
  dataset_id: 'hds_1',
  source: 'binance_vision',
  market: 'spot',
  symbol: 'ETHUSDT',
  interval: '1m',
  month: '2026-09',
  version: 1,
  feature_version: 'binance_spot_1m_ETHUSDT_2026-09_aaaaaaaaaaaa',
  archive_sha256: 'a'.repeat(64),
  official_sha256: 'a'.repeat(64),
  source_url: 'https://example/a.zip',
  checksum_url: 'https://example/a.zip.CHECKSUM',
  row_count: 43200,
  expected_points: 43200,
  gap_count: 0,
  first_ts_ms: 1788220800000,
  last_ts_ms: 1790812740000,
  quality: {},
  status: 'PUBLISHED',
  persistence_ok: true,
  backtest_eligible: true,
  ...overrides,
})

describe('history dataset form binding', () => {
  it('treats only published and persisted datasets as eligible', () => {
    expect(isDatasetEligible(dataset())).toBe(true)
    expect(isDatasetEligible(dataset({ persistence_ok: false }))).toBe(false)
    expect(isDatasetEligible(dataset({ backtest_eligible: false }))).toBe(false)
    expect(isDatasetEligible(dataset({ status: 'PENDING' }))).toBe(false)
  })

  it('treats datasets with gaps as ineligible', () => {
    expect(isDatasetEligible(dataset({ gap_count: 3 }))).toBe(false)
  })

  it('treats datasets whose row count does not match the expectation as ineligible', () => {
    expect(isDatasetEligible(dataset({ row_count: 43199 }))).toBe(false)
    expect(isDatasetEligible(dataset({ expected_points: 44640 }))).toBe(false)
  })

  it('fills symbol, range, version and interval from the queried dataset', () => {
    const form = applyDatasetToLabForm(baseForm(), dataset())

    expect(form.symbols).toBe('ETHUSDT')
    expect(form.start_ts_ms).toBe(1788220800000)
    expect(form.end_ts_ms).toBe(1790812740000)
    expect(form.feature_version).toBe('binance_spot_1m_ETHUSDT_2026-09_aaaaaaaaaaaa')
    expect(form.data_mode).toBe('real_feature_store')
    expect(form.engine).toBe('vectorbt')
    expect(form.interval).toBe('1m')
    expect(form.dataset_id).toBe('hds_1')
  })

  it('keeps the form untouched for an ineligible dataset', () => {
    const form = baseForm()
    const result = applyDatasetToLabForm(form, dataset({ backtest_eligible: false }))
    expect(result).toEqual(form)
  })

  it('clears dataset binding when switching back to the dev sample data', () => {
    const bound = applyDatasetToLabForm(baseForm(), dataset())
    const cleared = clearDatasetFromLabForm(bound)

    expect(cleared.dataset_id).toBe('')
    expect(cleared.interval).toBe('')
    expect(cleared.feature_version).toBe('dev_smoke')
    expect(cleared.data_mode).toBe('dev_smoke')
  })

  it('labels dataset options with real-history wording', () => {
    expect(datasetOptionLabel(dataset())).toContain('真实历史数据')
    expect(datasetOptionLabel(dataset())).toContain('ETHUSDT')
  })
})
