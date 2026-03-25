"""
Binance WebSocket Announcement Source - Binance WebSocket 公告源
================================================================
使用 Binance WebSocket 作为主数据源采集公告。

关键实现:
- 连接: wss://stream.binance.com:9443/ws/com_announcement_en
- 订阅报文: {"command": "SUBSCRIBE", "value": "com_announcement_en"}
- 核心方法: connect, disconnect, subscribe, recv_one, recv_async_iterator, ping, reconnect
- 不强制实现 fetch_initial
"""
import asyncio
import json
import logging
from contextlib import nullcontext
from typing import Optional, AsyncIterator
import websockets

from trader.adapters.announcements.models import RawAnnouncement

logger = logging.getLogger(__name__)


class BinanceWsAnnouncementSource:
    """Binance WebSocket 公告数据源
    
    简化实现，无 FSM，仅包含 5 个核心方法:
    - connect(): 建立连接
    - disconnect(): 断开连接
    - subscribe(): 订阅主题
    - recv_one(): 接收单条消息（阻塞）
    - recv_async_iterator(): 异步迭代器版本
    - ping(): 心跳检测
    - reconnect(): 重连机制
    
    不强制实现 fetch_initial。
    """
    
    # Binance WebSocket 端点
    # URL: wss://api.binance.com/sapi/wss?topic=com_announcement_en
    WS_URL = "wss://api.binance.com/sapi/wss"
    TOPIC = "com_announcement_en"
    
    def __init__(
        self,
        ws_url: Optional[str] = None,
        ping_interval: float = 30.0,
        reconnect_delay: float = 5.0,
        max_reconnect_attempts: int = 3,
    ):
        """
        初始化 WebSocket 公告源
        
        Args:
            ws_url: WebSocket URL（可选，默认使用 WS_URL）
            ping_interval: 心跳间隔（秒）
            reconnect_delay: 重连延迟（秒）
            max_reconnect_attempts: 最大重连次数
        """
        self._ws_url = ws_url or self.WS_URL
        self._ping_interval = ping_interval
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_attempts = max_reconnect_attempts
        
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._subscribed = False
        self._recv_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._should_stop = False
    
    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._connected and self._ws is not None
    
    @property
    def is_subscribed(self) -> bool:
        """是否已订阅"""
        return self._subscribed
    
    async def connect(self) -> None:
        """建立 WebSocket 连接 (实验 v2: URL 带 topic)"""
        if self._connected:
            logger.warning("WS_SOURCE_ALREADY_CONNECTED")
            return
        
        try:
            # 实验 v2: URL 带 topic 参数
            ws_url_with_topic = f"{self._ws_url}?topic={self.TOPIC}"
            self._ws = await websockets.connect(
                ws_url_with_topic,
                ping_interval=self._ping_interval,
            )
            self._connected = True
            self._should_stop = False
            logger.info("WS_SOURCE_CONNECTED", extra={"url": ws_url_with_topic})
        except Exception as e:
            logger.error("WS_SOURCE_CONNECT_ERROR", extra={"error": str(e)})
            self._connected = False
            raise
    
    async def disconnect(self) -> None:
        """断开 WebSocket 连接"""
        self._should_stop = True
        
        # 取消接收任务
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        
        # 取消心跳任务
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
        
        # 关闭 WebSocket
        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                logger.warning("WS_SOURCE_CLOSE_ERROR", extra={"error": str(e)})
            finally:
                self._ws = None
        
        self._connected = False
        self._subscribed = False
        logger.info("WS_SOURCE_DISCONNECTED")
    
    async def subscribe(self) -> None:
        """订阅公告主题 (实验 v2: 不发送 SUBSCRIBE，topic 在 URL 中)
        
        由于 topic 已在 connect() 时通过 URL 参数传递，
        此方法仅标记订阅状态，不发送额外报文。
        """
        if not self._connected or not self._ws:
            raise RuntimeError("WebSocket not connected")
        
        if self._subscribed:
            logger.warning("WS_SOURCE_ALREADY_SUBSCRIBED")
            return
        
        # 实验 v2: 不发送 SUBSCRIBE 报文，topic 已在 URL 中
        self._subscribed = True
        logger.info("WS_SOURCE_SUBSCRIBED", extra={"topic": self.TOPIC, "method": "url_param"})
    
    async def unsubscribe(self) -> None:
        """取消订阅"""
        if not self._connected or not self._ws:
            return
        
        try:
            unsubscribe_msg = {
                "command": "UNSUBSCRIBE",
                "value": self.TOPIC,
            }
            await self._ws.send(json.dumps(unsubscribe_msg))
            self._subscribed = False
            logger.info("WS_SOURCE_UNSUBSCRIBED", extra={"topic": self.TOPIC})
        except Exception as e:
            logger.warning("WS_SOURCE_UNSUBSCRIBE_ERROR", extra={"error": str(e)})
    
    async def recv_one(self, timeout: Optional[float] = None) -> RawAnnouncement:
        """接收单条公告消息（阻塞）
        
        从 WebSocket 接收一条消息并转换为 RawAnnouncement。
        此方法会阻塞，直到收到一条有效消息、超时或连接关闭。
        
        Binance WS 消息格式:
        - COMMAND: {"type": "COMMAND", "data": "SUCCESS", "subType": "SUBSCRIBE", "code": "00000000"}
        - DATA: {"type": "DATA", "topic": "com_announcement_en", "data": "{\"catalogId\":161,...}"}
        
        Args:
            timeout: 超时时间（秒），默认 None（无限等待）
            
        Returns:
            RawAnnouncement 实例
            
        Raises:
            RuntimeError: 如果未连接、未订阅或超时
        """
        if not self._connected or not self._ws:
            raise RuntimeError("WebSocket not connected")
        
        if not self._subscribed:
            raise RuntimeError("WebSocket not subscribed")
        
        # 最大跳过无效消息次数，防止无限循环
        max_skips = 100
        skip_count = 0
        
        try:
            async with asyncio.timeout(timeout) if timeout else nullcontext():
                async for message in self._ws:
                    if self._should_stop:
                        break
                    
                    try:
                        outer = json.loads(message)
                        msg_type = outer.get("type")
                        
                        # COMMAND 响应 (订阅确认等)
                        if msg_type == "COMMAND":
                            data_field = outer.get("data", "")
                            sub_type = outer.get("subType", "")
                            code = outer.get("code", "")
                            logger.info(
                                "WS_SOURCE_COMMAND",
                                extra={"data": data_field, "subType": sub_type, "code": code}
                            )
                            continue
                        
                        # DATA 消息 (公告数据)
                        if msg_type == "DATA":
                            inner_data = outer.get("data")
                            if isinstance(inner_data, str):
                                # data 是 JSON 字符串，需要二次解析
                                inner = json.loads(inner_data)
                            elif isinstance(inner_data, dict):
                                # 兼容：data 已经是 dict
                                inner = inner_data
                            else:
                                logger.warning(
                                    "WS_SOURCE_UNEXPECTED_DATA_TYPE",
                                    extra={"data_type": type(inner_data).__name__}
                                )
                                continue
                            
                            ann = RawAnnouncement.from_ws_message(inner)
                            if ann.title or ann.body:
                                logger.debug(
                                    "WS_SOURCE_RECEIVED",
                                    extra={"title": (ann.title or "")[:40]}
                                )
                                return ann
                            else:
                                # 空消息，记录 debug 并计数
                                skip_count += 1
                                if skip_count >= max_skips:
                                    logger.warning(
                                        "WS_SOURCE_TOO_MANY_EMPTY_MESSAGES",
                                        extra={"skip_count": skip_count}
                                    )
                                    raise RuntimeError(
                                        f"recv_one: received {skip_count} empty messages, aborting"
                                    )
                                logger.debug(
                                    "WS_SOURCE_EMPTY_MESSAGE_SKIPPED",
                                    extra={"skip_count": skip_count}
                                )
                                continue
                        
                        # 未知类型 -> warning + continue，不退出
                        logger.warning(
                            "WS_SOURCE_UNKNOWN_MESSAGE_TYPE",
                            extra={"msg_type": msg_type, "keys": list(outer.keys())}
                        )
                        continue
                        
                    except json.JSONDecodeError as e:
                        logger.warning("WS_SOURCE_JSON_ERROR", extra={"error": str(e)})
                    except Exception as e:
                        logger.warning("WS_SOURCE_PARSE_ERROR", extra={"error": str(e)})
                        
        except asyncio.TimeoutError:
            logger.warning("WS_SOURCE_RECV_TIMEOUT", extra={"timeout": timeout})
            raise RuntimeError(f"recv_one timed out after {timeout} seconds")
        except websockets.ConnectionClosed:
            logger.warning("WS_SOURCE_CONNECTION_CLOSED")
            self._connected = False
            self._subscribed = False
            raise
        except asyncio.CancelledError:
            logger.info("WS_SOURCE_RECV_CANCELLED")
            raise
    
    async def recv_async_iterator(self) -> AsyncIterator[RawAnnouncement]:
        """异步迭代器版本的接收循环
        
        Yields:
            RawAnnouncement 实例
        """
        if not self._connected or not self._ws:
            raise RuntimeError("WebSocket not connected")
        
        if not self._subscribed:
            raise RuntimeError("WebSocket not subscribed")
        
        # 最大跳过无效消息次数，防止无限循环（与 recv_one 保持一致）
        max_skips = 100
        skip_count = 0
        
        try:
            async for message in self._ws:
                if self._should_stop:
                    break
                
                try:
                    outer = json.loads(message)
                    msg_type = outer.get("type")
                    
                    # COMMAND 响应
                    if msg_type == "COMMAND":
                        data_field = outer.get("data", "")
                        sub_type = outer.get("subType", "")
                        code = outer.get("code", "")
                        logger.info(
                            "WS_SOURCE_COMMAND",
                            extra={"data": data_field, "subType": sub_type, "code": code}
                        )
                        continue
                    
                    # DATA 消息
                    if msg_type == "DATA":
                        inner_data = outer.get("data")
                        if isinstance(inner_data, str):
                            inner = json.loads(inner_data)
                        elif isinstance(inner_data, dict):
                            inner = inner_data
                        else:
                            logger.warning(
                                "WS_SOURCE_UNEXPECTED_DATA_TYPE",
                                extra={"data_type": type(inner_data).__name__}
                            )
                            continue
                        
                        ann = RawAnnouncement.from_ws_message(inner)
                        if ann.title or ann.body:
                            yield ann
                        else:
                            # 空消息，记录 debug 并计数（与 recv_one 保持一致）
                            skip_count += 1
                            if skip_count >= max_skips:
                                logger.warning(
                                    "WS_SOURCE_TOO_MANY_EMPTY_MESSAGES",
                                    extra={"skip_count": skip_count}
                                )
                                raise RuntimeError(
                                    f"recv_async_iterator: received {skip_count} empty messages, aborting"
                                )
                            logger.debug(
                                "WS_SOURCE_EMPTY_MESSAGE_SKIPPED",
                                extra={"skip_count": skip_count}
                            )
                            continue
                    
                    # 未知类型 -> warning + continue
                    logger.warning(
                        "WS_SOURCE_UNKNOWN_MESSAGE_TYPE",
                        extra={"msg_type": msg_type, "keys": list(outer.keys())}
                    )
                    continue
                    
                except json.JSONDecodeError as e:
                    logger.warning("WS_SOURCE_JSON_ERROR", extra={"error": str(e)})
                except Exception as e:
                    logger.warning("WS_SOURCE_PARSE_ERROR", extra={"error": str(e)})
                    
        except websockets.ConnectionClosed:
            logger.warning("WS_SOURCE_CONNECTION_CLOSED")
            self._connected = False
            self._subscribed = False
    
    async def ping(self) -> bool:
        """发送心跳检测
        
        Returns:
            True 如果连接正常，False 否则
        """
        if not self._connected or not self._ws:
            return False
        
        try:
            await self._ws.ping()
            return True
        except Exception as e:
            logger.warning("WS_SOURCE_PING_FAILED", extra={"error": str(e)})
            return False
    
    async def reconnect(self) -> None:
        """重连机制
        
        断开当前连接并重新连接、订阅。
        """
        logger.info("WS_SOURCE_RECONNECTING")
        
        await self.disconnect()
        await asyncio.sleep(self._reconnect_delay)
        
        await self.connect()
        await self.subscribe()
        
        logger.info("WS_SOURCE_RECONNECTED")
    
    async def get_announcement_updates(self) -> AsyncIterator[RawAnnouncement]:
        """获取公告更新流
        
        实现 AnnouncementSource 接口。
        不需要 fetch_initial，WS 源通过此方法持续获取更新。
        
        Yields:
            RawAnnouncement 实例
        """
        if not self._connected:
            await self.connect()
        
        if not self._subscribed:
            await self.subscribe()
        
        async for ann in self.recv_async_iterator():
            yield ann
    
    async def fetch_initial(self, max_results: int = 100) -> list[RawAnnouncement]:
        """获取初始公告 [DEPRECATED]
        
        .. deprecated::
            WS 源不支持 fetch_initial，bootstrap/backfill 应由 HTML 源负责。
            此方法仅返回空列表以保持接口兼容。
        
        Args:
            max_results: 最大结果数 (ignored)
            
        Returns:
            空列表 (WS 源无法提供历史数据)
        """
        import warnings
        warnings.warn(
            "WS source does not support fetch_initial. "
            "Use HTML source for bootstrap/backfill.",
            DeprecationWarning,
            stacklevel=2
        )
        return []
