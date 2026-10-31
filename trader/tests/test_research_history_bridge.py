"""Unit tests for the Binance Vision archive -> Research dataset projection."""

from __future__ import annotations

from decimal import Decimal

import pytest

from trader.core.domain.services.ohlcv_archive import KlineBar, month_bounds_ms, parse_month
from trader.trading.datasets import digest
from trader.trading.research_bridge import (
    CONVERSION_VERSION,
    RESEARCH_INTERVAL_MS,
    ResearchBridgeError,
    build_research_dataset,
    build_research_identity,
    build_research_provenance,
    project_kline_bars,
    split_symbol_assets,
)

SHA = "a" * 64
OTHER_SHA = "b" * 64


def kline(open_time_ms: int, **overrides) -> KlineBar:
    values = {
        "open": Decimal("100.5"),
        "high": Decimal("101.25"),
        "low": Decimal("99.75"),
        "close": Decimal("100.75"),
        "volume": Decimal("12.3456789"),
    }
    values.update(overrides)
    return KlineBar(open_time_ms=open_time_ms, **values)


def series(count: int, start_ms: int = 0) -> list[KlineBar]:
    return [kline(start_ms + index * RESEARCH_INTERVAL_MS) for index in range(count)]


def test_projection_uses_integer_milliseconds_with_close_availability():
    projected = project_kline_bars(series(3, start_ms=1_700_000_000_000))

    assert len(projected) == 3
    for index, bar in enumerate(projected):
        opened = 1_700_000_000_000 + index * RESEARCH_INTERVAL_MS
        assert bar["open_time_ms"] == opened
        assert isinstance(bar["open_time_ms"], int)
        assert bar["close_time_ms"] == opened + RESEARCH_INTERVAL_MS
        assert bar["available_at_ms"] == opened + RESEARCH_INTERVAL_MS


def test_projection_keeps_decimal_precision_without_float():
    bar = kline(
        1_700_000_000_000,
        open=Decimal("0.00000001"),
        high=Decimal("0.00000002"),
        low=Decimal("0.00000001"),
        close=Decimal("0.00000001"),
        volume=Decimal("0.00000003"),
    )

    projected = project_kline_bars([bar])[0]

    assert projected["open"] == "0.00000001"
    assert projected["high"] == "0.00000002"
    assert projected["low"] == "0.00000001"
    assert projected["close"] == "0.00000001"
    assert projected["volume"] == "0.00000003"
    for key in ("open", "high", "low", "close", "volume"):
        assert isinstance(projected[key], str)


def test_projection_rejects_gaps_in_the_minute_series():
    bars = [kline(0), kline(2 * RESEARCH_INTERVAL_MS)]

    with pytest.raises(ResearchBridgeError):
        project_kline_bars(bars)


def test_identity_maps_base_and_quote_assets_explicitly():
    assert split_symbol_assets("BTCUSDT") == ("BTC", "USDT")
    assert split_symbol_assets("ETHUSDT") == ("ETH", "USDT")

    identity = build_research_identity("BTCUSDT", archive_sha256=SHA, month="2026-09")
    assert identity["venue"] == "BINANCE"
    assert identity["market_type"] == "SPOT"
    assert identity["symbol"] == "BTCUSDT"
    assert identity["base_asset"] == "BTC"
    assert identity["quote_asset"] == "USDT"
    assert identity["interval"] == "1m"
    assert identity["source"] == "binance_vision"


def test_identity_version_is_stable_and_derived_from_the_archive_hash():
    first = build_research_identity("BTCUSDT", archive_sha256=SHA, month="2026-09")
    again = build_research_identity("BTCUSDT", archive_sha256=SHA, month="2026-09")
    other = build_research_identity("BTCUSDT", archive_sha256=OTHER_SHA, month="2026-09")

    assert first["version"] == again["version"]
    assert first["version"].startswith(CONVERSION_VERSION)
    assert SHA[:12] in first["version"]
    assert first["version"] != other["version"]


def test_identity_rejects_unsupported_symbol_or_digest():
    with pytest.raises(ResearchBridgeError):
        build_research_identity("SOLUSDT", archive_sha256=SHA, month="2026-09")
    with pytest.raises(ResearchBridgeError):
        build_research_identity("BTCUSDT", archive_sha256="not-a-digest", month="2026-09")
    with pytest.raises(ResearchBridgeError):
        build_research_identity("BTCUSDT", archive_sha256=SHA, month="2026-13")


def test_dataset_content_hash_is_stable_for_identical_input():
    bars = series(4)
    first = build_research_dataset(bars, symbol="BTCUSDT", archive_sha256=SHA, month="2026-09")
    again = build_research_dataset(bars, symbol="BTCUSDT", archive_sha256=SHA, month="2026-09")

    assert first["dataset_id"] == again["dataset_id"]
    assert first["dataset_id"].startswith("ds_")
    assert first["quality"]["bar_count"] == 4


def test_dataset_content_hash_changes_when_the_archive_changes():
    bars = series(4)
    first = build_research_dataset(bars, symbol="BTCUSDT", archive_sha256=SHA, month="2026-09")
    other = build_research_dataset(
        bars, symbol="BTCUSDT", archive_sha256=OTHER_SHA, month="2026-09"
    )

    assert first["dataset_id"] != other["dataset_id"]


def test_dataset_recomputes_to_the_same_content_hash():
    bars = series(4)
    dataset = build_research_dataset(bars, symbol="BTCUSDT", archive_sha256=SHA, month="2026-09")

    recomputed = "ds_" + digest({"identity": dataset["identity"], "bars": dataset["bars"]})
    assert recomputed == dataset["dataset_id"]


def test_month_boundary_bars_map_to_utc_month_edges():
    year, month = parse_month("2026-09")
    start_ms, end_ms = month_bounds_ms(year, month)

    first = project_kline_bars([kline(start_ms)])[0]
    last = project_kline_bars([kline(end_ms - RESEARCH_INTERVAL_MS)])[0]

    assert first["open_time_ms"] == start_ms
    assert first["close_time_ms"] == start_ms + RESEARCH_INTERVAL_MS
    assert last["open_time_ms"] == end_ms - RESEARCH_INTERVAL_MS
    assert last["close_time_ms"] == end_ms


def test_full_month_projects_to_a_gapless_dataset():
    year, month = parse_month("2026-09")
    start_ms, end_ms = month_bounds_ms(year, month)
    expected_points = (end_ms - start_ms) // RESEARCH_INTERVAL_MS

    bars = [kline(start_ms + index * RESEARCH_INTERVAL_MS) for index in range(expected_points)]
    dataset = build_research_dataset(bars, symbol="BTCUSDT", archive_sha256=SHA, month="2026-09")

    assert expected_points == 43_200
    assert dataset["quality"]["bar_count"] == 43_200
    assert dataset["quality"]["coverage_start_ms"] == start_ms
    assert dataset["quality"]["coverage_end_ms"] == end_ms
    assert dataset["bars"][-1]["close_time_ms"] == end_ms


def test_provenance_hash_is_deterministic_and_covers_the_payload():
    payload = dict(
        source_url="https://example.invalid/zip",
        checksum_url="https://example.invalid/CHECKSUM",
        official_sha256=SHA,
        archive_sha256=SHA,
        archive_version=1,
        feature_version="binance_spot_1m_BTCUSDT_2026-09_" + SHA[:12],
        symbol="BTCUSDT",
        interval="1m",
        month="2026-09",
        coverage_start_ms=1,
        coverage_end_ms=2,
        bar_count=1,
        conversion_version=CONVERSION_VERSION,
        quality={"complete": True},
    )

    first = build_research_provenance(**payload)
    again = build_research_provenance(**payload)
    changed = build_research_provenance(**{**payload, "bar_count": 2})

    assert first["provenance_hash"] == again["provenance_hash"]
    assert first["provenance_hash"] != changed["provenance_hash"]
    assert "provenance_hash" not in first["payload"]
