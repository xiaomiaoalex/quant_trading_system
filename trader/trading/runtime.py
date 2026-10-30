"""Trading runtime assembly and readiness.

Startup order is fixed: storage -> ledger/risk/OMS service -> venue adapter ->
reconciliation -> trading ready -> strategy execution. Strategies are never
started before reconciliation completes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Awaitable, Callable

from trader.trading.models import NotReady, PlaceOrderCommand, ReconcileSnapshot
from trader.trading.risk import RiskPolicy
from trader.trading.service import TradingService

logger = logging.getLogger(__name__)


class TradingReadinessProvider:
    def __init__(
        self,
        runtime: "TradingRuntime",
    ):
        self.runtime = runtime

    async def check(self) -> dict[str, Any]:
        return await self.runtime.readiness()


class TradingRuntime:
    def __init__(
        self,
        *,
        store,
        exchange,
        account_id: str,
        snapshot_provider: Callable[[str], Awaitable[ReconcileSnapshot | None]],
        clock: Callable[[], int],
        policy: RiskPolicy | None = None,
        notifier: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        owner: str = "trading-runtime",
        lease_scope: str | None = None,
        lease_seconds: int = 30,
        outbox_interval_seconds: float = 1.0,
        reconcile_interval_seconds: float = 30.0,
        lease_retry_interval_seconds: float | None = None,
    ):
        self.store = store
        self.exchange = exchange
        self.account_id = account_id
        self.snapshot_provider = snapshot_provider
        self.clock = clock
        self.policy = policy or RiskPolicy()
        self.notifier = notifier
        self.owner = owner
        self.lease_scope = lease_scope or f"trading:{account_id}"
        self.lease_seconds = lease_seconds
        self.outbox_interval_seconds = outbox_interval_seconds
        self.reconcile_interval_seconds = reconcile_interval_seconds
        self.lease_retry_interval_seconds = (
            lease_retry_interval_seconds
            if lease_retry_interval_seconds is not None
            else max(1.0, lease_seconds / 4)
        )

        self.service: TradingService | None = None
        self.lease_token: int | None = None
        self.started = False
        self.ready = False
        self.last_reasons: list[str] = []
        self.stages: list[str] = []
        self._tasks: list[asyncio.Task] = []
        self._loops_started: set[str] = set()
        self._stopping = False

    async def start(self) -> bool:
        self.stages.append("storage")
        try:
            await self.store.initialize()
        except Exception as exc:
            self.stages.append("storage_failed")
            self.last_reasons = [f"STORAGE_UNAVAILABLE: {exc}"]
            return False

        self.stages.append("ledger_risk_oms")
        self.service = TradingService(
            store=self.store,
            exchange=self.exchange,
            policy=self.policy,
            clock=self.clock,
        )

        self.stages.append("adapter")
        self._stopping = False

        # Background loops must run even while the account is blocked: the lease
        # loop retries ownership and the reconcile loop keeps the view fresh.
        self._ensure_background_loops()

        if hasattr(self.store, "acquire_lease"):
            self.stages.append("lease")
            self.lease_token = await self.store.acquire_lease(
                self.lease_scope, self.owner, self.lease_seconds
            )
            if self.lease_token is None:
                self.started = False
                self.last_reasons = ["EXECUTION_INSTANCE_CONFLICT"]
                self.stages.append("blocked")
                return False

        self.started = True
        return await self._complete_startup()

    async def _complete_startup(self) -> bool:
        """Reconcile and resume queued commands once this process owns the account."""
        self.stages.append("reconcile")
        try:
            snapshot = await self.snapshot_provider(self.account_id)
        except Exception as exc:
            snapshot = None
            self.last_reasons = [f"RECONCILIATION_UNAVAILABLE: {exc}"]
        if snapshot is not None:
            view = await self.service.recover(snapshot)
            self.ready = view.ready
            self.last_reasons = list(view.reasons)
        else:
            view = await self.service.risk_view(self.account_id)
            self.ready = False
            self.last_reasons = list(view.reasons) or ["RECONCILIATION_UNAVAILABLE"]

        if not self.ready:
            self.stages.append("blocked")
            return False

        # QUEUED commands never reached the venue (SENDING was not committed).
        self.stages.append("resume_queued")
        await self.service.resume_queued(self.account_id)
        view = await self.service.risk_view(self.account_id)
        self.ready = view.ready
        self.last_reasons = list(view.reasons)
        if not self.ready:
            self.stages.append("blocked")
            return False

        self.stages.append("ready")
        return True

    def _ensure_background_loops(self) -> None:
        if self.notifier is not None and "outbox" not in self._loops_started:
            self._tasks.append(asyncio.create_task(self._outbox_loop()))
            self._loops_started.add("outbox")
        if self.reconcile_interval_seconds > 0 and "reconcile" not in self._loops_started:
            self._tasks.append(asyncio.create_task(self._reconcile_loop()))
            self._loops_started.add("reconcile")
        if hasattr(self.store, "renew_lease") and "lease" not in self._loops_started:
            self._tasks.append(asyncio.create_task(self._lease_loop()))
            self._loops_started.add("lease")

    async def stop(self) -> None:
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        self._loops_started.clear()
        if self.lease_token is not None and hasattr(self.store, "release_lease"):
            await self.store.release_lease(self.lease_scope, self.owner, self.lease_token)
        self.lease_token = None
        self.started = False
        self.ready = False

    @property
    def strategy_start_allowed(self) -> bool:
        return self.ready and self.started

    async def readiness(self) -> dict[str, Any]:
        db_available = True
        if hasattr(self.store, "ping"):
            try:
                db_available = await self.store.ping()
            except Exception:
                db_available = False
        reasons: list[str] = []
        generation = None
        checked_at_ms = None
        if not db_available:
            reasons.append("STORAGE_UNAVAILABLE")
        elif self.service is not None:
            try:
                view = await self.service.risk_view(self.account_id)
                reasons.extend(view.reasons)
                generation = view.generation
                checked_at_ms = view.checked_at_ms
            except Exception:
                reasons.append("STORAGE_UNAVAILABLE")
        else:
            reasons.append("TRADING_RUNTIME_NOT_STARTED")
        if not self.started and "TRADING_RUNTIME_NOT_STARTED" not in reasons:
            reasons.append("TRADING_RUNTIME_NOT_STARTED")
        if self.started and not self.ready:
            reasons.extend(self.last_reasons)
        if self.lease_token is None:
            reasons.append("EXECUTION_INSTANCE_CONFLICT")
        return {
            "ready": not reasons,
            "reasons": list(dict.fromkeys(reasons)),
            "account_id": self.account_id,
            "db_available": db_available,
            "instance_owner": self.owner,
            "lease_token": self.lease_token,
            "generation": generation,
            "checked_at_ms": checked_at_ms,
        }

    async def place(self, command: PlaceOrderCommand, actor: str = "system"):
        if not self.strategy_start_allowed or self.service is None:
            raise NotReady("TRADING_RUNTIME_NOT_READY")
        return await self.service.place(command, actor=actor)

    async def drain_outbox(self) -> int:
        if self.service is None or self.notifier is None:
            return 0
        return await self.service.dispatch_outbox(self.account_id, self.notifier)

    async def _reconcile_loop(self) -> None:
        """Keep the account view aligned with the venue while the runtime runs."""
        while not self._stopping:
            await asyncio.sleep(self.reconcile_interval_seconds)
            if self.service is None:
                return
            if self.lease_token is None:
                # The account is not ours yet; never reconcile another owner's book.
                continue
            try:
                snapshot = await self.snapshot_provider(self.account_id)
            except Exception as exc:
                logger.warning("Periodic reconciliation provider failed: %s", exc)
                continue
            if snapshot is None:
                continue
            try:
                view = await self.service.recover(snapshot)
            except Exception as exc:
                logger.warning("Periodic reconciliation failed: %s", exc)
                continue
            self.ready = view.ready
            self.last_reasons = list(view.reasons)

    async def _outbox_loop(self) -> None:
        while not self._stopping:
            try:
                await self.drain_outbox()
            except Exception:
                pass
            await asyncio.sleep(self.outbox_interval_seconds)

    async def _lease_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self.lease_retry_interval_seconds)
            if self.lease_token is None:
                token = await self.store.acquire_lease(
                    self.lease_scope, self.owner, self.lease_seconds
                )
                if token is None:
                    continue
                logger.info("Execution lease acquired; completing startup")
                self.lease_token = token
                self.started = True
                self.last_reasons = [
                    reason
                    for reason in self.last_reasons
                    if reason != "EXECUTION_INSTANCE_CONFLICT"
                ]
                await self._complete_startup()
                continue
            if not await self.store.renew_lease(
                self.lease_scope, self.owner, self.lease_token, self.lease_seconds
            ):
                self.ready = False
                self.lease_token = None
                self.started = False
                self.last_reasons = ["EXECUTION_INSTANCE_CONFLICT"]
                continue
