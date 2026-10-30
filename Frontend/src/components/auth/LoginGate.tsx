import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { authAPI } from '@/api'
import { isAPIError } from '@/api/client'

type State = 'checking' | 'authenticated' | 'login' | 'unavailable'

export function LoginGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>('checking')
  const [username, setUsername] = useState('owner')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const check = async () => {
    setState('checking')
    setError(null)
    try {
      await authAPI.me()
      setState('authenticated')
    } catch (exc) {
      if (isAPIError(exc) && exc.code === 'HTTP_401') {
        setState('login')
      } else {
        setError(isAPIError(exc) ? exc.message : 'Unable to verify the session.')
        setState('unavailable')
      }
    }
  }

  useEffect(() => {
    void check()
  }, [])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await authAPI.login(username, password)
      setPassword('')
      setState('authenticated')
    } catch (exc) {
      setError(isAPIError(exc) ? exc.message : 'Login failed.')
    } finally {
      setSubmitting(false)
    }
  }

  if (state === 'authenticated') return <>{children}</>
  if (state === 'checking') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-950 text-sm text-gray-400">
        Verifying session…
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-950 p-6">
      <div className="w-full max-w-sm rounded-lg border border-gray-700 bg-gray-900 p-6">
        <h1 className="text-lg font-semibold text-gray-100">Trading console</h1>
        {state === 'unavailable' ? (
          <>
            <p className="mt-2 text-sm text-red-300">{error}</p>
            <button
              onClick={() => void check()}
              className="mt-4 w-full rounded bg-gray-800 px-4 py-2 text-sm text-gray-200 hover:bg-gray-700"
            >
              Retry
            </button>
          </>
        ) : (
          <form onSubmit={submit} className="mt-4 space-y-3">
            <label className="block text-xs uppercase text-gray-400">
              Owner
              <input
                className="mt-1 w-full rounded border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100"
                value={username}
                autoComplete="username"
                onChange={event => setUsername(event.target.value)}
              />
            </label>
            <label className="block text-xs uppercase text-gray-400">
              Password
              <input
                className="mt-1 w-full rounded border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100"
                type="password"
                value={password}
                autoComplete="current-password"
                onChange={event => setPassword(event.target.value)}
              />
            </label>
            {error && <p className="text-xs text-red-300">{error}</p>}
            <button
              type="submit"
              disabled={submitting || !password}
              className="w-full rounded bg-blue-900/40 px-4 py-2 text-sm font-medium text-blue-200 hover:bg-blue-900/60 disabled:opacity-50"
            >
              {submitting ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
