"""Recovery may rebuild our own lost order mapping from venue evidence only."""

from decimal import Decimal

import pytest

from trader.trading.models import (
    Execution,
    OrderObservation,
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


def service(clock=None, *, defer_send=False):
    return TradingService(
        MemoryTradingStore(),
        NoExchange(),
        clock=clock or Clock(),
        defer_send=defer_send,
    )


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


def execution(exec_id="7", cl_ord_id="qts-abc123", qty="0.00019"):
    return Execution(
        "acct",
        cl_ord_id,
        "BTCUSDT",
        exec_id,
        Decimal(qty),
        Decimal("77474.49"),
        Decimal("0.00000019"),
        "BTC",
        900_000,
    )


def snapshot(
    *,
    executions=(),
    observations=(),
    venue_orders=(),
    balances=None,
    generation=1,
    checked_at_ms=1_000_000,
):
    return ReconcileSnapshot(
        account_id="acct",
        checked_at_ms=checked_at_ms,
        generation=generation,
        balances=dict(balances or {"USDT": Decimal("1400"), "BTC": Decimal("0.036")}),
        executions=list(executions),
        open_orders=list(observations),
        venue_orders=list(venue_orders),
        marks={"BTCUSDT": {"price": Decimal("77000"), "at_ms": 1_000_000}},
        complete=True,
    )


@pytest.mark.asyncio
async def test_our_own_lost_order_is_adopted_instead_of_blocking():
    engine = service()
    view = await engine.recover(snapshot(executions=[execution()], venue_orders=[venue_order()]))
    assert "UNKNOWN_EXECUTION" not in view.reasons
    assert "UNKNOWN_ORDER" not in view.reasons
    state = await engine.store.read("acct")
    assert "qts-abc123" in state.orders
    # The fill is attributed to the position; the fee is charged in base.
    assert state.positions["BTCUSDT"] == Decimal("0.00019") - Decimal("0.00000019")
    assert state.balances["USDT"] == Decimal("1400")
    assert any(event["event_type"] == "order.adopted" for event in state.events)


@pytest.mark.asyncio
async def test_in_flight_open_order_is_adopted_instead_of_reported_foreign():
    engine = service()
    view = await engine.recover(
        snapshot(
            observations=[
                OrderObservation("acct", "qts-abc123", "OPEN", Decimal("0"), "42", 1_000_000)
            ],
            venue_orders=[venue_order(status="OPEN", filled_qty=Decimal("0"))],
        )
    )
    assert "UNKNOWN_ORDER" not in view.reasons
    state = await engine.store.read("acct")
    assert "qts-abc123" in state.orders


@pytest.mark.asyncio
async def test_foreign_client_order_id_still_blocks_new_risk():
    engine = service()
    view = await engine.recover(
        snapshot(
            executions=[execution(cl_ord_id="manual-order")],
            observations=[
                OrderObservation("acct", "manual-order", "OPEN", Decimal("1"), "99", 1_000_000)
            ],
        )
    )
    assert "UNKNOWN_EXECUTION" in view.reasons
    assert "UNKNOWN_ORDER" in view.reasons


@pytest.mark.asyncio
async def test_adoption_is_idempotent_and_does_not_double_book():
    engine = service()
    snap = snapshot(executions=[execution()], venue_orders=[venue_order()])
    await engine.recover(snap)
    await engine.recover(
        snapshot(
            executions=[execution()],
            venue_orders=[venue_order()],
            generation=2,
            checked_at_ms=1_000_500,
        )
    )
    state = await engine.store.read("acct")
    booked = [entry for entry in state.ledger if entry["kind"] in {"trade", "fee"}]
    assert len(booked) == 3  # quote, base and fee legs of a single fill
    assert len(state.executions) == 1


@pytest.mark.asyncio
async def test_opening_balance_makes_the_ledger_match_the_venue():
    engine = service()
    await engine.recover(snapshot(executions=[execution()], venue_orders=[venue_order()]))
    state = await engine.store.read("acct")
    opening = [entry for entry in state.ledger if entry["kind"] == "opening_balance"]
    # The venue already held BTC before this fill; the difference is booked
    # explicitly instead of being hidden.
    assert any(entry["asset"] == "BTC" for entry in opening)
    totals: dict[str, Decimal] = {}
    for entry in state.ledger:
        totals[entry["asset"]] = totals.get(entry["asset"], Decimal(0)) + Decimal(entry["delta"])
    for asset, balance in state.balances.items():
        assert totals.get(asset, Decimal(0)) == balance


@pytest.mark.asyncio
async def test_conflicting_content_for_the_same_execution_key_still_blocks():
    engine = service()
    await engine.recover(snapshot(executions=[execution()], venue_orders=[venue_order()]))
    conflicting = execution(qty="0.0002")
    view = await engine.recover(
        snapshot(
            executions=[conflicting],
            venue_orders=[venue_order(filled_qty=Decimal("0.0002"))],
            generation=2,
            checked_at_ms=1_000_500,
        )
    )
    assert "EXECUTION_KEY_CONFLICT" in view.reasons


@pytest.mark.asyncio
async def test_adopted_order_does_not_create_a_reservation():
    engine = service()
    await engine.recover(snapshot(executions=[execution()], venue_orders=[venue_order()]))
    state = await engine.store.read("acct")
    assert not state.holds


@pytest.mark.asyncio
async def test_a_new_command_uses_its_own_client_order_id():
    engine = service(defer_send=True)
    await engine.recover(snapshot())
    command = PlaceOrderCommand(
        command_id="cmd-1",
        account_id="acct",
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        side="BUY",
        qty=Decimal("0.00019"),
        price=Decimal("77474.49"),
    )
    receipt = await engine.place(command, actor="test")
    assert receipt.cl_ord_id != "qts-abc123"
