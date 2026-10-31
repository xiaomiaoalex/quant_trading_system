import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { ResearchBacktests } from '@/pages/ResearchBacktests'
import { researchAPI } from '@/api'
import type {
  ResearchBacktestRun,
  ResearchDatasetSummary,
  ResearchStrategySummary,
} from '@/types'

vi.mock('@/api', async importOriginal => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return {
    ...actual,
    researchAPI: {
      listResearchDatasets: vi.fn(),
      listResearchStrategies: vi.fn(),
      listResearchBacktests: vi.fn(),
      getResearchBacktest: vi.fn(),
      createResearchBacktest: vi.fn(),
      retryResearchBacktest: vi.fn(),
    },
  }
})

const eligibleDataset: ResearchDatasetSummary = {
  dataset_id: 'ds_eligible',
  symbol: 'BTCUSDT',
  interval: '1m',
  month: '2026-09',
  venue: 'BINANCE',
  market_type: 'SPOT',
  base_asset: 'BTC',
  quote_asset: 'USDT',
  source: 'binance_vision',
  version: 'binance_vision_1m_v1_aaaaaaaaaaaa',
  bar_count: 43200,
  coverage_start_ms: 1788220800000,
  coverage_end_ms: 1790812740000,
  complete: true,
  history_dataset_id: 'hds_1',
  official_eligible: true,
  archive_sha256: 'a'.repeat(64),
  official_sha256: 'a'.repeat(64),
  source_url: 'https://example/a.zip',
  checksum_url: 'https://example/a.zip.CHECKSUM',
  provenance_hash: 'p'.repeat(64),
  conversion_version: 'binance_vision_1m_v1',
  published_at: '2026-10-01T00:00:00Z',
  created_at: '2026-10-01T00:00:00Z',
}

const strategy: ResearchStrategySummary = {
  module_path: 'trader.strategies.ema_cross_btc',
  strategy_id: 'ema_cross_btc',
  name: 'EMA Cross BTC',
  version: '1.0.0',
  code_hash: 'd'.repeat(64),
  params: [],
}

function createRun(overrides: Partial<ResearchBacktestRun> = {}): ResearchBacktestRun {
  return {
    run_id: 'bt_running',
    status: 'RUNNING',
    model: 'event_runtime',
    manifest: {
      dataset_ids: ['ds_eligible'],
      strategy: { module_path: 'trader.strategies.ema_cross_btc' },
    },
    result: null,
    error: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  }
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>
}

function renderPage(entry = '/research/backtests?run_id=bt_running') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <ResearchBacktests />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('ResearchBacktests page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(researchAPI.listResearchDatasets).mockResolvedValue([eligibleDataset])
    vi.mocked(researchAPI.listResearchStrategies).mockResolvedValue([strategy])
    vi.mocked(researchAPI.listResearchBacktests).mockResolvedValue([createRun()])
    vi.mocked(researchAPI.getResearchBacktest).mockImplementation(async (runId: string) =>
      runId === 'bt_new'
        ? createRun({ run_id: 'bt_new', status: 'QUEUED' })
        : createRun({ run_id: runId }),
    )
    vi.mocked(researchAPI.createResearchBacktest).mockResolvedValue(
      createRun({ run_id: 'bt_new', status: 'QUEUED' }),
    )
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('restores the run from the URL run_id after a refresh', async () => {
    renderPage('/research/backtests?run_id=bt_running')

    await waitFor(() => {
      expect(vi.mocked(researchAPI.getResearchBacktest)).toHaveBeenCalledWith('bt_running')
    })
    expect(
      await screen.findByText(/正在按事件时间回放冻结数据集/),
    ).toBeInTheDocument()
  })

  it('submits the official-eligible dataset with the frozen manifest and selects the new run', async () => {
    renderPage('/research/backtests')

    const datasetOption = await screen.findByRole('option', { name: /ds_eligible/ })
    expect(datasetOption).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Research dataset'), 'ds_eligible')
    await userEvent.selectOptions(
      screen.getByLabelText('Installed strategy'),
      'trader.strategies.ema_cross_btc',
    )
    await userEvent.click(screen.getByRole('button', { name: '提交事件回测' }))

    await waitFor(() => {
      expect(vi.mocked(researchAPI.createResearchBacktest)).toHaveBeenCalledWith({
        manifest: {
          dataset_ids: ['ds_eligible'],
          strategy: { module_path: 'trader.strategies.ema_cross_btc' },
          initial_cash: '100000',
          fee_bps: '10',
          slippage_bps: '5',
          engine: 'event_runtime',
          purpose: 'research_screening',
        },
      })
    })

    // 提交后 run_id 写入 URL（刷新后可恢复），详情区展示新运行。
    await waitFor(() => {
      expect(screen.getByTestId('location').textContent).toBe(
        '/research/backtests?run_id=bt_new',
      )
    })
    expect(await screen.findByText('已提交事件回测：bt_new')).toBeInTheDocument()
  })
})
