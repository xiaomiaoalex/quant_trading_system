import { useMemo, useState } from 'react'
import { PageHeader } from '@/components/layout'
import { ConfirmDialog, EmptyState, ErrorState, LoadingState } from '@/components/ui'
import { formatAPIError } from '@/api/client'
import {
  useCancelOrder,
  useCommandStatus,
  useOrders,
  useTradingRisk,
} from '@/hooks/useTrading'
import { describeCommand, isActiveOrder } from '@/utils/tradingStatus'

const inputClass =
  'rounded border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200'

function newCommandId(prefix: string): string {
  const suffix =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${prefix}-${suffix}`
}

export function Orders() {
  const [accountId, setAccountId] = useState('binance_spot')
  const [pending, setPending] = useState<{ clOrdId: string; commandId: string } | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)

  const orders = useOrders(accountId)
  const risk = useTradingRisk(accountId)
  const cancel = useCancelOrder(accountId)
  const command = useCommandStatus(accountId, pending?.commandId ?? null)

  const pendingOrder = useMemo(
    () => orders.data?.find(order => order.cl_ord_id === pending?.clOrdId) ?? null,
    [orders.data, pending?.clOrdId],
  )
  const display = describeCommand(command.data, pendingOrder)

  if (orders.isLoading) {
    return (
      <div className="p-6">
        <LoadingState message="Loading orders..." />
      </div>
    )
  }
  if (orders.isError) {
    return (
      <div className="p-6">
        <ErrorState
          title="Failed to load orders"
          message={formatAPIError(orders.error)}
          onRetry={() => orders.refetch()}
        />
      </div>
    )
  }

  const reasonList = risk.data?.reasons ?? []

  return (
    <div className="min-h-screen bg-gray-900">
      <PageHeader title="Orders">
        <input
          className={inputClass}
          value={accountId}
          onChange={event => setAccountId(event.target.value)}
          aria-label="Account ID"
        />
        <button
          onClick={() => orders.refetch()}
          className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700"
        >
          Refresh
        </button>
      </PageHeader>

      <div className="space-y-4 p-6">
        {risk.data && risk.data.status !== 'READY' && (
          <div className="rounded-lg border border-orange-900/50 bg-orange-950/20 p-4">
            <p className="text-sm font-medium text-orange-300">
              New risk is blocked: {risk.data.status}
            </p>
            <p className="mt-1 text-xs text-orange-200/80">
              {reasonList.length ? reasonList.join(', ') : 'Reconciliation is not ready.'}
            </p>
          </div>
        )}

        {pending && (
          <div
            className="rounded-lg border border-gray-700 bg-gray-800 p-4"
            role="status"
            data-tone={display.tone}
          >
            <p className="text-sm font-medium text-gray-100">{display.label}</p>
            <p className="mt-1 text-xs text-gray-400">{display.detail}</p>
            <p className="mt-2 font-mono text-xs text-gray-500">
              command {pending.commandId} · order {pending.clOrdId}
            </p>
            {command.data && (
              <p className="mt-1 text-xs text-gray-400">
                command status: {command.data.status}
                {pendingOrder ? ` · order status: ${pendingOrder.status}` : ''}
              </p>
            )}
            {command.isError && (
              <p className="mt-1 text-xs text-orange-300">
                Unable to read command state; refresh before retrying.
              </p>
            )}
          </div>
        )}

        {cancel.isError && (
          <div className="rounded-lg border border-red-900/50 bg-red-950/20 p-4 text-sm text-red-300">
            {formatAPIError(cancel.error)}
          </div>
        )}

        {!orders.data?.length ? (
          <EmptyState
            title="No orders"
            message="No orders are recorded for this account."
          />
        ) : (
          <div className="overflow-hidden rounded-lg border border-gray-700">
            <table className="min-w-full divide-y divide-gray-800 text-sm">
              <thead className="bg-gray-800/60 text-xs uppercase text-gray-400">
                <tr>
                  <th className="px-4 py-3 text-left">Order</th>
                  <th className="px-4 py-3 text-left">Symbol</th>
                  <th className="px-4 py-3 text-left">Side</th>
                  <th className="px-4 py-3 text-right">Qty</th>
                  <th className="px-4 py-3 text-right">Booked</th>
                  <th className="px-4 py-3 text-right">Remaining</th>
                  <th className="px-4 py-3 text-left">Status</th>
                  <th className="px-4 py-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800">
                {orders.data.map(order => (
                  <tr key={order.cl_ord_id} className="bg-gray-900/40">
                    <td className="px-4 py-3 font-mono text-xs text-gray-300">{order.cl_ord_id}</td>
                    <td className="px-4 py-3 text-gray-200">{order.symbol}</td>
                    <td className="px-4 py-3 text-gray-200">{order.side}</td>
                    <td className="px-4 py-3 text-right text-gray-200">{order.qty}</td>
                    <td className="px-4 py-3 text-right text-gray-200">{order.booked_qty}</td>
                    <td className="px-4 py-3 text-right text-gray-200">{order.remaining_qty}</td>
                    <td className="px-4 py-3 text-gray-200">{order.status}</td>
                    <td className="px-4 py-3 text-right">
                      {isActiveOrder(order) ? (
                        <button
                          onClick={() => setConfirming(order.cl_ord_id)}
                          disabled={cancel.isPending}
                          className="rounded border border-orange-800 px-3 py-1 text-xs text-orange-300 hover:bg-orange-900/30 disabled:opacity-50"
                        >
                          Request cancel
                        </button>
                      ) : (
                        <span className="text-xs text-gray-500">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <ConfirmDialog
        isOpen={confirming !== null}
        title="Request order cancellation?"
        message="The venue must confirm the cancellation. The order stays active until a terminal observation arrives."
        confirmLabel="Request cancel"
        isLoading={cancel.isPending}
        onCancel={() => setConfirming(null)}
        onConfirm={async () => {
          if (!confirming) return
          const commandId = newCommandId('cancel')
          setConfirming(null)
          setPending({ clOrdId: confirming, commandId })
          try {
            await cancel.mutateAsync({ clOrdId: confirming, commandId })
          } catch {
            setPending(null)
          }
        }}
      />
    </div>
  )
}
