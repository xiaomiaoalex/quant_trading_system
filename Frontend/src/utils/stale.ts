// Stale detection utilities

/**
 * Check if a timestamp indicates stale data
 * @param timestamp - ISO 8601 timestamp string or Date
 * @param thresholdMs - Stale threshold in milliseconds (default: 60 seconds)
 */
export function isStale(
  timestamp: string | Date | null | undefined,
  thresholdMs = 60_000
): boolean {
  if (!timestamp) return true
  const time = typeof timestamp === 'string' ? new Date(timestamp).getTime() : timestamp.getTime()
  return Date.now() - time > thresholdMs
}

/**
 * Get human-readable time since timestamp
 * @param timestamp - ISO 8601 timestamp string or Date
 */
export function timeSince(timestamp: string | Date | null | undefined): string {
  if (!timestamp) return 'unknown'
  const time = typeof timestamp === 'string' ? new Date(timestamp).getTime() : timestamp.getTime()
  const seconds = Math.floor((Date.now() - time) / 1000)

  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

/**
 * Format timestamp for display
 * @param timestamp - ISO 8601 timestamp string or Date
 */
export function formatTimestamp(timestamp: string | Date | null | undefined): string {
  if (!timestamp) return '—'
  const date = typeof timestamp === 'string' ? new Date(timestamp) : timestamp
  return date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

/**
 * Format milliseconds timestamp for display
 * @param tsMs - Milliseconds timestamp
 */
export function formatTsMs(tsMs: number | null | undefined): string {
  if (!tsMs) return '—'
  return formatTimestamp(new Date(tsMs))
}
