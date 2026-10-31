import type { CommandReceipt, CommandRecord, OrderSnapshot } from '@/types'

export type StatusTone = 'neutral' | 'pending' | 'success' | 'danger' | 'warning'

export interface CommandDisplay {
  label: string
  detail: string
  tone: StatusTone
  pending: boolean
}

/**
 * A 202 receipt is not a terminal business result. In particular a confirmed
 * cancel command only means the venue accepted the request; the order is
 * cancelled only after a CANCELLED observation.
 */
export function describeCommand(
  command: CommandReceipt | CommandRecord | null | undefined,
  order?: OrderSnapshot | null,
): CommandDisplay {
  if (!command) {
    return { label: 'Idle', detail: '', tone: 'neutral', pending: false }
  }

  if (command.status === 'FAILED') {
    return {
      label: command.kind === 'CANCEL' ? 'Cancel request rejected' : 'Order rejected',
      detail: command.error || 'The venue rejected this command.',
      tone: 'danger',
      pending: false,
    }
  }

  if (command.status === 'UNKNOWN') {
    return {
      label: command.kind === 'CANCEL' ? 'Cancel status unknown' : 'Order status unknown',
      detail:
        'The venue did not confirm the result. The command will not be resent automatically; query the original order.',
      tone: 'warning',
      pending: true,
    }
  }

  if (command.status === 'QUEUED' || command.status === 'SENDING') {
    return {
      label: command.kind === 'CANCEL' ? 'Cancel request sending' : 'Order sending',
      detail: 'The command is persisted and in flight.',
      tone: 'pending',
      pending: true,
    }
  }

  if (command.kind === 'CANCEL') {
    if (order?.status === 'CANCELLED') {
      return {
        label: 'Cancelled',
        detail: 'The venue confirmed the cancellation.',
        tone: 'success',
        pending: false,
      }
    }
    if (order?.status === 'FILLED') {
      return {
        label: 'Cancel arrived too late',
        detail: 'The order was already filled.',
        tone: 'warning',
        pending: false,
      }
    }
    return {
      label: 'Cancel requested',
      detail: 'The venue accepted the request; waiting for the order to become terminal.',
      tone: 'pending',
      pending: true,
    }
  }

  return {
    label: order ? `Order ${order.status.toLowerCase().split('_').join(' ')}` : 'Order accepted',
    detail: 'Acceptance is not a fill. Query the order for booked quantity.',
    tone: order?.status === 'REJECTED' ? 'danger' : 'success',
    pending: false,
  }
}

export function isActiveOrder(order: OrderSnapshot): boolean {
  return ['PENDING_ACCEPTANCE', 'OPEN', 'PARTIALLY_FILLED'].includes(order.status)
}
