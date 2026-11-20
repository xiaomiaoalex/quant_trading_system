"""Control and execution roles communicate through durable account transactions."""

import pytest

from trader.tests.test_trading_runtime import runtime, snapshot, start_runtime
from trader.tests.test_trading_service import Clock, FakeExchange, bootstrap, command, make_service
from trader.trading.models import NotReady
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore


@pytest.mark.asyncio
async def test_deferred_cancel_all_remains_queued_and_runtime_finishes_children():
    store = MemoryTradingStore()
    venue = FakeExchange()
    executor = make_service(venue, store=store)
    await bootstrap(executor)
    placed = await executor.place(command())
    control = TradingService(store, FakeExchange(), clock=Clock(), defer_send=True)
    receipt = await control.control("cancel-all", "acct", "CANCEL_ALL", actor="owner")
    assert receipt.status == "QUEUED"
    assert venue.cancelled == []
    await executor.resume_queued("acct")
    await executor.resume_queued("acct")
    assert venue.cancelled == [(placed.cl_ord_id, f"cancel-all:{placed.cl_ord_id}")]
    assert (await executor.command("acct", "cancel-all"))["status"] == "CONFIRMED"


@pytest.mark.asyncio
async def test_running_runtime_drains_commands_accepted_by_independent_control_service():
    rt = runtime(snap=snapshot())
    assert await start_runtime(rt)
    control = TradingService(rt.store, FakeExchange(), clock=Clock(), defer_send=True)
    try:
        receipt = await control.place(command())
        assert receipt.status == "QUEUED"
        assert rt.exchange.placed == []
        await rt.drain_commands()
        assert len(rt.exchange.placed) == 1
        assert (await control.command("acct", receipt.command_id))["status"] == "CONFIRMED"
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_remote_api_enqueues_without_exchange_access_and_requires_live_owner():
    from trader.runtime.control_main import (
        NoExchangeIO,
        QueuedTradingControl,
        RemoteTradingReadiness,
    )

    rt = runtime(snap=snapshot())
    assert await start_runtime(rt)
    valid = True

    async def lease_reader(scope):
        return {"owner": rt.owner, "token": rt.lease_token, "valid": valid}

    service = TradingService(rt.store, NoExchangeIO(), clock=Clock(), defer_send=True)
    readiness = RemoteTradingReadiness(service, "acct", lease_reader)
    control = QueuedTradingControl(service, readiness)
    try:
        receipt = await control.place(command())
        assert receipt.status == "QUEUED"
        assert rt.exchange.placed == []
        await rt.drain_commands()
        assert len(rt.exchange.placed) == 1
        valid = False
        assert not (await readiness.check())["ready"]
        with pytest.raises(NotReady, match="EXECUTION_INSTANCE_UNAVAILABLE"):
            await control.place(command(command_id="no-owner"))
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_durable_reconnect_request_runs_only_on_execution_process():
    rt = runtime(snap=snapshot())
    assert await start_runtime(rt)
    calls = []

    async def reconnect():
        calls.append("reconnect")
        rt.clock.now = 1_000_001

    async def provider(account_id):
        return snapshot(generation=2, checked=1_000_001)

    rt.snapshot_provider = provider
    rt.reconnect_callback = reconnect
    control = TradingService(rt.store, FakeExchange(), clock=Clock(), defer_send=True)
    try:
        receipt = await control.control("reconnect", "acct", "RECONNECT")
        assert receipt.status == "QUEUED" and calls == []
        await rt.drain_commands()
        assert calls == ["reconnect"]
        assert (await control.command("acct", "reconnect"))["status"] == "CONFIRMED"
        await rt.drain_commands()
        assert calls == ["reconnect"]
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_reconnect_blocks_orders_before_touching_connection_and_recovers_after_alignment():
    rt = runtime(snap=snapshot())
    assert await start_runtime(rt)
    calls = []

    async def reconnect():
        calls.append("reconnect")
        assert not rt.strategy_start_allowed
        with pytest.raises(NotReady):
            await rt.place(command())
        rt.clock.now = 1_000_001

    async def provider(account_id):
        return snapshot(generation=2, checked=1_000_001)

    rt.snapshot_provider = provider
    try:
        await rt.reconnect(reconnect)
        assert calls == ["reconnect"]
        assert rt.ready
    finally:
        await rt.stop()
