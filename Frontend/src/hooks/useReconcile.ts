import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { reconcileAPI } from '@/api'
import { formatAPIError } from '@/api/client'

// Query key factory
export const reconcileKeys = {
  all: ['reconcile'] as const,
  report: () => [...reconcileKeys.all, 'report'] as const,
  driftEvents: () => [...reconcileKeys.all, 'driftEvents'] as const,
}

// Fetch reconciliation report
export function useReconcileReport() {
  return useQuery({
    queryKey: reconcileKeys.report(),
    queryFn: () => reconcileAPI.getReport(),
    staleTime: 15_000,
    refetchInterval: 30_000,
    retry: 2,
    throwOnError: false,
  })
}

// Fetch drift events
export function useDriftEvents() {
  return useQuery({
    queryKey: reconcileKeys.driftEvents(),
    queryFn: () => reconcileAPI.getDriftEvents(),
    staleTime: 30_000,
    retry: 2,
  })
}

// Trigger reconciliation mutation
interface UseTriggerReconciliationResult {
  trigger: () => Promise<boolean>
  isPending: boolean
  error: string | null
}

export function useTriggerReconciliation(): UseTriggerReconciliationResult {
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: () => reconcileAPI.triggerReconciliation(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: reconcileKeys.report() })
    },
  })

  return {
    trigger: async () => {
      try {
        const result = await mutation.mutateAsync()
        return result.ok
      } catch {
        return false
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}