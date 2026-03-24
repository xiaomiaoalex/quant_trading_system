from typing import List, Optional

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.api.models.schemas import (
    EventEnvelope, SnapshotEnvelope, ReplayRequest,
    ActionResult,
)


class EventService:
    """Service for events and snapshots"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

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
        """Get latest snapshot for a stream"""
        snapshot = self._storage.get_latest_snapshot(stream_key)
        if snapshot:
            return SnapshotEnvelope(**snapshot)
        return None

    def trigger_replay(self, request: ReplayRequest) -> ActionResult:
        """Trigger a replay"""
        return ActionResult(ok=True, message=f"Replay triggered for stream {request.stream_key}")
