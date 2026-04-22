import { useState } from 'react'
import { clsx } from 'clsx'
import { ConfirmDialog } from '@/components/ui'
import { KILLSWITCH_LEVELS, KILLSWITCH_DISPLAY, type KillSwitchLevel } from '@/types'
import { useSetKillSwitch } from '@/hooks'

interface KillSwitchControlProps {
  currentLevel: KillSwitchLevel
  scope?: string
}

export function KillSwitchControl({ currentLevel, scope = 'GLOBAL' }: KillSwitchControlProps) {
  const { setKillSwitch, isPending } = useSetKillSwitch()
  const [confirmLevel, setConfirmLevel] = useState<KillSwitchLevel | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  const handleLevelClick = (level: KillSwitchLevel) => {
    if (level === currentLevel) return
    setConfirmLevel(level)
  }

  const handleConfirm = async () => {
    if (confirmLevel === null) return

    const levelNames = {
      [KILLSWITCH_LEVELS.NORMAL]: 'Normal',
      [KILLSWITCH_LEVELS.NO_NEW_POSITIONS]: 'No New Positions',
      [KILLSWITCH_LEVELS.CLOSE_ONLY]: 'Close Only',
      [KILLSWITCH_LEVELS.FULL_STOP]: 'Full Stop',
    }

    const success = await setKillSwitch(
      confirmLevel,
      `Manual override via Monitor page by user`
    )

    if (success) {
      setSuccessMsg(`KillSwitch set to ${levelNames[confirmLevel]}`)
      setTimeout(() => setSuccessMsg(null), 3000)
    }

    setConfirmLevel(null)
  }

  const levels: KillSwitchLevel[] = [
    KILLSWITCH_LEVELS.NORMAL,
    KILLSWITCH_LEVELS.NO_NEW_POSITIONS,
    KILLSWITCH_LEVELS.CLOSE_ONLY,
    KILLSWITCH_LEVELS.FULL_STOP,
  ]

  const isDanger = confirmLevel !== null && confirmLevel >= KILLSWITCH_LEVELS.CLOSE_ONLY

  return (
    <>
      <div
        className={clsx(
          'rounded-lg border p-4',
          currentLevel > KILLSWITCH_LEVELS.NORMAL
            ? 'border-red-900/50 bg-red-950/20'
            : 'border-gray-700/50 bg-gray-800/50'
        )}
      >
        <div className="flex items-center justify-between gap-4">
          <div>
            <h3 className="text-sm font-medium text-gray-300">KillSwitch Control</h3>
            <p className="text-xs text-gray-500 mt-0.5">Manual override for {scope}</p>
          </div>

          <div className="flex gap-2">
            {levels.map((level) => {
              const config = KILLSWITCH_DISPLAY[level]
              const isActive = level === currentLevel
              const isDisabled = isActive || isPending

              return (
                <button
                  key={level}
                  type="button"
                  disabled={isDisabled}
                  onClick={() => handleLevelClick(level)}
                  className={clsx(
                    'rounded px-3 py-1.5 text-xs font-medium transition-colors',
                    'focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-900',
                    isActive
                      ? 'bg-red-900/50 text-red-300 cursor-default'
                      : isDisabled
                      ? 'bg-gray-700/50 text-gray-500 cursor-not-allowed'
                      : 'bg-gray-700 text-gray-300 hover:bg-gray-600',
                    level >= KILLSWITCH_LEVELS.CLOSE_ONLY &&
                      !isActive &&
                      'hover:bg-red-900/30 hover:text-red-300',
                    level === KILLSWITCH_LEVELS.FULL_STOP &&
                      !isActive &&
                      'hover:bg-red-900/50 hover:text-red-200',
                  )}
                >
                  L{level} {config.label}
                </button>
              )
            })}
          </div>
        </div>

        {successMsg && (
          <div className="mt-3 pt-3 border-t border-green-900/30">
            <p className="text-xs text-green-400">{successMsg}</p>
          </div>
        )}
      </div>

      <ConfirmDialog
        isOpen={confirmLevel !== null}
        title={`Set KillSwitch to L${confirmLevel}`}
        message={
          confirmLevel !== null && confirmLevel >= KILLSWITCH_LEVELS.CLOSE_ONLY
            ? `WARNING: Setting KillSwitch to "${KILLSWITCH_DISPLAY[confirmLevel].label}" will halt all trading operations. Only position close operations will be allowed. Are you sure?`
            : `Are you sure you want to set KillSwitch to "${confirmLevel !== null ? KILLSWITCH_DISPLAY[confirmLevel].label : ''}"?`
        }
        confirmLabel={confirmLevel !== null ? `Set to L${confirmLevel}` : 'Confirm'}
        cancelLabel="Cancel"
        variant={isDanger ? 'danger' : 'warning'}
        isLoading={isPending}
        onConfirm={handleConfirm}
        onCancel={() => setConfirmLevel(null)}
      />
    </>
  )
}