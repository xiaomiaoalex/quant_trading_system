"""Trading runtime assembly and readiness.

Startup order is fixed: storage -> ledger/risk/OMS service -> venue adapter ->
reconciliation -> trading ready -> strategy execution. Strategies are never
started before reconciliation completes.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Awaitable, Callable

from trader.trading.models import NotReady, PlaceOrderCommand, ReconcileSnapshot
from trader.trading.risk import RiskPolicy
from trader.trading.service import TradingService


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

        self.service: TradingService | None = None
        self.lease_token: int | None = None
        self.started = False
        self.ready = False
        self.last_reasons: list[str] = []
        self.stages: list[str] = []
        self._tasks: list[asyncio.Task] = []
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

        if self.ready and hasattr(self.store, "acquire_lease"):
            self.lease_token = await self.store.acquire_lease(
                self.lease_scope, self.owner, self.lease_seconds
            )
            if self.lease_token is None:
                self.ready = False
                self.last_reasons.append("EXECUTION_INSTANCE_CONFLICT")
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
        self.started = True
        self._stopping = False
        if self.notifier is not None:
            self._tasks.append(asyncio.create_task(self._outbox_loop()))
        if hasattr(self.store, "renew_lease"):
            self._tasks.append(asyncio.create_task(self._lease_loop()))
        return True

    async def stop(self) -> None:
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
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

    async def _outbox_loop(self) -> None:
        while not self._stopping:
            try:
                await self.drain_outbox()
            except Exception:
                pass
            await asyncio.sleep(self.outbox_interval_seconds)

    async def _lease_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(max(1.0, self.lease_seconds / 2))
            if self.lease_token is None:
                return
            if not await self.store.renew_lease(
                self.lease_scope, self.owner, self.lease_token, self.lease_seconds
            ):
                self.ready = False
                self.last_reasons.append("EXECUTION_INSTANCE_CONFLICT")
                return
