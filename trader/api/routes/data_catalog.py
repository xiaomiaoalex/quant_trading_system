from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from trader.adapters.persistence.feature_store import (
    FeatureStore,
    FeatureVersionConflictError,
    get_feature_store,
)
from trader.api.models.schemas import (
    DataCatalogResponse,
    DataSourceStatus,
    OHLCVImportRequest,
    OHLCVImportResponse,
)

router = APIRouter(tags=["DataCatalog"])


def _validate_ohlcv_bar(bar: Any) -> None:
    if bar.high < max(bar.open, bar.close):
        raise ValueError("high must be >= max(open, close)")
    if bar.low > min(bar.open, bar.close):
        raise ValueError("low must be <= min(open, close)")


def _quality_score(total_points: int) -> float:
    return 1.0 if total_points > 0 else 0.0


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
