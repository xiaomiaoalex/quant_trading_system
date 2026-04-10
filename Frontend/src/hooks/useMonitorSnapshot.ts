import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { monitorAPI } from '@/api'
import type { MonitorSnapshot, SystemHealthState, Alert } from '@/types'
import { deriveSystemHealthState, isSnapshotStale } from '@/types'
import { formatAPIError } from '@/api/client'

// Query keys for monitor
export const monitorKeys = {
  all: ['monitor'] as const,
  snapshot: () => [...monitorKeys.all, 'snapshot'] as const,
  alerts: () => [...monitorKeys.all, 'alerts'] as const,
  killswitch: () => [...monitorKeys.all, 'killswitch'] as const,
  readiness: () => [...monitorKeys.all, 'readiness'] as const,
  dependencies: () => [...monitorKeys.all, 'dependencies'] as const,
}

// Snapshot query with derived state
interface UseMonitorSnapshotOptions {
  staleThresholdMs?: number
  refetchInterval?: number
}

interface UseMonitorSnapshotResult {
  snapshot: MonitorSnapshot | null
  isLoading: boolean
  isError: boolean
  error: string | null
  isStale: boolean
  healthState: SystemHealthState
  refetch: () => void
}

export function useMonitorSnapshot(
  options: UseMonitorSnapshotOptions = {}
): UseMonitorSnapshotResult {
  const { staleThresholdMs = 60_000, refetchInterval = 30_000 } = options

  const query = useQuery({
    queryKey: monitorKeys.snapshot(),
    queryFn: () => monitorAPI.getSnapshot(),
    staleTime: 30_000,
    refetchInterval,
    retry: 2,
    select: data => data,
  })

  const isStale = isSnapshotStale(query.data ?? null, staleThresholdMs)
  const healthState = deriveSystemHealthState(query.data ?? null, isStale)

  return {
    snapshot: query.data ?? null,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error ? formatAPIError(query.error) : null,
    isStale,
    healthState,
    refetch: query.refetch,
  }
}

// Alerts query
interface UseMonitorAlertsResult {
  alerts: Alert[]
  totalCount: number
  isLoading: boolean
  isError: boolean
  error: string | null
  refetch: () => void
}

export function useMonitorAlerts(): UseMonitorAlertsResult {
  const query = useQuery({
    queryKey: monitorKeys.alerts(),
    queryFn: () => monitorAPI.getAlerts(),
    staleTime: 30_000,
    retry: 2,
  })

  return {
    alerts: query.data?.alerts ?? [],
    totalCount: query.data?.total_count ?? 0,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error ? formatAPIError(query.error) : null,
    refetch: query.refetch,
  }
}

// Clear single alert mutation
interface UseClearAlertResult {
  clearAlert: (ruleName: string, reason?: string) => Promise<boolean>
  isPending: boolean
  error: string | null
}

export function useClearAlert(): UseClearAlertResult {
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: ({ ruleName, reason }: { ruleName: string; reason?: string }) =>
      monitorAPI.clearAlert(ruleName, reason),
    onSuccess: () => {
      // Invalidate both alerts and snapshot queries
      queryClient.invalidateQueries({ queryKey: monitorKeys.alerts() })
      queryClient.invalidateQueries({ queryKey: monitorKeys.snapshot() })
    },
  })

  return {
    clearAlert: async (ruleName: string, reason?: string): Promise<boolean> => {
      try {
        const result = await mutation.mutateAsync({ ruleName, reason })
        return result.ok
      } catch {
        return false
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}

// Clear all alerts mutation
interface UseClearAllAlertsResult {
  clearAllAlerts: (reason?: string) => Promise<boolean>
  isPending: boolean
  error: string | null
}

export function useClearAllAlerts(): UseClearAllAlertsResult {
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (reason?: string) => monitorAPI.clearAllAlerts(reason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: monitorKeys.alerts() })
      queryClient.invalidateQueries({ queryKey: monitorKeys.snapshot() })
    },
  })

  return {
    clearAllAlerts: async (reason?: string): Promise<boolean> => {
      try {
        const result = await mutation.mutateAsync(reason)
        return result.ok
      } catch {
        return false
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}
