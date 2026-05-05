import { APIClient } from './client'
import type { CryptoRiskBudgetUpdateRequest, CryptoRiskProbeRequest } from '@/types'

export class CryptoRiskAPI extends APIClient {
  async getRuntime(): Promise<unknown> {
    return this.get<unknown>('/v1/risk/crypto/runtime')
  }

  async updateBudget(request: CryptoRiskBudgetUpdateRequest): Promise<unknown> {
    return this.patch<unknown>('/v1/risk/crypto/budget', request)
  }

  async runProbe(request: CryptoRiskProbeRequest): Promise<unknown> {
    return this.post<unknown>('/v1/risk/crypto/probe', request)
  }

  async listEvents(limit = 50): Promise<unknown> {
    const query = new URLSearchParams({
      stream_key: 'risk:crypto',
      limit: String(limit),
    })
    return this.get<unknown>(`/v1/events?${query.toString()}`)
  }
}

export const cryptoRiskAPI = new CryptoRiskAPI()
