import { useMemo, useState } from 'react'
import { useBacktestList, useBacktestReport, useCreateBacktest, useLoadedStrategies } from '@/hooks'
import { LoadingState, ErrorState } from '@/components/ui'
import { BacktestList, BacktestDetailPanel } from '@/components/backtests'
import { strategiesAPI } from '@/api'
import { formatAPIError } from '@/api/client'

const DEFAULT_STRATEGY_CODE = `from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from trader.core.application.strategy_protocol import (
    MarketData,
    RiskLevel,
    StrategyPlugin,
    StrategyResourceLimits,
    ValidationResult,
)
from trader.core.domain.models.signal import Signal, SignalType


@dataclass(slots=True)
class LabStrategy:
    strategy_id: str = "lab_strategy"
    name: str = "Lab Strategy"
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.MEDIUM
    resource_limits: StrategyResourceLimits = field(default_factory=StrategyResourceLimits)
    threshold: Decimal = Decimal("0.001")
    quantity: Decimal = Decimal("1")
    _last_price: Decimal | None = None

    async def initialize(self, config: dict[str, Any]) -> None:
        if "threshold" in config:
            self.threshold = Decimal(str(config["threshold"]))
        if "quantity" in config:
            self.quantity = Decimal(str(config["quantity"]))
        self._last_price = None

    async def on_market_data(self, data: MarketData) -> Signal | None:
        if self._last_price is None:
            self._last_price = data.price
            return None

        ratio = (data.price - self._last_price) / self._last_price if self._last_price > 0 else Decimal("0")
        self._last_price = data.price
        if ratio > self.threshold:
            return Signal(
                strategy_name=self.strategy_id,
                signal_type=SignalType.BUY,
                symbol=data.symbol,
                price=data.price,
                quantity=self.quantity,
                reason=f"up move {ratio:.4f}",
            )
        if ratio < -self.threshold:
            return Signal(
                strategy_name=self.strategy_id,
                signal_type=SignalType.SELL,
                symbol=data.symbol,
                price=data.price,
                quantity=self.quantity,
                reason=f"down move {ratio:.4f}",
            )
        return None

    async def on_fill(self, order_id: str, symbol: str, side: str, quantity: float, price: float) -> None:
        return None

    async def on_cancel(self, order_id: str, reason: str) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def update_config(self, config: dict[str, Any]) -> ValidationResult:
        await self.initialize(config)
        return self.validate()

    def validate(self) -> ValidationResult:
        return ValidationResult.valid()


_plugin = LabStrategy()


def get_plugin() -> StrategyPlugin:
    return _plugin
`

export function Backtests() {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined)

  const { data: backtests, isLoading, isError, error, refetch, isFetching } = useBacktestList(
    statusFilter ? { status: statusFilter } : undefined
  )
  const { data: report, isLoading: isReportLoading } = useBacktestReport(selectedRunId ?? '')
  const { data: loadedStrategies, refetch: refetchLoaded } = useLoadedStrategies()
  const { create, isPending: isCreating, error: createError } = useCreateBacktest()

  const [labForm, setLabForm] = useState({
    strategy_id: 'lab_strategy',
    name: 'Lab Strategy',
    description: 'Editable strategy code for fast backtest iteration',
    version: 1,
    symbols: 'BTCUSDT',
    start_ts_ms: Date.now() - 30 * 24 * 60 * 60 * 1000,
    end_ts_ms: Date.now(),
    venue: 'BINANCE',
    requested_by: 'console_user',
  })
  const [strategyCode, setStrategyCode] = useState(DEFAULT_STRATEGY_CODE)
  const [savedCodeVersion, setSavedCodeVersion] = useState<number | null>(null)
  const [debugResult, setDebugResult] = useState<Record<string, unknown> | null>(null)
  const [labError, setLabError] = useState<string | null>(null)
  const [labMessage, setLabMessage] = useState<string | null>(null)
  const [isDebugging, setIsDebugging] = useState(false)
  const [isRegistering, setIsRegistering] = useState(false)
  const [isLoadingStrategy, setIsLoadingStrategy] = useState(false)
  const [isRunningStrategy, setIsRunningStrategy] = useState(false)
  const [isStoppingStrategy, setIsStoppingStrategy] = useState(false)

  const runtimeInfo = useMemo(() => {
    return (loadedStrategies ?? []).find(item => item.strategy_id === labForm.strategy_id) ?? null
  }, [loadedStrategies, labForm.strategy_id])

  const handleDebug = async () => {
    setLabError(null)
    setLabMessage(null)
    setIsDebugging(true)
    try {
      const result = await strategiesAPI.debugStrategyCode({
        strategy_id: labForm.strategy_id,
        code: strategyCode,
        config: {},
      })
      setDebugResult(result as unknown as Record<string, unknown>)
      if (!result.ok) {
        setLabError(result.errors.join('; ') || 'Debug failed')
        return
      }
      setLabMessage(`Debug passed, generated ${result.signals.length} signal(s) in dry-run.`)
    } catch (e) {
      setLabError(formatAPIError(e))
    } finally {
      setIsDebugging(false)
    }
  }

  const handleRegister = async () => {
    setLabError(null)
    setLabMessage(null)
    setIsRegistering(true)
    try {
      const result = await strategiesAPI.createStrategyCode({
        strategy_id: labForm.strategy_id,
        name: labForm.name,
        description: labForm.description,
        code: strategyCode,
        created_by: labForm.requested_by,
        register_if_missing: true,
      })
      setSavedCodeVersion(result.code_version)
      setLabMessage(`Code registered as version #${result.code_version}.`)
      await refetch()
    } catch (e) {
      setLabError(formatAPIError(e))
    } finally {
      setIsRegistering(false)
    }
  }

  const handleCreateBacktest = async () => {
    setLabError(null)
    setLabMessage(null)
    const result = await create({
      strategy_id: labForm.strategy_id,
      version: Number(labForm.version),
      strategy_code_version: savedCodeVersion ?? undefined,
      symbols: labForm.symbols.split(',').map(s => s.trim()).filter(Boolean),
      start_ts_ms: labForm.start_ts_ms,
      end_ts_ms: labForm.end_ts_ms,
      venue: labForm.venue,
      requested_by: labForm.requested_by,
    })
    if (result) {
      setSelectedRunId(result.run_id)
      setLabMessage(`Backtest submitted: ${result.run_id}`)
      return
    }
    setLabError(createError ?? 'Backtest submit failed')
  }

  const handleLoadStrategy = async () => {
    setLabError(null)
    setLabMessage(null)
    setIsLoadingStrategy(true)
    try {
      await strategiesAPI.loadStrategy(labForm.strategy_id, {
        version: `v${labForm.version}`,
        code_version: savedCodeVersion ?? undefined,
      })
      await refetchLoaded()
      setLabMessage('Strategy loaded.')
    } catch (e) {
      setLabError(formatAPIError(e))
    } finally {
      setIsLoadingStrategy(false)
    }
  }

  const handleStartStrategy = async () => {
    setLabError(null)
    setLabMessage(null)
    setIsRunningStrategy(true)
    try {
      await strategiesAPI.startStrategy(labForm.strategy_id)
      await refetchLoaded()
      setLabMessage('Strategy running.')
    } catch (e) {
      setLabError(formatAPIError(e))
    } finally {
      setIsRunningStrategy(false)
    }
  }

  const handleStopStrategy = async () => {
    setLabError(null)
    setLabMessage(null)
    setIsStoppingStrategy(true)
    try {
      await strategiesAPI.stopStrategy(labForm.strategy_id)
      await refetchLoaded()
      setLabMessage('Strategy stopped.')
    } catch (e) {
      setLabError(formatAPIError(e))
    } finally {
      setIsStoppingStrategy(false)
    }
  }

  if (isLoading) return <div className="p-6"><LoadingState message="Loading backtests..." /></div>
  if (isError) return <div className="p-6"><ErrorState title="Failed to load backtests" message={formatAPIError(error)} onRetry={refetch} /></div>

  return (
    <div className="min-h-screen bg-gray-900">
      <div className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="flex items-center justify-between px-6 py-4">
          <div className="flex items-center gap-4">
            <h1 className="text-xl font-semibold text-white">Backtests</h1>
            {isFetching && <span className="text-xs text-gray-500">Refreshing...</span>}
          </div>
          <div className="flex items-center gap-3">
            <select
              value={statusFilter ?? ''}
              onChange={(e) => setStatusFilter(e.target.value || undefined)}
              className="rounded bg-gray-800 px-3 py-1.5 text-sm text-gray-300 border border-gray-700"
            >
              <option value="">All Status</option>
              <option value="RUNNING">Running</option>
              <option value="COMPLETED">Completed</option>
              <option value="FAILED">Failed</option>
            </select>
            <button onClick={() => refetch()} className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700">Refresh</button>
          </div>
        </div>
      </div>

      <div className="p-6">
        <div className="mb-6 rounded-lg border border-gray-700 bg-gray-800/50 p-4">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-base font-semibold text-white">Strategy Lab</h2>
            <div className="text-xs text-gray-400">
              runtime: <span className="text-gray-200">{runtimeInfo?.status ?? 'NOT_LOADED'}</span>
            </div>
          </div>

          {(labError || createError) && (
            <div className="mb-4 rounded bg-red-950/20 p-2 text-sm text-red-400">
              {labError ?? createError}
            </div>
          )}
          {labMessage && (
            <div className="mb-4 rounded bg-green-950/20 p-2 text-sm text-green-400">
              {labMessage}
            </div>
          )}

          <div className="mb-4 grid gap-3 md:grid-cols-3">
            <input
              type="text"
              value={labForm.strategy_id}
              onChange={(e) => setLabForm({ ...labForm, strategy_id: e.target.value })}
              className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
              placeholder="strategy_id"
            />
            <input
              type="text"
              value={labForm.name}
              onChange={(e) => setLabForm({ ...labForm, name: e.target.value })}
              className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
              placeholder="name"
            />
            <input
              type="text"
              value={labForm.symbols}
              onChange={(e) => setLabForm({ ...labForm, symbols: e.target.value })}
              className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
              placeholder="BTCUSDT,ETHUSDT"
            />
          </div>

          <textarea
            value={strategyCode}
            onChange={(e) => setStrategyCode(e.target.value)}
            className="mb-4 h-72 w-full rounded bg-gray-950 border border-gray-700 p-3 text-xs text-gray-200 font-mono"
            spellCheck={false}
          />

          <div className="mb-2 flex flex-wrap items-center gap-2">
            <button
              onClick={handleDebug}
              disabled={isDebugging}
              className="rounded bg-indigo-900/40 px-3 py-1.5 text-xs text-indigo-200 hover:bg-indigo-900/60 disabled:opacity-60"
            >
              {isDebugging ? 'Debugging...' : '1) Debug Code'}
            </button>
            <button
              onClick={handleRegister}
              disabled={isRegistering}
              className="rounded bg-blue-900/40 px-3 py-1.5 text-xs text-blue-200 hover:bg-blue-900/60 disabled:opacity-60"
            >
              {isRegistering ? 'Registering...' : '2) Register Code'}
            </button>
            <button
              onClick={handleCreateBacktest}
              disabled={isCreating}
              className="rounded bg-cyan-900/40 px-3 py-1.5 text-xs text-cyan-200 hover:bg-cyan-900/60 disabled:opacity-60"
            >
              {isCreating ? 'Backtesting...' : '3) Run Backtest'}
            </button>
            <button
              onClick={handleLoadStrategy}
              disabled={isLoadingStrategy}
              className="rounded bg-emerald-900/40 px-3 py-1.5 text-xs text-emerald-200 hover:bg-emerald-900/60 disabled:opacity-60"
            >
              {isLoadingStrategy ? 'Loading...' : '4) Load'}
            </button>
            <button
              onClick={handleStartStrategy}
              disabled={isRunningStrategy}
              className="rounded bg-green-900/40 px-3 py-1.5 text-xs text-green-200 hover:bg-green-900/60 disabled:opacity-60"
            >
              {isRunningStrategy ? 'Starting...' : '5) Run'}
            </button>
            <button
              onClick={handleStopStrategy}
              disabled={isStoppingStrategy}
              className="rounded bg-red-900/40 px-3 py-1.5 text-xs text-red-200 hover:bg-red-900/60 disabled:opacity-60"
            >
              {isStoppingStrategy ? 'Stopping...' : '6) Stop'}
            </button>
          </div>

          <div className="text-xs text-gray-400">
            saved code version: <span className="text-gray-200">{savedCodeVersion ?? '-'}</span>
          </div>
          {debugResult && (
            <pre className="mt-3 max-h-52 overflow-auto rounded bg-gray-950 border border-gray-700 p-3 text-xs text-gray-300">
              {JSON.stringify(debugResult, null, 2)}
            </pre>
          )}
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <div>
            <BacktestList
              backtests={backtests ?? []}
              onSelect={setSelectedRunId}
              selectedRunId={selectedRunId ?? undefined}
            />
          </div>
          <div>
            <BacktestDetailPanel
              report={report as any}
              isLoading={isReportLoading}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
