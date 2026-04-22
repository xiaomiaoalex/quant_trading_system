import { useState } from 'react'
import { clsx } from 'clsx'
import { useSafetyGate } from '@/hooks'
import { ConfirmDialog } from '@/components/ui'

export function SafetyGateControl() {
  const { status, isLoading, isPending, enable, disable } = useSafetyGate()
  const [confirmEnable, setConfirmEnable] = useState(false)
  const [confirmDisable, setConfirmDisable] = useState(false)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  const isEnabled = status?.live_trading_enabled ?? false
  const ksLevel = status?.killswitch_level ?? 0
  const ksReason = status?.killswitch_reason

  const handleEnable = async () => {
    setConfirmEnable(false)
    try {
      await enable(true)
      setSuccessMsg('Live trading enabled')
      setTimeout(() => setSuccessMsg(null), 3000)
    } catch {
      // error handled by hook
    }
  }

  const handleDisable = async () => {
    setConfirmDisable(false)
    try {
      await disable()
      setSuccessMsg('Live trading disabled')
      setTimeout(() => setSuccessMsg(null), 3000)
    } catch {
      // error handled by hook
    }
  }

  const blockedByKillSwitch = ksLevel >= 2

  return (
    <>
      <div
        className={clsx(
          'rounded-lg border p-4',
          isEnabled
            ? 'border-green-900/50 bg-green-950/10'
            : 'border-gray-700/50 bg-gray-800/50',
        )}
      >
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-medium text-gray-300">Safety Gate</h3>
              {isLoading && (
                <span className="text-xs text-gray-500 animate-pulse">Loading...</span>
              )}
            </div>
            <p className="text-xs text-gray-500 mt-0.5">
              Controls whether strategy signals are executed as real orders.
              Default: <span className="text-yellow-400">DISABLED</span> (safe mode).
            </p>
            {ksReason && (
              <p className="text-xs text-gray-500 mt-0.5">
                KillSwitch: L{ksLevel} — {ksReason}
              </p>
            )}
          </div>

          <div className="flex items-center gap-3">
            {/* Status badge */}
            <div
              className={clsx(
                'rounded-full px-3 py-1 text-xs font-medium',
                isEnabled
                  ? 'bg-green-900/40 text-green-300'
                  : 'bg-gray-700/50 text-gray-400',
              )}
            >
              {isEnabled ? 'ENABLED' : 'DISABLED'}
            </div>

            {/* Enable button */}
            <button
              type="button"
              disabled={isEnabled || blockedByKillSwitch || isPending}
              onClick={() => setConfirmEnable(true)}
              className={clsx(
                'rounded px-3 py-1.5 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-900',
                isEnabled || blockedByKillSwitch
                  ? 'bg-green-800/30 text-green-500 cursor-not-allowed'
                  : 'bg-green-700 text-green-100 hover:bg-green-600 focus:ring-green-500',
              )}
              title={blockedByKillSwitch ? 'Blocked by KillSwitch L2+' : undefined}
            >
              Enable
            </button>

            {/* Disable button */}
            <button
              type="button"
              disabled={!isEnabled || isPending}
              onClick={() => setConfirmDisable(true)}
              className={clsx(
                'rounded px-3 py-1.5 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-900',
                !isEnabled
                  ? 'bg-gray-700/30 text-gray-500 cursor-not-allowed'
                  : 'bg-red-800/50 text-red-200 hover:bg-red-700 focus:ring-red-500',
              )}
            >
              Disable
            </button>
          </div>
        </div>

        {successMsg && (
          <div className="mt-3 pt-3 border-t border-green-900/30">
            <p className="text-xs text-green-400">{successMsg}</p>
          </div>
        )}

        {blockedByKillSwitch && (
          <div className="mt-3 pt-3 border-t border-yellow-900/30">
            <p className="text-xs text-yellow-400">
              Cannot enable live trading while KillSwitch is at L{ksLevel}. Lower KillSwitch to L0 or L1 first.
            </p>
          </div>
        )}
      </div>

      <ConfirmDialog
        isOpen={confirmEnable}
        title="Enable Live Trading"
        message={
          '⚠️ WARNING: Enabling live trading will execute real orders on the exchange using real funds.\n\n' +
          'This is irreversible for open positions.\n\n' +
          'Are you sure you want to enable live trading?'
        }
        confirmLabel="Enable Live Trading"
        cancelLabel="Cancel"
        variant="danger"
        isLoading={isPending}
        onConfirm={handleEnable}
        onCancel={() => setConfirmEnable(false)}
      />

      <ConfirmDialog
        isOpen={confirmDisable}
        title="Disable Live Trading"
        message={
          'Disabling live trading will stop all new order execution. ' +
          'Existing open positions will remain until manually closed.\n\n' +
          'Are you sure?'
        }
        confirmLabel="Disable"
        cancelLabel="Cancel"
        variant="warning"
        isLoading={isPending}
        onConfirm={handleDisable}
        onCancel={() => setConfirmDisable(false)}
      />
    </>
  )
}
