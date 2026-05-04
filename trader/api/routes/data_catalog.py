from __future__ import annotations

from fastapi import APIRouter

from trader.api.models.schemas import DataCatalogResponse, DataSourceStatus

router = APIRouter(tags=["DataCatalog"])


@router.get("/v1/data/catalog", response_model=DataCatalogResponse)
async def get_data_catalog():
    return DataCatalogResponse(
        feature_version="dev_smoke",
        sources=[
            DataSourceStatus(
                source="binance_ohlcv",
                status="available",
                symbols=["BTCUSDT", "ETHUSDT"],
                feature_version="dev_smoke",
                quality_score=0.75,
                notes="Control-plane catalog is wired; production feature-store freshness is next.",
            ),
            DataSourceStatus(
                source="funding_oi",
                status="available",
                symbols=["BTCUSDT", "ETHUSDT"],
                feature_version="dev_smoke",
                quality_score=0.7,
            ),
            DataSourceStatus(
                source="onchain",
                status="stub",
                symbols=["BTCUSDT", "ETHUSDT"],
                feature_version="dev_smoke",
                quality_score=0.4,
                notes="External paid feeds are not required for the first Crypto core slice.",
            ),
            DataSourceStatus(
                source="announcements",
                status="available",
                symbols=[],
                feature_version="dev_smoke",
                quality_score=0.65,
            ),
        ],
    )
