"""
Base FSM - Finite State Machine Framework
==========================================
基础状态机框架，为 Public/Private Stream Manager 提供通用状态机能力。
"""
import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Optional, Any, List
from threading import Lock


logger = logging.getLogger(__name__)


class StreamState(Enum):
    """流状态"""
    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    STALE_DATA = "STALE_DATA"
    DEGRADED = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"
    ALIGNING = "ALIGNING"  # 重连后等待 REST 对齐


class StreamEvent(Enum):
    """流事件"""
    START = "START"
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    RECONNECT = "RECONNECT"
    STALE_DETECTED = "STALE_DETECTED"
    DATA_RECEIVED = "DATA_RECEIVED"
    ERROR = "ERROR"
    STOP = "STOP"


@dataclass
class StreamConfig:
    """流配置"""
    reconnect_max_attempts: int = 10
    reconnect_base_delay: float = 1.0
    stale_timeout_seconds: float = 30.0
    pong_timeout_seconds: float = 10.0
    max_reconnect_per_window: int = 10
    reconnect_window_seconds: int = 300


@dataclass
class StreamMetrics:
    """流指标"""
    state: StreamState = StreamState.IDLE
    connect_count: int = 0
    disconnect_count: int = 0
    reconnect_count: int = 0
    stale_count: int = 0
    last_connect_ts: float = 0.0
    last_disconnect_ts: float = 0.0
    last_data_ts: float = 0.0
    last_error: Optional[str] = None
    consecutive_failures: int = 0


class BaseStreamFSM(ABC):
    """
    基础流状态机

    提供通用的状态机能力，包括：
    - 状态转换管理
    - 指标收集
    - 事件回调
    """

    def __init__(self, name: str, config: Optional[StreamConfig] = None):
        self._name = name
        self._config = config or StreamConfig()
        self._state = StreamState.IDLE
        self._metrics = StreamMetrics()
        self._lock = Lock()
        self._running = False
        self._stop_event = asyncio.Event()

        self._event_handlers: Dict[StreamEvent, List[Callable]] = {
            event: [] for event in StreamEvent
        }

        self._reconnect_timestamps: List[float] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> StreamState:
        return self._state

    @property
    def metrics(self) -> StreamMetrics:
        return self._metrics

    def _set_state(self, new_state: StreamState) -> None:
        """设置状态"""
        old_state = self._state
        if old_state != new_state:
            with self._lock:
                self._state = new_state
            logger.info(f"[{self._name}] State: {old_state.value} -> {new_state.value}")
            self._metrics.state = new_state

    def register_handler(self, event: StreamEvent, handler: Callable) -> None:
        """注册事件处理器"""
        self._event_handlers[event].append(handler)

    async def _trigger_event(self, event: StreamEvent, data: Any = None) -> None:
        """触发事件"""
        for handler in self._event_handlers.get(event, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event, data)
                else:
                    handler(event, data)
            except Exception as e:
                logger.error(f"[{self._name}] Event handler error: {e}")

    async def start(self) -> None:
        """启动状态机"""
        if self._running:
            return

        self._running = True
        self._stop_event.clear()
        self._set_state(StreamState.CONNECTING)

        logger.info(f"[{self._name}] Starting...")
        await self._on_start()

    async def stop(self) -> None:
        """停止状态机"""
        if not self._running:
            return

        self._running = False
        self._stop_event.set()
        self._set_state(StreamState.DISCONNECTED)

        logger.info(f"[{self._name}] Stopping...")
        await self._on_stop()

    def is_running(self) -> bool:
        """检查是否正在运行"""
        return self._running

    async def wait_until_stopped(self) -> None:
        """等待直到停止"""
        await self._stop_event.wait()

    def _check_reconnect_storm(self) -> bool:
        """检查是否为重连风暴"""
        now = time.time()
        window = self._config.reconnect_window_seconds
        self._reconnect_timestamps = [ts for ts in self._reconnect_timestamps if now - ts < window]

        if len(self._reconnect_timestamps) >= self._config.max_reconnect_per_window:
            logger.warning(
                f"[{self._name}] Reconnect storm detected: "
                f"{len(self._reconnect_timestamps)} reconnects in {window}s"
            )
            return True

        return False

    def _record_reconnect(self) -> None:
        """记录重连"""
        self._reconnect_timestamps.append(time.time())
        self._metrics.reconnect_count += 1

    @abstractmethod
    async def _on_start(self) -> None:
        """启动时的具体逻辑"""
        pass

    @abstractmethod
    async def _on_stop(self) -> None:
        """停止时的具体逻辑"""
        pass

    def get_status(self) -> Dict[str, Any]:
        """获取状态"""
        return {
            "name": self._name,
            "state": self._state.value,
            "running": self._running,
            "metrics": {
                "connect_count": self._metrics.connect_count,
                "disconnect_count": self._metrics.disconnect_count,
                "reconnect_count": self._metrics.reconnect_count,
                "stale_count": self._metrics.stale_count,
                "last_error": self._metrics.last_error,
            }
        }
