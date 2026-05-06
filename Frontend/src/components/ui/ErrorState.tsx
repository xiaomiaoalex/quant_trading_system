import { clsx } from 'clsx'

interface ErrorStateProps {
  title?: string
  message: string | null
  onRetry?: () => void
  className?: string
}

export function ErrorState({ title = 'Error', message, onRetry, className }: ErrorStateProps) {
  return (
    <div
      className={clsx(
        'flex h-full min-h-[200px] w-full flex-col items-center justify-center gap-4 rounded-lg border border-red-900/40 bg-surface-3/60 p-6',
        className
      )}
      role="alert"
    >
      <div className="flex items-center gap-3">
        <div className="rounded-full bg-red-900/20 p-1.5">
          <svg
            className="h-5 w-5 text-red-400"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
            />
          </svg>
        </div>
        <h3 className="text-lg font-semibold text-red-300">{title}</h3>
      </div>
      {message && (
        <p className="max-w-md text-center text-sm text-accent-3 font-mono leading-relaxed">
          {message}
        </p>
      )}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 rounded-md bg-red-900/20 px-4 py-2 text-sm font-medium text-red-300 transition-colors hover:bg-red-900/40 focus-visible:ring-2 focus-visible:ring-red-500"
        >
          Retry
        </button>
      )}
    </div>
  )
}
