"""
BinanceSpotDemoBroker.py
========================

Binance Spot Demo / Spot Testnet Broker Adapter

当前范围：
- 仅支持 Spot API
- 支持 Demo 与 Spot Testnet 两种环境切换
- 支持账户读取、现货下单、撤单、查单、未结单查询

明确不包含：
- Margin 专用接口
- Futures / UM / CM 合约接口
- 借币还币
- 合约持仓与保证金字段
- 完整止损单族实现
"""

from __future__ import annotations
from urllib.parse import urlencode

import asyncio
import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Union

import aiohttp

from trader.adapters.binance.proxy_failover import get_proxy_failover_controller
from trader.core.application.ports import (
    BrokerAccount,
    BrokerBusinessError,
    BrokerNetworkError,
    BrokerOrder,
    BrokerPort,
)
from trader.core.domain.models.order import OrderSide, OrderStatus, OrderType
from trader.core.domain.models.position import BrokerPosition


DEFAULT_SPOT_DEMO_BASE_URL = "https://demo-api.binance.com/api"
DEFAULT_SPOT_DEMO_WS_URL = "wss://demo-stream.binance.com/ws"

DEFAULT_SPOT_TESTNET_BASE_URL = "https://testnet.binance.vision/api"
DEFAULT_SPOT_TESTNET_WS_URL = "wss://stream.testnet.binance.vision/ws"


@dataclass
class BinanceSpotDemoBrokerConfig:
    """Binance Spot Demo / Spot Testnet broker config."""

    api_key: str
    secret_key: str

    timeout: float = 15.0
    max_retries: int = 3
    recv_window: int = 5000

    # 显式 HTTP/HTTPS 代理，如 "http://127.0.0.1:7890"
    proxy_url: Optional[str] = None

    # 默认走 Spot Demo
    base_url: str = DEFAULT_SPOT_DEMO_BASE_URL
    ws_url: str = DEFAULT_SPOT_DEMO_WS_URL
    broker_name: str = "binance_spot_demo"

    # 是否校验 SSL 证书；默认 True 更安全
    verify_ssl: bool = True

    @classmethod
    def for_demo(
        cls,
        api_key: str,
        secret_key: str,
        timeout: float = 15.0,
        max_retries: int = 3,
        recv_window: int = 5000,
        proxy_url: Optional[str] = None,
        verify_ssl: bool = True,
    ) -> "BinanceSpotDemoBrokerConfig":
        return cls(
            api_key=api_key,
            secret_key=secret_key,
            timeout=timeout,
            max_retries=max_retries,
            recv_window=recv_window,
            proxy_url=proxy_url,
            base_url=DEFAULT_SPOT_DEMO_BASE_URL,
            ws_url=DEFAULT_SPOT_DEMO_WS_URL,
            broker_name="binance_spot_demo",
            verify_ssl=verify_ssl,
        )

    @classmethod
    def for_testnet(
        cls,
        api_key: str,
        secret_key: str,
        timeout: float = 15.0,
        max_retries: int = 3,
        recv_window: int = 5000,
        proxy_url: Optional[str] = None,
        verify_ssl: bool = True,
    ) -> "BinanceSpotDemoBrokerConfig":
        return cls(
            api_key=api_key,
            secret_key=secret_key,
            timeout=timeout,
            max_retries=max_retries,
            recv_window=recv_window,
            proxy_url=proxy_url,
            base_url=DEFAULT_SPOT_TESTNET_BASE_URL,
            ws_url=DEFAULT_SPOT_TESTNET_WS_URL,
            broker_name="binance_spot_testnet",
            verify_ssl=verify_ssl,
        )


class BinanceSpotDemoBroker(BrokerPort):
    """
    Binance Spot Demo / Spot Testnet Broker.

    当前支持：
    - Spot account 读取
    - Market / Limit 下单
    - 撤单
    - 查单
    - 查未结订单
    - Spot balances 近似映射 positions

    当前不支持：
    - Margin / Futures
    - listenKey / user stream
    - stop 单族
    """

    def __init__(self, config: BinanceSpotDemoBrokerConfig):
        self._config = config
        self._connected = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._callbacks: List[Callable[..., Any]] = []
        self._account_cache: Optional[Dict[str, Any]] = None
        self._time_offset_ms: int = 0
        self._time_offset_synced: bool = False
        self._time_sync_lock = asyncio.Lock()
        # 额外安全余量，避免“ahead of server time”边界误差
        self._timestamp_safety_margin_ms: int = 150
        self._proxy_failover = get_proxy_failover_controller()

    @property
    def broker_name(self) -> str:
        return self._config.broker_name

    @property
    def supported_features(self) -> List[str]:
        return [
            "MARKET_ORDER",
            "LIMIT_ORDER",
            "CANCEL_ORDER",
            "QUERY_ORDER",
            "GET_OPEN_ORDERS",
            "GET_ACCOUNT",
            "PING",
            "TIME",
            "EXCHANGE_INFO",
        ]

    def _build_url(self, endpoint: str) -> str:
        return f"{self._config.base_url}{endpoint}"

    def _headers(self) -> Dict[str, str]:
        return {"X-MBX-APIKEY": self._config.api_key}

    def _resolve_proxy(self) -> Optional[str]:
        return self._proxy_failover.select_proxy(self._config.proxy_url)

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.upper().replace("-", "").replace("/", "")

    def _coerce_side(self, side: Union[OrderSide, str]) -> str:
        if isinstance(side, str):
            return side.upper()
        value = getattr(side, "value", side)
        return str(value).upper()

    def _coerce_order_type(self, order_type: Union[OrderType, str]) -> str:
        if isinstance(order_type, str):
            return order_type.upper()
        value = getattr(order_type, "value", order_type)
        return str(value).upper()

    def _parse_order_side(self, value: str) -> OrderSide:
        raw = str(value).strip()
        candidates = [raw, raw.upper(), raw.lower()]
        for candidate in candidates:
            try:
                return OrderSide(candidate)
            except Exception:
                continue
        raise ValueError(f"Unsupported order side: {value}")

    def _parse_order_type(self, value: str) -> OrderType:
        raw = str(value).strip()
        candidates = [raw, raw.upper(), raw.lower()]
        for candidate in candidates:
            try:
                return OrderType(candidate)
            except Exception:
                continue
        raise ValueError(f"Unsupported order type: {value}")

    def _parse_order_status(self, value: str) -> OrderStatus:
        raw = str(value).strip().upper()

        mapping = {
            "NEW": OrderStatus.SUBMITTED,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELLED,
            "CANCELLED": OrderStatus.CANCELLED,
            "PENDING_CANCEL": OrderStatus.CANCEL_PENDING,
            "REJECTED": OrderStatus.REJECTED,
            "EXPIRED": OrderStatus.CANCELLED,
        }

        if raw in mapping:
            return mapping[raw]

        raise ValueError(f"Unsupported order status: {value}")

    def _sign(self, query_string: str) -> str:
        return hmac.new(
            self._config.secret_key.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _signed_params(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = dict(params or {})
        payload["timestamp"] = self._current_timestamp_ms()
        payload["recvWindow"] = self._config.recv_window

        query_string = urlencode(payload, doseq=True)
        payload["signature"] = self._sign(query_string)
        return payload

    def _current_timestamp_ms(self) -> int:
        return int(time.time() * 1000 + self._time_offset_ms - self._timestamp_safety_margin_ms)

    async def _ensure_time_offset(self) -> None:
        if self._time_offset_synced:
            return
        await self._refresh_time_offset()

    async def _refresh_time_offset(self) -> int:
        async with self._time_sync_lock:
            server_time_ms = await self.get_server_time()
            local_time_ms = int(time.time() * 1000)
            self._time_offset_ms = server_time_ms - local_time_ms
            self._time_offset_synced = True
            return self._time_offset_ms

    async def _ensure_session(self) -> None:
        if self._session is not None:
            return

        timeout = aiohttp.ClientTimeout(total=self._config.timeout)
        connector = aiohttp.TCPConnector(ssl=self._config.verify_ssl)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            trust_env=True,  # 允许系统 / 环境变量代理参与
        )

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """
        统一请求入口。
        - signed=True 时自动追加 timestamp/recvWindow/signature
        - proxy_url 会显式传给 aiohttp
        """
        await self._ensure_session()
        assert self._session is not None

        req_headers = headers or (self._headers() if signed else None)
        url = self._build_url(endpoint)

        last_error: Optional[Exception] = None
        did_resync_for_1021 = False

        for attempt in range(1, self._config.max_retries + 1):
            proxy = self._resolve_proxy()
            try:
                if signed:
                    await self._ensure_time_offset()
                    req_params = dict(params or {})
                    req_params["timestamp"] = self._current_timestamp_ms()
                    req_params["recvWindow"] = self._config.recv_window

                    query_string = urlencode(req_params, doseq=True)
                    signature = self._sign(query_string)
                    final_url = f"{url}?{query_string}&signature={signature}"
                else:
                    req_params = params or {}
                    if req_params:
                        final_url = f"{url}?{urlencode(req_params, doseq=True)}"
                    else:
                        final_url = url
                async with self._session.request(
                    method=method,
                    url=final_url,
                    headers=req_headers,
                    proxy=proxy,
                ) as resp:
                    self._proxy_failover.report_success(proxy)
                    content_type = resp.headers.get("Content-Type", "")
                    if "application/json" in content_type:
                        data = await resp.json()
                    else:
                        text = await resp.text()
                        data = {"raw": text}

                    if resp.status >= 400:
                        msg = data.get("msg", str(data))
                        code = data.get("code")
                        if signed and str(code) == "-1021" and not did_resync_for_1021:
                            did_resync_for_1021 = True
                            await self._refresh_time_offset()
                            await asyncio.sleep(0.1)
                            continue
                        raise BrokerBusinessError(
                            f"{method} {endpoint} failed: status={resp.status}, code={code}, msg={msg}"
                        )

                    return data

            except BrokerBusinessError:
                raise
            except Exception as exc:
                self._proxy_failover.report_failure(proxy)
                last_error = exc
                if attempt >= self._config.max_retries:
                    break
                await asyncio.sleep(min(0.5 * attempt, 2.0))

        raise BrokerNetworkError(
            f"Request failed after {self._config.max_retries} retries: "
            f"{method} {endpoint}, error={last_error}"
        )

    async def ping(self) -> bool:
        await self._request("GET", "/v3/ping", signed=False)
        return True

    async def get_server_time(self) -> int:
        data = await self._request("GET", "/v3/time", signed=False)
        return int(data["serverTime"])

    async def get_exchange_info(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        params = None
        if symbol:
            params = {"symbol": self._normalize_symbol(symbol)}
        return await self._request("GET", "/v3/exchangeInfo", params=params, signed=False)

    async def get_symbol_step_size(self, symbol: str) -> Decimal:
        """
        Fetch exchange info and return the LOT_SIZE stepSize for the given symbol.

        Returns:
            Decimal representation of the stepSize (e.g., Decimal("0.00000001")).
            Returns Decimal("0") if stepSize is "0" or not found.

        Raises:
            ValueError: If symbol not found in exchange info.
        """
        data = await self.get_exchange_info(symbol=symbol)
        symbols = data.get("symbols", [])
        if not symbols:
            raise ValueError(f"Symbol {symbol} not found in exchange info")
        symbol_data = symbols[0]
        filters = symbol_data.get("filters", [])
        for f in filters:
            if f.get("filterType") == "LOT_SIZE":
                step_size_str = f.get("stepSize", "0")
                return Decimal(step_size_str)
        # LOT_SIZE not found, return zero (will use default quantization)
        return Decimal("0")

    @staticmethod
    def quantize_by_step_size(quantity: Decimal, step_size: Decimal) -> Decimal:
        """
        Quantize quantity to the nearest valid LOT_SIZE step (floor to nearest valid step).

        Binance LOT_SIZE requires: quantity = floor(quantity / stepSize) * stepSize

        Args:
            quantity: The raw quantity to quantize.
            step_size: The stepSize from Binance LOT_SIZE filter (must be > 0).

        Returns:
            The largest quantity <= input that is a valid multiple of stepSize.

        Raises:
            ValueError: If step_size <= 0.
        """
        if step_size <= 0:
            raise ValueError(f"step_size must be positive, got {step_size}")
        steps = int(quantity / step_size)
        return Decimal(steps) * step_size

    async def connect(self) -> None:
        """
        建立连接。
        顺序：
        1. 创建 session
        2. ping
        3. time
        4. account 鉴权读取
        """
        if self._connected:
            return

        await self._ensure_session()

        try:
            await self.ping()
            await self._refresh_time_offset()
            await self._fetch_account()
            self._connected = True
        except Exception as exc:
            await self.disconnect()
            raise BrokerNetworkError(
                f"Failed to connect to {self._config.broker_name}: {exc}"
            ) from exc

    async def disconnect(self) -> None:
        self._connected = False
        self._account_cache = None
        self._time_offset_synced = False

        if self._session is not None:
            await self._session.close()
            self._session = None

    async def is_connected(self) -> bool:
        return self._connected

    async def _fetch_account(self) -> Dict[str, Any]:
        data = await self._request("GET", "/v3/account", signed=True)
        self._account_cache = data
        return data

    def _parse_order_response(self, data: Dict[str, Any]) -> BrokerOrder:
        transact_time = (
            data.get("transactTime")
            or data.get("updateTime")
            or data.get("time")
            or int(time.time() * 1000)
        )

        avg_price_raw = data.get("avgPrice")
        if avg_price_raw in (None, "", "0.00000000"):
            avg_price_raw = "0"

        return BrokerOrder(
            broker_order_id=str(data.get("orderId", "")),
            client_order_id=str(data.get("clientOrderId", "")),
            symbol=str(data.get("symbol", "")),
            side=self._parse_order_side(str(data.get("side", ""))),
            order_type=self._parse_order_type(str(data.get("type", ""))),
            quantity=Decimal(str(data.get("origQty", "0"))),
            filled_quantity=Decimal(str(data.get("executedQty", "0"))),
            average_price=Decimal(str(avg_price_raw)),
            status=self._parse_order_status(str(data.get("status", ""))),
            created_at=datetime.fromtimestamp(transact_time / 1000, tz=timezone.utc),
        )

    async def place_order(
        self,
        symbol: str,
        side: Union[OrderSide, str],
        order_type: Union[OrderType, str],
        quantity: Decimal,
        price: Optional[Decimal] = None,
        client_order_id: Optional[str] = None,
    ) -> BrokerOrder:
        if not self._connected:
            raise ConnectionError("Broker not connected")

        symbol_norm = self._normalize_symbol(symbol)
        side_value = self._coerce_side(side)
        order_type_value = self._coerce_order_type(order_type)

        params: Dict[str, Any] = {
            "symbol": symbol_norm,
            "side": side_value,
            "type": order_type_value,
            "quantity": str(quantity),
        }

        if client_order_id:
            params["newClientOrderId"] = client_order_id

        if order_type_value == "LIMIT":
            if price is None:
                raise ValueError("LIMIT order requires price")
            params["price"] = str(price)
            params["timeInForce"] = "GTC"

        if order_type_value not in {"MARKET", "LIMIT"}:
            raise ValueError(
                f"Unsupported spot order_type for current adapter: {order_type_value}"
            )

        data = await self._request("POST", "/v3/order", params=params, signed=True)
        return self._parse_order_response(data)

    async def cancel_order(
        self,
        client_order_id: str,
        broker_order_id: Optional[str] = None,
        symbol: str = "BTCUSDT",
    ) -> bool:
        if not self._connected:
            raise ConnectionError("Broker not connected")

        params: Dict[str, Any] = {
            "symbol": self._normalize_symbol(symbol),
        }

        if broker_order_id:
            params["orderId"] = broker_order_id
        else:
            params["origClientOrderId"] = client_order_id

        await self._request("DELETE", "/v3/order", params=params, signed=True)
        return True

    async def get_order(
        self,
        client_order_id: str,
        broker_order_id: Optional[str] = None,
        symbol: str = "BTCUSDT",
    ) -> Optional[BrokerOrder]:
        if not self._connected:
            raise ConnectionError("Broker not connected")

        params: Dict[str, Any] = {
            "symbol": self._normalize_symbol(symbol),
        }

        if broker_order_id:
            params["orderId"] = broker_order_id
        else:
            params["origClientOrderId"] = client_order_id

        try:
            data = await self._request("GET", "/v3/order", params=params, signed=True)
            return self._parse_order_response(data)
        except BrokerBusinessError as exc:
            msg = str(exc)
            if "Unknown order sent" in msg or "status=400" in msg:
                return None
            raise

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[BrokerOrder]:
        if not self._connected:
            raise ConnectionError("Broker not connected")

        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = self._normalize_symbol(symbol)

        data = await self._request("GET", "/v3/openOrders", params=params, signed=True)
        return [self._parse_order_response(item) for item in data]

    async def get_positions(self) -> List[BrokerPosition]:
        """
        Spot 下没有真正的 futures-style positions。
        这里仅把 balances 近似映射成统一接口所需的 position 视图。
        """
        if not self._connected:
            raise ConnectionError("Broker not connected")

        account = self._account_cache or await self._fetch_account()
        results: List[BrokerPosition] = []

        for balance in account.get("balances", []):
            free = Decimal(str(balance.get("free", "0")))
            locked = Decimal(str(balance.get("locked", "0")))
            total = free + locked
            if total <= 0:
                continue

            results.append(
                BrokerPosition(
                    symbol=str(balance["asset"]),
                    quantity=total,
                    avg_price=Decimal("0"),
                    unrealized_pnl=Decimal("0"),
                )
            )

        return results

    async def get_account(self) -> BrokerAccount:
        if not self._connected:
            raise ConnectionError("Broker not connected")

        account = self._account_cache or await self._fetch_account()

        total_equity = Decimal("0")
        available_cash = Decimal("0")

        for balance in account.get("balances", []):
            asset = str(balance.get("asset", ""))
            free = Decimal(str(balance.get("free", "0")))
            locked = Decimal(str(balance.get("locked", "0")))

            # 这里只做最简近似：把稳定币余额作为 equity/cash 估算
            if asset in {"USDT", "BUSD", "FDUSD", "USDC", "USD"}:
                total_equity += free + locked
                available_cash += free

        return BrokerAccount(
            total_equity=total_equity,
            available_cash=available_cash,
            currency="USDT",
        )

    def register_callback(self, callback: Callable[..., Any]) -> None:
        self._callbacks.append(callback)
