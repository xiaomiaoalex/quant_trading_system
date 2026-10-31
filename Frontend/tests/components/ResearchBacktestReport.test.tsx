import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ResearchBacktestReport } from '@/components/research/ResearchBacktestReport'
import type {
  ResearchBacktestReport as ResearchBacktestReportData,
  ResearchBacktestRun,
  ResearchDatasetSummary,
} from '@/types'

const archiveSha = 'a'.repeat(64)
const officialSha = 'b'.repeat(64)
const provenanceHash = 'c'.repeat(64)
const codeHash = 'd'.repeat(64)
const manifestHash = 'e'.repeat(64)
const runtimeManifestHash = 'f'.repeat(64)

const catalogDataset: ResearchDatasetSummary = {
  dataset_id: 'ds_report',
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
  archive_sha256: archiveSha,
  official_sha256: officialSha,
  source_url: 'https://data.binance.vision/BTCUSDT-1m-2026-09.zip',
  checksum_url: 'https://data.binance.vision/BTCUSDT-1m-2026-09.zip.CHECKSUM',
  provenance_hash: provenanceHash,
  conversion_version: 'binance_vision_1m_v1',
  published_at: '2026-10-01T00:00:00Z',
  created_at: '2026-10-01T00:00:00Z',
}

const report: ResearchBacktestReportData = {
  datasets: [
    {
      dataset_id: 'ds_report',
      identity: {
        venue: 'BINANCE',
        market_type: 'SPOT',
        symbol: 'BTCUSDT',
        base_asset: 'BTC',
        quote_asset: 'USDT',
        interval: '1m',
        source: 'binance_vision',
        version: 'binance_vision_1m_v1_aaaaaaaaaaaa',
        schema_version: 1,
        interval_ms: 60000,
      },
      quality: {
        complete: true,
        bar_count: 43200,
        coverage_start_ms: 1788220800000,
        coverage_end_ms: 1790812740000,
      },
    },
  ],
  provenance: { ds_report: provenanceHash },
  strategy: {
    module_path: 'trader.strategies.ema_cross_btc',
    params: { fast_period: 12 },
    version: '1.0.0',
    code_hash: codeHash,
  },
  runtime: {
    engine: 'event_runtime',
    runtime_version: 'spot-runtime-1',
    manifest_hash: manifestHash,
    runtime_manifest_hash: runtimeManifestHash,
  },
  costs: {
    initial_cash: '100000',
    fee_bps: '10',
    slippage_bps: '5',
    total_fees: '123.45',
  },
  performance: {
    net_value: '101234.56',
    total_return: '0.0123',
    max_drawdown_pct: '0.045',
    max_drawdown_amount: '456.7',
    win_rate: '0.55',
  },
  executions: {
    fill_count: 12,
    rejections: { INSUFFICIENT_CASH: 2 },
    execution_rejections: { PRICE_UNAVAILABLE: 1 },
  },
  limitations: ['BAR data cannot validate queue priority or market impact.'],
}

const succeededRun: ResearchBacktestRun = {
  run_id: 'bt_report',
  status: 'SUCCEEDED',
  model: 'event_runtime',
  manifest: { dataset_ids: ['ds_report'] },
  result: {
    engine: 'event_runtime',
    runtime_version: 'spot-runtime-1',
    admissible: true,
    manifest_hash: manifestHash,
    runtime_manifest_hash: runtimeManifestHash,
    limitations: ['BAR data cannot validate queue priority or market impact.'],
    net_value: '101234.56',
    total_return: '0.0123',
    total_fees: '123.45',
    max_drawdown: '0.045',
    max_drawdown_pct: '0.045',
    max_drawdown_amount: '456.7',
    win_rate: '0.55',
    equity_curve: ['100000', '101234.56'],
    equity_points: [{ at_ms: 1788220800000, equity: '100000' }],
    positions: {},
    fills: [],
    rejections: { INSUFFICIENT_CASH: 2 },
    execution_rejections: { PRICE_UNAVAILABLE: 1 },
    closed_trades: ['10', '-5'],
    errors: [],
    report,
  },
  error: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:10:00Z',
}

describe('ResearchBacktestReport', () => {
  it('renders the full event runtime report', () => {
    render(<ResearchBacktestReport run={succeededRun} datasetCatalog={[catalogDataset]} />)

    // 数据集身份与月覆盖
    expect(screen.getByText('数据集身份与月覆盖')).toBeInTheDocument()
    expect(screen.getAllByText('ds_report').length).toBeGreaterThan(0)
    expect(screen.getByText('BTCUSDT')).toBeInTheDocument()
    expect(screen.getByText('2026-09')).toBeInTheDocument()
    expect(
      screen.getByText('2026-09-01T00:00:00.000Z → 2026-09-30T23:59:00.000Z'),
    ).toBeInTheDocument()
    expect(screen.getAllByText('43200').length).toBeGreaterThan(0)

    // 来源与 SHA
    expect(screen.getByText('来源与 SHA')).toBeInTheDocument()
    expect(
      screen.getByText('https://data.binance.vision/BTCUSDT-1m-2026-09.zip'),
    ).toBeInTheDocument()
    expect(screen.getByText(archiveSha)).toBeInTheDocument()
    expect(screen.getByText(officialSha)).toBeInTheDocument()
    expect(screen.getByText(provenanceHash)).toBeInTheDocument()

    // 质量
    expect(screen.getByText('数据质量')).toBeInTheDocument()

    // 策略源码 hash / runtime / 成本 / 收益与回撤 / 成交与拒绝 / 限制说明
    expect(screen.getByText(codeHash)).toBeInTheDocument()
    expect(screen.getAllByText('spot-runtime-1').length).toBeGreaterThan(0)
    expect(screen.getByText('101234.56')).toBeInTheDocument()
    expect(screen.getByText('0.0123')).toBeInTheDocument()
    expect(screen.getByText('456.7')).toBeInTheDocument()
    expect(screen.getByText('0.55')).toBeInTheDocument()
    expect(screen.getByText('INSUFFICIENT_CASH: 2')).toBeInTheDocument()
    expect(screen.getByText('PRICE_UNAVAILABLE: 1')).toBeInTheDocument()
    expect(
      screen.getAllByText('BAR data cannot validate queue priority or market impact.'),
    ).toHaveLength(1)
    expect(screen.getByText('admissible')).toBeInTheDocument()
    expect(screen.getByText('成交与拒绝统计')).toBeInTheDocument()
    expect(screen.getByText('限制说明')).toBeInTheDocument()
  })

  it('shows the failure reason for a FAILED run', () => {
    render(
      <ResearchBacktestReport
        run={{
          ...succeededRun,
          status: 'FAILED',
          result: null,
          error: 'Frozen provenance mismatch: ds_report',
        }}
      />,
    )

    expect(screen.getByText('运行失败')).toBeInTheDocument()
    expect(screen.getByText('Frozen provenance mismatch: ds_report')).toBeInTheDocument()
    expect(screen.getByText(/仅 FAILED|冻结输入保持不变|重试/)).toBeInTheDocument()
  })

  it('shows the running state without a report', () => {
    render(<ResearchBacktestReport run={{ ...succeededRun, status: 'RUNNING', result: null }} />)

    expect(screen.getByText(/正在按事件时间回放冻结数据集/)).toBeInTheDocument()
    expect(screen.queryByText('运行报告')).toBeInTheDocument()
    expect(screen.queryByText('数据集身份与月覆盖')).not.toBeInTheDocument()
  })

  it('warns when a successful legacy run has no event report', () => {
    render(
      <ResearchBacktestReport
        run={{ ...succeededRun, model: 'legacy', result: null }}
      />,
    )

    expect(screen.getByText(/该运行缺少事件回测报告/)).toBeInTheDocument()
    expect(screen.queryByText('数据集身份与月覆盖')).not.toBeInTheDocument()
  })
})
