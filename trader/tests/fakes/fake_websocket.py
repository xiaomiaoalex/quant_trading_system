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


class ConnectionClosedError(Exception):
    """WebSocket 连接关闭异常（兼容 websockets 库）"""
    def __init__(self, code: int = 1006, reason: str = ""):
        self.code = code
        self.reason = reason
        super().__init__(f"Connection closed: code={code}, reason={reason}")


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
    hang_on_recv: bool = False
    hang_on_ping: bool = False
    ping_timeout_after_n: int = -1
    hang_raises_on_release: bool = True


class PingPongScript:
    """
    ping/pong 脚本化控制器
    
    用于精确控制 ping 响应行为：
    - 支持设置第 N 次 ping 后永远不返回 pong（模拟 STALE）
    - 支持设置 pong 延迟
    """
    
    def __init__(self):
        self._ping_count = 0
        self._timeout_after_n = -1
        self._delay_seconds = 0.0
        self._pong_error: Optional[Exception] = None
        self._never_respond = False
        
    def set_timeout_after(self, n: int) -> 'PingPongScript':
        """
        设置第 n 次 ping 后永不返回 pong
        
        Args:
            n: 第 n 次 ping 触发超时，-1 表示永不超时
        """
        self._timeout_after_n = n
        return self
    
    def set_delay(self, seconds: float) -> 'PingPongScript':
        """设置 pong 延迟"""
        self._delay_seconds = seconds
        return self
    
    def set_error(self, error: Exception) -> 'PingPongScript':
        """设置 pong 返回错误"""
        self._pong_error = error
        return self
    
    def should_timeout(self) -> bool:
        """判断当前 ping 是否应该超时"""
        self._ping_count += 1
        if self._timeout_after_n > 0 and self._ping_count >= self._timeout_after_n:
            self._never_respond = True
            return True
        return False
    
    def get_delay(self) -> float:
        """获取配置的延迟"""
        return self._delay_seconds
    
    def get_error(self) -> Optional[Exception]:
        """获取配置的错误"""
        return self._pong_error
    
    def never_respond(self) -> bool:
        """是否永远不响应"""
        return self._never_respond
    
    def reset(self) -> None:
        """重置状态"""
        self._ping_count = 0
        self._never_respond = False


class FakeWebSocket:
    """
    伪 WebSocket
    
    支持多种故障模式：
    - NORMAL: 正常消息流
    - HANG: recv 永久卡死（GFW 静默断流模拟）
    - DELAY: recv 延迟返回
    - DISORDER: 消息乱序
    - REPLAY: 消息重放
    - CLOSE: 主动关闭
    
    特性：
    - ping/pong 脚本化，支持第 N 次 ping 超时
    - hang 模式不依赖真实时间
    """
    
    def __init__(
        self, 
        config: Optional[WSConfig] = None,
        sleep_fn: Optional[Callable[[float], "asyncio.Future"]] = None
    ):
        """
        Args:
            config: WebSocket 配置
            sleep_fn: 可选的 sleep 函数。
                     如果不提供，默认使用 asyncio.sleep。
                     注入后可实现时间可控。
        """
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
        
        self._hang_future: Optional[asyncio.Future] = None
        self._hang_released = False
        
        self._ping_pong_script = PingPongScript()
        
        self._sleep_fn = sleep_fn
    
    def set_sleep_fn(self, sleep_fn: Callable[[float], "asyncio.Future"]) -> None:
        """注入 sleep 函数"""
        self._sleep_fn = sleep_fn
    
    async def _sleep(self, seconds: float) -> None:
        """内部 sleep 方法"""
        if self._sleep_fn:
            await self._sleep_fn(seconds)
        else:
            await asyncio.sleep(seconds)
        
    @property
    def ping_pong_script(self) -> PingPongScript:
        """获取 ping/pong 脚本控制器"""
        return self._ping_pong_script
    
    def set_ping_pong_script(self, script: PingPongScript) -> None:
        """设置 ping/pong 脚本"""
        self._ping_pong_script = script
        
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
            self._recv_waiter.set_exception(ConnectionClosedError(code, reason))
        
        if self._hang_future and not self._hang_future.done():
            self._hang_future.set_result(None)
    
    def release_hang(self) -> None:
        """
        释放 hang 状态（公开 API）
        
        用于手动解除 recv() 的 HANG 阻塞。
        禁止测试直接访问 _hang_future。
        """
        self._hang_released = True
        if self._hang_future and not self._hang_future.done():
            self._hang_future.set_result(None)
    
    async def send(self, data: Any) -> None:
        """发送消息"""
        if self._closed:
            raise ConnectionClosedError(1006, "Connection closed")
        self._sent_messages.append(data)
    
    async def recv(self) -> Any:
        """
        接收消息
        
        支持多种模式：
        - HANG: 永远阻塞（不依赖真实时间），直到被 release_hang()
        - CLOSE: 关闭连接
        - DELAY: 延迟返回
        
        空队列行为：
        - 默认直接返回 None，不真实 sleep（由上层 wait_for 控制节奏）
        """
        if self._closed:
            raise ConnectionClosedError(self._close_code or 1006, self._close_reason or "Connection closed")
        
        mode = self._config.mode
        
        if mode == WSMode.HANG or self._config.hang_on_recv:
            if self._hang_released:
                if self._config.hang_raises_on_release:
                    raise ConnectionClosedError(1006, "Connection hung")
                return None
            
            loop = asyncio.get_running_loop()
            if self._hang_future is None or self._hang_future.done():
                self._hang_future = loop.create_future()
            try:
                await asyncio.shield(self._hang_future)
            except asyncio.CancelledError:
                if self._hang_future.cancelled():
                    self._hang_future = loop.create_future()
                raise
            
            if self._config.hang_raises_on_release:
                raise ConnectionClosedError(1006, "Connection hung")
            return None
        
        if mode == WSMode.CLOSE:
            await self.close(1000, "Normal close")
            raise ConnectionClosedError(1000, "Normal close")
        
        if mode == WSMode.DELAY and self._config.delay_seconds > 0:
            await self._sleep(self._config.delay_seconds)
        
        if not self._message_queue:
            if mode == WSMode.REPLAY and self._recv_history:
                self._replay_index = 0
                return await self.recv()
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
    
    def ping(self, data: bytes = b"") -> asyncio.Future:
        """
        发送 ping（非 async）
        
        返回 waiter future，用于外部等待 pong。
        - 正常：waiter 立即完成（pong 已收到）
        - 延迟：waiter 在延迟后完成
        - 超时：waiter 永不完成（wait_for 超时）
        - 错误：waiter 抛异常
        
        用法（与生产代码一致）：
            waiter = ws.ping()
            try:
                await asyncio.wait_for(waiter, timeout=5.0)
                print("Pong received")
            except asyncio.TimeoutError:
                print("Pong timeout - STALE")
        """
        self._ping_count += 1
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        
        if self._config.mode == WSMode.HANG or self._config.hang_on_ping:
            hang_future = loop.create_future()
            return waiter
        
        if self._config.mode == WSMode.CLOSE:
            return waiter
        
        if self._ping_pong_script.should_timeout():
            self._ping_waiter = waiter
            return waiter
        
        if self._config.error_on_recv:
            waiter.set_exception(self._config.error_on_recv)
            return waiter
        
        if self._ping_pong_script.get_delay() > 0:
            delay = self._ping_pong_script.get_delay()
            
            async def delayed_pong():
                await self._sleep(delay)
                waiter.set_result(None)
            
            asyncio.create_task(delayed_pong())
            return waiter
        
        if self._ping_pong_script.get_error():
            waiter.set_exception(self._ping_pong_script.get_error())
            return waiter
        
        waiter.set_result(None)
        return waiter
    
    async def pong(self, data: bytes = b"") -> None:
        """发送 pong - 触发 ping waiter"""
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
