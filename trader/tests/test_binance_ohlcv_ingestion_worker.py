from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trader.adapters.binance.ohlcv_source import BinanceOHLCVBar
from trader.adapters.persistence.feature_store import FeatureStore
from trader.services.ohlcv_ingestion import BinanceOHLCVIngestionWorker
from trader.storage.in_memory import ControlPlaneInMemoryStorage


class _FakeOHLCVSource:
    def __init__(self, bars: list[BinanceOHLCVBar]) -> None:
        self.bars = bars
        self.calls: list[dict[str, int | str]] = []

    async def fetch_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_ts_ms: int,
        end_ts_ms: int,
        limit: int,
    ) -> list[BinanceOHLCVBar]:
        self.calls.append(
            {
                "symbol": symbol,
                "interval": interval,
                "start_ts_ms": start_ts_ms,
                "end_ts_ms": end_ts_ms,
                "limit": limit,
            }
        )
        return [
            bar
            for bar in self.bars
            if bar.symbol == symbol and start_ts_ms <= bar.ts_ms <= end_ts_ms
        ][:limit]

    async def close(self) -> None:
        return None


def _bar(symbol: str, ts_ms: int, close: str) -> BinanceOHLCVBar:
    price = Decimal(close)
    return BinanceOHLCVBar(
        symbol=symbol,
        interval="1h",
        ts_ms=ts_ms,
        open=price - Decimal("1"),
        high=price + Decimal("1"),
        low=price - Decimal("2"),
        close=price,
        volume=Decimal("1000"),
    )


def _request(**overrides):
    payload = {
        "symbols": ["BTCUSDT"],
        "feature_version": "binance_worker_v1",
        "interval": "1h",
        "start_ts_ms": 1704067200000,
        "end_ts_ms": 1704074400000,
        "lookback_hours": 24.0,
        "poll_interval_seconds": 300.0,
        "limit": 2,
        "requested_by": "pytest",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


@pytest.mark.asyncio
async def test_worker_paginates_binance_bars_and_writes_feature_store():
    storage = ControlPlaneInMemoryStorage()
    store = FeatureStore(storage=storage)
    store._ensure_postgres = AsyncMock(return_value=False)
    bars = [
        _bar("BTCUSDT", 1704067200000, "101"),
        _bar("BTCUSDT", 1704070800000, "102"),
        _bar("BTCUSDT", 1704074400000, "103"),
    ]
    source = _FakeOHLCVSource(bars)
    worker = BinanceOHLCVIngestionWorker(feature_store=store, source=source)

    result = await worker.sync_once(_request())

    assert result.total_imported == 3
    assert result.total_duplicates == 0
    assert result.symbol_results[0].latest_ts_ms == 1704074400000
    assert len(source.calls) == 2

    coverage = await store.list_feature_coverage("ohlcv", version="binance_worker_v1")
    assert coverage[0]["symbol"] == "BTCUSDT"
    assert coverage[0]["total_points"] == 3
    assert coverage[0]["latest_ts_ms"] == 1704074400000


@pytest.mark.asyncio
async def test_worker_resumes_from_latest_feature_store_timestamp():
    storage = ControlPlaneInMemoryStorage()
    store = FeatureStore(storage=storage)
    store._ensure_postgres = AsyncMock(return_value=False)
    first_ts = 1704067200000
    await store.write_feature(
        symbol="BTCUSDT",
        feature_name="ohlcv",
        version="binance_worker_resume_v1",
        ts_ms=first_ts,
        value={"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1000.0},
    )
    bars = [
        _bar("BTCUSDT", first_ts, "101"),
        _bar("BTCUSDT", first_ts + 3_600_000, "102"),
    ]
    source = _FakeOHLCVSource(bars)
    worker = BinanceOHLCVIngestionWorker(feature_store=store, source=source)

    result = await worker.sync_once(
        _request(
            feature_version="binance_worker_resume_v1",
            start_ts_ms=None,
            end_ts_ms=first_ts + 3_600_000,
            limit=1000,
        )
    )

    assert result.total_imported == 1
    assert source.calls[0]["start_ts_ms"] == first_ts + 3_600_000
    coverage = await store.list_feature_coverage("ohlcv", version="binance_worker_resume_v1")
    assert coverage[0]["total_points"] == 2
