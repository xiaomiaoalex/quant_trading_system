import { describe, expect, it } from 'vitest'
import { ResearchAPI } from '@/api/research'

class TestResearchAPI extends ResearchAPI {
  calls: Array<{ method: 'GET' | 'POST'; url: string; data?: unknown }> = []

  protected override async get<T>(url: string): Promise<T> {
    this.calls.push({ method: 'GET', url })
    return {
      candidate_id: 'candidate-1',
      strategy_id: 'strategy-1',
      status: 'BACKTEST_PASSED',
      config: {},
      feature_version: 'dev_smoke',
      events: [],
    } as T
  }

  protected override async post<T>(url: string, data?: unknown): Promise<T> {
    this.calls.push({ method: 'POST', url, data })
    return {
      candidate_id: 'candidate-1',
      strategy_id: 'strategy-1',
      deployment_id: 'deployment-1',
      code_version: 7,
      status: 'APPROVED_FOR_PAPER',
      promoted_at: '2026-05-20T12:00:00Z',
    } as T
  }
}

describe('ResearchAPI', () => {
  it('promotes candidates through the atomic promote-paper endpoint without a legacy body', async () => {
    const api = new TestResearchAPI()

    const result = await api.promoteCandidateToPaper('candidate-1')

    expect(api.calls).toEqual([
      {
        method: 'POST',
        url: '/v1/strategy-candidates/candidate-1/promote-paper',
        data: undefined,
      },
    ])
    expect(result.status).toBe('APPROVED_FOR_PAPER')
    expect(result.deployment_id).toBe('deployment-1')
  })

  it('fetches a candidate for async backtest status polling', async () => {
    const api = new TestResearchAPI()

    const result = await api.getCandidate('candidate-1')

    expect(api.calls).toEqual([
      {
        method: 'GET',
        url: '/v1/strategy-candidates/candidate-1',
      },
    ])
    expect(result.status).toBe('BACKTEST_PASSED')
  })
})
