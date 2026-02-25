"""
Degraded Cascade Controller - 级联保护控制器
============================================
实现适配器与控制面的级联保护机制。

功能：
- 监听 Adapter 的 DEGRADED_MODE 信号
- 自动向 Control Plane 上报风险事件
- 自动触发 KillSwitch L1
- 幂等性保证
- 本地自保机制
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, Callable, Awaitable, Set
from enum import Enum

import aiohttp

from trader.adapters.binance.environmental_risk import (
    EnvironmentalRiskEvent,
    LocalEventLog,
    RiskSeverity,
    RiskScope,
    RecommendedLevel,
)
from trader.adapters.binance.backoff import BackoffController, BackoffConfig
from trader.adapters.binance.connector import AdapterHealth, AdapterHealthReport


logger = logging.getLogger(__name__)


class CascadeState(Enum):
    """级联状态"""
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    SELF_PROTECTION = "SELF_PROTECTION"
    RECOVERING = "RECOVERING"


@dataclass
class CascadeConfig:
    """级联配置"""
    control_plane_base_url: str = "http://localhost:8080"
    dedup_window_ms: int = 60000
    min_report_interval_ms: int = 5000
    max_report_interval_ms: int = 30000
    self_protection_trigger_ms: int = 30000
    max_retries_per_event: int = 5
    request_timeout: float = 10.0


@dataclass
class CascadeMetrics:
    """级联指标"""
    degraded_enter_count: int = 0
    degraded_exit_count: int = 0
    risk_events_reported: int = 0
    risk_events_failed: int = 0
    killswitch_triggered: int = 0
    self_protection_entered: int = 0
    self_protection_exited: int = 0
    last_degraded_ts: float = 0.0
    last_recovery_ts: float = 0.0


class DegradedCascadeController:
    """
    级联保护控制器

    当适配器进入 DEGRADED_MODE 时：
    1. 向 Control Plane 上报风险事件
    2. 触发 KillSwitch L1
    3. 如果 Control Plane 不可达，触发本地自保
    """

    def __init__(
        self,
        control_plane_base_url: str,
        http_client: Optional[aiohttp.ClientSession] = None,
        backoff: Optional[BackoffController] = None,
        config: Optional[CascadeConfig] = None,
        adapter_name: str = "binance_adapter"
    ):
        self._config = config or CascadeConfig(control_plane_base_url=control_plane_base_url)
        self._http_client = http_client
        self._backoff = backoff or BackoffController(BackoffConfig(
            initial_delay=1.0,
            max_delay=30.0,
            multiplier=2.0,
        ))
        self._adapter_name = adapter_name

        self._state = CascadeState.NORMAL
        self._metrics = CascadeMetrics()

        self._reported_dedup_keys: Set[str] = set()
        self._last_report_ts: Dict[str, float] = {}

        self._local_event_log = LocalEventLog()

        self._self_protection_active = False
        self._self_protection_triggered_at: Optional[float] = None

        self._on_self_protection_callbacks: list = []
        self._on_recovery_callbacks: list = []

    @property
    def state(self) -> CascadeState:
        return self._state

    @property
    def metrics(self) -> CascadeMetrics:
        return self._metrics

    @property
    def is_self_protection_active(self) -> bool:
        return self._self_protection_active

    def register_self_protection_callback(
        self, callback: Callable[[bool, Optional[str]], Awaitable[None]]
    ) -> None:
        """注册自保回调"""
        self._on_self_protection_callbacks.append(callback)

    def register_recovery_callback(
        self, callback: Callable[[], Awaitable[None]]
    ) -> None:
        """注册恢复回调"""
        self._on_recovery_callbacks.append(callback)

    async def _ensure_http_client(self) -> aiohttp.ClientSession:
        """确保 HTTP 客户端存在"""
        if self._http_client is None or self._http_client.closed:
            self._http_client = aiohttp.ClientSession()
        return self._http_client

    async def on_adapter_health_changed(
        self,
        health: AdapterHealthReport,
        reason: str = ""
    ) -> None:
        """
        处理适配器健康状态变化

        Args:
            health: 适配器健康报告
            reason: 变化原因
        """
        if health.overall_health == AdapterHealth.DEGRADED:
            await self._on_degraded_enter(health, reason)
        elif health.overall_health == AdapterHealth.HEALTHY and self._state != CascadeState.NORMAL:
            await self._on_degraded_exit(health, reason)

    async def _on_degraded_enter(
        self,
        health: AdapterHealthReport,
        reason: str
    ) -> None:
        """处理进入 DEGRADED 模式"""
        if self._state == CascadeState.DEGRADED:
            logger.debug(f"[Cascade] Already in DEGRADED state, skipping")
            return

        logger.warning(f"[Cascade] Entering DEGRADED mode: {reason}")
        self._state = CascadeState.DEGRADED
        self._metrics.degraded_enter_count += 1
        self._metrics.last_degraded_ts = time.time()

        try:
            await self._report_to_control_plane(health, reason)
        except Exception as e:
            logger.error(f"[Cascade] Failed to report to control plane: {e}")
            await self._trigger_self_protection(e)

    async def _on_degraded_exit(
        self,
        health: AdapterHealthReport,
        reason: str
    ) -> None:
        """处理退出 DEGRADED 模式"""
        if self._state == CascadeState.NORMAL:
            return

        logger.info(f"[Cascade] Exiting DEGRADED mode: {reason}")
        self._state = CascadeState.RECOVERING
        self._metrics.degraded_exit_count += 1

        self._reported_dedup_keys.clear()
        self._last_report_ts.clear()

        await self._exit_self_protection()

        self._state = CascadeState.NORMAL
        self._metrics.last_recovery_ts = time.time()

        for callback in self._on_recovery_callbacks:
            try:
                await callback()
            except Exception as e:
                logger.error(f"[Cascade] Recovery callback error: {e}")

    async def _report_to_control_plane(
        self,
        health: AdapterHealthReport,
        reason: str
    ) -> None:
        """向 Control Plane 上报"""
        event = EnvironmentalRiskEvent.create_from_adapter_health(
            adapter_name=self._adapter_name,
            health_data={
                "public_stream_state": health.public_stream_state.value,
                "private_stream_state": health.private_stream_state.value,
                "rate_budget_state": health.rate_budget_state,
                "backoff_state": health.backoff_state,
                "metrics": health.metrics,
            },
            scope=RiskScope.GLOBAL
        )

        if not self._should_report(event):
            logger.debug(f"[Cascade] Skipping report due to dedup/rate limit")
            return

        self._local_event_log.add(event)

        risk_event_success = await self._post_risk_event(event)
        killswitch_success = await self._post_killswitch(event)

        if risk_event_success and killswitch_success:
            self._metrics.risk_events_reported += 1
            self._reported_dedup_keys.add(event.dedup_key)
            self._last_report_ts["risk"] = time.time()
            event.is_reported = True
        else:
            self._metrics.risk_events_failed += 1
            event.report_attempts += 1
            event.last_report_error = "Control plane unreachable"

            if event.report_attempts >= self._config.max_retries_per_event:
                await self._trigger_self_protection(Exception("Max retries reached"))

    def _should_report(self, event: EnvironmentalRiskEvent) -> bool:
        """检查是否应该上报（幂等 + 频率限制）"""
        if event.dedup_key in self._reported_dedup_keys:
            logger.debug(f"[Cascade] Dedup key already reported: {event.dedup_key}")
            return False

        now = time.time()
        last_report = self._last_report_ts.get("risk", 0)
        if now - last_report < (self._config.min_report_interval_ms / 1000):
            logger.debug(f"[Cascade] Rate limited: last report {now - last_report:.2f}s ago")
            return False

        return True

    async def _post_risk_event(self, event: EnvironmentalRiskEvent) -> bool:
        """
        POST /v1/risk/events

        Returns:
            True if successful, False otherwise
        """
        url = f"{self._config.control_plane_base_url}/v1/risk/events"
        payload = event.to_dict()

        logger.info(f"[Cascade] Posting risk event: {event.dedup_key}")

        try:
            client = await self._ensure_http_client()
            async with client.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self._config.request_timeout)
            ) as resp:
                if resp.status in (200, 201, 409):
                    logger.info(f"[Cascade] Risk event accepted: {resp.status}")
                    return True
                elif resp.status == 429:
                    retry_after = resp.headers.get("Retry-After", "60")
                    self._backoff.next_delay("risk_event", int(retry_after))
                    logger.warning(f"[Cascade] Risk event rate limited: {retry_after}s")
                    return False
                else:
                    logger.error(f"[Cascade] Risk event failed: {resp.status}")
                    return False

        except asyncio.TimeoutError:
            logger.error(f"[Cascade] Risk event timeout")
            self._backoff.next_delay("risk_event")
            return False
        except aiohttp.ClientError as e:
            logger.error(f"[Cascade] Risk event client error: {e}")
            self._backoff.next_delay("risk_event")
            return False

    async def _post_killswitch(self, event: EnvironmentalRiskEvent) -> bool:
        """
        POST /v1/killswitch

        Returns:
            True if successful, False otherwise
        """
        url = f"{self._config.control_plane_base_url}/v1/killswitch"
        payload = {
            "scope": "GLOBAL",
            "level": 1,
            "reason": event.reason,
            "updated_by": f"adapter:{self._adapter_name}"
        }

        logger.info(f"[Cascade] Posting killswitch L1")

        try:
            client = await self._ensure_http_client()
            async with client.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self._config.request_timeout)
            ) as resp:
                if resp.status in (200, 201, 409):
                    logger.info(f"[Cascade] KillSwitch accepted: {resp.status}")
                    self._metrics.killswitch_triggered += 1
                    return True
                elif resp.status == 429:
                    retry_after = resp.headers.get("Retry-After", "60")
                    self._backoff.next_delay("killswitch", int(retry_after))
                    logger.warning(f"[Cascade] KillSwitch rate limited")
                    return False
                else:
                    logger.error(f"[Cascade] KillSwitch failed: {resp.status}")
                    return False

        except asyncio.TimeoutError:
            logger.error(f"[Cascade] KillSwitch timeout")
            self._backoff.next_delay("killswitch")
            return False
        except aiohttp.ClientError as e:
            logger.error(f"[Cascade] KillSwitch client error: {e}")
            self._backoff.next_delay("killswitch")
            return False

    async def _trigger_self_protection(self, error: Optional[Exception] = None) -> None:
        """触发本地自保"""
        if self._self_protection_active:
            return

        logger.warning(f"[Cascade] Triggering SELF_PROTECTION: {error}")
        self._state = CascadeState.SELF_PROTECTION
        self._self_protection_active = True
        self._self_protection_triggered_at = time.time()
        self._metrics.self_protection_entered += 1

        for callback in self._on_self_protection_callbacks:
            try:
                await callback(True, str(error) if error else None)
            except Exception as e:
                logger.error(f"[Cascade] Self protection callback error: {e}")

    async def _exit_self_protection(self) -> None:
        """退出本地自保"""
        if not self._self_protection_active:
            return

        logger.info(f"[Cascade] Exiting SELF_PROTECTION")
        self._self_protection_active = False
        self._self_protection_triggered_at = None
        self._metrics.self_protection_exited += 1

        for callback in self._on_self_protection_callbacks:
            try:
                await callback(False, None)
            except Exception as e:
                logger.error(f"[Cascade] Exit self protection callback error: {e}")

    def can_open_new_position(self) -> bool:
        """
        检查是否可以开新仓

        本地自保模式下只允许平仓，不允许新开仓。
        """
        if self._self_protection_active:
            logger.warning("[Cascade] Blocked: Self protection active")
            return False
        return True

    def can_cancel_order(self) -> bool:
        """检查是否可以撤单（自保模式下允许撤单）"""
        return True

    def get_local_events(self) -> list:
        """获取本地事件日志"""
        return self._local_event_log.get_recent()

    def get_status(self) -> Dict[str, Any]:
        """获取状态"""
        return {
            "state": self._state.value,
            "self_protection_active": self._self_protection_active,
            "self_protection_duration_s": (
                time.time() - self._self_protection_triggered_at
                if self._self_protection_triggered_at else 0
            ),
            "metrics": {
                "degraded_enter_count": self._metrics.degraded_enter_count,
                "degraded_exit_count": self._metrics.degraded_exit_count,
                "risk_events_reported": self._metrics.risk_events_reported,
                "risk_events_failed": self._metrics.risk_events_failed,
                "killswitch_triggered": self._metrics.killswitch_triggered,
                "self_protection_entered": self._metrics.self_protection_entered,
                "self_protection_exited": self._metrics.self_protection_exited,
            },
            "reported_dedup_keys_count": len(self._reported_dedup_keys),
            "local_events_count": len(self._local_event_log.events),
        }

    async def close(self) -> None:
        """关闭控制器"""
        if self._http_client and not self._http_client.closed:
            await self._http_client.close()
