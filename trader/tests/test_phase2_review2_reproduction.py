"""Phase 2 round-2 review reproductions (authored by the independent tester).

Each test states the **contract expectation**, never the current behaviour. A red
result here is the counter-example the engineer needs; the assertion must not be
weakened to match what the code does today.

Scope: the three items the round-2 reviewer flagged as reproduce-able.

* B2 -- a MARKET BUY is settled in quote currency, so the venue may return more
  base than ``command.qty`` while spending no more than the quote budget. Both
  ``OrderAggregate.apply`` and ``OrderAggregate.observe`` still bound the base
  quantity by ``command.qty`` unconditionally.
* B3 -- a MARKET order whose terminal FILLED observation covers less base than
  ``command.qty`` must still leave no reservation on either layer.
* B4 -- a pre-book-contract snapshot keeps its account-level hold, but the
  migration copies only balances/positions into ``UNALLOCATED``, so
  ``allocate`` treats reserved cash as free.

Deterministic fakes only: in-memory store, fixed clock, recording exchange port.
No network, no real account, no shared fixture file.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trader.trading import books as book_ledger
from trader.trading.models import (
    Conflict,
    ExchangeOrderResult,
    ExchangeOutcome,
    Execution,
    OrderObservation,
    PlaceOrderCommand,
    ReconcileSnapshot,
    TradingError,
)
from trader.trading.order_spec import PRICE_SOURCE_MARKET_TICKER, PRICE_SOURCE_SIGNAL_LIMIT
from trader.trading.risk import RiskPolicy
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore, decode_state

# --------------------------------------------------------------------- baseline
ACCOUNT = "acct-repro"
SYMBOL = "BTCUSDT"
BASE = "BTC"
QUOTE = "USDT"
T0 = 1_700_000_000_000
FEE_RATE = Decimal("0.001")
BOOK_A = "book:alpha"
UNALLOCATED = book_ledger.UNALLOCATED

_STATE_FIELDS = (
    "account_id",
    "status",
    "version",
    "balances",
    "positions",
    "external_locked",
    "orders",
    "commands",
    "holds",
    "books",
    "executions",
    "ledger",
    "events",
    "outbox",
    "control",
    "reconciliation",
)


class FixedClock:
    """Deterministic clock; tests move time explicitly."""

    def __init__(self, now: int = T0) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


class RecordingExchange:
    """Exchange port fake that records every outbound call."""

    def __init__(self) -> None:
        self.place_calls: list[tuple[PlaceOrderCommand, str]] = []
        self.place_result = ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-1", "OPEN")

    async def place_order(self, command: PlaceOrderCommand, cl_ord_id: str) -> ExchangeOrderResult:
        self.place_calls.append((command, cl_ord_id))
        return self.place_result

    async def cancel_order(
        self, account_id: str, cl_ord_id: str, command_id: str, symbol: str
    ) -> ExchangeOrderResult:
        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-1", "OPEN")

    async def query_order(
        self, account_id: str, cl_ord_id: str, symbol: str
    ) -> ExchangeOrderResult:
        return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)


def build_service(store=None, exchange=None, clock=None, policy=None):
    return TradingService(
        store=store if store is not None else MemoryTradingStore(),
        exchange=exchange if exchange is not None else RecordingExchange(),
        policy=policy if policy is not None else RiskPolicy(fee_rate=FEE_RATE),
        clock=clock if clock is not None else FixedClock(),
    )


def venue_snapshot(balances=None, *, checked=T0, generation=1, marks=None):
    return ReconcileSnapshot(
        account_id=ACCOUNT,
        checked_at_ms=checked,
        generation=generation,
        balances=(
            balances if balances is not None else {QUOTE: Decimal("1000"), BASE: Decimal("0")}
        ),
        marks=marks if marks is not None else {SYMBOL: {"price": Decimal("100"), "at_ms": checked}},
    )


def market_buy_command(command_id: str, *, qty: str = "1", price: str = "100") -> PlaceOrderCommand:
    """A MARKET BUY whose reference price makes ``qty * price`` the quote budget."""
    return PlaceOrderCommand(
        command_id=command_id,
        account_id=ACCOUNT,
        symbol=SYMBOL,
        base_asset=BASE,
        quote_asset=QUOTE,
        side="BUY",
        qty=Decimal(qty),
        price=Decimal(price),
        deployment_id="dep-repro",
        decision_id=f"dec-{command_id}",
        order_type="MARKET",
        time_in_force="GTC",
        book_id=BOOK_A,
        price_source=PRICE_SOURCE_MARKET_TICKER,
        price_at_ms=T0,
    )


async def _market_service():
    """Account 1000 USDT, all of it explicitly allocated to ``book:alpha``."""
    store = MemoryTradingStore()
    exchange = RecordingExchange()
    clock = FixedClock()
    current = build_service(store=store, exchange=exchange, clock=clock)
    await current.recover(venue_snapshot())
    await current.allocate(
        "alloc-market",
        ACCOUNT,
        BOOK_A,
        QUOTE,
        Decimal("1000"),
        actor="repro",
        reason="market setup",
    )
    return current, exchange, store


async def seed_state(store, state):
    async with store.transaction(ACCOUNT) as target:
        for name in _STATE_FIELDS:
            setattr(target, name, getattr(state, name))


def _legacy_snapshot_with_hold():
    """A snapshot written before the book contract: 1000 USDT, 100.1 reserved."""
    return {
        "account_id": ACCOUNT,
        "status": "READY",
        "version": 3,
        "balances": {QUOTE: "1000", BASE: "0"},
        "positions": {},
        "external_locked": {},
        "orders": {
            "qts-legacy": {
                "cl_ord_id": "qts-legacy",
                "command": {
                    "command_id": "legacy-command",
                    "account_id": ACCOUNT,
                    "symbol": SYMBOL,
                    "base_asset": BASE,
                    "quote_asset": QUOTE,
                    "side": "BUY",
                    "qty": "1",
                    "price": "100",
                    "deployment_id": "legacy",
                    "order_type": "LIMIT",
                    "time_in_force": "GTC",
                    "decision_id": "legacy",
                    "book_id": "",
                    "price_source": PRICE_SOURCE_SIGNAL_LIMIT,
                    "price_at_ms": T0,
                },
                "status": "OPEN",
                "broker_order_id": "b-legacy",
                "booked_qty": "0",
                "booked_quote": "0",
                "observed_qty": "0",
                "observed_at_ms": T0,
            }
        },
        "commands": {
            "legacy-command": {
                "command_id": "legacy-command",
                "cl_ord_id": "qts-legacy",
                "kind": "PLACE",
                "payload_hash": "legacy-hash",
                "status": "QUEUED",
                "actor": "legacy",
            }
        },
        "holds": {"qts-legacy": {"asset": QUOTE, "amount": "100.1"}},
        "books": {},
        "executions": {},
        "ledger": [
            {
                "entry_id": "opening:USDT",
                "execution_key": "",
                "account_id": ACCOUNT,
                "cl_ord_id": "",
                "book_id": "",
                "asset": QUOTE,
                "delta": "1000",
                "kind": "opening_balance",
                "occurred_at_ms": T0,
            }
        ],
        "events": [],
        "outbox": [],
        "control": {},
        "reconciliation": {
            "checked_at_ms": T0,
            "generation": 1,
            "stream_healthy": True,
            "unresolved": [],
            "marks": {SYMBOL: {"price": "100", "at_ms": T0}},
        },
    }


# ============================================ B2: MARKET BUY quote-settled overfill
async def test_b2_market_buy_overfill_must_be_booked_not_conflicted():
    """A better-than-reference MARKET fill may exceed ``qty`` in base terms.

    Quote budget = 1 * 100 = 100 USDT. The venue reports 1.1 base @ 90, which
    spends 99 USDT: inside the budget, so it is the same legal settlement fact
    the adapter produced from ``quoteOrderQty``. The fill must be booked.

    Expected state after booking (contract 13.14): booked_qty 1.1, booked_quote
    99, USDT 901, BTC 1.1 — and the order is NOT terminal (99 of the 100 quote
    budget is unspent and no venue terminal observation arrived), so both hold
    layers keep the real residue 100.1 - 1.1 * 90 = 1.1. A later FILLED
    observation terminates the order and clears both layers.
    """
    current, exchange, store = await _market_service()
    receipt = await current.place(market_buy_command("cmd-b2a"))
    cl_ord_id = receipt.cl_ord_id
    placed = await store.read(ACCOUNT)
    # 1 * 100 * (1 + 0.001) = 100.1 USDT reserved on both layers
    assert placed.holds[cl_ord_id].amount == Decimal("100.1")
    assert placed.books[BOOK_A].holds[cl_ord_id].amount == Decimal("100.1")
    assert len(exchange.place_calls) == 1

    execution = Execution(
        ACCOUNT,
        cl_ord_id,
        SYMBOL,
        "exec-b2a",
        Decimal("1.1"),
        Decimal("90"),
        Decimal("0"),
        "",
        T0 + 1,
    )
    try:
        applied = await current.record_execution(execution)
    except Conflict as exc:
        pytest.fail(
            "MARKET BUY is quote-settled: 1.1 @ 90 spends 99 of the 100 USDT budget, "
            f"so this fill must be booked, not conflicted ({exc})"
        )
    assert applied is True

    state = await store.read(ACCOUNT)
    order = state.orders[cl_ord_id]
    assert order.booked_qty == Decimal("1.1")
    assert order.booked_quote == Decimal("99")
    assert state.balances[QUOTE] == Decimal("901")
    assert state.balances[BASE] == Decimal("1.1")
    assert state.positions[SYMBOL] == Decimal("1.1")
    assert state.books[BOOK_A].positions[SYMBOL] == Decimal("1.1")
    assert order.status == "PARTIALLY_FILLED"
    # not terminal: 1 of the 100 quote budget is unspent and no venue terminal
    # fact arrived, so both layers keep the real residue 100.1 - 1.1 * 90.
    assert state.holds[cl_ord_id].amount == Decimal("1.1")
    assert state.books[BOOK_A].holds[cl_ord_id].amount == Decimal("1.1")
    assert book_ledger.conservation_mismatch(state) == {}

    # Venue terminal evidence — not booked_qty == command.qty — controls the
    # final release (contract 13.14).
    accepted = await current.observe(
        OrderObservation(
            account_id=ACCOUNT,
            cl_ord_id=cl_ord_id,
            status="FILLED",
            cumulative_qty=Decimal("1.1"),
            broker_order_id="b-1",
            observed_at_ms=T0 + 2,
        )
    )
    assert accepted is True
    settled = await store.read(ACCOUNT)
    assert settled.orders[cl_ord_id].status == "FILLED"
    assert cl_ord_id not in settled.holds
    assert cl_ord_id not in settled.books[BOOK_A].holds
    assert book_ledger.conservation_mismatch(settled) == {}


async def test_b2_market_filled_observation_may_exceed_order_qty():
    """The FILLED observation carries the same quote-settled base quantity.

    ``observe`` already exempts MARKET from the "FILLED must cover the order"
    rule, but the earlier bound check still rejects ``cumulative_qty > qty``.
    """
    current, exchange, store = await _market_service()
    receipt = await current.place(market_buy_command("cmd-b2b"))
    cl_ord_id = receipt.cl_ord_id

    observation = OrderObservation(
        account_id=ACCOUNT,
        cl_ord_id=cl_ord_id,
        status="FILLED",
        cumulative_qty=Decimal("1.1"),
        broker_order_id="b-1",
        observed_at_ms=T0 + 2,
    )
    accepted = await current.observe(observation)
    assert accepted is True, "a quote-settled MARKET may fill more base than command.qty"

    state = await store.read(ACCOUNT)
    assert state.orders[cl_ord_id].status == "FILLED"
    assert state.orders[cl_ord_id].observed_qty == Decimal("1.1")
    assert cl_ord_id not in state.holds
    assert cl_ord_id not in state.books[BOOK_A].holds


# ============================== B3: terminal MARKET FILLED on a partial base fill
async def test_b3_market_filled_releases_both_hold_layers():
    """Final-state release must not depend on ``booked_qty == command.qty``.

    The venue fills 0.5 base of a quote-settled MARKET order and then reports
    FILLED with cumulative 0.5. Releasing the rest is correct; keeping a residue
    on either layer is not. The mid-test assertions separate the proportional
    release (at booking) from the terminal release (at observation).
    """
    current, exchange, store = await _market_service()
    receipt = await current.place(market_buy_command("cmd-b3"))
    cl_ord_id = receipt.cl_ord_id

    placed = await store.read(ACCOUNT)
    assert placed.holds[cl_ord_id].amount == Decimal("100.1")
    assert placed.books[BOOK_A].holds[cl_ord_id].amount == Decimal("100.1")

    execution = Execution(
        ACCOUNT,
        cl_ord_id,
        SYMBOL,
        "exec-b3",
        Decimal("0.5"),
        Decimal("100"),
        Decimal("0"),
        "",
        T0 + 1,
    )
    assert await current.record_execution(execution) is True

    booked = await store.read(ACCOUNT)
    assert booked.orders[cl_ord_id].status == "PARTIALLY_FILLED"
    # release = 0.5 * 100 + 0 (zero fee): 100.1 - 50 = 50.1 on BOTH layers
    assert booked.holds[cl_ord_id].amount == Decimal("50.1")
    assert booked.books[BOOK_A].holds[cl_ord_id].amount == Decimal("50.1")

    observation = OrderObservation(
        account_id=ACCOUNT,
        cl_ord_id=cl_ord_id,
        status="FILLED",
        cumulative_qty=Decimal("0.5"),
        broker_order_id="b-1",
        observed_at_ms=T0 + 2,
    )
    assert await current.observe(observation) is True

    settled = await store.read(ACCOUNT)
    assert settled.orders[cl_ord_id].status == "FILLED"
    assert cl_ord_id not in settled.holds, "terminal order kept an account-level reservation"
    assert cl_ord_id not in settled.books[BOOK_A].holds, "terminal order kept a book reservation"
    assert settled.balances[QUOTE] == Decimal("950")
    assert settled.balances[BASE] == Decimal("0.5")
    assert settled.positions[SYMBOL] == Decimal("0.5")
    assert book_ledger.conservation_mismatch(settled) == {}


# ============================ B4: legacy account hold vs UNALLOCATED book layer
def test_b4_migration_mirrors_legacy_hold_into_unallocated():
    """A migrated legacy hold must be mirrored on the book that owns the funds.

    The two-layer reservation is what makes ``book_available`` truthful. With
    the hold copied only to the account layer, ``UNALLOCATED`` advertises 1000
    free while 100.1 is committed to a live order.
    """
    state = decode_state(_legacy_snapshot_with_hold())

    assert state.holds["qts-legacy"].amount == Decimal("100.1")
    assert list(state.books) == [UNALLOCATED]
    assert book_ledger.conservation_mismatch(state) == {}, (
        "balance-only conservation cannot see a missing hold; that is why this "
        "needs its own assertion"
    )

    assert book_ledger.book_hold_amount(state.books[UNALLOCATED], QUOTE) == Decimal("100.1"), (
        "migration copied balances/positions but not the account hold, so the "
        "book layer reports reserved cash as free"
    )
    assert book_ledger.book_available(state.books[UNALLOCATED], QUOTE) == Decimal("899.9")


async def test_b4_allocation_cannot_spend_reserved_unallocated_funds():
    """``allocate`` must not move money that a live hold has already committed."""
    store = MemoryTradingStore()
    current = build_service(store=store, clock=FixedClock())
    await seed_state(store, decode_state(_legacy_snapshot_with_hold()))

    with pytest.raises(TradingError) as refused:
        await current.allocate(
            "cmd-b4-over",
            ACCOUNT,
            BOOK_A,
            QUOTE,
            Decimal("1000"),
            actor="repro",
            reason="b4 over-allocation",
        )
    assert str(refused.value) == "ALLOCATION_INSUFFICIENT_UNALLOCATED"

    untouched = await store.read(ACCOUNT)
    assert untouched.holds["qts-legacy"].amount == Decimal("100.1"), "the hold must survive"
    assert untouched.books[UNALLOCATED].balances[QUOTE] == Decimal("1000")
    assert untouched.books[BOOK_A].balances.get(QUOTE, Decimal("0")) == Decimal("0")

    # The unreserved part may still move: 1000 - 100.1 = 899.9
    await current.allocate(
        "cmd-b4-free",
        ACCOUNT,
        BOOK_A,
        QUOTE,
        Decimal("899.9"),
        actor="repro",
        reason="b4 free part",
    )
    state = await store.read(ACCOUNT)
    assert state.books[UNALLOCATED].balances[QUOTE] == Decimal("100.1")
    assert state.books[BOOK_A].balances[QUOTE] == Decimal("899.9")
    assert book_ledger.book_available(state.books[UNALLOCATED], QUOTE) == Decimal("0")
    assert book_ledger.conservation_mismatch(state) == {}
