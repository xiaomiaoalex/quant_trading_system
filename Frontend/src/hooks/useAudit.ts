import { useQuery } from '@tanstack/react-query'
import { auditAPI } from '@/api'
import type { AuditListParams } from '@/types'

export const auditKeys = {
  all: ['audit'] as const,
  list: (params?: AuditListParams) => [...auditKeys.all, 'list', params ?? {}] as const,
  detail: (entryId: string) => [...auditKeys.all, 'detail', entryId] as const,
}

export function useAuditEntries(params?: AuditListParams) {
  return useQuery({
    queryKey: auditKeys.list(params),
    queryFn: () => auditAPI.listEntries(params),
    staleTime: 15_000,
    refetchInterval: 30_000,
    retry: 2,
  })
}

export function useAuditEntry(entryId: string) {
  return useQuery({
    queryKey: auditKeys.detail(entryId),
    queryFn: () => auditAPI.getEntry(entryId),
    staleTime: 30_000,
    retry: 2,
    enabled: !!entryId,
  })
}
