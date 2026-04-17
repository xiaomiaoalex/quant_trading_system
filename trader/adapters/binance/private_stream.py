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
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Any, Callable, Awaitable

if TYPE_CHECKING:
    import aiohttp

import websockets
import websockets.client as ws_client

from trader.adapters.binance.stream_base import (
    BaseStreamFSM, StreamConfig, StreamState, StreamEvent
)
from trader.adapters.binance.rest_alignment import RestAlignmentSnapshot


logger = logging.getLogger(__name__)


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
    cl_ord_id: str
    trade_id: int
    exec_type: str
    side: str
    price: float
    qty: float
    commission: float
    exchange_ts_ms: int
    local_receive_ts_ms: int
    source: str = "WS"


@dataclass
class PrivateStreamConfig:
    """私有流配置"""
    base_url: str = "wss://stream.binance.com:9443/ws"
    rest_url: str = "https://testnet.binance.vision/api"
    listen_key_ttl: int = 3600            # listenKey 有效期（秒）
    listen_key_refresh_interval: int = 1800  # 刷新间隔（秒）
    reconnect_delay: float = 1.0
    max_reconnect_delay: float = 60.0
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
        self._config = config or PrivateStreamConfig()
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[ws_client.WebSocketClientProtocol] = None

        self._listen_key: Optional[str] = None
        self._listen_key_expiry_ts: float = 0

        self._pong_timeout_counter = 0
        self._last_user_event_ts = time.time()
        self._last_data_ts = time.time()

        self._order_update_handlers: List[Callable[[RawOrderUpdate], None]] = []
        self._fill_update_handlers: List[Callable[[RawFillUpdate], None]] = []
        self._force_resync_callback: Optional[Callable[[str], Awaitable[None]]] = None

        self._ping_task: Optional[asyncio.Task] = None
        self._stale_check_task: Optional[asyncio.Task] = None
        self._listen_key_task: Optional[asyncio.Task] = None
        self._recv_task: Optional[asyncio.Task] = None

        self._reconnect_lock = asyncio.Lock()
        self._is_aligning = False

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
        self._session = aiohttp.ClientSession()

        await self._create_listen_key()
        await self._connect()

        self._ping_task = asyncio.create_task(self._ping_loop())
        self._stale_check_task = asyncio.create_task(self._stale_check_loop())
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

        if self._listen_key:
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

    async def _create_listen_key(self) -> None:
        """创建 listenKey"""
        url = f"{self._config.rest_url}/v3/userDataStream"
        headers = {"X-MBX-APIKEY": self._credentials.api_key}

        try:
            async with self._session.post(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._listen_key = data["listenKey"]
                    self._listen_key_expiry_ts = time.time() + self._config.listen_key_ttl
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
            logger.error(f"[{self._name}] ListenKey creation error: {e}")
            raise

    async def _refresh_listen_key(self) -> None:
        """刷新 listenKey"""
        url = f"{self._config.rest_url}/v3/userDataStream"
        headers = {"X-MBX-APIKEY": self._credentials.api_key}
        params = {"listenKey": self._listen_key}

        try:
            async with self._session.put(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    self._listen_key_expiry_ts = time.time() + self._config.listen_key_ttl
                    logger.info(f"[{self._name}] ListenKey refreshed")
                else:
                    logger.warning(f"[{self._name}] ListenKey refresh failed: {resp.status}, triggering reconnect")
                    await self.reconnect()

        except Exception as e:
            logger.warning(f"[{self._name}] ListenKey refresh error: {e}, triggering reconnect")
            await self.reconnect()

    async def _delete_listen_key(self) -> None:
        """删除 listenKey"""
        if not self._listen_key:
            return

        url = f"{self._config.rest_url}/v3/userDataStream"
        headers = {"X-MBX-APIKEY": self._credentials.api_key}
        params = {"listenKey": self._listen_key}

        try:
            async with self._session.delete(url, headers=headers, params=params) as resp:
                logger.info(f"[{self._name}] ListenKey deleted: {resp.status}")

        except Exception as e:
            logger.warning(f"[{self._name}] ListenKey deletion error: {e}")

    async def _listen_key_keepalive_loop(self) -> None:
        """listenKey 保持活动循环"""
        while self._running:
            try:
                await asyncio.sleep(self._config.listen_key_refresh_interval)

                if self._running and self._listen_key:
                    await self._refresh_listen_key()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self._name}] ListenKey keepalive error: {e}")

    def _build_stream_url(self) -> str:
        """构建流 URL"""
        if not self._listen_key:
            raise Exception("ListenKey not available")
        return f"{self._config.base_url}/{self._listen_key}"

    async def _connect(self) -> None:
        """建立 WebSocket 连接"""
        url = self._build_stream_url()
        logger.info(f"[{self._name}] Connecting to {url[:50]}...")

        try:
            self._ws = await websockets.connect(
                url,
                ping_interval=None,
                ping_timeout=self._config.pong_timeout,
                pong_callback=self.on_pong,
            )
            self._set_state(StreamState.CONNECTED)
            self._metrics.connect_count += 1
            self._metrics.last_connect_ts = time.time()
            self._pong_timeout_counter = 0

            await self._trigger_event(StreamEvent.CONNECTED)
            asyncio.create_task(self._receive_loop())

        except Exception as e:
            logger.error(f"[{self._name}] Connection failed: {e}")
            self._set_state(StreamState.ERROR)
            self._metrics.last_error = str(e)
            raise

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

            delay = self._config.reconnect_delay
            while self._running and delay <= self._config.max_reconnect_delay:
                try:
                    logger.info(f"[{self._name}] Reconnecting in {delay}s...")
                    await asyncio.sleep(delay)

                    await self._create_listen_key()
                    await self._connect()

                    self._set_state(StreamState.ALIGNING)
                    self._is_aligning = True

                    alignment_result = await self.force_resync("ws_reconnect")

                    if alignment_result is not None:
                        self._set_state(StreamState.CONNECTED)
                        self._is_aligning = False
                        self._recv_task = asyncio.create_task(self._receive_loop())
                        return
                    else:
                        logger.warning(f"[{self._name}] Alignment failed, staying in ALIGNING")

                except Exception as e:
                    logger.warning(f"[{self._name}] Reconnect failed: {e}")
                    delay *= 2
                    delay = min(delay, self._config.max_reconnect_delay)

            self._set_state(StreamState.ERROR)
            self._metrics.last_error = "Max reconnection attempts reached"

    async def _receive_loop(self) -> None:
        """接收消息循环（带超时检测）"""
        stale_timeout = self._config.stale_timeout

        while self._running:
            try:
                message = await asyncio.wait_for(
                    self._ws.recv(),
                    timeout=stale_timeout
                )
                await self._handle_message(message)

            except asyncio.TimeoutError:
                logger.warning(f"[{self._name}] Receive timeout after {stale_timeout}s, triggering STALE")
                self._set_state(StreamState.STALE_DATA)
                self._metrics.stale_count += 1
                await self._trigger_event(StreamEvent.STALE_DETECTED)
                await self.reconnect()
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
            data = json.loads(message)
            now = time.time()
            exchange_ts = data.get("E", int(now * 1000))

            event_type = data.get("e")

            if event_type == "outboundAccountInfo":
                self._last_user_event_ts = now

            elif event_type == "executionReport":
                self._last_data_ts = now

                order_update = self._parse_order_update(data, exchange_ts)
                for handler in self._order_update_handlers:
                    try:
                        handler(order_update)
                    except Exception as e:
                        logger.error(f"[{self._name}] Order handler error: {e}")

                if data.get("x") in ["TRADE", "NEW"]:
                    fill_update = self._parse_fill_update(data, exchange_ts)
                    if fill_update is not None:
                        for handler in self._fill_update_handlers:
                            try:
                                handler(fill_update)
                            except Exception as e:
                                logger.error(f"[{self._name}] Fill handler error: {e}")

            await self._trigger_event(StreamEvent.DATA_RECEIVED, data)

        except json.JSONDecodeError as e:
            logger.warning(f"[{self._name}] JSON decode error: {e}")

    def _parse_order_update(self, data: Dict, exchange_ts: int) -> RawOrderUpdate:
        """解析订单更新"""
        return RawOrderUpdate(
            cl_ord_id=data.get("c"),
            broker_order_id=data.get("t"),
            status=data.get("X", "UNKNOWN"),
            filled_qty=float(data.get("z", 0)),
            avg_price=float(data.get("L", 0)) if data.get("L") else None,
            exchange_ts_ms=exchange_ts,
            local_receive_ts_ms=int(time.time() * 1000),
            source="WS"
        )

    def _parse_fill_update(self, data: Dict, exchange_ts: int) -> Optional[RawFillUpdate]:
        """解析成交更新"""
        try:
            raw_trade_id = data.get("t", 0)
            trade_id = 0
            if isinstance(raw_trade_id, int):
                trade_id = raw_trade_id
            else:
                raw_text = str(raw_trade_id)
                try:
                    trade_id = int(raw_text)
                except ValueError:
                    digits = "".join(ch for ch in raw_text if ch.isdigit())
                    trade_id = int(digits) if digits else 0

            return RawFillUpdate(
                cl_ord_id=data.get("c"),
                trade_id=trade_id,
                exec_type=data.get("x"),
                side=data.get("S"),
                price=float(data.get("p", 0)),
                qty=float(data.get("q", 0)),
                commission=float(data.get("n", 0)),
                exchange_ts_ms=exchange_ts,
                local_receive_ts_ms=int(time.time() * 1000),
                source="WS"
            )
        except (ValueError, TypeError, KeyError) as e:
            logger.warning(f"[{self._name}] Failed to parse fill update: {e}")
            return None

    async def _ping_loop(self) -> None:
        """Ping 循环"""
        while self._running:
            try:
                await asyncio.sleep(30)

                if self._ws and self._state == StreamState.CONNECTED:
                    await self._ws.ping()

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

                self._pong_timeout_counter += 1

                time_since_data = now - self._last_data_ts
                time_since_user_event = now - self._last_user_event_ts

                if self._pong_timeout_counter > self._config.pong_timeout_count:
                    logger.warning(
                        f"[{self._name}] Pong timeout: "
                        f"{self._pong_timeout_counter} consecutive timeouts"
                    )
                    self._set_state(StreamState.STALE_DATA)
                    self._metrics.stale_count += 1
                    self._pong_timeout_counter = 0
                    await self._trigger_event(StreamEvent.STALE_DETECTED)
                    await self.reconnect()
                    continue

                if time_since_data > self._config.stale_timeout:
                    logger.warning(
                        f"[{self._name}] Stale data: {time_since_data:.1f}s since last trade"
                    )
                    self._set_state(StreamState.STALE_DATA)
                    self._metrics.stale_count += 1
                    await self._trigger_event(StreamEvent.STALE_DETECTED)
                    await self.force_resync("stale_data")
                    await self.reconnect()

                if time_since_user_event > (self._config.stale_timeout * 2):
                    logger.warning(
                        f"[{self._name}] User event timeout: {time_since_user_event:.1f}s"
                    )
                    self._pong_timeout_counter += 1

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self._name}] Stale check error: {e}")

    def on_pong(self, data: bytes = b"") -> None:
        """处理 Pong 响应"""
        self._pong_timeout_counter = 0

    def get_listen_key(self) -> Optional[str]:
        """获取当前 listenKey"""
        return self._listen_key
