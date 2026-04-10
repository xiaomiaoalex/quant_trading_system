import { clsx } from 'clsx';

interface StaleBannerProps {
  lastUpdate: string | null;
  onRefresh?: () => void;
}

export function StaleBanner({ lastUpdate, onRefresh }: StaleBannerProps) {
  return (
    <div
      className={clsx(
        'flex items-center justify-between gap-4 rounded-lg border border-yellow-900/50 bg-yellow-950/20 px-4 py-2',
      )}
      role="alert"
    >
      <div className="flex items-center gap-2">
        <svg className="h-4 w-4 text-yellow-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
          />
        </svg>
        <p className="text-sm text-yellow-300">
          Data may be stale
          {lastUpdate && (
            <span className="ml-1 text-yellow-400/70">
              (Last update: {new Date(lastUpdate).toLocaleTimeString()})
            </span>
          )}
        </p>
      </div>
      {onRefresh && (
        <button
          type="button"
          onClick={onRefresh}
          className="rounded-md bg-yellow-900/30 px-3 py-1 text-xs font-medium text-yellow-300
            transition-colors hover:bg-yellow-900/50 focus:outline-none focus:ring-2
            focus:ring-yellow-500"
        >
          Refresh
        </button>
      )}
    </div>
  );
}
