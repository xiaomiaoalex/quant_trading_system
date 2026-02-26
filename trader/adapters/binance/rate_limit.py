"""
Rate Budget - Token Bucket Rate Limiter
=======================================
实现 REST API 限流器，基于 Token Bucket 算法。

特性：
- Token Bucket 算法实现
- 支持 P0/P1/P2 优先级
- 429 错误自适应降速
- Budget 紧张时自动降级到 P0
"""
import asyncio
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional
from threading import Lock


logger = logging.getLogger(__name__)


class Priority(Enum):
    """请求优先级"""
    P0 = 0  # 最高优先级：openOrders, account
    P1 = 1  # 中等优先级：orders, trades
    P2 = 2  # 最低优先级：其他查询


@dataclass
class RateBudgetConfig:
    """Rate Budget 配置"""
    initial_refill_rate: float = 10.0    # 每秒填充的 token 数量
    initial_bucket_size: float = 20.0     # 初始桶大小
    min_refill_rate: float = 1.0         # 最小填充率（降级后）
    cooldown_on_429: int = 60            # 429 错误后的冷却时间（秒）
    degrade_refill_multiplier: float = 0.5  # 降级时填充率乘数
    p0_only_refill_rate: float = 5.0     # 仅 P0 模式下的填充率


@dataclass
class BudgetState:
    """Budget 当前状态"""
    current_tokens: float
    last_refill_ts: float
    refill_rate: float
    bucket_size: float
    is_degraded: bool = False
    degraded_until_ts: float = 0.0


class RestRateBudget:
    """
    REST API Rate Budget (Token Bucket 实现)

    用于控制 REST API 调用频率，支持优先级和降级模式。
    """

    def __init__(self, config: Optional[RateBudgetConfig] = None):
        self._config = config or RateBudgetConfig()
        self._state = BudgetState(
            current_tokens=self._config.initial_bucket_size,
            last_refill_ts=time.time(),
            refill_rate=self._config.initial_refill_rate,
            bucket_size=self._config.initial_bucket_size,
        )
        self._lock = Lock()
        self._429_count = 0
        self._last_429_ts = 0.0

    def _refill(self) -> None:
        """填充 token（需要持有锁）"""
        now = time.time()
        elapsed = now - self._state.last_refill_ts

        if elapsed > 0:
            new_tokens = self._state.current_tokens + (elapsed * self._state.refill_rate)
            self._state.current_tokens = min(new_tokens, self._state.bucket_size)
            self._state.last_refill_ts = now

    def acquire(self, cost: int = 1, priority: Priority = Priority.P2) -> bool:
        """
        尝试获取 token

        Args:
            cost: 需要的 token 数量
            priority: 请求优先级

        Returns:
            bool: 是否成功获取 token
        """
        with self._lock:
            self._refill()

            now = time.time()
            if self._state.is_degraded and self._state.degraded_until_ts > now:
                if priority != Priority.P0:
                    logger.warning(
                        f"[RateBudget] Degraded to P0-only, rejecting priority {priority}"
                    )
                    return False

            if self._state.current_tokens >= cost:
                self._state.current_tokens -= cost
                logger.debug(
                    f"[RateBudget] Acquired {cost} tokens, "
                    f"remaining: {self._state.current_tokens:.2f}"
                )
                return True

            logger.warning(
                f"[RateBudget] Insufficient tokens: need {cost}, "
                f"have {self._state.current_tokens:.2f}"
            )
            return False

    async def acquire_async(self, cost: int = 1, priority: Priority = Priority.P2, timeout: float = 30.0) -> bool:
        """
        异步获取 token，带超时等待

        Args:
            cost: 需要的 token 数量
            priority: 请求优先级
            timeout: 超时时间（秒）

        Returns:
            bool: 是否成功获取 token
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            if self.acquire(cost, priority):
                return True

            if priority == Priority.P0 and self._state.is_degraded:
                await asyncio.sleep(0.1)
            else:
                await asyncio.sleep(0.5)

        return False

    def on_429(self, retry_after: Optional[int] = None) -> None:
        """
        处理 429 错误

        Args:
            retry_after: 服务器返回的 Retry-After 秒数（作为最小等待时间）
        """
        with self._lock:
            self._429_count += 1
            now = time.time()
            self._last_429_ts = now

            min_wait = retry_after if retry_after and retry_after > 0 else self._config.cooldown_on_429
            cooldown = max(min_wait, self._config.cooldown_on_429)

            old_refill_rate = self._state.refill_rate
            self._state.refill_rate = max(
                self._config.min_refill_rate,
                self._state.refill_rate * self._config.degrade_refill_multiplier
            )

            self._state.is_degraded = True
            self._state.degraded_until_ts = now + cooldown

            self.degrade_to_p0_only(cooldown_s=cooldown)

            logger.warning(
                f"[RateBudget] 429 received: reducing refill_rate "
                f"from {old_refill_rate:.2f} to {self._state.refill_rate:.2f}, "
                f"degraded for {cooldown}s, P0-only mode enabled"
            )

    def on_418(self) -> None:
        """
        处理 418 错误（被永久封禁）

        进入极端降级模式
        """
        with self._lock:
            logger.error("[RateBudget] 418 received: entering EXTREME_DEGRADED mode")
            self._state.refill_rate = self._config.min_refill_rate
            self._state.is_degraded = True
            self._state.degraded_until_ts = time.time() + 300

    def degrade_to_p0_only(self, cooldown_s: int = 60) -> None:
        """
        降级到仅允许 P0 请求

        Args:
            cooldown_s: 冷却时间
        """
        with self._lock:
            old_refill_rate = self._state.refill_rate
            self._state.refill_rate = self._config.p0_only_refill_rate
            self._state.is_degraded = True
            self._state.degraded_until_ts = time.time() + cooldown_s

            logger.warning(
                f"[RateBudget] Degraded to P0-only: refill_rate "
                f"from {old_refill_rate:.2f} to {self._state.refill_rate:.2f}"
            )

    def reset(self) -> None:
        """重置 Budget 到初始状态"""
        with self._lock:
            self._state.current_tokens = self._config.initial_bucket_size
            self._state.refill_rate = self._config.initial_refill_rate
            self._state.is_degraded = False
            self._429_count = 0
            logger.info("[RateBudget] Reset to initial state")

    def get_state(self) -> Dict:
        """获取当前状态"""
        with self._lock:
            self._refill()
            return {
                "current_tokens": self._state.current_tokens,
                "refill_rate": self._state.refill_rate,
                "bucket_size": self._state.bucket_size,
                "is_degraded": self._state.is_degraded,
                "429_count": self._429_count,
            }

    def is_p0_allowed(self) -> bool:
        """检查是否允许 P0 请求"""
        with self._lock:
            return self._state.current_tokens >= 1 or self._state.is_degraded
