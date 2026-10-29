import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { researchAPI } from '@/api'
import type { HistoryDataset, HistoryJob, HistoryJobCreateRequest } from '@/types'
import { formatAPIError } from '@/api/client'

export const historyKeys = {
  all: ['history'] as const,
  jobs: () => [...historyKeys.all, 'jobs'] as const,
  datasets: () => [...historyKeys.all, 'datasets'] as const,
  dataset: (datasetId: string) => [...historyKeys.all, 'dataset', datasetId] as const,
}

const ACTIVE_STATUSES = new Set(['QUEUED', 'RUNNING'])

export function isActiveJob(job: HistoryJob): boolean {
  return ACTIVE_STATUSES.has(job.status)
}

/**
 * 历史导入任务列表。
 *
 * 有活动任务（QUEUED / RUNNING）时每 2 秒轮询，全部进入终态后停止轮询。
 */
export function useHistoryJobs(options?: { enabled?: boolean }) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: historyKeys.jobs(),
    queryFn: () => researchAPI.listHistoryJobs(),
    enabled: options?.enabled ?? true,
    retry: 1,
    refetchInterval: current => {
      const jobs = current.state.data ?? []
      return jobs.some(isActiveJob) ? 2000 : false
    },
  })

  // 有任务成功时刷新数据集缓存
  const seenSucceeded = useRef(false)
  useEffect(() => {
    const jobs = query.data ?? []
    const anySucceeded = jobs.some(job => job.status === 'SUCCEEDED')
    if (anySucceeded && !seenSucceeded.current) {
      seenSucceeded.current = true
      void queryClient.invalidateQueries({ queryKey: historyKeys.datasets() })
    } else if (!anySucceeded) {
      seenSucceeded.current = false
    }
  }, [query.data, queryClient])

  return query
}

/** 已发布 / 可用于回测的历史数据集列表。 */
export function useHistoryDatasets(options?: { onlyEligible?: boolean; enabled?: boolean }) {
  return useQuery({
    queryKey: [...historyKeys.datasets(), options?.onlyEligible ?? false],
    queryFn: () =>
      researchAPI.listHistoryDatasets(
        options?.onlyEligible ? { only_eligible: true } : undefined,
      ),
    enabled: options?.enabled ?? true,
    staleTime: 10_000,
    retry: 1,
  })
}

/** 单个数据集详情（用于跳转后重新查询，避免信任 URL 参数）。 */
export function useHistoryDataset(datasetId: string | null | undefined) {
  return useQuery({
    queryKey: historyKeys.dataset(datasetId ?? ''),
    queryFn: () => researchAPI.getHistoryDataset(datasetId as string),
    enabled: !!datasetId,
    staleTime: 10_000,
    retry: 1,
  })
}

interface UseCreateHistoryJobResult {
  create: (request: HistoryJobCreateRequest) => Promise<HistoryJob | null>
  isPending: boolean
  error: string | null
}

export function useCreateHistoryJob(): UseCreateHistoryJobResult {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (request: HistoryJobCreateRequest) => researchAPI.createHistoryJob(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: historyKeys.jobs() })
    },
  })

  return {
    create: async (request: HistoryJobCreateRequest): Promise<HistoryJob | null> => {
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

interface UseRetryHistoryJobResult {
  retry: (jobId: string) => Promise<HistoryJob | null>
  isPending: boolean
  error: string | null
}

export function useRetryHistoryJob(): UseRetryHistoryJobResult {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (jobId: string) => researchAPI.retryHistoryJob(jobId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: historyKeys.jobs() })
    },
  })

  return {
    retry: async (jobId: string): Promise<HistoryJob | null> => {
      try {
        return await mutation.mutateAsync(jobId)
      } catch {
        return null
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}

export type { HistoryDataset, HistoryJob }
