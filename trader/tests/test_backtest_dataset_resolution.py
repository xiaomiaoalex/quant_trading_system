"""
Backtest Dataset Resolution Tests
==================================
覆盖历史数据集的准入校验、范围推导与显式冲突拒绝。

同时验证「漏 dataset_id 绕过门禁」被拒绝，以及旧入口保持兼容。
"""

from __future__ import annotations

import pytest

from trader.api.models.schemas import BacktestDatasetSpec, BacktestRequest
from trader.core.domain.services.ohlcv_archive import last_bar_open_ms, month_bounds_ms
from trader.services.backtesting.backtest_dataset_resolution import (
    BacktestRequestConflictError,
    requires_dataset_id,
    resolve_backtest_request,
    resolve_dataset_spec,
)
from trader.services.backtesting.history_dataset_resolver import (
    DatasetNotEligibleError,
    DatasetNotFoundError,
    DatasetRangeError,
    HistoryDatasetResolver,
)
from trader.tests.fakes.fake_history_store import FakeHistoryDatasetStore

YEAR = 2026
MONTH = 9
MONTH_STR = "2026-09"
FEATURE_VERSION = "binance_spot_1m_BTCUSDT_2026-09_aaaaaaaaaaaa"


def _store_with_dataset(**overrides) -> FakeHistoryDatasetStore:
    store = FakeHistoryDatasetStore()
    dataset = {
        "dataset_id": "hds_test",
        "source": "binance_vision",
        "market": "spot",
        "symbol": "BTCUSDT",
        "interval": "1m",
        "month": MONTH_STR,
        "version": 1,
        "feature_version": FEATURE_VERSION,
        "archive_path": "/tmp/a.zip",
        "archive_sha256": "a" * 64,
        "official_sha256": "a" * 64,
        "source_url": "https://example/a.zip",
        "checksum_url": "https://example/a.zip.CHECKSUM",
        "row_count": 43200,
        "expected_points": 43200,
        "gap_count": 0,
        "first_ts_ms": month_bounds_ms(YEAR, MONTH)[0],
        "last_ts_ms": last_bar_open_ms(YEAR, MONTH),
        "quality": {"complete": True},
        "status": "PUBLISHED",
        "persistence_ok": True,
        "backtest_eligible": True,
        "job_id": "hjob_test",
    }
    dataset.update(overrides)
    store.datasets["hds_test"] = dataset
    return store


def _request(**overrides) -> BacktestRequest:
    payload = {
        "strategy_id": "s1",
        "version": 1,
        "venue": "BINANCE",
        "requested_by": "pytest",
        "symbols": ["BTCUSDT"],
        "start_ts_ms": month_bounds_ms(YEAR, MONTH)[0],
        "end_ts_ms": last_bar_open_ms(YEAR, MONTH),
    }
    payload.update(overrides)
    return BacktestRequest(**payload)


async def test_resolve_dataset_returns_full_month_range_by_default():
    store = _store_with_dataset()
    resolver = HistoryDatasetResolver(store=store)

    resolved = await resolver.resolve("hds_test")

    assert resolved.start_ts_ms == month_bounds_ms(YEAR, MONTH)[0]
    assert resolved.end_ts_ms == last_bar_open_ms(YEAR, MONTH)
    assert resolved.range_expected_points == 43200
    assert resolved.symbol == "BTCUSDT"
    assert resolved.interval == "1m"


async def test_resolve_dataset_rejects_unknown_dataset():
    resolver = HistoryDatasetResolver(store=_store_with_dataset())
    with pytest.raises(DatasetNotFoundError):
        await resolver.resolve("hds_missing")


async def test_resolve_dataset_rejects_unpublished_dataset():
    store = _store_with_dataset(status="PENDING", persistence_ok=False, backtest_eligible=False)
    resolver = HistoryDatasetResolver(store=store)
    with pytest.raises(DatasetNotEligibleError):
        await resolver.resolve("hds_test")


async def test_resolve_dataset_rejects_incomplete_dataset():
    store = _store_with_dataset(row_count=43199)
    resolver = HistoryDatasetResolver(store=store)
    with pytest.raises(DatasetNotEligibleError):
        await resolver.resolve("hds_test")


async def test_resolve_dataset_rejects_dataset_with_gaps():
    store = _store_with_dataset(gap_count=3)
    resolver = HistoryDatasetResolver(store=store)
    with pytest.raises(DatasetNotEligibleError):
        await resolver.resolve("hds_test")


async def test_resolve_dataset_rejects_wrong_market():
    store = _store_with_dataset(market="futures")
    resolver = HistoryDatasetResolver(store=store)
    with pytest.raises(DatasetNotEligibleError):
        await resolver.resolve("hds_test")


async def test_resolve_dataset_rejects_wrong_interval():
    store = _store_with_dataset(interval="5m")
    resolver = HistoryDatasetResolver(store=store)
    with pytest.raises(DatasetNotEligibleError):
        await resolver.resolve("hds_test")


async def test_resolve_dataset_rejects_out_of_range_start():
    store = _store_with_dataset()
    resolver = HistoryDatasetResolver(store=store)
    with pytest.raises(DatasetRangeError):
        await resolver.resolve("hds_test", start_ts_ms=month_bounds_ms(YEAR, MONTH)[0] - 60_000)


async def test_resolve_dataset_rejects_unaligned_range():
    store = _store_with_dataset()
    resolver = HistoryDatasetResolver(store=store)
    with pytest.raises(DatasetRangeError):
        await resolver.resolve("hds_test", start_ts_ms=month_bounds_ms(YEAR, MONTH)[0] + 30_000)


async def test_resolve_dataset_accepts_aligned_sub_range():
    store = _store_with_dataset()
    resolver = HistoryDatasetResolver(store=store)
    start = month_bounds_ms(YEAR, MONTH)[0]
    resolved = await resolver.resolve("hds_test", start_ts_ms=start, end_ts_ms=start + 3_600_000)
    assert resolved.range_expected_points == 61


async def test_resolve_backtest_request_derives_dataset_fields():
    store = _store_with_dataset()
    request = _request(dataset_id="hds_test", start_ts_ms=None, end_ts_ms=None)

    resolved = await resolve_backtest_request(request, resolver=HistoryDatasetResolver(store=store))

    assert resolved.engine == "vectorbt"
    assert resolved.data_mode == "real_feature_store"
    assert resolved.symbols == ["BTCUSDT"]
    assert resolved.feature_version == FEATURE_VERSION
    assert resolved.interval == "1m"
    assert resolved.params["interval"] == "1m"
    assert resolved.start_ts_ms == month_bounds_ms(YEAR, MONTH)[0]
    assert resolved.end_ts_ms == last_bar_open_ms(YEAR, MONTH)


async def test_resolve_backtest_request_rejects_conflicting_symbol():
    store = _store_with_dataset()
    request = _request(dataset_id="hds_test", symbols=["ETHUSDT"])

    with pytest.raises(BacktestRequestConflictError):
        await resolve_backtest_request(request, resolver=HistoryDatasetResolver(store=store))


async def test_resolve_backtest_request_rejects_conflicting_feature_version():
    store = _store_with_dataset()
    request = _request(dataset_id="hds_test", feature_version="dev_smoke")

    with pytest.raises(BacktestRequestConflictError):
        await resolve_backtest_request(request, resolver=HistoryDatasetResolver(store=store))


async def test_resolve_backtest_request_rejects_conflicting_data_mode():
    store = _store_with_dataset()
    request = _request(dataset_id="hds_test", data_mode="dev_smoke")

    with pytest.raises(BacktestRequestConflictError):
        await resolve_backtest_request(request, resolver=HistoryDatasetResolver(store=store))


async def test_resolve_backtest_request_rejects_conflicting_engine():
    store = _store_with_dataset()
    request = _request(dataset_id="hds_test", engine="strategy_runner")

    with pytest.raises(BacktestRequestConflictError):
        await resolve_backtest_request(request, resolver=HistoryDatasetResolver(store=store))


async def test_resolve_backtest_request_rejects_conflicting_interval():
    store = _store_with_dataset()
    request = _request(dataset_id="hds_test", interval="5m")

    with pytest.raises(BacktestRequestConflictError):
        await resolve_backtest_request(request, resolver=HistoryDatasetResolver(store=store))


async def test_resolve_backtest_request_rejects_conflicting_params_interval():
    store = _store_with_dataset()
    request = _request(dataset_id="hds_test", params={"interval": "1h"})

    with pytest.raises(BacktestRequestConflictError):
        await resolve_backtest_request(request, resolver=HistoryDatasetResolver(store=store))


async def test_resolve_backtest_request_rejects_dataset_gate_bypass():
    request = _request(feature_version=FEATURE_VERSION)

    with pytest.raises(BacktestRequestConflictError):
        await resolve_backtest_request(request)


async def test_resolve_backtest_request_keeps_legacy_request_unchanged():
    request = _request()
    resolved = await resolve_backtest_request(request)
    assert resolved.engine == "strategy_runner"
    assert resolved.data_mode == "dev_smoke"
    assert resolved.feature_version == "dev_smoke"


async def test_resolve_backtest_request_legacy_requires_range():
    request = _request(start_ts_ms=None, end_ts_ms=None)
    with pytest.raises(ValueError):
        await resolve_backtest_request(request)


async def test_resolve_dataset_spec_derives_fields():
    store = _store_with_dataset()
    spec = BacktestDatasetSpec(dataset_id="hds_test")

    resolved = await resolve_dataset_spec(spec, resolver=HistoryDatasetResolver(store=store))

    assert resolved.symbols == ["BTCUSDT"]
    assert resolved.feature_version == FEATURE_VERSION
    assert resolved.data_mode == "real_feature_store"
    assert resolved.interval == "1m"
    assert resolved.start_ts_ms == month_bounds_ms(YEAR, MONTH)[0]
    assert resolved.end_ts_ms == last_bar_open_ms(YEAR, MONTH)


async def test_resolve_dataset_spec_rejects_gate_bypass():
    spec = BacktestDatasetSpec(feature_version=FEATURE_VERSION)
    with pytest.raises(BacktestRequestConflictError):
        await resolve_dataset_spec(spec)


async def test_requires_dataset_id_detects_registered_versions():
    assert requires_dataset_id(FEATURE_VERSION) is True
    assert requires_dataset_id("dev_smoke") is False
    assert requires_dataset_id(None) is False
