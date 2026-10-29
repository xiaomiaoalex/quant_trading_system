"""
History Dataset OHLCV Provider
==============================
按已发布历史数据集读取 OHLCV 的回测数据源。

与 `FeatureStoreOHLCVDataProvider` 的差异：
- 使用 FeatureStore 严格模式（PostgreSQL 不可用 / 读取失败直接抛错，不回退内存）
- 读取后再次核验真实完整性：行数、时间连续性、symbol / interval 一致性

目的：执行阶段的校验不能只依赖 version 名称，数据缺失必须让回测失败。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from trader.adapters.persistence.feature_store import FeatureStore, get_strict_feature_store
from trader.services.backtesting.feature_store_data_provider import FeatureStoreOHLCVDataProvider
from trader.services.backtesting.history_dataset_resolver import ResolvedDataset
from trader.services.backtesting.ports import OHLCV, DataProviderPort

logger = logging.getLogger(__name__)

_ONE_MINUTE_MS = 60_000


class HistoryDatasetOHLCVDataProvider(DataProviderPort):
    """按数据集读取真实历史 OHLCV，并在读取后复核完整性。"""

    def __init__(
        self,
        dataset: ResolvedDataset,
        feature_store: Optional[FeatureStore] = None,
    ) -> None:
        self._dataset = dataset
        self._feature_store = feature_store or get_strict_feature_store()
        self._inner = FeatureStoreOHLCVDataProvider(
            feature_store=self._feature_store,
            feature_version=dataset.feature_version,
        )
        self.last_quality_summary: Dict[str, Any] = {}

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[OHLCV]:
        if symbol != self._dataset.symbol:
            raise ValueError(
                f"Dataset {self._dataset.dataset_id} only provides {self._dataset.symbol}, "
                f"got {symbol}"
            )
        if interval != self._dataset.interval:
            raise ValueError(
                f"Dataset {self._dataset.dataset_id} only provides interval "
                f"{self._dataset.interval}, got {interval}"
            )

        klines = await self._inner.get_klines(
            symbol=symbol,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )

        expected = int((end_date - start_date).total_seconds() // 60) + 1
        if len(klines) != expected:
            raise ValueError(
                f"Incomplete OHLCV read for dataset {self._dataset.dataset_id}: "
                f"expected {expected} bars in [{start_date.isoformat()}, "
                f"{end_date.isoformat()}], got {len(klines)}"
            )

        for previous, current in zip(klines, klines[1:]):
            delta_ms = int(current.timestamp.timestamp() * 1000) - int(
                previous.timestamp.timestamp() * 1000
            )
            if delta_ms != _ONE_MINUTE_MS:
                raise ValueError(
                    f"Non-contiguous OHLCV for dataset {self._dataset.dataset_id}: "
                    f"gap of {delta_ms - _ONE_MINUTE_MS} ms at {current.timestamp.isoformat()}"
                )

        first_ts_ms = int(klines[0].timestamp.timestamp() * 1000)
        last_ts_ms = int(klines[-1].timestamp.timestamp() * 1000)
        summary = dict(self._inner.last_quality_summary)
        summary.update(
            {
                "source": "history_dataset",
                "dataset_id": self._dataset.dataset_id,
                "feature_version": self._dataset.feature_version,
                "market": self._dataset.market,
                "interval": self._dataset.interval,
                "archive_sha256": self._dataset.archive_sha256,
                "official_sha256": self._dataset.official_sha256,
                "source_url": self._dataset.source_url,
                "first_ts_ms": first_ts_ms,
                "last_ts_ms": last_ts_ms,
                "verified_rows": len(klines),
                "expected_rows": expected,
                "integrity_verified": True,
            }
        )
        self.last_quality_summary = summary
        return klines

    async def get_features(
        self,
        symbol: str,
        feature_names: List[str],
        start_date: datetime,
        end_date: datetime,
    ) -> Dict[str, List[Any]]:
        return await self._inner.get_features(symbol, feature_names, start_date, end_date)

    async def get_symbols(self) -> List[str]:
        return [self._dataset.symbol]


__all__ = ["HistoryDatasetOHLCVDataProvider"]
