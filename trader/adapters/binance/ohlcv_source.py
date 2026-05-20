from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import aiohttp


class BinanceOHLCVSourceError(RuntimeError):
    """Binance OHLCV REST source failure."""


@dataclass(slots=True)
class BinanceOHLCVRestConfig:
    base_url: str
    timeout: float = 10.0
    max_retries: int = 2
    retry_delay_seconds: float = 1.0


@dataclass(frozen=True, slots=True)
class BinanceOHLCVBar:
    symbol: str
    interval: str
    ts_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class BinanceOHLCVRestSource:
    """
    Adapter-side Binance kline source.

    Binance raw array payloads are converted to internal BinanceOHLCVBar objects
    before leaving the adapter boundary.
    """

    def __init__(
        self,
        config: BinanceOHLCVRestConfig,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._config = config
        self._session = session
        self._owns_session = session is None

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def fetch_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_ts_ms: int,
        end_ts_ms: int,
        limit: int,
    ) -> list[BinanceOHLCVBar]:
        if start_ts_ms > end_ts_ms:
            return []

        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "startTime": start_ts_ms,
            "endTime": end_ts_ms,
            "limit": min(max(limit, 1), 1000),
        }
        last_error: Exception | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                payload = await self._request("/v3/klines", params=params)
                if not isinstance(payload, list):
                    raise BinanceOHLCVSourceError("Binance kline response must be a list")
                return [self._map_bar(symbol.upper(), interval, item) for item in payload]
            except (aiohttp.ClientError, asyncio.TimeoutError, BinanceOHLCVSourceError) as exc:
                last_error = exc
                if attempt >= self._config.max_retries:
                    break
                await asyncio.sleep(self._config.retry_delay_seconds * (attempt + 1))

        raise BinanceOHLCVSourceError(f"Failed to fetch Binance klines: {last_error}")

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._config.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout, trust_env=True)
        return self._session

    async def _request(self, endpoint: str, *, params: dict[str, Any]) -> Any:
        session = await self._ensure_session()
        url = f"{self._config.base_url.rstrip('/')}{endpoint}"
        async with session.get(url, params=params) as response:
            if response.status == 429:
                retry_after = response.headers.get("Retry-After")
                raise BinanceOHLCVSourceError(f"Binance rate limited; Retry-After={retry_after}")
            if response.status >= 400:
                text = await response.text()
                raise BinanceOHLCVSourceError(f"Binance API error {response.status}: {text[:200]}")
            return await response.json()

    def _map_bar(self, symbol: str, interval: str, item: Any) -> BinanceOHLCVBar:
        if not isinstance(item, list) or len(item) < 6:
            raise BinanceOHLCVSourceError("Invalid Binance kline item shape")
        try:
            return BinanceOHLCVBar(
                symbol=symbol,
                interval=interval,
                ts_ms=int(item[0]),
                open=Decimal(str(item[1])),
                high=Decimal(str(item[2])),
                low=Decimal(str(item[3])),
                close=Decimal(str(item[4])),
                volume=Decimal(str(item[5])),
            )
        except Exception as exc:
            raise BinanceOHLCVSourceError(f"Invalid Binance kline values: {exc}") from exc
