import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'

interface EquityPoint {
  timestamp: number
  equity: number
}

interface EquityCurveChartProps {
  data: EquityPoint[]
  height?: number
}

function formatDate(ts: number): string {
  const d = new Date(ts)
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
}

function formatEquity(value: number): string {
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`
  if (value >= 1_000) return `$${(value / 1_000).toFixed(1)}k`
  return `$${value.toFixed(2)}`
}

export function EquityCurveChart({ data, height = 200 }: EquityCurveChartProps) {
  if (!data || data.length === 0) return null

  const minEquity = Math.min(...data.map(d => d.equity))
  const maxEquity = Math.max(...data.map(d => d.equity))
  const padding = (maxEquity - minEquity) * 0.05 || maxEquity * 0.01

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
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
          domain={[minEquity - padding, maxEquity + padding]}
          tickFormatter={formatEquity}
          tick={{ fill: '#9CA3AF', fontSize: 10 }}
          axisLine={{ stroke: '#4B5563' }}
          tickLine={false}
          width={60}
        />
        <Tooltip
          contentStyle={{ backgroundColor: '#1F2937', border: '1px solid #374151', borderRadius: '6px' }}
          labelStyle={{ color: '#9CA3AF', fontSize: 11 }}
          itemStyle={{ color: '#34D399', fontSize: 12 }}
          labelFormatter={(v) => formatDate(Number(v))}
          formatter={(v) => [formatEquity(Number(v)), 'Equity']}
        />
        <Line
          type="monotone"
          dataKey="equity"
          stroke="#34D399"
          strokeWidth={1.5}
          dot={false}
          activeDot={{ r: 3, fill: '#34D399' }}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
