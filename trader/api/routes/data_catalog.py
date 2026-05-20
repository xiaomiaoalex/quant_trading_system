from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from trader.adapters.binance.ohlcv_source import BinanceOHLCVRestConfig, BinanceOHLCVRestSource
from trader.adapters.persistence.feature_store import (
    FeatureStore,
    FeatureVersionConflictError,
    get_feature_store,
)
from trader.api.env_config import get_binance_env_config
from trader.api.models.schemas import (
    BinanceOHLCVIngestionRequest,
    BinanceOHLCVIngestionResult,
    BinanceOHLCVWorkerStatus,
    DataCatalogResponse,
    DataSourceStatus,
    OHLCVImportRequest,
    OHLCVImportResponse,
)
from trader.services.ohlcv_ingestion import BinanceOHLCVIngestionWorker

router = APIRouter(tags=["DataCatalog"])
_ohlcv_ingestion_worker: Any | None = None


def _validate_ohlcv_bar(bar: Any) -> None:
    if bar.high < max(bar.open, bar.close):
        raise ValueError("high must be >= max(open, close)")
    if bar.low > min(bar.open, bar.close):
        raise ValueError("low must be <= min(open, close)")


def _quality_score(total_points: int) -> float:
    return 1.0 if total_points > 0 else 0.0


def _serialize_worker_payload(payload: Any) -> Any:
    if hasattr(payload, "to_dict"):
        return payload.to_dict()
    return payload


def get_ohlcv_ingestion_worker() -> Any:
    global _ohlcv_ingestion_worker
    if _ohlcv_ingestion_worker is None:
        env_config = get_binance_env_config()
        source = BinanceOHLCVRestSource(
            BinanceOHLCVRestConfig(
                base_url=env_config["rest_base"],
                timeout=float(os.environ.get("BINANCE_OHLCV_TIMEOUT_SECONDS", "10")),
            )
        )
        _ohlcv_ingestion_worker = BinanceOHLCVIngestionWorker(
            feature_store=get_feature_store(),
            source=source,
        )
    return _ohlcv_ingestion_worker


def set_ohlcv_ingestion_worker(worker: Any | None) -> None:
    global _ohlcv_ingestion_worker
    _ohlcv_ingestion_worker = worker


def _static_sources(feature_version: str) -> List[DataSourceStatus]:
    return [
        DataSourceStatus(
            source="funding_oi",
            status="available",
            symbols=["BTCUSDT", "ETHUSDT"],
            feature_version=feature_version,
            quality_score=0.7,
        ),
        DataSourceStatus(
            source="onchain",
            status="stub",
            symbols=["BTCUSDT", "ETHUSDT"],
            feature_version=feature_version,
            quality_score=0.4,
            notes="External paid feeds are not required for the first Crypto core slice.",
        ),
        DataSourceStatus(
            source="announcements",
            status="available",
            symbols=[],
            feature_version=feature_version,
            quality_score=0.65,
        ),
    ]


async def _ohlcv_sources(feature_store: FeatureStore) -> List[DataSourceStatus]:
    coverage = await feature_store.list_feature_coverage("ohlcv")
    if not coverage:
        return [
            DataSourceStatus(
                source="feature_store_ohlcv",
                status="missing",
                symbols=[],
                feature_version="dev_smoke",
                quality_score=0.0,
                notes="No real FeatureStore OHLCV is available yet.",
            )
        ]

    output: List[DataSourceStatus] = []
    for item in coverage:
        output.append(
            DataSourceStatus(
                source="feature_store_ohlcv",
                status="available",
                symbols=[str(item["symbol"])],
                latest_ts_ms=item.get("latest_ts_ms"),
                first_ts_ms=item.get("first_ts_ms"),
                feature_version=str(item["version"]),
                quality_score=_quality_score(int(item.get("total_points") or 0)),
                total_points=int(item.get("total_points") or 0),
                coverage_percent=100.0 if int(item.get("total_points") or 0) > 0 else 0.0,
                notes="Versioned real OHLCV available for vectorbt real_feature_store backtests.",
            )
        )
    return output


@router.get("/v1/data/catalog", response_model=DataCatalogResponse)
async def get_data_catalog():
    feature_store = get_feature_store()
    ohlcv_sources = await _ohlcv_sources(feature_store)
    available_versions = [
        source.feature_version for source in ohlcv_sources if source.status == "available"
    ]
    feature_version = available_versions[-1] if available_versions else "dev_smoke"
    return DataCatalogResponse(
        feature_version=feature_version,
        sources=ohlcv_sources + _static_sources(feature_version),
    )


@router.get("/v1/data/ohlcv/coverage", response_model=List[DataSourceStatus])
async def get_ohlcv_coverage(
    feature_version: Optional[str] = Query(default=None),
):
    feature_store = get_feature_store()
    coverage = await feature_store.list_feature_coverage("ohlcv", version=feature_version)
    return [
        DataSourceStatus(
            source="feature_store_ohlcv",
            status="available",
            symbols=[str(item["symbol"])],
            latest_ts_ms=item.get("latest_ts_ms"),
            first_ts_ms=item.get("first_ts_ms"),
            feature_version=str(item["version"]),
            quality_score=_quality_score(int(item.get("total_points") or 0)),
            total_points=int(item.get("total_points") or 0),
            coverage_percent=100.0 if int(item.get("total_points") or 0) > 0 else 0.0,
        )
        for item in coverage
    ]


@router.post("/v1/data/ohlcv/import", response_model=OHLCVImportResponse)
async def import_ohlcv(request: OHLCVImportRequest):
    if not request.bars:
        raise HTTPException(status_code=400, detail="bars cannot be empty")

    imported = 0
    duplicates = 0
    first_ts_ms: Optional[int] = None
    latest_ts_ms: Optional[int] = None
    feature_store = get_feature_store()

    for bar in sorted(request.bars, key=lambda item: item.ts_ms):
        try:
            _validate_ohlcv_bar(bar)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        value: Dict[str, float] = {
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": float(bar.volume),
        }
        meta: Dict[str, Any] = {
            "interval": request.interval,
            "source": request.source,
        }
        if request.requested_by:
            meta["requested_by"] = request.requested_by

        try:
            created, is_duplicate = await feature_store.write_feature(
                symbol=request.symbol,
                feature_name="ohlcv",
                version=request.feature_version,
                ts_ms=bar.ts_ms,
                value=value,
                meta=meta,
            )
        except FeatureVersionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        if created:
            imported += 1
        if is_duplicate:
            duplicates += 1

        first_ts_ms = bar.ts_ms if first_ts_ms is None else min(first_ts_ms, bar.ts_ms)
        latest_ts_ms = bar.ts_ms if latest_ts_ms is None else max(latest_ts_ms, bar.ts_ms)

    total_points = imported + duplicates
    return OHLCVImportResponse(
        symbol=request.symbol,
        feature_version=request.feature_version,
        interval=request.interval,
        imported=imported,
        duplicates=duplicates,
        first_ts_ms=first_ts_ms,
        latest_ts_ms=latest_ts_ms,
        total_points=total_points,
    )


@router.post("/v1/data/ohlcv/sync-binance", response_model=BinanceOHLCVIngestionResult)
async def sync_binance_ohlcv(request: BinanceOHLCVIngestionRequest):
    worker = get_ohlcv_ingestion_worker()
    try:
        result = await worker.sync_once(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_worker_payload(result)


@router.post("/v1/data/ohlcv/worker/start", response_model=BinanceOHLCVWorkerStatus)
async def start_binance_ohlcv_worker(request: BinanceOHLCVIngestionRequest):
    worker = get_ohlcv_ingestion_worker()
    try:
        status = await worker.start(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _serialize_worker_payload(status)


@router.post("/v1/data/ohlcv/worker/stop", response_model=BinanceOHLCVWorkerStatus)
async def stop_binance_ohlcv_worker():
    worker = get_ohlcv_ingestion_worker()
    status = await worker.stop()
    return _serialize_worker_payload(status)


@router.get("/v1/data/ohlcv/worker/status", response_model=BinanceOHLCVWorkerStatus)
async def get_binance_ohlcv_worker_status():
    worker = get_ohlcv_ingestion_worker()
    status = await worker.status()
    return _serialize_worker_payload(status)


def build_ohlcv_ingestion_request_from_env() -> BinanceOHLCVIngestionRequest | None:
    enabled = os.environ.get("BINANCE_OHLCV_INGESTION_ENABLED", "false").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None

    raw_symbols = os.environ.get("BINANCE_OHLCV_SYMBOLS", "BTCUSDT,ETHUSDT")
    symbols = [item.strip() for item in raw_symbols.split(",") if item.strip()]
    return BinanceOHLCVIngestionRequest(
        symbols=symbols,
        feature_version=os.environ.get("BINANCE_OHLCV_FEATURE_VERSION", "binance_ohlcv_v1"),
        interval=os.environ.get("BINANCE_OHLCV_INTERVAL", "1h"),
        lookback_hours=float(os.environ.get("BINANCE_OHLCV_LOOKBACK_HOURS", "24")),
        poll_interval_seconds=float(os.environ.get("BINANCE_OHLCV_POLL_SECONDS", "300")),
        limit=int(os.environ.get("BINANCE_OHLCV_LIMIT", "1000")),
        requested_by="lifespan",
    )


async def maybe_start_ohlcv_ingestion_from_env() -> None:
    request = build_ohlcv_ingestion_request_from_env()
    if request is None:
        return
    await get_ohlcv_ingestion_worker().start(request)


async def shutdown_ohlcv_ingestion_worker() -> None:
    worker = _ohlcv_ingestion_worker
    if worker is None:
        return
    if hasattr(worker, "close"):
        await worker.close()
    else:
        await worker.stop()
