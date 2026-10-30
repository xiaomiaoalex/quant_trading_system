import { APIClient, setCSRFToken } from './client'

export interface LoginResponse {
  actor: string
  csrf_token: string
}

export class AuthAPI extends APIClient {
  async login(username: string, password: string): Promise<LoginResponse> {
    const response = await this.post<LoginResponse>('/v1/auth/login', { username, password })
    setCSRFToken(response.csrf_token)
    return response
  }

  async logout(): Promise<{ ok: boolean }> {
    try {
      return await this.post<{ ok: boolean }>('/v1/auth/logout')
    } finally {
      setCSRFToken(null)
    }
  }

  async me(): Promise<{ actor: string }> {
    return this.get<{ actor: string }>('/v1/auth/me')
  }
}

export const authAPI = new AuthAPI()
