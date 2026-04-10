import { APIClient } from './client'
import type { ReconcileReport, DriftEvent } from '@/types'

export class ReconcileAPI extends APIClient {
  async getReport(): Promise<ReconcileReport> {
    return this.get<ReconcileReport>('/v1/reconciler/report')
  }

  async triggerReconciliation(): Promise<{ ok: boolean; message?: string; reconciliation_id?: string }> {
    return this.post<{ ok: boolean; message?: string; reconciliation_id?: string }>('/v1/reconciler/trigger')
  }

  async getDriftEvents(): Promise<DriftEvent[]> {
    return this.get<DriftEvent[]>('/v1/events?stream_key=order_drifts')
  }
}

export const reconcileAPI = new ReconcileAPI()