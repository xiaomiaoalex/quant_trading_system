"""
Backoff Controller - Exponential Backoff with Jitter
=====================================================
实现带 Jitter 的指数退避控制器。

特性：
- 每个任务独立的退避状态
- 支持 Full Jitter 策略
- 支持 Retry-After 响应头
- 重连风暴检测
"""
import asyncio
import random
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional
from threading import Lock
from collections import defaultdict


logger = logging.getLogger(__name__)


@dataclass
class BackoffConfig:
    """退避配置"""
    initial_delay: float = 1.0          # 初始延迟（秒）
    max_delay: float = 60.0              # 最大延迟（秒）
    multiplier: float = 2.0              # 指数乘数
    jitter_range: float = 1.0            # Jitter 范围（0-1 之间乘数）
    retry_after_multiplier: float = 1.0   # Retry-After 响应头乘数
    max_retries: int = 10                # 最大重试次数
    reset_after_seconds: float = 60.0    # 成功后退避重置时间


@dataclass
class TaskBackoffState:
    """单个任务的退避状态"""
    task_name: str
    current_delay: float
    retry_count: int
    last_retry_ts: float
    is_in_backoff: bool = False


class BackoffController:
    """
    指数退避控制器

    为每个任务维护独立的退避状态，支持：
    - 指数退避 + Full Jitter
    - Retry-After 响应头处理
    - 重连风暴检测
    """

    def __init__(self, config: Optional[BackoffConfig] = None):
        self._config = config or BackoffConfig()
        self._task_states: Dict[str, TaskBackoffState] = {}
        self._lock = Lock()
        self._total_reconnect_count = 0
        self._reconnect_timestamps: list = []
        self._reconnect_storm_threshold = 10  # 5分钟内10次重连视为风暴
        self._reconnect_storm_window = 300     # 5分钟窗口

    def _ensure_task_state(self, task_name: str) -> TaskBackoffState:
        """确保任务状态存在"""
        if task_name not in self._task_states:
            self._task_states[task_name] = TaskBackoffState(
                task_name=task_name,
                current_delay=self._config.initial_delay,
                retry_count=0,
                last_retry_ts=0.0,
            )
        return self._task_states[task_name]

    def next_delay(self, task_name: str, retry_after_s: Optional[float] = None) -> float:
        """
        计算下一次延迟

        Args:
            task_name: 任务名称
            retry_after_s: 服务器返回的 Retry-After 秒数

        Returns:
            float: 下一次应该等待的秒数
        """
        with self._lock:
            state = self._ensure_task_state(task_name)
            now = time.time()

            if state.last_retry_ts > 0 and (now - state.last_retry_ts) > self._config.reset_after_seconds:
                state.current_delay = self._config.initial_delay
                state.retry_count = 0

            if retry_after_s and retry_after_s > 0:
                delay = retry_after_s * self._config.retry_after_multiplier
            else:
                delay = state.current_delay

            jittered_delay = self._apply_jitter(delay)

            delay = min(jittered_delay, self._config.max_delay)

            state.current_delay = delay * self._config.multiplier
            state.current_delay = min(state.current_delay, self._config.max_delay)
            state.retry_count += 1
            state.last_retry_ts = now
            state.is_in_backoff = True

            if task_name == "reconnect":
                self._total_reconnect_count += 1
                self._reconnect_timestamps.append(now)
                self._cleanup_reconnect_history(now)

            logger.debug(
                f"[Backoff] Task {task_name}: delay={delay:.2f}s, "
                f"next={state.current_delay:.2f}s, retries={state.retry_count}"
            )

            return delay

    def _apply_jitter(self, delay: float) -> float:
        """应用 Full Jitter"""
        jitter_factor = random.uniform(
            1.0 - self._config.jitter_range,
            1.0 + self._config.jitter_range
        )
        return max(0.1, delay * jitter_factor)

    def _cleanup_reconnect_history(self, now: float) -> None:
        """清理历史记录"""
        cutoff = now - self._reconnect_storm_window
        self._reconnect_timestamps = [ts for ts in self._reconnect_timestamps if ts > cutoff]

    def reset(self, task_name: str) -> None:
        """
        重置任务的退避状态

        Args:
            task_name: 任务名称
        """
        with self._lock:
            if task_name in self._task_states:
                state = self._task_states[task_name]
                state.current_delay = self._config.initial_delay
                state.retry_count = 0
                state.is_in_backoff = False
                logger.debug(f"[Backoff] Task {task_name} reset")

    def reset_all(self) -> None:
        """重置所有任务的退避状态"""
        with self._lock:
            for state in self._task_states.values():
                state.current_delay = self._config.initial_delay
                state.retry_count = 0
                state.is_in_backoff = False
            logger.info("[Backoff] All tasks reset")

    def get_delay(self, task_name: str) -> float:
        """获取当前任务的延迟（不增加）"""
        with self._lock:
            state = self._ensure_task_state(task_name)
            return state.current_delay

    def get_retry_count(self, task_name: str) -> int:
        """获取当前任务的重试次数"""
        with self._lock:
            state = self._ensure_task_state(task_name)
            return state.retry_count

    def is_reconnect_storm(self) -> bool:
        """
        检测是否为重连风暴

        Returns:
            bool: 是否处于重连风暴中
        """
        with self._lock:
            now = time.time()
            self._cleanup_reconnect_history(now)
            return len(self._reconnect_timestamps) >= self._reconnect_storm_threshold

    def get_state(self) -> Dict:
        """获取所有任务的状态"""
        with self._lock:
            return {
                "tasks": {
                    name: {
                        "current_delay": state.current_delay,
                        "retry_count": state.retry_count,
                        "is_in_backoff": state.is_in_backoff,
                    }
                    for name, state in self._task_states.items()
                },
                "reconnect_count_5min": len(self._reconnect_timestamps),
                "is_reconnect_storm": self.is_reconnect_storm(),
            }

    async def wait_if_needed(self, task_name: str) -> None:
        """如果任务正在退避中，则等待"""
        with self._lock:
            state = self._ensure_task_state(task_name)
            if not state.is_in_backoff:
                return
            delay = state.current_delay

        if delay > 0:
            logger.debug(f"[Backoff] Waiting {delay:.2f}s before {task_name}")
            await asyncio.sleep(delay)

        with self._lock:
            state.is_in_backoff = False


class BackoffControllerAsync:
    """
    异步退避控制器封装

    提供异步友好的接口
    """

    def __init__(self, config: Optional[BackoffConfig] = None):
        self._controller = BackoffController(config)

    async def execute_with_backoff(
        self,
        task_name: str,
        coro,
        retry_after_s: Optional[float] = None,
        max_retries: Optional[int] = None
    ):
        """
        执行带退避的异步任务

        Args:
            task_name: 任务名称
            coro: 异步协程
            retry_after_s: Retry-After 秒数
            max_retries: 最大重试次数

        Returns:
            任务执行结果

        Raises:
            最后一次尝试的错误
        """
        max_retries = max_retries or self._controller._config.max_retries

        last_error = None
        for attempt in range(max_retries):
            try:
                result = await coro
                self._controller.reset(task_name)
                return result
            except Exception as e:
                last_error = e
                logger.warning(
                    f"[Backoff] Task {task_name} attempt {attempt + 1}/{max_retries} failed: {e}"
                )

                if attempt < max_retries - 1:
                    delay = self._controller.next_delay(task_name, retry_after_s)
                    await asyncio.sleep(delay)

        raise last_error
