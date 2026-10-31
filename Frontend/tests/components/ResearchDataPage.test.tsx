import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ResearchData } from '@/pages/ResearchData'
import { researchAPI } from '@/api'
import type { ResearchDatasetSummary } from '@/types'

vi.mock('@/api', async importOriginal => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return {
    ...actual,
    researchAPI: {
      listResearchDatasets: vi.fn(),
      listHistoryJobs: vi.fn(),
      listHistoryDatasets: vi.fn(),
      createHistoryJob: vi.fn(),
      retryHistoryJob: vi.fn(),
    },
  }
})

const dataset: ResearchDatasetSummary = {
  dataset_id: 'ds_catalog',
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

describe('ResearchData page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(researchAPI.listResearchDatasets).mockResolvedValue([dataset])
    vi.mocked(researchAPI.listHistoryJobs).mockResolvedValue([])
    vi.mocked(researchAPI.listHistoryDatasets).mockResolvedValue([])
  })

  it('shows the Research dataset catalog and reuses the archive import capability', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <ResearchData />
      </QueryClientProvider>,
    )

    expect(await screen.findByText('Research 数据集目录')).toBeInTheDocument()
    expect(await screen.findByText('ds_catalog')).toBeInTheDocument()
    expect(screen.getByText('官方合格')).toBeInTheDocument()

    // 复用既有历史归档导入能力（下载 / 校验 / 发布）
    expect(screen.getByText('历史归档导入')).toBeInTheDocument()
    expect(await screen.findByText('暂无历史任务')).toBeInTheDocument()
    expect(await screen.findByText('暂无数据集')).toBeInTheDocument()
  })
})
