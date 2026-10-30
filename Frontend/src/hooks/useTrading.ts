import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { tradingAPI } from '@/api'
import type { CommandReceipt } from '@/types'

export const tradingKeys = {
  orders: (accountId: string) => ['trading', 'orders', accountId] as const,
  command: (accountId: string, commandId: string) =>
    ['trading', 'command', accountId, commandId] as const,
  risk: (accountId: string) => ['trading', 'risk', accountId] as const,
  metrics: (accountId: string) => ['trading', 'metrics', accountId] as const,
}

export function useOrders(accountId: string) {
  return useQuery({
    queryKey: tradingKeys.orders(accountId),
    queryFn: () => tradingAPI.listOrders(accountId),
    enabled: Boolean(accountId),
    refetchInterval: 5000,
  })
}

export function useTradingRisk(accountId: string) {
  return useQuery({
    queryKey: tradingKeys.risk(accountId),
    queryFn: () => tradingAPI.getRisk(accountId),
    enabled: Boolean(accountId),
    refetchInterval: 5000,
  })
}

export function useCommandStatus(accountId: string, commandId: string | null) {
  return useQuery({
    queryKey: tradingKeys.command(accountId, commandId ?? ''),
    queryFn: () => tradingAPI.getCommand(commandId as string, accountId),
    enabled: Boolean(accountId && commandId),
    refetchInterval: query => {
      const status = query.state.data?.status
      return status === 'CONFIRMED' || status === 'FAILED' ? false : 2000
    },
  })
}

export function useCancelOrder(accountId: string) {
  const queryClient = useQueryClient()
  return useMutation<CommandReceipt, unknown, { clOrdId: string; commandId: string }>({
    mutationFn: ({ clOrdId, commandId }) =>
      tradingAPI.cancelOrder(clOrdId, { command_id: commandId, account_id: accountId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: tradingKeys.orders(accountId) })
    },
  })
}
