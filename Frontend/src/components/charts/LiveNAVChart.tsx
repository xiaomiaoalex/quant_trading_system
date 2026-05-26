import { useState, useCallback } from 'react'
import { EquityCurveChart } from './EquityCurveChart'
import { useDeploymentNAV } from '@/hooks/useStrategies'
import { useSSE } from '@/hooks/useSSE'
import type { NAVPoint } from '@/types'

interface LiveNAVChartProps {
  deploymentId: string
  strategyId: string
  height?: number
}

interface NavUpdatePayload {
  deployment_id?: string
  strategy_id?: string
  nav_point?: {
    timestamp_ms: number
    equity: number
    cash?: number
    unrealized_pnl?: number
    realized_pnl?: number
    total_pnl?: number
  }
}

const MAX_LIVE_POINTS = 1000

export function LiveNAVChart({ deploymentId, strategyId, height = 100 }: LiveNAVChartProps) {
  const { data: historicalData } = useDeploymentNAV(deploymentId)
  const [livePoints, setLivePoints] = useState<NAVPoint[]>([])

  const onMessage = useCallback(
    (eventType: string, raw: unknown) => {
      if (eventType !== 'nav_update') return
      const payload = raw as NavUpdatePayload
      // 兼容后端 fallback：deployment_id 不匹配时检查 strategy_id
      if (!payload?.nav_point) return
      if (payload.deployment_id !== deploymentId && payload.strategy_id !== strategyId) return
      const p = payload.nav_point
      setLivePoints((prev) => {
        const next = [
          ...prev,
          {
            deployment_id: deploymentId,
            strategy_id: strategyId,
            timestamp_ms: p.timestamp_ms,
            equity: p.equity,
            cash: p.cash ?? 0,
            unrealized_pnl: p.unrealized_pnl ?? 0,
            realized_pnl: p.realized_pnl ?? 0,
            total_pnl: p.total_pnl ?? 0,
          },
        ]
        // Rolling window: keep last MAX_LIVE_POINTS to prevent unbounded memory growth
        return next.length > MAX_LIVE_POINTS ? next.slice(-MAX_LIVE_POINTS) : next
      })
    },
    [deploymentId, strategyId],
  )

  // 后端以 strategy_id 广播，前端按 strategy_id 订阅，再按 deployment_id 过滤
  useSSE([`nav:${strategyId}`], undefined, { onMessage })

  // 去重：historicalData 可能已经包含 SSE 推送的最新点
  const lastHistoricalTs = historicalData?.at(-1)?.timestamp_ms ?? 0
  const dedupedLive = livePoints.filter((p) => p.timestamp_ms > lastHistoricalTs)
  const combined: NAVPoint[] = [...(historicalData ?? []), ...dedupedLive]

  if (combined.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-gray-500 text-xs"
        style={{ height }}
      >
        暂无净值数据
      </div>
    )
  }

  const chartData = combined.map((p) => ({
    timestamp: p.timestamp_ms,
    equity: p.equity,
  }))

  return <EquityCurveChart data={chartData} height={height} />
}
