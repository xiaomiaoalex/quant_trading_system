"""
History Data API Tests
======================
覆盖 /v1/data/history/* 接口：创建任务、查询进度、重试、数据集列表与错误语义。

不访问网络、不依赖 PostgreSQL：monkeypatch 服务层的存储与归档客户端。
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from trader.api.main import app
from trader.core.domain.services.ohlcv_archive import (
    KlineBar,
    expected_points_for_month,
    month_bounds_ms,
)
from trader.services import history_data_ingestion as ingestion_module
from trader.services.history_data_ingestion import HistoryIngestionService, HistoryJobWorker
from trader.tests.fakes.fake_history_store import (
    FakeArchiveClient,
    FakeFeatureStore,
    FakeHistoryDatasetStore,
)

YEAR = 2026
MONTH = 9
MONTH_STR = "2026-09"


def _full_month_bars() -> list[KlineBar]:
    start_ms, _ = month_bounds_ms(YEAR, MONTH)
    total = expected_points_for_month(YEAR, MONTH)
    return [
        KlineBar(
            open_time_ms=start_ms + index * 60_000,
            open=Decimal("100.0"),
            high=Decimal("101.0"),
            low=Decimal("99.0"),
            close=Decimal("100.5"),
            volume=Decimal("12.5"),
        )
        for index in range(total)
    ]


@pytest.fixture
def history_backend(monkeypatch):
    feature_store = FakeFeatureStore()
    store = FakeHistoryDatasetStore(feature_store=feature_store)
    archive_client = FakeArchiveClient(zip_bytes=b"zip", bars=_full_month_bars())

    async def _fake_store():
        return store

    monkeypatch.setattr(ingestion_module, "get_history_dataset_store", _fake_store)
    monkeypatch.setattr(
        HistoryIngestionService,
        "_get_archive_client",
        lambda self: archive_client,
    )

    # 测试期间不启动后台 worker，避免任务被自动领取导致状态断言不稳定
    async def _noop_start(self) -> None:
        return None

    monkeypatch.setattr(HistoryJobWorker, "start", _noop_start)
    return store


def test_create_history_job_returns_202_with_queued_job(history_backend):
    with TestClient(app) as client:
        response = client.post(
            "/v1/data/history/jobs",
            json={"symbol": "BTCUSDT", "month": MONTH_STR},
        )
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "QUEUED"
    assert payload["stage"] == "QUEUED"
    assert payload["symbol"] == "BTCUSDT"
    assert payload["interval"] == "1m"
    assert payload["market"] == "spot"
    assert payload["source"] == "binance_vision"
    assert payload["rows_processed"] == 0
    assert payload["bytes_downloaded"] == 0


def test_create_history_job_rejects_unsupported_symbol(history_backend):
    with TestClient(app) as client:
        response = client.post(
            "/v1/data/history/jobs",
            json={"symbol": "SOLUSDT", "month": MONTH_STR},
        )
    assert response.status_code == 400


def test_create_history_job_rejects_conflicting_market(history_backend):
    with TestClient(app) as client:
        response = client.post(
            "/v1/data/history/jobs",
            json={"symbol": "BTCUSDT", "month": MONTH_STR, "market": "futures"},
        )
    assert response.status_code == 422


def test_create_history_job_rejects_conflicting_interval(history_backend):
    with TestClient(app) as client:
        response = client.post(
            "/v1/data/history/jobs",
            json={"symbol": "BTCUSDT", "month": MONTH_STR, "interval": "1h"},
        )
    assert response.status_code == 422


def test_create_history_job_reports_missing_archive(history_backend, monkeypatch):
    monkeypatch.setattr(
        HistoryIngestionService,
        "_get_archive_client",
        lambda self: FakeArchiveClient(exists=False),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/data/history/jobs",
            json={"symbol": "BTCUSDT", "month": MONTH_STR},
        )
    assert response.status_code == 404


def test_list_history_jobs_returns_progress_fields(history_backend):
    with TestClient(app) as client:
        client.post("/v1/data/history/jobs", json={"symbol": "BTCUSDT", "month": MONTH_STR})
        response = client.get("/v1/data/history/jobs")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["status"] == "QUEUED"
    assert "stage" in payload[0]
    assert "bytes_downloaded" in payload[0]
    assert "rows_processed" in payload[0]


def test_get_history_job_returns_404_for_unknown_job(history_backend):
    with TestClient(app) as client:
        response = client.get("/v1/data/history/jobs/hjob_missing")
    assert response.status_code == 404


def test_retry_history_job_requeues_failed_job(history_backend):
    with TestClient(app) as client:
        created = client.post(
            "/v1/data/history/jobs",
            json={"symbol": "BTCUSDT", "month": MONTH_STR},
        ).json()
        job_id = created["job_id"]

        history_backend.jobs[job_id]["status"] = "FAILED"
        history_backend.jobs[job_id]["error_code"] = "NETWORK_ERROR"

        response = client.post(f"/v1/data/history/jobs/{job_id}/retry")

    assert response.status_code == 202
    assert response.json()["status"] == "QUEUED"
    assert response.json()["attempt"] == 2


def test_retry_history_job_returns_409_for_running_job(history_backend):
    with TestClient(app) as client:
        created = client.post(
            "/v1/data/history/jobs",
            json={"symbol": "BTCUSDT", "month": MONTH_STR},
        ).json()
        response = client.post(f"/v1/data/history/jobs/{created['job_id']}/retry")
    assert response.status_code == 409


def test_list_history_datasets_returns_empty_list(history_backend):
    with TestClient(app) as client:
        response = client.get("/v1/data/history/datasets")
    assert response.status_code == 200
    assert response.json() == []


def test_list_history_datasets_reports_published_dataset(history_backend):
    history_backend.datasets["hds_test"] = {
        "dataset_id": "hds_test",
        "source": "binance_vision",
        "market": "spot",
        "symbol": "BTCUSDT",
        "interval": "1m",
        "month": MONTH_STR,
        "version": 1,
        "feature_version": "binance_spot_1m_BTCUSDT_2026-09_aaaaaaaaaaaa",
        "archive_path": "/tmp/a.zip",
        "archive_sha256": "a" * 64,
        "official_sha256": "a" * 64,
        "source_url": "https://example/a.zip",
        "checksum_url": "https://example/a.zip.CHECKSUM",
        "row_count": 43200,
        "expected_points": 43200,
        "gap_count": 0,
        "first_ts_ms": month_bounds_ms(YEAR, MONTH)[0],
        "last_ts_ms": month_bounds_ms(YEAR, MONTH)[1] - 60_000,
        "quality": {"complete": True},
        "status": "PUBLISHED",
        "persistence_ok": True,
        "backtest_eligible": True,
        "created_at": "2026-10-29T00:00:00Z",
        "published_at": "2026-10-29T00:00:00Z",
    }
    with TestClient(app) as client:
        response = client.get("/v1/data/history/datasets?only_eligible=true")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["market"] == "spot"
    assert payload[0]["interval"] == "1m"
    assert payload[0]["row_count"] == 43200
    assert payload[0]["backtest_eligible"] is True


def test_get_history_dataset_returns_404_for_unknown_dataset(history_backend):
    with TestClient(app) as client:
        response = client.get("/v1/data/history/datasets/hds_missing")
    assert response.status_code == 404
