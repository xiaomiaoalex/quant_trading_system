"""
Connection Manager - 前端连接状态管理
=====================================
负责追踪前端 HTTP 轮询客户端的活跃状态。

前端使用 HTTP 轮询（非 WebSocket），通过记录客户端最后活跃时间判断健康状态。

状态定义：
- IDLE: 无客户端连接（正常态，不算故障）
- HEALTHY: 有活跃客户端，最近一次请求在 30s 内
- DEGRADED: 客户端响应慢或最近一次请求超过 30s

设计原则：
- Fail-Closed: 管理服务异常不影响交易执行
- 轻量级: 无需持久化连接状态
- 前端兼容: 不依赖 WebSocket 协议
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ClientSession:
    """客户端会话"""
    client_id: str
    last_seen_ts_ms: int
    is_healthy: bool = True


@dataclass
class FrontendConnectionStatus:
    """前端连接状态"""
    active_sessions: int
    last_seen_ts_ms: Optional[int]
    status: str
    clients: Dict[str, int] = field(default_factory=dict)

    @property
    def is_idle(self) -> bool:
        return self.status == "IDLE"

    @property
    def is_healthy(self) -> bool:
        return self.status == "HEALTHY"


class ConnectionManager:
    """
    前端连接管理器

    追踪前端 HTTP 轮询客户端的活跃状态。
    前端定期调用 `/health/heartbeat` 端点来表明存活。
    """
    _healthy_timeout_seconds: float = 30.0

    def __init__(self) -> None:
        self._clients: Dict[str, ClientSession] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """启动连接管理器"""
        if self._running:
            return
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("[ConnectionManager] Started")

    async def stop(self) -> None:
        """停止连接管理器"""
        if not self._running:
            return
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            self._clients.clear()
        logger.info("[ConnectionManager] Stopped")

    async def record_ping(self, client_id: str) -> None:
        """
        记录客户端 ping

        前端每次轮询时调用此方法更新客户端最后活跃时间
        """
        now_ms = int(time.time() * 1000)
        async with self._lock:
            self._clients[client_id] = ClientSession(
                client_id=client_id,
                last_seen_ts_ms=now_ms,
                is_healthy=True
            )

    async def get_status(self) -> FrontendConnectionStatus:
        """获取前端连接状态"""
        async with self._lock:
            now_ms = int(time.time() * 1000)
            threshold_ms = int(self._healthy_timeout_seconds * 1000)

            active_clients = {}
            healthy_count = 0
            last_seen = None

            for client_id, session in self._clients.items():
                if now_ms - session.last_seen_ts_ms < threshold_ms:
                    active_clients[client_id] = session.last_seen_ts_ms
                    healthy_count += 1
                    if last_seen is None or session.last_seen_ts_ms > last_seen:
                        last_seen = session.last_seen_ts_ms

            if len(active_clients) == 0:
                status = "IDLE"
            elif healthy_count == len(active_clients):
                status = "HEALTHY"
            else:
                status = "DEGRADED"

            return FrontendConnectionStatus(
                active_sessions=len(active_clients),
                last_seen_ts_ms=last_seen,
                status=status,
                clients=active_clients
            )

    async def _cleanup_loop(self) -> None:
        """清理过期客户端"""
        while self._running:
            try:
                await asyncio.sleep(60)
                await self._cleanup_stale_clients()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[ConnectionManager] Cleanup error: {e}")

    async def _cleanup_stale_clients(self) -> None:
        """清理过期客户端"""
        async with self._lock:
            now_ms = int(time.time() * 1000)
            threshold_ms = int(self._healthy_timeout_seconds * 1000 * 3)

            stale = [
                client_id for client_id, session in self._clients.items()
                if now_ms - session.last_seen_ts_ms > threshold_ms
            ]

            for client_id in stale:
                del self._clients[client_id]

            if stale:
                logger.debug(f"[ConnectionManager] Cleaned up {len(stale)} stale clients")

    def get_client_count(self) -> int:
        """获取活跃客户端数"""
        return len(self._clients)
