import { useMemo, useState, useCallback, useEffect } from 'react'
import Editor from '@monaco-editor/react'
import { useBacktestList, useBacktestReport, useCreateBacktest, useLoadedStrategies } from '@/hooks'
import { LoadingState, ErrorState } from '@/components/ui'
import { BacktestList, BacktestDetailPanel } from '@/components/backtests'
import { PageHeader } from '@/components/layout'
import { researchAPI } from '@/api'
import { formatAPIError, isAPIError } from '@/api/client'
import { useNavigate } from 'react-router-dom'
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
  const navigate = useNavigate()
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
    engine: 'vectorbt' as BacktestEngine,
    symbols: 'BTCUSDT',
    start_ts_ms: Date.now() - 30 * 24 * 60 * 60 * 1000,
    end_ts_ms: Date.now(),
    venue: 'BINANCE',
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
      if (candidate?.deployment_id) return item.deployment_id === candidate.deployment_id
      return item.strategy_id === labForm.strategy_id
    }) ?? null
  }, [candidate?.deployment_id, loadedStrategies, labForm.strategy_id])

  const labSymbols = useMemo(
    () => labForm.symbols.split(',').map(s => s.trim()).filter(Boolean),
    [labForm.symbols],
  )

  useEffect(() => {
    if (!candidate || candidate.status !== 'BACKTEST_RUNNING') return

    let cancelled = false
    const refreshCandidate = async () => {
      try {
        const latest = await researchAPI.getCandidate(candidate.candidate_id)
        if (cancelled) return
        setCandidate(latest)
        if (latest.backtest_run_id) {
          setSelectedRunId(latest.backtest_run_id)
        }
        if (latest.status !== 'BACKTEST_RUNNING') {
          void refetch()
          setLabMessage(`Backtest finished. Candidate status: ${latest.status}`)
        }
      } catch (e) {
        if (!cancelled) {
          setLabError(formatAPIError(e))
        }
      }
    }

    void refreshCandidate()
    const intervalId = window.setInterval(refreshCandidate, 2000)
    return () => {
      cancelled = true
      window.clearInterval(intervalId)
    }
  }, [candidate?.candidate_id, candidate?.status, refetch])

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
      if (result.backtest_run_id) {
        setSelectedRunId(result.backtest_run_id)
      }
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
      const result = await researchAPI.promoteCandidateToPaper(candidate.candidate_id)
      setCandidate({
        ...candidate,
        status: result.status,
        deployment_id: result.deployment_id,
        code_version: result.code_version ?? candidate.code_version,
        updated_at: result.promoted_at,
      })
      setLabMessage(`已提升至纸上交易。Deployment ID：${result.deployment_id}。前往「Strategies」页面查看运行状态。`)
      await refetchLoaded()
    } catch (e: unknown) {
      // client.ts interceptor converts Axios errors to APIError { code, message, details }
      if (isAPIError(e)) {
        const code = e.code
        const details = e.details as Record<string, unknown> | undefined
        if (code === 'PROMOTE_LOAD_FAILED') {
          setLabError('加载失败（PROMOTE_LOAD_FAILED）：运行时初始化异常，候选已自动回滚至 VALIDATION_PASSED，可重试。')
        } else if (code === 'PROMOTE_CONFLICT') {
          setLabError('并发冲突（PROMOTE_CONFLICT）：该候选正在被提升中，请稍后重试。')
        } else if (code === 'INVALID_STATE') {
          const currentState = details?.current_state ?? '?'
          setLabError(`状态不符（当前：${currentState}）：需要 VALIDATION_PASSED 才能 promote。`)
        } else {
          setLabError(formatAPIError(e))
        }
      } else {
        setLabError(formatAPIError(e))
      }
    } finally {
      setIsPromoting(false)
    }
  }

  const canSubmitBacktest = candidate?.status === 'DEBUG_PASSED'
  const canValidate = candidate?.status === 'BACKTEST_PASSED'
  const canPromote = candidate?.status === 'VALIDATION_PASSED'

  // 状态驱动：主操作按钮（同一时刻只有一个主操作）
  type PrimaryAction = 'save' | 'debug' | 'backtest' | 'validate' | 'promote' | 'approved' | 'running'
  const primaryAction: PrimaryAction = (() => {
    if (!candidate) return 'save'
    switch (candidate.status) {
      case 'DRAFT': return 'debug'
      case 'DEBUG_PASSED': return 'backtest'
      case 'BACKTEST_RUNNING': return 'running'
      case 'BACKTEST_PASSED': return 'validate'
      case 'VALIDATION_PASSED': return 'promote'
      case 'APPROVED_FOR_PAPER': return 'approved'
      case 'PAPER_RUNNING': return 'approved'
      default: return 'save'
    }
  })()

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
              deployment: <span className="text-gray-200">{candidate?.deployment_id ?? runtimeInfo?.deployment_id ?? '-'}</span>
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

          {/* 状态驱动操作区：主操作 + 辅助操作 */}
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {/* 主操作：根据状态动态显示，每个状态只有一个主入口 */}

            {/* 无候选：Save Draft 是唯一入口 */}
            {primaryAction === 'save' && (
              <button
                onClick={handleSaveDraft}
                disabled={isSaving}
                className="rounded bg-gray-600/60 px-4 py-1.5 text-xs font-medium text-gray-100 hover:bg-gray-600/80 disabled:opacity-60"
              >
                {isSaving ? 'Saving...' : '▶ Save Draft'}
              </button>
            )}

            {/* DRAFT：Debug 是主操作，Save Draft 作为灰色辅助 */}
            {primaryAction === 'debug' && (
              <>
                <button
                  onClick={handleDebug}
                  disabled={isDebugging}
                  className="rounded bg-indigo-600/60 px-4 py-1.5 text-xs font-medium text-indigo-100 hover:bg-indigo-600/80 disabled:opacity-60"
                >
                  {isDebugging ? 'Debugging...' : '▶ Debug'}
                </button>
                <button
                  onClick={handleSaveDraft}
                  disabled={isSaving}
                  className="rounded bg-gray-700/30 px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-700/50 disabled:opacity-40"
                >
                  {isSaving ? 'Saving...' : 'Save Draft'}
                </button>
              </>
            )}

            {primaryAction === 'backtest' && (
              <button
                onClick={handleSubmitBacktest}
                disabled={!canSubmitBacktest || isBacktesting}
                className="rounded bg-cyan-600/60 px-4 py-1.5 text-xs font-medium text-cyan-100 hover:bg-cyan-600/80 disabled:opacity-60"
              >
                {isBacktesting ? 'Backtesting...' : '▶ Submit Backtest'}
              </button>
            )}

            {primaryAction === 'running' && (
              <span className="flex items-center gap-1.5 text-xs text-blue-400">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse" />
                Backtest running...
              </span>
            )}

            {primaryAction === 'validate' && (
              <button
                onClick={handleValidate}
                disabled={!canValidate || isValidating}
                className="rounded bg-blue-600/60 px-4 py-1.5 text-xs font-medium text-blue-100 hover:bg-blue-600/80 disabled:opacity-60"
              >
                {isValidating ? 'Validating...' : '▶ Validate'}
              </button>
            )}

            {primaryAction === 'promote' && (
              <button
                onClick={handlePromote}
                disabled={!canPromote || isPromoting}
                className="rounded bg-emerald-600/70 px-5 py-1.5 text-xs font-semibold text-emerald-50 hover:bg-emerald-600/90 shadow shadow-emerald-900/30 disabled:opacity-60"
              >
                {isPromoting ? 'Promoting...' : '🚀 Promote to Paper'}
              </button>
            )}

            {/* APPROVED_FOR_PAPER / PAPER_RUNNING：显示状态 + 跳转到部署监控 */}
            {primaryAction === 'approved' && (
              <div className="flex items-center gap-2">
                <span className="flex items-center gap-1.5 rounded bg-emerald-950/40 px-3 py-1.5 text-xs text-emerald-300">
                  <span>✓ Approved for paper trading</span>
                  {candidate?.deployment_id && (
                    <span className="font-mono text-emerald-500/70">· {candidate.deployment_id}</span>
                  )}
                </span>
                <button
                  onClick={() => navigate('/strategies')}
                  className="rounded bg-emerald-800/50 px-3 py-1.5 text-xs font-medium text-emerald-200 hover:bg-emerald-800/70"
                >
                  查看运行状态 →
                </button>
              </div>
            )}

            {/* 状态指示步骤条 */}
            <div className="ml-auto flex items-center gap-1 text-xs text-gray-600">
              {(['DRAFT','DEBUG_PASSED','BACKTEST_PASSED','VALIDATION_PASSED','APPROVED_FOR_PAPER'] as const).map((s, i) => {
                const statuses = ['DRAFT','DEBUG_PASSED','BACKTEST_RUNNING','BACKTEST_PASSED','VALIDATION_PASSED','APPROVED_FOR_PAPER','PAPER_RUNNING']
                const currentIdx = candidate ? statuses.indexOf(candidate.status) : -1
                const stepIdx = statuses.indexOf(s)
                const done = currentIdx >= stepIdx && currentIdx !== -1
                const labels = ['Draft','Debug','Backtest','Validate','Promoted']
                return (
                  <span key={s} className={`flex items-center gap-1 ${done ? 'text-gray-400' : 'text-gray-700'}`}>
                    {i > 0 && <span className="text-gray-700">›</span>}
                    <span className={done ? 'text-gray-300' : ''}>{labels[i]}</span>
                  </span>
                )
              })}
            </div>
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
