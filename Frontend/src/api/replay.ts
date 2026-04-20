import { APIClient } from './client'
import type { ReplayJob, ReplayListParams, ReplayRequest } from '@/types'

export class ReplayAPI extends APIClient {
  async listJobs(params?: ReplayListParams): Promise<ReplayJob[]> {
    const searchParams = new URLSearchParams()
    if (params?.status) searchParams.set('status', params.status)
    if (params?.stream_key) searchParams.set('stream_key', params.stream_key)
    if (params?.requested_by) searchParams.set('requested_by', params.requested_by)
    if (typeof params?.limit === 'number') searchParams.set('limit', String(params.limit))
    if (typeof params?.offset === 'number') searchParams.set('offset', String(params.offset))
    const query = searchParams.toString()
    return this.get<ReplayJob[]>(`/v1/replay${query ? `?${query}` : ''}`)
  }

  async getJob(jobId: string): Promise<ReplayJob> {
    return this.get<ReplayJob>(`/v1/replay/${jobId}`)
  }

  async trigger(request: ReplayRequest): Promise<ReplayJob> {
    return this.post<ReplayJob>('/v1/replay', request)
  }
}

export const replayAPI = new ReplayAPI()

