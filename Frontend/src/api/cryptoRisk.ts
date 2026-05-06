import { APIClient } from './client'
import type {
  CryptoRiskAuditFilters,
  CryptoRiskBudgetUpdateRequest,
  CryptoRiskProbeRequest,
} from '@/types'

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

  async listEvents(filters: CryptoRiskAuditFilters = { limit: 50 }): Promise<unknown> {
    const query = new URLSearchParams({
      limit: String(filters.limit ?? 50),
    })
    if (filters.event_type) query.set('event_type', filters.event_type)
    if (filters.trace_id) query.set('trace_id', filters.trace_id)
    if (filters.signal_id) query.set('signal_id', filters.signal_id)
    return this.get<unknown>(`/v1/risk/crypto/audit?${query.toString()}`)
  }
}

export const cryptoRiskAPI = new CryptoRiskAPI()
