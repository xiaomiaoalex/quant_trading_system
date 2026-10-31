import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ResearchBacktestRunList } from '@/components/research/ResearchBacktestRunList'
import type { ResearchBacktestRun } from '@/types'

function createRun(overrides: Partial<ResearchBacktestRun> = {}): ResearchBacktestRun {
  return {
    run_id: 'bt_1',
    status: 'QUEUED',
    model: 'event_runtime',
    manifest: {
      dataset_ids: ['ds_1'],
      strategy: { module_path: 'trader.strategies.ema_cross_btc' },
    },
    result: null,
    error: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  }
}

const baseProps = {
  selectedRunId: null,
  onSelect: vi.fn(),
  onRetry: vi.fn(),
  isLoading: false,
  isError: false,
  errorMessage: null,
  onReload: vi.fn(),
}

describe('ResearchBacktestRunList', () => {
  it('renders the empty state', () => {
    render(<ResearchBacktestRunList {...baseProps} runs={[]} />)

    expect(screen.getByText('暂无回测运行')).toBeInTheDocument()
  })

  it('offers explicit retry only for FAILED runs', async () => {
    const onRetry = vi.fn()
    render(
      <ResearchBacktestRunList
        {...baseProps}
        onRetry={onRetry}
        runs={[
          createRun({ run_id: 'bt_failed', status: 'FAILED', error: 'boom' }),
          createRun({ run_id: 'bt_running', status: 'RUNNING' }),
        ]}
      />,
    )

    expect(screen.getAllByText('重试')).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(onRetry).toHaveBeenCalledWith('bt_failed')
  })

  it('selects a run when the user asks for the report', async () => {
    const onSelect = vi.fn()
    render(
      <ResearchBacktestRunList
        {...baseProps}
        onSelect={onSelect}
        runs={[createRun({ run_id: 'bt_selected', status: 'SUCCEEDED' })]}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: '查看报告' }))
    expect(onSelect).toHaveBeenCalledWith('bt_selected')
  })

  it('explains that polling stopped once all runs are terminal', () => {
    render(
      <ResearchBacktestRunList
        {...baseProps}
        runs={[createRun({ run_id: 'bt_done', status: 'SUCCEEDED' })]}
      />,
    )

    expect(screen.getByText('全部运行已终态，轮询已停止')).toBeInTheDocument()
  })

  it('shows a stale warning when the list refresh failed but cached runs exist', () => {
    render(
      <ResearchBacktestRunList
        {...baseProps}
        runs={[createRun({ status: 'RUNNING' })]}
        isError
        errorMessage="HTTP_500: upstream failure"
      />,
    )

    expect(
      screen.getByText(/运行列表刷新失败，当前展示的是可能已过期的缓存数据/),
    ).toBeInTheDocument()
  })
})
