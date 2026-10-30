"""Runtime assembly, readiness and startup-order acceptance tests."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from trader.tests.test_trading_service import Clock, FakeExchange, command
from trader.trading.models import Execution, NotReady, OrderObservation, ReconcileSnapshot
from trader.trading.risk import RiskPolicy
from trader.trading.runtime import TradingRuntime
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore


def snapshot(
    account_id="acct",
    *,
    generation=1,
    checked=1_000_000,
    balances=None,
    open_orders=(),
    executions=(),
    stream_healthy=True,
    complete=True,
):
    return ReconcileSnapshot(
        account_id=account_id,
        checked_at_ms=checked,
        generation=generation,
        balances=balances or {"USDT": Decimal("1000"), "BTC": Decimal("0")},
        open_orders=list(open_orders),
        executions=list(executions),
        marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": checked}},
        stream_healthy=stream_healthy,
        complete=complete,
    )


def runtime(store=None, *, clock=None, owner="runtime-1", snap=None, notifier=None):
    async def provider(account_id):
        return snap

    return TradingRuntime(
        store=store or MemoryTradingStore(),
        exchange=FakeExchange(),
        account_id="acct",
        snapshot_provider=provider,
        clock=clock or Clock(),
        policy=RiskPolicy(fee_rate=Decimal("0.001")),
        notifier=notifier,
        owner=owner,
        lease_seconds=30,
        outbox_interval_seconds=3600,
    )


@pytest.mark.asyncio
async def test_startup_reconciles_before_becoming_ready_and_allows_strategies():
    clock = Clock()
    rt = runtime(clock=clock, snap=snapshot())
    assert await rt.start() is True
    assert rt.stages == [
        "storage",
        "ledger_risk_oms",
        "adapter",
        "lease",
        "reconcile",
        "resume_queued",
        "ready",
    ]
    assert rt.strategy_start_allowed is True
    readiness = await rt.readiness()
    assert readiness["ready"] is True
    assert readiness["reasons"] == []
    await rt.stop()
    assert rt.strategy_start_allowed is False


@pytest.mark.asyncio
async def test_runtime_without_reconciliation_cannot_start_strategies_or_place_orders():
    rt = runtime(snap=None)
    assert await rt.start() is False
    assert rt.strategy_start_allowed is False
    with pytest.raises(NotReady):
        await rt.place(command())
    readiness = await rt.readiness()
    assert readiness["ready"] is False
    assert "ACCOUNT_NOT_RECONCILED" in readiness["reasons"]


@pytest.mark.asyncio
async def test_second_execution_instance_is_blocked_until_the_lease_is_released():
    store = MemoryTradingStore()
    clock = Clock(1_000_200)
    first = runtime(store, clock=clock, owner="runtime-1", snap=snapshot())
    second = runtime(
        store,
        clock=clock,
        owner="runtime-2",
        snap=snapshot(generation=2, checked=1_000_100),
    )
    assert await first.start() is True
    assert await second.start() is False
    assert "EXECUTION_INSTANCE_CONFLICT" in second.last_reasons
    await second.stop()

    await first.stop()
    third = runtime(
        store,
        clock=clock,
        owner="runtime-3",
        snap=snapshot(generation=3, checked=1_000_200),
    )
    assert await third.start() is True
    await third.stop()


@pytest.mark.asyncio
async def test_database_outage_makes_readiness_503_but_liveness_is_separate():
    store = MemoryTradingStore()
    rt = runtime(store, snap=snapshot())
    assert await rt.start() is True
    store.fail_transactions = True
    readiness = await rt.readiness()
    assert readiness["ready"] is False
    assert "STORAGE_UNAVAILABLE" in readiness["reasons"]
    await rt.stop()


@pytest.mark.asyncio
async def test_stale_reconciliation_blocks_new_risk():
    clock = Clock(1_000_000)
    rt = runtime(
        clock=clock,
        snap=snapshot(checked=1_000_000),
    )
    rt.policy = RiskPolicy(max_reconciliation_age_ms=1000)
    assert await rt.start() is True
    clock.now = 1_002_000
    readiness = await rt.readiness()
    assert readiness["ready"] is False
    assert "ACCOUNT_SNAPSHOT_STALE" in readiness["reasons"]
    with pytest.raises(NotReady):
        await rt.place(command(command_id="late"))
    await rt.stop()


@pytest.mark.asyncio
async def test_outbox_dispatch_retries_without_losing_fills():
    delivered = []

    async def notifier(event):
        if not delivered:
            delivered.append("failed")
            raise RuntimeError("sse down")
        delivered.append(event["event_type"])

    rt = runtime(snap=snapshot(), notifier=notifier)
    assert await rt.start() is True
    receipt = await rt.place(command())
    assert await rt.drain_outbox() == 0
    assert await rt.drain_outbox() == 2
    assert delivered == ["failed", "order.queued", "order.accepted"]
    state = await rt.service.store.read("acct")
    assert state.orders[receipt.cl_ord_id].booked_qty == Decimal("0")
    await rt.stop()


@pytest.mark.asyncio
async def test_restart_recovers_orders_holds_positions_and_never_reapplies_fills():
    clock = Clock(1_000_000)
    store = MemoryTradingStore()
    first = runtime(store, clock=clock, owner="runtime-1", snap=snapshot())
    assert await first.start() is True
    receipt = await first.place(command(qty=Decimal("2"), price=Decimal("100")))
    execution = Execution(
        "acct",
        receipt.cl_ord_id,
        "BTCUSDT",
        "e-1",
        Decimal("1"),
        Decimal("100"),
        Decimal("0.1"),
        "USDT",
        1_000_100,
    )
    await first.service.record_execution(execution)
    await first.stop()

    clock.now = 1_000_100
    second = runtime(
        store,
        clock=clock,
        owner="runtime-2",
        snap=snapshot(
            generation=2,
            checked=1_000_100,
            balances={"USDT": Decimal("899.9"), "BTC": Decimal("1")},
            open_orders=[
                OrderObservation(
                    "acct", receipt.cl_ord_id, "PARTIALLY_FILLED", Decimal("1"), "b-1", 1_000_100
                )
            ],
            executions=[execution],
        ),
    )
    assert await second.start() is True
    state = await store.read("acct")
    assert state.orders[receipt.cl_ord_id].booked_qty == Decimal("1")
    assert state.positions["BTCUSDT"] == Decimal("1")
    assert state.holds[receipt.cl_ord_id].amount == Decimal("100.100")
    assert state.balances["USDT"] == Decimal("899.9")
    assert state.executions[execution.key]["exec_id"] == "e-1"
    # Replaying the same execution after restart is a no-op.
    before = state.balances["USDT"]
    assert await second.service.record_execution(execution) is False
    after = (await store.read("acct")).balances["USDT"]
    assert after == before
    await second.stop()


@pytest.mark.asyncio
async def test_two_processes_share_the_same_storage_and_cannot_both_reserve():
    store = MemoryTradingStore()
    clock = Clock()
    one = runtime(store, clock=clock, owner="runtime-1", snap=snapshot())
    two = runtime(store, clock=clock, owner="runtime-2", snap=snapshot())
    assert await one.start() is True
    # Three and four share the store but are not started as runtimes; they model
    # a second process that reaches the same account transaction.
    service_two = TradingService(store, FakeExchange(), policy=RiskPolicy(), clock=clock)

    async def attempt(service, command_id):
        try:
            return await service.place(
                command(command_id=command_id, qty=Decimal("6"), price=Decimal("100"))
            )
        except Exception as exc:
            return exc

    results = await asyncio.gather(attempt(one.service, "a"), attempt(service_two, "b"))
    assert len([value for value in results if not isinstance(value, Exception)]) == 1
    await one.stop()


@pytest.mark.asyncio
async def test_incomplete_snapshot_never_enables_trading():
    rt = runtime(snap=snapshot(complete=False))
    assert await rt.start() is False
    readiness = await rt.readiness()
    assert readiness["ready"] is False
    assert "INCOMPLETE_SNAPSHOT" in readiness["reasons"]


def test_runtime_entrypoints_import_without_network_side_effects():
    import trader.runtime.research_worker_main as research_main
    import trader.runtime.trading_main as trading_main

    assert callable(research_main.main)
    assert trading_main.app is not None
    paths = {route.path for route in trading_main.app.routes}
    assert "/health/trading" in paths
    assert "/v1/trading/orders" in paths
    assert "/v1/research/backtests" in paths


@pytest.mark.asyncio
async def test_periodic_reconciliation_observes_terminal_orders():
    clock = Clock(1_000_000)
    calls = {"count": 0}

    async def provider(account_id):
        calls["count"] += 1
        index = calls["count"]
        if index == 1:
            return snapshot(generation=1, checked=1_000_000)
        if index == 2:
            return snapshot(
                generation=2,
                checked=1_000_100,
                open_orders=[
                    OrderObservation("acct", "qts-1", "CANCELLED", Decimal("0"), "b-1", 1_000_100)
                ],
            )
        return snapshot(generation=index, checked=1_000_000 + index * 100)

    rt = runtime(clock=clock, snap=None)
    rt.snapshot_provider = provider
    rt.reconcile_interval_seconds = 0.01
    assert await rt.start() is True
    await asyncio.sleep(0.2)
    state = await rt.store.read("acct")
    assert state.reconciliation.get("checked_at_ms") >= 1_000_200
    assert calls["count"] >= 3
    await rt.stop()


@pytest.mark.asyncio
async def test_runtime_retries_the_execution_lease_until_the_stale_owner_expires():
    store = MemoryTradingStore()
    await store.acquire_lease("trading:acct", "dead-owner", 30)

    rt = runtime(store, owner="runtime-1", snap=snapshot())
    rt.lease_seconds = 30
    rt.lease_retry_interval_seconds = 0.02
    rt.reconcile_interval_seconds = 0.01
    assert await rt.start() is False
    assert "EXECUTION_INSTANCE_CONFLICT" in rt.last_reasons

    store.expire_lease("trading:acct")
    await asyncio.sleep(0.4)
    assert rt.lease_token is not None
    assert rt.started is True
    await rt.stop()
