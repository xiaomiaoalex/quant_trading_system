import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { backtestsAPI } from '@/api'
import type { BacktestRequest, BacktestRun } from '@/types'
import { formatAPIError } from '@/api/client'

// Query key factory
export const backtestKeys = {
  all: ['backtests'] as const,
  list: (filters?: { status?: string; strategy_id?: string }) =>
    [...backtestKeys.all, 'list', filters ?? {}] as const,
  detail: (runId: string) => [...backtestKeys.all, 'detail', runId] as const,
  report: (runId: string) => [...backtestKeys.all, 'report', runId] as const,
}

// List backtests with optional filters
export function useBacktestList(filters?: { status?: string; strategy_id?: string }) {
  return useQuery({
    queryKey: backtestKeys.list(filters),
    queryFn: () => backtestsAPI.listBacktests(filters),
    staleTime: 10_000,
    refetchInterval: 30_000,
    retry: 2,
  })
}

// Get single backtest detail
export function useBacktestDetail(runId: string) {
  return useQuery({
    queryKey: backtestKeys.detail(runId),
    queryFn: () => backtestsAPI.getBacktest(runId),
    staleTime: 5_000,
    refetchInterval: 10_000,
    retry: 2,
    enabled: !!runId,
  })
}

// Get backtest report
export function useBacktestReport(runId: string) {
  return useQuery({
    queryKey: backtestKeys.report(runId),
    queryFn: () => backtestsAPI.getBacktestReport(runId),
    staleTime: 60_000,
    retry: 2,
    enabled: !!runId,
  })
}

// Create backtest mutation
interface UseCreateBacktestResult {
  create: (request: BacktestRequest) => Promise<BacktestRun | null>
  isPending: boolean
  error: string | null
}

export function useCreateBacktest(): UseCreateBacktestResult {
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (request: BacktestRequest) => backtestsAPI.createBacktest(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: backtestKeys.all })
    },
  })

  return {
    create: async (request: BacktestRequest): Promise<BacktestRun | null> => {
      try {
        return await mutation.mutateAsync(request)
      } catch {
        return null
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}