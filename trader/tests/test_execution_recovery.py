"""A fill seen before its order is known must be booked once the order is adopted.

The private stream can deliver an execution before the local order aggregate
exists. That fill is stored unapplied. Recovery must be able to book it later
instead of turning its own bookkeeping marker into a permanent conflict.
"""

from decimal import Decimal

import pytest

from trader.trading.models import (
    Execution,
    OrderAggregate,
    PlaceOrderCommand,
    ReconcileSnapshot,
    VenueOrder,
)
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore


class Clock:
    def __init__(self, now=1_000_000):
        self.now = now

    def __call__(self):
        return self.now


class NoExchange:
    async def place_order(self, *args, **kwargs):
        raise AssertionError("no venue IO in this test")

    cancel_order = place_order
    query_order = place_order


def service(clock=None):
    return TradingService(MemoryTradingStore(), NoExchange(), clock=clock or Clock())


def venue_order(**overrides):
    payload = dict(
        cl_ord_id="qts-abc123",
        broker_order_id="42",
        symbol="BTCUSDT",
        side="BUY",
        order_type="MARKET",
        qty=Decimal("0.00019"),
        price=Decimal("77474.49"),
        filled_qty=Decimal("0.00019"),
        status="FILLED",
        created_at_ms=900_000,
        base_asset="BTC",
        quote_asset="USDT",
    )
    payload.update(overrides)
    return VenueOrder(**payload)


def execution(exec_id="7", cl_ord_id="qts-abc123", qty="0.00019", price="77474.49"):
    return Execution(
        "acct",
        cl_ord_id,
        "BTCUSDT",
        exec_id,
        Decimal(qty),
        Decimal(price),
        Decimal("0.00000019"),
        "BTC",
        900_000,
    )


def snapshot(*, executions=(), venue_orders=(), generation=1, checked_at_ms=1_000_000):
    return ReconcileSnapshot(
        account_id="acct",
        checked_at_ms=checked_at_ms,
        generation=generation,
        balances={"USDT": Decimal("1400"), "BTC": Decimal("0.036")},
        executions=list(executions),
        open_orders=[],
        venue_orders=list(venue_orders),
        marks={"BTCUSDT": {"price": Decimal("77000"), "at_ms": 1_000_000}},
        complete=True,
    )


def booked_legs(state):
    return [entry for entry in state.ledger if entry["kind"] in {"trade", "fee"}]


@pytest.mark.asyncio
async def test_execution_seen_before_order_is_booked_when_the_snapshot_adopts_the_order():
    engine = service()
    assert await engine.record_execution(execution()) is False
    state = await engine.store.read("acct")
    assert "UNKNOWN_EXECUTION" in [entry["code"] for entry in state.reconciliation["unresolved"]]

    view = await engine.recover(snapshot(venue_orders=[venue_order()]))

    state = await engine.store.read("acct")
    assert "EXECUTION_KEY_CONFLICT" not in view.reasons
    assert "UNKNOWN_EXECUTION" not in view.reasons
    assert state.positions["BTCUSDT"] == Decimal("0.00019") - Decimal("0.00000019")
    assert len(booked_legs(state)) == 3
    assert state.executions[execution().key].get("applied") is not False


@pytest.mark.asyncio
async def test_replayed_execution_after_recovery_books_instead_of_conflicting():
    engine = service()
    await engine.record_execution(execution())

    view = await engine.recover(snapshot(executions=[execution()], venue_orders=[venue_order()]))

    assert "EXECUTION_KEY_CONFLICT" not in view.reasons
    state = await engine.store.read("acct")
    assert len(booked_legs(state)) == 3
    assert state.executions[execution().key].get("applied") is not False


@pytest.mark.asyncio
async def test_rebooking_is_idempotent_across_repeated_snapshots():
    clock = Clock()
    engine = service(clock)
    await engine.record_execution(execution())
    await engine.recover(snapshot(executions=[execution()], venue_orders=[venue_order()]))
    clock.now = 1_000_500
    await engine.recover(
        snapshot(
            executions=[execution()],
            venue_orders=[venue_order()],
            generation=2,
            checked_at_ms=1_000_500,
        )
    )

    state = await engine.store.read("acct")
    assert len(booked_legs(state)) == 3
    assert len(state.executions) == 1


@pytest.mark.asyncio
async def test_rebooking_a_fill_already_inside_the_opening_balance_does_not_double_count():
    clock = Clock()
    engine = service(clock)
    # First reconcile: the fill has no venue order yet, so it is stored unapplied
    # and the opening balance is recorded from the venue's post-fill balances.
    await engine.recover(snapshot(executions=[execution()], venue_orders=[]))

    clock.now = 1_000_500
    view = await engine.recover(
        snapshot(
            executions=[execution()],
            venue_orders=[venue_order()],
            generation=2,
            checked_at_ms=1_000_500,
        )
    )

    assert "BALANCE_MISMATCH" not in view.reasons
    state = await engine.store.read("acct")
    totals: dict[str, Decimal] = {}
    for entry in state.ledger:
        totals[entry["asset"]] = totals.get(entry["asset"], Decimal(0)) + Decimal(entry["delta"])
    for asset, balance in state.balances.items():
        assert totals.get(asset, Decimal(0)) == balance
    assert state.positions["BTCUSDT"] == Decimal("0.00019") - Decimal("0.00000019")


@pytest.mark.asyncio
async def test_different_content_for_an_unapplied_execution_still_conflicts():
    engine = service()
    await engine.record_execution(execution())

    view = await engine.recover(
        snapshot(
            executions=[execution(qty="0.0002", price="77500")],
            venue_orders=[venue_order(filled_qty=Decimal("0.0002"))],
        )
    )

    assert "EXECUTION_KEY_CONFLICT" in view.reasons
    state = await engine.store.read("acct")
    assert not booked_legs(state)


@pytest.mark.asyncio
async def test_record_execution_reports_applied_when_it_books_a_stored_fill():
    engine = service()
    fact = execution()
    command = PlaceOrderCommand(
        command_id=f"adopted:{fact.cl_ord_id}",
        account_id="acct",
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        side="BUY",
        qty=Decimal("0.00019"),
        price=Decimal("77474.49"),
        deployment_id="recovered",
        order_type="MARKET",
        time_in_force="GTC",
        decision_id=f"adopted:{fact.cl_ord_id}",
    )
    async with engine.store.transaction("acct") as state:
        state.executions[fact.key] = dict(fact.content, applied=False)
        state.orders[fact.cl_ord_id] = OrderAggregate(
            cl_ord_id=fact.cl_ord_id, command=command, broker_order_id="42"
        )

    assert await engine.record_execution(fact) is True
    state = await engine.store.read("acct")
    assert len(booked_legs(state)) == 3


@pytest.mark.asyncio
async def test_conflict_recorded_by_the_local_applied_marker_is_cleared():
    """Evidence written by the old bug must not keep the account blocked forever."""
    engine = service()
    fact = execution()
    async with engine.store.transaction("acct") as state:
        state.executions[fact.key] = dict(fact.content, applied=False)
        state.reconciliation["unresolved"] = [
            {
                "code": "EXECUTION_KEY_CONFLICT",
                "detail": {
                    "existing": dict(fact.content, applied=False),
                    "incoming": fact.content,
                },
                "at_ms": 0,
            }
        ]

    view = await engine.recover(snapshot(venue_orders=[venue_order()]))

    assert "EXECUTION_KEY_CONFLICT" not in view.reasons
    state = await engine.store.read("acct")
    assert len(booked_legs(state)) == 3


@pytest.mark.asyncio
async def test_rebooking_an_overfill_stays_blocked_as_a_quantity_conflict():
    clock = Clock()
    engine = service(clock)
    await engine.record_execution(execution())
    await engine.recover(snapshot(venue_orders=[venue_order(qty=Decimal("0.0001"))]))
    clock.now = 1_000_500
    view = await engine.recover(
        snapshot(
            venue_orders=[venue_order(qty=Decimal("0.0001"))],
            generation=2,
            checked_at_ms=1_000_500,
        )
    )

    assert "EXECUTION_QUANTITY_CONFLICT" in view.reasons
    state = await engine.store.read("acct")
    assert not booked_legs(state)
    assert state.executions[execution().key].get("applied") is False


@pytest.mark.asyncio
async def test_unknown_execution_stays_blocking_while_another_fill_has_no_order():
    engine = service()
    await engine.record_execution(execution())
    foreign = execution(exec_id="8", cl_ord_id="manual-order")
    await engine.record_execution(foreign)

    view = await engine.recover(snapshot(executions=[execution()], venue_orders=[venue_order()]))

    assert "UNKNOWN_EXECUTION" in view.reasons
    state = await engine.store.read("acct")
    assert state.executions[execution().key].get("applied") is not False
    assert state.executions[foreign.key].get("applied") is False
