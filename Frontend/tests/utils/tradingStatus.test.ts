import { describe, expect, it } from 'vitest'
import { describeCommand, isActiveOrder } from '@/utils/tradingStatus'
import type { CommandRecord, OrderSnapshot } from '@/types'

function order(overrides: Partial<OrderSnapshot> = {}): OrderSnapshot {
  return {
    cl_ord_id: 'qts-1',
    status: 'OPEN',
    broker_order_id: 'b-1',
    symbol: 'BTCUSDT',
    base_asset: 'BTC',
    quote_asset: 'USDT',
    side: 'BUY',
    qty: '1',
    price: '100',
    order_type: 'LIMIT',
    booked_qty: '0',
    observed_qty: '0',
    remaining_qty: '1',
    deployment_id: 'dep-1',
    ...overrides,
  }
}

function command(overrides: Partial<CommandRecord> = {}): CommandRecord {
  return {
    command_id: 'cancel-1',
    cl_ord_id: 'qts-1',
    kind: 'CANCEL',
    status: 'CONFIRMED',
    order_status: 'OPEN',
    error: '',
    actor: 'owner',
    ...overrides,
  }
}

describe('describeCommand', () => {
  it('does not report a confirmed cancel request as cancelled while the order is active', () => {
    const display = describeCommand(command(), order())
    expect(display.pending).toBe(true)
    expect(display.label).toBe('Cancel requested')
    expect(display.detail).toContain('waiting')
  })

  it('reports cancelled only after the order reaches the terminal state', () => {
    const display = describeCommand(command(), order({ status: 'CANCELLED' }))
    expect(display.pending).toBe(false)
    expect(display.label).toBe('Cancelled')
    expect(display.tone).toBe('success')
  })

  it('surfaces unknown results as blocking, not success', () => {
    const display = describeCommand(command({ status: 'UNKNOWN' }), order())
    expect(display.pending).toBe(true)
    expect(display.tone).toBe('warning')
    expect(display.detail).toContain('will not be resent')
  })

  it('reports a rejected command as failed', () => {
    const display = describeCommand(command({ status: 'FAILED', error: 'ORDER_NOT_FOUND' }), order())
    expect(display.pending).toBe(false)
    expect(display.tone).toBe('danger')
    expect(display.detail).toBe('ORDER_NOT_FOUND')
  })

  it('treats acceptance of a place command as not a fill', () => {
    const display = describeCommand(
      command({ kind: 'PLACE', status: 'CONFIRMED' }),
      order({ status: 'PARTIALLY_FILLED' }),
    )
    expect(display.label).toBe('Order partially filled')
    expect(display.detail).toContain('not a fill')
  })
})

describe('isActiveOrder', () => {
  it('keeps partially filled and open orders actionable', () => {
    expect(isActiveOrder(order())).toBe(true)
    expect(isActiveOrder(order({ status: 'PARTIALLY_FILLED' }))).toBe(true)
    expect(isActiveOrder(order({ status: 'FILLED' }))).toBe(false)
    expect(isActiveOrder(order({ status: 'CANCELLED' }))).toBe(false)
  })
})
