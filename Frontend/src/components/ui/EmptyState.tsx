import { clsx } from 'clsx';

interface EmptyStateProps {
  title?: string;
  message?: string;
  action?: {
    label: string;
    onClick: () => void;
  };
  icon?: React.ReactNode;
  className?: string;
}

export function EmptyState({
  title = 'No Data',
  message,
  action,
  icon,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={clsx('flex h-full min-h-[200px] w-full flex-col items-center justify-center gap-3 rounded-lg border border-gray-700/50 bg-gray-800/20 p-6',
        className,
      )}
      role="status"
    >
      {icon ? (
        <div className="text-gray-500">{icon}</div>
      ) : (
        <svg
          className="h-12 w-12 text-gray-600"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          aria-hidden="true"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"
          />
        </svg>
      )}
      <h3 className="text-lg font-medium text-gray-300">{title}</h3>
      {message && (
        <p className="max-w-md text-center text-sm text-gray-500">{message}</p>
      )}
      {action && (
        <button
          type="button"
          onClick={action.onClick}
          className="mt-2 rounded-md bg-blue-900/30 px-4 py-2 text-sm font-medium text-blue-300 transition-colors hover:bg-blue-900/50 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-gray-900"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
