import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { replayAPI } from '@/api'
import type { ReplayListParams, ReplayRequest } from '@/types'
import { formatAPIError } from '@/api/client'

export const replayKeys = {
  all: ['replay'] as const,
  list: (params?: ReplayListParams) => [...replayKeys.all, 'list', params ?? {}] as const,
  detail: (jobId: string) => [...replayKeys.all, 'detail', jobId] as const,
}

export function useReplayJobs(params?: ReplayListParams) {
  return useQuery({
    queryKey: replayKeys.list(params),
    queryFn: () => replayAPI.listJobs(params),
    staleTime: 5_000,
    refetchInterval: 10_000,
    retry: 2,
  })
}

export function useReplayJob(jobId: string) {
  return useQuery({
    queryKey: replayKeys.detail(jobId),
    queryFn: () => replayAPI.getJob(jobId),
    staleTime: 1_000,
    refetchInterval: 2_000,
    retry: 2,
    enabled: !!jobId,
  })
}

interface UseTriggerReplayResult {
  trigger: (request: ReplayRequest) => Promise<string | null>
  isPending: boolean
  error: string | null
}

export function useTriggerReplay(): UseTriggerReplayResult {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (request: ReplayRequest) => replayAPI.trigger(request),
    onSuccess: (job) => {
      queryClient.invalidateQueries({ queryKey: replayKeys.all })
      queryClient.setQueryData(replayKeys.detail(job.job_id), job)
    },
  })

  return {
    trigger: async (request: ReplayRequest): Promise<string | null> => {
      try {
        const job = await mutation.mutateAsync(request)
        return job.job_id
      } catch {
        return null
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}

