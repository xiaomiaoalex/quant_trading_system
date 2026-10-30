import { describe, expect, it } from 'vitest'
import { ResearchAPI } from '@/api/research'

class TestHistoryAPI extends ResearchAPI {
  calls: Array<{ method: 'GET' | 'POST'; url: string; data?: unknown }> = []

  protected override async get<T>(url: string): Promise<T> {
    this.calls.push({ method: 'GET', url })
    return [] as T
  }

  protected override async post<T>(url: string, data?: unknown): Promise<T> {
    this.calls.push({ method: 'POST', url, data })
    return {} as T
  }
}

describe('history data API', () => {
  it('creates a history job through POST /v1/data/history/jobs', async () => {
    const api = new TestHistoryAPI()

    await api.createHistoryJob({ symbol: 'BTCUSDT', month: '2026-09' })

    expect(api.calls).toEqual([
      {
        method: 'POST',
        url: '/v1/data/history/jobs',
        data: { symbol: 'BTCUSDT', month: '2026-09' },
      },
    ])
  })

  it('lists history jobs with filters', async () => {
    const api = new TestHistoryAPI()

    await api.listHistoryJobs({ symbol: 'BTCUSDT', status: 'FAILED', limit: 10 })

    expect(api.calls[0]?.url).toBe(
      '/v1/data/history/jobs?status=FAILED&symbol=BTCUSDT&limit=10',
    )
  })

  it('retries a failed job', async () => {
    const api = new TestHistoryAPI()

    await api.retryHistoryJob('hjob_1')

    expect(api.calls).toEqual([
      { method: 'POST', url: '/v1/data/history/jobs/hjob_1/retry', data: {} },
    ])
  })

  it('lists datasets and filters by eligibility', async () => {
    const api = new TestHistoryAPI()

    await api.listHistoryDatasets({ only_eligible: true })

    expect(api.calls[0]?.url).toBe('/v1/data/history/datasets?only_eligible=true')
  })

  it('fetches a single dataset by id', async () => {
    const api = new TestHistoryAPI()

    await api.getHistoryDataset('hds_1')

    expect(api.calls[0]?.url).toBe('/v1/data/history/datasets/hds_1')
  })
})
