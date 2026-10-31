import { describe, it, expect } from 'vitest'
import {
  DEFAULT_FEE_BPS,
  DEFAULT_INITIAL_CASH,
  DEFAULT_SLIPPAGE_BPS,
  buildResearchBacktestRequest,
  formatCoverageMs,
  isActiveResearchStatus,
  isOfficialEligibleDataset,
  isTerminalResearchStatus,
  parseDecimalText,
  researchDatasetLabel,
  runDatasetIds,
  runStrategyModulePath,
  validateResearchBacktestInputs,
} from '@/utils/researchBacktest'
import type { ResearchBacktestRun, ResearchDatasetSummary } from '@/types'

const dataset: ResearchDatasetSummary = {
  dataset_id: 'ds_1',
  symbol: 'BTCUSDT',
  interval: '1m',
  month: '2026-09',
  venue: 'BINANCE',
  market_type: 'SPOT',
  base_asset: 'BTC',
  quote_asset: 'USDT',
  source: 'binance_vision',
  version: 'v1',
  bar_count: 10,
  coverage_start_ms: 0,
  coverage_end_ms: 600000,
  complete: true,
  history_dataset_id: 'hds_1',
  official_eligible: true,
  archive_sha256: null,
  official_sha256: null,
  source_url: null,
  checksum_url: null,
  provenance_hash: null,
  conversion_version: null,
  published_at: null,
  created_at: null,
}

const validInputs = {
  datasetId: 'ds_1',
  modulePath: 'trader.strategies.ema_cross_btc',
  initialCash: DEFAULT_INITIAL_CASH,
  feeBps: DEFAULT_FEE_BPS,
  slippageBps: DEFAULT_SLIPPAGE_BPS,
}

describe('researchBacktest utils', () => {
  it('builds the frozen manifest with fixed engine and purpose', () => {
    expect(buildResearchBacktestRequest(validInputs)).toEqual({
      manifest: {
        dataset_ids: ['ds_1'],
        strategy: { module_path: 'trader.strategies.ema_cross_btc' },
        initial_cash: '100000',
        fee_bps: '10',
        slippage_bps: '5',
        engine: 'event_runtime',
        purpose: 'research_screening',
      },
    })
  })

  it('accepts the documented default inputs', () => {
    expect(validateResearchBacktestInputs(validInputs)).toEqual([])
  })

  it('rejects non-positive capital and out-of-range costs', () => {
    expect(
      validateResearchBacktestInputs({ ...validInputs, initialCash: '0' }),
    ).toContain('初始资金必须大于 0')
    expect(
      validateResearchBacktestInputs({ ...validInputs, initialCash: 'abc' }),
    ).toContain('初始资金必须是十进制数字（例如 100000）')
    expect(
      validateResearchBacktestInputs({ ...validInputs, feeBps: '101' }),
    ).toContain('费率不能超过 100 bps')
    expect(
      validateResearchBacktestInputs({ ...validInputs, slippageBps: '1001' }),
    ).toContain('滑点不能超过 1000 bps')
  })

  it('parses decimal text without accepting signs or exponents', () => {
    expect(parseDecimalText('100000')).toBe(100000)
    expect(parseDecimalText(' 7.5 ')).toBe(7.5)
    expect(parseDecimalText('-1')).toBeNull()
    expect(parseDecimalText('1e3')).toBeNull()
    expect(parseDecimalText('')).toBeNull()
  })

  it('gates on backend official eligibility only', () => {
    expect(isOfficialEligibleDataset(dataset)).toBe(true)
    expect(isOfficialEligibleDataset({ ...dataset, official_eligible: false })).toBe(false)
    expect(isOfficialEligibleDataset(null)).toBe(false)
  })

  it('classifies terminal and active statuses', () => {
    expect(isTerminalResearchStatus('SUCCEEDED')).toBe(true)
    expect(isTerminalResearchStatus('FAILED')).toBe(true)
    expect(isTerminalResearchStatus('CANCELLED')).toBe(true)
    expect(isActiveResearchStatus('QUEUED')).toBe(true)
    expect(isActiveResearchStatus('RUNNING')).toBe(true)
    expect(isActiveResearchStatus('FAILED')).toBe(false)
  })

  it('formats coverage and dataset labels', () => {
    expect(formatCoverageMs(null, null)).toBe('-')
    expect(formatCoverageMs(0, 600000)).toBe('1970-01-01T00:00:00.000Z → 1970-01-01T00:10:00.000Z')
    expect(researchDatasetLabel(dataset)).toContain('BTCUSDT 2026-09 1m')
    expect(researchDatasetLabel(dataset)).toContain('官方合格')
    expect(researchDatasetLabel({ ...dataset, official_eligible: false })).toContain('资格不足')
  })

  it('extracts display data from the frozen manifest', () => {
    const run = {
      run_id: 'bt_1',
      status: 'QUEUED',
      model: 'event_runtime',
      manifest: {
        dataset_ids: ['ds_1', 42],
        strategy: { module_path: 'trader.strategies.ema_cross_btc' },
      },
    } as unknown as ResearchBacktestRun

    expect(runDatasetIds(run)).toEqual(['ds_1'])
    expect(runStrategyModulePath(run)).toBe('trader.strategies.ema_cross_btc')
    expect(
      runDatasetIds({ ...run, manifest: null } as ResearchBacktestRun),
    ).toEqual([])
    expect(
      runStrategyModulePath({ ...run, manifest: { strategy: null } } as ResearchBacktestRun),
    ).toBeNull()
  })
})
