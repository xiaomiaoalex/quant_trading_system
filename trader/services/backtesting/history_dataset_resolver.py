"""
History Dataset Resolver
========================
历史数据集（history_datasets）的回测准入解析。

职责：
- 按 dataset_id 读取已发布数据集，并做「发布状态 / 持久化 / 回测资格 / 市场 / 周期」校验
- 推导回测使用的 symbol、interval、feature_version 与时间范围
- 拒绝越界、未对齐、跨数据集的显式冲突

约束：
- 只在 PG 可用时工作；PG 不可用一律抛错（Fail-Closed），不得静默降级
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from trader.adapters.persistence.history_dataset_store import (
    HistoryDatasetStore,
    HistoryStoreUnavailableError,
)
from trader.core.domain.services.ohlcv_archive import (
    ARCHIVE_SOURCE_NAME,
    ARCHIVE_SUPPORTED_INTERVAL,
    ARCHIVE_SUPPORTED_MARKET,
    align_to_minute,
    is_aligned_to_minute,
    last_bar_open_ms,
    month_bounds_ms,
    parse_month,
)

logger = logging.getLogger(__name__)

_ONE_MINUTE_MS = 60_000


class DatasetNotFoundError(LookupError):
    """数据集不存在。"""


class DatasetNotEligibleError(RuntimeError):
    """数据集不满足回测准入条件（未发布 / 不完整 / 持久化不可用等）。"""


class DatasetRangeError(ValueError):
    """请求的时间范围超出数据集覆盖范围或未对齐。"""


@dataclass(frozen=True, slots=True)
class ResolvedDataset:
    """通过准入校验的数据集快照。"""

    dataset_id: str
    source: str
    market: str
    symbol: str
    interval: str
    month: str
    version: int
    feature_version: str
    archive_path: str
    archive_sha256: str
    official_sha256: Optional[str]
    source_url: str
    checksum_url: str
    row_count: int
    expected_points: int
    gap_count: int
    first_ts_ms: Optional[int]
    last_ts_ms: Optional[int]
    quality: Dict[str, Any]
    start_ts_ms: int
    end_ts_ms: int

    @property
    def range_expected_points(self) -> int:
        """请求范围内应有的 K 线数量（含结束端点）。"""
        if self.end_ts_ms < self.start_ts_ms:
            return 0
        return int((self.end_ts_ms - self.start_ts_ms) // _ONE_MINUTE_MS) + 1

    def provenance(self) -> Dict[str, Any]:
        """回测报告用的数据来源证据。"""
        return {
            "dataset_id": self.dataset_id,
            "source": self.source,
            "market": self.market,
            "symbol": self.symbol,
            "interval": self.interval,
            "month": self.month,
            "version": self.version,
            "feature_version": self.feature_version,
            "source_url": self.source_url,
            "official_sha256": self.official_sha256,
            "archive_sha256": self.archive_sha256,
            "archive_path": self.archive_path,
            "start_ts_ms": self.start_ts_ms,
            "end_ts_ms": self.end_ts_ms,
            "row_count": self.row_count,
            "expected_points": self.expected_points,
            "gap_count": self.gap_count,
            "quality": self.quality,
        }


class HistoryDatasetResolver:
    """数据集解析与准入校验。"""

    def __init__(self, store: Optional[HistoryDatasetStore] = None) -> None:
        self._store = store

    async def _get_store(self) -> HistoryDatasetStore:
        if self._store is not None:
            return self._store
        from trader.services.history_data_ingestion import get_history_dataset_store

        return await get_history_dataset_store()

    async def get_dataset(self, dataset_id: str) -> Dict[str, Any]:
        """读取数据集原始记录（不做准入校验）。"""
        store = await self._get_store()
        try:
            dataset = await store.get_dataset(dataset_id)
        except HistoryStoreUnavailableError:
            raise
        if dataset is None:
            raise DatasetNotFoundError(f"Dataset {dataset_id} not found")
        return dataset

    async def list_datasets(self, **kwargs: Any) -> list[Dict[str, Any]]:
        store = await self._get_store()
        return await store.list_datasets(**kwargs)

    async def resolve(
        self,
        dataset_id: str,
        *,
        start_ts_ms: Optional[int] = None,
        end_ts_ms: Optional[int] = None,
    ) -> ResolvedDataset:
        """
        解析数据集并做准入校验。

        Raises:
            DatasetNotFoundError: 数据集不存在
            DatasetNotEligibleError: 未发布 / 不完整 / 持久化不可用 / 市场或周期不符
            DatasetRangeError: 时间范围越界或未对齐
        """
        dataset = await self.get_dataset(dataset_id)

        if dataset.get("status") != "PUBLISHED":
            raise DatasetNotEligibleError(
                f"Dataset {dataset_id} is {dataset.get('status')}; only PUBLISHED datasets "
                "can be used for backtests"
            )
        if not dataset.get("persistence_ok"):
            raise DatasetNotEligibleError(f"Dataset {dataset_id} is not persisted in PostgreSQL")
        if not dataset.get("backtest_eligible"):
            raise DatasetNotEligibleError(f"Dataset {dataset_id} is not marked backtest eligible")
        if int(dataset.get("gap_count") or 0) != 0:
            raise DatasetNotEligibleError(
                f"Dataset {dataset_id} contains {dataset['gap_count']} gap(s)"
            )
        if int(dataset.get("row_count") or 0) != int(dataset.get("expected_points") or 0):
            raise DatasetNotEligibleError(
                f"Dataset {dataset_id} is incomplete: {dataset.get('row_count')} rows, "
                f"expected {dataset.get('expected_points')}"
            )
        if dataset.get("market") != ARCHIVE_SUPPORTED_MARKET:
            raise DatasetNotEligibleError(
                f"Dataset {dataset_id} market '{dataset.get('market')}' is not supported"
            )
        if dataset.get("interval") != ARCHIVE_SUPPORTED_INTERVAL:
            raise DatasetNotEligibleError(
                f"Dataset {dataset_id} interval '{dataset.get('interval')}' is not supported"
            )

        year, month_number = parse_month(str(dataset["month"]))
        month_start_ms, month_end_exclusive_ms = month_bounds_ms(year, month_number)
        month_last_bar_ms = last_bar_open_ms(year, month_number)

        resolved_start = month_start_ms if start_ts_ms is None else int(start_ts_ms)
        resolved_end = month_last_bar_ms if end_ts_ms is None else int(end_ts_ms)

        if not is_aligned_to_minute(resolved_start) or not is_aligned_to_minute(resolved_end):
            raise DatasetRangeError(
                "Backtest range must be aligned to 1 minute boundaries for 1m datasets"
            )
        if resolved_start < month_start_ms or resolved_end > month_last_bar_ms:
            raise DatasetRangeError(
                f"Requested range [{resolved_start}, {resolved_end}] is outside dataset "
                f"coverage [{month_start_ms}, {month_last_bar_ms}]"
            )
        if resolved_end <= resolved_start:
            raise DatasetRangeError("end_ts_ms must be greater than start_ts_ms")

        return ResolvedDataset(
            dataset_id=str(dataset["dataset_id"]),
            source=str(dataset["source"]),
            market=str(dataset["market"]),
            symbol=str(dataset["symbol"]),
            interval=str(dataset["interval"]),
            month=str(dataset["month"]),
            version=int(dataset.get("version") or 1),
            feature_version=str(dataset["feature_version"]),
            archive_path=str(dataset["archive_path"]),
            archive_sha256=str(dataset["archive_sha256"]),
            official_sha256=dataset.get("official_sha256"),
            source_url=str(dataset["source_url"]),
            checksum_url=str(dataset["checksum_url"]),
            row_count=int(dataset.get("row_count") or 0),
            expected_points=int(dataset.get("expected_points") or 0),
            gap_count=int(dataset.get("gap_count") or 0),
            first_ts_ms=dataset.get("first_ts_ms"),
            last_ts_ms=dataset.get("last_ts_ms"),
            quality=_as_dict(dataset.get("quality")),
            start_ts_ms=resolved_start,
            end_ts_ms=resolved_end,
        )


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


__all__ = [
    "DatasetNotEligibleError",
    "DatasetNotFoundError",
    "DatasetRangeError",
    "HistoryDatasetResolver",
    "ResolvedDataset",
]
