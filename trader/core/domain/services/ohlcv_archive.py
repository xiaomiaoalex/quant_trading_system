"""
OHLCV Archive Domain Services
=============================
币安公开归档（Binance Vision）1 分钟 K 线归档的纯领域逻辑。

本模块属于 Core Plane：
- 禁止 IO（不读文件、不发网络请求）
- 只做「解析 + 校验 + 覆盖度计算」的确定性计算

职责：
1. 归档时间戳解析（2025-01-01 起币安现货归档改为微秒，之前为毫秒）
2. K 线字段解析与数值合法性校验（非有限值、正价格、非负量、OHLC 关系）
3. 分钟对齐校验
4. 月份覆盖度校验（UTC [月初, 下月月初)），缺口 / 重复 / 冲突检测

设计要点：
- 时间戳一律用整数毫秒处理，禁止浮点时间戳
- 数值一律用 Decimal 解析，避免二进制浮点误差
- 相同时间同值去重；相同时间异值视为冲突，直接失败（不插值、不覆盖）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# 历史数据集 feature_version 前缀。带此前缀的请求必须显式提供 dataset_id。
DATASET_FEATURE_VERSION_PREFIX = "binance_spot_1m"

# 归档支持的币对 / 周期 / 市场
ARCHIVE_SUPPORTED_SYMBOLS: Tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
ARCHIVE_SUPPORTED_INTERVAL = "1m"
ARCHIVE_SUPPORTED_MARKET = "spot"
ARCHIVE_SOURCE_NAME = "binance_vision"

_ONE_MINUTE_MS = 60_000
_MONTH_PATTERN = re.compile(r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])$")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# 时间戳量级阈值：>= 1e15 视为微秒，>= 1e12 视为毫秒
_MICROSECOND_THRESHOLD = 10**15
_MILLISECOND_THRESHOLD = 10**12

# 归档 K 线标准列（官方文档顺序）
KLINE_COLUMNS: Tuple[str, ...] = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
)

_REQUIRED_COLUMNS = ("open_time", "open", "high", "low", "close", "volume")


class OhlcvArchiveError(Exception):
    """归档解析/校验错误基类。"""


class OhlcvRequestError(OhlcvArchiveError):
    """请求参数非法（symbol / interval / month 不支持）。"""


class OhlcvQualityError(OhlcvArchiveError):
    """归档内容质量不达标（数值非法、缺口、冲突、覆盖不足）。"""


@dataclass(frozen=True, slots=True)
class KlineBar:
    """规范化后的 1 分钟 K 线。"""

    open_time_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def as_value(self) -> Dict[str, str]:
        """转成 FeatureStore 存储用的紧凑字典（字符串保留精度）。"""
        return {
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
        }


@dataclass(frozen=True, slots=True)
class MonthCoverage:
    """月份覆盖度统计。"""

    year: int
    month: int
    expected_points: int
    actual_points: int
    duplicate_points: int
    gap_count: int
    first_ts_ms: Optional[int]
    last_ts_ms: Optional[int]
    month_start_ms: int
    month_end_exclusive_ms: int

    @property
    def is_complete(self) -> bool:
        """全月、无缺口、无冲突才算完整。"""
        return (
            self.actual_points == self.expected_points
            and self.gap_count == 0
            and self.duplicate_points == 0
            and self.first_ts_ms == self.month_start_ms
            and self.last_ts_ms == self.month_end_exclusive_ms - _ONE_MINUTE_MS
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "year": self.year,
            "month": self.month,
            "expected_points": self.expected_points,
            "actual_points": self.actual_points,
            "duplicate_points": self.duplicate_points,
            "gap_count": self.gap_count,
            "first_ts_ms": self.first_ts_ms,
            "last_ts_ms": self.last_ts_ms,
            "month_start_ms": self.month_start_ms,
            "month_end_exclusive_ms": self.month_end_exclusive_ms,
            "complete": self.is_complete,
        }


def parse_month(month: str) -> Tuple[int, int]:
    """解析 YYYY-MM，返回 (year, month)。格式非法抛 OhlcvRequestError。"""
    if not isinstance(month, str):
        raise OhlcvRequestError("month must be a string in YYYY-MM format")
    match = _MONTH_PATTERN.match(month.strip())
    if match is None:
        raise OhlcvRequestError(f"Invalid month '{month}', expected YYYY-MM")
    return int(match.group("year")), int(match.group("month"))


def month_bounds_ms(year: int, month: int) -> Tuple[int, int]:
    """返回 UTC [月初, 下月月初) 的毫秒边界。"""
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def expected_points_for_month(year: int, month: int) -> int:
    """整月应有的 1 分钟 K 线数量。"""
    start_ms, end_ms = month_bounds_ms(year, month)
    return int((end_ms - start_ms) // _ONE_MINUTE_MS)


def last_bar_open_ms(year: int, month: int) -> int:
    """当月最后一根 K 线的开盘时间（毫秒）。"""
    start_ms, end_ms = month_bounds_ms(year, month)
    return end_ms - _ONE_MINUTE_MS


def parse_open_time_ms(raw: str) -> int:
    """
    解析归档中的开盘时间戳为 UTC 毫秒整数。

    币安公开归档：2025-01-01 起现货使用微秒，之前使用毫秒。
    这里按量级自动识别，不使用浮点运算。
    """
    text = (raw or "").strip()
    if not text:
        raise OhlcvQualityError("open_time is empty")
    try:
        value = int(text)
    except ValueError as exc:
        raise OhlcvQualityError(f"Invalid open_time '{raw}': not an integer") from exc
    if value <= 0:
        raise OhlcvQualityError(f"Invalid open_time '{raw}': must be positive")
    if value >= _MICROSECOND_THRESHOLD:
        return value // 1000
    if value >= _MILLISECOND_THRESHOLD:
        return value
    raise OhlcvQualityError(f"Invalid open_time '{raw}': unexpected timestamp magnitude")


def _parse_decimal(raw: str, field: str) -> Decimal:
    text = (raw or "").strip()
    if not text:
        raise OhlcvQualityError(f"Invalid {field}: empty value")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise OhlcvQualityError(f"Invalid {field} '{raw}': not a decimal number") from exc
    if not value.is_finite():
        raise OhlcvQualityError(f"Invalid {field} '{raw}': value is not finite")
    return value


def detect_header(fields: Sequence[str]) -> bool:
    """判断 CSV 首行是否为表头。"""
    if not fields:
        return False
    normalized = [item.strip().lower() for item in fields]
    return all(name in normalized for name in _REQUIRED_COLUMNS)


def parse_kline_fields(fields: Sequence[str]) -> KlineBar:
    """解析一行归档字段为 KlineBar，并做数值合法性校验。"""
    if len(fields) < 6:
        raise OhlcvQualityError(
            f"Invalid kline row: expected at least 6 columns, got {len(fields)}"
        )

    open_time_ms = parse_open_time_ms(fields[0])
    open_price = _parse_decimal(fields[1], "open")
    high = _parse_decimal(fields[2], "high")
    low = _parse_decimal(fields[3], "low")
    close = _parse_decimal(fields[4], "close")
    volume = _parse_decimal(fields[5], "volume")

    validate_bar_values(
        open_time_ms=open_time_ms,
        open_price=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )
    return KlineBar(
        open_time_ms=open_time_ms,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def validate_bar_values(
    *,
    open_time_ms: int,
    open_price: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    volume: Decimal,
) -> None:
    """
    单根 K 线的数值 / 对齐校验。

    规则：
    - 价格为正
    - 成交量非负（允许 0）
    - high >= max(open, close)，low <= min(open, close)，high >= low
    - 开盘时间对齐到整分钟
    """
    for name, value in (
        ("open", open_price),
        ("high", high),
        ("low", low),
        ("close", close),
    ):
        if not value.is_finite():
            raise OhlcvQualityError(f"Invalid {name}: value is not finite")
        if value <= 0:
            raise OhlcvQualityError(f"Invalid {name} '{value}': price must be positive")
    if not volume.is_finite():
        raise OhlcvQualityError("Invalid volume: value is not finite")
    if volume < 0:
        raise OhlcvQualityError(f"Invalid volume '{volume}': volume must be non-negative")
    if high < low:
        raise OhlcvQualityError(f"Invalid high/low: high {high} < low {low}")
    if high < open_price or high < close:
        raise OhlcvQualityError(
            f"Invalid high {high}: must be >= open {open_price} and close {close}"
        )
    if low > open_price or low > close:
        raise OhlcvQualityError(
            f"Invalid low {low}: must be <= open {open_price} and close {close}"
        )
    if open_time_ms % _ONE_MINUTE_MS != 0:
        raise OhlcvQualityError(
            f"Invalid open_time {open_time_ms}: not aligned to a 1 minute boundary"
        )


def normalize_bars(bars: Iterable[KlineBar]) -> Tuple[List[KlineBar], int]:
    """
    规范化 K 线序列：按时间升序，相同时间同值去重，相同时间异值报冲突。

    Returns:
        (排序去重后的 K 线列表, 被去重的重复条数)

    Raises:
        OhlcvQualityError: 同一时间戳出现不同数值
    """
    by_time: Dict[int, KlineBar] = {}
    duplicates = 0
    for bar in bars:
        existing = by_time.get(bar.open_time_ms)
        if existing is None:
            by_time[bar.open_time_ms] = bar
            continue
        duplicates += 1
        if existing != bar:
            raise OhlcvQualityError(
                "Conflicting values for the same open_time "
                f"{bar.open_time_ms}: {existing.as_value()} != {bar.as_value()}"
            )
    ordered = [by_time[key] for key in sorted(by_time)]
    return ordered, duplicates


def count_gaps(bars: Sequence[KlineBar], interval_ms: int = _ONE_MINUTE_MS) -> int:
    """统计相邻 K 线之间缺失的分钟数。"""
    if len(bars) < 2:
        return 0
    gaps = 0
    for previous, current in zip(bars, bars[1:]):
        delta = current.open_time_ms - previous.open_time_ms
        if delta <= 0:
            continue
        missing = delta // interval_ms - 1
        if missing > 0:
            gaps += int(missing)
    return gaps


def validate_month_coverage(
    bars: Sequence[KlineBar],
    year: int,
    month: int,
) -> MonthCoverage:
    """
    校验 K 线是否完整覆盖指定 UTC 自然月。

    覆盖区间为 [月初, 下月月初)，最后一根 K 线开盘时间为「下月月初 - 1 分钟」。
    """
    start_ms, end_ms = month_bounds_ms(year, month)
    expected = expected_points_for_month(year, month)

    if not bars:
        raise OhlcvQualityError(f"No klines parsed for {year}-{month:02d}: archive appears empty")

    out_of_range = [bar.open_time_ms for bar in bars if not (start_ms <= bar.open_time_ms < end_ms)]
    if out_of_range:
        raise OhlcvQualityError(
            f"Out-of-range open_time in {year}-{month:02d}: {out_of_range[0]} "
            f"not within [{start_ms}, {end_ms})"
        )

    ordered, duplicates = normalize_bars(bars)
    gaps = count_gaps(ordered)
    coverage = MonthCoverage(
        year=year,
        month=month,
        expected_points=expected,
        actual_points=len(ordered),
        duplicate_points=duplicates,
        gap_count=gaps,
        first_ts_ms=ordered[0].open_time_ms,
        last_ts_ms=ordered[-1].open_time_ms,
        month_start_ms=start_ms,
        month_end_exclusive_ms=end_ms,
    )
    if not coverage.is_complete:
        raise OhlcvQualityError(
            f"Incomplete coverage for {year}-{month:02d}: expected {expected} points, "
            f"got {coverage.actual_points}, gaps={coverage.gap_count}, "
            f"duplicates={coverage.duplicate_points}, "
            f"range=[{coverage.first_ts_ms}, {coverage.last_ts_ms}] "
            f"expected range=[{start_ms}, {end_ms - _ONE_MINUTE_MS}]"
        )
    return coverage


def ms_to_datetime(ts_ms: int) -> datetime:
    """毫秒整数转 UTC datetime（整数运算，避免浮点误差）。"""
    return _EPOCH + timedelta(milliseconds=int(ts_ms))


def month_to_datetime(year: int, month: int) -> Tuple[datetime, datetime]:
    """返回 (月初, 最后 bar 开盘时间) 的 UTC datetime。"""
    start_ms, end_ms = month_bounds_ms(year, month)
    return (
        ms_to_datetime(start_ms),
        ms_to_datetime(end_ms - _ONE_MINUTE_MS),
    )


def align_to_minute(ts_ms: int) -> int:
    """向下对齐到整分钟。"""
    return ts_ms - (ts_ms % _ONE_MINUTE_MS)


def is_aligned_to_minute(ts_ms: int) -> bool:
    """判断时间戳是否对齐整分钟。"""
    return ts_ms % _ONE_MINUTE_MS == 0


def build_feature_version(symbol: str, month: str, archive_sha256: str) -> str:
    """
    构造数据集的 feature_version。

    形如 ``binance_spot_1m_<symbol>_<month>_<sha256[:12]>``。
    官方修订归档会改变 sha256，因此天然形成新版本，旧版本保持可用。
    """
    digest = (archive_sha256 or "").lower()
    if len(digest) < 12:
        raise OhlcvQualityError("archive sha256 is required to build feature version")
    return f"{DATASET_FEATURE_VERSION_PREFIX}_{symbol}_{month}_{digest[:12]}"


def month_offset(year: int, month: int, delta_months: int) -> Tuple[int, int]:
    """月份偏移（用于判断归档月份是否已结束）。"""
    index = year * 12 + (month - 1) + delta_months
    return index // 12, index % 12 + 1
