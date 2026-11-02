"""Phase 0 foundation safety baseline.

These tests pin the *correct* safety behaviour of the unified spot trading
core. Two defects are known today, so both tests are marked
``xfail(strict=True)``: the test must fail, and an unexpected pass is itself a
baseline failure (the defect was fixed without removing the marker).

Phase 1 removes the markers once the behaviour is implemented:

- ``test_balance_mismatch_keeps_blocking_until_the_difference_is_explained``
  A venue balance difference that the local ledger cannot explain must keep the
  account blocked. Clearing the reason code alone is not an explanation.
- ``test_unknown_command_blocks_further_sends_and_keeps_its_own_block``
  While one command is UNKNOWN, no further new risk may be sent and the
  UNKNOWN block may not be cleared by another command succeeding.

The tests use the public service API only, with deterministic fakes. No
network, no real account, no PostgreSQL.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trader.trading.models import (
    ExchangeOrderResult,
    ExchangeOutcome,
    PlaceOrderCommand,
    ReconcileSnapshot,
)
from trader.trading.risk import RiskPolicy
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore

ACCOUNT_ID = "acct"


class ScriptedExchange:
    """Exchange fake that replays a fixed script of place outcomes.

    Each ``place_order`` call consumes the next scripted result, so a test can
    produce "first send uncertain, second send accepted" deterministically.
    """

    def __init__(self, place_outcomes: list[ExchangeOrderResult]):
        self.place_outcomes = list(place_outcomes)
        self.placed: list[tuple[PlaceOrderCommand, str]] = []
        self.cancelled: list[tuple[str, str]] = []

    async def place_order(self, command: PlaceOrderCommand, cl_ord_id: str) -> ExchangeOrderResult:
        self.placed.append((command, cl_ord_id))
        if not self.place_outcomes:
            raise AssertionError("unexpected place_order call")
        return self.place_outcomes.pop(0)

    async def cancel_order(
        self, account_id: str, cl_ord_id: str, command_id: str, symbol: str
    ) -> ExchangeOrderResult:
        self.cancelled.append((cl_ord_id, command_id))
        return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)

    async def query_order(
        self, account_id: str, cl_ord_id: str, symbol: str
    ) -> ExchangeOrderResult:
        return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)


class Clock:
    def __init__(self, now: int = 1_000_000):
        self.now = now

    def __call__(self) -> int:
        return self.now


def command(command_id: str = "cmd-1", **overrides) -> PlaceOrderCommand:
    values = dict(
        command_id=command_id,
        account_id=ACCOUNT_ID,
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        side="BUY",
        qty=Decimal("1"),
        price=Decimal("100"),
        deployment_id="dep-1",
        decision_id="dec-1",
    )
    values.update(overrides)
    return PlaceOrderCommand(**values)


def make_service(*, store=None, exchange=None, clock=None, defer_send=False):
    return TradingService(
        store=store or MemoryTradingStore(),
        exchange=exchange or ScriptedExchange([]),
        policy=RiskPolicy(fee_rate=Decimal("0.001")),
        clock=clock or Clock(),
        defer_send=defer_send,
    )


async def bootstrap(service, *, balances, checked_at_ms, generation, clock_now=None):
    """Submit one complete venue snapshot so the account starts reconciled."""
    if clock_now is not None:
        service.clock.now = clock_now
    snapshot = ReconcileSnapshot(
        account_id=ACCOUNT_ID,
        checked_at_ms=checked_at_ms,
        generation=generation,
        balances=balances,
        marks={"BTCUSDT": {"price": Decimal("100"), "at_ms": checked_at_ms}},
        complete=True,
        stream_healthy=True,
    )
    return await service.recover(snapshot)


def ledger_total(state, asset: str) -> Decimal:
    return sum(
        (Decimal(str(entry["delta"])) for entry in state.ledger if entry["asset"] == asset),
        Decimal(0),
    )


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Phase 0 baseline: the venue balance difference is overwritten into the local "
        "balances and clearing the reason code restores READY, so an unexplained "
        "difference between the local ledger and the venue is silently absorbed."
    ),
)
async def test_balance_mismatch_keeps_blocking_until_the_difference_is_explained():
    clock = Clock(1_000_000)
    service = make_service(clock=clock)
    await bootstrap(
        service,
        balances={"USDT": Decimal("1000"), "BTC": Decimal("0")},
        checked_at_ms=1_000_000,
        generation=1,
    )

    # A fresh, complete snapshot shows 10 USDT less than the opening balance.
    view = await bootstrap(
        service,
        balances={"USDT": Decimal("990"), "BTC": Decimal("0")},
        checked_at_ms=1_000_100,
        generation=2,
        clock_now=1_000_100,
    )
    assert "BALANCE_MISMATCH" in view.reasons
    assert view.ready is False

    state = await service.store.read(ACCOUNT_ID)
    ledger_usdt = ledger_total(state, "USDT")
    venue_usdt = Decimal("990")
    unexplained = ledger_usdt != venue_usdt

    resolved = await service.resolve(ACCOUNT_ID, "BALANCE_MISMATCH")

    # Safe contract: while no fact explains the difference between the local
    # ledger and the venue, removing the reason code alone must not restore
    # READY. The account stays blocked until the difference is explained.
    if unexplained:
        assert resolved.ready is False, (
            "an unexplained balance difference must keep the account blocked; "
            f"ledger={ledger_usdt} venue={venue_usdt} reasons={resolved.reasons}"
        )
        assert resolved.status != "READY"
        assert resolved.reasons


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Phase 0 baseline: resume_queued keeps sending queued commands after one "
        "command became UNKNOWN, and the later accepted command clears the "
        "UNKNOWN_COMMAND block account-wide."
    ),
)
async def test_unknown_command_blocks_further_sends_and_keeps_its_own_block():
    clock = Clock(1_000_000)
    store = MemoryTradingStore()
    exchange = ScriptedExchange(
        [
            ExchangeOrderResult(ExchangeOutcome.UNKNOWN, reason="timeout"),
            ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-2", "OPEN"),
        ]
    )
    # The control process never talks to the venue; it only queues commands.
    control = make_service(store=store, exchange=exchange, clock=clock, defer_send=True)
    # The execution process owns the account and drains the durable queue.
    execution = make_service(store=store, exchange=exchange, clock=clock, defer_send=False)

    await bootstrap(
        control,
        balances={"USDT": Decimal("1000"), "BTC": Decimal("0")},
        checked_at_ms=1_000_000,
        generation=1,
    )

    first = await control.place(command(command_id="cmd-1"))
    second = await control.place(command(command_id="cmd-2"))
    assert first.status == "QUEUED"
    assert second.status == "QUEUED"
    assert exchange.placed == []

    await execution.resume_queued(ACCOUNT_ID)

    # Safe contract: the first send was uncertain, so the second command must
    # not have been sent and the UNKNOWN block must still be in force.
    assert len(exchange.placed) == 1, (
        "no further new risk may be sent while a command is UNKNOWN; "
        f"sent={[item[0].command_id for item in exchange.placed]}"
    )

    state = await store.read(ACCOUNT_ID)
    assert state.commands["cmd-1"].status == "UNKNOWN"

    view = await execution.risk_view(ACCOUNT_ID)
    assert view.ready is False
    assert "UNKNOWN_COMMAND" in view.reasons
