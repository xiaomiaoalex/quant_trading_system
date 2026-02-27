"""
Fake WebSocket - 多种故障模式
============================
用于测试的 WebSocket 模拟器，支持多种故障模式。
"""
import asyncio
from typing import Optional, Callable, Any, List, Dict
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock


class WSMode(Enum):
    """WebSocket 模式"""
    NORMAL = "normal"           # 正常流
    HANG = "hang"               # 永久卡死（recv 不返回）
    DELAY = "delay"             # 延迟
    DISORDER = "disorder"       # 乱序
    REPLAY = "replay"           # 重放
    CLOSE = "close"             # 主动关闭


@dataclass
class WSConfig:
    """WebSocket 配置"""
    mode: WSMode = WSMode.NORMAL
    delay_seconds: float = 0.0
    message_queue: List[Any] = field(default_factory=list)
    close_on_recv: bool = False
    error_on_recv: Optional[Exception] = None


class FakeWebSocket:
    """
    伪 WebSocket
    
    支持多种故障模式：
    - NORMAL: 正常消息流
    - HANG: recv 永久卡死
    - DELAY: recv 延迟返回
    - DISORDER: 消息乱序
    - REPLAY: 消息重放
    - CLOSE: 主动关闭
    """
    
    def __init__(self, config: Optional[WSConfig] = None):
        self._config = config or WSConfig()
        self._closed = False
        self._close_code: Optional[int] = None
        self._close_reason: Optional[str] = None
        self._lock = Lock()
        
        self._sent_messages: List[Any] = []
        self._recv_history: List[Any] = []
        self._ping_count = 0
        self._pong_count = 0
        
        self._message_queue = list(self._config.message_queue)
        self._disorder_index = 0
        self._replay_index = 0
        
        self._recv_waiter: Optional[asyncio.Future] = None
        self._ping_waiter: Optional[asyncio.Future] = None
        
    @property
    def closed(self) -> bool:
        return self._closed
    
    @property
    def sent_messages(self) -> List[Any]:
        return self._sent_messages.copy()
    
    @property
    def recv_history(self) -> List[Any]:
        return self._recv_history.copy()
    
    @property
    def ping_count(self) -> int:
        return self._ping_count
    
    @property
    def pong_count(self) -> int:
        return self._pong_count
    
    def set_mode(self, mode: WSMode) -> None:
        """设置模式"""
        self._config.mode = mode
    
    def set_message_queue(self, messages: List[Any]) -> None:
        """设置消息队列"""
        self._message_queue = list(messages)
        self._disorder_index = 0
        self._replay_index = 0
    
    def push_message(self, message: Any) -> None:
        """推送消息"""
        self._message_queue.append(message)
    
    async def connect(self) -> None:
        """模拟连接"""
        self._closed = False
    
    async def close(self, code: int = 1000, reason: str = "") -> None:
        """关闭连接"""
        self._closed = True
        self._close_code = code
        self._close_reason = reason
        
        if self._recv_waiter and not self._recv_waiter.done():
            self._recv_waiter.set_exception(asyncio.ConnectionClosedError(code, reason))
    
    async def send(self, data: Any) -> None:
        """发送消息"""
        if self._closed:
            raise asyncio.ConnectionClosedError(1006, "Connection closed")
        self._sent_messages.append(data)
    
    async def recv(self) -> Any:
        """接收消息"""
        if self._closed:
            raise asyncio.ConnectionClosedError(self._close_code or 1006, self._close_reason or "Connection closed")
        
        mode = self._config.mode
        
        if mode == WSMode.HANG:
            await asyncio.sleep(3600)
            raise asyncio.ConnectionClosedError(1006, "Connection hung")
        
        if mode == WSMode.CLOSE:
            await self.close(1000, "Normal close")
            raise asyncio.ConnectionClosedError(1000, "Normal close")
        
        if mode == WSMode.DELAY and self._config.delay_seconds > 0:
            await asyncio.sleep(self._config.delay_seconds)
        
        if not self._message_queue:
            if mode == WSMode.REPLAY and self._recv_history:
                self._replay_index = 0
                return await self.recv()
            await asyncio.sleep(0.1)
            return None
        
        message = self._get_next_message()
        self._recv_history.append(message)
        return message
    
    def _get_next_message(self) -> Any:
        """获取下一条消息"""
        mode = self._config.mode
        
        if mode == WSMode.DISORDER:
            messages = self._message_queue.copy()
            if self._disorder_index < len(messages):
                msg = messages[len(messages) - 1 - self._disorder_index]
                self._disorder_index += 1
                return msg
            return messages[0] if messages else None
        
        if mode == WSMode.REPLAY:
            if self._replay_index < len(self._recv_history):
                msg = self._recv_history[self._replay_index]
                self._replay_index += 1
                return msg
            self._replay_index = 0
        
        if self._message_queue:
            return self._message_queue.pop(0)
        
        return None
    
    async def ping(self, data: bytes = b"") -> None:
        """发送 ping"""
        self._ping_count += 1
        
        if self._config.mode == WSMode.HANG:
            await asyncio.sleep(3600)
        
        if self._config.mode == WSMode.CLOSE:
            return
        
        if self._config.error_on_recv:
            raise self._config.error_on_recv
    
    async def pong(self, data: bytes = b"") -> None:
        """发送 pong"""
        self._pong_count += 1
        
        if self._ping_waiter and not self._ping_waiter.done():
            self._ping_waiter.set_result(data)


class WebSocketPair:
    """成对的 WebSocket，用于双向通信测试"""
    
    def __init__(self):
        self.client = FakeWebSocket()
        self.server = FakeWebSocket()
        self._setup_pipe()
    
    def _setup_pipe(self):
        """建立管道"""
        original_client_send = self.client.send
        original_server_send = self.server.send
        
        async def client_to_server(data):
            await original_client_send(data)
            self.server.push_message(data)
        
        async def server_to_client(data):
            await original_server_send(data)
            self.client.push_message(data)
        
        self.client.send = client_to_server
        self.server.send = server_to_client
