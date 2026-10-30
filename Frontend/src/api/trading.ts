import { APIClient } from './client'
import type {
  AccountRiskView,
  CommandReceipt,
  CommandRecord,
  OrderSnapshot,
  PlaceOrderRequest,
  TradingMetrics,
} from '@/types'

export interface CancelOrderRequest {
  command_id: string
  account_id: string
}

export class TradingAPI extends APIClient {
  async placeOrder(request: PlaceOrderRequest): Promise<CommandReceipt> {
    return this.post<CommandReceipt>('/v1/trading/orders', request)
  }

  async cancelOrder(clOrdId: string, request: CancelOrderRequest): Promise<CommandReceipt> {
    return this.post<CommandReceipt>(
      `/v1/trading/orders/${encodeURIComponent(clOrdId)}/cancel`,
      request,
    )
  }

  async getCommand(commandId: string, accountId: string): Promise<CommandRecord> {
    return this.get<CommandRecord>(`/v1/commands/${encodeURIComponent(commandId)}`, {
      params: { account_id: accountId },
    })
  }

  async listOrders(accountId: string): Promise<OrderSnapshot[]> {
    return this.get<OrderSnapshot[]>('/v1/trading/orders', {
      params: { account_id: accountId },
    })
  }

  async getOrder(clOrdId: string, accountId: string): Promise<OrderSnapshot> {
    return this.get<OrderSnapshot>(`/v1/trading/orders/${encodeURIComponent(clOrdId)}`, {
      params: { account_id: accountId },
    })
  }

  async getRisk(accountId: string): Promise<AccountRiskView> {
    return this.get<AccountRiskView>(
      `/v1/trading/accounts/${encodeURIComponent(accountId)}/risk`,
    )
  }

  async getMetrics(accountId: string): Promise<TradingMetrics> {
    return this.get<TradingMetrics>('/v1/trading/metrics', {
      params: { account_id: accountId },
    })
  }
}

export const tradingAPI = new TradingAPI()
