import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthAPI } from '@/api/auth'
import { getCSRFToken, setCSRFToken } from '@/api/client'

class TestAuthAPI extends AuthAPI {
  protected override async get<T>(): Promise<T> {
    return { actor: 'owner', csrf_token: 'restored-token' } as T
  }
}

describe('AuthAPI session restore', () => {
  beforeEach(() => setCSRFToken(null))

  it('restores the CSRF header token when a page reload keeps the session cookie', async () => {
    const api = new TestAuthAPI()
    expect(getCSRFToken()).toBe('')
    await api.me()
    expect(getCSRFToken()).toBe('restored-token')
  })

  it('clears a previous token when session validation fails', async () => {
    const api = new AuthAPI()
    setCSRFToken('old-token')
    vi.spyOn(api as unknown as { get: () => Promise<never> }, 'get')
      .mockRejectedValue({ code: 'HTTP_401', message: 'Authentication required' })
    await expect(api.me()).rejects.toMatchObject({ code: 'HTTP_401' })
    expect(getCSRFToken()).toBe('')
  })
})
