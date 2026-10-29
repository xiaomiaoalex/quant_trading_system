"""
OHLCV Archive Domain Tests
===========================
覆盖币安公开归档 K 线的时间戳解析、字段校验与月度覆盖度校验。

命名规范：test_<场景>_<预期结果>
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trader.core.domain.services.ohlcv_archive import (
    KlineBar,
    OhlcvQualityError,
    OhlcvRequestError,
    build_feature_version,
    count_gaps,
    detect_header,
    expected_points_for_month,
    last_bar_open_ms,
    month_bounds_ms,
    normalize_bars,
    parse_kline_fields,
    parse_month,
    parse_open_time_ms,
    validate_month_coverage,
)

YEAR = 2026
MONTH = 9


def _bar(open_time_ms: int, price: str = "100.0", volume: str = "1.0") -> KlineBar:
    return KlineBar(
        open_time_ms=open_time_ms,
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=Decimal(volume),
    )


def _full_month_bars(unit: str = "ms") -> list[KlineBar]:
    start_ms, end_ms = month_bounds_ms(YEAR, MONTH)
    total = expected_points_for_month(YEAR, MONTH)
    bars = []
    for index in range(total):
        ts_ms = start_ms + index * 60_000
        raw = ts_ms if unit == "ms" else ts_ms * 1000
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
        del raw
    return bars


def test_parse_month_accepts_valid_format():
    assert parse_month("2026-09") == (2026, 9)


def test_parse_month_rejects_invalid_format():
    with pytest.raises(OhlcvRequestError):
        parse_month("2026-13")
    with pytest.raises(OhlcvRequestError):
        parse_month("202609")


def test_parse_open_time_ms_handles_millisecond_era():
    assert parse_open_time_ms("1704067200000") == 1704067200000


def test_parse_open_time_ms_handles_microsecond_era():
    assert parse_open_time_ms("1704067200000000") == 1704067200000


def test_parse_open_time_ms_rejects_non_integer():
    with pytest.raises(OhlcvQualityError):
        parse_open_time_ms("2026-09-01T00:00:00Z")


def test_detect_header_recognizes_optional_header_row():
    assert detect_header(["open_time", "open", "high", "low", "close", "volume"]) is True
    assert detect_header(["1704067200000", "1", "2", "0.5", "1.5", "10"]) is False


def test_parse_kline_fields_parses_standard_row():
    bar = parse_kline_fields(
        ["1704067200000", "100.0", "101.0", "99.0", "100.5", "12.5", "1704067259999"]
    )
    assert bar.open_time_ms == 1704067200000
    assert bar.open == Decimal("100.0")
    assert bar.high == Decimal("101.0")
    assert bar.low == Decimal("99.0")
    assert bar.close == Decimal("100.5")
    assert bar.volume == Decimal("12.5")


def test_parse_kline_fields_accepts_zero_volume():
    bar = parse_kline_fields(["1704067200000", "100.0", "101.0", "99.0", "100.5", "0"])
    assert bar.volume == Decimal("0")


def test_parse_kline_fields_rejects_non_finite_value():
    with pytest.raises(OhlcvQualityError):
        parse_kline_fields(["1704067200000", "nan", "101.0", "99.0", "100.5", "1"])


def test_parse_kline_fields_rejects_infinite_value():
    with pytest.raises(OhlcvQualityError):
        parse_kline_fields(["1704067200000", "100.0", "inf", "99.0", "100.5", "1"])


def test_parse_kline_fields_rejects_non_positive_price():
    with pytest.raises(OhlcvQualityError):
        parse_kline_fields(["1704067200000", "0", "101.0", "99.0", "100.5", "1"])


def test_parse_kline_fields_rejects_negative_volume():
    with pytest.raises(OhlcvQualityError):
        parse_kline_fields(["1704067200000", "100.0", "101.0", "99.0", "100.5", "-1"])


def test_parse_kline_fields_rejects_invalid_ohlc_relation():
    with pytest.raises(OhlcvQualityError):
        parse_kline_fields(["1704067200000", "100.0", "100.5", "99.0", "101.0", "1"])


def test_parse_kline_fields_rejects_minute_misalignment():
    with pytest.raises(OhlcvQualityError):
        parse_kline_fields(["1704067230000", "100.0", "101.0", "99.0", "100.5", "1"])


def test_normalize_bars_deduplicates_identical_rows():
    start_ms, _ = month_bounds_ms(YEAR, MONTH)
    bars = [_bar(start_ms), _bar(start_ms)]
    normalized, duplicates = normalize_bars(bars)
    assert len(normalized) == 1
    assert duplicates == 1


def test_normalize_bars_rejects_conflicting_values():
    start_ms, _ = month_bounds_ms(YEAR, MONTH)
    bars = [_bar(start_ms, price="100.0"), _bar(start_ms, price="101.0")]
    with pytest.raises(OhlcvQualityError):
        normalize_bars(bars)


def test_count_gaps_detects_missing_minutes():
    start_ms, _ = month_bounds_ms(YEAR, MONTH)
    bars = [_bar(start_ms), _bar(start_ms + 180_000)]
    assert count_gaps(bars) == 2


def test_validate_month_coverage_accepts_complete_month():
    bars = _full_month_bars()
    coverage = validate_month_coverage(bars, YEAR, MONTH)
    assert coverage.is_complete is True
    assert coverage.actual_points == expected_points_for_month(YEAR, MONTH)
    assert coverage.gap_count == 0
    assert coverage.first_ts_ms == month_bounds_ms(YEAR, MONTH)[0]
    assert coverage.last_ts_ms == last_bar_open_ms(YEAR, MONTH)


def test_validate_month_coverage_accepts_microsecond_timestamps():
    bars = _full_month_bars(unit="us")
    coverage = validate_month_coverage(bars, YEAR, MONTH)
    assert coverage.is_complete is True


def test_validate_month_coverage_rejects_gap():
    bars = _full_month_bars()
    del bars[100]
    with pytest.raises(OhlcvQualityError):
        validate_month_coverage(bars, YEAR, MONTH)


def test_validate_month_coverage_rejects_out_of_range_timestamp():
    bars = _full_month_bars()
    bars[0] = KlineBar(
        open_time_ms=month_bounds_ms(YEAR, MONTH)[0] - 60_000,
        open=Decimal("100.0"),
        high=Decimal("101.0"),
        low=Decimal("99.0"),
        close=Decimal("100.5"),
        volume=Decimal("1"),
    )
    with pytest.raises(OhlcvQualityError):
        validate_month_coverage(bars, YEAR, MONTH)


def test_validate_month_coverage_rejects_empty_archive():
    with pytest.raises(OhlcvQualityError):
        validate_month_coverage([], YEAR, MONTH)


def test_build_feature_version_uses_hash_prefix():
    digest = "a" * 64
    version = build_feature_version("BTCUSDT", "2026-09", digest)
    assert version == "binance_spot_1m_BTCUSDT_2026-09_aaaaaaaaaaaa"


def test_build_feature_version_changes_with_hash():
    first = build_feature_version("BTCUSDT", "2026-09", "a" * 64)
    second = build_feature_version("BTCUSDT", "2026-09", "b" * 64)
    assert first != second
