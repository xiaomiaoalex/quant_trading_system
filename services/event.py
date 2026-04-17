from typing import TYPE_CHECKING, List, Optional

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.api.models.schemas import (
    EventEnvelope, SnapshotEnvelope, ReplayRequest,
    ActionResult,
)

if TYPE_CHECKING:
    from trader.adapters.persistence.postgres.snapshot_storage import PostgresSnapshotStorage


class EventService:
    """
    Service for events and snapshots
    
    Supports dual storage backend:
    - InMemoryStorage: For events (always in-memory for performance)
    - PostgresSnapshotStorage: For snapshots (production-grade persistence)
    
    The snapshot storage is optional and falls back to InMemoryStorage
    when not available.
    """

    def __init__(
        self,
        storage: Optional[InMemoryStorage] = None,
        snapshot_storage: Optional["PostgresSnapshotStorage"] = None,
    ):
        self._storage = storage or get_storage()
        self._snapshot_storage = snapshot_storage

    def list_events(
        self,
        stream_key: Optional[str] = None,
        event_type: Optional[str] = None,
        trace_id: Optional[str] = None,
        since_ts_ms: Optional[int] = None,
        limit: int = 2000,
    ) -> List[EventEnvelope]:
        """Query events"""
        events = self._storage.list_events(stream_key, event_type, trace_id, since_ts_ms, limit)
        return [EventEnvelope(**e) for e in events]

    def get_latest_snapshot(self, stream_key: str) -> Optional[SnapshotEnvelope]:
        """
        Get latest snapshot for a stream (sync version).
        
        NOTE: This is a synchronous method. For async contexts, use
        `get_latest_snapshot_async()` instead to avoid potential deadlocks
        from blocking on future.result().
        
        Priority:
        1. PostgresSnapshotStorage (production)
        2. InMemoryStorage (fallback)
        """
        # Try PG storage first if available
        if self._snapshot_storage is not None:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # We're in an async context - use run_coroutine_threadsafe
                    # WARNING: This blocks until the coroutine completes.
                    # For async callers, use get_latest_snapshot_async() instead.
                    import concurrent.futures
                    future = asyncio.run_coroutine_threadsafe(
                        self._snapshot_storage.get_latest(stream_key),
                        loop
                    )
                    snapshot = future.result(timeout=5.0)
                else:
                    snapshot = loop.run_until_complete(
                        self._snapshot_storage.get_latest(stream_key)
                    )
                if snapshot is not None:
                    return snapshot
            except Exception:
                # Fall through to InMemoryStorage
                pass
        
        # Fallback to InMemoryStorage
        snapshot_dict = self._storage.get_latest_snapshot(stream_key)
        if snapshot_dict:
            return SnapshotEnvelope(**snapshot_dict)
        return None

    async def get_latest_snapshot_async(self, stream_key: str) -> Optional[SnapshotEnvelope]:
        """
        Async version of get_latest_snapshot
        
        Use this method when within an async context for proper awaiting.
        """
        # Try PG storage first if available
        if self._snapshot_storage is not None:
            snapshot = await self._snapshot_storage.get_latest(stream_key)
            if snapshot is not None:
                return snapshot
        
        # Fallback to InMemoryStorage
        snapshot_dict = self._storage.get_latest_snapshot(stream_key)
        if snapshot_dict:
            return SnapshotEnvelope(**snapshot_dict)
        return None

    def list_snapshots(
        self,
        stream_key: str,
        since_ts_ms: Optional[int] = None,
        until_ts_ms: Optional[int] = None,
        limit: int = 100,
    ) -> List[SnapshotEnvelope]:
        """
        List snapshots for a stream with time range filter (Task 9.11).
        
        Priority:
        1. PostgresSnapshotStorage (production)
        2. InMemoryStorage (fallback)
        """
        # Try PG storage first if available
        if self._snapshot_storage is not None:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    future = asyncio.run_coroutine_threadsafe(
                        self._snapshot_storage.list_snapshots(stream_key, since_ts_ms, until_ts_ms, limit),
                        loop
                    )
                    snapshots = future.result(timeout=5.0)
                else:
                    snapshots = loop.run_until_complete(
                        self._snapshot_storage.list_snapshots(stream_key, since_ts_ms, until_ts_ms, limit)
                    )
                if snapshots:
                    return [SnapshotEnvelope(**s) for s in snapshots]
            except Exception:
                pass
        
        # Fallback to InMemoryStorage
        snapshot_dicts = self._storage.list_snapshots(stream_key, since_ts_ms, until_ts_ms, limit)
        return [SnapshotEnvelope(**s) for s in snapshot_dicts]

    def trigger_replay(self, request: ReplayRequest) -> ActionResult:
        """Trigger a replay"""
        return ActionResult(ok=True, message=f"Replay triggered for stream {request.stream_key}")
