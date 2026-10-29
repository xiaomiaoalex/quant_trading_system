import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { HistoryDataPanel } from '@/components/history/HistoryDataPanel'
import { researchAPI } from '@/api'
import type { HistoryDataset, HistoryJob } from '@/types'

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

const createDataset = (overrides: Partial<HistoryDataset> = {}): HistoryDataset => ({
  dataset_id: 'hds_1',
  source: 'binance_vision',
  market: 'spot',
  symbol: 'BTCUSDT',
  interval: '1m',
  month: '2026-09',
  version: 1,
  feature_version: 'binance_spot_1m_BTCUSDT_2026-09_aaaaaaaaaaaa',
  archive_sha256: 'a'.repeat(64),
  official_sha256: 'a'.repeat(64),
  source_url: 'https://example/a.zip',
  checksum_url: 'https://example/a.zip.CHECKSUM',
  row_count: 43200,
  expected_points: 43200,
  gap_count: 0,
  first_ts_ms: 1788220800000,
  last_ts_ms: 1790812740000,
  quality: {},
  status: 'PUBLISHED',
  persistence_ok: true,
  backtest_eligible: true,
  ...overrides,
})

const renderPanel = (onUseForBacktest?: (datasetId: string) => void) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <HistoryDataPanel onUseForBacktest={onUseForBacktest} />
    </QueryClientProvider>,
  )
}

describe('HistoryDataPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(researchAPI.listHistoryJobs).mockResolvedValue([])
    vi.mocked(researchAPI.listHistoryDatasets).mockResolvedValue([])
  })

  it('shows the empty state when there is neither job nor dataset', async () => {
    renderPanel()

    await waitFor(() => {
      expect(screen.getByText('暂无历史任务')).toBeInTheDocument()
    })
    expect(screen.getByText('暂无数据集')).toBeInTheDocument()
  })

  it('shows the error state when the job query fails', async () => {
    vi.mocked(researchAPI.listHistoryJobs).mockRejectedValue({
      code: 'HTTP_503',
      message: 'History store unavailable',
    })

    renderPanel()

    await waitFor(
      () => {
        expect(screen.getByText('加载历史任务失败')).toBeInTheDocument()
      },
      { timeout: 5000 },
    )
  })

  it('creates one job per selected symbol', async () => {
    vi.mocked(researchAPI.createHistoryJob).mockResolvedValue(createJob())

    renderPanel()

    await userEvent.click(screen.getByRole('button', { name: '下载' }))

    await waitFor(() => {
      expect(vi.mocked(researchAPI.createHistoryJob).mock.calls.length).toBe(2)
    })
    const symbols = vi.mocked(researchAPI.createHistoryJob).mock.calls.map(call => call[0].symbol)
    expect(symbols).toEqual(['BTCUSDT', 'ETHUSDT'])
  })

  it('rejects an invalid month before calling the API', async () => {
    renderPanel()

    const monthInput = screen.getByLabelText('Archive month')
    await userEvent.clear(monthInput)
    await userEvent.type(monthInput, '2026-13')
    await userEvent.click(screen.getByRole('button', { name: '下载' }))

    await waitFor(() => {
      expect(screen.getByText('月份格式必须是 YYYY-MM')).toBeInTheDocument()
    })
    expect(vi.mocked(researchAPI.createHistoryJob)).not.toHaveBeenCalled()
  })

  it('offers a retry button for failed jobs and calls the retry endpoint', async () => {
    vi.mocked(researchAPI.listHistoryJobs).mockResolvedValue([
      createJob({ status: 'FAILED', stage: 'FAILED', error_code: 'NETWORK_ERROR' }),
    ])
    vi.mocked(researchAPI.retryHistoryJob).mockResolvedValue(createJob({ status: 'QUEUED' }))

    renderPanel()

    await waitFor(() => {
      expect(screen.getByText('重试')).toBeInTheDocument()
    })
    await userEvent.click(screen.getByText('重试'))

    await waitFor(() => {
      expect(vi.mocked(researchAPI.retryHistoryJob).mock.calls[0]?.[0]).toBe('hjob_1')
    })
  })

  it('shows a readable error label for a failed job', async () => {
    vi.mocked(researchAPI.listHistoryJobs).mockResolvedValue([
      createJob({ status: 'FAILED', error_code: 'ARCHIVE_NOT_FOUND' }),
    ])

    renderPanel()

    await waitFor(() => {
      expect(screen.getByText(/归档不存在/)).toBeInTheDocument()
    })
  })

  it('exposes the dataset for backtest only when it is eligible', async () => {
    vi.mocked(researchAPI.listHistoryDatasets).mockResolvedValue([
      createDataset({ dataset_id: 'hds_ok' }),
      createDataset({ dataset_id: 'hds_bad', backtest_eligible: false, persistence_ok: false }),
    ])
    const onUseForBacktest = vi.fn()

    renderPanel(onUseForBacktest)

    await waitFor(() => {
      expect(screen.getByText('用于回测')).toBeInTheDocument()
    })
    expect(screen.getByText('数据未完整')).toBeInTheDocument()

    await userEvent.click(screen.getByText('用于回测'))
    expect(onUseForBacktest).toHaveBeenCalledWith('hds_ok')
  })
})
