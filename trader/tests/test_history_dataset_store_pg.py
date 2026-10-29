"""
History Dataset PostgreSQL Integration Tests
=============================================
在隔离测试数据库上验证：
- 历史任务 / 数据集在「重启」（新建 store 实例）后仍可读
- 活动任务唯一约束生效（重复提交复用同一任务）
- 任务原子领取
- FeatureStore 严格模式：PG 不可用时抛错，绝不静默回退内存
- 数据集回测资格（发布 + 持久化）跨实例一致

PostgreSQL 不可用时整组跳过。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Optional
from urllib.parse import urlparse, urlunparse

import pytest

from trader.adapters.persistence.feature_store import (
    FeatureStore,
    FeatureStoreStrictError,
    FeatureVersionConflictError,
)
from trader.adapters.persistence.history_dataset_store import HistoryDatasetStore
from trader.adapters.persistence.postgres import ASYNCPG_AVAILABLE, is_postgres_available
from trader.core.domain.services.ohlcv_archive import (
    build_feature_version,
    last_bar_open_ms,
    month_bounds_ms,
)
from trader.services.backtesting.history_dataset_resolver import (
    DatasetNotEligibleError,
    HistoryDatasetResolver,
)

TEST_DB_NAME = "qts_history_test"
# 每次运行使用唯一标识，避免隔离库中残留数据导致主键冲突
RUN_ID = uuid.uuid4().hex[:8]
# 隔离库中 month 参与唯一约束，使用每次运行唯一的占位月份避免残留冲突
MONTH_LABEL = RUN_ID[:7]
YEAR = 2026
MONTH = 9
MONTH_STR = "2026-09"

skip_if_no_asyncpg = pytest.mark.skipif(
    not ASYNCPG_AVAILABLE, reason="asyncpg package not installed"
)
skip_if_no_postgres = pytest.mark.skipif(
    not is_postgres_available(),
    reason="PostgreSQL not available. Set POSTGRES_CONNECTION_STRING or POSTGRES_HOST/POSTGRES_DB/POSTGRES_USER",
)


def _base_dsn() -> Optional[str]:
    """从环境变量构造基础 DSN。"""
    connection_string = os.getenv("POSTGRES_CONNECTION_STRING")
    if connection_string:
        return connection_string
    host = os.getenv("POSTGRES_HOST")
    database = os.getenv("POSTGRES_DB")
    user = os.getenv("POSTGRES_USER")
    if not (host and database and user):
        return None
    password = os.getenv("POSTGRES_PASSWORD", "")
    port = os.getenv("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def _with_database(dsn: str, database: str) -> str:
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{database}"))


@pytest.fixture(scope="module")
def isolated_dsn():
    """创建并返回隔离测试数据库的 DSN。"""
    base = _base_dsn()
    if base is None:
        pytest.skip("PostgreSQL not configured")
    admin_dsn = _with_database(base, "postgres")
    target_dsn = _with_database(base, TEST_DB_NAME)

    async def _ensure_database() -> None:
        import asyncpg

        conn = await asyncpg.connect(admin_dsn)
        try:
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB_NAME
            )
            if not exists:
                await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
        finally:
            await conn.close()

    try:
        asyncio.run(_ensure_database())
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Cannot prepare isolated test database: {exc}")
    return target_dsn


def _make_store(dsn: str) -> HistoryDatasetStore:
    from trader.adapters.persistence.postgres import PostgreSQLStorage

    store = HistoryDatasetStore(storage=PostgreSQLStorage(connection_string=dsn))
    return store


async def _strict_store(dsn: str) -> FeatureStore:
    """构造一个直连隔离库的严格模式 FeatureStore。"""
    from trader.adapters.persistence.postgres import PostgreSQLStorage

    store = FeatureStore(storage=None, strict=True)
    store._postgres_storage = PostgreSQLStorage(connection_string=dsn)
    await store._postgres_storage.connect()
    store._use_postgres = True
    return store


@skip_if_no_asyncpg
@skip_if_no_postgres
async def test_history_job_and_dataset_survive_store_restart(isolated_dsn):
    store = _make_store(isolated_dsn)
    await store.connect()
    try:
        job = await store.create_job(
            job_id=f"hjob_pg_restart_{RUN_ID}",
            source="binance_vision",
            market="spot",
            symbol="BTCUSDT",
            interval="1m",
            month=MONTH_LABEL,
        )
        assert job["status"] == "QUEUED"
        assert await store.claim_job(job["job_id"]) is True

        dataset = await store.insert_dataset(
            dataset_id=f"hds_pg_restart_{RUN_ID}",
            source="binance_vision",
            market="spot",
            symbol="BTCUSDT",
            interval="1m",
            month=MONTH_LABEL,
            version=1,
            feature_version="binance_spot_1m_BTCUSDT_2026-09_aaaaaaaaaaaa",
            archive_path="/tmp/a.zip",
            archive_sha256="a" * 64,
            official_sha256="a" * 64,
            source_url="https://example/a.zip",
            checksum_url="https://example/a.zip.CHECKSUM",
            row_count=43200,
            expected_points=43200,
            gap_count=0,
            first_ts_ms=month_bounds_ms(YEAR, MONTH)[0],
            last_ts_ms=last_bar_open_ms(YEAR, MONTH),
            quality={"complete": True},
            status="PENDING",
            persistence_ok=False,
            backtest_eligible=False,
            job_id=job["job_id"],
        )
        assert dataset["status"] == "PENDING"

        published = await store.publish_dataset_with_job(
            job_id=job["job_id"],
            dataset_id=f"hds_pg_restart_{RUN_ID}",
            feature_version="binance_spot_1m_BTCUSDT_2026-09_aaaaaaaaaaaa",
            row_count=43200,
            expected_points=43200,
            gap_count=0,
            first_ts_ms=month_bounds_ms(YEAR, MONTH)[0],
            last_ts_ms=last_bar_open_ms(YEAR, MONTH),
            quality={"complete": True},
        )
        assert published["job"]["status"] == "SUCCEEDED"
        assert published["dataset"]["status"] == "PUBLISHED"
    finally:
        await store.disconnect()

    # 模拟重启：全新 store 实例
    restarted = _make_store(isolated_dsn)
    await restarted.connect()
    try:
        job_after = await restarted.get_job(f"hjob_pg_restart_{RUN_ID}")
        assert job_after is not None
        assert job_after["status"] == "SUCCEEDED"
        assert job_after["dataset_id"] == f"hds_pg_restart_{RUN_ID}"

        dataset_after = await restarted.get_dataset(f"hds_pg_restart_{RUN_ID}")
        assert dataset_after is not None
        assert dataset_after["status"] == "PUBLISHED"
        assert dataset_after["persistence_ok"] is True
        assert dataset_after["backtest_eligible"] is True
    finally:
        await restarted.disconnect()


@skip_if_no_asyncpg
@skip_if_no_postgres
async def test_duplicate_active_job_is_reused(isolated_dsn):
    store = _make_store(isolated_dsn)
    await store.connect()
    try:
        first = await store.create_job(
            job_id=f"hjob_dup_1_{RUN_ID}",
            source="binance_vision",
            market="spot",
            symbol="ETHUSDT",
            interval="1m",
            month=MONTH_LABEL,
        )
        second = await store.create_job(
            job_id=f"hjob_dup_2_{RUN_ID}",
            source="binance_vision",
            market="spot",
            symbol="ETHUSDT",
            interval="1m",
            month=MONTH_LABEL,
        )
        assert second["job_id"] == first["job_id"]
    finally:
        await store.disconnect()


@skip_if_no_asyncpg
@skip_if_no_postgres
async def test_job_claim_is_atomic(isolated_dsn):
    store = _make_store(isolated_dsn)
    await store.connect()
    try:
        job = await store.create_job(
            job_id=f"hjob_claim_{RUN_ID}",
            source="binance_vision",
            market="spot",
            symbol="BTCUSDT",
            interval="1m",
            month=MONTH_LABEL,
        )
        assert await store.claim_job(job["job_id"]) is True
        assert await store.claim_job(job["job_id"]) is False
    finally:
        await store.disconnect()


@skip_if_no_asyncpg
@skip_if_no_postgres
async def test_fail_running_jobs_marks_interrupted_jobs(isolated_dsn):
    store = _make_store(isolated_dsn)
    await store.connect()
    try:
        job = await store.create_job(
            job_id=f"hjob_interrupted_{RUN_ID}",
            source="binance_vision",
            market="spot",
            symbol="BTCUSDT",
            interval="1m",
            month=MONTH_LABEL,
        )
        await store.claim_job(job["job_id"])
        affected = await store.fail_running_jobs("service restarted")
        assert affected >= 1
        recovered = await store.get_job(job["job_id"])
        assert recovered["status"] == "FAILED"
        assert recovered["error_code"] == "SERVICE_INTERRUPTED"
    finally:
        await store.disconnect()


@skip_if_no_asyncpg
@skip_if_no_postgres
async def test_feature_store_strict_mode_persists_rows_across_instances(isolated_dsn):
    from trader.adapters.persistence.postgres import PostgreSQLStorage

    feature_version = build_feature_version("BTCUSDT", MONTH_STR, "c" * 64)
    start_ms, _ = month_bounds_ms(YEAR, MONTH)

    store = FeatureStore(storage=None, strict=True)
    store._postgres_storage = PostgreSQLStorage(connection_string=isolated_dsn)
    await store._postgres_storage.connect()
    store._use_postgres = True

    for index in range(3):
        await store.write_feature(
            symbol="BTCUSDT",
            feature_name="ohlcv",
            version=feature_version,
            ts_ms=start_ms + index * 60_000,
            value={"open": "1.0", "high": "2.0", "low": "0.5", "close": "1.5", "volume": "3.0"},
            meta={"interval": "1m", "source": "binance_vision"},
        )

    restarted = FeatureStore(storage=None, strict=True)
    restarted._postgres_storage = PostgreSQLStorage(connection_string=isolated_dsn)
    await restarted._postgres_storage.connect()
    restarted._use_postgres = True

    points = await restarted.read_feature_range(
        symbol="BTCUSDT",
        feature_name="ohlcv",
        start_time=start_ms,
        end_time=start_ms + 10 * 60_000,
        version=feature_version,
    )
    assert len(points) == 3
    assert points[0].ts_ms == start_ms

    history_store = _make_store(isolated_dsn)
    await history_store.connect()
    try:
        counted = await history_store.count_feature_rows(feature_version)
        assert counted["total_points"] == 3
        assert counted["first_ts_ms"] == start_ms
    finally:
        await history_store.disconnect()

    await store._postgres_storage.disconnect()
    await restarted._postgres_storage.disconnect()


@skip_if_no_asyncpg
@skip_if_no_postgres
async def test_feature_store_bulk_write_is_idempotent_and_detects_conflicts(isolated_dsn):
    from trader.adapters.persistence.postgres import PostgreSQLStorage

    feature_version = build_feature_version("ETHUSDT", MONTH_STR, RUN_ID.ljust(64, "0")[:64])
    start_ms, _ = month_bounds_ms(YEAR, MONTH)
    rows = [
        (start_ms + index * 60_000, {"open": "1.0", "high": "2.0", "low": "0.5", "close": "1.5"})
        for index in range(3)
    ]

    store = FeatureStore(storage=None, strict=True)
    store._postgres_storage = PostgreSQLStorage(connection_string=isolated_dsn)
    await store._postgres_storage.connect()
    store._use_postgres = True

    created, duplicates = await store.write_features_bulk(
        "ETHUSDT", "ohlcv", feature_version, rows, meta={"interval": "1m"}
    )
    assert created == 3
    assert duplicates == 0

    created_again, duplicates_again = await store.write_features_bulk(
        "ETHUSDT", "ohlcv", feature_version, rows, meta={"interval": "1m"}
    )
    assert created_again == 0
    assert duplicates_again == 3

    conflicting = [(rows[0][0], {"open": "9.0", "high": "2.0", "low": "0.5", "close": "1.5"})]
    with pytest.raises(FeatureVersionConflictError):
        await store.write_features_bulk(
            "ETHUSDT", "ohlcv", feature_version, conflicting, meta={"interval": "1m"}
        )

    points = await store.read_feature_range(
        symbol="ETHUSDT",
        feature_name="ohlcv",
        start_time=start_ms,
        end_time=start_ms + 10 * 60_000,
        version=feature_version,
    )
    assert len(points) == 3

    await store._postgres_storage.disconnect()


@skip_if_no_asyncpg
@skip_if_no_postgres
async def test_feature_store_bulk_write_concurrent_same_value_is_idempotent(isolated_dsn):
    """并发写入同值：不得报错，总行数为 1，计数真实。"""
    store_a = await _strict_store(isolated_dsn)
    store_b = await _strict_store(isolated_dsn)
    try:
        feature_version = build_feature_version(
            "BTCUSDT", MONTH_STR, (RUN_ID + "a").ljust(64, "0")[:64]
        )
        start_ms, _ = month_bounds_ms(YEAR, MONTH)
        rows = [(start_ms, {"open": "1.0", "high": "2.0", "low": "0.5", "close": "1.5"})]

        results = await asyncio.gather(
            store_a.write_features_bulk("BTCUSDT", "ohlcv", feature_version, rows),
            store_b.write_features_bulk("BTCUSDT", "ohlcv", feature_version, rows),
        )
        created_counts = sorted(created for created, _ in results)
        duplicate_counts = sorted(duplicates for _, duplicates in results)
        assert created_counts == [0, 1]
        assert duplicate_counts == [0, 1]

        points = await store_a.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ohlcv",
            start_time=start_ms,
            end_time=start_ms + 60_000,
            version=feature_version,
        )
        assert len(points) == 1
    finally:
        await store_a._postgres_storage.disconnect()
        await store_b._postgres_storage.disconnect()


@skip_if_no_asyncpg
@skip_if_no_postgres
async def test_feature_store_bulk_write_concurrent_different_value_raises_conflict(isolated_dsn):
    """并发写入异值：必须有一个失败并抛冲突，且不得静默吞掉写入。"""
    store_a = await _strict_store(isolated_dsn)
    store_b = await _strict_store(isolated_dsn)
    try:
        feature_version = build_feature_version(
            "BTCUSDT", MONTH_STR, (RUN_ID + "b").ljust(64, "0")[:64]
        )
        start_ms, _ = month_bounds_ms(YEAR, MONTH)
        value_a = {"open": "1.0", "high": "2.0", "low": "0.5", "close": "1.5"}
        value_b = {"open": "9.0", "high": "2.0", "low": "0.5", "close": "1.5"}

        outcomes = await asyncio.gather(
            store_a.write_features_bulk("BTCUSDT", "ohlcv", feature_version, [(start_ms, value_a)]),
            store_b.write_features_bulk("BTCUSDT", "ohlcv", feature_version, [(start_ms, value_b)]),
            return_exceptions=True,
        )

        successes = [item for item in outcomes if not isinstance(item, Exception)]
        failures = [item for item in outcomes if isinstance(item, Exception)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], FeatureVersionConflictError)

        points = await store_a.read_feature_range(
            symbol="BTCUSDT",
            feature_name="ohlcv",
            start_time=start_ms,
            end_time=start_ms + 60_000,
            version=feature_version,
        )
        assert len(points) == 1
    finally:
        await store_a._postgres_storage.disconnect()
        await store_b._postgres_storage.disconnect()


@skip_if_no_asyncpg
@skip_if_no_postgres
async def test_feature_store_bulk_write_rolls_back_entire_batch_on_conflict(isolated_dsn):
    """批次中任一行冲突 → 整批回滚（无部分写入）。"""
    store = await _strict_store(isolated_dsn)
    try:
        feature_version = build_feature_version(
            "ETHUSDT", MONTH_STR, (RUN_ID + "c").ljust(64, "0")[:64]
        )
        start_ms, _ = month_bounds_ms(YEAR, MONTH)
        good = {"open": "1.0", "high": "2.0", "low": "0.5", "close": "1.5"}
        bad = {"open": "9.0", "high": "2.0", "low": "0.5", "close": "1.5"}

        await store.write_features_bulk("ETHUSDT", "ohlcv", feature_version, [(start_ms, good)])

        conflicting_batch = [
            (start_ms + 60_000, good),  # 新行
            (start_ms, bad),  # 与已有行冲突
        ]
        with pytest.raises(FeatureVersionConflictError):
            await store.write_features_bulk("ETHUSDT", "ohlcv", feature_version, conflicting_batch)

        points = await store.read_feature_range(
            symbol="ETHUSDT",
            feature_name="ohlcv",
            start_time=start_ms,
            end_time=start_ms + 5 * 60_000,
            version=feature_version,
        )
        # 只有最初那一行；批次中的新行被回滚
        assert len(points) == 1
        assert points[0].ts_ms == start_ms
    finally:
        await store._postgres_storage.disconnect()


@skip_if_no_asyncpg
@skip_if_no_postgres
async def test_feature_store_bulk_write_handles_intra_batch_duplicates(isolated_dsn):
    """批内重复：同 ts 同值折叠为 duplicate；同 ts 异值直接抛冲突。"""
    store = await _strict_store(isolated_dsn)
    try:
        feature_version = build_feature_version(
            "ETHUSDT", MONTH_STR, (RUN_ID + "d").ljust(64, "0")[:64]
        )
        start_ms, _ = month_bounds_ms(YEAR, MONTH)
        value = {"open": "1.0", "high": "2.0", "low": "0.5", "close": "1.5"}
        other = {"open": "9.0", "high": "2.0", "low": "0.5", "close": "1.5"}

        created, duplicates = await store.write_features_bulk(
            "ETHUSDT",
            "ohlcv",
            feature_version,
            [(start_ms, value), (start_ms, value)],
        )
        assert created == 1
        assert duplicates == 1

        with pytest.raises(FeatureVersionConflictError):
            await store.write_features_bulk(
                "ETHUSDT",
                "ohlcv",
                feature_version,
                [(start_ms + 60_000, value), (start_ms + 60_000, other)],
            )

        points = await store.read_feature_range(
            symbol="ETHUSDT",
            feature_name="ohlcv",
            start_time=start_ms,
            end_time=start_ms + 5 * 60_000,
            version=feature_version,
        )
        assert len(points) == 1
    finally:
        await store._postgres_storage.disconnect()


@skip_if_no_asyncpg
@skip_if_no_postgres
async def test_feature_store_strict_mode_fails_closed_without_postgres(monkeypatch):
    from trader.adapters.persistence import feature_store as feature_store_module

    async def _unavailable(timeout: float = 2.0):
        return False, "PostgreSQL not available"

    monkeypatch.setattr(feature_store_module, "check_postgres_connection", _unavailable)

    store = FeatureStore(strict=True)
    with pytest.raises(FeatureStoreStrictError):
        await store.write_feature(
            symbol="BTCUSDT",
            feature_name="ohlcv",
            version="binance_spot_1m_BTCUSDT_2026-09_aaaaaaaaaaaa",
            ts_ms=1_700_000_000_000,
            value={"open": "1.0", "high": "2.0", "low": "0.5", "close": "1.5", "volume": "3.0"},
        )

    memory_keys = store._memory_storage.feature_values_by_key
    assert not any(key.startswith("BTCUSDT:ohlcv:") for key in memory_keys)

    # 批量写入同样必须 fail-closed，不得回退内存
    with pytest.raises(FeatureStoreStrictError):
        await store.write_features_bulk(
            "BTCUSDT",
            "ohlcv",
            "binance_spot_1m_BTCUSDT_2026-09_bbbbbbbbbbbb",
            [(1_700_000_000_000, {"open": "1.0", "high": "2.0", "low": "0.5", "close": "1.5"})],
        )
    assert not any(key.startswith("BTCUSDT:ohlcv:") for key in memory_keys)


@skip_if_no_asyncpg
@skip_if_no_postgres
async def test_dataset_requires_published_and_persisted_state(isolated_dsn):
    store = _make_store(isolated_dsn)
    await store.connect()
    try:
        await store.insert_dataset(
            dataset_id=f"hds_pending_only_{RUN_ID}",
            source="binance_vision",
            market="spot",
            symbol="BTCUSDT",
            interval="1m",
            month=MONTH_LABEL,
            version=2,
            feature_version="binance_spot_1m_BTCUSDT_2026-09_bbbbbbbbbbbb",
            archive_path="/tmp/b.zip",
            archive_sha256="b" * 64,
            source_url="https://example/b.zip",
            checksum_url="https://example/b.zip.CHECKSUM",
            row_count=43200,
            expected_points=43200,
            gap_count=0,
            status="PENDING",
            persistence_ok=False,
            backtest_eligible=False,
        )

        resolver = HistoryDatasetResolver(store=store)
        with pytest.raises(DatasetNotEligibleError):
            await resolver.resolve(f"hds_pending_only_{RUN_ID}")
    finally:
        await store.disconnect()
