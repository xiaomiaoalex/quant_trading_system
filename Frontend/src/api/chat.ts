import { APIClient } from './client'
import type { ChatSession, ChatMessage, SendMessageResponse, RegistrationResult } from '@/types'

export class ChatAPI extends APIClient {
  async createSession(riskLevel = 'LOW'): Promise<ChatSession> {
    return this.post<ChatSession>('/api/chat/sessions', { risk_level: riskLevel })
  }

  async sendMessage(sessionId: string, message: string): Promise<SendMessageResponse> {
    return this.post<SendMessageResponse>(`/api/chat/sessions/${sessionId}/messages`, null, {
      params: { message },
    })
  }

  async getHistory(sessionId: string): Promise<ChatMessage[]> {
    return this.get<ChatMessage[]>(`/api/chat/sessions/${sessionId}/history`)
  }

  async approveStrategy(sessionId: string, strategyId?: string): Promise<RegistrationResult> {
    return this.post<RegistrationResult>(`/api/chat/sessions/${sessionId}/approve`, {
      strategy_id: strategyId,
    })
  }

  async rejectStrategy(sessionId: string, reason?: string): Promise<{ success: boolean }> {
    return this.post<{ success: boolean }>(`/api/chat/sessions/${sessionId}/reject`, { reason })
  }

  async deleteSession(sessionId: string): Promise<void> {
    return this.delete<void>(`/api/chat/sessions/${sessionId}`)
  }

  async listSessions(limit = 100, offset = 0): Promise<ChatSession[]> {
    return this.get<ChatSession[]>('/api/chat/sessions', { params: { limit, offset } })
  }

  async getSession(sessionId: string): Promise<ChatSession> {
    return this.get<ChatSession>(`/api/chat/sessions/${sessionId}`)
  }
}

export const chatAPI = new ChatAPI()
