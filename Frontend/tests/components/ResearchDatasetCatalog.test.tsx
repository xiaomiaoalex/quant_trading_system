import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ResearchDatasetCatalog } from '@/components/research/ResearchDatasetCatalog'
import type { ResearchDatasetSummary } from '@/types'

function createDataset(overrides: Partial<ResearchDatasetSummary> = {}): ResearchDatasetSummary {
  return {
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
    ...overrides,
  }
}

const baseProps = {
  isLoading: false,
  isError: false,
  errorMessage: null,
  onRetry: vi.fn(),
}

describe('ResearchDatasetCatalog', () => {
  it('renders the loading state', () => {
    render(<ResearchDatasetCatalog {...baseProps} datasets={[]} isLoading />)

    expect(screen.getByText('Loading Research datasets...')).toBeInTheDocument()
  })

  it('renders the empty state', () => {
    render(<ResearchDatasetCatalog {...baseProps} datasets={[]} />)

    expect(screen.getByText('暂无 Research 数据集')).toBeInTheDocument()
  })

  it('renders the error state and retries', () => {
    const onRetry = vi.fn()
    render(
      <ResearchDatasetCatalog
        {...baseProps}
        datasets={[]}
        isError
        errorMessage="HTTP_503: Research store unavailable"
        onRetry={onRetry}
      />,
    )

    expect(screen.getByText('加载 Research 数据集失败')).toBeInTheDocument()
    screen.getByRole('button', { name: 'Retry' }).click()
    expect(onRetry).toHaveBeenCalled()
  })

  it('marks official eligibility and explains ineligible datasets', () => {
    render(
      <ResearchDatasetCatalog
        {...baseProps}
        datasets={[
          createDataset({ dataset_id: 'ds_ok' }),
          createDataset({
            dataset_id: 'ds_no_link',
            history_dataset_id: null,
            official_eligible: false,
          }),
          createDataset({
            dataset_id: 'ds_incomplete',
            history_dataset_id: 'hds_2',
            complete: false,
            official_eligible: false,
          }),
        ]}
      />,
    )

    expect(screen.getAllByText('官方合格')).toHaveLength(1)
    expect(screen.getAllByText('资格不足')).toHaveLength(2)
    expect(
      screen.getByText('未关联历史归档（尚未由归档任务发布）'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('数据不完整（质量校验未通过）'),
    ).toBeInTheDocument()
    expect(screen.getByText('ds_ok')).toBeInTheDocument()
  })

  it('shows a stale warning when cached data is present but refresh failed', () => {
    render(
      <ResearchDatasetCatalog
        {...baseProps}
        datasets={[createDataset()]}
        isError
        errorMessage="HTTP_500: upstream failure"
      />,
    )

    expect(
      screen.getByText(/数据集目录刷新失败，当前展示的是可能已过期的缓存数据/),
    ).toBeInTheDocument()
  })
})
