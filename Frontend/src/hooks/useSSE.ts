/**
 * useSSE - Server-Sent Events Hook for Real-time Updates
 * =====================================================
 *
 * Subscribes to SSE streams and triggers React Query invalidation on updates.
 *
 * Usage:
 *   const { isConnected } = useSSE(['monitor', 'strategies'], () => {
 *     queryClient.invalidateQueries({ queryKey: monitorKeys.snapshot() })
 *   })
 */

import { useEffect, useRef, useState, useCallback } from 'react'


interface SSEOptions {
  /** Callback when message is received */
  onMessage?: (eventType: string, data: unknown) => void
  /** Callback when connection opens */
  onOpen?: () => void
  /** Callback when error occurs */
  onError?: (error: Event) => void
  /** Reconnect delay in ms (default: 3000) */
  reconnectDelay?: number
  /** Enable debug logging */
  debug?: boolean
}

interface SSEReturn {
  /** Whether SSE connection is active */
  isConnected: boolean
  /** Last received event type */
  lastEventType: string | null
  /** Last received data */
  lastData: unknown
  /** Manual reconnect function */
  reconnect: () => void
}

/**
 * useSSE - Connect to SSE endpoint and listen for events
 *
 * @param channels - Array of channel names to subscribe to
 * @param onUpdate - Callback to invalidate React Query caches
 * @param options - SSE configuration options
 */
export function useSSE(
  channels: string[],
  onUpdate?: () => void,
  options: SSEOptions = {}
): SSEReturn {
  const {
    onMessage,
    onOpen,
    onError,
    reconnectDelay = 3000,
    debug = false,
  } = options

  const [isConnected, setIsConnected] = useState(false)
  const [lastEventType, setLastEventType] = useState<string | null>(null)
  const [lastData, setLastData] = useState<unknown>(null)

  const eventSourceRef = useRef<EventSource | null>(null)
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  const connect = useCallback(() => {
    // Clean up existing connection
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
    }
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
    }

    // Build SSE URL
    const channelParam = channels.join(",")
    const url = `/v1/sse/connect?channels=${encodeURIComponent(channelParam)}`

    if (debug) {
      console.log(`[SSE] Connecting to ${url}`)
    }

    const eventSource = new EventSource(url)
    eventSourceRef.current = eventSource

    // Connection opened
    eventSource.onopen = () => {
      if (debug) console.log(`[SSE] Connected to channels: ${channels.join(", ")}`)
      setIsConnected(true)
      onOpen?.()
    }

    // Handle all events by listening to the message event
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        setLastEventType("message")
        setLastData(data)
        if (debug) console.log(`[SSE] Message:`, data)

        onMessage?.("message", data)
        onUpdate?.()
      } catch {
        if (debug) console.log(`[SSE] Raw message:`, event.data)
      }
    }

    // Listen for specific event types
    const eventTypes = ["monitor_update", "strategy_update", "order_update", "reconciliation_update"]
    for (const eventType of eventTypes) {
      eventSource.addEventListener(eventType, (event) => {
        try {
          const data = JSON.parse((event as MessageEvent).data)
          setLastEventType(eventType)
          setLastData(data)
          if (debug) console.log(`[SSE] ${eventType}:`, data)

          onMessage?.(eventType, data)
          onUpdate?.()
        } catch {
          if (debug) console.log(`[SSE] Raw ${eventType}:`, (event as MessageEvent).data)
        }
      })
    }

    // Handle errors
    eventSource.onerror = (error) => {
      if (debug) console.log(`[SSE] Error, will reconnect in ${reconnectDelay}ms`)
      setIsConnected(false)
      onError?.(error)

      // Auto-reconnect after delay
      reconnectTimeoutRef.current = setTimeout(() => {
        if (debug) console.log(`[SSE] Reconnecting...`)
        connect()
      }, reconnectDelay)
    }
  }, [channels.join(","), onUpdate, onMessage, onOpen, onError, debug, reconnectDelay]) // eslint-disable-line react-hooks/exhaustive-deps

  // Connect on mount, disconnect on unmount
  useEffect(() => {
    // Guard against SSR - EventSource is not available in Node.js
    if (typeof window === 'undefined') {
      if (debug) console.log('[SSE] Skipping connect - SSR environment')
      return
    }

    connect()

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }
    }
  }, [connect])

  const reconnect = useCallback(() => {
    connect()
  }, [connect])

  return {
    isConnected,
    lastEventType,
    lastData,
    reconnect,
  }
}
