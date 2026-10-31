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

from trader.trading.models import (
    CommandReceipt,
    ExchangeOrderResult,
    ExchangeOutcome,
    NotReady,
    PlaceOrderCommand,
    ReconcileSnapshot,
)
from trader.trading.risk import RiskPolicy
from trader.trading.service import TradingService

logger = logging.getLogger(__name__)


class _OwnedExchange:
    """Every outbound execution validates the runtime lease at the IO boundary."""

    def __init__(self, runtime: "TradingRuntime"):
        self.runtime = runtime

    async def place_order(self, command: PlaceOrderCommand, cl_ord_id: str) -> ExchangeOrderResult:
        await self.runtime.require_ownership(command.account_id)
        if not self.runtime.strategy_start_allowed:
            raise NotReady("TRADING_RUNTIME_NOT_READY")
        if not self.runtime.symbol_allowed(command.symbol):
            return ExchangeOrderResult(
                ExchangeOutcome.REJECTED, reason="SYMBOL_NOT_IN_COVERAGE_SCOPE"
            )
        return await self.runtime.exchange.place_order(command, cl_ord_id)

    async def cancel_order(
        self, account_id: str, cl_ord_id: str, command_id: str, symbol: str
    ) -> ExchangeOrderResult:
        await self.runtime.require_ownership(account_id)
        return await self.runtime.exchange.cancel_order(account_id, cl_ord_id, command_id, symbol)

    async def query_order(
        self, account_id: str, cl_ord_id: str, symbol: str
    ) -> ExchangeOrderResult:
        await self.runtime.require_ownership(account_id)
        return await self.runtime.exchange.query_order(account_id, cl_ord_id, symbol)


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
        command_interval_seconds: float = 0.25,
        reconnect_callback: Callable[[], Awaitable[None]] | None = None,
        private_session_factory: Callable[["TradingRuntime"], Any] | None = None,
        require_private_session: bool = False,
        allowed_symbols: set[str] | None = None,
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
        self.command_interval_seconds = command_interval_seconds
        self.reconnect_callback = reconnect_callback
        self._alignment_lock = asyncio.Lock()
        self.private_session_factory = private_session_factory
        self.require_private_session = require_private_session
        self.private_session = None
        self._private_started = False
        self.strategy_runner = None
        self.allowed_symbols = (
            {symbol.upper() for symbol in allowed_symbols} if allowed_symbols else None
        )

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
            exchange=_OwnedExchange(self),
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
        self.ready = False
        await self._publish_runtime_state()
        self.stages.append("reconcile")
        if self.private_session_factory is not None or self.require_private_session:
            if self.private_session is None and self.private_session_factory is not None:
                self.private_session = self.private_session_factory(self)
            if self.private_session is None:
                await self.block("PRIVATE_SESSION_NOT_ASSEMBLED")
                return False
            try:
                if not self._private_started:
                    self._private_started = True
                    await self.private_session.start()
                else:
                    await self.private_session.align()
            except NotReady as exc:
                await self.block(str(exc))
            await self._update_session_readiness()
            if not self.ready:
                self.stages.append("blocked")
                return False
            self.stages.append("resume_queued")
            await self.service.resume_queued(self.account_id)
            self.stages.append("ready")
            return True
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
            await self._publish_runtime_state()
            return False

        # QUEUED commands never reached the venue (SENDING was not committed).
        self.stages.append("resume_queued")
        await self.service.resume_queued(self.account_id)
        view = await self.service.risk_view(self.account_id)
        self.ready = view.ready
        self.last_reasons = list(view.reasons)
        await self._publish_runtime_state()
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
        if self.command_interval_seconds > 0 and "commands" not in self._loops_started:
            self._tasks.append(asyncio.create_task(self._command_loop()))
            self._loops_started.add("commands")

    async def stop(self) -> None:
        self._stopping = True
        self.ready = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        self._loops_started.clear()
        if self.private_session is not None:
            await self.private_session.stop()
        self._private_started = False
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
            reasons.extend(self.last_reasons or ["TRADING_RUNTIME_NOT_READY"])
        if self.lease_token is None:
            reasons.append("EXECUTION_INSTANCE_CONFLICT")
        warmup_state = None
        warmup_reason = ""
        if self.strategy_runner is not None:
            warmup_state = getattr(self.strategy_runner.warmup_state, "value", None)
            warmup_reason = getattr(self.strategy_runner, "warmup_reason", "") or ""
            if warmup_state == "BLOCKED":
                # A blocked warm-up means the strategy cannot be trusted; the
                # runtime must not report itself ready in that state.
                reasons.append(warmup_reason or "STRATEGY_WARMUP_BLOCKED")
        return {
            "ready": not reasons,
            "reasons": list(dict.fromkeys(reasons)),
            "account_id": self.account_id,
            "db_available": db_available,
            "instance_owner": self.owner,
            "lease_token": self.lease_token,
            "generation": generation,
            "checked_at_ms": checked_at_ms,
            "strategy_warmup_remaining": (
                self.strategy_runner.warmup_remaining if self.strategy_runner is not None else None
            ),
            "strategy_warmup_state": warmup_state,
            "strategy_warmup_reason": warmup_reason,
            "strategy_ready": (
                self.strategy_runner.strategy_ready if self.strategy_runner is not None else None
            ),
        }

    def symbol_allowed(self, symbol: str) -> bool:
        """A symbol outside the reconciled set has no coverage proof."""
        return self.allowed_symbols is None or str(symbol).upper() in self.allowed_symbols

    async def place(self, command: PlaceOrderCommand, actor: str = "system"):
        await self.require_ownership(command.account_id)
        if not self.strategy_start_allowed or self.service is None:
            raise NotReady("TRADING_RUNTIME_NOT_READY")
        if not self.symbol_allowed(command.symbol):
            raise NotReady("SYMBOL_NOT_IN_COVERAGE_SCOPE")
        return await self.service.place(command, actor=actor)

    async def require_ownership(self, account_id: str) -> None:
        """Check current storage ownership; an in-memory ready flag is insufficient."""
        if account_id != self.account_id:
            raise NotReady("EXECUTION_ACCOUNT_MISMATCH")
        if not self.started or self.lease_token is None:
            raise NotReady("EXECUTION_INSTANCE_CONFLICT")
        try:
            valid = await self.store.renew_lease(
                self.lease_scope, self.owner, self.lease_token, self.lease_seconds
            )
        except Exception as exc:
            self.ready = False
            self.last_reasons = ["STORAGE_UNAVAILABLE"]
            raise NotReady("STORAGE_UNAVAILABLE") from exc
        if not valid:
            self.ready = False
            self.started = False
            self.lease_token = None
            self.last_reasons = ["EXECUTION_INSTANCE_CONFLICT"]
            raise NotReady("EXECUTION_INSTANCE_CONFLICT")

    async def cancel(
        self, command_id: str, account_id: str, cl_ord_id: str, actor: str = "system"
    ) -> CommandReceipt:
        # Account risk can be blocked while cancellation remains available.
        await self.require_ownership(account_id)
        return await self.service.cancel(command_id, account_id, cl_ord_id, actor=actor)

    async def control(
        self, command_id: str, account_id: str, action: str, actor: str = "system"
    ) -> CommandReceipt:
        await self.require_ownership(account_id)
        return await self.service.control(command_id, account_id, action, actor=actor)

    async def drain_outbox(self) -> int:
        if self.service is None or self.notifier is None:
            return 0
        return await self.service.dispatch_outbox(self.account_id, self.notifier)

    async def _publish_runtime_state(self) -> None:
        if self.lease_token is None:
            return
        async with self.store.transaction(self.account_id) as state:
            state.control["runtime"] = {
                "owner": self.owner,
                "token": self.lease_token,
                "ready": self.ready,
                "at_ms": self.clock(),
                "reasons": list(self.last_reasons),
            }

    async def block(self, reason: str) -> None:
        self.ready = False
        self.last_reasons = [reason]
        async with self.store.transaction(self.account_id) as state:
            state.reconciliation["stream_healthy"] = False
            self.service._refresh_status(state)
        await self._publish_runtime_state()

    async def mark_stream_ready(self) -> None:
        await self.require_ownership(self.account_id)
        async with self.store.transaction(self.account_id) as state:
            state.reconciliation["stream_healthy"] = True
            self.service._refresh_status(state)

    async def _update_session_readiness(self) -> None:
        view = await self.service.risk_view(self.account_id)
        self.ready = bool(self.private_session and self.private_session.ready and view.ready)
        reasons = list(view.reasons)
        if self.private_session is None or not self.private_session.ready:
            session_reason = getattr(self.private_session, "last_reason", "")
            reasons.extend(
                [session_reason]
                if session_reason
                else self.last_reasons or ["PRIVATE_STREAM_RECOVERING"]
            )
        self.last_reasons = list(dict.fromkeys(reasons))
        await self._publish_runtime_state()

    async def on_market_data(self, data, *, event_id: str | None = None):
        if self.strategy_runner is None:
            raise NotReady("STRATEGY_RUNNER_NOT_ASSEMBLED")
        if not self.strategy_start_allowed:
            raise NotReady("TRADING_RUNTIME_NOT_READY")
        return await self.strategy_runner.on_market_data(data, event_id=event_id)

    async def reconnect(self, callback: Callable[[], Awaitable[None]]) -> None:
        await self.require_ownership(self.account_id)
        async with self._alignment_lock:
            await self.block("VENUE_RECONNECTING")
            await callback()
            if self.private_session is not None:
                await self.private_session.reconnect()
                await self._update_session_readiness()
            else:
                await self._complete_startup()

    async def drain_commands(self) -> list[CommandReceipt]:
        await self.require_ownership(self.account_id)
        state = await self.store.read(self.account_id)
        for record in state.commands.values():
            request = state.control.get("requests", {}).get(record.command_id, {})
            if record.kind != "CONTROL" or request.get("action") != "RECONNECT":
                continue
            if record.status != "QUEUED" or self.reconnect_callback is None:
                continue
            if not await self.service._mark_sending(self.account_id, record.command_id):
                continue
            try:
                await self.reconnect(self.reconnect_callback)
                status, error = "CONFIRMED", ""
            except Exception as exc:
                status, error = "UNKNOWN", str(exc)
            async with self.store.transaction(self.account_id) as current:
                current.commands[record.command_id].status = status
                current.commands[record.command_id].error = error
        view = await self.service.risk_view(self.account_id)
        return await self.service.resume_queued(
            self.account_id, allow_places=self.ready and view.ready
        )

    async def _command_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self.command_interval_seconds)
            if not self.started or self.lease_token is None:
                continue
            try:
                await self.drain_commands()
            except Exception:
                logger.exception("Durable command drain failed; pending commands retained")

    async def _reconcile_loop(self) -> None:
        """Keep the account view aligned with the venue while the runtime runs."""
        while not self._stopping:
            await asyncio.sleep(self.reconcile_interval_seconds)
            if self.service is None:
                return
            if self.lease_token is None:
                # The account is not ours yet; never reconcile another owner's book.
                continue
            if self.require_private_session and self.private_session is None:
                await self.block("PRIVATE_SESSION_NOT_ASSEMBLED")
                continue
            try:
                async with self._alignment_lock:
                    if self.private_session is not None:
                        await self.private_session.align()
                        await self._update_session_readiness()
                        continue
                    snapshot = await self.snapshot_provider(self.account_id)
                    if snapshot is None:
                        continue
                    view = await self.service.recover(snapshot)
                    self.ready = view.ready
                    self.last_reasons = list(view.reasons)
                    await self._publish_runtime_state()
            except Exception as exc:
                logger.warning("Periodic reconciliation provider failed: %s", exc)
                await self.block("RECONCILIATION_UNAVAILABLE")
                continue

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
