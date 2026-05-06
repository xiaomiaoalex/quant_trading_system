import { useEffect, useMemo, useState } from 'react'
import { clsx } from 'clsx'
import { ConfirmDialog, ErrorState, LoadingState } from '@/components/ui'
import {
  useCryptoRiskEvents,
  useCryptoRiskProbe,
  useCryptoRiskRuntime,
  useUpdateCryptoRiskBudget,
} from '@/hooks'
import { formatAPIError } from '@/api/client'
import type { CryptoRiskBudgetUpdateRequest, CryptoRiskProbeResponse } from '@/types'
import { formatKeyValueMap, parseKeyValueMapInput, parseSymbolListInput } from '@/utils'

type ConfirmAction = 'budget' | 'probe' | null

function formatDateTime(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function formatEventTime(tsMs: number): string {
  const date = new Date(tsMs)
  if (Number.isNaN(date.getTime())) return String(tsMs)
  return date.toLocaleString()
}

function summarizePayload(value: Record<string, unknown>): string {
  return JSON.stringify(value, null, 2)
}

function statusTone(state: 'ok' | 'warn' | 'bad') {
  return {
    ok: 'border-emerald-800/60 bg-emerald-950/20 text-emerald-300',
    warn: 'border-yellow-800/60 bg-yellow-950/20 text-yellow-300',
    bad: 'border-red-800/60 bg-red-950/20 text-red-300',
  }[state]
}

function probeState(result: CryptoRiskProbeResponse | null): 'ok' | 'warn' | 'bad' {
  if (!result) return 'warn'
  return result.ok ? 'ok' : 'bad'
}

interface MetricTileProps {
  label: string
  value: string
  tone?: 'ok' | 'warn' | 'bad'
}

function MetricTile({ label, value, tone = 'warn' }: MetricTileProps) {
  return (
    <div className={clsx('rounded-md border px-4 py-3', statusTone(tone))}>
      <div className="text-xs font-medium uppercase text-gray-400">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold">{value}</div>
    </div>
  )
}

interface TextAreaFieldProps {
  label: string
  value: string
  placeholder: string
  onChange: (value: string) => void
}

function TextAreaField({ label, value, placeholder, onChange }: TextAreaFieldProps) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium uppercase text-gray-400">{label}</span>
      <textarea
        value={value}
        placeholder={placeholder}
        rows={4}
        onChange={event => onChange(event.target.value)}
        className="min-h-28 w-full resize-y rounded-md border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-200 placeholder:text-gray-600 focus:border-blue-500 focus:outline-none"
      />
    </label>
  )
}

export function CryptoRiskOps() {
  const [symbolCaps, setSymbolCaps] = useState('')
  const [symbolClusters, setSymbolClusters] = useState('')
  const [clusterCaps, setClusterCaps] = useState('')
  const [totalCap, setTotalCap] = useState('0')
  const [maxMarginRatio, setMaxMarginRatio] = useState('0.80')
  const [minLiquidationBufferRatio, setMinLiquidationBufferRatio] = useState('0')
  const [updatedBy, setUpdatedBy] = useState('frontend_operator')
  const [probeSymbols, setProbeSymbols] = useState('BTCUSDT')
  const [requestedBy, setRequestedBy] = useState('frontend_operator')
  const [auditEventType, setAuditEventType] = useState('')
  const [auditTraceId, setAuditTraceId] = useState('')
  const [auditSignalId, setAuditSignalId] = useState('')
  const [isBudgetDirty, setIsBudgetDirty] = useState(false)
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [probeResult, setProbeResult] = useState<CryptoRiskProbeResponse | null>(null)

  const {
    data: runtime,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
  } = useCryptoRiskRuntime()
  const auditFilters = useMemo(
    () => ({
      event_type: auditEventType || undefined,
      trace_id: auditTraceId.trim() || undefined,
      signal_id: auditSignalId.trim() || undefined,
      limit: 50,
    }),
    [auditEventType, auditTraceId, auditSignalId]
  )
  const { data: events, refetch: refetchEvents, isFetching: isEventsFetching } =
    useCryptoRiskEvents(auditFilters)
  const {
    updateBudget,
    isPending: isBudgetPending,
    error: budgetError,
  } = useUpdateCryptoRiskBudget()
  const { runProbe, isPending: isProbePending, error: probeError } = useCryptoRiskProbe()

  useEffect(() => {
    if (!runtime || isBudgetDirty) return
    setSymbolCaps(formatKeyValueMap(runtime.risk_budget.symbol_notional_caps))
    setSymbolClusters(formatKeyValueMap(runtime.risk_budget.symbol_clusters))
    setClusterCaps(formatKeyValueMap(runtime.risk_budget.cluster_notional_caps))
    setTotalCap(runtime.risk_budget.total_notional_cap)
    setMaxMarginRatio(runtime.risk_budget.max_margin_ratio)
    setMinLiquidationBufferRatio(runtime.risk_budget.min_liquidation_buffer_ratio)
    setProbeSymbols(current => current || runtime.base_symbols.join(', '))
  }, [runtime, isBudgetDirty])

  const checkRows = useMemo(
    () =>
      Object.entries(probeResult?.checks ?? {}).sort(([left], [right]) => left.localeCompare(right)),
    [probeResult]
  )

  const runtimeTone: 'ok' | 'warn' | 'bad' = runtime?.wired ? 'ok' : runtime?.fail_closed ? 'bad' : 'warn'
  const probeTone = probeState(probeResult)
  const isActionPending = isBudgetPending || isProbePending

  const markDirty = (setter: (value: string) => void) => (value: string) => {
    setIsBudgetDirty(true)
    setter(value)
  }

  const buildBudgetRequest = (): CryptoRiskBudgetUpdateRequest => ({
    symbol_notional_caps: parseKeyValueMapInput(symbolCaps),
    symbol_clusters: parseKeyValueMapInput(symbolClusters),
    cluster_notional_caps: parseKeyValueMapInput(clusterCaps),
    total_notional_cap: totalCap.trim(),
    max_margin_ratio: maxMarginRatio.trim(),
    min_liquidation_buffer_ratio: minLiquidationBufferRatio.trim(),
    updated_by: updatedBy.trim(),
  })

  const openBudgetConfirm = () => {
    setFormError(null)
    setMessage(null)
    try {
      const request = buildBudgetRequest()
      if (!request.updated_by) {
        setFormError('updated_by is required')
        return
      }
    } catch (buildError) {
      setFormError(buildError instanceof Error ? buildError.message : 'Invalid budget input')
      return
    }
    setConfirmAction('budget')
  }

  const openProbeConfirm = () => {
    setFormError(null)
    setMessage(null)
    if (!requestedBy.trim()) {
      setFormError('requested_by is required')
      return
    }
    if (parseSymbolListInput(probeSymbols).length === 0) {
      setFormError('At least one symbol is required')
      return
    }
    setConfirmAction('probe')
  }

  const confirmSubmit = async () => {
    if (confirmAction === 'budget') {
      const status = await updateBudget(buildBudgetRequest())
      if (status) {
        setIsBudgetDirty(false)
        setMessage('Risk budget updated')
        void refetchEvents()
      }
    }
    if (confirmAction === 'probe') {
      const result = await runProbe({
        symbols: parseSymbolListInput(probeSymbols),
        requested_by: requestedBy.trim(),
      })
      if (result) {
        setProbeResult(result)
        setMessage('Read-only probe completed')
        void refetchEvents()
      }
    }
    setConfirmAction(null)
  }

  if (isLoading) {
    return (
      <div className="p-6">
        <LoadingState message="Loading crypto risk runtime..." />
      </div>
    )
  }

  if (isError || !runtime) {
    return (
      <div className="p-6">
        <ErrorState
          title="Crypto risk runtime unavailable"
          message={formatAPIError(error)}
          onRetry={() => {
            void refetch()
          }}
        />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-900">
      <div className="sticky top-0 z-10 border-b border-gray-800 bg-gray-900/90 backdrop-blur-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 px-6 py-4">
          <div>
            <h1 className="text-xl font-semibold text-white">Crypto Risk</h1>
            <div className="mt-1 text-xs text-gray-500">
              Updated {formatDateTime(runtime.updated_at)} by {runtime.updated_by ?? '-'}
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              void refetch()
              void refetchEvents()
            }}
            className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700"
          >
            {isFetching || isEventsFetching ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
      </div>

      <div className="space-y-5 p-6">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <MetricTile label="Execution Env" value={runtime.execution_env} tone="ok" />
          <MetricTile label="Runtime" value={runtime.wired ? 'wired' : 'not wired'} tone={runtimeTone} />
          <MetricTile label="Fail Closed" value={runtime.fail_closed ? 'yes' : 'no'} tone={runtime.fail_closed ? 'bad' : 'ok'} />
          <MetricTile label="Source Mode" value={probeResult?.mode ?? '-'} tone={probeTone} />
        </div>

        {runtime.last_error && (
          <div className="rounded-md border border-red-900/60 bg-red-950/20 px-4 py-3 text-sm text-red-300">
            {runtime.last_error}
          </div>
        )}
        {(formError || budgetError || probeError) && (
          <div className="rounded-md border border-red-900/60 bg-red-950/20 px-4 py-3 text-sm text-red-300">
            {formError ?? budgetError ?? probeError}
          </div>
        )}
        {message && (
          <div className="rounded-md border border-emerald-900/60 bg-emerald-950/20 px-4 py-3 text-sm text-emerald-300">
            {message}
          </div>
        )}

        <section className="rounded-md border border-gray-700 bg-gray-800/40">
          <div className="border-b border-gray-700 px-4 py-3">
            <h2 className="text-sm font-semibold text-gray-200">Readiness Probe</h2>
          </div>
          <div className="grid gap-4 p-4 lg:grid-cols-[1fr_1fr_auto]">
            <label className="block">
              <span className="mb-1 block text-xs font-medium uppercase text-gray-400">Symbols</span>
              <input
                value={probeSymbols}
                onChange={event => setProbeSymbols(event.target.value)}
                className="w-full rounded-md border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-200 focus:border-blue-500 focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium uppercase text-gray-400">Requested By</span>
              <input
                value={requestedBy}
                onChange={event => setRequestedBy(event.target.value)}
                className="w-full rounded-md border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-200 focus:border-blue-500 focus:outline-none"
              />
            </label>
            <div className="flex items-end">
              <button
                type="button"
                onClick={openProbeConfirm}
                disabled={!runtime.wired || isProbePending}
                className="h-10 rounded-md bg-blue-900/50 px-4 text-sm font-medium text-blue-200 hover:bg-blue-900/70 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {isProbePending ? 'Running...' : 'Run Probe'}
              </button>
            </div>
          </div>

          <div className="border-t border-gray-700 px-4 py-3 text-xs text-gray-400">
            Source URL: <span className="text-gray-300">{runtime.futures_base_url ?? '-'}</span>
          </div>

          <div className="overflow-x-auto border-t border-gray-700">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-900/80 text-left text-xs uppercase text-gray-500">
                <tr>
                  <th className="px-4 py-2">Check</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Latency</th>
                  <th className="px-4 py-2">Message</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800">
                {checkRows.length === 0 ? (
                  <tr>
                    <td className="px-4 py-4 text-gray-500" colSpan={4}>
                      No probe result.
                    </td>
                  </tr>
                ) : (
                  checkRows.map(([name, check]) => (
                    <tr key={name}>
                      <td className="px-4 py-3 text-gray-200">{name}</td>
                      <td className="px-4 py-3">
                        <span
                          className={clsx(
                            'rounded-full px-2 py-0.5 text-xs font-medium',
                            check.status === 'passed'
                              ? 'bg-emerald-950/40 text-emerald-300'
                              : 'bg-red-950/40 text-red-300'
                          )}
                        >
                          {check.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-gray-300">{check.latency_ms}ms</td>
                      <td className="px-4 py-3 text-gray-400">{check.message}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="rounded-md border border-gray-700 bg-gray-800/40">
          <div className="border-b border-gray-700 px-4 py-3">
            <h2 className="text-sm font-semibold text-gray-200">Risk Budget</h2>
          </div>
          <div className="grid gap-4 p-4 lg:grid-cols-3">
            <TextAreaField
              label="Symbol Caps"
              value={symbolCaps}
              placeholder="BTCUSDT=10000"
              onChange={markDirty(setSymbolCaps)}
            />
            <TextAreaField
              label="Symbol Clusters"
              value={symbolClusters}
              placeholder="BTCUSDT=BTC_BETA"
              onChange={markDirty(setSymbolClusters)}
            />
            <TextAreaField
              label="Cluster Caps"
              value={clusterCaps}
              placeholder="BTC_BETA=15000"
              onChange={markDirty(setClusterCaps)}
            />
          </div>
          <div className="grid gap-4 border-t border-gray-700 p-4 md:grid-cols-4">
            <label className="block">
              <span className="mb-1 block text-xs font-medium uppercase text-gray-400">Total Cap</span>
              <input
                value={totalCap}
                onChange={event => markDirty(setTotalCap)(event.target.value)}
                className="w-full rounded-md border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-200 focus:border-blue-500 focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium uppercase text-gray-400">Max Margin</span>
              <input
                value={maxMarginRatio}
                onChange={event => markDirty(setMaxMarginRatio)(event.target.value)}
                className="w-full rounded-md border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-200 focus:border-blue-500 focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium uppercase text-gray-400">Liq Buffer</span>
              <input
                value={minLiquidationBufferRatio}
                onChange={event => markDirty(setMinLiquidationBufferRatio)(event.target.value)}
                className="w-full rounded-md border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-200 focus:border-blue-500 focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium uppercase text-gray-400">Updated By</span>
              <input
                value={updatedBy}
                onChange={event => setUpdatedBy(event.target.value)}
                className="w-full rounded-md border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-200 focus:border-blue-500 focus:outline-none"
              />
            </label>
          </div>
          <div className="flex items-center justify-end border-t border-gray-700 px-4 py-3">
            <button
              type="button"
              onClick={openBudgetConfirm}
              disabled={!runtime.wired || isBudgetPending}
              className="rounded-md bg-emerald-900/50 px-4 py-2 text-sm font-medium text-emerald-200 hover:bg-emerald-900/70 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isBudgetPending ? 'Saving...' : 'Save Budget'}
            </button>
          </div>
        </section>

        <section className="rounded-md border border-gray-700 bg-gray-800/40">
          <div className="border-b border-gray-700 px-4 py-3">
            <h2 className="text-sm font-semibold text-gray-200">Audit Stream</h2>
          </div>
          <div className="grid gap-3 border-b border-gray-700 p-4 md:grid-cols-[1fr_1fr_1fr_auto]">
            <label className="block">
              <span className="mb-1 block text-xs font-medium uppercase text-gray-400">Event</span>
              <select
                value={auditEventType}
                onChange={event => setAuditEventType(event.target.value)}
                className="h-10 w-full rounded-md border border-gray-700 bg-gray-950 px-3 text-sm text-gray-200 focus:border-blue-500 focus:outline-none"
              >
                <option value="">All</option>
                <option value="crypto_risk.pre_trade_rejected">Pre-trade Rejected</option>
                <option value="crypto_risk.budget_updated">Budget Updated</option>
                <option value="crypto_risk.probe_run">Probe Run</option>
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium uppercase text-gray-400">Trace</span>
              <input
                value={auditTraceId}
                onChange={event => setAuditTraceId(event.target.value)}
                className="h-10 w-full rounded-md border border-gray-700 bg-gray-950 px-3 text-sm text-gray-200 focus:border-blue-500 focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium uppercase text-gray-400">Signal</span>
              <input
                value={auditSignalId}
                onChange={event => setAuditSignalId(event.target.value)}
                className="h-10 w-full rounded-md border border-gray-700 bg-gray-950 px-3 text-sm text-gray-200 focus:border-blue-500 focus:outline-none"
              />
            </label>
            <div className="flex items-end">
              <button
                type="button"
                onClick={() => {
                  setAuditEventType('')
                  setAuditTraceId('')
                  setAuditSignalId('')
                }}
                className="h-10 rounded-md bg-gray-800 px-3 text-sm font-medium text-gray-300 hover:bg-gray-700"
              >
                Clear
              </button>
            </div>
          </div>
          <div className="max-h-[420px] overflow-auto">
            <table className="min-w-full text-sm">
              <thead className="sticky top-0 bg-gray-900/95 text-left text-xs uppercase text-gray-500">
                <tr>
                  <th className="px-4 py-2">Time</th>
                  <th className="px-4 py-2">Event</th>
                  <th className="px-4 py-2">Trace</th>
                  <th className="px-4 py-2">Payload</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800">
                {!events || events.length === 0 ? (
                  <tr>
                    <td className="px-4 py-4 text-gray-500" colSpan={4}>
                      No risk events.
                    </td>
                  </tr>
                ) : (
                  events.map(event => (
                    <tr key={`${event.event_id ?? event.trace_id}-${event.ts_ms}`}>
                      <td className="px-4 py-3 text-gray-400">{formatEventTime(event.ts_ms)}</td>
                      <td className="px-4 py-3 text-gray-200">{event.event_type}</td>
                      <td className="px-4 py-3 text-xs text-gray-500">{event.trace_id ?? '-'}</td>
                      <td className="px-4 py-3">
                        <pre className="max-h-36 max-w-xl overflow-auto rounded border border-gray-800 bg-gray-950 p-2 text-xs text-gray-400">
                          {summarizePayload(event.payload)}
                        </pre>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <ConfirmDialog
        isOpen={confirmAction !== null}
        title={confirmAction === 'budget' ? 'Update Risk Budget' : 'Run Read-only Probe'}
        message={
          confirmAction === 'budget'
            ? 'This will hot-update the active crypto risk budget.'
            : 'This will call read-only Binance risk source endpoints.'
        }
        confirmLabel={confirmAction === 'budget' ? 'Update Budget' : 'Run Probe'}
        variant={confirmAction === 'budget' ? 'warning' : 'info'}
        isLoading={isActionPending}
        onCancel={() => setConfirmAction(null)}
        onConfirm={confirmSubmit}
      />
    </div>
  )
}
