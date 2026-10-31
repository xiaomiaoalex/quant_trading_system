"""
History Data API Routes
=======================
币安公开归档历史行情的导入任务与数据集接口。

接口：
- POST /v1/data/history/jobs               创建导入任务（202）
- GET  /v1/data/history/jobs               查询任务进度
- GET  /v1/data/history/jobs/{job_id}      查询单个任务
- POST /v1/data/history/jobs/{job_id}/retry 重试 FAILED 任务（202）
- GET  /v1/data/history/datasets           列出数据集（覆盖 / 质量 / 持久化 / 回测资格）
- GET  /v1/data/history/datasets/{id}      查询单个数据集

错误语义：
- 400 请求非法（不支持的 symbol / 非法月份 / 月份未结束 / source-market-interval 冲突）
- 404 归档不存在或资源不存在
- 409 状态冲突（例如重试一个非 FAILED 的任务）
- 503 PostgreSQL 不可用（不创建假任务）
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Path, Query, Request

from trader.adapters.persistence.history_dataset_store import (
    HistoryStoreError,
    HistoryStoreUnavailableError,
)
from trader.api.models.schemas import (
    HistoryDatasetResponse,
    HistoryJobCreateRequest,
    HistoryJobResponse,
)
from trader.core.domain.services.ohlcv_archive import OhlcvRequestError
from trader.services.history_data_ingestion import (
    HistoryIngestionError,
    HistoryIngestionService,
    HistoryJobWorker,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["DataHistory"])

_history_worker: Optional[HistoryJobWorker] = None


def get_history_worker() -> HistoryJobWorker:
    """获取（必要时创建）历史导入 worker（单并发）。"""
    global _history_worker
    if _history_worker is None:
        _history_worker = HistoryJobWorker(HistoryIngestionService())
    return _history_worker


def history_service(request: Request) -> HistoryIngestionService:
    """取得历史导入服务。

    新 Control API 通过 ``app.state.history_service`` 注入依赖；旧 API 未注入时
    回退到默认服务（保持旧路径兼容）。
    """
    service = getattr(request.app.state, "history_service", None)
    if service is not None:
        return service
    return HistoryIngestionService()


async def start_history_worker() -> None:
    """启动历史导入 worker（lifespan 使用）。"""
    worker = get_history_worker()
    await worker.start()
    logger.info("[HistoryData] Ingestion worker started")


async def stop_history_worker() -> None:
    """停止历史导入 worker 并释放连接（lifespan 使用）。"""
    global _history_worker
    worker = _history_worker
    _history_worker = None
    if worker is not None:
        await worker.stop()
    try:
        from trader.services.history_data_ingestion import (
            reset_history_archive_client,
            reset_history_dataset_store,
        )

        await reset_history_dataset_store()
        await reset_history_archive_client()
    except Exception as exc:  # pragma: no cover - shutdown must not raise
        logger.warning("[HistoryData] Failed to release history store: %s", exc)
    logger.info("[HistoryData] Ingestion worker stopped")


def _job_payload(row: Dict[str, Any]) -> HistoryJobResponse:
    return HistoryJobResponse(
        job_id=str(row["job_id"]),
        source=str(row["source"]),
        market=str(row["market"]),
        symbol=str(row["symbol"]),
        interval=str(row["interval"]),
        month=str(row["month"]),
        status=str(row["status"]),
        stage=str(row.get("stage") or row["status"]),
        dataset_id=row.get("dataset_id"),
        feature_version=row.get("feature_version"),
        research_dataset_id=row.get("research_dataset_id"),
        rows_processed=int(row.get("rows_processed") or 0),
        bytes_downloaded=int(row.get("bytes_downloaded") or 0),
        error_code=row.get("error_code"),
        error_reason=row.get("error_reason"),
        requested_by=str(row.get("requested_by") or "console_user"),
        attempt=int(row.get("attempt") or 1),
        created_at=_iso(row.get("created_at")),
        started_at=_iso(row.get("started_at")),
        finished_at=_iso(row.get("finished_at")),
        updated_at=_iso(row.get("updated_at")),
    )


def _dataset_payload(row: Dict[str, Any]) -> HistoryDatasetResponse:
    quality = row.get("quality")
    if isinstance(quality, str):
        import json

        try:
            quality = json.loads(quality)
        except json.JSONDecodeError:
            quality = {}
    return HistoryDatasetResponse(
        dataset_id=str(row["dataset_id"]),
        source=str(row["source"]),
        market=str(row["market"]),
        symbol=str(row["symbol"]),
        interval=str(row["interval"]),
        month=str(row["month"]),
        version=int(row.get("version") or 1),
        feature_version=str(row["feature_version"]),
        archive_sha256=str(row["archive_sha256"]),
        official_sha256=row.get("official_sha256"),
        source_url=str(row["source_url"]),
        checksum_url=str(row["checksum_url"]),
        row_count=int(row.get("row_count") or 0),
        expected_points=int(row.get("expected_points") or 0),
        gap_count=int(row.get("gap_count") or 0),
        first_ts_ms=row.get("first_ts_ms"),
        last_ts_ms=row.get("last_ts_ms"),
        quality=quality if isinstance(quality, dict) else {},
        status=str(row.get("status") or "PENDING"),
        persistence_ok=bool(row.get("persistence_ok")),
        backtest_eligible=bool(row.get("backtest_eligible")),
        research_dataset_id=row.get("research_dataset_id"),
        created_at=_iso(row.get("created_at")),
        published_at=_iso(row.get("published_at")),
    )


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


@router.post(
    "/v1/data/history/jobs",
    response_model=HistoryJobResponse,
    status_code=202,
)
async def create_history_job(payload: HistoryJobCreateRequest, request: Request):
    """创建历史归档导入任务。"""
    service = history_service(request)
    try:
        job = await service.submit_job(
            symbol=payload.symbol,
            month=payload.month,
            requested_by=payload.requested_by,
        )
    except OhlcvRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HistoryStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"History store unavailable: {exc}") from exc
    except HistoryStoreError as exc:
        raise HTTPException(status_code=503, detail=f"History store error: {exc}") from exc
    return _job_payload(job)


@router.get("/v1/data/history/jobs", response_model=List[HistoryJobResponse])
async def list_history_jobs(
    status: Optional[str] = Query(default=None),
    symbol: Optional[str] = Query(default=None),
    month: Optional[str] = Query(default=None),
    limit: int = Query(100, ge=1, le=500),
    request: Request = None,
):
    """查询历史导入任务（真实阶段 / 已下载字节 / 已处理行数 / 错误码）。"""
    service = history_service(request)
    try:
        jobs = await service.list_jobs(status=status, symbol=symbol, month=month, limit=limit)
    except HistoryStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"History store unavailable: {exc}") from exc
    except HistoryStoreError as exc:
        raise HTTPException(status_code=503, detail=f"History store error: {exc}") from exc
    return [_job_payload(job) for job in jobs]


@router.get("/v1/data/history/jobs/{job_id}", response_model=HistoryJobResponse)
async def get_history_job(job_id: str = Path(...), request: Request = None):
    """查询单个导入任务。"""
    service = history_service(request)
    try:
        job = await service.get_job(job_id)
    except HistoryStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"History store unavailable: {exc}") from exc
    except HistoryStoreError as exc:
        raise HTTPException(status_code=503, detail=f"History store error: {exc}") from exc
    if job is None:
        raise HTTPException(status_code=404, detail=f"History job {job_id} not found")
    return _job_payload(job)


@router.post(
    "/v1/data/history/jobs/{job_id}/retry",
    response_model=HistoryJobResponse,
    status_code=202,
)
async def retry_history_job(job_id: str = Path(...), request: Request = None):
    """重试 FAILED 任务（幂等：复用同一个 job_id）。"""
    service = history_service(request)
    try:
        job = await service.retry_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"History job {job_id} not found") from exc
    except HistoryIngestionError as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    except HistoryStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"History store unavailable: {exc}") from exc
    except HistoryStoreError as exc:
        raise HTTPException(status_code=503, detail=f"History store error: {exc}") from exc
    return _job_payload(job)


@router.get("/v1/data/history/datasets", response_model=List[HistoryDatasetResponse])
async def list_history_datasets(
    symbol: Optional[str] = Query(default=None),
    month: Optional[str] = Query(default=None),
    only_eligible: bool = Query(default=False),
    limit: int = Query(200, ge=1, le=500),
    request: Request = None,
):
    """列出历史数据集（覆盖 / 质量 / 持久化状态 / 回测资格）。"""
    service = history_service(request)
    try:
        datasets = await service.list_datasets(
            symbol=symbol,
            month=month,
            only_eligible=only_eligible,
            limit=limit,
        )
    except HistoryStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"History store unavailable: {exc}") from exc
    except HistoryStoreError as exc:
        raise HTTPException(status_code=503, detail=f"History store error: {exc}") from exc
    return [_dataset_payload(row) for row in datasets]


@router.get("/v1/data/history/datasets/{dataset_id}", response_model=HistoryDatasetResponse)
async def get_history_dataset(dataset_id: str = Path(...), request: Request = None):
    """查询单个历史数据集（按 id 直接查询，不做列表扫描）。"""
    service = history_service(request)
    try:
        row = await service.get_dataset(dataset_id)
    except HistoryStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"History store unavailable: {exc}") from exc
    except HistoryStoreError as exc:
        raise HTTPException(status_code=503, detail=f"History store error: {exc}") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"History dataset {dataset_id} not found")
    return _dataset_payload(row)
