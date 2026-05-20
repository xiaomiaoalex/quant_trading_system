import { useMemo, useState, useCallback } from 'react'
import Editor from '@monaco-editor/react'
import { useBacktestList, useBacktestReport, useCreateBacktest, useLoadedStrategies } from '@/hooks'
import { LoadingState, ErrorState } from '@/components/ui'
import { BacktestList, BacktestDetailPanel } from '@/components/backtests'
import { PageHeader } from '@/components/layout'
import { researchAPI } from '@/api'
import { formatAPIError } from '@/api/client'
import { buildDeploymentId, type DeploymentMode } from '@/types/strategies'
import type { BacktestDataMode, BacktestEngine, StrategyCandidate } from '@/types'

const STRATEGY_TEMPLATES: Record<string, string> = {
  minimal: `from trader.core.application.strategy_protocol import (
    MarketData, StrategyResourceLimits, ValidationResult, RiskLevel
)
from trader.core.domain.models.signal import Signal

class MinimalStrategy:
    def __init__(self):
        self.name = "MinimalStrategy"
        self.version = "1.0.0"
        self.risk_level = RiskLevel.LOW
        self.resource_limits = StrategyResourceLimits()
        self.strategy_id = ""
        self.deployment_id = ""
        self.symbols = []

    async def initialize(self, config: dict) -> None:
        pass

    async def on_market_data(self, data: MarketData) -> Signal | None:
        return None

    async def on_fill(self, fill: dict) -> None:
        pass

    async def on_cancel(self, cancel: dict) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def validate(self) -> ValidationResult:
        return ValidationResult.valid()

    def update_config(self, config: dict) -> ValidationResult:
        return ValidationResult.valid()

def get_plugin():
    return MinimalStrategy()
`,
  ma_cross: `from trader.core.application.strategy_protocol import (
    MarketData, StrategyResourceLimits, ValidationResult, RiskLevel
)
from trader.core.domain.models.signal import Signal, SignalType

class MACrossStrategy:
    def __init__(self):
        self.name = "MACrossStrategy"
        self.version = "1.0.0"
        self.risk_level = RiskLevel.MEDIUM
        self.resource_limits = StrategyResourceLimits()
        self.strategy_id = ""
        self.deployment_id = ""
        self.symbols = []
        self.fast_period = 12
        self.slow_period = 26
        self.prices = []

    async def initialize(self, config: dict) -> None:
        self.fast_period = config.get("fast_period", 12)
        self.slow_period = config.get("slow_period", 26)
        self.prices = []

    async def on_market_data(self, data: MarketData) -> Signal | None:
        self.prices.append(float(data.price))
        if len(self.prices) < self.slow_period:
            return None
        fast_ma = sum(self.prices[-self.fast_period:]) / self.fast_period
        slow_ma = sum(self.prices[-self.slow_period:]) / self.slow_period
        prev_fast = sum(self.prices[-self.fast_period-1:-1]) / self.fast_period
        prev_slow = sum(self.prices[-self.slow_period-1:-1]) / self.slow_period
        if prev_fast <= prev_slow and fast_ma > slow_ma:
            return Signal(
                strategy_name=self.strategy_id,
                signal_type=SignalType.BUY,
                symbol=data.symbol,
                price=data.price,
                quantity=1,
                reason="ma_cross_up",
            )
        if prev_fast >= prev_slow and fast_ma < slow_ma:
            return Signal(
                strategy_name=self.strategy_id,
                signal_type=SignalType.SELL,
                symbol=data.symbol,
                price=data.price,
                quantity=1,
                reason="ma_cross_down",
            )
        return None

    async def on_fill(self, fill: dict) -> None:
        pass

    async def on_cancel(self, cancel: dict) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def validate(self) -> ValidationResult:
        return ValidationResult.valid()

    def update_config(self, config: dict) -> ValidationResult:
        return ValidationResult.valid()

def get_plugin():
    return MACrossStrategy()
`,
  threshold: `from trader.core.application.strategy_protocol import (
    MarketData, StrategyResourceLimits, ValidationResult, RiskLevel
)
from trader.core.domain.models.signal import Signal, SignalType
from decimal import Decimal

class ThresholdStrategy:
    def __init__(self):
        self.name = "ThresholdStrategy"
        self.version = "1.0.0"
        self.risk_level = RiskLevel.MEDIUM
        self.resource_limits = StrategyResourceLimits()
        self.strategy_id = ""
        self.deployment_id = ""
        self.symbols = []
        self.threshold = Decimal("0.001")
        self.quantity = Decimal("1")
        self._last_price = None

    async def initialize(self, config: dict) -> None:
        self.threshold = Decimal(str(config.get("threshold", 0.001)))
        self.quantity = Decimal(str(config.get("quantity", 1)))
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
                reason=f"up_move_{ratio:.4f}",
            )
        if ratio < -self.threshold:
            return Signal(
                strategy_name=self.strategy_id,
                signal_type=SignalType.SELL,
                symbol=data.symbol,
                price=data.price,
                quantity=self.quantity,
                reason=f"down_move_{ratio:.4f}",
            )
        return None

    async def on_fill(self, fill: dict) -> None:
        pass

    async def on_cancel(self, cancel: dict) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def validate(self) -> ValidationResult:
        return ValidationResult.valid()

    def update_config(self, config: dict) -> ValidationResult:
        return ValidationResult.valid()

def get_plugin():
    return ThresholdStrategy()
`,
}

export function Backtests() {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined)

  const { data: backtests, isLoading, isError, error, refetch, isFetching } = useBacktestList(
    statusFilter ? { status: statusFilter } : undefined
  )
  const { data: report, isLoading: isReportLoading } = useBacktestReport(selectedRunId ?? '')
  const { data: loadedStrategies, refetch: refetchLoaded } = useLoadedStrategies()
  const { error: createError } = useCreateBacktest()

  const [labForm, setLabForm] = useState({
    strategy_id: 'lab_strategy',
    name: 'Lab Strategy',
    description: 'Editable strategy code for fast backtest iteration',
    version: 1,
    engine: 'vectorbt' as BacktestEngine,
    symbols: 'BTCUSDT',
    start_ts_ms: Date.now() - 30 * 24 * 60 * 60 * 1000,
    end_ts_ms: Date.now(),
    venue: 'BINANCE',
    account_id: 'binance_demo',
    mode: 'paper' as DeploymentMode,
    deployment_id: '',
    feature_version: 'dev_smoke',
    initial_capital: 100000,
    fee_bps: 10,
    slippage_bps: 5,
    benchmark: 'BTCUSDT',
    data_mode: 'dev_smoke' as BacktestDataMode,
    risk_mode: 'risk_adjusted' as 'raw_only' | 'risk_adjusted' | 'event_replay',
    requested_by: 'console_user',
  })
  const [strategyCode, setStrategyCode] = useState(STRATEGY_TEMPLATES.threshold)
  const [templateKey, setTemplateKey] = useState('threshold')
  const [candidate, setCandidate] = useState<StrategyCandidate | null>(null)
  const [debugResult, setDebugResult] = useState<Record<string, unknown> | null>(null)
  const [labError, setLabError] = useState<string | null>(null)
  const [labMessage, setLabMessage] = useState<string | null>(null)
  const [isDebugging, setIsDebugging] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isBacktesting, setIsBacktesting] = useState(false)
  const [isValidating, setIsValidating] = useState(false)
  const [isPromoting, setIsPromoting] = useState(false)

  const runtimeInfo = useMemo(() => {
    return (loadedStrategies ?? []).find(item => {
      if (labForm.deployment_id) return item.deployment_id === labForm.deployment_id
      return item.strategy_id === labForm.strategy_id
    }) ?? null
  }, [loadedStrategies, labForm.deployment_id, labForm.strategy_id])

  const labSymbols = useMemo(
    () => labForm.symbols.split(',').map(s => s.trim()).filter(Boolean),
    [labForm.symbols],
  )

  const resolvedDeploymentId = useMemo(() => {
    if (labForm.deployment_id.trim()) return labForm.deployment_id.trim()
    const primarySymbol = labSymbols[0] ?? 'BTCUSDT'
    return buildDeploymentId(labForm.strategy_id, primarySymbol, labForm.mode, labForm.account_id)
  }, [labForm.account_id, labForm.deployment_id, labForm.mode, labForm.strategy_id, labSymbols])

  const handleTemplateChange = useCallback((key: string) => {
    setTemplateKey(key)
    setStrategyCode(STRATEGY_TEMPLATES[key])
  }, [])

  const handleDebug = async () => {
    setLabError(null)
    setLabMessage(null)
    setIsDebugging(true)
    try {
      let currentCandidate = candidate
      if (!currentCandidate) {
        currentCandidate = await researchAPI.createCandidate({
          strategy_id: labForm.strategy_id,
          name: labForm.name,
          description: labForm.description,
          code: strategyCode,
          created_by: labForm.requested_by,
        })
        setCandidate(currentCandidate)
      }
      const result = await researchAPI.debugCandidate(currentCandidate.candidate_id, {
        code: strategyCode,
        config: {},
      })
      setDebugResult(result as unknown as Record<string, unknown>)
      if (result.candidate) {
        setCandidate(result.candidate)
      }
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

  const handleSaveDraft = async () => {
    setLabError(null)
    setLabMessage(null)
    setIsSaving(true)
    try {
      let currentCandidate = candidate
      if (!currentCandidate) {
        currentCandidate = await researchAPI.createCandidate({
          strategy_id: labForm.strategy_id,
          name: labForm.name,
          description: labForm.description,
          code: strategyCode,
          created_by: labForm.requested_by,
        })
        setCandidate(currentCandidate)
        setLabMessage('Draft saved as new candidate.')
      } else {
        setLabMessage('Draft already exists. Use Debug to update code version.')
      }
    } catch (e) {
      setLabError(formatAPIError(e))
    } finally {
      setIsSaving(false)
    }
  }

  const handleSubmitBacktest = async () => {
    setLabError(null)
    setLabMessage(null)
    setIsBacktesting(true)
    try {
      let currentCandidate = candidate
      if (!currentCandidate) {
        setLabError('Please save draft and debug first.')
        setIsBacktesting(false)
        return
      }
      if (currentCandidate.status !== 'DEBUG_PASSED') {
        setLabError('Candidate must pass debug before backtest.')
        setIsBacktesting(false)
        return
      }
      const result = await researchAPI.runCandidateBacktest(currentCandidate.candidate_id, {
        dataset: {
          symbols: labSymbols,
          start_ts_ms: labForm.start_ts_ms,
          end_ts_ms: labForm.end_ts_ms,
          feature_version: labForm.feature_version,
          venue: labForm.venue,
          initial_capital: labForm.initial_capital,
          fee_bps: labForm.fee_bps,
          slippage_bps: labForm.slippage_bps,
          benchmark: labForm.benchmark || undefined,
          data_mode: labForm.data_mode,
          risk_mode: labForm.risk_mode,
        },
        requested_by: labForm.requested_by,
      })
      setCandidate(result)
      setLabMessage(`Backtest submitted. Candidate status: ${result.status}`)
    } catch (e) {
      setLabError(formatAPIError(e))
    } finally {
      setIsBacktesting(false)
    }
  }

  const handleValidate = async () => {
    setLabError(null)
    setLabMessage(null)
    setIsValidating(true)
    try {
      if (!candidate) {
        setLabError('No candidate to validate.')
        return
      }
      if (candidate.status !== 'BACKTEST_PASSED') {
        setLabError('Candidate must be BACKTEST_PASSED before validation.')
        return
      }
      const result = await researchAPI.validateCandidate(candidate.candidate_id)
      setCandidate(result)
      setLabMessage(`Validation complete. Status: ${result.status}`)
    } catch (e) {
      setLabError(formatAPIError(e))
    } finally {
      setIsValidating(false)
    }
  }

  const handlePromote = async () => {
    setLabError(null)
    setLabMessage(null)
    setIsPromoting(true)
    try {
      if (!candidate) {
        setLabError('No candidate to promote.')
        return
      }
      if (candidate.status !== 'VALIDATION_PASSED') {
        setLabError('Candidate must be VALIDATION_PASSED before promote.')
        return
      }
      const result = await researchAPI.promoteCandidate(candidate.candidate_id, {
        deployment_id: resolvedDeploymentId,
        symbols: labSymbols,
        account_id: labForm.account_id,
        venue: labForm.venue,
        mode: labForm.mode,
        version: `v${labForm.version}`,
        config: {},
      })
      setCandidate(result)
      setLabMessage(`Promoted to paper. Status: ${result.status}`)
      await refetchLoaded()
    } catch (e) {
      setLabError(formatAPIError(e))
    } finally {
      setIsPromoting(false)
    }
  }

  const canDebug = true
  const canSaveDraft = true
  const canSubmitBacktest = candidate?.status === 'DEBUG_PASSED'
  const canValidate = candidate?.status === 'BACKTEST_PASSED'
  const canPromote = candidate?.status === 'VALIDATION_PASSED'

  if (isLoading) return <div className="p-6"><LoadingState message="Loading backtests..." /></div>
  if (isError) return <div className="p-6"><ErrorState title="Failed to load backtests" message={formatAPIError(error)} onRetry={refetch} /></div>

  return (
    <div className="min-h-screen bg-gray-900">
      <PageHeader title="Backtests">
        {isFetching && <span className="text-xs text-accent-3">Refreshing...</span>}
        <div className="flex items-center gap-3">
          <select
            value={statusFilter ?? ''}
            onChange={(e) => setStatusFilter(e.target.value || undefined)}
            aria-label="Filter by status"
            className="rounded bg-gray-800 px-3 py-1.5 text-sm text-gray-300 border border-gray-700"
          >
            <option value="">All Status</option>
            <option value="RUNNING">Running</option>
            <option value="COMPLETED">Completed</option>
            <option value="FAILED">Failed</option>
          </select>
          <button onClick={() => refetch()} className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700">Refresh</button>
        </div>
      </PageHeader>

      <div className="p-6">
        <div className="mb-6 rounded-lg border border-gray-700 bg-gray-800/50 p-4">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-base font-semibold text-white">Strategy Lab</h2>
            <div className="text-xs text-gray-400">
              deployment: <span className="text-gray-200">{runtimeInfo?.deployment_id ?? resolvedDeploymentId}</span>
              <span className="mx-2 text-gray-600">/</span>
              runtime: <span className="text-gray-200">{runtimeInfo?.status ?? 'NOT_LOADED'}</span>
              {candidate && (
                <>
                  <span className="mx-2 text-gray-600">/</span>
                  candidate: <span className="text-gray-200">{candidate.candidate_id}</span>
                  <span className="mx-2 text-gray-600">/</span>
                  status: <span className="text-gray-200">{candidate.status}</span>
                </>
              )}
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
              aria-label="Strategy ID"
              className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
              placeholder="strategy_id"
            />
            <input
              type="text"
              value={labForm.name}
              onChange={(e) => setLabForm({ ...labForm, name: e.target.value })}
              aria-label="Backtest name"
              className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
              placeholder="name"
            />
            <input
              type="text"
              value={labForm.symbols}
              onChange={(e) => setLabForm({ ...labForm, symbols: e.target.value })}
              aria-label="Trading symbols"
              className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
              placeholder="BTCUSDT,ETHUSDT"
            />
            <select
              value={labForm.data_mode}
              onChange={(e) => setLabForm({ ...labForm, data_mode: e.target.value as BacktestDataMode })}
              aria-label="Backtest data mode"
              className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
            >
              <option value="dev_smoke">dev_smoke</option>
              <option value="real_feature_store">real_feature_store</option>
            </select>
            <select
              value={labForm.risk_mode}
              onChange={(e) => setLabForm({ ...labForm, risk_mode: e.target.value as 'raw_only' | 'risk_adjusted' | 'event_replay' })}
              aria-label="Backtest risk mode"
              className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
            >
              <option value="raw_only">raw_only</option>
              <option value="risk_adjusted">risk_adjusted</option>
              <option value="event_replay">event_replay</option>
            </select>
            <input
              type="text"
              value={labForm.feature_version}
              onChange={(e) => setLabForm({ ...labForm, feature_version: e.target.value })}
              aria-label="Feature version"
              className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
              placeholder="feature_version"
            />
            <input
              type="number"
              value={labForm.initial_capital}
              onChange={(e) => setLabForm({ ...labForm, initial_capital: Number(e.target.value) })}
              aria-label="Initial capital"
              className="rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200"
              placeholder="initial capital"
            />
          </div>

          <div className="mb-2 flex items-center gap-2">
            <span className="text-xs text-gray-400">Template:</span>
            <select
              value={templateKey}
              onChange={(e) => handleTemplateChange(e.target.value)}
              className="rounded bg-gray-900 border border-gray-700 px-2 py-1 text-xs text-gray-200"
            >
              <option value="minimal">Minimal</option>
              <option value="ma_cross">MA Cross</option>
              <option value="threshold">Threshold</option>
            </select>
          </div>

          <div className="mb-4 rounded border border-gray-700 overflow-hidden" style={{ height: '400px' }}>
            <Editor
              height="100%"
              defaultLanguage="python"
              value={strategyCode}
              onChange={(value) => setStrategyCode(value ?? '')}
              theme="vs-dark"
              options={{
                minimap: { enabled: false },
                fontSize: 12,
                lineNumbers: 'on',
                roundedSelection: false,
                scrollBeyondLastLine: false,
                readOnly: false,
                automaticLayout: true,
              }}
            />
          </div>

          <div className="mb-2 flex flex-wrap items-center gap-2">
            <button
              onClick={handleSaveDraft}
              disabled={!canSaveDraft || isSaving}
              className="rounded bg-gray-700/40 px-3 py-1.5 text-xs text-gray-200 hover:bg-gray-700/60 disabled:opacity-60"
            >
              {isSaving ? 'Saving...' : 'Save Draft'}
            </button>
            <button
              onClick={handleDebug}
              disabled={!canDebug || isDebugging}
              className="rounded bg-indigo-900/40 px-3 py-1.5 text-xs text-indigo-200 hover:bg-indigo-900/60 disabled:opacity-60"
            >
              {isDebugging ? 'Debugging...' : 'Debug'}
            </button>
            <button
              onClick={handleSubmitBacktest}
              disabled={!canSubmitBacktest || isBacktesting}
              className="rounded bg-cyan-900/40 px-3 py-1.5 text-xs text-cyan-200 hover:bg-cyan-900/60 disabled:opacity-60"
            >
              {isBacktesting ? 'Backtesting...' : 'Submit Backtest Gate'}
            </button>
            <button
              onClick={handleValidate}
              disabled={!canValidate || isValidating}
              className="rounded bg-blue-900/40 px-3 py-1.5 text-xs text-blue-200 hover:bg-blue-900/60 disabled:opacity-60"
            >
              {isValidating ? 'Validating...' : 'Validate'}
            </button>
            <button
              onClick={handlePromote}
              disabled={!canPromote || isPromoting}
              className="rounded bg-emerald-900/40 px-3 py-1.5 text-xs text-emerald-200 hover:bg-emerald-900/60 disabled:opacity-60"
            >
              {isPromoting ? 'Promoting...' : 'Promote to Paper'}
            </button>
          </div>

          {debugResult && (
            <div className="mt-3 rounded bg-gray-950 border border-gray-700 p-3">
              <h3 className="text-xs font-semibold text-gray-300 mb-2">Debug Result</h3>
              <div className="grid gap-2 text-xs text-gray-400 md:grid-cols-2">
                <div>ok: <span className={debugResult.ok ? 'text-green-400' : 'text-red-400'}>{String(debugResult.ok)}</span></div>
                <div>signals: <span className="text-gray-200">{String((debugResult.signals as unknown[])?.length ?? 0)}</span></div>
              </div>
              {debugResult.errors && (debugResult.errors as string[]).length > 0 ? (
                <div className="mt-2 text-xs text-red-400">
                  Errors: {(debugResult.errors as string[]).join('; ')}
                </div>
              ) : null}
            </div>
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
