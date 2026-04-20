import { APIClient } from './client'
import type { AuditEntry, AuditListParams } from '@/types'

export class AuditAPI extends APIClient {
  async listEntries(params?: AuditListParams): Promise<AuditEntry[]> {
    const searchParams = new URLSearchParams()
    if (params?.strategy_id) searchParams.set('strategy_id', params.strategy_id)
    if (params?.status) searchParams.set('status', params.status)
    if (params?.event_type) searchParams.set('event_type', params.event_type)
    if (params?.llm_backend) searchParams.set('llm_backend', params.llm_backend)
    if (params?.since) searchParams.set('since', params.since)
    if (params?.until) searchParams.set('until', params.until)
    if (typeof params?.limit === 'number') searchParams.set('limit', String(params.limit))
    if (typeof params?.offset === 'number') searchParams.set('offset', String(params.offset))

    const query = searchParams.toString()
    return this.get<AuditEntry[]>(`/api/audit/entries${query ? `?${query}` : ''}`)
  }

  async getEntry(entryId: string): Promise<AuditEntry> {
    return this.get<AuditEntry>(`/api/audit/entries/${entryId}`)
  }
}

export const auditAPI = new AuditAPI()
