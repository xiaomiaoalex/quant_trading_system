"""Tests for the Research provenance backfill command."""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from typing import List

import pytest

from trader.core.domain.services.ohlcv_archive import (
    KlineBar,
    expected_points_for_month,
    month_bounds_ms,
)
from trader.tests.fakes.fake_history_store import (
    FakeHistoryDatasetStore,
    FakeResearchHistoryBridgeStore,
)

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "backfill_research_dataset_links.py"
)

YEAR = 2026
MONTH = 9
MONTH_STR = "2026-09"
SHA = "b" * 64


def _load_module():
    spec = importlib.util.spec_from_file_location("backfill_research_dataset_links", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _full_month_bars() -> List[KlineBar]:
    start_ms, _ = month_bounds_ms(YEAR, MONTH)
    total = expected_points_for_month(YEAR, MONTH)
    return [
        KlineBar(
            open_time_ms=start_ms + index * 60_000,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=Decimal("12.5"),
        )
        for index in range(total)
    ]


def _published_dataset(tmp_path: Path, *, archived_sha: str = SHA):
    archive = tmp_path / "BTCUSDT-1m-2026-09-v1.zip"
    archive.write_bytes(b"archive-bytes")
    store = FakeHistoryDatasetStore()
    start_ms, end_ms = month_bounds_ms(YEAR, MONTH)
    dataset = {
        "dataset_id": "hds_backfill",
        "source": "binance_vision",
        "market": "spot",
        "symbol": "BTCUSDT",
        "interval": "1m",
        "month": MONTH_STR,
        "version": 1,
        "feature_version": "binance_spot_1m_BTCUSDT_2026-09_bbbbbbbbbbbb",
        "archive_path": str(archive),
        "archive_sha256": archived_sha,
        "official_sha256": archived_sha,
        "source_url": "https://example.invalid/BTCUSDT-1m-2026-09.zip",
        "checksum_url": "https://example.invalid/BTCUSDT-1m-2026-09.zip.CHECKSUM",
        "row_count": 43_200,
        "expected_points": 43_200,
        "gap_count": 0,
        "first_ts_ms": start_ms,
        "last_ts_ms": end_ms - 60_000,
        "quality": {"complete": True},
        "status": "PUBLISHED",
        "persistence_ok": True,
        "backtest_eligible": True,
        "research_dataset_id": None,
        "job_id": None,
    }
    store.datasets[dataset["dataset_id"]] = dataset
    return store, dataset


@pytest.fixture
def backfill_module(monkeypatch):
    module = _load_module()
    bars = _full_month_bars()
    monkeypatch.setattr(module, "sha256_file", lambda path: SHA)
    monkeypatch.setattr(module, "read_klines_from_archive", lambda path, ref: list(bars))
    return module


@pytest.mark.asyncio
async def test_dry_run_plans_without_writing(backfill_module, tmp_path):
    store, dataset = _published_dataset(tmp_path)
    bridge = FakeResearchHistoryBridgeStore(store)

    results = await backfill_module.backfill(
        apply=False, store=store, bridge=bridge, client=object()
    )

    assert len(results) == 1
    record = results[0]
    assert record["outcome"] == backfill_module.OUTCOME_PLANNED
    assert record["research_dataset_id"].startswith("ds_")
    assert record["bar_count"] == 43_200
    assert bridge.publish_calls == []
    assert bridge.datasets == {}
    assert store.datasets[dataset["dataset_id"]]["research_dataset_id"] is None


@pytest.mark.asyncio
async def test_apply_backfills_the_link_idempotently(backfill_module, tmp_path):
    store, dataset = _published_dataset(tmp_path)
    bridge = FakeResearchHistoryBridgeStore(store)

    first = await backfill_module.backfill(
        apply=True, store=store, bridge=bridge, client=object()
    )
    second = await backfill_module.backfill(
        apply=True, store=store, bridge=bridge, client=object()
    )

    assert first[0]["outcome"] == backfill_module.OUTCOME_APPLIED
    assert second[0]["outcome"] == backfill_module.OUTCOME_ALREADY_LINKED
    assert len(bridge.publish_calls) == 1
    assert len(bridge.datasets) == 1
    research_dataset_id = first[0]["research_dataset_id"]
    assert store.datasets[dataset["dataset_id"]]["research_dataset_id"] == (
        research_dataset_id
    )
    assert bridge.links[research_dataset_id]["official_eligible"] is True


@pytest.mark.asyncio
async def test_archive_revision_is_reported_as_conflict(
    backfill_module, tmp_path, monkeypatch
):
    store, dataset = _published_dataset(tmp_path)
    bridge = FakeResearchHistoryBridgeStore(store)
    monkeypatch.setattr(backfill_module, "sha256_file", lambda path: "f" * 64)

    results = await backfill_module.backfill(
        apply=True, store=store, bridge=bridge, client=object()
    )

    assert results[0]["outcome"] == backfill_module.OUTCOME_CONFLICT
    assert "does not match" in results[0]["reason"]
    assert bridge.publish_calls == []
    assert store.datasets[dataset["dataset_id"]]["research_dataset_id"] is None
