from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from trader.adapters.persistence.feature_store import get_feature_store
from trader.api.main import app
from trader.storage.in_memory import get_storage


def _bars(start_ts_ms: int, count: int = 6) -> list[dict[str, float | int]]:
    price = 100.0
    output: list[dict[str, float | int]] = []
    for i in range(count):
        close = price + 1.0
        output.append(
            {
                "ts_ms": start_ts_ms + i * 3_600_000,
                "open": price,
                "high": close + 0.5,
                "low": price - 0.5,
                "close": close,
                "volume": 1000 + i,
            }
        )
        price = close
    return output


def _reset_version(symbol: str, version: str) -> None:
    storage = get_storage()
    for key in list(storage.feature_values_by_key):
        if key.startswith(f"{symbol}:ohlcv:{version}:"):
            del storage.feature_values_by_key[key]
    get_feature_store()._ensure_postgres = AsyncMock(return_value=False)


def test_import_ohlcv_writes_feature_store_and_updates_catalog():
    symbol = "BTCUSDT"
    version = "api_real_ohlcv_v1"
    start_ts_ms = 1704067200000
    _reset_version(symbol, version)

    with TestClient(app) as client:
        response = client.post(
            "/v1/data/ohlcv/import",
            json={
                "symbol": symbol,
                "feature_version": version,
                "interval": "1h",
                "source": "unit_test",
                "requested_by": "pytest",
                "bars": _bars(start_ts_ms, count=6),
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["imported"] == 6
        assert payload["duplicates"] == 0
        assert payload["latest_ts_ms"] == start_ts_ms + 5 * 3_600_000

        catalog = client.get("/v1/data/catalog").json()

    ohlcv_sources = [
        source for source in catalog["sources"] if source["source"] == "feature_store_ohlcv"
    ]
    assert ohlcv_sources
    source = ohlcv_sources[0]
    assert source["status"] == "available"
    assert symbol in source["symbols"]
    assert source["feature_version"] == version
    assert source["latest_ts_ms"] == start_ts_ms + 5 * 3_600_000
    assert source["total_points"] == 6
    assert source["quality_score"] == 1.0


def test_import_ohlcv_is_idempotent_for_same_values():
    symbol = "ETHUSDT"
    version = "api_real_ohlcv_idempotent_v1"
    start_ts_ms = 1704067200000
    bars = _bars(start_ts_ms, count=3)
    _reset_version(symbol, version)

    with TestClient(app) as client:
        first = client.post(
            "/v1/data/ohlcv/import",
            json={
                "symbol": symbol,
                "feature_version": version,
                "interval": "1h",
                "bars": bars,
            },
        )
        second = client.post(
            "/v1/data/ohlcv/import",
            json={
                "symbol": symbol,
                "feature_version": version,
                "interval": "1h",
                "bars": bars,
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["imported"] == 3
    assert second.json()["imported"] == 0
    assert second.json()["duplicates"] == 3


def test_import_ohlcv_rejects_invalid_bar_shape():
    symbol = "BTCUSDT"
    version = "api_real_ohlcv_invalid_v1"
    _reset_version(symbol, version)

    with TestClient(app) as client:
        response = client.post(
            "/v1/data/ohlcv/import",
            json={
                "symbol": symbol,
                "feature_version": version,
                "bars": [
                    {
                        "ts_ms": 1704067200000,
                        "open": 100,
                        "high": 99,
                        "low": 98,
                        "close": 101,
                        "volume": 10,
                    }
                ],
            },
        )

    assert response.status_code == 400
    assert "high must be >= max(open, close)" in response.json()["detail"]
