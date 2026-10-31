"""Control and execution roles communicate through durable account transactions."""

import pytest

from trader.tests.test_trading_runtime import runtime, snapshot
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
    assert await rt.start()
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
    assert await rt.start()
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
    assert await rt.start()
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
async def test_retired_legacy_sender_cannot_be_reenabled_or_lazily_constructed(monkeypatch):
    from trader.api.routes import strategies

    monkeypatch.setattr(strategies, "_legacy_sender_retired", True, raising=False)
    monkeypatch.setattr(strategies, "_live_trading_enabled", True)
    assert not strategies._is_live_trading_enabled()
    with pytest.raises(RuntimeError, match="LEGACY_EXECUTION_RETIRED"):
        await strategies._create_broker()


@pytest.mark.asyncio
async def test_legacy_cancel_endpoint_cannot_falsely_mutate_order_projection():
    import httpx

    from trader.api.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post("/v1/orders/legacy-order/cancel")
        assert response.status_code == 503
        assert "LEGACY_EXECUTION_RETIRED" in response.json()["detail"]


@pytest.mark.asyncio
async def test_legacy_readonly_broker_rejects_execution_but_keeps_account_queries():
    from trader.api.routes.strategies import _ReadOnlyLegacyBroker

    class Broker:
        async def _fetch_account(self):
            return {"balances": []}

        async def place_order(self):
            raise AssertionError("raw sender must never be reached")

    broker = _ReadOnlyLegacyBroker(Broker())
    assert await broker._fetch_account() == {"balances": []}
    with pytest.raises(RuntimeError, match="LEGACY_EXECUTION_RETIRED"):
        await broker.place_order()
    with pytest.raises(RuntimeError, match="LEGACY_EXECUTION_RETIRED"):
        await broker.cancel_order()


@pytest.mark.asyncio
async def test_legacy_retirement_is_restored_even_when_startup_fails(monkeypatch):
    from contextlib import asynccontextmanager

    from trader.api import main
    from trader.api.routes import strategies

    @asynccontextmanager
    async def broken(app):
        assert strategies._legacy_sender_retired
        raise RuntimeError("storage failure")
        yield

    monkeypatch.setattr(main, "_legacy_readonly_lifespan", broken)
    monkeypatch.setattr(strategies, "_legacy_sender_retired", False)
    with pytest.raises(RuntimeError, match="storage failure"):
        async with main.lifespan(main.app):
            pass
    assert not strategies._legacy_sender_retired


@pytest.mark.asyncio
async def test_retired_legacy_killswitch_write_is_rejected_before_any_effect(monkeypatch):
    from unittest.mock import AsyncMock

    import httpx

    from trader.api.main import app
    from trader.api.routes import strategies
    from trader.services import KillSwitchService

    # The legacy empty-state reader synthesizes updated_at on each read. Pin
    # its input snapshot so the assertion compares state rather than wall time.
    state = KillSwitchService().get_state("GLOBAL")
    monkeypatch.setattr(KillSwitchService, "get_state", lambda self, scope="GLOBAL": state)
    apply = AsyncMock(return_value=state)
    monkeypatch.setattr(KillSwitchService, "set_state_durable", apply)
    monkeypatch.setattr(strategies, "_legacy_sender_retired", True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        before = await client.get("/v1/killswitch")
        assert before.status_code == 200
        response = await client.post(
            "/v1/killswitch",
            json={
                "scope": "GLOBAL",
                "level": 2,
                "reason": "stop",
                "updated_by": "owner",
            },
        )
        assert response.status_code == 503
        assert "LEGACY_EXECUTION_RETIRED" in response.json()["detail"]
        apply.assert_not_awaited()
        after = await client.get("/v1/killswitch")
        assert after.status_code == 200
        assert after.json() == before.json()


@pytest.mark.asyncio
async def test_reconnect_blocks_orders_before_touching_connection_and_recovers_after_alignment():
    rt = runtime(snap=snapshot())
    assert await rt.start()
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
