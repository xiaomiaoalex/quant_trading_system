"""Independent authenticated Control API. No exchange credentials or sender are assembled."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import replace

import uvicorn

from trader.adapters.persistence.history_dataset_store import HistoryDatasetStore
from trader.adapters.persistence.research_history_bridge_store import ResearchHistoryBridgeStore
from trader.api.auth import AuthSettings, PostgresSessionStore
from trader.api.routes import data_catalog
from trader.api.routes.sse import get_sse_manager
from trader.api.trading_app import create_trading_app
from trader.ops.dr_evidence import dr_gate_from_env
from trader.runtime.credentials import assert_control_credentials_absent
from trader.runtime.history_plane import build_history_plane as _build_history_plane
from trader.services.alerting import alert_monitor_from_env
from trader.services.history_data_ingestion import HistoryIngestionService
from trader.trading.jobs import PostgresResearchStore
from trader.trading.models import NotReady
from trader.trading.pg import create_pool
from trader.trading.postgres import PostgresTradingStore
from trader.trading.risk import risk_policy_from_env
from trader.trading.service import TradingService

logger = logging.getLogger(__name__)


class NoExchangeIO:
    async def place_order(self, *args, **kwargs):
        raise NotReady("CONTROL_PROCESS_CANNOT_EXECUTE")

    cancel_order = place_order
    query_order = place_order


class RemoteTradingReadiness:
    def __init__(self, service, account_id, lease_reader, dr_gate=None):
        self.service = service
        self.account_id = account_id
        self.lease_reader = lease_reader
        self.dr_gate = dr_gate

    def _dr_reasons(self):
        if self.dr_gate is None:
            return []
        try:
            result = self.dr_gate()
        except Exception:
            return ["DR_EVIDENCE_INVALID"]
        if getattr(result, "ready", False):
            return []
        return list(getattr(result, "reasons", ()) or ["DR_EVIDENCE_INVALID"])

    async def check(self):
        try:
            view = await self.service.risk_view(self.account_id)
            state = await self.service.store.read(self.account_id)
            runtime = state.control.get("runtime", {})
            lease = await self.lease_reader(f"trading:{self.account_id}")
            reasons = list(view.reasons)
            if not lease or not lease["valid"]:
                reasons.append("EXECUTION_INSTANCE_UNAVAILABLE")
            elif runtime.get("owner") != lease["owner"] or runtime.get("token") != lease["token"]:
                reasons.append("EXECUTION_OWNER_NOT_ALIGNED")
            elif not runtime.get("ready"):
                reasons.extend(runtime.get("reasons") or ["TRADING_RUNTIME_NOT_READY"])
            reasons.extend(self._dr_reasons())
            return {
                "ready": not reasons,
                "reasons": list(dict.fromkeys(reasons)),
                "account_id": self.account_id,
                "db_available": True,
            }
        except Exception:
            return {
                "ready": False,
                "reasons": ["STORAGE_UNAVAILABLE"],
                "account_id": self.account_id,
                "db_available": False,
            }


class QueuedTradingControl:
    def __init__(self, service, readiness):
        self.service = service
        self.readiness = readiness

    def __getattr__(self, name):
        return getattr(self.service, name)

    async def place(self, command, actor="system"):
        if command.account_id != self.readiness.account_id:
            raise NotReady("EXECUTION_ACCOUNT_MISMATCH")
        status = await self.readiness.check()
        if not status["ready"]:
            raise NotReady(",".join(status["reasons"]))
        return await self.service.place(command, actor=actor)

    async def cancel(self, command_id, account_id, cl_ord_id, actor="system"):
        if account_id != self.readiness.account_id:
            raise NotReady("EXECUTION_ACCOUNT_MISMATCH")
        return await self.service.cancel(command_id, account_id, cl_ord_id, actor=actor)

    async def allocate(
        self, command_id, account_id, book_id, asset, amount, actor="system", reason=""
    ):
        # Allocation is an accounting intent, not new risk: it may be queued
        # while the account is blocked. Only the execution owner applies it.
        if account_id != self.readiness.account_id:
            raise NotReady("EXECUTION_ACCOUNT_MISMATCH")
        return await self.service.allocate(
            command_id,
            account_id,
            book_id,
            asset,
            amount,
            actor=actor,
            reason=reason,
        )

    async def control(
        self, command_id, account_id, action, actor="system", *, code="", evidence=None
    ):
        if account_id != self.readiness.account_id:
            raise NotReady("EXECUTION_ACCOUNT_MISMATCH")
        return await self.service.control(
            command_id, account_id, action, actor=actor, code=code, evidence=evidence
        )


async def build_history_plane(research):
    """Build the research plane with a fail-closed eligibility gate.

    The shared builder never returns an application without an eligibility
    gate; if the history store cannot be assembled, submission is refused with
    a stable reason. Module-level factories are passed explicitly so the
    existing failure-injection tests keep controlling this entry point.
    """
    return await _build_history_plane(
        research,
        history_store_factory=HistoryDatasetStore,
        bridge_factory=ResearchHistoryBridgeStore,
        service_factory=HistoryIngestionService,
    )


@asynccontextmanager
async def lifespan(app):
    # Control must never hold exchange write credentials. Refuse to start when
    # they are present instead of silently accepting an over-privileged process.
    assert_control_credentials_absent()
    pool = None
    task = None
    alert_task = None
    alert_monitor = None
    history_store = None
    try:
        pool = await create_pool()
        store = PostgresTradingStore(pool)
        await store.initialize()
        sessions = PostgresSessionStore(pool)
        await sessions.initialize()
        research = PostgresResearchStore(pool)
        await research.initialize()
        account_id = os.getenv("QTS_ACCOUNT_ID", "binance_spot")
        service = TradingService(
            store,
            NoExchangeIO(),
            policy=risk_policy_from_env(),
            clock=lambda: int(time.time() * 1000),
            defer_send=True,
        )

        async def lease_reader(scope):
            return await pool.fetchrow(
                "SELECT owner,fencing_token AS token,expires_at>clock_timestamp() AS valid "
                "FROM trading_runtime_leases WHERE scope=$1",
                scope,
            )

        readiness = RemoteTradingReadiness(
            service, account_id, lease_reader, dr_gate=dr_gate_from_env()
        )
        app.state.trading_service = QueuedTradingControl(service, readiness)
        app.state.readiness_provider = readiness
        app.state.sessions = sessions
        app.state.research_store = research

        # 历史归档：Control API 只提供接口与资格校验，导入由独立 worker 进程执行。
        history_store, history_service, research_application = await build_history_plane(research)
        app.state.history_service = history_service
        app.state.research_application = research_application

        # Preserve the opt-in OHLCV ingestion worker from the retired process.
        # It is data-plane only; it never touches orders or account state.
        try:
            await data_catalog.maybe_start_ohlcv_ingestion_from_env()
        except Exception:
            logger.exception("OHLCV ingestion worker failed to start from env")

        async def notify(event):
            channel = (
                "orders"
                if event["event_type"].startswith(("order.", "execution."))
                else "reconciliation"
            )
            await get_sse_manager().broadcast(channel, event["event_type"], event)

        async def outbox():
            while True:
                try:
                    await service.dispatch_outbox(account_id, notify)
                except Exception:
                    pass  # Persistent outbox remains pending for the next iteration.
                await asyncio.sleep(1)

        task = asyncio.create_task(outbox())

        # The Control process observes the authoritative lease/runtime state,
        # so it can alert on a missing or expired execution even when the
        # execution process itself is dead.
        alert_monitor = alert_monitor_from_env(
            source="control",
            account_id=account_id,
            readiness=readiness.check,
        )
        app.state.alert_monitor = alert_monitor
        if alert_monitor is not None:
            alert_task = asyncio.create_task(alert_monitor.run_forever())
    except Exception:
        app.state.trading_service = None
        app.state.readiness_provider = None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if alert_task:
            alert_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await alert_task
        if alert_monitor is not None:
            with contextlib.suppress(Exception):
                await alert_monitor.close()
        with contextlib.suppress(Exception):
            await data_catalog.shutdown_ohlcv_ingestion_worker()
        if history_store is not None:
            with contextlib.suppress(Exception):
                await history_store.disconnect()
        if pool:
            await pool.close()


app = create_trading_app(
    auth_settings=replace(AuthSettings.from_env(), require_auth=True),
    lifespan=lifespan,
    include_sse=True,
)


def main():
    uvicorn.run(
        app,
        host=os.getenv("QTS_API_HOST", "127.0.0.1"),
        port=int(os.getenv("QTS_TRADING_API_PORT", "8090")),
    )


if __name__ == "__main__":
    main()
