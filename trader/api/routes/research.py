"""Research dataset and durable backtest run endpoints."""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from trader.trading.strategy_catalog import list_installed_strategies

router = APIRouter(tags=["Research"])


class DatasetIdentity(BaseModel):
    venue: str
    market_type: str
    symbol: str
    base_asset: str
    quote_asset: str
    interval: str
    source: str
    version: str


class BarInput(BaseModel):
    open_time_ms: int
    close_time_ms: int
    available_at_ms: int | None = None
    open: str | int | float
    high: str | int | float
    low: str | int | float
    close: str | int | float
    volume: str | int | float


class DatasetCreateRequest(BaseModel):
    identity: DatasetIdentity
    bars: list[BarInput] = Field(min_length=1)


class BacktestSubmitRequest(BaseModel):
    manifest: dict[str, Any]


class ResearchDatasetSummary(BaseModel):
    """Research 数据集目录条目（只含元数据，不含行情载荷）。"""

    dataset_id: str
    symbol: Optional[str] = None
    interval: Optional[str] = None
    month: Optional[str] = None
    venue: Optional[str] = None
    market_type: Optional[str] = None
    base_asset: Optional[str] = None
    quote_asset: Optional[str] = None
    source: Optional[str] = None
    version: Optional[str] = None
    bar_count: Optional[int] = None
    coverage_start_ms: Optional[int] = None
    coverage_end_ms: Optional[int] = None
    complete: bool = False
    history_dataset_id: Optional[str] = None
    official_eligible: bool = False
    archive_sha256: Optional[str] = None
    official_sha256: Optional[str] = None
    source_url: Optional[str] = None
    checksum_url: Optional[str] = None
    provenance_hash: Optional[str] = None
    conversion_version: Optional[str] = None
    published_at: Optional[str] = None
    created_at: Optional[str] = None


class ResearchStrategyParameter(BaseModel):
    name: str
    type: str
    default: Optional[str] = None
    required: bool = False


class ResearchStrategySummary(BaseModel):
    """已安装的可选研究策略。"""

    module_path: str
    strategy_id: str
    name: str
    version: str
    code_hash: str
    params: List[ResearchStrategyParameter] = Field(default_factory=list)


def _store(request: Request):
    store = getattr(request.app.state, "research_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Research store unavailable")
    return store


def _dataset_summary(row: dict) -> ResearchDatasetSummary:
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        import json

        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    return ResearchDatasetSummary(
        dataset_id=str(row.get("dataset_id")),
        symbol=metadata.get("symbol"),
        interval=metadata.get("interval"),
        month=row.get("month"),
        venue=metadata.get("venue"),
        market_type=metadata.get("market_type"),
        base_asset=metadata.get("base_asset"),
        quote_asset=metadata.get("quote_asset"),
        source=metadata.get("source"),
        version=metadata.get("version"),
        bar_count=metadata.get("bar_count"),
        coverage_start_ms=metadata.get("coverage_start_ms"),
        coverage_end_ms=metadata.get("coverage_end_ms"),
        complete=bool(metadata.get("complete")),
        history_dataset_id=row.get("history_dataset_id"),
        official_eligible=bool(row.get("official_eligible")),
        archive_sha256=row.get("archive_sha256"),
        official_sha256=row.get("official_sha256"),
        source_url=row.get("source_url"),
        checksum_url=row.get("checksum_url"),
        provenance_hash=row.get("provenance_hash"),
        conversion_version=row.get("conversion_version"),
        published_at=row.get("published_at"),
        created_at=row.get("created_at"),
    )


@router.post("/v1/research/datasets", status_code=201)
async def register_dataset(payload: DatasetCreateRequest, request: Request):
    bars = []
    for bar in payload.bars:
        value = bar.model_dump()
        if value["available_at_ms"] is None:
            value["available_at_ms"] = value["close_time_ms"]
        bars.append(value)
    try:
        data = await _store(request).register_dataset(payload.identity.model_dump(), bars)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return data


@router.get("/v1/research/datasets")
async def list_research_datasets(
    request: Request,
    symbol: Optional[str] = Query(default=None),
    month: Optional[str] = Query(default=None),
    official_only: bool = Query(default=False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出 Research 数据集目录（只含元数据 / 质量 / provenance）。"""
    rows = await _store(request).list_datasets(
        symbol=symbol,
        month=month,
        official_only=official_only,
        limit=limit,
        offset=offset,
    )
    return [_dataset_summary(row) for row in rows]


@router.get("/v1/research/datasets/{dataset_id}")
async def get_dataset(dataset_id: str, request: Request):
    data = await _store(request).get_dataset(dataset_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return data


@router.get("/v1/research/strategies")
async def list_research_strategies():
    """列出允许提交回测的已安装策略及其参数。"""
    return list_installed_strategies()


@router.post("/v1/research/backtests", status_code=202)
async def submit_backtest(payload: BacktestSubmitRequest, request: Request):
    application = getattr(request.app.state, "research_application", None)
    if application is None:
        raise HTTPException(status_code=503, detail="Research application unavailable")
    try:
        record = await application.submit(payload.manifest)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return record


@router.get("/v1/research/backtests")
async def list_backtests(request: Request, limit: int = 100):
    return await _store(request).list(limit)


@router.get("/v1/research/backtests/{run_id}")
async def get_backtest(run_id: str, request: Request):
    record = await _store(request).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return record


@router.post("/v1/research/backtests/{run_id}/cancel", status_code=202)
async def cancel_backtest(run_id: str, request: Request):
    record = await _store(request).cancel(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return record


@router.post("/v1/research/backtests/{run_id}/retry", status_code=202)
async def retry_backtest(run_id: str, request: Request):
    """重试 FAILED 的回测运行（冻结输入不变，只重新排队）。"""
    application = getattr(request.app.state, "research_application", None)
    if application is None:
        raise HTTPException(status_code=503, detail="Research application unavailable")
    try:
        record = await application.retry(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return record
