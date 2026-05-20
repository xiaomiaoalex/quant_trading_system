"""
FeatureStore OHLCV data provider for backtesting.

This adapter keeps the VectorBT engine behind DataProviderPort while letting
real research backtests read versioned OHLCV data from FeatureStore.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from trader.adapters.persistence.feature_store import FeaturePoint, FeatureStore, get_feature_store
from trader.services.backtesting.data_pipeline import DataQualityStatus, DataValidator
from trader.services.backtesting.ports import OHLCV, DataProviderPort


class FeatureStoreOHLCVDataProvider(DataProviderPort):
    """Read versioned OHLCV data from FeatureStore through DataProviderPort."""

    _OHLCV_FEATURE_NAME = "ohlcv"
    _SEPARATE_FEATURES = ("open", "high", "low", "close", "volume")

    def __init__(
        self,
        feature_store: Optional[FeatureStore] = None,
        feature_version: str = "v1",
    ) -> None:
        self._feature_store = feature_store or get_feature_store()
        self._feature_version = feature_version
        self.last_quality_summary: Dict[str, Any] = {}

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[OHLCV]:
        start_ms = int(start_date.timestamp() * 1000)
        end_ms = int(end_date.timestamp() * 1000)

        points = await self._feature_store.read_feature_range(
            symbol=symbol,
            feature_name=self._OHLCV_FEATURE_NAME,
            start_time=start_ms,
            end_time=end_ms,
            version=self._feature_version,
        )
        feature_names = [self._OHLCV_FEATURE_NAME]
        klines = self._parse_compact_ohlcv(points) if points else []

        if not klines:
            klines = await self._read_separate_ohlcv(symbol, start_ms, end_ms)
            feature_names = list(self._SEPARATE_FEATURES)

        if not klines:
            self.last_quality_summary = self._missing_quality_summary(
                symbol=symbol,
                interval=interval,
                start_date=start_date,
                end_date=end_date,
                feature_names=feature_names,
            )
            raise ValueError(
                "FeatureStore missing OHLCV data for "
                f"{symbol} version={self._feature_version} "
                f"feature_names={','.join(feature_names)}"
            )

        self.last_quality_summary = self._quality_summary(
            klines=klines,
            symbol=symbol,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            feature_names=feature_names,
        )
        return klines

    async def get_features(
        self,
        symbol: str,
        feature_names: List[str],
        start_date: datetime,
        end_date: datetime,
    ) -> Dict[str, List[Any]]:
        start_ms = int(start_date.timestamp() * 1000)
        end_ms = int(end_date.timestamp() * 1000)
        output: Dict[str, List[Any]] = {}
        for feature_name in feature_names:
            points = await self._feature_store.read_feature_range(
                symbol=symbol,
                feature_name=feature_name,
                start_time=start_ms,
                end_time=end_ms,
                version=self._feature_version,
            )
            output[feature_name] = [point.value for point in points]
        return output

    async def get_symbols(self) -> List[str]:
        storage = getattr(self._feature_store, "_memory_storage", None)
        values = getattr(storage, "feature_values_by_key", {}) if storage is not None else {}
        symbols = {
            str(feature.get("symbol"))
            for feature in values.values()
            if feature.get("version") == self._feature_version
        }
        return sorted(symbol for symbol in symbols if symbol)

    def _parse_compact_ohlcv(self, points: List[FeaturePoint]) -> List[OHLCV]:
        klines: List[OHLCV] = []
        for point in points:
            if not isinstance(point.value, dict):
                raise ValueError(
                    "FeatureStore ohlcv value must be a dict with open/high/low/close/volume"
                )
            klines.append(
                OHLCV(
                    timestamp=datetime.fromtimestamp(point.ts_ms / 1000, tz=timezone.utc),
                    open=self._decimal_field(point.value, "open"),
                    high=self._decimal_field(point.value, "high"),
                    low=self._decimal_field(point.value, "low"),
                    close=self._decimal_field(point.value, "close"),
                    volume=self._decimal_field(point.value, "volume"),
                )
            )
        return klines

    async def _read_separate_ohlcv(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> List[OHLCV]:
        by_feature: Dict[str, Dict[int, FeaturePoint]] = {}
        for feature_name in self._SEPARATE_FEATURES:
            points = await self._feature_store.read_feature_range(
                symbol=symbol,
                feature_name=feature_name,
                start_time=start_ms,
                end_time=end_ms,
                version=self._feature_version,
            )
            by_feature[feature_name] = {point.ts_ms: point for point in points}

        if any(not points for points in by_feature.values()):
            return []

        common_ts = set.intersection(*(set(points.keys()) for points in by_feature.values()))
        klines: List[OHLCV] = []
        for ts_ms in sorted(common_ts):
            klines.append(
                OHLCV(
                    timestamp=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                    open=Decimal(str(by_feature["open"][ts_ms].value)),
                    high=Decimal(str(by_feature["high"][ts_ms].value)),
                    low=Decimal(str(by_feature["low"][ts_ms].value)),
                    close=Decimal(str(by_feature["close"][ts_ms].value)),
                    volume=Decimal(str(by_feature["volume"][ts_ms].value)),
                )
            )
        return klines

    def _decimal_field(self, value: Dict[str, Any], field_name: str) -> Decimal:
        if field_name not in value:
            raise ValueError(f"FeatureStore OHLCV value missing field: {field_name}")
        return Decimal(str(value[field_name]))

    def _quality_summary(
        self,
        klines: List[OHLCV],
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
        feature_names: List[str],
    ) -> Dict[str, Any]:
        validator = DataValidator()
        report = validator.validate(klines, symbol=symbol, interval=interval)
        expected_points = self._expected_points(start_date, end_date, interval)
        requested_coverage = (
            min(100.0, len(klines) / expected_points * 100.0)
            if expected_points > 0
            else report.coverage_percent
        )
        quality_score = max(
            0.0,
            min(1.0, min(report.coverage_percent, requested_coverage) / 100.0),
        )
        missing_data = (
            len(klines) < expected_points
            or report.status == DataQualityStatus.FAIL
            or quality_score < 1.0
        )
        return {
            "source": "feature_store",
            "feature_version": self._feature_version,
            "feature_names": feature_names,
            "quality_score": round(quality_score, 4),
            "missing_data": missing_data,
            "total_points": len(klines),
            "expected_points": expected_points,
            "coverage_percent": round(requested_coverage, 4),
            "validator_status": report.status.value,
            "validator_coverage_percent": round(report.coverage_percent, 4),
            "warnings": list(report.warnings),
            "issues": [issue.issue_type for issue in report.issues],
            "first_ts_ms": int(klines[0].timestamp.timestamp() * 1000),
            "last_ts_ms": int(klines[-1].timestamp.timestamp() * 1000),
        }

    def _missing_quality_summary(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
        feature_names: List[str],
    ) -> Dict[str, Any]:
        return {
            "source": "feature_store",
            "feature_version": self._feature_version,
            "feature_names": feature_names,
            "quality_score": 0.0,
            "missing_data": True,
            "total_points": 0,
            "expected_points": self._expected_points(start_date, end_date, interval),
            "coverage_percent": 0.0,
            "validator_status": DataQualityStatus.FAIL.value,
            "warnings": ["FeatureStore OHLCV data is missing"],
            "issues": ["EMPTY_DATA"],
            "symbol": symbol,
        }

    def _expected_points(self, start_date: datetime, end_date: datetime, interval: str) -> int:
        delta = self._interval_delta(interval)
        span = end_date - start_date
        if span.total_seconds() < 0:
            return 0
        return int(span / delta) + 1

    def _interval_delta(self, interval: str) -> timedelta:
        if interval == "1m":
            return timedelta(minutes=1)
        if interval == "5m":
            return timedelta(minutes=5)
        if interval == "15m":
            return timedelta(minutes=15)
        if interval == "30m":
            return timedelta(minutes=30)
        if interval == "4h":
            return timedelta(hours=4)
        if interval == "1d":
            return timedelta(days=1)
        if interval == "1w":
            return timedelta(weeks=1)
        return timedelta(hours=1)
