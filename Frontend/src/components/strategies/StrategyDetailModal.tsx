import { useState } from 'react'
import { clsx } from 'clsx'
import type { RegisteredStrategy, StrategyRuntimeInfo, StrategyEventEnvelope } from '@/types'
import { STRATEGY_STATUS_DISPLAY } from '@/types'
import type { StrategyStatus } from '@/types'
import { useStrategySignals, useStrategyErrors, useStrategyEvents } from '@/hooks'

interface StrategyDetailModalProps {
  strategy: RegisteredStrategy
  runtime: StrategyRuntimeInfo | null
  isOpen: boolean
  onClose: () => void
}

function formatTimestamp(timestamp: string | null | undefined): string {
  if (!timestamp) return '-'
  try {
    return new Date(timestamp).toLocaleString()
  } catch {
    return timestamp
  }
}

function formatEventTime(tsMs: number): string {
  try {
    return new Date(tsMs).toLocaleTimeString()
  } catch {
    return String(tsMs)
  }
}

function getEventTypeLabel(eventType: string): { label: string; color: string } {
  switch (eventType) {
    case 'strategy.signal': return { label: 'Signal', color: 'text-blue-400' }
    case 'strategy.order.submitted': return { label: 'Order', color: 'text-green-400' }
    case 'strategy.order.filled': return { label: 'Filled', color: 'text-emerald-400' }
    case 'strategy.order.cancelled': return { label: 'Cancelled', color: 'text-yellow-400' }
    case 'strategy.order.rejected': return { label: 'Rejected', color: 'text-red-400' }
    case 'strategy.error': return { label: 'Error', color: 'text-red-400' }
    case 'strategy.tick': return { label: 'Tick', color: 'text-gray-400' }
    default: return { label: eventType, color: 'text-gray-400' }
  }
}

function SignalEventRow({ event }: { event: StrategyEventEnvelope }) {
  const payload = event.payload as { symbol?: string; direction?: string; signal_type?: string; reason?: string }
  return (
    <div className="flex items-start gap-3 py-2 border-b border-gray-800 last:border-0">
      <span className="text-xs text-gray-500 mt-0.5 shrink-0">{formatEventTime(event.ts_ms)}</span>
      <span className={clsx('text-xs font-medium px-1.5 py-0.5 rounded', getEventTypeLabel(event.event_type).color, 'bg-gray-700/50')}>
        {getEventTypeLabel(event.event_type).label}
      </span>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-white">
          {payload.symbol} {payload.direction}
        </p>
        <p className="text-xs text-gray-400">{payload.signal_type} - {payload.reason}</p>
      </div>
    </div>
  )
}

function ErrorEventRow({ event }: { event: StrategyEventEnvelope }) {
  const payload = event.payload as Record<string, unknown>

  // Try multiple possible error message fields
  const errorMsg = (
    payload.error_message ??
    payload.reason ??
    payload.message ??
    payload.error ??
    JSON.stringify(payload)
  ) as string

  return (
    <div className="flex items-start gap-3 py-2 border-b border-gray-800 last:border-0">
      <span className="text-xs text-gray-500 mt-0.5 shrink-0">{formatEventTime(event.ts_ms)}</span>
      <span className="text-xs font-medium px-1.5 py-0.5 rounded text-red-400 bg-red-900/30">Error</span>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-red-300 break-words">{errorMsg}</p>
      </div>
    </div>
  )
}

function OrderEventRow({ event }: { event: StrategyEventEnvelope }) {
  const payload = event.payload as {
    order_id?: string
    symbol?: string
    side?: string
    quantity?: string
    filled_qty?: string
    avg_price?: string
    price?: string
    status?: string
    reason?: string
  }

  const eventConfig = getEventTypeLabel(event.event_type)
  const isRejected = event.event_type === 'strategy.order.rejected'
  const isFilled = event.event_type === 'strategy.order.filled'
  const isSubmitted = event.event_type === 'strategy.order.submitted'

  const shortOrderId = payload.order_id ? payload.order_id.slice(-12) : '-'
  const fillPercent = payload.quantity && payload.filled_qty
    ? Math.min(100, Math.round((parseFloat(payload.filled_qty) / parseFloat(payload.quantity)) * 100))
    : null

  return (
    <div className="flex items-start gap-3 py-2 border-b border-gray-800 last:border-0">
      <span className="text-xs text-gray-500 mt-0.5 shrink-0">{formatEventTime(event.ts_ms)}</span>
      <span className={clsx('text-xs font-medium px-1.5 py-0.5 rounded shrink-0', eventConfig.color, 'bg-gray-700/50')}>
        {eventConfig.label}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm text-white font-medium">{payload.symbol}</span>
          <span className={clsx(
            'text-xs font-medium px-1.5 py-0.5 rounded',
            payload.side === 'BUY' ? 'text-green-400 bg-green-900/30' : 'text-red-400 bg-red-900/30'
          )}>
            {payload.side}
          </span>
          {payload.status && (
            <span className={clsx('text-xs px-1.5 py-0.5 rounded', 'bg-gray-700/50 text-gray-300')}>
              {payload.status}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 mt-1 text-xs text-gray-400 flex-wrap">
          <span>Qty: {payload.quantity ?? '-'}</span>
          {(isFilled || isSubmitted) && payload.filled_qty && (
            <span className={clsx(fillPercent === 100 ? 'text-emerald-400' : 'text-yellow-400')}>
              Filled: {payload.filled_qty}
              {fillPercent !== null && ` (${fillPercent}%)`}
            </span>
          )}
          {(isFilled || isSubmitted) && payload.avg_price && (
            <span>Avg: {payload.avg_price}</span>
          )}
          {isRejected && payload.reason && (
            <span className="text-red-400">Reason: {payload.reason}</span>
          )}
          <span className="text-gray-500 font-mono">#{shortOrderId}</span>
        </div>
      </div>
    </div>
  )
}

type Tab = 'info' | 'orders' | 'signals' | 'errors'

export function StrategyDetailModal({ strategy, runtime, isOpen, onClose }: StrategyDetailModalProps) {
  const [activeTab, setActiveTab] = useState<Tab>('info')

  const { data: signals } = useStrategySignals(runtime?.deployment_id ?? '')
  const { data: errors } = useStrategyErrors(runtime?.deployment_id ?? '')
  const { data: allEvents } = useStrategyEvents(runtime?.deployment_id ?? '')

  const orders = allEvents?.filter(e =>
    e.event_type.startsWith('strategy.order.')
  ) ?? []

  if (!isOpen) return null

  const status = runtime?.status ?? 'stopped'
  const statusConfig = STRATEGY_STATUS_DISPLAY[status as StrategyStatus] ?? STRATEGY_STATUS_DISPLAY.stopped

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: 'info', label: 'Info' },
    { id: 'orders', label: 'Orders', count: orders.length },
    { id: 'signals', label: 'Signals', count: signals?.length },
    { id: 'errors', label: 'Errors', count: errors?.length },
  ]

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 w-full max-w-3xl rounded-lg border border-gray-700 bg-gray-800 shadow-xl">
        <div className="flex items-center justify-between border-b border-gray-700 px-6 py-4">
          <h2 className="text-lg font-semibold text-white">Strategy Details</h2>
          <button
            onClick={onClose}
            className="rounded p-1 text-gray-400 hover:bg-gray-700 hover:text-white"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-gray-700 px-6">
          {tabs.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={clsx(
                'px-4 py-3 text-sm font-medium border-b-2 -mb-px transition-colors',
                activeTab === tab.id
                  ? 'text-blue-400 border-blue-400'
                  : 'text-gray-400 border-transparent hover:text-gray-200'
              )}
            >
              {tab.label}
              {tab.count !== undefined && tab.count > 0 && (
                <span className="ml-1.5 rounded-full bg-gray-700 px-1.5 py-0.5 text-xs">
                  {tab.count}
                </span>
              )}
            </button>
          ))}
        </div>

        <div className="max-h-[60vh] overflow-y-auto p-6">
          {activeTab === 'info' && (
            <>
              {/* Strategy Info */}
              <section className="mb-6">
                <h3 className="mb-3 text-sm font-medium text-gray-400 uppercase">Strategy Info</h3>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded bg-gray-900/50 p-3">
                    <p className="text-xs text-gray-500">Name</p>
                    <p className="text-sm text-white font-medium">{strategy.name}</p>
                  </div>
                  <div className="rounded bg-gray-900/50 p-3">
                    <p className="text-xs text-gray-500">ID</p>
                    <p className="text-sm text-white font-mono">{strategy.strategy_id}</p>
                  </div>
                  <div className="rounded bg-gray-900/50 p-3">
                    <p className="text-xs text-gray-500">Status</p>
                    <span className={clsx('inline-flex rounded-full px-2 py-0.5 text-xs font-medium', statusConfig.color, statusConfig.bgColor)}>
                      {statusConfig.label}
                    </span>
                  </div>
                  <div className="rounded bg-gray-900/50 p-3">
                    <p className="text-xs text-gray-500">Symbol</p>
                    <p className="text-sm text-white font-mono">{runtime?.symbols?.[0] ?? '-'}</p>
                  </div>
                  <div className="rounded bg-gray-900/50 p-3">
                    <p className="text-xs text-gray-500">Language</p>
                    <p className="text-sm text-white">{strategy.language ?? 'Python'}</p>
                  </div>
                  <div className="sm:col-span-2 rounded bg-gray-900/50 p-3">
                    <p className="text-xs text-gray-500">Entrypoint</p>
                    <p className="text-sm text-white font-mono">{strategy.entrypoint ?? '-'}</p>
                  </div>
                  {strategy.description && (
                    <div className="sm:col-span-2 rounded bg-gray-900/50 p-3">
                      <p className="text-xs text-gray-500">Description</p>
                      <p className="text-sm text-white">{strategy.description}</p>
                    </div>
                  )}
                </div>
              </section>

              {/* Runtime Metrics */}
              {runtime && (
                <section className="mb-6">
                  <h3 className="mb-3 text-sm font-medium text-gray-400 uppercase">Runtime Metrics</h3>
                  <div className="grid gap-3 sm:grid-cols-3">
                    <div className="rounded bg-gray-900/50 p-3 text-center">
                      <p className="text-2xl font-bold text-white">{runtime.tick_count ?? 0}</p>
                      <p className="text-xs text-gray-500">Ticks</p>
                    </div>
                    <div className="rounded bg-gray-900/50 p-3 text-center">
                      <p className="text-2xl font-bold text-white">{runtime.signal_count ?? 0}</p>
                      <p className="text-xs text-gray-500">Signals</p>
                    </div>
                    <div className="rounded bg-gray-900/50 p-3 text-center">
                      <p className={clsx('text-2xl font-bold', (runtime.error_count ?? 0) > 0 ? 'text-red-400' : 'text-white')}>
                        {runtime.error_count ?? 0}
                      </p>
                      <p className="text-xs text-gray-500">Errors</p>
                    </div>
                  </div>
                </section>
              )}

              {/* Timestamps */}
              {runtime && (
                <section className="mb-6">
                  <h3 className="mb-3 text-sm font-medium text-gray-400 uppercase">Timestamps</h3>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="rounded bg-gray-900/50 p-3">
                      <p className="text-xs text-gray-500">Loaded At</p>
                      <p className="text-sm text-white">{formatTimestamp(runtime.loaded_at)}</p>
                    </div>
                    <div className="rounded bg-gray-900/50 p-3">
                      <p className="text-xs text-gray-500">Started At</p>
                      <p className="text-sm text-white">{formatTimestamp(runtime.started_at)}</p>
                    </div>
                    <div className="rounded bg-gray-900/50 p-3">
                      <p className="text-xs text-gray-500">Last Tick At</p>
                      <p className="text-sm text-white">{formatTimestamp(runtime.last_tick_at)}</p>
                    </div>
                    <div className="rounded bg-gray-900/50 p-3">
                      <p className="text-xs text-gray-500">Version</p>
                      <p className="text-sm text-white">{runtime.version ?? '-'}</p>
                    </div>
                  </div>
                </section>
              )}

              {/* Blocked Reason */}
              {runtime?.blocked_reason && (
                <section className="mb-6">
                  <h3 className="mb-3 text-sm font-medium text-gray-400 uppercase">Blocked Reason</h3>
                  <div className="rounded border border-red-900/50 bg-red-950/20 p-3">
                    <p className="text-sm text-red-400">{runtime.blocked_reason}</p>
                  </div>
                </section>
              )}

              {/* Last Error */}
              {runtime?.last_error && (
                <section className="mb-6">
                  <h3 className="mb-3 text-sm font-medium text-gray-400 uppercase">Last Error</h3>
                  <div className="rounded border border-red-900/50 bg-red-950/20 p-3">
                    <p className="whitespace-pre-wrap text-sm text-red-400 font-mono">{runtime.last_error}</p>
                  </div>
                </section>
              )}

              {/* Config */}
              {runtime?.config && Object.keys(runtime.config).length > 0 && (
                <section>
                  <h3 className="mb-3 text-sm font-medium text-gray-400 uppercase">Config</h3>
                  <div className="rounded bg-gray-900/50 p-3">
                    <pre className="whitespace-pre-wrap text-xs text-gray-300 font-mono">
                      {JSON.stringify(runtime.config, null, 2)}
                    </pre>
                  </div>
                </section>
              )}
            </>
          )}

          {activeTab === 'orders' && (
            <section>
              <h3 className="mb-3 text-sm font-medium text-gray-400 uppercase">Order History ({orders.length})</h3>
              {orders.length > 0 ? (
                <div className="rounded bg-gray-900/50 p-3 divide-y divide-gray-800">
                  {orders.map((event, i) => (
                    <OrderEventRow key={event.trace_id ?? `${event.ts_ms}-${i}`} event={event} />
                  ))}
                </div>
              ) : (
                <div className="rounded bg-gray-900/50 p-6 text-center">
                  <p className="text-sm text-gray-500">No orders yet</p>
                </div>
              )}
            </section>
          )}

          {activeTab === 'signals' && (
            <section>
              <h3 className="mb-3 text-sm font-medium text-gray-400 uppercase">Recent Signals ({signals?.length ?? 0})</h3>
              {signals && signals.length > 0 ? (
                <div className="rounded bg-gray-900/50 p-3 divide-y divide-gray-800">
                  {signals.map((event, i) => (
                    <SignalEventRow key={event.trace_id ?? i} event={event} />
                  ))}
                </div>
              ) : (
                <div className="rounded bg-gray-900/50 p-6 text-center">
                  <p className="text-sm text-gray-500">No signals generated yet</p>
                </div>
              )}
            </section>
          )}

          {activeTab === 'errors' && (
            <section>
              <h3 className="mb-3 text-sm font-medium text-gray-400 uppercase">Recent Errors ({errors?.length ?? 0})</h3>
              {errors && errors.length > 0 ? (
                <div className="rounded bg-gray-900/50 p-3 divide-y divide-gray-800">
                  {errors.map((event, i) => (
                    <ErrorEventRow key={event.trace_id ?? i} event={event} />
                  ))}
                </div>
              ) : (
                <div className="rounded bg-gray-900/50 p-6 text-center">
                  <p className="text-sm text-gray-500">No errors recorded</p>
                </div>
              )}
            </section>
          )}
        </div>

        <div className="border-t border-gray-700 px-6 py-4">
          <button
            onClick={onClose}
            className="rounded-md bg-gray-700 px-4 py-2 text-sm font-medium text-gray-200 hover:bg-gray-600"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
