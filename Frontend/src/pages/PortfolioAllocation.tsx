import { useCallback, useEffect, useMemo, useState } from 'react'
import { researchAPI } from '@/api'
import { ErrorState, LoadingState, EmptyState } from '@/components/ui'
import { PageHeader } from '@/components/layout'
import { formatAPIError } from '@/api/client'
import type { StrategyAllocationProfile, StrategyAllocationProfileUpdateRequest } from '@/types'

type AllocationMode = 'ABSOLUTE_NOTIONAL' | 'PERCENT_OF_NAV'
type NavSource = 'account_equity' | 'paper_nav' | 'manual'

const inputClass = 'rounded bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-gray-200'
const labelClass = 'mb-1 block text-xs font-medium uppercase text-gray-400'

function formatMoney(value?: number | null) {
  if (value === null || value === undefined) return '-'
  return new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 2,
    minimumFractionDigits: 0,
  }).format(value)
}

function formatPercent(value?: number | null) {
  if (value === null || value === undefined) return '-'
  return `${(value * 100).toFixed(1)}%`
}

export function PortfolioAllocation() {
  const [profiles, setProfiles] = useState<StrategyAllocationProfile[]>([])
  const [deploymentId, setDeploymentId] = useState('lab_strategy__btcusdt__paper__binance_demo')
  const [strategyId, setStrategyId] = useState('lab_strategy')
  const [allocationMode, setAllocationMode] = useState<AllocationMode>('ABSOLUTE_NOTIONAL')
  const [maxNotional, setMaxNotional] = useState(10000)
  const [targetWeightPercent, setTargetWeightPercent] = useState(20)
  const [navSource, setNavSource] = useState<NavSource>('manual')
  const [manualNav, setManualNav] = useState(50000)
  const [hardCapNotional, setHardCapNotional] = useState(10000)
  const [maxSymbolExposure, setMaxSymbolExposure] = useState(10000)
  const [maxPortfolioWeight, setMaxPortfolioWeight] = useState(20)
  const [minConfidence, setMinConfidence] = useState(50)
  const [allowShort, setAllowShort] = useState(true)
  const [enabled, setEnabled] = useState(true)
  const [priority, setPriority] = useState(100)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)

  const loadProfiles = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      setProfiles(await researchAPI.listAllocations())
    } catch (e) {
      setError(formatAPIError(e))
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadProfiles()
  }, [loadProfiles])

  const estimatedBudget = useMemo(() => {
    if (allocationMode === 'ABSOLUTE_NOTIONAL') return maxNotional
    const raw = manualNav * (targetWeightPercent / 100)
    return hardCapNotional > 0 ? Math.min(raw, hardCapNotional) : raw
  }, [allocationMode, hardCapNotional, manualNav, maxNotional, targetWeightPercent])

  const canSave =
    deploymentId.trim().length > 0 &&
    strategyId.trim().length > 0 &&
    maxSymbolExposure >= 0 &&
    maxPortfolioWeight >= 0 &&
    maxPortfolioWeight <= 100 &&
    minConfidence >= 0 &&
    minConfidence <= 100 &&
    (allocationMode === 'ABSOLUTE_NOTIONAL'
      ? maxNotional >= 0
      : targetWeightPercent >= 0 &&
        targetWeightPercent <= 100 &&
        (navSource !== 'manual' || manualNav > 0))

  const saveProfile = async () => {
    if (!canSave) return
    setError(null)
    setIsSaving(true)
    try {
      const request: StrategyAllocationProfileUpdateRequest = {
        strategy_id: strategyId.trim(),
        allocation_mode: allocationMode,
        max_notional: allocationMode === 'ABSOLUTE_NOTIONAL' ? maxNotional : null,
        max_symbol_exposure: maxSymbolExposure,
        max_portfolio_weight: maxPortfolioWeight / 100,
        min_confidence: minConfidence / 100,
        allow_short: allowShort,
        priority,
        enabled,
        target_weight: allocationMode === 'PERCENT_OF_NAV' ? targetWeightPercent / 100 : null,
        hard_cap_notional:
          allocationMode === 'PERCENT_OF_NAV' && hardCapNotional > 0 ? hardCapNotional : null,
        nav_source: allocationMode === 'PERCENT_OF_NAV' ? navSource : 'account_equity',
        manual_nav:
          allocationMode === 'PERCENT_OF_NAV' && navSource === 'manual' ? manualNav : null,
        updated_by: 'frontend',
      }
      await researchAPI.upsertAllocation(deploymentId.trim(), request)
      await loadProfiles()
    } catch (e) {
      setError(formatAPIError(e))
    } finally {
      setIsSaving(false)
    }
  }

  if (isLoading) {
    return (
      <div className="p-6">
        <LoadingState message="Loading allocation profiles..." />
      </div>
    )
  }
  if (error && profiles.length === 0) {
    return (
      <div className="p-6">
        <ErrorState title="Failed to load allocations" message={error} onRetry={loadProfiles} />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-900">
      <PageHeader title="Portfolio Allocation">
        <button
          onClick={loadProfiles}
          className="rounded-md bg-gray-800 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700"
        >
          Refresh
        </button>
      </PageHeader>

      <div className="p-6 space-y-6">
        {error && <div className="rounded bg-red-950/20 p-2 text-sm text-red-400">{error}</div>}

        <div className="border border-gray-700 bg-gray-800/50 p-4">
          <div className="mb-4 flex w-fit rounded border border-gray-700 bg-gray-900 p-1">
            {[
              ['ABSOLUTE_NOTIONAL', 'Absolute'],
              ['PERCENT_OF_NAV', 'NAV %'],
            ].map(([mode, label]) => (
              <button
                key={mode}
                type="button"
                onClick={() => setAllocationMode(mode as AllocationMode)}
                className={`px-3 py-1.5 text-sm ${
                  allocationMode === mode
                    ? 'bg-emerald-900/60 text-emerald-100'
                    : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="grid gap-3 md:grid-cols-4">
            <label>
              <span className={labelClass}>Deployment</span>
              <input
                value={deploymentId}
                onChange={(e) => setDeploymentId(e.target.value)}
                className={`${inputClass} w-full`}
                placeholder="deployment_id"
              />
            </label>
            <label>
              <span className={labelClass}>Strategy</span>
              <input
                value={strategyId}
                onChange={(e) => setStrategyId(e.target.value)}
                className={`${inputClass} w-full`}
                placeholder="strategy_id"
              />
            </label>
            {allocationMode === 'ABSOLUTE_NOTIONAL' ? (
              <label>
                <span className={labelClass}>Max Notional</span>
                <input
                  type="number"
                  min={0}
                  value={maxNotional}
                  onChange={(e) => setMaxNotional(Number(e.target.value))}
                  className={`${inputClass} w-full`}
                />
              </label>
            ) : (
              <label>
                <span className={labelClass}>Target Weight %</span>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={targetWeightPercent}
                  onChange={(e) => setTargetWeightPercent(Number(e.target.value))}
                  className={`${inputClass} w-full`}
                />
              </label>
            )}
            <label>
              <span className={labelClass}>Symbol Cap</span>
              <input
                type="number"
                min={0}
                value={maxSymbolExposure}
                onChange={(e) => setMaxSymbolExposure(Number(e.target.value))}
                className={`${inputClass} w-full`}
              />
            </label>

            {allocationMode === 'PERCENT_OF_NAV' && (
              <>
                <label>
                  <span className={labelClass}>NAV Source</span>
                  <select
                    value={navSource}
                    onChange={(e) => setNavSource(e.target.value as NavSource)}
                    className={`${inputClass} w-full`}
                  >
                    <option value="manual">Manual</option>
                    <option value="paper_nav">Paper NAV</option>
                    <option value="account_equity">Account Equity</option>
                  </select>
                </label>
                <label>
                  <span className={labelClass}>Manual NAV</span>
                  <input
                    type="number"
                    min={0}
                    value={manualNav}
                    disabled={navSource !== 'manual'}
                    onChange={(e) => setManualNav(Number(e.target.value))}
                    className={`${inputClass} w-full disabled:opacity-40`}
                  />
                </label>
                <label>
                  <span className={labelClass}>Hard Cap</span>
                  <input
                    type="number"
                    min={0}
                    value={hardCapNotional}
                    onChange={(e) => setHardCapNotional(Number(e.target.value))}
                    className={`${inputClass} w-full`}
                  />
                </label>
                <div>
                  <span className={labelClass}>Estimate</span>
                  <div className="border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200">
                    {formatMoney(estimatedBudget)}
                  </div>
                </div>
              </>
            )}

            <label>
              <span className={labelClass}>Portfolio Weight %</span>
              <input
                type="number"
                min={0}
                max={100}
                value={maxPortfolioWeight}
                onChange={(e) => setMaxPortfolioWeight(Number(e.target.value))}
                className={`${inputClass} w-full`}
              />
            </label>
            <label>
              <span className={labelClass}>Min Confidence %</span>
              <input
                type="number"
                min={0}
                max={100}
                value={minConfidence}
                onChange={(e) => setMinConfidence(Number(e.target.value))}
                className={`${inputClass} w-full`}
              />
            </label>
            <label>
              <span className={labelClass}>Priority</span>
              <input
                type="number"
                min={0}
                value={priority}
                onChange={(e) => setPriority(Number(e.target.value))}
                className={`${inputClass} w-full`}
              />
            </label>
            <div className="flex items-end gap-4">
              <label className="flex items-center gap-2 text-sm text-gray-300">
                <input
                  type="checkbox"
                  checked={allowShort}
                  onChange={(e) => setAllowShort(e.target.checked)}
                  className="rounded border-gray-700 bg-gray-900"
                />
                Short
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-300">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                  className="rounded border-gray-700 bg-gray-900"
                />
                Enabled
              </label>
            </div>
            <button
              onClick={saveProfile}
              disabled={!canSave || isSaving}
              className="bg-emerald-900/40 px-3 py-2 text-sm text-emerald-200 hover:bg-emerald-900/60 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {isSaving ? 'Saving...' : 'Save Budget'}
            </button>
          </div>
        </div>

        {profiles.length === 0 ? (
          <EmptyState
            title="No Allocation Profiles"
            message="No allocation profiles found. Save a budget to create one."
            action={{ label: 'Refresh', onClick: loadProfiles }}
          />
        ) : (
          <div className="overflow-hidden border border-gray-700">
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-700">
                <thead className="bg-gray-800">
                  <tr>
                    {[
                      'Deployment',
                      'Strategy',
                      'Mode',
                      'Weight',
                      'Basis NAV',
                      'Effective',
                      'Remaining',
                      'Priority',
                      'Enabled',
                    ].map((header) => (
                      <th
                        key={header}
                        scope="col"
                        className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-400"
                      >
                        {header}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800 bg-gray-900">
                  {profiles.map((profile) => (
                    <tr key={profile.deployment_id} className="table-row-hover">
                      <td className="px-4 py-3 text-xs font-mono text-gray-200">
                        {profile.deployment_id}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-300">{profile.strategy_id}</td>
                      <td className="px-4 py-3 text-xs text-gray-300">
                        {profile.allocation_mode === 'PERCENT_OF_NAV' ? 'NAV %' : 'Absolute'}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-300">
                        {formatPercent(profile.target_weight)}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-300">
                        {formatMoney(profile.basis_nav)}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-300">
                        {formatMoney(profile.effective_max_notional || profile.max_notional)}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-300">
                        {formatMoney(profile.remaining_notional)}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-300">{profile.priority}</td>
                      <td className="px-4 py-3 text-xs">
                        <span className={profile.enabled ? 'text-emerald-400' : 'text-gray-500'}>
                          {profile.enabled ? 'yes' : 'no'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
