import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.api.models.schemas import (
    EventEnvelope, SnapshotEnvelope, ReplayRequest,
    ActionResult,
)
from trader.core.application.replay_runner import ReplayOptions, ReplayRunner, StreamEvent

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

    def _to_stream_events(self, request: ReplayRequest) -> List[StreamEvent]:
        """Convert storage events into replay-runner stream events."""
        raw_events = self._storage.list_events(
            stream_key=request.stream_key,
            since_ts_ms=request.from_ts_ms,
            limit=20000,
        )
        if request.to_ts_ms is not None:
            raw_events = [event for event in raw_events if int(event.get("ts_ms", 0)) <= request.to_ts_ms]

        stream_events: List[StreamEvent] = []
        for seq, event in enumerate(raw_events):
            ts_ms = int(event.get("ts_ms", 0))
            payload = event.get("payload")
            if not isinstance(payload, dict):
                payload = event.get("data") if isinstance(event.get("data"), dict) else {}
            aggregate_id = str(
                payload.get("client_order_id")
                or payload.get("cl_ord_id")
                or event.get("stream_key")
                or request.stream_key
            )
            stream_events.append(
                StreamEvent(
                    event_id=str(event.get("event_id", seq + 1)),
                    stream_key=request.stream_key,
                    seq=seq,
                    event_type=str(event.get("event_type", "")),
                    aggregate_id=aggregate_id,
                    aggregate_type="ORDER",
                    timestamp=datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc),
                    ts_ms=ts_ms,
                    data=payload,
                    metadata={"trace_id": event.get("trace_id")},
                    schema_version=int(event.get("schema_version", 1)),
                )
            )
        return stream_events

    async def run_replay(self, request: ReplayRequest) -> Dict[str, Any]:
        """
        Execute replay on current event stream and return a deterministic summary.
        """
        stream_events = self._to_stream_events(request)

        class _InMemoryReplayAdapter:
            def __init__(self, events: List[StreamEvent]):
                self._events = events

            async def read_stream(
                self,
                stream_key: str,
                from_seq: int = 0,
                limit: int = 1000,
            ) -> List[StreamEvent]:
                matched = [event for event in self._events if event.stream_key == stream_key and event.seq > from_seq]
                return matched[:limit]

            async def get_latest_seq(self, stream_key: str) -> int:
                matched = [event.seq for event in self._events if event.stream_key == stream_key]
                return max(matched) if matched else -1

        runner = ReplayRunner(event_store=_InMemoryReplayAdapter(stream_events))
        result = await runner.replay_stream(
            ReplayOptions(
                stream_key=request.stream_key,
                from_seq=-1,  # include seq=0 event
                limit=max(len(stream_events), 1),
                record_states=False,
            )
        )
        return {
            "stream_key": request.stream_key,
            "from_ts_ms": request.from_ts_ms,
            "to_ts_ms": request.to_ts_ms,
            "events_total": len(stream_events),
            "events_replayed": result.events_replayed,
            "final_seq": result.final_seq,
            "orders_reconstructed": len(result.final_state.orders_by_cl),
            "error_count": len(result.errors),
            "errors": result.errors[:5],
        }

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
        """Trigger replay synchronously (legacy service API)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            summary = asyncio.run(self.run_replay(request))
            return ActionResult(
                ok=True,
                message=(
                    f"Replay completed for stream {request.stream_key}: "
                    f"{summary['events_replayed']}/{summary['events_total']} events replayed"
                ),
            )
        return ActionResult(
            ok=True,
            message=f"Replay accepted for stream {request.stream_key}",
        )
