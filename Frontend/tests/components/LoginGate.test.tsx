import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LoginGate } from '@/components/auth/LoginGate'
import { authAPI } from '@/api'

vi.mock('@/api', () => ({
  authAPI: {
    me: vi.fn(),
    login: vi.fn(),
  },
}))

const mockedMe = vi.mocked(authAPI.me)
const mockedLogin = vi.mocked(authAPI.login)

describe('LoginGate', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders children when the session is valid', async () => {
    mockedMe.mockResolvedValue({ actor: 'owner' })
    render(
      <LoginGate>
        <div>console</div>
      </LoginGate>,
    )
    expect(await screen.findByText('console')).toBeTruthy()
  })

  it('shows the login form on 401 and signs in with the session csrf', async () => {
    mockedMe.mockRejectedValue({ code: 'HTTP_401', message: 'Authentication required' })
    mockedLogin.mockResolvedValue({ actor: 'owner', csrf_token: 'csrf-1' })

    render(
      <LoginGate>
        <div>console</div>
      </LoginGate>,
    )

    const password = await screen.findByLabelText('Password')
    fireEvent.change(password, { target: { value: 'correct horse battery staple' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(mockedLogin).toHaveBeenCalled())
    expect(mockedLogin).toHaveBeenCalledWith('owner', 'correct horse battery staple')
    expect(await screen.findByText('console')).toBeTruthy()
  })

  it('fails closed instead of rendering the console when the API is unavailable', async () => {
    mockedMe.mockRejectedValue({ code: 'NETWORK_ERROR', message: 'Unable to reach server.' })
    render(
      <LoginGate>
        <div>console</div>
      </LoginGate>,
    )
    expect(await screen.findByText('Unable to reach server.')).toBeTruthy()
    expect(screen.queryByText('console')).toBeNull()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })
})
