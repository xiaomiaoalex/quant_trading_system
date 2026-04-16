import { clsx } from 'clsx'
import type { BacktestStatus } from '@/types'
import { BACKTEST_STATUS_DISPLAY } from '@/types'

interface BacktestStatusBadgeProps {
  status: BacktestStatus
}

export function BacktestStatusBadge({ status }: BacktestStatusBadgeProps) {
  const config = BACKTEST_STATUS_DISPLAY[status] ?? BACKTEST_STATUS_DISPLAY.PENDING

  return (
    <span
      className={clsx(
        'inline-flex rounded-full px-2 py-0.5 text-xs font-medium',
        config.color,
        config.bgColor
      )}
    >
      {config.label}
    </span>
  )
}