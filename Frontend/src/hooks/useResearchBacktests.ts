import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { researchAPI } from '@/api'
import { formatAPIError } from '@/api/client'
import { isActiveResearchRun } from '@/utils/researchBacktest'
import type {
  ResearchBacktestCreateRequest,
  ResearchBacktestRun,
  ResearchDatasetQuery,
} from '@/types'

/** 活动运行（QUEUED / RUNNING）轮询间隔。 */
export const RESEARCH_RUN_POLL_INTERVAL_MS = 2_000

export const researchBacktestKeys = {
  all: ['research-backtests'] as const,
  datasets: (query: ResearchDatasetQuery) =>
    [...researchBacktestKeys.all, 'datasets', query] as const,
  strategies: () => [...researchBacktestKeys.all, 'strategies'] as const,
  runs: (limit: number) => [...researchBacktestKeys.all, 'runs', limit] as const,
  run: (runId: string) => [...researchBacktestKeys.all, 'run', runId] as const,
}

interface UseResearchDatasetsOptions {
  query?: ResearchDatasetQuery
  enabled?: boolean
}

/**
 * Research 数据集目录。
 *
 * 资格判定完全来自后端 `official_eligible`；查询失败时由调用方阻断提交。
 */
export function useResearchDatasets(options: UseResearchDatasetsOptions = {}) {
  const query = options.query ?? {}
  return useQuery({
    queryKey: researchBacktestKeys.datasets(query),
    queryFn: () => researchAPI.listResearchDatasets(query),
    enabled: options.enabled ?? true,
    staleTime: 15_000,
    retry: 1,
  })
}

/** 后端已安装、允许提交回测的策略列表。 */
export function useResearchStrategies(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: researchBacktestKeys.strategies(),
    queryFn: () => researchAPI.listResearchStrategies(),
    enabled: options?.enabled ?? true,
    staleTime: 60_000,
    retry: 1,
  })
}

interface UseResearchBacktestRunsOptions {
  limit?: number
  enabled?: boolean
}

/**
 * 运行列表。
 *
 * 只要存在 QUEUED / RUNNING 运行就每 2 秒轮询；全部进入终态后停止。
 */
export function useResearchBacktestRuns(options: UseResearchBacktestRunsOptions = {}) {
  const limit = options.limit ?? 100
  return useQuery({
    queryKey: researchBacktestKeys.runs(limit),
    queryFn: () => researchAPI.listResearchBacktests({ limit }),
    enabled: options.enabled ?? true,
    staleTime: RESEARCH_RUN_POLL_INTERVAL_MS,
    retry: 1,
    refetchInterval: current => {
      const runs = current.state.data ?? []
      return runs.some(isActiveResearchRun) ? RESEARCH_RUN_POLL_INTERVAL_MS : false
    },
  })
}

/**
 * 单个运行详情。
 *
 * 按 run_id 查询，刷新页面后可恢复正在运行或已完成的运行；
 * 终态（SUCCEEDED / FAILED / CANCELLED）停止轮询。
 */
export function useResearchBacktestRun(runId: string | null | undefined) {
  return useQuery({
    queryKey: researchBacktestKeys.run(runId ?? ''),
    queryFn: () => researchAPI.getResearchBacktest(runId as string),
    enabled: !!runId,
    staleTime: RESEARCH_RUN_POLL_INTERVAL_MS,
    retry: 1,
    refetchInterval: current => {
      const run = current.state.data
      if (!run) return false
      return isActiveResearchRun(run) ? RESEARCH_RUN_POLL_INTERVAL_MS : false
    },
  })
}

function applyRunToCache(
  queryClient: ReturnType<typeof useQueryClient>,
  run: ResearchBacktestRun,
) {
  queryClient.setQueryData(researchBacktestKeys.run(run.run_id), run)
  void queryClient.invalidateQueries({ queryKey: [...researchBacktestKeys.all, 'runs'] })
}

interface UseSubmitResearchBacktestResult {
  submit: (request: ResearchBacktestCreateRequest) => Promise<ResearchBacktestRun | null>
  isPending: boolean
  error: string | null
}

export function useSubmitResearchBacktest(): UseSubmitResearchBacktestResult {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (request: ResearchBacktestCreateRequest) =>
      researchAPI.createResearchBacktest(request),
    onSuccess: run => applyRunToCache(queryClient, run),
  })

  return {
    submit: async (
      request: ResearchBacktestCreateRequest,
    ): Promise<ResearchBacktestRun | null> => {
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

interface UseRetryResearchBacktestResult {
  retry: (runId: string) => Promise<ResearchBacktestRun | null>
  isPending: boolean
  error: string | null
}

/** 显式重试 FAILED 运行；后端对非 FAILED 返回 409。 */
export function useRetryResearchBacktest(): UseRetryResearchBacktestResult {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (runId: string) => researchAPI.retryResearchBacktest(runId),
    onSuccess: run => applyRunToCache(queryClient, run),
  })

  return {
    retry: async (runId: string): Promise<ResearchBacktestRun | null> => {
      try {
        return await mutation.mutateAsync(runId)
      } catch {
        return null
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}

export type { ResearchBacktestRun }
