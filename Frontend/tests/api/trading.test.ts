import { describe, expect, it } from 'vitest'
import { TradingAPI } from '@/api/trading'

class TestTradingAPI extends TradingAPI {
  calls: Array<{ method: 'GET' | 'POST'; url: string; data?: unknown; config?: unknown }> = []

  protected override async get<T>(url: string, config?: unknown): Promise<T> {
    this.calls.push({ method: 'GET', url, config })
    return {
      command_id: 'cancel-1',
      cl_ord_id: 'qts-1',
      kind: 'CANCEL',
      status: 'CONFIRMED',
      order_status: 'OPEN',
      error: '',
      actor: 'owner',
    } as T
  }

  protected override async post<T>(url: string, data?: unknown): Promise<T> {
    this.calls.push({ method: 'POST', url, data })
    return {
      command_id: 'cancel-1',
      cl_ord_id: 'qts-1',
      kind: 'CANCEL',
      status: 'CONFIRMED',
      order_status: 'OPEN',
      error: '',
    } as T
  }
}

describe('TradingAPI', () => {
  it('submits cancel as a command receipt instead of claiming cancellation', async () => {
    const api = new TestTradingAPI()
    const receipt = await api.cancelOrder('qts-1', {
      command_id: 'cancel-1',
      account_id: 'acct',
    })

    expect(api.calls).toEqual([
      {
        method: 'POST',
        url: '/v1/trading/orders/qts-1/cancel',
        data: { command_id: 'cancel-1', account_id: 'acct' },
      },
    ])
    expect(receipt.status).toBe('CONFIRMED')
    expect(receipt.order_status).toBe('OPEN')
  })

  it('queries the persistent command state', async () => {
    const api = new TestTradingAPI()
    const command = await api.getCommand('cancel-1', 'acct')

    expect(api.calls).toEqual([
      {
        method: 'GET',
        url: '/v1/commands/cancel-1',
        config: { params: { account_id: 'acct' } },
      },
    ])
    expect(command.actor).toBe('owner')
  })

  it('lists orders and risk views through the unified trading endpoints', async () => {
    const api = new TestTradingAPI()
    await api.listOrders('acct')
    await api.getRisk('acct')
    await api.getMetrics('acct')

    expect(api.calls.map(call => call.url)).toEqual([
      '/v1/trading/orders',
      '/v1/trading/accounts/acct/risk',
      '/v1/trading/metrics',
    ])
  })
})
