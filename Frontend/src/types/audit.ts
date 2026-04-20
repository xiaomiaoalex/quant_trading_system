export interface AuditEntry {
  entry_id: string
  strategy_id: string
  strategy_name?: string | null
  version?: string | null
  event_type: string
  status: string
  prompt?: string | null
  generated_code?: string | null
  code_hash?: string | null
  llm_backend?: string | null
  llm_model?: string | null
  execution_result?: Record<string, unknown> | null
  approver?: string | null
  approval_comment?: string | null
  metadata?: Record<string, unknown> | null
  created_at?: string | null
  updated_at?: string | null
}

export interface AuditListParams {
  strategy_id?: string
  status?: string
  event_type?: string
  llm_backend?: string
  since?: string
  until?: string
  limit?: number
  offset?: number
}
