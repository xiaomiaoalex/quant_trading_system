import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  useResearchBacktestRun,
  useResearchBacktestRuns,
  useRetryResearchBacktest,
  useSubmitResearchBacktest,
} from '@/hooks/useResearchBacktests'
import { researchAPI } from '@/api'
import { isActiveResearchRun, isTerminalResearchStatus } from '@/utils/researchBacktest'
import type { ResearchBacktestRun } from '@/types'

vi.mock('@/api', () => ({
  researchAPI: {
    listResearchDatasets: vi.fn(),
    listResearchStrategies: vi.fn(),
    listResearchBacktests: vi.fn(),
    getResearchBacktest: vi.fn(),
    createResearchBacktest: vi.fn(),
    retryResearchBacktest: vi.fn(),
  },
}))

const createRun = (overrides: Partial<ResearchBacktestRun> = {}): ResearchBacktestRun => ({
  run_id: 'bt_1',
  status: 'QUEUED',
  model: 'event_runtime',
  manifest: { dataset_ids: ['ds_1'], strategy: { module_path: 'trader.strategies.ema_cross_btc' } },
  result: null,
  error: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  ...overrides,
})

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

describe('isTerminalResearchStatus', () => {
  it('treats SUCCEEDED / FAILED / CANCELLED as terminal', () => {
    expect(isTerminalResearchStatus('SUCCEEDED')).toBe(true)
    expect(isTerminalResearchStatus('FAILED')).toBe(true)
    expect(isTerminalResearchStatus('CANCELLED')).toBe(true)
    expect(isTerminalResearchStatus('QUEUED')).toBe(false)
    expect(isTerminalResearchStatus('RUNNING')).toBe(false)
  })

  it('treats QUEUED / RUNNING as active', () => {
    expect(isActiveResearchRun(createRun({ status: 'QUEUED' }))).toBe(true)
    expect(isActiveResearchRun(createRun({ status: 'RUNNING' }))).toBe(true)
    expect(isActiveResearchRun(createRun({ status: 'SUCCEEDED' }))).toBe(false)
  })
})

describe('useResearchBacktestRuns', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('polls every 2 seconds while a run is active', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(researchAPI.listResearchBacktests).mockResolvedValue([
      createRun({ status: 'RUNNING' }),
    ])

    const { result } = renderHook(() => useResearchBacktestRuns({ limit: 100 }), {
      wrapper: createWrapper(),
    })

    await vi.waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })

    const callsAfterLoad = vi.mocked(researchAPI.listResearchBacktests).mock.calls.length
    await vi.advanceTimersByTimeAsync(2500)

    await vi.waitFor(() => {
      expect(
        vi.mocked(researchAPI.listResearchBacktests).mock.calls.length,
      ).toBeGreaterThan(callsAfterLoad)
    })
  })

  it('stops polling once every run reached a terminal state', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(researchAPI.listResearchBacktests).mockResolvedValue([
      createRun({ run_id: 'bt_done', status: 'SUCCEEDED' }),
      createRun({ run_id: 'bt_failed', status: 'FAILED', error: 'boom' }),
      createRun({ run_id: 'bt_cancelled', status: 'CANCELLED' }),
    ])

    const { result } = renderHook(() => useResearchBacktestRuns(), {
      wrapper: createWrapper(),
    })

    await vi.waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })

    const callsAfterLoad = vi.mocked(researchAPI.listResearchBacktests).mock.calls.length
    await vi.advanceTimersByTimeAsync(6000)

    expect(vi.mocked(researchAPI.listResearchBacktests).mock.calls.length).toBe(callsAfterLoad)
    expect(result.current.data).toHaveLength(3)
  })

  it('exposes the failure state when the list query rejects', async () => {
    vi.mocked(researchAPI.listResearchBacktests).mockRejectedValue({
      code: 'HTTP_503',
      message: 'Research store unavailable',
    })

    const { result } = renderHook(() => useResearchBacktestRuns(), {
      wrapper: createWrapper(),
    })

    await waitFor(
      () => {
        expect(result.current.isError).toBe(true)
      },
      { timeout: 5000 },
    )
  })
})

describe('useResearchBacktestRun', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('re-queries the selected run by run_id after a refresh', async () => {
    vi.mocked(researchAPI.getResearchBacktest).mockResolvedValue(
      createRun({ run_id: 'bt_restored', status: 'SUCCEEDED' }),
    )

    const { result } = renderHook(() => useResearchBacktestRun('bt_restored'), {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })

    expect(vi.mocked(researchAPI.getResearchBacktest)).toHaveBeenCalledWith('bt_restored')
    expect(result.current.data?.status).toBe('SUCCEEDED')
  })

  it('does not query when no run id is selected', async () => {
    renderHook(() => useResearchBacktestRun(null), { wrapper: createWrapper() })

    await waitFor(() => {
      expect(vi.mocked(researchAPI.getResearchBacktest)).not.toHaveBeenCalled()
    })
  })

  it('polls an active run and stops once it succeeds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(researchAPI.getResearchBacktest)
      .mockResolvedValueOnce(createRun({ status: 'RUNNING' }))
      .mockResolvedValue(createRun({ status: 'SUCCEEDED' }))

    const { result } = renderHook(() => useResearchBacktestRun('bt_1'), {
      wrapper: createWrapper(),
    })

    await vi.waitFor(() => {
      expect(result.current.data?.status).toBe('RUNNING')
    })

    await vi.advanceTimersByTimeAsync(2500)
    await vi.waitFor(() => {
      expect(result.current.data?.status).toBe('SUCCEEDED')
    })

    const callsAfterSuccess = vi.mocked(researchAPI.getResearchBacktest).mock.calls.length
    await vi.advanceTimersByTimeAsync(6000)
    expect(vi.mocked(researchAPI.getResearchBacktest).mock.calls.length).toBe(callsAfterSuccess)
  })

  it('stops polling immediately for a terminal FAILED run', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(researchAPI.getResearchBacktest).mockResolvedValue(
      createRun({ status: 'FAILED', error: 'frozen provenance mismatch' }),
    )

    const { result } = renderHook(() => useResearchBacktestRun('bt_1'), {
      wrapper: createWrapper(),
    })

    await vi.waitFor(() => {
      expect(result.current.data?.status).toBe('FAILED')
    })

    const callsAfterLoad = vi.mocked(researchAPI.getResearchBacktest).mock.calls.length
    await vi.advanceTimersByTimeAsync(6000)
    expect(vi.mocked(researchAPI.getResearchBacktest).mock.calls.length).toBe(callsAfterLoad)
  })
})

describe('useSubmitResearchBacktest / useRetryResearchBacktest', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('submits the manifest and returns the created run', async () => {
    const created = createRun({ run_id: 'bt_new' })
    vi.mocked(researchAPI.createResearchBacktest).mockResolvedValue(created)

    const { result } = renderHook(() => useSubmitResearchBacktest(), {
      wrapper: createWrapper(),
    })

    const request = {
      manifest: {
        dataset_ids: ['ds_1'],
        strategy: { module_path: 'trader.strategies.ema_cross_btc' },
        initial_cash: '100000',
        fee_bps: '10',
        slippage_bps: '5',
        engine: 'event_runtime' as const,
        purpose: 'research_screening' as const,
      },
    }

    let submitted: ResearchBacktestRun | null = null
    await act(async () => {
      submitted = await result.current.submit(request)
    })

    expect(vi.mocked(researchAPI.createResearchBacktest)).toHaveBeenCalledWith(request)
    expect(submitted).toEqual(created)
  })

  it('returns retry failure and exposes the API error', async () => {
    vi.mocked(researchAPI.retryResearchBacktest).mockRejectedValue({
      code: 'HTTP_409',
      message: 'Run bt_1 is RUNNING; only FAILED runs can be retried',
    })

    const { result } = renderHook(() => useRetryResearchBacktest(), {
      wrapper: createWrapper(),
    })

    let retried: ResearchBacktestRun | null = createRun()
    await act(async () => {
      retried = await result.current.retry('bt_1')
    })

    expect(retried).toBeNull()
    await waitFor(() => {
      expect(result.current.error).toContain('HTTP_409')
    })
  })
})
