"""
Event API Routes
================
Event log, snapshot, and replay endpoints.
"""
from typing import Optional
from fastapi import APIRouter, Query

from trader.api.models.schemas import EventEnvelope, SnapshotEnvelope, ReplayRequest, ActionResult
from trader.services import EventService

router = APIRouter(tags=["Events", "Snapshots", "Replay"])


@router.get("/v1/events", response_model=list[EventEnvelope])
async def list_events(
    stream_key: Optional[str] = Query(None, description="Filter by stream key"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    trace_id: Optional[str] = Query(None, description="Filter by trace ID"),
    since_ts_ms: Optional[int] = Query(None, description="Filter by timestamp (ms)"),
    limit: int = Query(2000, description="Max results", le=20000),
):
    """
    Query canonical event log.

    Returns a list of events with optional filters.
    """
    service = EventService()
    return service.list_events(stream_key, event_type, trace_id, since_ts_ms, limit)


@router.get("/v1/snapshots/latest", response_model=Optional[SnapshotEnvelope])
async def get_latest_snapshot(stream_key: str = Query(..., description="Stream key")):
    """
    Get latest snapshot for a stream.

    Returns the latest snapshot for the specified stream.
    """
    service = EventService()
    return service.get_latest_snapshot(stream_key)


@router.post("/v1/replay", response_model=ActionResult)
async def trigger_replay(request: ReplayRequest):
    """
    Trigger a replay/rebuild for a stream.

    Triggers a replay of events for the specified stream (admin operation).
    """
    service = EventService()
    return service.trigger_replay(request)
