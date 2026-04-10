import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { strategiesAPI } from '@/api'
import type { StrategyParams } from '@/types'
import { formatAPIError } from '@/api/client'

// Query key factory
export const strategyKeys = {
  all: ['strategies'] as const,
  registry: () => [...strategyKeys.all, 'registry'] as const,
  loaded: () => [...strategyKeys.all, 'loaded'] as const,
  status: (id: string) => [...strategyKeys.all, 'status', id] as const,
  params: (id: string) => [...strategyKeys.all, 'params', id] as const,
}

// Fetch registered strategies (metadata only, no runtime status)
export function useStrategyRegistry() {
  return useQuery({
    queryKey: strategyKeys.registry(),
    queryFn: () => strategiesAPI.getRegistry(),
    staleTime: 30_000,
    retry: 2,
  })
}

// Fetch loaded/running strategies (with runtime status)
export function useLoadedStrategies() {
  return useQuery({
    queryKey: strategyKeys.loaded(),
    queryFn: () => strategiesAPI.getLoaded(),
    staleTime: 15_000,
    retry: 2,
  })
}

// Fetch single strategy status
export function useStrategyStatus(strategyId: string) {
  return useQuery({
    queryKey: strategyKeys.status(strategyId),
    queryFn: () => strategiesAPI.getStatus(strategyId),
    staleTime: 10_000,
    retry: 2,
    enabled: !!strategyId,
  })
}

// Fetch strategy params
export function useStrategyParams(strategyId: string) {
  return useQuery({
    queryKey: strategyKeys.params(strategyId),
    queryFn: () => strategiesAPI.getParams(strategyId),
    staleTime: 60_000,
    retry: 2,
    enabled: !!strategyId,
  })
}

// Mutation hooks
interface UseStrategyMutationResult {
  mutate: () => void
  mutateAsync: () => Promise<boolean>
  isPending: boolean
  error: string | null
}

function useStrategyMutation(
  mutationFn: () => Promise<{ ok: boolean; message?: string }>
): UseStrategyMutationResult {
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: strategyKeys.loaded() })
      queryClient.invalidateQueries({ queryKey: strategyKeys.registry() })
    },
  })

  return {
    mutate: mutation.mutate,
    mutateAsync: async () => {
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

export function useLoadStrategy(strategyId: string, modulePath = 'strategies.default', version = 'v1') {
  return useStrategyMutation(
    () => strategiesAPI.loadStrategy(strategyId, modulePath, version)
  )
}

export function useUnloadStrategy(strategyId: string) {
  return useStrategyMutation(
    () => strategiesAPI.unloadStrategy(strategyId)
  )
}

export function useStartStrategy(strategyId: string) {
  return useStrategyMutation(
    () => strategiesAPI.startStrategy(strategyId)
  )
}

export function useStopStrategy(strategyId: string) {
  return useStrategyMutation(
    () => strategiesAPI.stopStrategy(strategyId)
  )
}

export function usePauseStrategy(strategyId: string) {
  return useStrategyMutation(
    () => strategiesAPI.pauseStrategy(strategyId)
  )
}

export function useResumeStrategy(strategyId: string) {
  return useStrategyMutation(
    () => strategiesAPI.resumeStrategy(strategyId)
  )
}

export function useUpdateStrategyParams(strategyId: string) {
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (params: StrategyParams) => strategiesAPI.updateParams(strategyId, params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: strategyKeys.params(strategyId) })
    },
  })

  return {
    mutate: mutation.mutate,
    mutateAsync: async (params: StrategyParams): Promise<boolean> => {
      try {
        const result = await mutation.mutateAsync(params)
        return result.success
      } catch {
        return false
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}