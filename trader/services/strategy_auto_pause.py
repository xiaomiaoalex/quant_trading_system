"""
Strategy Auto-Pause Service (Stage 6.5)
=========================================
当策略在滑动时间窗口内被风控拒绝达到阈值时，自动暂停策略。
风控恢复后自动探测并恢复。

触发条件：单个 strategy 在 60 秒内被拒绝 >= 10 次（可配置）
恢复探测：30 秒周期，连续 2 次健康 → 自动 resume
手动恢复：POST /v1/strategies/{deployment_id}/force-resume
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trader.core.domain.models.signal import Signal
    from trader.core.domain.models.order import OrderSide
    from trader.api.routes.sse import SSEManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AutoPauseConfig:
    window_sec: int = 60
    threshold: int = 10
    probe_interval_sec: int = 30
    consecutive_probe_required: int = 2  # 连续 2 次探测通过才恢复


@dataclass(slots=True)
class PausedRecord:
    """记录被自动暂停的策略状态"""

    strategy_id: str
    deployment_id: str
    last_reason: str
    reject_count: int
    paused_at_ms: int
    consecutive_probe_pass: int = 0


@dataclass(slots=True)
class AutoPauseDecision:
    """record_rejection() 的返回值"""

    should_pause: bool
    current_count: int
    threshold: int
    window_sec: int


class StrategyAutoPauseService:
    """逐策略滑动窗口拒绝计数 + 自动暂停 + 后台恢复探测。"""

    def __init__(
        self,
        oms_handler: Any,  # OMSCallbackHandler
        sse_manager: "SSEManager | None",
        candidate_service: Any,  # StrategyCandidateService
        config: AutoPauseConfig | None = None,
    ) -> None:
        self._oms = oms_handler
        self._sse = sse_manager
        self._candidate = candidate_service
        self._cfg = config or AutoPauseConfig()

        # strategy_id -> deque of rejection timestamps
        self._windows: dict[str, asyncio.Lock] = {}
        self._timestamps: dict[str, deque[float]] = {}

        # deployment_id -> PausedRecord
        self._paused: dict[str, PausedRecord] = {}
        self._pause_lock = asyncio.Lock()

        self._probe_task: asyncio.Task | None = None
        self._running: bool = False

    # ── Public API ─────────────────────────────────────────────────────────────

    async def record_rejection(
        self,
        strategy_id: str,
        deployment_id: str,
        reason: str,
    ) -> AutoPauseDecision:
        """
        OMSCallback 每次拒绝时调用。

        超阈值时触发自动暂停，但不阻塞主流程。
        """
        now = time.monotonic()
        window = self._timestamps.setdefault(strategy_id, deque())
        lock = self._windows.setdefault(strategy_id, asyncio.Lock())

        async with lock:
            # 清理窗口外的旧时间戳
            cutoff = now - self._cfg.window_sec
            while window and window[0] < cutoff:
                window.popleft()

            window.append(now)
            count = len(window)

        decision = AutoPauseDecision(
            should_pause=count >= self._cfg.threshold,
            current_count=count,
            threshold=self._cfg.threshold,
            window_sec=self._cfg.window_sec,
        )

        if decision.should_pause:
            async with self._pause_lock:
                if deployment_id not in self._paused:
                    await self._auto_pause(strategy_id, deployment_id, reason, count)

        return decision

    async def force_resume(
        self,
        deployment_id: str,
        requested_by: str,
    ) -> None:
        """手动跳过探测条件直接恢复（POST /v1/strategies/{id}/force-resume）"""
        if deployment_id not in self._paused:
            logger.warning("[AutoPause] force_resume: %s not paused by risk", deployment_id)
            return

        record = self._paused[deployment_id]
        await self._auto_resume(
            record.strategy_id,
            deployment_id,
            triggered_by="force_resume",
            requested_by=requested_by,
        )

    def get_paused_strategies(self) -> list[dict[str, Any]]:
        """返回所有被自动暂停的策略，供 API 查询"""
        return [
            {
                "deployment_id": dep_id,
                "strategy_id": r.strategy_id,
                "last_reason": r.last_reason,
                "reject_count": r.reject_count,
                "paused_at_ms": r.paused_at_ms,
                "consecutive_probe_pass": r.consecutive_probe_pass,
                "probe_required": self._cfg.consecutive_probe_required,
                "window_sec": self._cfg.window_sec,
                "threshold": self._cfg.threshold,
            }
            for dep_id, r in self._paused.items()
        ]

    def is_paused(self, deployment_id: str) -> bool:
        return deployment_id in self._paused

    async def start(self) -> None:
        """启动后台恢复探测循环"""
        if self._running:
            return
        self._running = True
        self._probe_task = asyncio.create_task(self._probe_loop())
        logger.info(
            "[AutoPause] Started (window=%ds threshold=%d probe_interval=%ds)",
            self._cfg.window_sec,
            self._cfg.threshold,
            self._cfg.probe_interval_sec,
        )

    async def stop(self) -> None:
        """停止后台探测循环"""
        self._running = False
        if self._probe_task is not None:
            self._probe_task.cancel()
            try:
                await self._probe_task
            except asyncio.CancelledError:
                pass
            self._probe_task = None
        logger.info("[AutoPause] Stopped")

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _auto_pause(
        self,
        strategy_id: str,
        deployment_id: str,
        last_reason: str,
        reject_count: int,
    ) -> None:
        """1) pause runner  2) candidate PAUSED_BY_RISK  3) SSE 4) 记录"""
        paused_at_ms = int(time.time() * 1000)
        logger.warning(
            "[AutoPause] Pausing strategy: %s (deployment=%s) after %d rejections " "(reason: %s)",
            strategy_id,
            deployment_id,
            reject_count,
            last_reason,
        )

        # 1) StrategyRunner pause
        try:
            from trader.api.routes.strategies import get_strategy_runner

            runner = get_strategy_runner()
            await runner.pause(deployment_id)
        except Exception as exc:
            logger.warning("[AutoPause] runner.pause failed (non-fatal): %s", exc)

        # 2) Candidate state: PAUSED_BY_RISK
        candidate_dict = self._find_candidate_by_deployment(deployment_id)
        try:
            if candidate_dict is not None:
                self._transition_candidate(
                    candidate_dict,
                    "PAUSED_BY_RISK",
                    f"auto_paused_by_risk: {last_reason}",
                )
        except Exception as exc:
            logger.warning("[AutoPause] candidate state transition failed (non-fatal): %s", exc)

        self._append_candidate_event(
            candidate_dict,
            event_type="strategy_candidate.auto_paused",
            deployment_id=deployment_id,
            strategy_id=strategy_id,
            payload={
                "reason": last_reason,
                "reject_count_in_window": reject_count,
                "window_sec": self._cfg.window_sec,
                "threshold": self._cfg.threshold,
                "paused_at_ms": paused_at_ms,
            },
        )

        # 3) SSE broadcast
        await self._broadcast_update(
            deployment_id,
            strategy_id,
            "auto_paused",
            {
                "reason": last_reason,
                "reject_count": reject_count,
                "reject_count_in_window": reject_count,
                "window_sec": self._cfg.window_sec,
                "threshold": self._cfg.threshold,
                "paused_at_ms": paused_at_ms,
            },
        )

        # 4) 记录
        self._paused[deployment_id] = PausedRecord(
            strategy_id=strategy_id,
            deployment_id=deployment_id,
            last_reason=last_reason,
            reject_count=reject_count,
            paused_at_ms=paused_at_ms,
        )

    async def _auto_resume(
        self,
        strategy_id: str,
        deployment_id: str,
        triggered_by: str,
        requested_by: str = "auto_probe",
    ) -> None:
        """1) runner resume  2) candidate PAPER_RUNNING  3) SSE"""
        logger.info(
            "[AutoPause] Resuming strategy: %s (triggered_by=%s)",
            deployment_id,
            triggered_by,
        )

        record = self._paused.pop(deployment_id, None)
        resumed_at_ms = int(time.time() * 1000)

        # 1) StrategyRunner resume
        try:
            from trader.api.routes.strategies import get_strategy_runner

            runner = get_strategy_runner()
            await runner.resume(deployment_id)
        except Exception as exc:
            logger.warning("[AutoPause] runner.resume failed (non-fatal): %s", exc)

        # 2) Candidate state: PAPER_RUNNING
        candidate_dict = self._find_candidate_by_deployment(deployment_id)
        try:
            if candidate_dict is not None:
                self._transition_candidate(
                    candidate_dict,
                    "PAPER_RUNNING",
                    f"auto_resumed_by_risk: {triggered_by}",
                )
        except Exception as exc:
            logger.warning("[AutoPause] candidate resume transition failed (non-fatal): %s", exc)

        probe_pass = record.consecutive_probe_pass if record else 0
        self._append_candidate_event(
            candidate_dict,
            event_type="strategy_candidate.auto_resumed",
            deployment_id=deployment_id,
            strategy_id=strategy_id,
            payload={
                "triggered_by": triggered_by,
                "requested_by": requested_by,
                "probe_consecutive_pass": probe_pass,
                "probe_required": self._cfg.consecutive_probe_required,
                "paused_duration_ms": (resumed_at_ms - record.paused_at_ms) if record else None,
                "resumed_at_ms": resumed_at_ms,
            },
        )
        self._timestamps.pop(strategy_id, None)

        # 3) SSE broadcast
        await self._broadcast_update(
            deployment_id,
            strategy_id,
            "auto_resumed",
            {
                "triggered_by": triggered_by,
                "requested_by": requested_by,
                "probe_consecutive_pass": probe_pass,
                "paused_duration_ms": (resumed_at_ms - record.paused_at_ms) if record else None,
                "resumed_at_ms": resumed_at_ms,
            },
        )

    async def _probe_loop(self) -> None:
        """后台 30s 周期：对所有暂停策略做风险探测，连续通过 2 次则 resume。"""
        while self._running:
            try:
                await asyncio.sleep(self._cfg.probe_interval_sec)
            except asyncio.CancelledError:
                break

            if not self._running:
                break

            await self._probe_paused_once()

    async def _probe_paused_once(self) -> None:
        """Run one recovery probe pass over currently auto-paused strategies."""
        # 遍历快照避免在迭代中修改字典
        paused_copy = dict(self._paused)
        for dep_id, record in paused_copy.items():
            try:
                is_healthy = await self._is_risk_healthy(record.strategy_id)
            except Exception as exc:
                logger.warning("[AutoPause] Probe failed for %s: %s", dep_id, exc)
                is_healthy = False

            if is_healthy:
                record.consecutive_probe_pass += 1
                logger.info(
                    "[AutoPause] Probe pass %d/%d for %s",
                    record.consecutive_probe_pass,
                    self._cfg.consecutive_probe_required,
                    dep_id,
                )
                if record.consecutive_probe_pass >= self._cfg.consecutive_probe_required:
                    await self._auto_resume(
                        record.strategy_id,
                        dep_id,
                        triggered_by="auto_probe",
                    )
            else:
                # 探测失败，重置计数
                if record.consecutive_probe_pass > 0:
                    record.consecutive_probe_pass = 0
                    logger.info("[AutoPause] Probe fail, resetting counter for %s", dep_id)

    async def _is_risk_healthy(self, strategy_id: str) -> bool:
        """
        构造 dummy Signal 并走完整 pre_trade_risk_check，
        如果 passed=True 则视为健康。
        """
        check = getattr(self._oms, "_pre_trade_risk_check", None)
        if check is None:
            # 无风控回调 → 视为健康
            return True

        try:
            dummy_signal = self._make_dummy_signal(strategy_id)
            result = check(dummy_signal)
            if inspect.isawaitable(result):
                result = await result
            passed = getattr(result, "passed", True)
            return bool(passed)
        except Exception as exc:
            logger.debug("[AutoPause] Risk probe for %s raised: %s", strategy_id, exc)
            return False

    def _make_dummy_signal(self, strategy_id: str) -> "Signal":
        """构造一个最小化的 dummy Signal 用于风险探测。"""
        from trader.core.domain.models.signal import Signal, SignalType

        return Signal(
            signal_id=f"probe_{strategy_id}_{int(time.time() * 1000)}",
            strategy_name=strategy_id,
            symbol="BTCUSDT",
            signal_type=SignalType.BUY,  # 保守地测试开多方向
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
            metadata={"strategy_id": strategy_id, "auto_pause_probe": True},
        )

    def _find_candidate_by_deployment(self, deployment_id: str) -> dict[str, Any] | None:
        storage = getattr(self._candidate, "_storage", None)
        if storage is None:
            return None
        for candidate in storage.list_strategy_candidates(limit=10000):
            if str(candidate.get("deployment_id") or "") == deployment_id:
                return candidate
        return None

    def _transition_candidate(
        self,
        candidate: dict[str, Any],
        to_status: str,
        reason: str,
    ) -> None:
        current = str(candidate.get("status") or "")
        if current == to_status:
            return
        self._candidate._transition(candidate, to_status, reason)

    def _append_candidate_event(
        self,
        candidate: dict[str, Any] | None,
        event_type: str,
        deployment_id: str,
        strategy_id: str,
        payload: dict[str, Any],
    ) -> None:
        storage = getattr(self._candidate, "_storage", None)
        if storage is None:
            return

        candidate_id = str(candidate.get("candidate_id")) if candidate is not None else None
        event_payload = {
            "candidate_id": candidate_id,
            "deployment_id": deployment_id,
            "strategy_id": strategy_id,
            **payload,
        }
        stream_key = (
            f"strategy_candidate:{candidate_id}" if candidate_id else f"deployment:{deployment_id}"
        )
        storage.append_event(
            {
                "stream_key": stream_key,
                "event_type": event_type,
                "schema_version": 1,
                "trace_id": (
                    f"candidate:{candidate_id}" if candidate_id else f"deployment:{deployment_id}"
                ),
                "ts_ms": int(time.time() * 1000),
                "source": "strategy_auto_pause_service",
                "payload": event_payload,
            }
        )
        if candidate_id and candidate is not None:
            events = list(candidate.get("events", []))
            events.append(event_payload)
            storage.update_strategy_candidate(candidate_id, {"events": events})

    async def _broadcast_update(
        self,
        deployment_id: str,
        strategy_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """SSE 广播策略状态更新"""
        if self._sse is None:
            return
        try:
            await self._sse.broadcast(
                "strategies",
                "strategy_update",
                {
                    "deployment_id": deployment_id,
                    "strategy_id": strategy_id,
                    "event_type": event_type,
                    **payload,
                },
            )
        except Exception as exc:
            logger.warning("[AutoPause] SSE broadcast failed: %s", exc)
