export type MessageRole = 'user' | 'assistant' | 'system'

export type SessionStatus = 'active' | 'waiting_approval' | 'approved' | 'rejected' | 'completed' | 'expired'

export interface Attachment {
  attachment_id: string
  name: string
  content: string
  mime_type: string
}

export interface ChatMessage {
  message_id: string
  role: MessageRole
  content: string
  timestamp: string
  attachments: Attachment[]
}

export interface ChatSession {
  session_id: string
  status: SessionStatus
  created_at: string
  updated_at: string
  message_count: number
  has_strategy: boolean
  metadata: Record<string, unknown>
}

export interface SendMessageResponse {
  response_id: string
  message: ChatMessage
  suggestions: string[]
  status: SessionStatus
  metadata: Record<string, unknown>
}

export interface RegistrationResult {
  success: boolean
  strategy_id?: string
  entry_id?: string
  error?: string
}

export const SESSION_STATUS_DISPLAY: Record<SessionStatus, { label: string; color: string }> = {
  active: { label: 'Active', color: 'text-green-400' },
  waiting_approval: { label: 'Pending Approval', color: 'text-yellow-400' },
  approved: { label: 'Approved', color: 'text-blue-400' },
  rejected: { label: 'Rejected', color: 'text-red-400' },
  completed: { label: 'Completed', color: 'text-gray-400' },
  expired: { label: 'Expired', color: 'text-gray-500' },
}

export const MESSAGE_ROLE_DISPLAY: Record<MessageRole, { label: string; color: string }> = {
  user: { label: 'You', color: 'text-blue-400' },
  assistant: { label: 'Assistant', color: 'text-purple-400' },
  system: { label: 'System', color: 'text-gray-400' },
}
