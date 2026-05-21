import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts'

interface EquityPoint {
  timestamp: number
  equity: number
}

interface DrawdownPoint {
  timestamp: number
  drawdown: number
}

interface DrawdownChartProps {
  data: EquityPoint[]
  height?: number
}

function computeDrawdown(data: EquityPoint[]): DrawdownPoint[] {
  let peak = -Infinity
  return data.map(({ timestamp, equity }) => {
    if (equity > peak) peak = equity
    const drawdown = peak > 0 ? ((equity - peak) / peak) * 100 : 0
    return { timestamp, drawdown }
  })
}

function formatDate(ts: number): string {
  const d = new Date(ts)
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
}

export function DrawdownChart({ data, height = 160 }: DrawdownChartProps) {
  if (!data || data.length === 0) return null

  const ddData = computeDrawdown(data)
  const minDD = Math.min(...ddData.map(d => d.drawdown))
  const domainMin = Math.min(minDD * 1.1, -0.5)

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={ddData} margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
        <defs>
          <linearGradient id="ddGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#EF4444" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#EF4444" stopOpacity={0.05} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
        <XAxis
          dataKey="timestamp"
          tickFormatter={formatDate}
          tick={{ fill: '#9CA3AF', fontSize: 10 }}
          axisLine={{ stroke: '#4B5563' }}
          tickLine={false}
          minTickGap={60}
        />
        <YAxis
          domain={[domainMin, 0]}
          tickFormatter={(v) => `${v.toFixed(1)}%`}
          tick={{ fill: '#9CA3AF', fontSize: 10 }}
          axisLine={{ stroke: '#4B5563' }}
          tickLine={false}
          width={52}
        />
        <Tooltip
          contentStyle={{ backgroundColor: '#1F2937', border: '1px solid #374151', borderRadius: '6px' }}
          labelStyle={{ color: '#9CA3AF', fontSize: 11 }}
          itemStyle={{ color: '#EF4444', fontSize: 12 }}
          labelFormatter={(v) => formatDate(Number(v))}
          formatter={(v) => [`${Number(v).toFixed(2)}%`, 'Drawdown']}
        />
        <ReferenceLine y={0} stroke="#4B5563" strokeDasharray="3 3" />
        <Area
          type="monotone"
          dataKey="drawdown"
          stroke="#EF4444"
          strokeWidth={1.5}
          fill="url(#ddGradient)"
          dot={false}
          activeDot={{ r: 3, fill: '#EF4444' }}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
