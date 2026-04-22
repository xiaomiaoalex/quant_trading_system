"""
Binance Data Provider - 实现 DataProviderPort
============================================
从 Binance Spot Demo REST API 获取 K 线数据。
"""
from __future__ import annotations

import aiohttp
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from trader.services.backtesting.ports import DataProviderPort, OHLCV


DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "DOTUSDT", "MATICUSDT", "LTCUSDT",
]


@dataclass
class BinanceDataConfig:
    """Binance 数据源配置"""
    base_url: str = "https://testnet.binance.vision/api"
    timeout: float = 30.0
    max_retries: int = 3
    symbols: List[str] = field(default_factory=lambda: DEFAULT_SYMBOLS.copy())
    supported_intervals: List[str] = field(
        default_factory=lambda: ["1m", "5m", "15m", "1h", "4h", "1d"]
    )


class BinanceDataProvider:
    """Binance Spot Demo 数据供给，实现 DataProviderPort。"""

    def __init__(self, config: Optional[BinanceDataConfig] = None):
        self._config = config or BinanceDataConfig()
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, Any] = {}

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[OHLCV]:
        if interval not in self._config.supported_intervals:
            raise ValueError(
                f"Unsupported interval: {interval}. "
                f"Supported: {self._config.supported_intervals}"
            )

        cache_key = f"{symbol}:{interval}:{start_date.isoformat()}:{end_date.isoformat()}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        session = await self._ensure_session()
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "startTime": int(start_date.timestamp() * 1000),
            "endTime": int(end_date.timestamp() * 1000),
            "limit": 1000,
        }

        url = f"{self._config.base_url}/v3/klines"
        retries = 0

        while retries < self._config.max_retries:
            try:
                async with session.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=self._config.timeout)
                ) as resp:
                    if resp.status == 200:
                        raw_data = await resp.json()
                        klines = [
                            OHLCV(
                                timestamp=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                                open=Decimal(k[1]),
                                high=Decimal(k[2]),
                                low=Decimal(k[3]),
                                close=Decimal(k[4]),
                                volume=Decimal(k[5]),
                            )
                            for k in raw_data
                        ]
                        self._cache[cache_key] = klines
                        return klines
                    elif resp.status == 429:
                        await asyncio.sleep(5)
                        retries += 1
                    else:
                        raise RuntimeError(f"Binance API error: {resp.status}")
            except aiohttp.ClientError:
                retries += 1
                await asyncio.sleep(2 ** retries)

        raise RuntimeError(f"Failed to fetch klines after {self._config.max_retries} retries")

    async def get_features(
        self,
        symbol: str,
        feature_names: List[str],
        start_date: datetime,
        end_date: datetime,
    ) -> Dict[str, List[Any]]:
        return {}

    async def get_symbols(self) -> List[str]:
        return self._config.symbols.copy()

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()