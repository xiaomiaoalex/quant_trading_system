import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ResearchBacktestForm } from '@/components/research/ResearchBacktestForm'
import type { ResearchDatasetSummary, ResearchStrategySummary } from '@/types'

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

const ineligibleDataset: ResearchDatasetSummary = {
  ...eligibleDataset,
  dataset_id: 'ds_ineligible',
  history_dataset_id: null,
  official_eligible: false,
  provenance_hash: null,
  published_at: null,
}

const strategy: ResearchStrategySummary = {
  module_path: 'trader.strategies.ema_cross_btc',
  strategy_id: 'ema_cross_btc',
  name: 'EMA Cross BTC',
  version: '1.0.0',
  code_hash: 'd'.repeat(64),
  params: [{ name: 'fast_period', type: 'int', default: '12', required: false }],
}

interface RenderOptions {
  datasets?: ResearchDatasetSummary[]
  strategies?: ResearchStrategySummary[]
  datasetsError?: string | null
  strategiesError?: string | null
  datasetsLoading?: boolean
  strategiesLoading?: boolean
  onSubmit?: (request: unknown) => void
}

function renderForm(options: RenderOptions = {}) {
  const onSubmit = options.onSubmit ?? vi.fn()
  render(
    <ResearchBacktestForm
      datasets={options.datasets ?? [eligibleDataset, ineligibleDataset]}
      strategies={options.strategies ?? [strategy]}
      datasetsLoading={options.datasetsLoading ?? false}
      datasetsError={options.datasetsError ?? null}
      strategiesLoading={options.strategiesLoading ?? false}
      strategiesError={options.strategiesError ?? null}
      isSubmitting={false}
      submitError={null}
      onSubmit={onSubmit}
    />,
  )
  return { onSubmit }
}

describe('ResearchBacktestForm', () => {
  it('blocks submission until a dataset and a strategy are selected', () => {
    renderForm()

    const submit = screen.getByRole('button', { name: '提交事件回测' })
    expect(submit).toBeDisabled()
    expect(screen.getByText('请选择一个 Research 数据集')).toBeInTheDocument()
    expect(screen.getByText('请选择一个已安装策略')).toBeInTheDocument()
  })

  it('disables submission for a dataset without official eligibility', async () => {
    renderForm()

    await userEvent.selectOptions(screen.getByLabelText('Research dataset'), 'ds_ineligible')
    await userEvent.selectOptions(
      screen.getByLabelText('Installed strategy'),
      'trader.strategies.ema_cross_btc',
    )

    const submit = screen.getByRole('button', { name: '提交事件回测' })
    expect(submit).toBeDisabled()
    expect(
      screen.getByText(/不具备官方资格（official_eligible=false），已禁止提交/),
    ).toBeInTheDocument()
  })

  it('submits the exact frozen manifest for an official eligible dataset', async () => {
    const { onSubmit } = renderForm()

    await userEvent.selectOptions(screen.getByLabelText('Research dataset'), 'ds_eligible')
    await userEvent.selectOptions(
      screen.getByLabelText('Installed strategy'),
      'trader.strategies.ema_cross_btc',
    )

    const submit = screen.getByRole('button', { name: '提交事件回测' })
    expect(submit).toBeEnabled()
    await userEvent.click(submit)

    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit).toHaveBeenCalledWith({
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

  it('keeps user-tuned capital, fee and slippage in the manifest', async () => {
    const { onSubmit } = renderForm()

    await userEvent.selectOptions(screen.getByLabelText('Research dataset'), 'ds_eligible')
    await userEvent.selectOptions(
      screen.getByLabelText('Installed strategy'),
      'trader.strategies.ema_cross_btc',
    )

    const cash = screen.getByLabelText('Initial cash')
    await userEvent.clear(cash)
    await userEvent.type(cash, '250000')

    const fee = screen.getByLabelText('Fee bps')
    await userEvent.clear(fee)
    await userEvent.type(fee, '7.5')

    const slippage = screen.getByLabelText('Slippage bps')
    await userEvent.clear(slippage)
    await userEvent.type(slippage, '3')

    await userEvent.click(screen.getByRole('button', { name: '提交事件回测' }))

    const request = vi.mocked(onSubmit).mock.calls[0]?.[0] as {
      manifest: { initial_cash: string; fee_bps: string; slippage_bps: string }
    }
    expect(request.manifest.initial_cash).toBe('250000')
    expect(request.manifest.fee_bps).toBe('7.5')
    expect(request.manifest.slippage_bps).toBe('3')
  })

  it('disables submission when the dataset eligibility query failed', async () => {
    renderForm({ datasets: [], datasetsError: 'HTTP_503: Research store unavailable' })

    await userEvent.selectOptions(
      screen.getByLabelText('Installed strategy'),
      'trader.strategies.ema_cross_btc',
    )

    expect(screen.getByRole('button', { name: '提交事件回测' })).toBeDisabled()
    expect(
      screen.getByText(/数据集资格查询失败，无法确认官方资格/),
    ).toBeInTheDocument()
  })

  it('blocks invalid cost inputs before calling the API', async () => {
    const { onSubmit } = renderForm()

    await userEvent.selectOptions(screen.getByLabelText('Research dataset'), 'ds_eligible')
    await userEvent.selectOptions(
      screen.getByLabelText('Installed strategy'),
      'trader.strategies.ema_cross_btc',
    )

    const cash = screen.getByLabelText('Initial cash')
    await userEvent.clear(cash)
    await userEvent.type(cash, 'abc')

    expect(screen.getByRole('button', { name: '提交事件回测' })).toBeDisabled()
    expect(
      screen.getByText('初始资金必须是十进制数字（例如 100000）'),
    ).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
  })
})
