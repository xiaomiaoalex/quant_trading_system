"""
Heartbeat Service - 后端进程心跳检测
=====================================
负责检测后端进程健康状态：

1. Event Loop 响应性检测（loop lag）
2. 活跃 Task 数量监控
3. 进程运行时间
4. 可选内存使用检测（降级方案）

设计原则：
- Fail-Closed: 检测服务异常不影响交易执行
- 轻量级: 检测间隔 10s，不阻塞事件循环
- Windows 兼容: 内存检测可选降级
"""
import asyncio
import logging
import time
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ProcessHeartbeat:
    """进程心跳"""
    event_loop_lag_ms: float
    last_event_loop_check_ts_ms: int
    active_tasks: int
    uptime_seconds: float
    memory_usage_mb: Optional[float] = None

    @property
    def is_healthy(self) -> bool:
        """心跳是否健康"""
        return self.event_loop_lag_ms < 1000.0


class ProcessHeartbeatService:
    """
    进程心跳服务

    使用 loop lag 检测事件循环响应性：
    - 调度一个立即执行的 task，记录 scheduled time 和 actual execution time 的差值
    - 差值 > 1s 视为事件循环卡顿
    """
    _check_interval: float = 10.0
    _max_lag_threshold_ms: float = 1000.0
    _start_time: float = field(default_factory=time.time)

    def __init__(self) -> None:
        self._running = False
        self._check_task: Optional[asyncio.Task] = None
        self._last_heartbeat: Optional[ProcessHeartbeat] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """启动心跳服务"""
        if self._running:
            return
        self._running = True
        self._start_time = time.time()
        self._check_task = asyncio.create_task(self._check_loop())
        logger.info("[Heartbeat] Started")

    async def stop(self) -> None:
        """停止心跳服务"""
        if not self._running:
            return
        self._running = False
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        logger.info("[Heartbeat] Stopped")

    async def _check_loop(self) -> None:
        """心跳检测循环"""
        while self._running:
            try:
                await asyncio.sleep(self._check_interval)
                heartbeat = await self._check_heartbeat()
                async with self._lock:
                    self._last_heartbeat = heartbeat

                if heartbeat.event_loop_lag_ms > self._max_lag_threshold_ms:
                    logger.warning(
                        f"[Heartbeat] Event loop lag detected: {heartbeat.event_loop_lag_ms:.1f}ms"
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Heartbeat] Check error: {e}")

    async def _check_heartbeat(self) -> ProcessHeartbeat:
        """执行心跳检测"""
        lag_ms = await self._measure_loop_lag()
        active_tasks = len(asyncio.all_tasks())
        uptime = time.time() - self._start_time
        memory_mb = self._get_memory_usage()

        return ProcessHeartbeat(
            event_loop_lag_ms=lag_ms,
            last_event_loop_check_ts_ms=int(time.time() * 1000),
            active_tasks=active_tasks,
            uptime_seconds=uptime,
            memory_usage_mb=memory_mb,
        )

    async def _measure_loop_lag(self) -> float:
        """
        测量事件循环延迟

        通过在未来事件循环迭代中调度回调，测量实际执行时间与预期时间的差值。
        如果事件循环被阻塞，call_later 的回调会延迟执行。
        """
        loop = asyncio.get_running_loop()
        start_time = loop.time()
        
        def _scheduled_callback():
            pass
        
        loop.call_later(0, _scheduled_callback)
        await asyncio.sleep(0)
        
        elapsed = loop.time() - start_time
        lag_ms = elapsed * 1000

        if lag_ms > self._max_lag_threshold_ms:
            logger.warning(
                f"[Heartbeat] Loop lag threshold exceeded: {lag_ms:.1f}ms"
            )

        return lag_ms

    def _get_memory_usage(self) -> Optional[float]:
        """
        获取内存使用（MB）

        降级方案：
        - Windows: 使用 os.getpid() + psutil (如果可用)
        - 其他: 尝试 resource.getrusage()
        - 失败时返回 None
        """
        try:
            import sys
            if sys.platform == "win32":
                try:
                    import psutil
                    process = psutil.Process(os.getpid())
                    return process.memory_info().rss / (1024 * 1024)
                except ImportError:
                    return None
            else:
                import resource
                usage = resource.getrusage(resource.RUSAGE_SELF)
                return usage.ru_maxrss / 1024
        except Exception as e:
            logger.debug(f"[Heartbeat] Memory detection failed: {e}")
            return None

    def get_last_heartbeat(self) -> Optional[ProcessHeartbeat]:
        """获取最近一次心跳"""
        return self._last_heartbeat

    async def force_check(self) -> ProcessHeartbeat:
        """强制执行一次心跳检测"""
        heartbeat = await self._check_heartbeat()
        async with self._lock:
            self._last_heartbeat = heartbeat
        return heartbeat
