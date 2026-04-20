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

// Fetch drift events from /v1/events?stream_key=order_drifts
export function useDriftEvents() {
  return useQuery({
    queryKey: reconcileKeys.driftEvents(),
    queryFn: async () => {
      const events = await reconcileAPI.getDriftEvents()
      // Transform EventEnvelope[] to Drift[] by extracting payload
      return events.map(event => ({
        cl_ord_id: event.payload.cl_ord_id,
        drift_type: event.payload.drift_type,
        local_status: event.payload.local_status,
        exchange_status: event.payload.exchange_status,
        detected_at: event.payload.detected_at,
        symbol: event.payload.symbol,
        quantity: event.payload.quantity,
        filled_quantity: event.payload.filled_quantity,
        exchange_filled_quantity: event.payload.exchange_filled_quantity,
        grace_period_remaining_sec: null,
        ownership: null,
      }))
    },
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