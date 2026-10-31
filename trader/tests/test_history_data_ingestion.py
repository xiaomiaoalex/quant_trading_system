"""
History Data Ingestion Service Tests
=====================================
覆盖历史归档导入任务的创建 / 复用 / 执行 / 失败分类 / 重试 / 重启恢复。

不访问网络、不依赖 PostgreSQL：使用 trader/tests/fakes 下的替身。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trader.adapters.market_data.binance_vision import (
    ArchiveChecksumMismatch,
    ArchiveDownloadError,
    ArchiveNotFoundError,
)
from trader.core.domain.services.ohlcv_archive import (
    KlineBar,
    OhlcvRequestError,
    expected_points_for_month,
    month_bounds_ms,
)
from trader.services.history_data_ingestion import (
    ERROR_ARCHIVE_NOT_FOUND,
    ERROR_CHECKSUM_MISMATCH,
    ERROR_NETWORK,
    ERROR_PERSISTENCE,
    ERROR_QUALITY,
    HistoryIngestionError,
    HistoryIngestionService,
)
from trader.tests.fakes.fake_history_store import (
    FakeArchiveClient,
    FakeFeatureStore,
    FakeHistoryDatasetStore,
    FakeResearchHistoryBridgeStore,
)

YEAR = 2026
MONTH = 9
FULL_MONTH = "2026-09"


def _full_month_bars(skip: int | None = None) -> list[KlineBar]:
    start_ms, _ = month_bounds_ms(YEAR, MONTH)
    total = expected_points_for_month(YEAR, MONTH)
    bars = []
    for index in range(total):
        if skip is not None and index == skip:
            continue
        ts_ms = start_ms + index * 60_000
        bars.append(
            KlineBar(
                open_time_ms=ts_ms,
                open=Decimal("100.0"),
                high=Decimal("101.0"),
                low=Decimal("99.0"),
                close=Decimal("100.5"),
                volume=Decimal("12.5"),
            )
        )
    return bars


def _service(
    *,
    bars=None,
    feature_store=None,
    archive_client=None,
    store=None,
):
    feature_store = feature_store or FakeFeatureStore()
    store = store or FakeHistoryDatasetStore(feature_store=feature_store)
    archive_client = archive_client or FakeArchiveClient(
        zip_bytes=b"zip",
        bars=bars if bars is not None else _full_month_bars(),
    )
    service = HistoryIngestionService(
        store=store,
        archive_client=archive_client,
        feature_store=feature_store,
        bridge_store=FakeResearchHistoryBridgeStore(store),
    )
    return service, store, feature_store, archive_client


async def test_submit_job_creates_queued_job():
    service, store, _, _ = _service()
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)
    assert job["status"] == "QUEUED"
    assert job["symbol"] == "BTCUSDT"
    assert job["month"] == FULL_MONTH
    assert job["interval"] == "1m"
    assert job["market"] == "spot"
    assert job["source"] == "binance_vision"


async def test_submit_job_rejects_unsupported_symbol():
    service, _, _, _ = _service()
    with pytest.raises(OhlcvRequestError):
        await service.submit_job(symbol="SOLUSDT", month=FULL_MONTH)


async def test_submit_job_rejects_month_that_has_not_ended():
    service, _, _, _ = _service()
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    with pytest.raises(OhlcvRequestError):
        service.validate_request("BTCUSDT", "2026-09", now=now)


async def test_submit_job_reuses_active_job():
    service, _, _, archive_client = _service()
    first = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)
    second = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)
    assert first["job_id"] == second["job_id"]
    # 提交路径不做任何远程调用
    assert archive_client.download_calls == 0


async def test_submit_job_reuses_active_job_without_touching_remote():
    archive_client = FakeArchiveClient(download_error=ArchiveDownloadError("network down"))
    service, _, _, _ = _service(archive_client=archive_client)
    first = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)
    second = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)

    assert first["job_id"] == second["job_id"]
    assert second["status"] == "QUEUED"
    assert archive_client.download_calls == 0


async def test_submit_job_returns_associated_job_when_dataset_published():
    service, store, _, archive_client = _service()
    await store.insert_dataset(
        dataset_id="hds_existing",
        source="binance_vision",
        market="spot",
        symbol="BTCUSDT",
        interval="1m",
        month=FULL_MONTH,
        version=1,
        feature_version="binance_spot_1m_BTCUSDT_2026-09_aaaaaaaaaaaa",
        archive_path="/tmp/a.zip",
        archive_sha256="a" * 64,
        source_url="https://example/a.zip",
        checksum_url="https://example/a.zip.CHECKSUM",
        status="PUBLISHED",
        persistence_ok=True,
        backtest_eligible=True,
        row_count=43200,
        expected_points=43200,
        job_id="hjob_owner",
    )
    await store.create_job(
        job_id="hjob_owner",
        source="binance_vision",
        market="spot",
        symbol="BTCUSDT",
        interval="1m",
        month=FULL_MONTH,
        status="SUCCEEDED",
        stage="PUBLISHED",
        dataset_id="hds_existing",
        feature_version="binance_spot_1m_BTCUSDT_2026-09_aaaaaaaaaaaa",
    )

    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)

    assert job["status"] == "SUCCEEDED"
    assert job["dataset_id"] == "hds_existing"
    # 复用关联的既有任务，不新建任务行
    assert job["job_id"] == "hjob_owner"
    assert len(store.jobs) == 1
    # 完全走数据库，不触发任何远程调用
    assert archive_client.download_calls == 0


async def test_submit_job_succeeds_when_remote_archive_is_gone():
    """归档被官方移除 / 修订后，重复提交仍返回关联的成功任务（不做远程探测）。"""
    service, store, _, archive_client = _service()
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)
    result = await service.run_job(job["job_id"])
    assert result["status"] == "SUCCEEDED"

    # 归档随后消失（worker 下载阶段才会感知）
    archive_client.set_payload(b"revised-bytes")
    before = len(store.jobs)

    reused = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)

    assert reused["status"] == "SUCCEEDED"
    assert reused["job_id"] == job["job_id"]
    assert reused["dataset_id"] == result["dataset_id"]
    assert len(store.jobs) == before
    assert archive_client.download_calls == 1  # 只有首次真正导入时下载过


async def test_submit_job_never_probes_remote_for_new_job():
    archive_client = FakeArchiveClient(download_error=ArchiveDownloadError("network down"))
    service, _, _, _ = _service(archive_client=archive_client)

    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)

    assert job["status"] == "QUEUED"
    assert archive_client.download_calls == 0


async def test_run_job_publishes_dataset_and_marks_job_succeeded():
    service, store, feature_store, _ = _service()
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)

    result = await service.run_job(job["job_id"])

    assert result["status"] == "SUCCEEDED"
    assert result["stage"] == "PUBLISHED"
    assert result["rows_processed"] == 43200
    dataset = store.datasets[result["dataset_id"]]
    assert dataset["status"] == "PUBLISHED"
    assert dataset["persistence_ok"] is True
    assert dataset["backtest_eligible"] is True
    assert dataset["row_count"] == 43200
    assert dataset["expected_points"] == 43200
    assert dataset["gap_count"] == 0
    assert feature_store.write_calls == 43200


async def test_run_job_publishes_research_dataset_with_frozen_provenance():
    service, store, _, _ = _service()
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)

    result = await service.run_job(job["job_id"])

    bridge = service.bridge_store
    assert len(bridge.datasets) == 1
    research_dataset_id, document = next(iter(bridge.datasets.items()))
    assert research_dataset_id.startswith("ds_")
    assert document["dataset_id"] == research_dataset_id
    assert document["identity"]["symbol"] == "BTCUSDT"
    assert document["identity"]["base_asset"] == "BTC"
    assert document["identity"]["quote_asset"] == "USDT"
    assert document["identity"]["source"] == "binance_vision"
    assert document["quality"]["bar_count"] == 43200
    first_bar = document["bars"][0]
    assert first_bar["close_time_ms"] == first_bar["open_time_ms"] + 60_000
    assert first_bar["available_at_ms"] == first_bar["close_time_ms"]

    link = bridge.links[research_dataset_id]
    assert link["official_eligible"] is True
    assert link["provenance_hash"]
    assert link["archive_sha256"]
    assert link["month"] == FULL_MONTH
    assert link["bar_count"] == 43200
    assert link["symbol"] == "BTCUSDT"

    # 历史数据集必须指向同一个 Research 数据集
    history_dataset = store.datasets[result["dataset_id"]]
    assert history_dataset["research_dataset_id"] == research_dataset_id


async def test_reimport_of_the_same_archive_is_idempotent_in_the_research_plane():
    service, store, _, _ = _service()
    first = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)
    await service.run_job(first["job_id"])
    second = await store.create_job(
        job_id="hjob_same_hash",
        source="binance_vision",
        market="spot",
        symbol="BTCUSDT",
        interval="1m",
        month=FULL_MONTH,
    )

    await service.run_job(second["job_id"])

    bridge = service.bridge_store
    assert len(bridge.datasets) == 1
    assert len(bridge.links) == 1
    assert len(store.datasets) == 1


async def test_failed_atomic_publish_leaves_no_selectable_half_product():
    service, store, _, _ = _service()
    service.bridge_store.fail_publish = True
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)

    result = await service.run_job(job["job_id"])

    assert result["status"] == "FAILED"
    assert result["error_code"] == ERROR_PERSISTENCE
    assert len(store.datasets) == 1
    dataset = next(iter(store.datasets.values()))
    assert dataset["status"] != "PUBLISHED"
    assert dataset["backtest_eligible"] is False
    assert dataset["research_dataset_id"] is None
    assert service.bridge_store.datasets == {}


async def test_run_job_reuses_dataset_for_same_hash():
    """同一 symbol/month 且归档 hash 不变 → 复用同一个数据集，版本号不增加。"""
    service, store, _, archive_client = _service()
    first = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)
    await service.run_job(first["job_id"])
    v1 = list(store.datasets.values())[0]

    # 再次导入同一份归档（hash 不变）
    second = await store.create_job(
        job_id="hjob_same_hash",
        source="binance_vision",
        market="spot",
        symbol="BTCUSDT",
        interval="1m",
        month=FULL_MONTH,
    )
    result = await service.run_job(second["job_id"])

    assert result["status"] == "SUCCEEDED"
    assert len(store.datasets) == 1
    assert list(store.datasets.values())[0]["version"] == v1["version"] == 1
    assert archive_client.download_calls == 2  # 两次导入都重新下载并校验了 hash


async def test_run_job_creates_new_version_when_hash_changes():
    """同一 symbol/month 但官方修订归档（hash 变化）→ 新版本 2，旧版本与旧文件保留。"""
    service, store, _, archive_client = _service()
    first = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)
    await service.run_job(first["job_id"])
    v1 = list(store.datasets.values())[0]
    v1_archive_path = v1["archive_path"]
    assert v1["version"] == 1
    assert os.path.exists(v1_archive_path)

    # 官方修订归档 → hash 变化
    archive_client.set_payload(b"revised-archive-bytes")
    second = await store.create_job(
        job_id="hjob_revised",
        source="binance_vision",
        market="spot",
        symbol="BTCUSDT",
        interval="1m",
        month=FULL_MONTH,
    )
    result = await service.run_job(second["job_id"])

    assert result["status"] == "SUCCEEDED"
    assert len(store.datasets) == 2
    versions = sorted(int(item["version"]) for item in store.datasets.values())
    assert versions == [1, 2]

    v1_after = store.datasets[v1["dataset_id"]]
    v2 = store.datasets[result["dataset_id"]]
    assert v1_after["version"] == 1
    assert v1_after["status"] == "PUBLISHED"
    assert v1_after["feature_version"] != v2["feature_version"]
    # 旧版本文件仍在（不被覆盖 / 不被删除）
    assert os.path.exists(v1_archive_path)
    assert v2["archive_path"] != v1_archive_path
    assert os.path.exists(v2["archive_path"])


async def test_run_job_fails_with_archive_not_found_error_code():
    archive_client = FakeArchiveClient(
        download_error=ArchiveNotFoundError("missing"),
    )
    service, _, _, _ = _service(archive_client=archive_client)
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)

    result = await service.run_job(job["job_id"])

    assert result["status"] == "FAILED"
    assert result["error_code"] == ERROR_ARCHIVE_NOT_FOUND


async def test_run_job_fails_with_network_error_code():
    archive_client = FakeArchiveClient(download_error=ArchiveDownloadError("timeout"))
    service, _, _, _ = _service(archive_client=archive_client)
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)

    result = await service.run_job(job["job_id"])

    assert result["status"] == "FAILED"
    assert result["error_code"] == ERROR_NETWORK


async def test_run_job_fails_with_checksum_error_code():
    archive_client = FakeArchiveClient(
        download_error=ArchiveChecksumMismatch("bad hash"),
    )
    service, _, _, _ = _service(archive_client=archive_client)
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)

    result = await service.run_job(job["job_id"])

    assert result["status"] == "FAILED"
    assert result["error_code"] == ERROR_CHECKSUM_MISMATCH


async def test_run_job_fails_with_quality_error_code_on_gap():
    service, _, _, _ = _service(bars=_full_month_bars(skip=10))
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)

    result = await service.run_job(job["job_id"])

    assert result["status"] == "FAILED"
    assert result["error_code"] == ERROR_QUALITY


async def test_run_job_fails_with_persistence_error_code():
    feature_store = FakeFeatureStore(fail=True)
    service, store, _, _ = _service(feature_store=feature_store)
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)

    result = await service.run_job(job["job_id"])

    assert result["status"] == "FAILED"
    assert result["error_code"] == ERROR_PERSISTENCE
    assert all(item["backtest_eligible"] is False for item in store.datasets.values())


async def test_retry_after_persistence_failure_reuses_same_dataset_version():
    feature_store = FakeFeatureStore(fail=True)
    service, store, _, _ = _service(feature_store=feature_store)
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)
    await service.run_job(job["job_id"])
    assert len(store.datasets) == 1
    failed_dataset = list(store.datasets.values())[0]
    assert failed_dataset["status"] == "FAILED"

    # 修复存储后重试：沿用同一个 dataset_id / version，不新建版本
    healthy_store = FakeFeatureStore()
    service._feature_store = healthy_store
    store._feature_store = healthy_store

    retried = await service.retry_job(job["job_id"])
    result = await service.run_job(retried["job_id"])

    assert result["status"] == "SUCCEEDED"
    assert len(store.datasets) == 1
    published = list(store.datasets.values())[0]
    assert published["dataset_id"] == failed_dataset["dataset_id"]
    assert published["version"] == failed_dataset["version"]
    assert published["status"] == "PUBLISHED"


async def test_retry_job_requeues_failed_job():
    archive_client = FakeArchiveClient(download_error=ArchiveDownloadError("timeout"))
    service, _, _, _ = _service(archive_client=archive_client)
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)
    await service.run_job(job["job_id"])

    retried = await service.retry_job(job["job_id"])

    assert retried["status"] == "QUEUED"
    assert retried["attempt"] == 2
    assert retried["error_code"] is None


async def test_retry_job_rejects_non_failed_job():
    service, _, _, _ = _service()
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)
    with pytest.raises(HistoryIngestionError):
        await service.retry_job(job["job_id"])


async def test_retry_job_rejects_unknown_job():
    service, _, _, _ = _service()
    with pytest.raises(KeyError):
        await service.retry_job("hjob_missing")


async def test_recover_interrupted_jobs_marks_running_jobs_failed():
    service, store, _, _ = _service()
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)
    await store.claim_job(job["job_id"])

    count = await service.recover_interrupted_jobs()

    assert count == 1
    recovered = await store.get_job(job["job_id"])
    assert recovered["status"] == "FAILED"
    assert recovered["error_code"] == "SERVICE_INTERRUPTED"


async def test_process_next_queued_job_runs_pending_job():
    service, _, _, _ = _service()
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)

    processed = await service.process_next_queued_job()

    assert processed is not None
    assert processed["job_id"] == job["job_id"]
    assert processed["status"] == "SUCCEEDED"


async def test_process_next_queued_job_returns_none_when_idle():
    service, _, _, _ = _service()
    assert await service.process_next_queued_job() is None


async def test_list_datasets_reports_published_dataset():
    service, _, _, _ = _service()
    job = await service.submit_job(symbol="BTCUSDT", month=FULL_MONTH)
    await service.run_job(job["job_id"])

    datasets = await service.list_datasets(symbol="BTCUSDT", month=FULL_MONTH)
    eligible = await service.list_datasets(symbol="BTCUSDT", month=FULL_MONTH, only_eligible=True)

    assert len(datasets) == 1
    assert len(eligible) == 1
    assert eligible[0]["backtest_eligible"] is True
