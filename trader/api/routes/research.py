"""Research dataset and durable backtest run endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

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


def _store(request: Request):
    store = getattr(request.app.state, "research_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Research store unavailable")
    return store


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


@router.get("/v1/research/datasets/{dataset_id}")
async def get_dataset(dataset_id: str, request: Request):
    data = await _store(request).get_dataset(dataset_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return data


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
