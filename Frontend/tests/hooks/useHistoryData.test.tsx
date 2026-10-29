import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useHistoryDatasets, useHistoryJobs, isActiveJob } from '@/hooks/useHistoryData'
import { researchAPI } from '@/api'
import type { HistoryJob } from '@/types'

vi.mock('@/api', () => ({
  researchAPI: {
    listHistoryJobs: vi.fn(),
    listHistoryDatasets: vi.fn(),
    getHistoryDataset: vi.fn(),
    createHistoryJob: vi.fn(),
    retryHistoryJob: vi.fn(),
  },
}))

const createJob = (overrides: Partial<HistoryJob> = {}): HistoryJob => ({
  job_id: 'hjob_1',
  source: 'binance_vision',
  market: 'spot',
  symbol: 'BTCUSDT',
  interval: '1m',
  month: '2026-09',
  status: 'QUEUED',
  stage: 'QUEUED',
  dataset_id: null,
  feature_version: null,
  rows_processed: 0,
  bytes_downloaded: 0,
  error_code: null,
  error_reason: null,
  requested_by: 'console_user',
  attempt: 1,
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

describe('useHistoryJobs', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('treats QUEUED and RUNNING jobs as active', () => {
    expect(isActiveJob(createJob({ status: 'QUEUED' }))).toBe(true)
    expect(isActiveJob(createJob({ status: 'RUNNING' }))).toBe(true)
    expect(isActiveJob(createJob({ status: 'SUCCEEDED' }))).toBe(false)
    expect(isActiveJob(createJob({ status: 'FAILED' }))).toBe(false)
  })

  it('polls every 2 seconds while a job is active', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(researchAPI.listHistoryJobs).mockResolvedValue([
      createJob({ status: 'RUNNING', stage: 'DOWNLOAD' }),
    ])

    const { result } = renderHook(() => useHistoryJobs(), { wrapper: createWrapper() })

    await vi.waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })

    const callsAfterLoad = vi.mocked(researchAPI.listHistoryJobs).mock.calls.length
    await vi.advanceTimersByTimeAsync(2500)

    await vi.waitFor(() => {
      expect(vi.mocked(researchAPI.listHistoryJobs).mock.calls.length).toBeGreaterThan(
        callsAfterLoad,
      )
    })
  })

  it('stops polling once every job reached a terminal state', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(researchAPI.listHistoryJobs).mockResolvedValue([
      createJob({ status: 'SUCCEEDED', stage: 'PUBLISHED' }),
      createJob({ job_id: 'hjob_2', status: 'FAILED', error_code: 'NETWORK_ERROR' }),
    ])

    const { result } = renderHook(() => useHistoryJobs(), { wrapper: createWrapper() })

    await vi.waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })

    const callsAfterLoad = vi.mocked(researchAPI.listHistoryJobs).mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)

    expect(vi.mocked(researchAPI.listHistoryJobs).mock.calls.length).toBe(callsAfterLoad)
    expect(result.current.data).toHaveLength(2)
  })

  it('refreshes the dataset cache when a job succeeded', async () => {
    vi.mocked(researchAPI.listHistoryJobs).mockResolvedValue([
      createJob({ status: 'SUCCEEDED', stage: 'PUBLISHED', dataset_id: 'hds_1' }),
    ])
    vi.mocked(researchAPI.listHistoryDatasets).mockResolvedValue([])

    renderHook(
      () => ({ jobs: useHistoryJobs(), datasets: useHistoryDatasets() }),
      { wrapper: createWrapper() },
    )

    await waitFor(() => {
      expect(vi.mocked(researchAPI.listHistoryDatasets).mock.calls.length).toBeGreaterThan(0)
    })
  })
})
