"""Real PostgreSQL coordination of separately assembled API and execution roles."""

import asyncio

import pytest

from trader.runtime.control_main import NoExchangeIO, QueuedTradingControl, RemoteTradingReadiness
from trader.tests.test_trading_postgres import pg_store
from trader.tests.test_trading_runtime import runtime, snapshot
from trader.tests.test_trading_service import Clock, command
from trader.trading.service import TradingService


@pytest.mark.asyncio
async def test_postgres_control_queue_execution_shutdown_and_live_owner_gate():
    async with pg_store() as (store, pool):
        rt = runtime(store=store, snap=snapshot())
        rt.command_interval_seconds = 0
        assert await rt.start()

        async def lease_reader(scope):
            return await pool.fetchrow(
                "SELECT owner,fencing_token AS token,expires_at>clock_timestamp() AS valid "
                "FROM trading_runtime_leases WHERE scope=$1",
                scope,
            )

        service = TradingService(store, NoExchangeIO(), clock=Clock(), defer_send=True)
        readiness = RemoteTradingReadiness(service, "acct", lease_reader)
        api = QueuedTradingControl(service, readiness)
        try:
            receipts = await asyncio.gather(*(api.place(command()) for _ in range(100)))
            assert {r.status for r in receipts} == {"QUEUED"}
            assert rt.exchange.placed == []
            assert await pool.fetchval("SELECT count(*) FROM trading_commands") == 1
            await rt.drain_commands()
            assert len(rt.exchange.placed) == 1
            assert (await service.command("acct", "cmd-1"))["status"] == "CONFIRMED"
            await rt.drain_commands()
            assert len(rt.exchange.placed) == 1
        finally:
            await rt.stop()
        assert not (await readiness.check())["ready"]
