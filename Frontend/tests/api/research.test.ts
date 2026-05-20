import { describe, expect, it } from 'vitest'
import { ResearchAPI } from '@/api/research'

class TestResearchAPI extends ResearchAPI {
  calls: Array<{ url: string; data: unknown }> = []

  protected override async post<T>(url: string, data?: unknown): Promise<T> {
    this.calls.push({ url, data })
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
        url: '/v1/strategy-candidates/candidate-1/promote-paper',
        data: undefined,
      },
    ])
    expect(result.status).toBe('APPROVED_FOR_PAPER')
    expect(result.deployment_id).toBe('deployment-1')
  })
})
