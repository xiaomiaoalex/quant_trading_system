import { clsx } from 'clsx';

interface ErrorStateProps {
  title?: string;
  message: string | null;
  onRetry?: () => void;
  className?: string;
}

export function ErrorState({
  title = 'Error',
  message,
  onRetry,
  className,
}: ErrorStateProps) {
  return (
    <div
      className={clsx('flex h-full min-h-[200px] w-full flex-col items-center justify-center gap-4 rounded-lg border border-red-900/50 bg-red-950/20 p-6',
        className,
      )}
      role="alert"
    >
      <div className="flex items-center gap-3">
        <svg
          className="h-6 w-6 text-red-500"
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
        <h3 className="text-lg font-semibold text-red-400">{title}</h3>
      </div>
      {message && (
        <p className="max-w-md text-center text-sm text-gray-400">{message}</p>
      )}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 rounded-md bg-red-900/30 px-4 py-2 text-sm font-medium text-red-300 transition-colors hover:bg-red-900/50 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 focus:ring-offset-gray-900"
        >
          Retry
        </button>
      )}
    </div>
  );
}
