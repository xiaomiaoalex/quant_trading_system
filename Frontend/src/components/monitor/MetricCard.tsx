import { clsx } from 'clsx'

interface MetricCardProps {
  title: string
  value: string | number
  subValue?: string
  trend?: 'up' | 'down' | 'neutral'
  trendValue?: string
  isLoading?: boolean
  className?: string
}

export function MetricCard({
  title,
  value,
  subValue,
  trend,
  trendValue,
  isLoading = false,
  className,
}: MetricCardProps) {
  return (
    <div
      className={clsx(
        'rounded-lg border border-gray-700/50 bg-gray-800/50 p-4 card-hover',
        className
      )}
    >
      <div className="flex items-start justify-between">
        <p className="text-sm font-medium text-gray-400">{title}</p>
        {trend && <TrendIndicator trend={trend} value={trendValue} />}
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        {isLoading ? (
          <div className="h-8 w-24 animate-pulse rounded bg-gray-700" />
        ) : (
          <p className="text-2xl font-semibold text-white">{value}</p>
        )}
        {subValue && <p className="text-sm text-gray-500">{subValue}</p>}
      </div>
    </div>
  )
}

function TrendIndicator({ trend, value }: { trend: 'up' | 'down' | 'neutral'; value?: string }) {
  if (!value) return null

  const config = {
    up: { color: 'text-green-400', icon: '↑' },
    down: { color: 'text-red-400', icon: '↓' },
    neutral: { color: 'text-gray-400', icon: '→' },
  }

  return (
    <span className={clsx('flex items-center gap-0.5 text-sm font-medium', config[trend].color)}>
      {config[trend].icon} {value}
    </span>
  )
}
