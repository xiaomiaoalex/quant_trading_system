import type { Alert } from '@/types';
import { SeverityBadge } from '@/components/ui';
import { formatTimestamp } from '@/utils';

interface AlertListProps {
  alerts: Alert[];
  isLoading?: boolean;
  onClearAlert?: (ruleName: string) => void;
}

export function AlertList({ alerts, isLoading, onClearAlert }: AlertListProps) {
  if (isLoading) {
    return (
      <div className="rounded-lg border border-gray-700/50 bg-gray-800/50">
        <div className="border-b border-gray-700/50 px-4 py-3">
          <h3 className="text-sm font-medium text-gray-300">Active Alerts</h3>
        </div>
        <div className="p-4">
          <div className="space-y-3">
            {[1, 2].map((i) => (
              <div key={i} className="h-16 animate-pulse rounded bg-gray-700/50" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-gray-700/50 bg-gray-800/50 overflow-hidden">
      <div className="border-b border-gray-700/50 px-4 py-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium text-gray-300">Active Alerts</h3>
          {alerts.length > 0 && (
            <span className="rounded-full bg-red-950/50 px-2 py-0.5 text-xs font-medium text-red-400">
              {alerts.length} active
            </span>
          )}
        </div>
      </div>
      {alerts.length === 0 ? (
        <div className="p-8 text-center">
          <div className="flex justify-center">
            <svg className="h-12 w-12 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
          </div>
          <p className="mt-2 text-sm text-gray-500">No active alerts</p>
        </div>
      ) : (
        <ul className="divide-y divide-gray-700/30">
          {alerts.map((alert) => (
            <li key={alert.alert_id} className="p-4">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <SeverityBadge severity={alert.severity} />
                    <span className="text-sm font-medium text-white truncate">
                      {alert.rule_name}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-gray-400 line-clamp-2">
                    {alert.message}
                  </p>
                  <div className="mt-2 flex items-center gap-4 text-xs text-gray-500">
                    <span>
                      <span className="font-medium text-gray-400">{alert.metric_key}</span>:{' '}
                      {alert.metric_value.toFixed(2)} (threshold: {alert.threshold})
                    </span>
                    <span>{formatTimestamp(alert.triggered_at)}</span>
                  </div>
                </div>
                {onClearAlert && (
                  <button
                    type="button"
                    onClick={() => onClearAlert(alert.rule_name)}
                    className="flex-shrink-0 rounded-md bg-gray-700/50 px-2 py-1 text-xs font-medium
                      text-gray-300 transition-colors hover:bg-gray-700 focus:outline-none
                      focus:ring-2 focus:ring-gray-500"
                  >
                    Clear
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
