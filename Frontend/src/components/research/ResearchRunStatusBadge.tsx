import { clsx } from 'clsx'
import type { ResearchBacktestStatus } from '@/types'

const RESEARCH_RUN_STATUS_DISPLAY: Record<
  ResearchBacktestStatus,
  { label: string; bgClass: string; textClass: string }
> = {
  QUEUED: { label: '排队中', bgClass: 'bg-gray-700', textClass: 'text-gray-300' },
  RUNNING: { label: '运行中', bgClass: 'bg-blue-950/40', textClass: 'text-blue-300' },
  SUCCEEDED: { label: '成功', bgClass: 'bg-emerald-950/40', textClass: 'text-emerald-300' },
  FAILED: { label: '失败', bgClass: 'bg-red-950/40', textClass: 'text-red-300' },
  CANCELLED: { label: '已取消', bgClass: 'bg-gray-700', textClass: 'text-gray-400' },
}

interface ResearchRunStatusBadgeProps {
  status: ResearchBacktestStatus
}

export function ResearchRunStatusBadge({ status }: ResearchRunStatusBadgeProps) {
  const config = RESEARCH_RUN_STATUS_DISPLAY[status] ?? {
    label: status,
    bgClass: 'bg-gray-700',
    textClass: 'text-gray-300',
  }
  return (
    <span
      className={clsx('rounded px-2 py-1 text-xs font-medium', config.bgClass, config.textClass)}
    >
      {config.label}
    </span>
  )
}
