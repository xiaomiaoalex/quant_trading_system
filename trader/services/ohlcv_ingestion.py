from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from trader.adapters.binance.ohlcv_source import BinanceOHLCVBar
from trader.adapters.persistence.feature_store import FeatureStore, FeatureVersionConflictError

logger = logging.getLogger(__name__)

INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


class OHLCVSourcePort(Protocol):
    async def fetch_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_ts_ms: int,
        end_ts_ms: int,
        limit: int,
    ) -> list[BinanceOHLCVBar]: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class BinanceOHLCVSymbolIngestionResult:
    symbol: str
    imported: int = 0
    duplicates: int = 0
    conflicts: int = 0
    first_ts_ms: int | None = None
    latest_ts_ms: int | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BinanceOHLCVIngestionResult:
    running: bool
    feature_version: str
    interval: str
    symbols: list[str]
    started_at: str
    finished_at: str
    total_imported: int
    total_duplicates: int
    total_conflicts: int
    last_error: str | None
    symbol_results: list[BinanceOHLCVSymbolIngestionResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["symbol_results"] = [asdict(item) for item in self.symbol_results]
        return payload


@dataclass(frozen=True, slots=True)
class BinanceOHLCVWorkerStatus:
    running: bool
    feature_version: str | None
    interval: str | None
    symbols: list[str]
    poll_interval_seconds: float | None
    last_started_at: str | None
    last_finished_at: str | None
    last_error: str | None
    total_imported: int
    total_duplicates: int
    total_conflicts: int
    last_result: BinanceOHLCVIngestionResult | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["last_result"] = self.last_result.to_dict() if self.last_result else None
        return payload


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _now_ms() -> int:
    return int(time.time() * 1000)


def normalize_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for symbol in symbols:
        normalized = symbol.upper().replace("/", "").replace("-", "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
    return output


class BinanceOHLCVIngestionWorker:
    """
    Continuously pulls Binance OHLCV and writes compact ohlcv rows to FeatureStore.

    The worker is service-layer orchestration only: network IO stays in the
    injected source adapter, persistence stays in FeatureStore, and OMS/Core are
    not touched.
    """

    def __init__(
        self,
        *,
        feature_store: FeatureStore,
        source: OHLCVSourcePort,
    ) -> None:
        self._feature_store = feature_store
        self._source = source
        self._state_lock = asyncio.Lock()
        self._sync_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._request: Any | None = None
        self._last_started_at: str | None = None
        self._last_finished_at: str | None = None
        self._last_error: str | None = None
        self._last_result: BinanceOHLCVIngestionResult | None = None
        self._total_imported = 0
        self._total_duplicates = 0
        self._total_conflicts = 0

    async def sync_once(self, request: Any) -> BinanceOHLCVIngestionResult:
        self._validate_request(request)
        async with self._sync_lock:
            started_at = _utc_now_iso()
            symbol_results: list[BinanceOHLCVSymbolIngestionResult] = []
            last_error: str | None = None
            for symbol in normalize_symbols(list(request.symbols)):
                result = await self._sync_symbol(symbol, request)
                symbol_results.append(result)
                if result.error:
                    last_error = result.error

            finished_at = _utc_now_iso()
            total_imported = sum(item.imported for item in symbol_results)
            total_duplicates = sum(item.duplicates for item in symbol_results)
            total_conflicts = sum(item.conflicts for item in symbol_results)
            ingestion_result = BinanceOHLCVIngestionResult(
                running=self.is_running,
                feature_version=str(request.feature_version),
                interval=str(request.interval),
                symbols=normalize_symbols(list(request.symbols)),
                started_at=started_at,
                finished_at=finished_at,
                total_imported=total_imported,
                total_duplicates=total_duplicates,
                total_conflicts=total_conflicts,
                last_error=last_error,
                symbol_results=symbol_results,
            )
            self._last_started_at = started_at
            self._last_finished_at = finished_at
            self._last_error = last_error
            self._last_result = ingestion_result
            self._total_imported += total_imported
            self._total_duplicates += total_duplicates
            self._total_conflicts += total_conflicts
            return ingestion_result

    async def start(self, request: Any) -> BinanceOHLCVWorkerStatus:
        self._validate_request(request)
        async with self._state_lock:
            if self._task is not None and not self._task.done():
                return await self.status()

            self._request = request
            self._stop_event = asyncio.Event()
            self._last_started_at = _utc_now_iso()
            self._task = asyncio.create_task(self._run_loop(), name="binance-ohlcv-ingestion")
            logger.info(
                "[OHLCVIngestion] started symbols=%s interval=%s feature_version=%s",
                normalize_symbols(list(request.symbols)),
                request.interval,
                request.feature_version,
            )
            return await self.status()

    async def stop(self) -> BinanceOHLCVWorkerStatus:
        task: asyncio.Task[None] | None
        async with self._state_lock:
            task = self._task
            if task is None or task.done():
                self._task = None
                return await self.status()
            self._stop_event.set()

        try:
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with self._state_lock:
            self._task = None
            self._last_finished_at = _utc_now_iso()
        logger.info("[OHLCVIngestion] stopped")
        return await self.status()

    async def close(self) -> None:
        await self.stop()
        await self._source.close()

    async def status(self) -> BinanceOHLCVWorkerStatus:
        request = self._request
        return BinanceOHLCVWorkerStatus(
            running=self.is_running,
            feature_version=getattr(request, "feature_version", None),
            interval=getattr(request, "interval", None),
            symbols=normalize_symbols(list(getattr(request, "symbols", []) or [])),
            poll_interval_seconds=getattr(request, "poll_interval_seconds", None),
            last_started_at=self._last_started_at,
            last_finished_at=self._last_finished_at,
            last_error=self._last_error,
            total_imported=self._total_imported,
            total_duplicates=self._total_duplicates,
            total_conflicts=self._total_conflicts,
            last_result=self._last_result,
        )

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            request = self._request
            if request is None:
                return
            try:
                await self.sync_once(request)
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception("[OHLCVIngestion] sync failed")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=max(float(getattr(request, "poll_interval_seconds", 300.0)), 1.0),
                )
            except asyncio.TimeoutError:
                continue

    async def _sync_symbol(self, symbol: str, request: Any) -> BinanceOHLCVSymbolIngestionResult:
        interval_ms = INTERVAL_MS[str(request.interval)]
        start_ts_ms = await self._resolve_start_ts_ms(symbol, request, interval_ms)
        end_ts_ms = self._resolve_end_ts_ms(request, interval_ms)
        if start_ts_ms > end_ts_ms:
            return BinanceOHLCVSymbolIngestionResult(symbol=symbol)

        imported = 0
        duplicates = 0
        conflicts = 0
        first_ts_ms: int | None = None
        latest_ts_ms: int | None = None
        error: str | None = None
        cursor = start_ts_ms
        limit = min(max(int(getattr(request, "limit", 1000)), 1), 1000)

        while cursor <= end_ts_ms:
            try:
                bars = await self._source.fetch_klines(
                    symbol=symbol,
                    interval=str(request.interval),
                    start_ts_ms=cursor,
                    end_ts_ms=end_ts_ms,
                    limit=limit,
                )
            except Exception as exc:
                error = str(exc)
                break

            if not bars:
                break

            max_seen_ts = cursor
            for bar in bars:
                if bar.ts_ms < cursor or bar.ts_ms > end_ts_ms:
                    continue
                created, duplicate, conflict_error = await self._write_bar(symbol, request, bar)
                if created:
                    imported += 1
                if duplicate:
                    duplicates += 1
                if conflict_error is not None:
                    conflicts += 1
                    error = conflict_error
                first_ts_ms = bar.ts_ms if first_ts_ms is None else min(first_ts_ms, bar.ts_ms)
                latest_ts_ms = bar.ts_ms if latest_ts_ms is None else max(latest_ts_ms, bar.ts_ms)
                max_seen_ts = max(max_seen_ts, bar.ts_ms)

            next_cursor = max_seen_ts + interval_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(bars) < limit:
                break

        return BinanceOHLCVSymbolIngestionResult(
            symbol=symbol,
            imported=imported,
            duplicates=duplicates,
            conflicts=conflicts,
            first_ts_ms=first_ts_ms,
            latest_ts_ms=latest_ts_ms,
            error=error,
        )

    async def _write_bar(
        self,
        symbol: str,
        request: Any,
        bar: BinanceOHLCVBar,
    ) -> tuple[bool, bool, str | None]:
        value = {
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": float(bar.volume),
        }
        meta = {
            "interval": str(request.interval),
            "source": "binance_spot_rest",
        }
        requested_by = getattr(request, "requested_by", None)
        if requested_by:
            meta["requested_by"] = str(requested_by)

        try:
            created, duplicate = await self._feature_store.write_feature(
                symbol=symbol,
                feature_name="ohlcv",
                version=str(request.feature_version),
                ts_ms=bar.ts_ms,
                value=value,
                meta=meta,
            )
            return created, duplicate, None
        except FeatureVersionConflictError as exc:
            return False, False, str(exc)

    async def _resolve_start_ts_ms(self, symbol: str, request: Any, interval_ms: int) -> int:
        explicit = getattr(request, "start_ts_ms", None)
        if explicit is not None:
            return int(explicit)

        coverage = await self._feature_store.list_feature_coverage(
            "ohlcv",
            version=str(request.feature_version),
        )
        for item in coverage:
            if str(item.get("symbol")) == symbol and item.get("latest_ts_ms") is not None:
                return int(item["latest_ts_ms"]) + interval_ms

        lookback_hours = float(getattr(request, "lookback_hours", 24.0))
        return _now_ms() - int(lookback_hours * 3_600_000)

    def _resolve_end_ts_ms(self, request: Any, interval_ms: int) -> int:
        explicit = getattr(request, "end_ts_ms", None)
        if explicit is not None:
            return int(explicit)
        return _now_ms() - interval_ms

    def _validate_request(self, request: Any) -> None:
        symbols = normalize_symbols(list(getattr(request, "symbols", []) or []))
        if not symbols:
            raise ValueError("symbols cannot be empty")
        interval = str(getattr(request, "interval", ""))
        if interval not in INTERVAL_MS:
            raise ValueError(f"Unsupported interval: {interval}")
        if not str(getattr(request, "feature_version", "")).strip():
            raise ValueError("feature_version cannot be empty")
