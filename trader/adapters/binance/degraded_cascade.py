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
from typing import TYPE_CHECKING, Dict, Optional, Any, Callable, Awaitable, Set
from enum import Enum

if TYPE_CHECKING:
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
    # Anti-flap auto-downgrade metrics
    downgrade_requested: int = 0
    downgrade_succeeded: int = 0
    downgrade_failed: int = 0


class DegradedCascadeController:
    """
    级联保护控制器

    当适配器进入 DEGRADED_MODE 时：
    1. 向 Control Plane 上报风险事件
    2. 触发 KillSwitch L1
    3. 如果 Control Plane 不可达，触发本地自保

    使用队列+单worker模式保证：
    - 不阻塞健康回调
    - 有上限重试 + 真正退避
    - Fail-closed 计时器
    """

    def __init__(
        self,
        control_plane_base_url: str,
        http_client: "Optional[aiohttp.ClientSession]" = None,
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

        self._reported_dedup_keys: Dict[str, float] = {}
        self._last_report_ts: Dict[str, float] = {}

        self._local_event_log = LocalEventLog()

        self._self_protection_active = False
        self._self_protection_triggered_at: Optional[float] = None

        self._on_self_protection_callbacks: list = []
        self._on_recovery_callbacks: list = []

        self._q: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        self._unreachable_since_ms: Optional[int] = None
        self._local_killswitch_active = False
        self._cooldown_until_ms: Optional[int] = None
        self._dedup_ttl_ms = 600000

        # Anti-flap: consecutive healthy check tracking for auto-downgrade
        self._consecutive_healthy_checks: int = 0
        self._healthy_check_threshold: int = 3
        self._downgrade_requested_at_ms: Optional[int] = None

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

    async def start(self) -> None:
        """启动 worker 循环"""
        if self._worker_task is None or self._worker_task.done():
            self._stop_event.clear()
            self._worker_task = asyncio.create_task(self._worker_loop())
            logger.info("[Cascade] Worker loop started")

    async def stop(self) -> None:
        """停止 worker 循环"""
        self._stop_event.set()
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            logger.info("[Cascade] Worker loop stopped")

        # 关闭 HTTP 客户端，避免连接泄漏
        if self._http_client and not self._http_client.closed:
            await self._http_client.close()
            self._http_client = None
            logger.info("[Cascade] HTTP client closed")

    async def _ensure_http_client(self) -> "aiohttp.ClientSession":
        """确保 HTTP 客户端存在"""
        import aiohttp
        if self._http_client is None or self._http_client.closed:
            self._http_client = aiohttp.ClientSession()
        return self._http_client

    async def _worker_loop(self) -> None:
        """Worker 循环：处理上报队列（去重、冷却、重试、退避、Fail-closed）"""
        MAX_RETRIES = self._config.max_retries_per_event

        while not self._stop_event.is_set():
            try:
                envelope = await asyncio.wait_for(
                    self._q.get(),
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            health = envelope["health"]
            reason = envelope["reason"]

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

            now_ms = int(time.time() * 1000)

            if self._local_killswitch_active:
                logger.debug("[Cascade] Local killswitch active, skipping report")
                continue

            if self._cooldown_until_ms and now_ms < self._cooldown_until_ms:
                logger.debug("[Cascade] In cooldown period, skipping")
                continue

            if event.dedup_key in self._reported_dedup_keys:
                last_ts = self._reported_dedup_keys.get(event.dedup_key, 0)
                if now_ms - last_ts < self._dedup_ttl_ms:
                    logger.debug(f"[Cascade] Dedup key still fresh: {event.dedup_key}")
                    continue
                else:
                    del self._reported_dedup_keys[event.dedup_key]

            self._local_event_log.add(event)
            event.report_attempts = 0

            for attempt in range(MAX_RETRIES):
                risk_ok = await self._post_risk_event(event)
                kill_ok = await self._post_killswitch(event)

                if risk_ok and kill_ok:
                    self._metrics.risk_events_reported += 1
                    self._reported_dedup_keys[event.dedup_key] = now_ms
                    self._last_report_ts["risk"] = time.time()
                    event.is_reported = True
                    self._unreachable_since_ms = None
                    break

                self._metrics.risk_events_failed += 1
                event.report_attempts += 1

                delay = self._backoff.next_delay("risk_event")
                logger.warning(f"[Cascade] Report failed, retry {attempt + 1}/{MAX_RETRIES} in {delay}s")

                await asyncio.sleep(delay)

                now_ms = int(time.time() * 1000)
                if self._unreachable_since_ms and (now_ms - self._unreachable_since_ms) >= self._config.self_protection_trigger_ms:
                    await self._trigger_self_protection(Exception("Control plane unreachable > threshold"))
                    self._local_killswitch_active = True
                    break

            if not event.is_reported and not self._local_killswitch_active:
                if self._unreachable_since_ms is None:
                    self._unreachable_since_ms = int(time.time() * 1000)

    async def on_adapter_health_changed(
        self,
        health: AdapterHealthReport,
        reason: str = ""
    ) -> None:
        """
        处理适配器健康状态变化（非阻塞，只 enqueue）

        Args:
            health: 适配器健康报告
            reason: 变化原因
        """
        if health.overall_health == AdapterHealth.DEGRADED:
            await self._on_degraded_enter(health, reason)
        elif health.overall_health == AdapterHealth.HEALTHY:
            # DEGRADED → RECOVERING transition (first HEALTHY after degraded)
            if self._state == CascadeState.DEGRADED:
                await self._on_degraded_exit(health, reason)
            elif self._state == CascadeState.RECOVERING:
                await self._on_healthy_in_recovering(health, reason)

    def _enqueue_report(self, health: AdapterHealthReport, reason: str) -> None:
        """将上报任务加入队列（非阻塞）"""
        envelope = {
            "health": health,
            "reason": reason,
            "enqueued_at": time.time()
        }
        try:
            self._q.put_nowait(envelope)
        except asyncio.QueueFull:
            logger.warning("[Cascade] Queue full, dropping report")

    async def _on_degraded_enter(
        self,
        health: AdapterHealthReport,
        reason: str
    ) -> None:
        """处理进入 DEGRADED 模式（使用队列上报）"""
        if self._state == CascadeState.DEGRADED:
            logger.debug(f"[Cascade] Already in DEGRADED state, skipping")
            return

        logger.warning(f"[Cascade] Entering DEGRADED mode: {reason}")
        self._state = CascadeState.DEGRADED
        self._metrics.degraded_enter_count += 1
        self._metrics.last_degraded_ts = time.time()

        cooldown_ms = 60000
        self._cooldown_until_ms = int(time.time() * 1000) + cooldown_ms

        self._enqueue_report(health, reason)

    async def _on_degraded_exit(
        self,
        health: AdapterHealthReport,
        reason: str
    ) -> None:
        """处理退出 DEGRADED 模式（Anti-Flap：进入 RECOVERING 状态）"""
        if self._state == CascadeState.NORMAL:
            return

        logger.info(f"[Cascade] Exiting DEGRADED mode: {reason}")
        self._state = CascadeState.RECOVERING
        self._metrics.degraded_exit_count += 1

        now_ms = int(time.time() * 1000)
        expired_keys = [k for k, ts in self._reported_dedup_keys.items() if now_ms - ts >= self._dedup_ttl_ms]
        for k in expired_keys:
            del self._reported_dedup_keys[k]

        self._unreachable_since_ms = None

        await self._exit_self_protection()

        # Reset consecutive healthy counter for downgrade tracking
        self._consecutive_healthy_checks = 0

    async def _on_healthy_in_recovering(
        self,
        health: AdapterHealthReport,
        reason: str
    ) -> None:
        """
        处理 RECOVERING 状态下的 HEALTHY 健康报告。

        Anti-Flap 设计：
        - 连续 3 次 HEALTHY 检查后才触发自动降级
        - 降级请求有 30s 幂等窗口，防止重复请求
        - 降级成功后进入 NORMAL，失败则保持在 RECOVERING
        """
        if self._state != CascadeState.RECOVERING:
            return

        logger.info(
            f"[Cascade] HEALTHY in RECOVERING: "
            f"consecutive={self._consecutive_healthy_checks + 1}/{self._healthy_check_threshold}"
        )

        self._consecutive_healthy_checks += 1

        if self._consecutive_healthy_checks >= self._healthy_check_threshold:
            await self._request_killswitch_downgrade()

    async def _request_killswitch_downgrade(self) -> None:
        """
        请求 KillSwitch 降级到 L0（NORMAL）。

        Anti-Flap 幂等设计：
        - 使用 `_downgrade_requested_at_ms` + 30s 窗口防止重复请求
        - 即使 POST 成功两次，KillSwitchService.set_state() 本身是幂等的
        """
        now_ms = int(time.time() * 1000)

        # 幂等窗口检查：30s 内不重复请求
        if self._downgrade_requested_at_ms is not None:
            if now_ms - self._downgrade_requested_at_ms < 30_000:
                logger.debug("[Cascade] Downgrade already requested within 30s window")
                return

        self._downgrade_requested_at_ms = now_ms
        self._metrics.downgrade_requested += 1

        logger.info(
            "[KillSwitch] [Cascade] >>> Auto-downgrade request: "
            "level=0 (NORMAL) reason='Adapter recovered, consecutive_healthy=%s' "
            "threshold=%s",
            self._consecutive_healthy_checks,
            self._healthy_check_threshold,
        )

        try:
            success = await self._post_killswitch_downgrade()

            if success:
                logger.info("[KillSwitch] [Cascade] <<< Auto-downgrade accepted: level=0 (NORMAL)")
                self._state = CascadeState.NORMAL
                self._metrics.last_recovery_ts = time.time()
                self._metrics.downgrade_succeeded += 1
                self._consecutive_healthy_checks = 0

                for callback in self._on_recovery_callbacks:
                    try:
                        await callback()
                    except Exception as e:
                        logger.error(f"[Cascade] Recovery callback error: {e}")
            else:
                logger.warning("[KillSwitch] [Cascade] Auto-downgrade failed, will retry next check")
                self._metrics.downgrade_failed += 1
                self._consecutive_healthy_checks = 0

        except Exception as e:
            logger.error(f"[Cascade] Auto-downgrade exception: {e}")
            self._metrics.downgrade_failed += 1
            self._consecutive_healthy_checks = 0

    async def _post_killswitch_downgrade(self) -> bool:
        """
        POST /v1/killswitch 请求降级到 L0。
        """
        import aiohttp
        url = f"{self._config.control_plane_base_url}/v1/killswitch"

        payload = {
            "scope": "GLOBAL",
            "level": 0,
            "reason": f"Adapter auto-recovery: {self._consecutive_healthy_checks} consecutive healthy checks",
            "updated_by": f"adapter:{self._adapter_name}:auto-downgrade"
        }

        try:
            client = await self._ensure_http_client()
            async with client.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self._config.request_timeout)
            ) as resp:
                resp_body = await resp.json() if resp.content_type == 'application/json' else None

                if resp.status in (200, 201, 409):
                    new_level = resp_body.get("level", 0) if resp_body else 0
                    logger.info(
                        "[KillSwitch] [Cascade] <<< Auto-downgrade response: "
                        "level=%s status=%s",
                        new_level,
                        resp.status,
                    )
                    return True
                elif resp.status == 429:
                    retry_after = resp.headers.get("Retry-After", "60")
                    self._backoff.next_delay("killswitch_downgrade", int(retry_after))
                    logger.warning(
                        "[KillSwitch] [Cascade] Auto-downgrade rate limited: retry_after=%s",
                        retry_after
                    )
                    return False
                else:
                    logger.error(
                        "[KillSwitch] [Cascade] Auto-downgrade failed: status=%s",
                        resp.status
                    )
                    return False

        except asyncio.TimeoutError:
            logger.error("[KillSwitch] [Cascade] Auto-downgrade timeout")
            self._backoff.next_delay("killswitch_downgrade")
            return False
        except aiohttp.ClientError as e:
            logger.error(f"[KillSwitch] [Cascade] Auto-downgrade client error: {e}")
            self._backoff.next_delay("killswitch_downgrade")
            return False

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
            self._reported_dedup_keys[event.dedup_key] = time.time()
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
        """
        import aiohttp
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

        使用 event.recommended_level 作为 KillSwitch 级别。
        与控制面的 Fail-Closed 行为保持一致。
        """
        import aiohttp
        url = f"{self._config.control_plane_base_url}/v1/killswitch"

        recommended_lvl = event.recommended_level.value if isinstance(event.recommended_level, RecommendedLevel) else int(event.recommended_level)
        level_names = {0: "NORMAL", 1: "NO_NEW_POSITIONS", 2: "CLOSE_ONLY", 3: "FULL_STOP"}

        payload = {
            "scope": "GLOBAL",
            "level": recommended_lvl,
            "reason": event.reason,
            "updated_by": f"adapter:{self._adapter_name}"
        }

        # [KillSwitch] 日志入口：来自适配器的 KillSwitch 请求
        logger.info(
            "[KillSwitch] [Cascade] >>> Outgoing KillSwitch request: "
            "adapter=%s level=%s (%s) reason='%s' dedup_key='%s' url=%s",
            self._adapter_name,
            recommended_lvl,
            level_names.get(recommended_lvl, f"LEVEL_{recommended_lvl}"),
            event.reason,
            event.dedup_key,
            url,
        )
        logger.info(
            "[KillSwitch] [Cascade] Risk metrics: severity=%s scope=%s",
            event.severity.value if hasattr(event.severity, 'value') else event.severity,
            event.scope.value if hasattr(event.scope, 'value') else event.scope,
        )

        try:
            client = await self._ensure_http_client()
            async with client.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self._config.request_timeout)
            ) as resp:
                resp_body = await resp.json() if resp.content_type == 'application/json' else None

                if resp.status in (200, 201, 409):
                    new_level = resp_body.get("level", recommended_lvl) if resp_body else recommended_lvl
                    new_reason = resp_body.get("reason") if resp_body else None
                    logger.info(
                        "[KillSwitch] [Cascade] <<< KillSwitch accepted: "
                        "level=%s (%s) status=%s new_state_reason='%s'",
                        new_level,
                        level_names.get(new_level, f"LEVEL_{new_level}"),
                        resp.status,
                        new_reason or "N/A",
                    )
                    self._metrics.killswitch_triggered += 1
                    return True
                elif resp.status == 429:
                    retry_after = resp.headers.get("Retry-After", "60")
                    self._backoff.next_delay("killswitch", int(retry_after))
                    logger.warning(
                        "[KillSwitch] [Cascade] KillSwitch rate limited: retry_after=%s",
                        retry_after
                    )
                    return False
                else:
                    logger.error(
                        "[KillSwitch] [Cascade] KillSwitch failed: level=%s status=%s",
                        recommended_lvl,
                        resp.status
                    )
                    return False

        except asyncio.TimeoutError:
            logger.error(
                "[KillSwitch] [Cascade] KillSwitch timeout: level=%s (%s)",
                recommended_lvl,
                level_names.get(recommended_lvl, f"LEVEL_{recommended_lvl}")
            )
            self._backoff.next_delay("killswitch")
            return False
        except aiohttp.ClientError as e:
            logger.error(
                "[KillSwitch] [Cascade] KillSwitch client error: level=%s (%s) error=%s",
                recommended_lvl,
                level_names.get(recommended_lvl, f"LEVEL_{recommended_lvl}"),
                e
            )
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
                "downgrade_requested": self._metrics.downgrade_requested,
                "downgrade_succeeded": self._metrics.downgrade_succeeded,
                "downgrade_failed": self._metrics.downgrade_failed,
            },
            "anti_flap": {
                "consecutive_healthy_checks": self._consecutive_healthy_checks,
                "healthy_check_threshold": self._healthy_check_threshold,
                "downgrade_requested_at_ms": self._downgrade_requested_at_ms,
            },
            "reported_dedup_keys_count": len(self._reported_dedup_keys),
            "local_events_count": len(self._local_event_log.events),
        }

    async def close(self) -> None:
        """关闭控制器"""
        if self._http_client and not self._http_client.closed:
            await self._http_client.close()
