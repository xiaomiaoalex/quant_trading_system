import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { chatAPI } from '@/api'
import type { ChatSession, SendMessageResponse, RegistrationResult } from '@/types'
import { formatAPIError } from '@/api/client'

export const chatKeys = {
  all: ['chat'] as const,
  sessions: () => [...chatKeys.all, 'sessions'] as const,
  session: (id: string) => [...chatKeys.all, 'session', id] as const,
  history: (id: string) => [...chatKeys.all, 'history', id] as const,
}

export function useChatSessions() {
  return useQuery({
    queryKey: chatKeys.sessions(),
    queryFn: () => chatAPI.listSessions(),
    staleTime: 30_000,
    retry: 2,
    throwOnError: false,
  })
}

export function useChatSession(sessionId: string) {
  return useQuery({
    queryKey: chatKeys.session(sessionId),
    queryFn: () => chatAPI.getSession(sessionId),
    staleTime: 30_000,
    retry: 2,
    enabled: !!sessionId,
    throwOnError: false,
  })
}

export function useChatHistory(sessionId: string) {
  return useQuery({
    queryKey: chatKeys.history(sessionId),
    queryFn: () => chatAPI.getHistory(sessionId),
    staleTime: 10_000,
    retry: 2,
    enabled: !!sessionId,
    refetchInterval: 5000,
    throwOnError: false,
  })
}

interface UseSendMessageResult {
  sendMessage: (message: string) => Promise<SendMessageResponse | null>
  isPending: boolean
  error: string | null
}

export function useSendMessage(sessionId: string): UseSendMessageResult {
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (message: string) => chatAPI.sendMessage(sessionId, message),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: chatKeys.history(sessionId) })
      queryClient.invalidateQueries({ queryKey: chatKeys.session(sessionId) })
    },
  })

  return {
    sendMessage: async (message: string): Promise<SendMessageResponse | null> => {
      try {
        return await mutation.mutateAsync(message)
      } catch {
        return null
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}

interface UseApproveStrategyResult {
  approve: () => Promise<RegistrationResult | null>
  isPending: boolean
  error: string | null
}

export function useApproveStrategy(sessionId: string): UseApproveStrategyResult {
  const mutation = useMutation({
    mutationFn: () => chatAPI.approveStrategy(sessionId),
  })

  return {
    approve: async (): Promise<RegistrationResult | null> => {
      try {
        return await mutation.mutateAsync()
      } catch {
        return null
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}

interface UseRejectStrategyResult {
  reject: (reason?: string) => Promise<boolean>
  isPending: boolean
  error: string | null
}

export function useRejectStrategy(sessionId: string): UseRejectStrategyResult {
  const mutation = useMutation({
    mutationFn: (reason?: string) => chatAPI.rejectStrategy(sessionId, reason),
  })

  return {
    reject: async (reason?: string): Promise<boolean> => {
      try {
        const result = await mutation.mutateAsync(reason)
        return result?.success ?? false
      } catch {
        return false
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}

export function useDeleteSession() {
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (sessionId: string) => chatAPI.deleteSession(sessionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: chatKeys.sessions() })
    },
  })

  return {
    deleteSession: async (sessionId: string): Promise<boolean> => {
      try {
        await mutation.mutateAsync(sessionId)
        return true
      } catch {
        return false
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}

export function useCreateSession() {
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: (riskLevel?: string) => chatAPI.createSession(riskLevel),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: chatKeys.sessions() })
    },
  })

  return {
    createSession: async (riskLevel?: string): Promise<ChatSession | null> => {
      try {
        return await mutation.mutateAsync(riskLevel)
      } catch {
        return null
      }
    },
    isPending: mutation.isPending,
    error: mutation.error ? formatAPIError(mutation.error) : null,
  }
}
