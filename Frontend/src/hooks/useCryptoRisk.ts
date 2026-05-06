import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { cryptoRiskAPI } from '@/api'
import { formatAPIError } from '@/api/client'
import {
  CryptoRiskEventEnvelopeListSchema,
  CryptoRiskProbeResponseSchema,
  CryptoRiskRuntimeStatusSchema,
} from '@/contracts'
import type {
  CryptoRiskAuditFilters,
  CryptoRiskBudgetUpdateRequest,
  CryptoRiskEventEnvelope,
  CryptoRiskProbeRequest,
  CryptoRiskProbeResponse,
  CryptoRiskRuntimeStatus,
} from '@/types'

export const cryptoRiskKeys = {
  all: ['crypto-risk'] as const,
  runtime: () => [...cryptoRiskKeys.all, 'runtime'] as const,
  events: (filters: CryptoRiskAuditFilters) => [...cryptoRiskKeys.all, 'events', filters] as const,
}

export function useCryptoRiskRuntime() {
  return useQuery<CryptoRiskRuntimeStatus>({
    queryKey: cryptoRiskKeys.runtime(),
    queryFn: async () => {
      const raw = await cryptoRiskAPI.getRuntime()
      const parsed = CryptoRiskRuntimeStatusSchema.safeParse(raw)
      if (!parsed.success) {
        throw new Error(
          `Crypto risk runtime schema mismatch: ${parsed.error.errors.map(error => error.message).join(', ')}`
        )
      }
      return parsed.data
    },
    staleTime: 5_000,
    refetchInterval: 10_000,
    retry: 2,
  })
}

export function useCryptoRiskEvents(filters: CryptoRiskAuditFilters = { limit: 50 }) {
  return useQuery<CryptoRiskEventEnvelope[]>({
    queryKey: cryptoRiskKeys.events(filters),
    queryFn: async () => {
      const raw = await cryptoRiskAPI.listEvents(filters)
      const parsed = CryptoRiskEventEnvelopeListSchema.safeParse(raw)
      if (!parsed.success) {
        throw new Error(
          `Crypto risk events schema mismatch: ${parsed.error.errors.map(error => error.message).join(', ')}`
        )
      }
      return parsed.data
    },
    staleTime: 5_000,
    refetchInterval: 15_000,
    retry: 2,
  })
}

interface UseCryptoRiskBudgetUpdateResult {
  updateBudget: (request: CryptoRiskBudgetUpdateRequest) => Promise<CryptoRiskRuntimeStatus | null>
  isPending: boolean
  error: string | null
}

export function useUpdateCryptoRiskBudget(): UseCryptoRiskBudgetUpdateResult {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: async (request: CryptoRiskBudgetUpdateRequest) => {
      const raw = await cryptoRiskAPI.updateBudget(request)
      const parsed = CryptoRiskRuntimeStatusSchema.safeParse(raw)
      if (!parsed.success) {
        throw new Error(
          `Crypto risk budget update schema mismatch: ${parsed.error.errors.map(error => error.message).join(', ')}`
        )
      }
      return parsed.data
    },
    onSuccess: status => {
      queryClient.setQueryData(cryptoRiskKeys.runtime(), status)
      queryClient.invalidateQueries({ queryKey: cryptoRiskKeys.all })
    },
  })

  return {
    updateBudget: async request => {
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

interface UseCryptoRiskProbeResult {
  runProbe: (request: CryptoRiskProbeRequest) => Promise<CryptoRiskProbeResponse | null>
  isPending: boolean
  error: string | null
}

export function useCryptoRiskProbe(): UseCryptoRiskProbeResult {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: async (request: CryptoRiskProbeRequest) => {
      const raw = await cryptoRiskAPI.runProbe(request)
      const parsed = CryptoRiskProbeResponseSchema.safeParse(raw)
      if (!parsed.success) {
        throw new Error(
          `Crypto risk probe schema mismatch: ${parsed.error.errors.map(error => error.message).join(', ')}`
        )
      }
      return parsed.data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cryptoRiskKeys.all })
    },
  })

  return {
    runProbe: async request => {
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
