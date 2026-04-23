// Target path: Frontend/src/hooks/useStrategies.ts

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { strategiesAPI } from '@/api'
import type { LoadStrategyPayload, StrategyParams } from '@/types'
import { formatAPIError } from '@/api/client'

export const strategyKeys = {
  all: ['strategies'] as const,
  registry: () => [...strategyKeys.all, 'registry'] as const,
  loaded: () => [...strategyKeys.all, 'loaded'] as const,
  status: (deploymentId: string) => [...strategyKeys.all, 'status', deploymentId] as const,
  params: (strategyId: string) => [...strategyKeys.all, 'params', strategyId] as const,
  events: (deploymentId: string) => [...strategyKeys.all, 'events', deploymentId] as const,
  signals: (deploymentId: string) => [...strategyKeys.all, 'signals', deploymentId] as const,
  errors: (deploymentId: string) => [...strategyKeys.all, 'errors', deploymentId] as const,
  tradingPairs: () => [...strategyKeys.all, 'trading-pairs'] as const,
  safetyGate: () => [...strategyKeys.all, 'safety-gate'] as const,
}

function invalidateStrategyLists(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({
    queryKey: strategyKeys.loaded(),
    exact: true,
    refetchType: 'active',
  })
  queryClient.invalidateQueries({
    queryKey: strategyKeys.registry(),
    exact: true,
    refetchType: 'active',
  })
}

export function useStrategyRegistry() {
  return useQuery({
    queryKey: strategyKeys.registry(),
    queryFn: () => strategiesAPI.getRegistry(),
    staleTime: 30_000,
    retry: 2,
    throwOnError: false,
  })
}

export function useLoadedStrategies() {
  return useQuery({
    queryKey: strategyKeys.loaded(),
    queryFn: () => strategiesAPI.getLoaded(),
    staleTime: 5_000,
    refetchInterval: 5_000,
    retry: 2,
    throwOnError: false,
  })
}

export function useStrategyStatus(deploymentId: string) {
  return useQuery({
    queryKey: strategyKeys.status(deploymentId),
    queryFn: () => strategiesAPI.getStatus(deploymentId),
    staleTime: 5_000,
    retry: 2,
    enabled: !!deploymentId,
    throwOnError: false,
  })
}

export function useStrategyParams(strategyId: string) {
  return useQuery({
    queryKey: strategyKeys.params(strategyId),
    queryFn: () => strategiesAPI.getParams(strategyId),
    staleTime: 60_000,
    retry: 2,
    enabled: !!strategyId,
  })
}

interface UseDeploymentMutationResult<TInput = void> {
  mutateAsync: (input: TInput) => Promise<boolean>
  isPending: boolean
  error: string | null
}

export function useLoadStrategy(): UseDeploymentMutationResult<{
  strategyId: string
  payload: LoadStrategyPayload
}> {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: ({ strategyId, payload }: { strategyId: string; payload: LoadStrategyPayload }) =>
      strategiesAPI.loadStrategy(strategyId, payload),
    onSuccess: () => {
      invalidateStrategyLists(queryClient)
    },
  })

  return {
    mutateAsync: async (input) => {
      try {
        await mutation.mutateAsync(input)
        return true
      } catch {
        return false
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}

function useRuntimeMutation(
  mutationFn: () => Promise<unknown>,
): UseDeploymentMutationResult<void> {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn,
    onSuccess: () => {
      invalidateStrategyLists(queryClient)
    },
  })

  return {
    mutateAsync: async () => {
      try {
        await mutation.mutateAsync()
        return true
      } catch {
        return false
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}

export function useUnloadStrategy(deploymentId: string) {
  return useRuntimeMutation(() => strategiesAPI.unloadStrategy(deploymentId))
}

export function useStartStrategy(deploymentId: string) {
  return useRuntimeMutation(() => strategiesAPI.startStrategy(deploymentId))
}

export function useStopStrategy(deploymentId: string) {
  return useRuntimeMutation(() => strategiesAPI.stopStrategy(deploymentId))
}

export function usePauseStrategy(deploymentId: string) {
  return useRuntimeMutation(() => strategiesAPI.pauseStrategy(deploymentId))
}

export function useResumeStrategy(deploymentId: string) {
  return useRuntimeMutation(() => strategiesAPI.resumeStrategy(deploymentId))
}

// Optional clearer aliases for future callers
export const useStartDeployment = useStartStrategy
export const useStopDeployment = useStopStrategy
export const usePauseDeployment = usePauseStrategy
export const useResumeDeployment = useResumeStrategy
export const useUnloadDeployment = useUnloadStrategy

export function useUpdateStrategyParams(strategyId: string) {
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (params: StrategyParams) => strategiesAPI.updateParams(strategyId, params),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: strategyKeys.params(strategyId),
        exact: true,
        refetchType: 'active',
      })
    },
  })

  return {
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

export function useStrategyEvents(deploymentId: string, eventType?: string) {
  return useQuery({
    queryKey: [...strategyKeys.events(deploymentId), { eventType }],
    queryFn: () => strategiesAPI.getStrategyEvents(deploymentId, eventType),
    staleTime: 5_000,
    refetchInterval: 5_000,
    retry: 2,
    enabled: !!deploymentId,
    throwOnError: false,
  })
}

export function useStrategySignals(deploymentId: string) {
  return useQuery({
    queryKey: strategyKeys.signals(deploymentId),
    queryFn: () => strategiesAPI.getStrategySignals(deploymentId),
    staleTime: 5_000,
    refetchInterval: 5_000,
    retry: 2,
    enabled: !!deploymentId,
    throwOnError: false,
  })
}

export function useStrategyErrors(deploymentId: string) {
  return useQuery({
    queryKey: strategyKeys.errors(deploymentId),
    queryFn: () => strategiesAPI.getStrategyErrors(deploymentId),
    staleTime: 10_000,
    retry: 2,
    enabled: !!deploymentId,
    throwOnError: false,
  })
}

export function useTradingPairs(statusFilter = 'TRADING', quoteAsset = 'USDT') {
  return useQuery({
    queryKey: strategyKeys.tradingPairs(),
    queryFn: () => strategiesAPI.getTradingPairs(statusFilter, quoteAsset),
    staleTime: 60_000,
    retry: 2,
    throwOnError: false,
  })
}

export function useSafetyGate() {
  const queryClient = useQueryClient()

  const query = useQuery({
    queryKey: strategyKeys.safetyGate(),
    queryFn: () => strategiesAPI.getSafetyGateStatus(),
    staleTime: 10_000,
    refetchInterval: 10_000,
    retry: 2,
    throwOnError: false,
  })

  const mutation = useMutation({
    mutationFn: ({ enabled, confirmed }: { enabled: boolean; confirmed?: boolean }) =>
      strategiesAPI.setSafetyGateEnabled(enabled, confirmed ?? false),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: strategyKeys.safetyGate(),
        exact: true,
        refetchType: 'active',
      })
      queryClient.invalidateQueries({
        queryKey: ['monitor', 'snapshot'],
        exact: true,
        refetchType: 'active',
      })
    },
  })

  const enable = async (confirmed = false) => mutation.mutateAsync({ enabled: true, confirmed })
  const disable = async () => mutation.mutateAsync({ enabled: false })

  return {
    status: query.data ?? null,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error ? formatAPIError(query.error) : null,
    isPending: mutation.isPending,
    enable,
    disable,
  }
}