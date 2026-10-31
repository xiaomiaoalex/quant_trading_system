import { describe, expect, it } from 'vitest'
import { ResearchAPI } from '@/api/research'
import type { ResearchDatasetSummary, ResearchStrategySummary } from '@/types'

class TestResearchAPI extends ResearchAPI {
  calls: Array<{ method: 'GET' | 'POST'; url: string; data?: unknown }> = []

  private getPayload: unknown = []
  private postPayload: unknown = {}

  setGetPayload(value: unknown) {
    this.getPayload = value
  }

  setPostPayload(value: unknown) {
    this.postPayload = value
  }

  protected override async get<T>(url: string): Promise<T> {
    this.calls.push({ method: 'GET', url })
    return this.getPayload as T
  }

  protected override async post<T>(url: string, data?: unknown): Promise<T> {
    this.calls.push({ method: 'POST', url, data })
    return this.postPayload as T
  }
}

function createDataset(overrides: Partial<ResearchDatasetSummary> = {}): ResearchDatasetSummary {
  return {
    dataset_id: 'ds_' + 'a'.repeat(64),
    symbol: 'BTCUSDT',
    interval: '1m',
    month: '2026-09',
    venue: 'BINANCE',
    market_type: 'SPOT',
    base_asset: 'BTC',
    quote_asset: 'USDT',
    source: 'binance_vision',
    version: 'binance_vision_1m_v1_abcdef012345',
    bar_count: 43200,
    coverage_start_ms: 1788220800000,
    coverage_end_ms: 1790812740000,
    complete: true,
    history_dataset_id: 'hds_1',
    official_eligible: true,
    archive_sha256: 'b'.repeat(64),
    official_sha256: 'b'.repeat(64),
    source_url: 'https://example/BTCUSDT-1m-2026-09.zip',
    checksum_url: 'https://example/BTCUSDT-1m-2026-09.zip.CHECKSUM',
    provenance_hash: 'c'.repeat(64),
    conversion_version: 'binance_vision_1m_v1',
    published_at: '2026-10-01T00:00:00Z',
    created_at: '2026-10-01T00:00:00Z',
    ...overrides,
  }
}

function createStrategy(overrides: Partial<ResearchStrategySummary> = {}): ResearchStrategySummary {
  return {
    module_path: 'trader.strategies.ema_cross_btc',
    strategy_id: 'ema_cross_btc',
    name: 'EMA Cross BTC',
    version: '1.0.0',
    code_hash: 'd'.repeat(64),
    params: [
      { name: 'fast_period', type: 'int', default: '12', required: false },
    ],
    ...overrides,
  }
}

function createRun(overrides: Record<string, unknown> = {}) {
  return {
    run_id: 'bt_1',
    status: 'QUEUED',
    model: 'event_runtime',
    manifest: { dataset_ids: ['ds_1'], engine: 'event_runtime' },
    result: null,
    error: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  }
}

describe('ResearchAPI (Research plane)', () => {
  it('lists datasets with assembled query parameters and validates the payload', async () => {
    const api = new TestResearchAPI()
    api.setGetPayload([createDataset()])

    const result = await api.listResearchDatasets({
      symbol: 'BTCUSDT',
      month: '2026-09',
      official_only: true,
      limit: 50,
      offset: 0,
    })

    expect(api.calls).toEqual([
      {
        method: 'GET',
        url: '/v1/research/datasets?symbol=BTCUSDT&month=2026-09&official_only=true&limit=50&offset=0',
      },
    ])
    expect(result).toHaveLength(1)
    expect(result[0]?.official_eligible).toBe(true)
    expect(result[0]?.dataset_id).toBe('ds_' + 'a'.repeat(64))
  })

  it('omits the query string when no dataset filters are provided', async () => {
    const api = new TestResearchAPI()
    api.setGetPayload([])

    await api.listResearchDatasets()

    expect(api.calls).toEqual([{ method: 'GET', url: '/v1/research/datasets' }])
  })

  it('rejects dataset payloads that drift from the contract', async () => {
    const api = new TestResearchAPI()
    const broken = createDataset()
    // official_eligible 缺失会导致提交门禁失效，必须被 Zod 拦住。
    delete (broken as unknown as Record<string, unknown>).official_eligible
    api.setGetPayload([broken])

    await expect(api.listResearchDatasets()).rejects.toThrow(/Research datasets schema mismatch/)
  })

  it('lists installed strategies from the backend catalog', async () => {
    const api = new TestResearchAPI()
    api.setGetPayload([createStrategy()])

    const result = await api.listResearchStrategies()

    expect(api.calls).toEqual([{ method: 'GET', url: '/v1/research/strategies' }])
    expect(result[0]?.module_path).toBe('trader.strategies.ema_cross_btc')
  })

  it('submits the manifest inside the documented request envelope', async () => {
    const api = new TestResearchAPI()
    api.setPostPayload(createRun())

    const request = {
      manifest: {
        dataset_ids: ['ds_1'],
        strategy: { module_path: 'trader.strategies.ema_cross_btc' },
        initial_cash: '100000',
        fee_bps: '10',
        slippage_bps: '5',
        engine: 'event_runtime' as const,
        purpose: 'research_screening' as const,
      },
    }

    const run = await api.createResearchBacktest(request)

    expect(api.calls).toEqual([
      { method: 'POST', url: '/v1/research/backtests', data: request },
    ])
    expect(run.run_id).toBe('bt_1')
    expect(run.status).toBe('QUEUED')
  })

  it('lists runs with an explicit limit', async () => {
    const api = new TestResearchAPI()
    api.setGetPayload([createRun({ status: 'SUCCEEDED' })])

    const runs = await api.listResearchBacktests({ limit: 100 })

    expect(api.calls).toEqual([
      { method: 'GET', url: '/v1/research/backtests?limit=100' },
    ])
    expect(runs[0]?.status).toBe('SUCCEEDED')
  })

  it('fetches, retries and cancels a run by run_id', async () => {
    const api = new TestResearchAPI()
    api.setGetPayload(createRun())
    api.setPostPayload(createRun({ status: 'QUEUED' }))

    await api.getResearchBacktest('bt_1')
    await api.retryResearchBacktest('bt_1')
    await api.cancelResearchBacktest('bt_1')

    expect(api.calls).toEqual([
      { method: 'GET', url: '/v1/research/backtests/bt_1' },
      { method: 'POST', url: '/v1/research/backtests/bt_1/retry', data: {} },
      { method: 'POST', url: '/v1/research/backtests/bt_1/cancel', data: {} },
    ])
  })

  it('encodes run identifiers in the path', async () => {
    const api = new TestResearchAPI()
    api.setGetPayload(createRun())

    await api.getResearchBacktest('bt/with space')

    expect(api.calls).toEqual([
      { method: 'GET', url: '/v1/research/backtests/bt%2Fwith%20space' },
    ])
  })

  it('rejects run payloads with an unknown status', async () => {
    const api = new TestResearchAPI()
    api.setGetPayload(createRun({ status: 'DONE' }))

    await expect(api.getResearchBacktest('bt_1')).rejects.toThrow(
      /Research backtest run schema mismatch/,
    )
  })

  it('keeps legacy results out of the strict event-result contract', async () => {
    const api = new TestResearchAPI()
    api.setGetPayload(
      createRun({
        status: 'SUCCEEDED',
        model: 'legacy',
        result: { net_value: 123.45 },
      }),
    )

    // legacy 结果字段与事件结果不同，不能因缺 report / 类型不同就拒绝整条响应。
    const run = await api.getResearchBacktest('bt_1')
    expect(run.result).toBeNull()

    // 事件运行的结果字段一旦存在，就必须满足字符串小数契约。
    api.setGetPayload(createRun({ status: 'SUCCEEDED', result: { net_value: 12.5 } }))
    await expect(api.getResearchBacktest('bt_1')).rejects.toThrow(
      /Research backtest run result schema mismatch/,
    )
  })
})
