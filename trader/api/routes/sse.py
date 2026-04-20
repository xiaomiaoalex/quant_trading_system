"""
SSE (Server-Sent Events) Manager - Real-time Updates
====================================================

Provides real-time push updates to frontend clients via SSE protocol.

Usage:
1. Backend publishes events: sse_manager.broadcast("monitor_snapshot", data)
2. Frontend connects: new EventSource("/v1/sse/monitor")
3. Frontend receives: onmessage event with data
"""

import asyncio
import json
import logging
from typing import Dict, Set, Callable, Any, Optional
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/sse", tags=["SSE"])


class SSEClient:
    """Represents a connected SSE client"""

    def __init__(self, client_id: str, queue: asyncio.Queue):
        self.client_id = client_id
        self.queue = queue
        self.subscriptions: Set[str] = set()

    async def send(self, event_type: str, data: Any) -> None:
        """Send an event to this client"""
        message = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        await self.queue.put(message)

    def subscribe(self, channel: str) -> None:
        """Subscribe to a channel"""
        self.subscriptions.add(channel)

    def unsubscribe(self, channel: str) -> None:
        """Unsubscribe from a channel"""
        self.subscriptions.discard(channel)


class SSEManager:
    """
    Server-Sent Events Manager

    Manages SSE client connections and broadcasts events to subscribed clients.
    """

    def __init__(self):
        self._clients: Dict[str, SSEClient] = {}
        self._lock = asyncio.Lock()
        self._client_counter = 0

    async def connect(self, client_id: Optional[str] = None) -> SSEClient:
        """Register a new SSE client connection"""
        async with self._lock:
            if client_id is None:
                self._client_counter += 1
                client_id = f"client_{self._client_counter}"

            queue = asyncio.Queue(maxsize=100)
            client = SSEClient(client_id, queue)
            self._clients[client_id] = client
            logger.info(f"[SSE] Client connected: {client_id}")
            return client

    async def disconnect(self, client_id: str) -> None:
        """Unregister an SSE client connection"""
        async with self._lock:
            if client_id in self._clients:
                del self._clients[client_id]
                logger.info(f"[SSE] Client disconnected: {client_id}")

    async def broadcast(self, channel: str, event_type: str, data: Any) -> int:
        """
        Broadcast an event to all clients subscribed to the channel.

        Returns the number of clients that received the event.
        """
        sent_count = 0
        async with self._lock:
            for client in self._clients.values():
                if channel in client.subscriptions or "*" in client.subscriptions:
                    try:
                        await client.send(event_type, data)
                        sent_count += 1
                    except Exception as e:
                        logger.warning(f"[SSE] Failed to send to {client.client_id}: {e}")
        if sent_count > 0:
            logger.debug(f"[SSE] Broadcast {event_type} to {sent_count} clients on channel {channel}")
        return sent_count

    def subscribe(self, client_id: str, channel: str) -> bool:
        """Subscribe a client to a channel"""
        client = self._clients.get(client_id)
        if client:
            client.subscribe(channel)
            return True
        return False

    def unsubscribe(self, client_id: str, channel: str) -> bool:
        """Unsubscribe a client from a channel"""
        client = self._clients.get(client_id)
        if client:
            client.unsubscribe(channel)
            return True
        return False

    def get_client_count(self) -> int:
        """Get the number of connected clients"""
        return len(self._clients)


# Global SSE manager instance
_sse_manager: Optional[SSEManager] = None


def get_sse_manager() -> SSEManager:
    """Get the global SSE manager instance"""
    global _sse_manager
    if _sse_manager is None:
        _sse_manager = SSEManager()
    return _sse_manager


@router.get("/connect")
async def sse_connect(
    request: Request,
    channels: str = "monitor,sse_connect",
    client_id: Optional[str] = None,
):
    """
    SSE endpoint for client connections.

    Query parameters:
    - channels: comma-separated list of channels to subscribe (default: "monitor")
    - client_id: optional client identifier

    Event types:
    - monitor_snapshot: Monitor page data updated
    - strategy_update: Strategy status changed
    - order_update: Order status changed
    - reconciliation_update: Reconciliation report available
    """

    sse_manager = get_sse_manager()
    client = await sse_manager.connect(client_id)

    # Parse and subscribe to channels
    for channel in channels.split(","):
        channel = channel.strip()
        if channel:
            client.subscribe(channel)
            logger.info(f"[SSE] {client.client_id} subscribed to {channel}")

    async def event_generator():
        """Generate SSE events for the client"""
        try:
            while True:
                try:
                    # Wait for messages with timeout to allow checking connection
                    message = await asyncio.wait_for(client.queue.get(), timeout=30.0)
                    yield message
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield b": keepalive\n\n"
                except asyncio.CancelledError:
                    break
        finally:
            await sse_manager.disconnect(client.client_id)
            logger.info(f"[SSE] {client.client_id} connection closed")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


# Helper functions to broadcast updates from other parts of the application


async def broadcast_monitor_update(data: Dict[str, Any]) -> int:
    """Broadcast monitor page update"""
    manager = get_sse_manager()
    return await manager.broadcast("monitor", "monitor_update", data)


async def broadcast_strategy_update(strategy_id: str, data: Dict[str, Any]) -> int:
    """Broadcast strategy status update"""
    manager = get_sse_manager()
    return await manager.broadcast("strategies", "strategy_update", {
        "strategy_id": strategy_id,
        **data
    })


async def broadcast_order_update(order_id: str, data: Dict[str, Any]) -> int:
    """Broadcast order update"""
    manager = get_sse_manager()
    return await manager.broadcast("orders", "order_update", {
        "order_id": order_id,
        **data
    })


async def broadcast_reconciliation_update(data: Dict[str, Any]) -> int:
    """Broadcast reconciliation report update"""
    manager = get_sse_manager()
    return await manager.broadcast("reconciliation", "reconciliation_update", data)
