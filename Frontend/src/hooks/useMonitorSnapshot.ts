import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { monitorAPI } from '@/api'
import type { MonitorSnapshot, SystemHealthState, Alert } from '@/types'
import { deriveSystemHealthState, isSnapshotStale } from '@/types'
import { formatAPIError } from '@/api/client'
import {
  MonitorSnapshotSchema,
  MonitorAlertsResponseSchema,
} from '@/contracts/monitor'

// Query keys for monitor
export const monitorKeys = {
  all: ['monitor'] as const,
  snapshot: () => [...monitorKeys.all, 'snapshot'] as const,
  alerts: () => [...monitorKeys.all, 'alerts'] as const,
  killswitch: () => [...monitorKeys.all, 'killswitch'] as const,
  readiness: () => [...monitorKeys.all, 'readiness'] as const,
  dependencies: () => [...monitorKeys.all, 'dependencies'] as const,
}

// ─── Snapshot query with Zod validation ──────────────────────────────────────

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
  refetch: () => Promise<void>
}

export function useMonitorSnapshot(
  options: UseMonitorSnapshotOptions = {}
): UseMonitorSnapshotResult {
  const { staleThresholdMs = 10_000, refetchInterval = 5_000 } = options

  const query = useQuery({
    queryKey: monitorKeys.snapshot(),
    queryFn: async () => {
      const raw = await monitorAPI.getSnapshot()
      // Runtime contract validation — fails fast on field drift
      const parsed = MonitorSnapshotSchema.safeParse(raw)
      if (!parsed.success) {
        // eslint-disable-next-line no-console
        console.error('MonitorSnapshot validation failed:', parsed.error.flatten())
        throw new Error(
          `Monitor snapshot schema mismatch: ${parsed.error.errors.map((e) => e.message).join(', ')}`
        )
      }
      return parsed.data
    },
    staleTime: 5_000,
    refetchInterval,
    retry: 2,
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
    refetch: async () => {
      await query.refetch()
    },
  }
}

// ─── Alerts query with Zod validation ────────────────────────────────────────

interface UseMonitorAlertsResult {
  alerts: Alert[]
  totalCount: number
  isLoading: boolean
  isError: boolean
  error: string | null
  refetch: () => Promise<void>
}

export function useMonitorAlerts(): UseMonitorAlertsResult {
  const query = useQuery({
    queryKey: monitorKeys.alerts(),
    queryFn: async () => {
      const raw = await monitorAPI.getAlerts()
      const parsed = MonitorAlertsResponseSchema.safeParse(raw)
      if (!parsed.success) {
        // eslint-disable-next-line no-console
        console.error('MonitorAlertsResponse validation failed:', parsed.error.flatten())
        throw new Error(
          `Alerts response schema mismatch: ${parsed.error.errors.map((e) => e.message).join(', ')}`
        )
      }
      return parsed.data
    },
    staleTime: 30_000,
    retry: 2,
  })

  return {
    alerts: query.data?.alerts ?? [],
    totalCount: query.data?.total_count ?? 0,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error ? formatAPIError(query.error) : null,
    refetch: async () => {
      await query.refetch()
    },
  }
}

// ─── Clear single alert mutation ─────────────────────────────────────────────

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
      // Invalidate with exact match to avoid over-broad invalidation
      queryClient.invalidateQueries({
        queryKey: monitorKeys.alerts(),
        exact: true,
        refetchType: 'active',
      })
      queryClient.invalidateQueries({
        queryKey: monitorKeys.snapshot(),
        exact: true,
        refetchType: 'active',
      })
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

// ─── Clear all alerts mutation ───────────────────────────────────────────────

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
      queryClient.invalidateQueries({
        queryKey: monitorKeys.alerts(),
        exact: true,
        refetchType: 'active',
      })
      queryClient.invalidateQueries({
        queryKey: monitorKeys.snapshot(),
        exact: true,
        refetchType: 'active',
      })
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

// ─── Set killswitch mutation ─────────────────────────────────────────────────

interface UseSetKillSwitchResult {
  setKillSwitch: (level: number, reason?: string) => Promise<boolean>
  isPending: boolean
  error: string | null
}

export function useSetKillSwitch(): UseSetKillSwitchResult {
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: ({ level, reason }: { level: number; reason?: string }) =>
      monitorAPI.setKillSwitch('GLOBAL', level, reason),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: monitorKeys.snapshot(),
        exact: true,
        refetchType: 'active',
      })
    },
  })

  return {
    setKillSwitch: async (level: number, reason?: string): Promise<boolean> => {
      try {
        await mutation.mutateAsync({ level, reason })
        return true
      } catch {
        return false
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}
