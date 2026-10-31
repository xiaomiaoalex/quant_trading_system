"""Independent authenticated Control API. No exchange credentials or sender are assembled."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from contextlib import asynccontextmanager
from dataclasses import replace

import uvicorn

from trader.adapters.persistence.history_dataset_store import HistoryDatasetStore
from trader.adapters.persistence.research_history_bridge_store import ResearchHistoryBridgeStore
from trader.api.auth import AuthSettings, PostgresSessionStore
from trader.api.routes.sse import get_sse_manager
from trader.api.trading_app import create_trading_app
from trader.services.history_data_ingestion import HistoryIngestionService
from trader.trading.jobs import BacktestApplication, PostgresResearchStore
from trader.trading.models import NotReady
from trader.trading.pg import create_pool
from trader.trading.postgres import PostgresTradingStore
from trader.trading.service import TradingService


class NoExchangeIO:
    async def place_order(self, *args, **kwargs):
        raise NotReady("CONTROL_PROCESS_CANNOT_EXECUTE")

    cancel_order = place_order
    query_order = place_order


class RemoteTradingReadiness:
    def __init__(self, service, account_id, lease_reader):
        self.service = service
        self.account_id = account_id
        self.lease_reader = lease_reader

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

    async def control(self, command_id, account_id, action, actor="system"):
        if account_id != self.readiness.account_id:
            raise NotReady("EXECUTION_ACCOUNT_MISMATCH")
        return await self.service.control(command_id, account_id, action, actor=actor)


@asynccontextmanager
async def lifespan(app):
    pool = None
    task = None
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
            store, NoExchangeIO(), clock=lambda: int(time.time() * 1000), defer_send=True
        )

        async def lease_reader(scope):
            return await pool.fetchrow(
                "SELECT owner,fencing_token AS token,expires_at>clock_timestamp() AS valid "
                "FROM trading_runtime_leases WHERE scope=$1",
                scope,
            )

        readiness = RemoteTradingReadiness(service, account_id, lease_reader)
        app.state.trading_service = QueuedTradingControl(service, readiness)
        app.state.readiness_provider = readiness
        app.state.sessions = sessions
        app.state.research_store = research

        # 历史归档：Control API 只提供接口与资格校验，导入由独立 worker 进程执行。
        try:
            history_store = HistoryDatasetStore()
            await history_store.connect()
            bridge = ResearchHistoryBridgeStore(storage=history_store.storage)
            app.state.history_service = HistoryIngestionService(
                store=history_store,
                bridge_store=bridge,
            )

            async def dataset_eligible(dataset_id: str) -> bool:
                """官方资格来自可信关联 + 已发布的历史数据集，绝不只看 source 字符串。"""
                link = await bridge.find_link_by_research_dataset(dataset_id)
                if not link or not link.get("official_eligible"):
                    return False
                history_dataset_id = link.get("history_dataset_id")
                if not history_dataset_id:
                    return False
                row = await history_store.get_dataset(history_dataset_id)
                if row is None:
                    return False
                return (
                    row.get("status") == "PUBLISHED"
                    and bool(row.get("persistence_ok"))
                    and bool(row.get("backtest_eligible"))
                )

            app.state.research_application = BacktestApplication(
                research, eligibility=dataset_eligible
            )
        except Exception:
            app.state.history_service = None
            app.state.research_application = BacktestApplication(research)

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
