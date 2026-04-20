export type ReplayJobStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED'

export interface ReplayRequest {
  stream_key: string
  from_ts_ms?: number
  to_ts_ms?: number
  requested_by: string
}

export interface ReplayJob {
  job_id: string
  stream_key: string
  status: ReplayJobStatus | string
  requested_by: string
  requested_at: string
  started_at?: string | null
  finished_at?: string | null
  result_summary?: Record<string, unknown> | null
  error?: string | null
}

export interface ReplayListParams {
  status?: string
  stream_key?: string
  requested_by?: string
  limit?: number
  offset?: number
}

