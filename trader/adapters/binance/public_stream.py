"""
Public Stream Manager - Binance Public WebSocket Stream
=========================================================
管理 Binance 公有 WebSocket 流（K线、深度、行情等）。

特性：
- 独立 FSM 状态机
- 自动重连
- 心跳检测
- 数据解析和元数据打标
"""
import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Any, Callable
from urllib.parse import urlencode

if TYPE_CHECKING:
    import aiohttp

import websockets
import websockets.client as ws_client

from trader.adapters.binance.stream_base import (
    BaseStreamFSM, StreamConfig, StreamState, StreamEvent
)
from trader.adapters.binance.websockets_compat import install_connection_lost_guard


logger = logging.getLogger(__name__)
install_connection_lost_guard(logger)


@dataclass
class MarketEvent:
    """市场事件"""
    stream: str
    event_type: str
    data: Dict[str, Any]
    exchange_ts_ms: int
    local_receive_ts_ms: int
    source: str = "WS"


@dataclass
class PublicStreamConfig:
    """公有流配置"""
    base_url: str = "wss://stream.binance.com:9443/ws"
    reconnect_delay: float = 1.0
    max_reconnect_delay: float = 60.0
    reconnect_jitter_ratio: float = 0.2
    reconnect_jitter_max_seconds: float = 3.0
    ping_interval: float = 30.0
    stale_timeout: float = 30.0
    open_timeout: float = 15.0
    initial_connect_max_attempts: int = 3
    initial_connect_base_delay: float = 1.0
    proxy_url: Optional[str] = None
    streams: List[str] = field(default_factory=lambda: ["btcusdt@trade", "btcusdt@kline_1m"])


class PublicStreamManager(BaseStreamFSM):
    """
    Public Stream Manager

    管理 Binance 公有 WebSocket 连接的完整生命周期。
    与 PrivateStreamManager 完全独立。
    """

    def __init__(
        self,
        config: Optional[PublicStreamConfig] = None,
        stream_config: Optional[StreamConfig] = None
    ):
        fsm_config = stream_config or StreamConfig()
        super().__init__("PublicStream", fsm_config)

        # 注意：不要覆盖 BaseStreamFSM._config（其类型是 StreamConfig）
        # 否则基类重连风暴检测会读取不到字段（max_reconnect_per_window/reconnect_window_seconds）。
        self._public_config = config or PublicStreamConfig()
        self._ws: Optional[ws_client.WebSocketClientProtocol] = None
        self._session: Optional[aiohttp.ClientSession] = None

        self._last_pong_ts = time.time()
        self._last_data_ts = time.time()

        self._market_event_handlers: List[Callable[[MarketEvent], None]] = []

        self._ping_task: Optional[asyncio.Task] = None
        self._stale_check_task: Optional[asyncio.Task] = None

    def register_market_handler(self, handler: Callable[[MarketEvent], None]) -> None:
        """注册市场事件处理器"""
        self._market_event_handlers.append(handler)

    def _build_stream_url(self) -> str:
        """构建流 URL"""
        streams = "/".join(self._public_config.streams)
        return f"{self._public_config.base_url}/{streams}"

    def _resolve_proxy(self) -> str | bool | None:
        """解析代理配置，优先级：config > BINANCE_PROXY_URL > BINANCE_PROXY > HTTPS_PROXY > HTTP_PROXY > ALL_PROXY。"""
        explicit = self._public_config.proxy_url
        if explicit:
            return explicit
        env_proxy = (
            os.environ.get("BINANCE_PROXY_URL")
            or os.environ.get("BINANCE_PROXY")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("ALL_PROXY")
        )
        return env_proxy or None

    def _apply_jitter(self, delay: float) -> float:
        """对重连延迟施加抖动，避免多个连接器同频重连。"""
        ratio = max(0.0, min(self._public_config.reconnect_jitter_ratio, 1.0))
        if ratio <= 0:
            return max(0.0, delay)
        scale = random.uniform(1.0 - ratio, 1.0 + ratio)
        jittered = max(0.0, delay * scale)
        max_jittered = delay + max(0.0, self._public_config.reconnect_jitter_max_seconds)
        return min(jittered, max_jittered)

    async def _on_start(self) -> None:
        """启动时的具体逻辑"""
        import aiohttp
        self._session = aiohttp.ClientSession()
        await self._connect_with_retry()

        self._ping_task = asyncio.create_task(self._ping_loop())
        self._stale_check_task = asyncio.create_task(self._stale_check_loop())

    async def _on_stop(self) -> None:
        """停止时的具体逻辑"""
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

        if self._stale_check_task:
            self._stale_check_task.cancel()
            try:
                await self._stale_check_task
            except asyncio.CancelledError:
                pass

        await self._disconnect()

        if self._session:
            await self._session.close()
            self._session = None

    async def _connect(self) -> None:
        """建立 WebSocket 连接"""
        url = self._build_stream_url()
        proxy = self._resolve_proxy()
        logger.info(f"[{self._name}] Connecting to {url} (proxy={proxy})")

        try:
            self._ws = await websockets.connect(
                url,
                ping_interval=None,
                ping_timeout=self._public_config.ping_interval,
                open_timeout=self._public_config.open_timeout,
                proxy=proxy,
            )
            self._set_state(StreamState.CONNECTED)
            self._metrics.connect_count += 1
            self._metrics.last_connect_ts = time.time()
            self._last_pong_ts = time.time()
            self._last_data_ts = time.time()

            await self._trigger_event(StreamEvent.CONNECTED)
            asyncio.create_task(self._receive_loop())

        except Exception as e:
            logger.error(
                f"[{self._name}] Connection failed: type={type(e).__name__}, repr={e!r}, "
                f"url={url}, proxy={proxy}"
            )
            self._set_state(StreamState.ERROR)
            self._metrics.last_error = str(e)
            raise

    async def _connect_with_retry(self) -> None:
        """启动阶段连接重试，提升弱网场景下首连成功率。"""
        max_attempts = max(1, self._public_config.initial_connect_max_attempts)
        base_delay = max(0.1, self._public_config.initial_connect_base_delay)
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                await self._connect()
                return
            except Exception as e:
                last_error = e
                if attempt >= max_attempts:
                    break
                delay = self._apply_jitter(base_delay * (2 ** (attempt - 1)))
                logger.warning(
                    f"[{self._name}] Initial connect attempt {attempt}/{max_attempts} failed, "
                    f"retry in {delay:.2f}s: type={type(e).__name__}, repr={e!r}"
                )
                await asyncio.sleep(delay)

        if last_error is not None:
            raise last_error

    async def _disconnect(self) -> None:
        """断开 WebSocket 连接"""
        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                logger.warning(f"[{self._name}] Error closing WS: {e}")
            finally:
                self._ws = None

        self._metrics.disconnect_count += 1
        self._metrics.last_disconnect_ts = time.time()

    async def reconnect(self) -> None:
        """重连"""
        if self._check_reconnect_storm():
            self._set_state(StreamState.DEGRADED)
            await self._trigger_event(StreamEvent.ERROR, "Reconnect storm detected")
            return

        self._set_state(StreamState.RECONNECTING)
        self._record_reconnect()

        delay = self._public_config.reconnect_delay
        while self._running and delay <= self._public_config.max_reconnect_delay:
            try:
                sleep_delay = self._apply_jitter(delay)
                logger.info(
                    f"[{self._name}] Reconnecting in {sleep_delay:.2f}s "
                    f"(base={delay:.2f}s)"
                )
                await asyncio.sleep(sleep_delay)

                await self._connect()
                return

            except Exception as e:
                logger.warning(f"[{self._name}] Reconnect failed: {e}")
                delay *= 2
                delay = min(delay, self._public_config.max_reconnect_delay)

        self._set_state(StreamState.ERROR)
        self._metrics.last_error = "Max reconnection attempts reached"

    async def _receive_loop(self) -> None:
        """接收消息循环"""
        try:
            async for message in self._ws:
                if not self._running:
                    break

                await self._handle_message(message)

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"[{self._name}] Connection closed: {e}")
            await self.reconnect()

        except Exception as e:
            logger.error(f"[{self._name}] Receive error: {e}")
            self._metrics.last_error = str(e)
            await self.reconnect()

    async def _handle_message(self, message: str) -> None:
        """处理接收到的消息"""
        try:
            data = json.loads(message)
            now = time.time()

            if "e" in data:
                event_type = data["e"]
                exchange_ts = data.get("E", int(now * 1000))
            else:
                event_type = "unknown"
                exchange_ts = int(now * 1000)

            event = MarketEvent(
                stream=data.get("stream", ""),
                event_type=event_type,
                data=data,
                exchange_ts_ms=exchange_ts,
                local_receive_ts_ms=int(now * 1000),
                source="WS"
            )

            self._last_data_ts = now
            self._metrics.last_data_ts = now

            for handler in self._market_event_handlers:
                try:
                    handler(event)
                except Exception as e:
                    logger.error(f"[{self._name}] Handler error: {e}")

            await self._trigger_event(StreamEvent.DATA_RECEIVED, event)

        except json.JSONDecodeError as e:
            logger.warning(f"[{self._name}] JSON decode error: {e}")

    async def _ping_loop(self) -> None:
        """Ping 循环"""
        while self._running:
            try:
                await asyncio.sleep(self._public_config.ping_interval)

                if self._ws and self._state == StreamState.CONNECTED:
                    pong_waiter = await self._ws.ping()
                    await asyncio.wait_for(
                        pong_waiter,
                        timeout=self._public_config.stale_timeout,
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
                time_since_pong = now - self._last_pong_ts

                if time_since_data > self._public_config.stale_timeout:
                    logger.warning(
                        f"[{self._name}] Stale data detected: "
                        f"{time_since_data:.1f}s since last data"
                    )
                    self._set_state(StreamState.STALE_DATA)
                    self._metrics.stale_count += 1
                    await self._trigger_event(StreamEvent.STALE_DETECTED)
                    await self.reconnect()

                if time_since_pong > (
                    self._public_config.ping_interval + self._public_config.stale_timeout
                ):
                    logger.warning(
                        f"[{self._name}] Pong timeout: "
                        f"{time_since_pong:.1f}s since last pong"
                    )
                    await self.reconnect()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self._name}] Stale check error: {e}")

    def on_pong(self, data: bytes = b"") -> None:
        """处理 Pong 响应"""
        self._last_pong_ts = time.time()
