"""
Event API Routes
================
Event log, snapshot, and replay endpoints (Task 9.7, 9.11).
"""
import uuid
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict
from fastapi import APIRouter, HTTPException, Query, Path, BackgroundTasks

from trader.api.models.schemas import EventEnvelope, SnapshotEnvelope, ReplayRequest, ActionResult, ReplayJob
from trader.services import EventService

router = APIRouter(tags=["Events", "Snapshots", "Replay"])

# Replay job storage
_replay_jobs: Dict[str, ReplayJob] = {}
_replay_jobs_lock = asyncio.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clear_replay_jobs() -> None:
    """Clear replay jobs storage (tests helper)."""
    _replay_jobs.clear()


async def _run_replay_task(job_id: str, request: ReplayRequest) -> None:
    """
    后台执行 replay 任务。
    
    更新 job 状态为 RUNNING，执行完成后更新为 COMPLETED/FAILED。
    """
    async with _replay_jobs_lock:
        if job_id in _replay_jobs:
            job = _replay_jobs[job_id]
            job.status = "RUNNING"
            job.started_at = _utc_now_iso()
    
    service = EventService()
    
    try:
        # 执行 replay
        summary = await service.run_replay(request)
        
        async with _replay_jobs_lock:
            if job_id in _replay_jobs:
                job = _replay_jobs[job_id]
                job.status = "COMPLETED"
                job.finished_at = _utc_now_iso()
                job.result_summary = summary
    except Exception as e:
        async with _replay_jobs_lock:
            if job_id in _replay_jobs:
                job = _replay_jobs[job_id]
                job.status = "FAILED"
                job.finished_at = _utc_now_iso()
                job.error = str(e)


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


@router.post("/v1/replay", response_model=ReplayJob)
async def trigger_replay(request: ReplayRequest, background_tasks: BackgroundTasks):
    """
    Trigger a replay/rebuild for a stream (Task 9.7)。

    返回 job_id，前端可据此查询任务状态。
    实际 replay 在后台异步执行。
    """
    # 创建 job 记录
    job_id = str(uuid.uuid4())
    now = _utc_now_iso()
    
    job = ReplayJob(
        job_id=job_id,
        stream_key=request.stream_key,
        status="PENDING",
        requested_by=request.requested_by,
        requested_at=now,
        started_at=None,
        finished_at=None,
        result_summary=None,
        error=None,
    )
    
    # 存储 job
    async with _replay_jobs_lock:
        _replay_jobs[job_id] = job
    
    # 使用 BackgroundTasks 异步执行
    background_tasks.add_task(_run_replay_task, job_id, request)
    
    return job


@router.get("/v1/replay/{job_id}", response_model=ReplayJob)
async def get_replay_status(job_id: str = Path(..., description="Replay job ID")):
    """
    Get replay job status (Task 9.7)。
    
    返回任务的当前状态和结果摘要。
    """
    async with _replay_jobs_lock:
        if job_id not in _replay_jobs:
            raise HTTPException(status_code=404, detail=f"Replay job {job_id} not found")
        return _replay_jobs[job_id]


@router.get("/v1/replay", response_model=list[ReplayJob])
async def list_replay_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    stream_key: Optional[str] = Query(None, description="Filter by stream key"),
    requested_by: Optional[str] = Query(None, description="Filter by requester"),
    limit: int = Query(100, ge=1, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """List replay jobs with optional filters and pagination."""
    async with _replay_jobs_lock:
        jobs = list(_replay_jobs.values())

    if status:
        status_norm = status.strip().upper()
        jobs = [job for job in jobs if job.status.upper() == status_norm]
    if stream_key:
        jobs = [job for job in jobs if job.stream_key == stream_key]
    if requested_by:
        jobs = [job for job in jobs if job.requested_by == requested_by]

    jobs.sort(key=lambda item: item.requested_at, reverse=True)
    return jobs[offset : offset + limit]


# Task 9.11: 快照历史查询接口
@router.get("/v1/snapshots", response_model=list[SnapshotEnvelope])
async def list_snapshots(
    stream_key: str = Query(..., description="Stream key"),
    since_ts_ms: Optional[int] = Query(None, description="Filter by start timestamp (ms)"),
    until_ts_ms: Optional[int] = Query(None, description="Filter by end timestamp (ms)"),
    limit: int = Query(100, le=500, description="Max results"),
):
    """
    List snapshots for a stream (Task 9.11)。
    
    支持按时间范围筛选。
    """
    service = EventService()
    return service.list_snapshots(stream_key, since_ts_ms, until_ts_ms, limit)
