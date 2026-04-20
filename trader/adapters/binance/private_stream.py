"""
Private Stream Manager - Binance Private WebSocket Stream
==========================================================
管理 Binance 私有 WebSocket 流（订单、成交等）。

特性：
- 独立 FSM 状态机（与 Public 完全隔离）
- listenKey 管理（创建、刷新、心跳）
- 静默断流检测（Pong 超时 或 30s 无成交推送）
- 自动重连 + REST 对齐触发

与 PublicStreamManager 的关系：
- 两者是完全独立的 FSM
- 无共享状态
- 可独立运行和故障
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Any, Callable, Awaitable

if TYPE_CHECKING:
    import aiohttp

import websockets
import websockets.client as ws_client

from trader.adapters.binance.stream_base import (
    BaseStreamFSM, StreamConfig, StreamState, StreamEvent
)
from trader.adapters.binance.proxy_failover import get_proxy_failover_controller
from trader.adapters.binance.rest_alignment import RestAlignmentSnapshot
from trader.adapters.binance.websockets_compat import install_connection_lost_guard


logger = logging.getLogger(__name__)
install_connection_lost_guard(logger)


class ListenKeyEndpointGoneError(Exception):
    """Raised when Binance listenKey REST endpoint is no longer available."""


@dataclass
class BinanceCredentials:
    """Binance API 凭证"""
    api_key: str
    secret_key: str
    testnet: bool = True


@dataclass
class RawOrderUpdate:
    """原始订单更新（来自 WS）"""
    cl_ord_id: Optional[str]
    broker_order_id: Optional[str]
    status: str
    filled_qty: float
    avg_price: Optional[float]
    exchange_ts_ms: int
    local_receive_ts_ms: int
    source: str = "WS"


@dataclass
class RawFillUpdate:
    """原始成交更新（来自 WS）"""
    cl_ord_id: Optional[str]
    trade_id: int
    exec_type: str
    side: str
    price: float
    qty: float
    commission: float
    exchange_ts_ms: int
    local_receive_ts_ms: int
    broker_order_id: Optional[str] = None
    symbol: Optional[str] = None
    exec_id: Optional[str] = None
    source: str = "WS"


@dataclass
class PrivateStreamConfig:
    """私有流配置"""
    mode: str = "auto"  # auto | ws_api_signature | legacy_listen_key
    base_url: str = "wss://stream.binance.com:9443/ws"
    rest_url: str = "https://testnet.binance.vision/api"
    ws_api_urls: List[str] = field(default_factory=list)
    proxy_url: Optional[str] = None
    open_timeout: float = 15.0
    request_timeout: float = 15.0
    ws_api_subscribe_timeout: float = 15.0
    ws_api_recv_window_ms: int = 5000
    ws_api_connect_max_attempts_per_url: int = 3
    ws_api_connect_base_delay: float = 1.0
    ws_api_connect_max_delay: float = 20.0
    time_sync_max_attempts: int = 3
    time_sync_base_delay: float = 0.5
    listen_key_ttl: int = 3600            # listenKey 有效期（秒）
    listen_key_refresh_interval: int = 1800  # 刷新间隔（秒）
    listen_key_create_max_attempts: int = 5
    listen_key_create_base_delay: float = 1.5
    reconnect_delay: float = 1.0
    max_reconnect_delay: float = 60.0
    reconnect_jitter_ratio: float = 0.2
    reconnect_jitter_max_seconds: float = 3.0
    ping_interval: float = 30.0
    pong_timeout: int = 10                # Pong 超时次数
    stale_timeout: int = 30                # 无数据超时（秒）
    pong_timeout_count: int = 2            # 连续 Pong 超时次数阈值


class PrivateStreamManager(BaseStreamFSM):
    """
    Private Stream Manager

    管理 Binance 私有 WebSocket 连接的完整生命周期。
    与 PublicStreamManager 完全独立。
    """

    def __init__(
        self,
        credentials: BinanceCredentials,
        config: Optional[PrivateStreamConfig] = None,
        stream_config: Optional[StreamConfig] = None
    ):
        private_stream_config = stream_config or StreamConfig(
            stale_timeout_seconds=30.0,
            pong_timeout_seconds=10.0,
        )
        super().__init__("PrivateStream", private_stream_config)

        self._credentials = credentials
        # 注意：不要覆盖 BaseStreamFSM._config（其类型是 StreamConfig）
        self._private_config = config or PrivateStreamConfig()
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[ws_client.WebSocketClientProtocol] = None

        self._listen_key: Optional[str] = None
        self._listen_key_expiry_ts: float = 0
        self._subscription_id: Optional[int] = None
        self._selected_mode: str = "unknown"
        self._selected_ws_url: Optional[str] = None
        self._timestamp_offset_ms: int = 0
        self._ws_api_subscribe_attempts: int = 0
        self._ws_api_subscribe_success: int = 0
        self._ws_api_subscribe_failures: int = 0
        self._last_ws_api_subscribe_error: Optional[str] = None
        self._last_ws_api_subscribe_ts: float = 0.0
        self._legacy_fallback_count: int = 0

        self._pong_timeout_counter = 0
        self._last_pong_ts = time.time()
        self._last_user_event_ts = time.time()
        self._last_data_ts = time.time()
        self._has_seen_user_event = False
        self._has_seen_execution_report = False

        self._order_update_handlers: List[Callable[[RawOrderUpdate], None]] = []
        self._fill_update_handlers: List[Callable[[RawFillUpdate], None]] = []
        self._force_resync_callback: Optional[Callable[[str], Awaitable[None]]] = None

        self._ping_task: Optional[asyncio.Task] = None
        self._stale_check_task: Optional[asyncio.Task] = None
        self._listen_key_task: Optional[asyncio.Task] = None
        self._recv_task: Optional[asyncio.Task] = None

        self._reconnect_lock = asyncio.Lock()
        self._is_aligning = False
        self._proxy_failover = get_proxy_failover_controller()

    def register_order_handler(self, handler: Callable[[RawOrderUpdate], None]) -> None:
        """注册订单更新处理器"""
        self._order_update_handlers.append(handler)

    def register_fill_handler(self, handler: Callable[[RawFillUpdate], None]) -> None:
        """注册成交更新处理器"""
        self._fill_update_handlers.append(handler)

    def set_force_resync_callback(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """设置强制对齐回调"""
        self._force_resync_callback = callback

    async def _on_start(self) -> None:
        """启动时的具体逻辑"""
        import aiohttp
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._private_config.request_timeout),
            trust_env=True,
        )

        await self._start_private_stream()

        self._ping_task = asyncio.create_task(self._ping_loop())
        self._stale_check_task = asyncio.create_task(self._stale_check_loop())
        if self._selected_mode == "legacy_listen_key":
            self._listen_key_task = asyncio.create_task(self._listen_key_keepalive_loop())

    async def _on_stop(self) -> None:
        """停止时的具体逻辑"""
        for task in [self._ping_task, self._stale_check_task, self._listen_key_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        await self._disconnect()

        if self._session:
            await self._session.close()
            self._session = None

        if self._selected_mode == "legacy_listen_key" and self._listen_key:
            await self._delete_listen_key()

    async def force_resync(self, reason: str) -> Optional[RestAlignmentSnapshot]:
        """
        强制 REST 对齐

        Args:
            reason: 对齐原因

        Returns:
            RestAlignmentSnapshot 或 None
        """
        logger.warning(f"[{self._name}] Force resync triggered: {reason}")

        if self._force_resync_callback:
            return await self._force_resync_callback(reason)
        else:
            logger.error(f"[{self._name}] No force_resync callback configured")
            return None

    async def _start_private_stream(self) -> None:
        """启动私有流（优先官方 ws-api 签名订阅，必要时回退 legacy listenKey）。"""
        mode = self._normalize_mode(self._private_config.mode)
        startup_errors: List[str] = []

        if mode in ("auto", "ws_api_signature"):
            try:
                await self._start_ws_api_signature_mode()
                return
            except Exception as e:
                startup_errors.append(f"ws_api_signature: {e}")
                logger.warning(f"[{self._name}] ws-api signature mode unavailable: {e}")
                if mode == "ws_api_signature":
                    raise
                await self._disconnect()

        await self._start_legacy_mode()
        if startup_errors:
            self._legacy_fallback_count += 1
            logger.warning(
                f"[{self._name}] Fallback to legacy listenKey mode, previous errors={startup_errors}"
            )

    @staticmethod
    def _normalize_mode(raw: str) -> str:
        mode = (raw or "auto").strip().lower()
        if mode not in ("auto", "ws_api_signature", "legacy_listen_key"):
            return "auto"
        return mode

    def _resolve_ws_api_urls(self) -> List[str]:
        """解析 ws-api 候选地址，支持环境变量覆盖。"""
        env_multi = os.environ.get("BINANCE_WS_API_URLS", "").strip()
        if env_multi:
            urls = [u.strip() for u in env_multi.split(",") if u.strip()]
            if urls:
                return urls

        env_single = os.environ.get("BINANCE_WS_API_URL", "").strip()
        if env_single:
            return [env_single]

        if self._private_config.ws_api_urls:
            return list(self._private_config.ws_api_urls)

        # 根据 rest_url 推断环境，避免跨环境 endpoint 盲试导致冷启动过慢。
        rest_url = (self._private_config.rest_url or "").lower()
        if "demo-api.binance.com" in rest_url:
            return ["wss://demo-ws-api.binance.com/ws-api/v3"]
        if "testnet.binance.vision" in rest_url:
            return ["wss://ws-api.testnet.binance.vision/ws-api/v3"]
        return ["wss://ws-api.binance.com/ws-api/v3"]

    async def _start_ws_api_signature_mode(self) -> None:
        """通过 userDataStream.subscribe.signature 启动私有流。"""
        last_error: Exception | None = None
        urls = self._resolve_ws_api_urls()
        if not urls:
            raise RuntimeError("No ws-api endpoints configured")
        if self._selected_ws_url and self._selected_ws_url in urls:
            urls = [self._selected_ws_url] + [u for u in urls if u != self._selected_ws_url]

        for url in urls:
            max_attempts = max(1, self._private_config.ws_api_connect_max_attempts_per_url)
            delay = max(0.1, self._private_config.ws_api_connect_base_delay)
            for attempt in range(1, max_attempts + 1):
                try:
                    await self._connect(url, start_receiver=False)
                    await self._sync_server_time_offset_via_ws_api()
                    await self._subscribe_ws_api_user_stream()
                    self._selected_mode = "ws_api_signature"
                    self._selected_ws_url = url
                    self._start_receive_loop()
                    logger.info(
                        f"[{self._name}] ws-api signature mode connected: url={url}, "
                        f"subscription_id={self._subscription_id}"
                    )
                    return
                except Exception as e:
                    last_error = e
                    logger.warning(
                        f"[{self._name}] ws-api endpoint failed: url={url}, "
                        f"attempt={attempt}/{max_attempts}, type={type(e).__name__}, repr={e!r}"
                    )
                    await self._disconnect()
                    if attempt >= max_attempts:
                        break
                    sleep_delay = self._apply_jitter(delay)
                    logger.info(
                        f"[{self._name}] ws-api retry in {sleep_delay:.2f}s "
                        f"(base={delay:.2f}s, url={url})"
                    )
                    await asyncio.sleep(sleep_delay)
                    delay = min(delay * 2, self._private_config.ws_api_connect_max_delay)

        assert last_error is not None
        raise last_error

    async def _start_legacy_mode(self) -> None:
        """legacy listenKey 启动模式（兼容兜底）。"""
        await self._create_listen_key_with_retry()
        await self._connect(self._build_legacy_stream_url(), start_receiver=True)
        self._selected_mode = "legacy_listen_key"
        self._selected_ws_url = self._build_legacy_stream_url()
        self._subscription_id = None

    async def _sync_server_time_offset(self) -> None:
        """同步服务器时间偏移，降低签名时间戳漂移导致的鉴权失败。"""
        url = f"{self._private_config.rest_url}/v3/time"
        max_attempts = max(1, self._private_config.time_sync_max_attempts)
        delay = max(0.1, self._private_config.time_sync_base_delay)

        for attempt in range(1, max_attempts + 1):
            proxy = self._resolve_proxy()
            try:
                async with self._session.get(url, proxy=proxy) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"status={resp.status}")
                    data = await resp.json()
                    server_ms = int(data.get("serverTime", 0))
                    if server_ms <= 0:
                        raise RuntimeError("missing serverTime")
                    local_ms = int(time.time() * 1000)
                    self._timestamp_offset_ms = server_ms - local_ms
                    logger.info(
                        f"[{self._name}] Time offset synced: {self._timestamp_offset_ms}ms"
                    )
                    self._proxy_failover.report_success(proxy)
                    return
            except Exception as e:
                self._proxy_failover.report_failure(proxy)
                if attempt >= max_attempts:
                    logger.warning(
                        f"[{self._name}] Time sync failed after {max_attempts} attempts: "
                        f"type={type(e).__name__}, repr={e!r}"
                    )
                    return
                sleep_delay = self._apply_jitter(delay)
                logger.warning(
                    f"[{self._name}] Time sync attempt {attempt}/{max_attempts} failed, "
                    f"retry in {sleep_delay:.2f}s: type={type(e).__name__}, repr={e!r}"
                )
                await asyncio.sleep(sleep_delay)
                delay = min(delay * 2, 5.0)

    async def _sync_server_time_offset_via_ws_api(self) -> None:
        """优先通过 ws-api `time` 对时，失败时回退 REST。"""
        if self._ws is None:
            await self._sync_server_time_offset()
            return

        req_id = f"private-time-{uuid.uuid4().hex[:8]}"
        try:
            request = {"id": req_id, "method": "time"}
            await self._ws.send(json.dumps(request))
            raw_resp = await asyncio.wait_for(
                self._ws.recv(),
                timeout=self._private_config.ws_api_subscribe_timeout,
            )
            resp = json.loads(raw_resp)
            status = int(resp.get("status", 0))
            if status != 200:
                raise RuntimeError(f"ws-api time status={status}, resp={resp}")
            result = resp.get("result", {}) or {}
            server_ms = int(result.get("serverTime", 0))
            if server_ms <= 0:
                raise RuntimeError(f"ws-api time missing serverTime: {resp}")
            local_ms = int(time.time() * 1000)
            self._timestamp_offset_ms = server_ms - local_ms
            logger.info(
                f"[{self._name}] Time offset synced via ws-api: {self._timestamp_offset_ms}ms"
            )
        except Exception as e:
            logger.warning(f"[{self._name}] ws-api time sync failed, fallback REST: {e}")
            await self._sync_server_time_offset()

    def _apply_jitter(self, delay: float) -> float:
        """对重试延迟施加抖动，缓解同频重连风暴。"""
        ratio = max(0.0, min(self._private_config.reconnect_jitter_ratio, 1.0))
        if ratio <= 0:
            return max(0.0, delay)
        scale = random.uniform(1.0 - ratio, 1.0 + ratio)
        jittered = max(0.0, delay * scale)
        max_jittered = delay + max(0.0, self._private_config.reconnect_jitter_max_seconds)
        return min(jittered, max_jittered)

    def _build_ws_api_signature(self, params: Dict[str, Any]) -> str:
        payload = "&".join(f"{key}={params[key]}" for key in sorted(params))
        return hmac.new(
            self._credentials.secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def _subscribe_ws_api_user_stream(self) -> None:
        """发送 userDataStream.subscribe.signature 请求并校验结果。"""
        if self._ws is None:
            raise RuntimeError("WS not connected before subscribe")

        self._ws_api_subscribe_attempts += 1
        try:
            timestamp = int(time.time() * 1000) + self._timestamp_offset_ms
            params: Dict[str, Any] = {
                "apiKey": self._credentials.api_key,
                "timestamp": timestamp,
                "recvWindow": self._private_config.ws_api_recv_window_ms,
            }
            params["signature"] = self._build_ws_api_signature(params)

            req_id = f"private-sub-{uuid.uuid4().hex[:12]}"
            request = {
                "id": req_id,
                "method": "userDataStream.subscribe.signature",
                "params": params,
            }

            await self._ws.send(json.dumps(request))
            raw_resp = await asyncio.wait_for(
                self._ws.recv(),
                timeout=self._private_config.ws_api_subscribe_timeout,
            )
            resp = json.loads(raw_resp)

            status = int(resp.get("status", 0))
            if status != 200:
                error = resp.get("error", {})
                code = error.get("code")
                msg = error.get("msg")
                raise RuntimeError(
                    "userDataStream.subscribe.signature failed: "
                    f"status={status}, code={code}, msg={msg}"
                )

            result = resp.get("result", {}) or {}
            self._subscription_id = result.get("subscriptionId")
            self._ws_api_subscribe_success += 1
            self._last_ws_api_subscribe_error = None
            self._last_ws_api_subscribe_ts = time.time()
        except Exception as e:
            self._ws_api_subscribe_failures += 1
            self._last_ws_api_subscribe_error = str(e)
            raise

    async def _create_listen_key(self) -> None:
        """创建 listenKey"""
        url = f"{self._private_config.rest_url}/v3/userDataStream"
        headers = {"X-MBX-APIKEY": self._credentials.api_key}
        proxy = self._resolve_proxy()

        try:
            async with self._session.post(
                url,
                headers=headers,
                proxy=proxy,
            ) as resp:
                self._proxy_failover.report_success(proxy)
                if resp.status == 200:
                    data = await resp.json()
                    self._listen_key = data["listenKey"]
                    self._listen_key_expiry_ts = time.time() + self._private_config.listen_key_ttl
                    logger.info(f"[{self._name}] ListenKey created: {self._listen_key[:10]}...")
                else:
                    error = await resp.text()
                    logger.error(f"[{self._name}] Failed to create listenKey: {error}")
                    if resp.status == 410:
                        raise ListenKeyEndpointGoneError(
                            "Binance listenKey endpoint returned 410 Gone. "
                            "Legacy userDataStream REST endpoints are unavailable."
                        )
                    raise Exception(f"Failed to create listenKey: {resp.status}")

        except Exception as e:
            self._proxy_failover.report_failure(proxy)
            logger.error(f"[{self._name}] ListenKey creation error: {e}")
            raise

    def _resolve_proxy(self) -> Optional[str]:
        """解析代理配置（支持主备自动切换）。"""
        return self._proxy_failover.select_proxy(self._private_config.proxy_url)

    async def _create_listen_key_with_retry(self) -> None:
        """创建 listenKey（带重试，适配 VPN 抖动场景）。"""
        max_attempts = max(1, self._private_config.listen_key_create_max_attempts)
        delay = max(0.1, self._private_config.listen_key_create_base_delay)

        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                await self._create_listen_key()
                return
            except Exception as e:
                last_error = e
                if attempt >= max_attempts:
                    break
                logger.warning(
                    f"[{self._name}] ListenKey create attempt {attempt}/{max_attempts} failed, "
                    f"retry in {delay:.1f}s: {e}"
                )
                await asyncio.sleep(self._apply_jitter(delay))
                delay = min(delay * 2, 30.0)

        assert last_error is not None
        raise last_error

    async def _refresh_listen_key(self) -> None:
        """刷新 listenKey"""
        url = f"{self._private_config.rest_url}/v3/userDataStream"
        headers = {"X-MBX-APIKEY": self._credentials.api_key}
        params = {"listenKey": self._listen_key}
        proxy = self._resolve_proxy()

        try:
            async with self._session.put(
                url,
                headers=headers,
                params=params,
                proxy=proxy,
            ) as resp:
                self._proxy_failover.report_success(proxy)
                if resp.status == 200:
                    self._listen_key_expiry_ts = time.time() + self._private_config.listen_key_ttl
                    logger.info(f"[{self._name}] ListenKey refreshed")
                else:
                    logger.warning(f"[{self._name}] ListenKey refresh failed: {resp.status}, triggering reconnect")
                    await self.reconnect()

        except Exception as e:
            self._proxy_failover.report_failure(proxy)
            logger.warning(f"[{self._name}] ListenKey refresh error: {e}, triggering reconnect")
            await self.reconnect()

    async def _delete_listen_key(self) -> None:
        """删除 listenKey"""
        if not self._listen_key:
            return

        url = f"{self._private_config.rest_url}/v3/userDataStream"
        headers = {"X-MBX-APIKEY": self._credentials.api_key}
        params = {"listenKey": self._listen_key}
        proxy = self._resolve_proxy()

        try:
            async with self._session.delete(
                url,
                headers=headers,
                params=params,
                proxy=proxy,
            ) as resp:
                self._proxy_failover.report_success(proxy)
                logger.info(f"[{self._name}] ListenKey deleted: {resp.status}")

        except Exception as e:
            self._proxy_failover.report_failure(proxy)
            logger.warning(f"[{self._name}] ListenKey deletion error: {e}")

    async def _listen_key_keepalive_loop(self) -> None:
        """listenKey 保持活动循环"""
        while self._running:
            try:
                await asyncio.sleep(self._private_config.listen_key_refresh_interval)

                if self._running and self._listen_key:
                    await self._refresh_listen_key()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self._name}] ListenKey keepalive error: {e}")

    def _build_legacy_stream_url(self) -> str:
        """构建 legacy listenKey 流 URL。"""
        if not self._listen_key:
            raise Exception("ListenKey not available")
        return f"{self._private_config.base_url}/{self._listen_key}"

    async def _connect(self, url: str, start_receiver: bool = True) -> None:
        """建立 WebSocket 连接"""
        proxy = self._resolve_proxy()
        logger.info(f"[{self._name}] Connecting to {url[:80]} (proxy={proxy})")

        try:
            self._ws = await websockets.connect(
                url,
                ping_interval=None,
                ping_timeout=self._private_config.pong_timeout,
                open_timeout=self._private_config.open_timeout,
                proxy=proxy,
            )
            self._set_state(StreamState.CONNECTED)
            self._metrics.connect_count += 1
            self._metrics.last_connect_ts = time.time()
            self._pong_timeout_counter = 0
            self._last_pong_ts = time.time()
            self._last_user_event_ts = self._last_pong_ts
            self._last_data_ts = self._last_pong_ts
            self._has_seen_user_event = False
            self._has_seen_execution_report = False
            self._proxy_failover.report_success(proxy)

            await self._trigger_event(StreamEvent.CONNECTED)
            if start_receiver:
                self._start_receive_loop()

        except Exception as e:
            self._proxy_failover.report_failure(proxy)
            logger.error(
                f"[{self._name}] Connection failed: type={type(e).__name__}, repr={e!r}, "
                f"url={url}, proxy={proxy}"
            )
            self._set_state(StreamState.ERROR)
            self._metrics.last_error = str(e)
            raise

    async def _disconnect(self) -> None:
        """断开 WebSocket 连接"""
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None

        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                logger.warning(f"[{self._name}] Error closing WS: {e}")
            finally:
                self._ws = None

        self._metrics.disconnect_count += 1
        self._metrics.last_disconnect_ts = time.time()

    def _start_receive_loop(self) -> None:
        """启动接收循环（确保最多只有一个 recv 协程）。"""
        if self._recv_task and not self._recv_task.done():
            return
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def reconnect(self) -> None:
        """重连（带互斥锁保护）"""
        async with self._reconnect_lock:
            if self._state in (StreamState.RECONNECTING, StreamState.ALIGNING):
                logger.debug(f"[{self._name}] Reconnect already in progress, skipping")
                return

            if self._check_reconnect_storm():
                self._set_state(StreamState.DEGRADED)
                await self._trigger_event(StreamEvent.ERROR, "Reconnect storm detected")
                return

            self._set_state(StreamState.RECONNECTING)
            self._record_reconnect()

            if self._recv_task and not self._recv_task.done():
                self._recv_task.cancel()
                try:
                    await self._recv_task
                except asyncio.CancelledError:
                    pass
                self._recv_task = None

            delay = self._private_config.reconnect_delay
            while self._running and delay <= self._private_config.max_reconnect_delay:
                try:
                    sleep_delay = self._apply_jitter(delay)
                    logger.info(
                        f"[{self._name}] Reconnecting in {sleep_delay:.2f}s "
                        f"(base={delay:.2f}s)"
                    )
                    await asyncio.sleep(sleep_delay)

                    if self._selected_mode == "ws_api_signature":
                        await self._start_ws_api_signature_mode()
                    else:
                        await self._create_listen_key_with_retry()
                        await self._connect(self._build_legacy_stream_url(), start_receiver=True)

                    self._set_state(StreamState.ALIGNING)
                    self._is_aligning = True

                    alignment_result = await self.force_resync("ws_reconnect")

                    if alignment_result is not None or self._selected_mode == "ws_api_signature":
                        self._set_state(StreamState.CONNECTED)
                        self._is_aligning = False
                        return
                    else:
                        logger.warning(f"[{self._name}] Alignment failed, staying in ALIGNING")

                except Exception as e:
                    logger.warning(f"[{self._name}] Reconnect failed: {e}")
                    delay *= 2
                    delay = min(delay, self._private_config.max_reconnect_delay)

            self._set_state(StreamState.ERROR)
            self._metrics.last_error = "Max reconnection attempts reached"

    async def _receive_loop(self) -> None:
        """接收消息循环（阻塞接收，连通性依赖 ping/pong 与 close 事件判定）。"""
        while self._running:
            try:
                if self._ws is None:
                    await asyncio.sleep(0.1)
                    continue
                message = await self._ws.recv()
                await self._handle_message(message)

            except asyncio.CancelledError:
                break

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"[{self._name}] Connection closed: {e}")
                await self.reconnect()
                break

            except Exception as e:
                logger.error(f"[{self._name}] Receive error: {e}")
                self._metrics.last_error = str(e)
                await self.reconnect()
                break

    async def _handle_message(self, message: str) -> None:
        """处理接收到的消息（ALIGNING 状态下丢弃）"""
        if self._is_aligning:
            logger.debug(f"[{self._name}] Discarding message during ALIGNING state")
            return

        try:
            payload = json.loads(message)

            # ws-api 控制应答
            if "status" in payload and "id" in payload and "event" not in payload:
                status = int(payload.get("status", 0))
                if status != 200:
                    logger.warning(f"[{self._name}] ws-api control response error: {payload}")
                return

            # ws-api 用户数据事件封装：{"subscriptionId":..., "event": {...}}
            if "event" in payload and isinstance(payload.get("event"), dict):
                data = payload["event"]
            else:
                # legacy listenKey 事件是扁平结构
                data = payload

            now = time.time()
            exchange_ts = data.get("E", int(now * 1000))

            event_type = data.get("e")

            if event_type in ("outboundAccountInfo", "outboundAccountPosition", "balanceUpdate"):
                self._last_user_event_ts = now
                self._has_seen_user_event = True

            elif event_type == "executionReport":
                self._last_data_ts = now
                self._has_seen_execution_report = True

                order_update = self._parse_order_update(data, exchange_ts)
                for handler in self._order_update_handlers:
                    try:
                        handler(order_update)
                    except Exception as e:
                        logger.error(f"[{self._name}] Order handler error: {e}")

                fill_update = self._parse_fill_update(data, exchange_ts)
                if fill_update is not None:
                    for handler in self._fill_update_handlers:
                        try:
                            handler(fill_update)
                        except Exception as e:
                            logger.error(f"[{self._name}] Fill handler error: {e}")

            await self._trigger_event(StreamEvent.DATA_RECEIVED, payload)

        except json.JSONDecodeError as e:
            logger.warning(f"[{self._name}] JSON decode error: {e}")

    def _parse_order_update(self, data: Dict, exchange_ts: int) -> RawOrderUpdate:
        """解析订单更新"""
        raw_broker_order_id = data.get("i")
        broker_order_id = str(raw_broker_order_id) if raw_broker_order_id is not None else None
        filled_qty = self._safe_float(data.get("z", 0.0))
        avg_price = self._compute_avg_price(data, filled_qty)

        return RawOrderUpdate(
            cl_ord_id=data.get("c"),
            broker_order_id=broker_order_id,
            status=data.get("X", "UNKNOWN"),
            filled_qty=filled_qty,
            avg_price=avg_price,
            exchange_ts_ms=exchange_ts,
            local_receive_ts_ms=int(time.time() * 1000),
            source="WS"
        )

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _compute_avg_price(self, data: Dict[str, Any], filled_qty: float) -> Optional[float]:
        """基于 executionReport 累计成交信息计算平均价。"""
        if filled_qty <= 0:
            return None

        cum_quote_qty = self._safe_float(data.get("Z", 0.0))
        if cum_quote_qty > 0:
            return cum_quote_qty / filled_qty

        last_fill_price = self._safe_float(data.get("L", 0.0))
        if last_fill_price > 0:
            return last_fill_price
        return None

    @staticmethod
    def _parse_int(value: Any, default: int = 0) -> int:
        if isinstance(value, int):
            return value
        text = str(value or "").strip()
        if not text:
            return default
        try:
            return int(text)
        except ValueError:
            digits = "".join(ch for ch in text if ch.isdigit())
            return int(digits) if digits else default

    def _parse_fill_update(self, data: Dict, exchange_ts: int) -> Optional[RawFillUpdate]:
        """解析成交更新"""
        try:
            exec_type = str(data.get("x") or "")
            if exec_type != "TRADE":
                return None

            fill_qty = self._safe_float(data.get("l", 0.0))
            if fill_qty <= 0:
                return None

            trade_id = self._parse_int(data.get("t"), default=0)
            raw_exec_id = data.get("I")
            exec_id: Optional[str] = None
            if raw_exec_id is not None and str(raw_exec_id).strip():
                exec_id = str(raw_exec_id).strip()
            elif trade_id > 0:
                exec_id = str(trade_id)

            raw_broker_order_id = data.get("i")
            broker_order_id = str(raw_broker_order_id) if raw_broker_order_id is not None else None

            return RawFillUpdate(
                cl_ord_id=data.get("c"),
                trade_id=trade_id,
                exec_type=exec_type,
                side=data.get("S"),
                price=self._safe_float(data.get("L", 0.0)),
                qty=fill_qty,
                commission=self._safe_float(data.get("n", 0.0)),
                exchange_ts_ms=exchange_ts,
                local_receive_ts_ms=int(time.time() * 1000),
                broker_order_id=broker_order_id,
                symbol=data.get("s"),
                exec_id=exec_id,
                source="WS"
            )
        except (ValueError, TypeError, KeyError) as e:
            logger.warning(f"[{self._name}] Failed to parse fill update: {e}")
            return None

    async def _ping_loop(self) -> None:
        """Ping 循环"""
        while self._running:
            try:
                await asyncio.sleep(self._private_config.ping_interval)

                if self._ws and self._state == StreamState.CONNECTED:
                    pong_waiter = await self._ws.ping()
                    await asyncio.wait_for(
                        pong_waiter,
                        timeout=float(self._private_config.pong_timeout),
                    )
                    self.on_pong()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[{self._name}] Ping error: {e}")

    async def _stale_check_loop(self) -> None:
        """静默检测循环"""
        while self._running:
            try:
                await asyncio.sleep(5)

                now = time.time()
                time_since_data = now - self._last_data_ts
                time_since_user_event = now - self._last_user_event_ts
                time_since_pong = now - self._last_pong_ts

                max_pong_lag = float(
                    self._private_config.ping_interval
                    + self._private_config.pong_timeout * self._private_config.pong_timeout_count
                )
                if time_since_pong > max_pong_lag:
                    logger.warning(
                        f"[{self._name}] Pong timeout: "
                        f"{time_since_pong:.1f}s since last pong"
                    )
                    self._set_state(StreamState.STALE_DATA)
                    self._metrics.stale_count += 1
                    self._pong_timeout_counter = 0
                    await self._trigger_event(StreamEvent.STALE_DETECTED)
                    await self.reconnect()
                    continue

                # 仅在真正观察到 executionReport 后才做 data stale 判定，避免空闲账户误判重连。
                should_check_data_stale = (
                    self._selected_mode != "ws_api_signature"
                    and self._has_seen_execution_report
                )
                if should_check_data_stale and time_since_data > self._private_config.stale_timeout:
                    logger.warning(
                        f"[{self._name}] Stale data: {time_since_data:.1f}s since last trade"
                    )
                    self._set_state(StreamState.STALE_DATA)
                    self._metrics.stale_count += 1
                    await self._trigger_event(StreamEvent.STALE_DETECTED)
                    await self.force_resync("stale_data")
                    await self.reconnect()

                should_check_user_event_stale = (
                    self._selected_mode != "ws_api_signature"
                    and self._has_seen_user_event
                )
                if should_check_user_event_stale and time_since_user_event > (self._private_config.stale_timeout * 2):
                    logger.warning(
                        f"[{self._name}] User event timeout: {time_since_user_event:.1f}s"
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self._name}] Stale check error: {e}")

    def on_pong(self, data: bytes = b"") -> None:
        """处理 Pong 响应"""
        self._pong_timeout_counter = 0
        self._last_pong_ts = time.time()

    def get_status(self) -> Dict[str, Any]:
        """扩展状态：增加私有流模式与订阅质量指标，便于监控页直连展示。"""
        base = super().get_status()
        now = time.time()
        ws_api_attempts = self._ws_api_subscribe_attempts
        ws_api_success_rate = (
            round(self._ws_api_subscribe_success / ws_api_attempts, 4)
            if ws_api_attempts > 0
            else None
        )
        proxy_state = self._proxy_failover.get_state(self._private_config.proxy_url)
        base["private_runtime"] = {
            "selected_mode": self._selected_mode,
            "selected_ws_url": self._selected_ws_url,
            "subscription_id": self._subscription_id,
            "legacy_fallback_count": self._legacy_fallback_count,
            "timestamp_offset_ms": self._timestamp_offset_ms,
            "proxy_configured": bool(proxy_state.get("candidates")),
            "active_proxy": proxy_state.get("active_proxy"),
            "ws_api_subscribe_attempts": ws_api_attempts,
            "ws_api_subscribe_success": self._ws_api_subscribe_success,
            "ws_api_subscribe_failures": self._ws_api_subscribe_failures,
            "ws_api_subscribe_success_rate": ws_api_success_rate,
            "last_ws_api_subscribe_error": self._last_ws_api_subscribe_error,
            "last_ws_api_subscribe_ts_ms": (
                int(self._last_ws_api_subscribe_ts * 1000)
                if self._last_ws_api_subscribe_ts > 0
                else None
            ),
            "last_data_age_seconds": max(0.0, round(now - self._last_data_ts, 3)),
            "last_pong_age_seconds": max(0.0, round(now - self._last_pong_ts, 3)),
        }
        return base

    def get_listen_key(self) -> Optional[str]:
        """获取当前 listenKey"""
        return self._listen_key
