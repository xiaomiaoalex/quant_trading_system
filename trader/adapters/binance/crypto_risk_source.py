from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, TYPE_CHECKING
from urllib.parse import urlencode

from trader.adapters.binance.crypto_risk_mapper import (
    map_account_risk,
    map_exchange_info,
    map_leverage_brackets,
    map_mark_prices,
    map_open_orders,
    map_positions,
)
from trader.core.domain.models.crypto_risk import (
    CryptoAccountRisk,
    CryptoInstrumentSpec,
    CryptoMarketType,
    CryptoPositionRisk,
    LeverageBracket,
    OpenOrderRisk,
)

if TYPE_CHECKING:
    import aiohttp


BINANCE_USD_M_FUTURES_BASE_URL = "https://fapi.binance.com"


class BinanceFuturesRiskDataSourceError(RuntimeError):
    """Binance USD-M risk data source failure."""


@dataclass(slots=True)
class BinanceFuturesRiskDataSourceConfig:
    api_key: str
    secret_key: str
    base_url: str = BINANCE_USD_M_FUTURES_BASE_URL
    timeout: float = 10.0
    recv_window_ms: int = 5000
    proxy_url: str | None = None
    max_retries: int = 2


def _normalize_symbol(symbol: str) -> str:
    return symbol.upper().replace("-", "").replace("/", "").strip()


class BinanceFuturesRiskDataSource:
    """
    Adapter-side Binance USD-M source for crypto pre-trade risk snapshots.

    Raw Binance fields are converted before leaving this adapter boundary.
    """

    def __init__(
        self,
        config: BinanceFuturesRiskDataSourceConfig,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._config = config
        self._session = session
        self._owns_session = session is None

    async def start(self) -> None:
        await self._ensure_session()

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def get_account_risk(self) -> CryptoAccountRisk:
        data = await self._request("GET", "/fapi/v3/account", signed=True)
        return map_account_risk(data)

    async def get_positions(self, symbols: set[str] | None = None) -> list[CryptoPositionRisk]:
        params = self._single_symbol_params(symbols)
        data = await self._request("GET", "/fapi/v2/positionRisk", params=params, signed=True)
        positions = map_positions(data)
        requested = {_normalize_symbol(symbol) for symbol in symbols} if symbols else None
        if requested is None:
            return positions
        return [position for position in positions if position.symbol in requested]

    async def get_open_orders(self, symbols: set[str] | None = None) -> list[OpenOrderRisk]:
        params = self._single_symbol_params(symbols)
        data = await self._request("GET", "/fapi/v1/openOrders", params=params, signed=True)
        orders = map_open_orders(data)
        requested = {_normalize_symbol(symbol) for symbol in symbols} if symbols else None
        if requested is None:
            return orders
        return [order for order in orders if order.symbol in requested]

    async def get_instrument_specs(self, symbols: set[str]) -> dict[str, CryptoInstrumentSpec]:
        data = await self._request("GET", "/fapi/v1/exchangeInfo", signed=False)
        return map_exchange_info(
            data,
            market_type=CryptoMarketType.USD_M_FUTURES,
            symbols={_normalize_symbol(symbol) for symbol in symbols},
        )

    async def get_leverage_brackets(self, symbols: set[str]) -> dict[str, list[LeverageBracket]]:
        params = self._single_symbol_params(symbols)
        data = await self._request("GET", "/fapi/v1/leverageBracket", params=params, signed=True)
        brackets = map_leverage_brackets(data)
        requested = {_normalize_symbol(symbol) for symbol in symbols}
        return {symbol: values for symbol, values in brackets.items() if symbol in requested}

    async def get_mark_prices(self, symbols: set[str]) -> dict[str, Decimal]:
        params = self._single_symbol_params(symbols)
        data = await self._request("GET", "/fapi/v1/premiumIndex", params=params, signed=False)
        marks = map_mark_prices(data)
        requested = {_normalize_symbol(symbol) for symbol in symbols}
        return {symbol: price for symbol, price in marks.items() if symbol in requested}

    async def get_venue_health(self) -> str:
        return "HEALTHY"

    def _single_symbol_params(self, symbols: set[str] | None) -> dict[str, Any] | None:
        if symbols is None or len(symbols) != 1:
            return None
        symbol = next(iter(symbols))
        normalized = _normalize_symbol(symbol)
        return {"symbol": normalized} if normalized else None

    async def _ensure_session(self) -> None:
        if self._session is not None:
            return
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=self._config.timeout)
        self._session = aiohttp.ClientSession(timeout=timeout, trust_env=True)

    def _signed_params(self, params: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(params or {})
        payload["timestamp"] = int(time.time() * 1000)
        payload["recvWindow"] = self._config.recv_window_ms
        query = urlencode(payload, doseq=True)
        payload["signature"] = hmac.new(
            self._config.secret_key.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return payload

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        signed: bool,
    ) -> Any:
        await self._ensure_session()
        assert self._session is not None

        headers = {"X-MBX-APIKEY": self._config.api_key} if signed else None
        request_params = self._signed_params(params) if signed else dict(params or {})
        query = urlencode(request_params, doseq=True)
        url = f"{self._config.base_url}{endpoint}"
        if query:
            url = f"{url}?{query}"

        last_error: Exception | None = None
        attempts = max(1, self._config.max_retries)
        for attempt in range(attempts):
            try:
                async with self._session.request(
                    method,
                    url,
                    headers=headers,
                    proxy=self._config.proxy_url,
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()

                    text = await resp.text()
                    raise BinanceFuturesRiskDataSourceError(
                        f"{method} {endpoint} failed: status={resp.status}, body={text}"
                    )
            except Exception as exc:
                last_error = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(0.2 * (attempt + 1))

        raise BinanceFuturesRiskDataSourceError(
            f"{method} {endpoint} failed after {attempts} attempts: {last_error}"
        )
