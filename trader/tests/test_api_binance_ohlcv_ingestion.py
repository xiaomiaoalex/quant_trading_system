from __future__ import annotations

from fastapi.testclient import TestClient

from trader.api.main import app
from trader.api.routes import data_catalog


class _FakeOHLCVWorker:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.sync_calls = 0
        self.running = False
        self.last_request = None

    async def sync_once(self, request):
        self.sync_calls += 1
        self.last_request = request
        return {
            "running": self.running,
            "feature_version": request.feature_version,
            "interval": request.interval,
            "symbols": request.symbols,
            "started_at": "2026-05-20T00:00:00Z",
            "finished_at": "2026-05-20T00:00:01Z",
            "total_imported": 2,
            "total_duplicates": 0,
            "total_conflicts": 0,
            "last_error": None,
            "symbol_results": [
                {
                    "symbol": request.symbols[0],
                    "imported": 2,
                    "duplicates": 0,
                    "conflicts": 0,
                    "first_ts_ms": 1704067200000,
                    "latest_ts_ms": 1704070800000,
                    "error": None,
                }
            ],
        }

    async def start(self, request):
        if not self.running:
            self.start_calls += 1
            self.running = True
        self.last_request = request
        return await self.status()

    async def stop(self):
        self.stop_calls += 1
        self.running = False
        return await self.status()

    async def status(self):
        return {
            "running": self.running,
            "feature_version": "research_binance_v1",
            "interval": "1h",
            "symbols": ["BTCUSDT"],
            "poll_interval_seconds": 300.0,
            "last_started_at": "2026-05-20T00:00:00Z" if self.running else None,
            "last_finished_at": None,
            "last_error": None,
            "total_imported": 0,
            "total_duplicates": 0,
            "total_conflicts": 0,
            "last_result": None,
        }


def test_sync_binance_ohlcv_endpoint_uses_worker(monkeypatch):
    worker = _FakeOHLCVWorker()
    monkeypatch.setattr(data_catalog, "get_ohlcv_ingestion_worker", lambda: worker, raising=False)

    with TestClient(app) as client:
        response = client.post(
            "/v1/data/ohlcv/sync-binance",
            json={
                "symbols": ["BTCUSDT"],
                "feature_version": "research_binance_v1",
                "interval": "1h",
                "start_ts_ms": 1704067200000,
                "end_ts_ms": 1704070800000,
                "limit": 2,
                "requested_by": "pytest",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["feature_version"] == "research_binance_v1"
    assert payload["total_imported"] == 2
    assert payload["symbol_results"][0]["symbol"] == "BTCUSDT"
    assert worker.sync_calls == 1


def test_start_binance_ohlcv_worker_is_idempotent(monkeypatch):
    worker = _FakeOHLCVWorker()
    monkeypatch.setattr(data_catalog, "get_ohlcv_ingestion_worker", lambda: worker, raising=False)

    with TestClient(app) as client:
        first = client.post(
            "/v1/data/ohlcv/worker/start",
            json={
                "symbols": ["BTCUSDT"],
                "feature_version": "research_binance_v1",
                "interval": "1h",
                "poll_interval_seconds": 60,
            },
        )
        second = client.post(
            "/v1/data/ohlcv/worker/start",
            json={
                "symbols": ["BTCUSDT"],
                "feature_version": "research_binance_v1",
                "interval": "1h",
                "poll_interval_seconds": 60,
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["running"] is True
    assert second.json()["running"] is True
    assert worker.start_calls == 1


def test_stop_and_status_binance_ohlcv_worker(monkeypatch):
    worker = _FakeOHLCVWorker()
    monkeypatch.setattr(data_catalog, "get_ohlcv_ingestion_worker", lambda: worker, raising=False)

    with TestClient(app) as client:
        start = client.post(
            "/v1/data/ohlcv/worker/start",
            json={
                "symbols": ["BTCUSDT"],
                "feature_version": "research_binance_v1",
                "interval": "1h",
            },
        )
        status_running = client.get("/v1/data/ohlcv/worker/status")
        stop = client.post("/v1/data/ohlcv/worker/stop")
        status_stopped = client.get("/v1/data/ohlcv/worker/status")

    assert start.status_code == 200
    assert status_running.status_code == 200
    assert stop.status_code == 200
    assert status_stopped.status_code == 200
    assert status_running.json()["running"] is True
    assert status_stopped.json()["running"] is False
    assert worker.stop_calls == 1
