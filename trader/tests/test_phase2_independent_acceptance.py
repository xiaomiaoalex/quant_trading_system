"""Phase 2 independent acceptance tests (authored by the independent tester).

Every expectation in this module is derived from the frozen contracts
(``docs/INTERFACE_CONTRACTS.md`` 13.1-13.12 and 13.9,
``docs/PHASE2_BOOK_LEDGER_MIGRATION.md``) and from hand-computed arithmetic.
No expected value is produced by calling the code under test, and no
conclusion is borrowed from the engineer's own tests.

Deterministic fakes only: in-memory store, fixed fake clock, recording
exchange port. No network, no real account, no shared fixture file.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from trader.trading import books as book_ledger
from trader.trading.models import (
    Conflict,
    ExchangeOrderResult,
    ExchangeOutcome,
    Execution,
    NotReady,
    OrderObservation,
    PlaceOrderCommand,
    ReconcileSnapshot,
    TradingError,
    advance_order_status,
)
from trader.trading.order_spec import (
    PRICE_SOURCE_MARKET_CLOSED_BAR,
    PRICE_SOURCE_MARKET_TICKER,
    PRICE_SOURCE_SIGNAL_LIMIT,
    build_order_spec,
    validate_order_command,
)
from trader.trading.risk import RiskPolicy, RiskRejected, SpotRiskEngine
from trader.trading.service import TradingService
from trader.trading.store import MemoryTradingStore, decode_state

# --------------------------------------------------------------------- baseline
ACCOUNT = "acct-indep"
SYMBOL = "BTCUSDT"
BASE = "BTC"
QUOTE = "USDT"
T0 = 1_700_000_000_000
FEE_RATE = Decimal("0.001")
BOOK_A = "book:alpha"
BOOK_B = "book:beta"
UNALLOCATED = book_ledger.UNALLOCATED

_STATE_FIELDS = (
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
    """Deterministic clock; the tests move time explicitly."""

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
        self.cancel_calls: list[tuple[str, str]] = []
        self.place_result = ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-1", "OPEN")

    async def place_order(self, command: PlaceOrderCommand, cl_ord_id: str) -> ExchangeOrderResult:
        self.place_calls.append((command, cl_ord_id))
        return self.place_result

    async def cancel_order(
        self, account_id: str, cl_ord_id: str, command_id: str, symbol: str
    ) -> ExchangeOrderResult:
        self.cancel_calls.append((cl_ord_id, command_id))
        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-1", "OPEN")

    async def query_order(
        self, account_id: str, cl_ord_id: str, symbol: str
    ) -> ExchangeOrderResult:
        return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)


def build_service(store=None, exchange=None, clock=None, policy=None, defer_send=False):
    return TradingService(
        store=store if store is not None else MemoryTradingStore(),
        exchange=exchange if exchange is not None else RecordingExchange(),
        policy=policy if policy is not None else RiskPolicy(fee_rate=FEE_RATE),
        clock=clock if clock is not None else FixedClock(),
        defer_send=defer_send,
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


def place_command(
    command_id="cmd-1",
    *,
    book_id=BOOK_A,
    side="BUY",
    qty="1",
    price="100",
    order_type="LIMIT",
    price_source=PRICE_SOURCE_SIGNAL_LIMIT,
    price_at_ms=T0,
    deployment_id="dep-indep",
):
    return PlaceOrderCommand(
        command_id=command_id,
        account_id=ACCOUNT,
        symbol=SYMBOL,
        base_asset=BASE,
        quote_asset=QUOTE,
        side=side,
        qty=Decimal(qty),
        price=Decimal(price),
        deployment_id=deployment_id,
        decision_id=f"dec-{command_id}",
        order_type=order_type,
        time_in_force="GTC",
        book_id=book_id,
        price_source=price_source,
        price_at_ms=price_at_ms,
    )


async def allocate(current, command_id, book_id, amount, asset=QUOTE, reason="independent test"):
    return await current.allocate(
        command_id,
        ACCOUNT,
        book_id,
        asset,
        Decimal(str(amount)),
        actor="independent-tester",
        reason=reason,
    )


async def seed_state(store, state):
    async with store.transaction(ACCOUNT) as target:
        for name in _STATE_FIELDS:
            setattr(target, name, getattr(state, name))


def _legacy_snapshot_dict(
    *,
    order_status="OPEN",
    booked_qty="0",
    hold="100.1",
    command="ghost-command",
    include_unknown_command=True,
):
    """A snapshot written before the book contract existed."""
    holds = {} if hold is None else {"qts-legacy": {"asset": QUOTE, "amount": hold}}
    unresolved = (
        [
            {
                "code": "UNKNOWN_COMMAND",
                "detail": {"command_id": command, "cl_ord_id": "qts-legacy"},
                "at_ms": T0,
            }
        ]
        if include_unknown_command
        else []
    )
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
                "status": order_status,
                "broker_order_id": "b-legacy",
                "booked_qty": booked_qty,
                "booked_quote": "0",
                "observed_qty": booked_qty,
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
        "holds": holds,
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
        "events": [
            {
                "event_id": "legacy-event-1",
                "event_type": "order.queued",
                "schema_version": 1,
                "trace_id": "legacy",
                "account_id": ACCOUNT,
                "cl_ord_id": "qts-legacy",
                "occurred_at_ms": T0,
                "source": "api",
                "payload": {"book_id": ""},
            }
        ],
        "outbox": [],
        "control": {},
        "reconciliation": {
            "checked_at_ms": T0,
            "generation": 1,
            "stream_healthy": True,
            "unresolved": unresolved,
            "marks": {SYMBOL: {"price": "100", "at_ms": T0}},
        },
    }


# =============================================================== T1: book 隔离
async def _prepared_two_book_service():
    """Account 1000 USDT partitioned as alpha=100, beta=900, UNALLOCATED=0."""
    store = MemoryTradingStore()
    exchange = RecordingExchange()
    clock = FixedClock()
    current = build_service(store=store, exchange=exchange, clock=clock)
    await current.recover(venue_snapshot())
    await allocate(current, "alloc-a", BOOK_A, 100)
    await allocate(current, "alloc-b", BOOK_B, 900)
    return current, exchange, store


async def _prepared_single_book_service(amount=1000):
    """Account 1000 USDT, all of it explicitly allocated to alpha."""
    store = MemoryTradingStore()
    exchange = RecordingExchange()
    clock = FixedClock()
    current = build_service(store=store, exchange=exchange, clock=clock)
    await current.recover(venue_snapshot())
    await allocate(current, "alloc-a", BOOK_A, amount)
    return current, exchange, store


async def oracle_no_borrowing(exchange, store, command_id):
    """T1 oracle: an over-budget order leaves no trace anywhere."""
    assert exchange.place_calls == [], "the venue must never receive an over-budget order"
    state = await store.read(ACCOUNT)
    assert state.commands.get(command_id) is None, "no command record may be created"
    assert state.orders == {}, "no order intent may be created"
    assert state.holds == {}, "no account-layer hold may survive"
    assert state.books[BOOK_A].holds == {}, "no book-layer hold may survive"
    assert state.books[BOOK_B].holds == {}
    assert state.books[BOOK_A].balances[QUOTE] == Decimal("100")
    assert state.books[BOOK_B].balances[QUOTE] == Decimal("900")
    assert state.books[UNALLOCATED].balances[QUOTE] == Decimal("0")
    assert state.balances[QUOTE] == Decimal("1000")
    assert book_ledger.conservation_mismatch(state) == {}


async def test_t1_book_cannot_borrow_another_book_or_the_account_balance():
    """alpha=100, beta=900, account=1000: alpha asking for 200.2 must be refused."""
    current, exchange, store = await _prepared_two_book_service()

    with pytest.raises(RiskRejected) as refused:
        await current.place(place_command("cmd-over-budget", qty="2", price="100"))
    # 2 * 100 * 1.001 = 200.2 > alpha's own 100, even though the account holds 1000.
    assert str(refused.value) == "BOOK_INSUFFICIENT_AVAILABLE_BALANCE"

    await oracle_no_borrowing(exchange, store, "cmd-over-budget")


async def test_t1_positive_control_the_boundary_is_the_books_own_balance():
    """0.9 * 100 * 1.001 = 90.09 <= alpha's 100: the same path must be admitted."""
    current, exchange, store = await _prepared_two_book_service()

    receipt = await current.place(place_command("cmd-within-budget", qty="0.9", price="100"))
    assert receipt.status == "CONFIRMED"
    assert len(exchange.place_calls) == 1

    state = await store.read(ACCOUNT)
    assert state.holds[receipt.cl_ord_id].amount == Decimal("90.09")
    assert state.books[BOOK_A].holds[receipt.cl_ord_id].amount == Decimal("90.09")
    assert state.books[BOOK_B].balances[QUOTE] == Decimal("900")
    assert state.balances[QUOTE] == Decimal("1000")
    assert book_ledger.conservation_mismatch(state) == {}


# ============================================ T2: 双层预约 / 成交记账原子回滚
async def test_t2_book_reservation_failure_rolls_back_both_hold_layers(monkeypatch):
    current, _exchange, store = await _prepared_single_book_service(amount=1000)

    def refuse_book_hold(state, book_id, cl_ord_id, hold):
        raise TradingError("INJECTED_BOOK_HOLD_FAILURE")

    monkeypatch.setattr(book_ledger, "reserve_book_hold", refuse_book_hold)

    with pytest.raises(TradingError) as exc:
        await current.place(place_command("cmd-atomic", qty="2", price="100"))
    assert "INJECTED_BOOK_HOLD_FAILURE" in str(exc.value)

    state = await store.read(ACCOUNT)
    assert state.orders == {}, "the order intent must roll back with the book hold"
    assert state.holds == {}, "the account-layer hold must roll back with the book hold"
    assert state.books[BOOK_A].holds == {}
    assert state.commands.get("cmd-atomic") is None
    assert state.books[BOOK_A].balances[QUOTE] == Decimal("1000")
    assert state.balances[QUOTE] == Decimal("1000")


async def test_t2_execution_booking_failure_rolls_back_ledger_and_balances(monkeypatch):
    store = MemoryTradingStore()
    clock = FixedClock()
    current = build_service(store=store, clock=clock)
    await current.recover(venue_snapshot())
    await allocate(current, "alloc-a", BOOK_A, 1000)
    receipt = await current.place(place_command("cmd-fill", qty="2", price="100"))

    before = await store.read(ACCOUNT)
    before_balances = dict(before.balances)
    before_book = dict(before.books[BOOK_A].balances)
    before_ledger = len(before.ledger)
    before_executions = dict(before.executions)

    def refuse_release(state, book_id, cl_ord_id, asset, amount):
        raise TradingError("INJECTED_RELEASE_FAILURE")

    monkeypatch.setattr(book_ledger, "release_book_execution_hold", refuse_release)

    with pytest.raises(TradingError):
        await current.record_execution(
            Execution(
                ACCOUNT,
                receipt.cl_ord_id,
                SYMBOL,
                "exec-atomic",
                Decimal("1"),
                Decimal("100"),
                Decimal("0.05"),
                QUOTE,
                T0 + 100,
            )
        )

    after = await store.read(ACCOUNT)
    assert after.balances == before_balances, "a failed fill must not move a balance"
    assert after.books[BOOK_A].balances == before_book
    assert len(after.ledger) == before_ledger, "a failed fill must not append ledger entries"
    assert after.executions == before_executions
    assert after.orders[receipt.cl_ord_id].booked_qty == Decimal("0")


# ==================================== T3: 部分成交 / 多币种费用 / 撤销 / UNKNOWN
async def _partially_filled_service():
    store = MemoryTradingStore()
    clock = FixedClock()
    exchange = RecordingExchange()
    current = build_service(store=store, clock=clock, exchange=exchange)
    await current.recover(venue_snapshot())
    await allocate(current, "alloc-a", BOOK_A, 1000)
    receipt = await current.place(place_command("cmd-partial", qty="2", price="100"))
    state = await store.read(ACCOUNT)
    assert state.holds[receipt.cl_ord_id].amount == Decimal("200.2")
    return current, store, receipt.cl_ord_id


async def test_t3_partial_fills_base_quote_third_asset_fees_and_conservation():
    """1.0 USDT-fee + 0.5 base-fee + 0.1 third-asset-fee fills, all hand-computed."""
    current, store, cl_ord_id = await _partially_filled_service()

    await current.record_execution(
        Execution(
            ACCOUNT,
            cl_ord_id,
            SYMBOL,
            "e1",
            Decimal("1"),
            Decimal("100"),
            Decimal("0.05"),
            QUOTE,
            T0 + 10,
        )
    )
    await current.record_execution(
        Execution(
            ACCOUNT,
            cl_ord_id,
            SYMBOL,
            "e2",
            Decimal("0.5"),
            Decimal("100"),
            Decimal("0.02"),
            BASE,
            T0 + 20,
        )
    )
    await current.record_execution(
        Execution(
            ACCOUNT,
            cl_ord_id,
            SYMBOL,
            "e3",
            Decimal("0.1"),
            Decimal("100"),
            Decimal("0.01"),
            "BNB",
            T0 + 30,
        )
    )

    state = await store.read(ACCOUNT)
    # USDT: 1000 - 100 - 0.05 - 50 - 10 = 839.95
    assert state.balances[QUOTE] == Decimal("839.95")
    # BTC: 0 + 1 + 0.5 - 0.02 + 0.1 = 1.58
    assert state.balances[BASE] == Decimal("1.58")
    # BNB: the third-asset fee is booked in its own currency, not absorbed into USDT
    assert state.balances["BNB"] == Decimal("-0.01")
    assert state.books[BOOK_A].balances[QUOTE] == Decimal("839.95")
    assert state.books[BOOK_A].balances[BASE] == Decimal("1.58")
    assert state.books[BOOK_A].balances["BNB"] == Decimal("-0.01")
    assert state.positions[SYMBOL] == Decimal("1.58")
    # release = (100 + 0.05 quote fee) + 50 + 10; base/third-asset fees do not
    # release quote: 200.2 - 160.05 = 40.15 remaining on both layers
    assert state.holds[cl_ord_id].amount == Decimal("40.15")
    assert state.books[BOOK_A].holds[cl_ord_id].amount == Decimal("40.15")
    assert book_ledger.conservation_mismatch(state) == {}
    assert state.orders[cl_ord_id].booked_qty == Decimal("1.6")


async def test_t3_genuine_fills_are_commutative_and_never_dropped():
    """Delivering real fills in a different order must reach the same books."""
    ordered, ordered_store, ordered_cl = await _partially_filled_service()
    swapped, swapped_store, swapped_cl = await _partially_filled_service()

    def fill(cl_ord_id, exec_id, qty, fee_asset):
        return Execution(
            ACCOUNT,
            cl_ord_id,
            SYMBOL,
            exec_id,
            Decimal(qty),
            Decimal("100"),
            Decimal("0.01"),
            fee_asset,
            T0 + 10,
        )

    for exec_id, qty, fee_asset in (
        ("e1", "1", QUOTE),
        ("e2", "0.5", BASE),
        ("e3", "0.1", "BNB"),
    ):
        assert await ordered.record_execution(fill(ordered_cl, exec_id, qty, fee_asset)) is True

    for exec_id, qty, fee_asset in (
        ("e3", "0.1", "BNB"),
        ("e1", "1", QUOTE),
        ("e2", "0.5", BASE),
    ):
        assert await swapped.record_execution(fill(swapped_cl, exec_id, qty, fee_asset)) is True

    first = await ordered_store.read(ACCOUNT)
    second = await swapped_store.read(ACCOUNT)
    assert first.balances == second.balances
    assert first.books[BOOK_A].balances == second.books[BOOK_A].balances
    # every genuine fill is booked: 1 + 0.5 + 0.1 = 1.6 of 2
    assert first.orders[ordered_cl].booked_qty == Decimal("1.6")
    assert second.orders[swapped_cl].booked_qty == Decimal("1.6")
    # Hand-computed from the contract. This scenario gives every fill fee 0.01, so the
    # BTC-fee fill reduces BTC by 0.01 and the USDT-fee fill reduces USDT by 0.01:
    #   BTC  = 1 + (0.5 - 0.01) + 0.1          = 1.59
    #   USDT = 1000 - 100 - 0.01 - 50 - 10     = 839.99
    #   BNB  = 0 - 0.01                        = -0.01
    #   200.2 - (100 + 0.01 quote fee) - 50 - 10 = 40.19 reserved on both layers
    assert first.books[BOOK_A].balances[BASE] == Decimal("1.59")
    assert first.books[BOOK_A].balances[QUOTE] == Decimal("839.99")
    assert first.books[BOOK_A].balances["BNB"] == Decimal("-0.01")
    assert first.positions[SYMBOL] == Decimal("1.59")
    assert second.positions[SYMBOL] == Decimal("1.59")
    assert first.holds[ordered_cl].amount == Decimal("40.19")
    assert first.books[BOOK_A].holds[ordered_cl].amount == Decimal("40.19")
    assert second.holds[swapped_cl].amount == Decimal("40.19")
    assert second.books[BOOK_A].holds[swapped_cl].amount == Decimal("40.19")
    assert book_ledger.conservation_mismatch(first) == {}


async def test_t3_duplicate_execution_is_no_op_and_conflicting_content_is_refused():
    current, store, cl_ord_id = await _partially_filled_service()
    fill = Execution(
        ACCOUNT,
        cl_ord_id,
        SYMBOL,
        "dup-1",
        Decimal("1"),
        Decimal("100"),
        Decimal("0.05"),
        QUOTE,
        T0 + 10,
    )

    assert await current.record_execution(fill) is True
    assert await current.record_execution(fill) is False, "an identical fill must be a no-op"

    state = await store.read(ACCOUNT)
    assert state.balances[QUOTE] == Decimal("899.95")
    assert state.balances[BASE] == Decimal("1")

    with pytest.raises(Conflict) as conflict:
        await current.record_execution(
            Execution(
                ACCOUNT,
                cl_ord_id,
                SYMBOL,
                "dup-1",
                Decimal("1.5"),
                Decimal("100"),
                Decimal("0.05"),
                QUOTE,
                T0 + 10,
            )
        )
    assert str(conflict.value) == "EXECUTION_KEY_CONFLICT"

    after = await store.read(ACCOUNT)
    assert after.balances[QUOTE] == Decimal("899.95"), "a refused fill must not move a balance"


async def test_t3_cancel_releases_both_remaining_hold_layers():
    current, store, cl_ord_id = await _partially_filled_service()
    await current.record_execution(
        Execution(
            ACCOUNT,
            cl_ord_id,
            SYMBOL,
            "e1",
            Decimal("1"),
            Decimal("100"),
            Decimal("0.05"),
            QUOTE,
            T0 + 10,
        )
    )

    accepted = await current.observe(
        OrderObservation(ACCOUNT, cl_ord_id, "CANCELLED", Decimal("1"), observed_at_ms=T0 + 50)
    )
    assert accepted is True

    state = await store.read(ACCOUNT)
    assert cl_ord_id not in state.holds, "the account-layer remainder must be released"
    assert cl_ord_id not in state.books[BOOK_A].holds, "the book-layer remainder must be released"
    assert state.balances[QUOTE] == Decimal("899.95"), "releasing a hold must not move a balance"
    assert book_ledger.conservation_mismatch(state) == {}


async def test_t3_unknown_place_keeps_both_hold_layers_and_blocks_new_risk():
    store = MemoryTradingStore()
    clock = FixedClock()
    exchange = RecordingExchange()
    exchange.place_result = ExchangeOrderResult(ExchangeOutcome.UNKNOWN, "", "OPEN", "TIMEOUT")
    current = build_service(store=store, clock=clock, exchange=exchange)
    await current.recover(venue_snapshot())
    await allocate(current, "alloc-a", BOOK_A, 1000)

    receipt = await current.place(place_command("cmd-unknown", qty="1", price="100"))
    assert receipt.status == "UNKNOWN"

    state = await store.read(ACCOUNT)
    assert state.holds[receipt.cl_ord_id].amount == Decimal("100.1")
    assert state.books[BOOK_A].holds[receipt.cl_ord_id].amount == Decimal("100.1")

    with pytest.raises(NotReady) as blocked:
        await current.place(place_command("cmd-after-unknown", qty="1", price="100"))
    assert str(blocked.value) == "UNKNOWN_COMMAND_PENDING"
    assert len(exchange.place_calls) == 1, "an uncertain send must never be repeated"


# ============================================ T4: 迁移不猜历史 / 不改写原事实
async def test_t4_legacy_snapshot_migration_preserves_facts_and_does_not_guess_attribution():
    store = MemoryTradingStore()
    current = build_service(store=store, clock=FixedClock())
    await seed_state(store, decode_state(_legacy_snapshot_dict(include_unknown_command=False)))

    state = await store.read(ACCOUNT)
    assert list(state.books) == [UNALLOCATED], "migration must not invent a strategy book"
    assert all(
        not book_ledger.is_strategy_book(book_id)
        for book_id in state.books
        if book_id != UNALLOCATED
    )
    assert state.books[UNALLOCATED].balances[QUOTE] == Decimal("1000")
    # original append-only facts are untouched
    assert [entry["entry_id"] for entry in state.ledger] == ["opening:USDT"]
    assert [entry["event_id"] for entry in state.events] == ["legacy-event-1"]
    assert state.orders["qts-legacy"].command.command_id == "legacy-command"
    assert state.orders["qts-legacy"].command.book_id == "", "the order intent is not rewritten"
    assert state.books[UNALLOCATED].balances[BASE] == Decimal("0")
    assert state.balances[QUOTE] == Decimal("1000")
    assert state.holds["qts-legacy"].amount == Decimal("100.1")

    marker = state.reconciliation[book_ledger.BOOK_LEDGER_KEY]
    assert "qts-legacy" in marker["unattributed_orders"]
    assert "BOOK_ATTRIBUTION_UNKNOWN" in book_ledger.book_reasons(state)

    # A real admission decision on the migrated state is refused, not defaulted.
    engine = SpotRiskEngine(RiskPolicy(fee_rate=FEE_RATE))
    with pytest.raises(NotReady) as blocked:
        engine.evaluate(state, place_command("cmd-during-attribution", qty="1", price="100"), T0)
    assert str(blocked.value) == "BOOK_ATTRIBUTION_UNKNOWN"

    # The blocking difference is also visible to the operator through the service.
    await current.observe(
        OrderObservation(ACCOUNT, "qts-legacy", "OPEN", Decimal("0"), observed_at_ms=T0)
    )
    view = await current.risk_view(ACCOUNT)
    assert "BOOK_ATTRIBUTION_UNKNOWN" in view.reasons
    assert view.ready is False
    with pytest.raises(NotReady):
        await current.place(place_command("cmd-during-attribution", qty="1", price="100"))


async def test_t4_repeated_migration_is_idempotent():
    store = MemoryTradingStore()
    await seed_state(store, decode_state(_legacy_snapshot_dict()))

    before = await store.read(ACCOUNT)
    keys_before = sorted(before.reconciliation[book_ledger.BOOK_LEDGER_KEY]["unattributed_orders"])

    async with store.transaction(ACCOUNT) as state:
        book_ledger.migrate_book_ledger(state)
    async with store.transaction(ACCOUNT) as state:
        book_ledger.migrate_book_ledger(state)

    after = await store.read(ACCOUNT)
    keys_after = sorted(after.reconciliation[book_ledger.BOOK_LEDGER_KEY]["unattributed_orders"])
    assert keys_after == keys_before
    assert sorted(after.books) == sorted(before.books)
    assert after.books[UNALLOCATED].balances == before.books[UNALLOCATED].balances
    assert len(after.ledger) == len(before.ledger)


async def test_t4_legacy_unknown_command_scope_follows_the_evidence():
    store = MemoryTradingStore()
    await seed_state(store, decode_state(_legacy_snapshot_dict(command="ghost-command")))
    unprovable = await store.read(ACCOUNT)
    marker = unprovable.reconciliation[book_ledger.BOOK_LEDGER_KEY]
    assert marker["legacy_unknown_commands"] == ["ghost-command"]
    assert unprovable.reconciliation["unresolved"][0].get("scope") is None

    store = MemoryTradingStore()
    await seed_state(store, decode_state(_legacy_snapshot_dict(command="legacy-command")))
    provable = await store.read(ACCOUNT)
    assert provable.reconciliation[book_ledger.BOOK_LEDGER_KEY]["legacy_unknown_commands"] == []
    assert provable.reconciliation["unresolved"][0]["scope"] == "legacy-command"


async def test_t4_resolve_clears_attribution_without_moving_balances():
    store = MemoryTradingStore()
    current = build_service(store=store, clock=FixedClock())
    await seed_state(
        store, decode_state(_legacy_snapshot_dict(order_status="FILLED", booked_qty="1", hold=None))
    )
    seeded = await store.read(ACCOUNT)
    assert "qts-legacy" in seeded.reconciliation[book_ledger.BOOK_LEDGER_KEY]["unattributed_orders"]

    with pytest.raises(TradingError) as unknown_book:
        await current.resolve(
            ACCOUNT,
            "BOOK_ATTRIBUTION_UNKNOWN",
            actor="independent-tester",
            evidence={
                "kind": "operator_attribution",
                "reference": "ticket-indep-0",
                "reason": "negative case",
                "orders": {"qts-legacy": "book:ghost"},
            },
        )
    assert str(unknown_book.value) == "RESOLVE_EVIDENCE_BOOK_UNKNOWN"

    with pytest.raises(TradingError) as mismatch:
        await current.resolve(
            ACCOUNT,
            "BOOK_ATTRIBUTION_UNKNOWN",
            actor="independent-tester",
            evidence={
                "kind": "operator_attribution",
                "reference": "ticket-indep-0",
                "reason": "negative case",
                "orders": {"qts-other": UNALLOCATED},
            },
        )
    assert str(mismatch.value) == "RESOLVE_EVIDENCE_ORDER_MISMATCH"

    view = await current.resolve(
        ACCOUNT,
        "BOOK_ATTRIBUTION_UNKNOWN",
        actor="independent-tester",
        evidence={
            "kind": "operator_attribution",
            "reference": "ticket-indep-1",
            "reason": "independent tester verified the venue statement",
            "orders": {"qts-legacy": UNALLOCATED},
        },
    )
    assert "BOOK_ATTRIBUTION_UNKNOWN" not in view.reasons

    state = await store.read(ACCOUNT)
    assert state.books[UNALLOCATED].balances[QUOTE] == Decimal("1000"), "resolve moves no balance"
    assert book_ledger.conservation_mismatch(state) == {}


# ============================== T5: 规格显式性 / 价格来源 / 幂等 / Control→Execution
def test_t5_order_type_must_be_explicit_and_metadata_is_not_consulted():
    assert "metadata" not in inspect.signature(build_order_spec).parameters

    with pytest.raises(TradingError) as refused:
        build_order_spec(
            symbol=SYMBOL,
            side="BUY",
            order_type=None,
            qty=Decimal("1"),
            limit_price=Decimal("100"),
            reference_price=Decimal("100"),
            reference_price_source=PRICE_SOURCE_MARKET_TICKER,
            reference_at_ms=T0,
            decision_at_ms=T0,
            time_in_force="GTC",
            book_id=BOOK_A,
        )
    assert str(refused.value) == "ORDER_TYPE_REQUIRED"


def test_t5_limit_uses_the_signal_price_and_market_traces_its_reference():
    limit = build_order_spec(
        symbol=SYMBOL,
        side="BUY",
        order_type="LIMIT",
        qty=Decimal("1"),
        limit_price=Decimal("123.5"),
        reference_price=Decimal("999"),
        reference_price_source=PRICE_SOURCE_MARKET_TICKER,
        reference_at_ms=T0 - 5_000,
        decision_at_ms=T0,
        time_in_force="GTC",
        book_id=BOOK_A,
    )
    assert limit.price == Decimal("123.5"), "a LIMIT order is priced by the signal"
    assert limit.price_source == PRICE_SOURCE_SIGNAL_LIMIT
    assert limit.price_at_ms == T0, "a LIMIT order records the decision time"

    market = build_order_spec(
        symbol=SYMBOL,
        side="BUY",
        order_type="MARKET",
        qty=Decimal("1"),
        limit_price=Decimal("123.5"),
        reference_price=Decimal("98.75"),
        reference_price_source=PRICE_SOURCE_MARKET_CLOSED_BAR,
        reference_at_ms=T0 - 1_000,
        decision_at_ms=T0,
        time_in_force="GTC",
        book_id=BOOK_A,
    )
    assert market.price == Decimal("98.75"), "a MARKET order is priced by its reference"
    assert market.price_source == PRICE_SOURCE_MARKET_CLOSED_BAR
    assert market.price_at_ms == T0 - 1_000, "a MARKET order records the reference time"

    with pytest.raises(TradingError) as refused:
        build_order_spec(
            symbol=SYMBOL,
            side="BUY",
            order_type="MARKET",
            qty=Decimal("1"),
            limit_price=Decimal("123.5"),
            reference_price=Decimal("98.75"),
            reference_price_source=PRICE_SOURCE_SIGNAL_LIMIT,
            reference_at_ms=T0,
            decision_at_ms=T0,
            time_in_force="GTC",
            book_id=BOOK_A,
        )
    assert str(refused.value) == "INVALID_ORDER_PRICE_SOURCE"

    with pytest.raises(TradingError) as refused:
        build_order_spec(
            symbol=SYMBOL,
            side="BUY",
            order_type="LIMIT",
            qty=Decimal("1"),
            limit_price=Decimal("100"),
            reference_price=Decimal("100"),
            reference_price_source=PRICE_SOURCE_MARKET_TICKER,
            reference_at_ms=T0,
            decision_at_ms=-1,
            time_in_force="GTC",
            book_id=BOOK_A,
        )
    assert str(refused.value) == "ORDER_PRICE_TIME_REQUIRED"

    with pytest.raises(TradingError) as refused:
        validate_order_command(place_command(book_id=UNALLOCATED))
    assert str(refused.value) == "UNALLOCATED_BOOK_CANNOT_TRADE"


async def test_t5_economic_idempotency_includes_book_id_and_price_source():
    store = MemoryTradingStore()
    exchange = RecordingExchange()
    current = build_service(store=store, exchange=exchange, clock=FixedClock())
    await current.recover(venue_snapshot())
    await allocate(current, "alloc-a", BOOK_A, 1000)

    first = await current.place(place_command("cmd-idem", qty="1", price="100"))
    again = await current.place(place_command("cmd-idem", qty="1", price="100"))
    assert first.cl_ord_id == again.cl_ord_id
    assert again.status == "CONFIRMED"
    assert len(exchange.place_calls) == 1, "an identical retry must not reach the venue twice"

    with pytest.raises(Conflict) as conflict:
        await current.place(place_command("cmd-idem", qty="1", price="100", book_id=BOOK_B))
    assert str(conflict.value) == "COMMAND_ID_REUSED_WITH_DIFFERENT_PARAMETERS"

    with pytest.raises(Conflict):
        await current.place(
            place_command("cmd-idem", qty="1", price="100", price_source="MANUAL_LIMIT")
        )
    assert len(exchange.place_calls) == 1


async def test_t5_terminal_status_never_regresses_and_duplicate_observations_are_idempotent():
    store = MemoryTradingStore()
    current = build_service(store=store, clock=FixedClock())
    await current.recover(venue_snapshot())
    await allocate(current, "alloc-a", BOOK_A, 1000)
    receipt = await current.place(place_command("cmd-terminal", qty="1", price="100"))
    await current.record_execution(
        Execution(
            ACCOUNT,
            receipt.cl_ord_id,
            SYMBOL,
            "e-final",
            Decimal("1"),
            Decimal("100"),
            Decimal("0"),
            "",
            T0 + 10,
        )
    )

    state = await store.read(ACCOUNT)
    order = state.orders[receipt.cl_ord_id]
    assert order.status == "FILLED"

    with pytest.raises(Conflict) as conflict:
        advance_order_status(order, "CANCELLED")
    assert "Conflicting terminal" in str(conflict.value)
    assert order.status == "FILLED"

    assert (
        await current.observe(
            OrderObservation(
                ACCOUNT, receipt.cl_ord_id, "FILLED", Decimal("1"), observed_at_ms=T0 + 20
            )
        )
        is True
    )
    assert (
        await current.observe(
            OrderObservation(
                ACCOUNT, receipt.cl_ord_id, "FILLED", Decimal("1"), observed_at_ms=T0 + 20
            )
        )
        is True
    )
    after = await store.read(ACCOUNT)
    assert after.orders[receipt.cl_ord_id].status == "FILLED"
    assert after.orders[receipt.cl_ord_id].booked_qty == Decimal("1")


async def test_t5_control_only_queues_allocation_and_execution_applies_it():
    store = MemoryTradingStore()
    clock = FixedClock()
    control = build_service(store=store, clock=clock, defer_send=True)
    execution = build_service(store=store, clock=clock)
    await control.recover(venue_snapshot())

    queued = await control.allocate(
        "alloc-ctrl",
        ACCOUNT,
        BOOK_A,
        QUOTE,
        Decimal("600"),
        actor="owner",
        reason="control-side queue",
    )
    assert queued.status == "QUEUED"

    after_queue = await store.read(ACCOUNT)
    assert after_queue.books[UNALLOCATED].balances[QUOTE] == Decimal(
        "1000"
    ), "control moves nothing"
    assert BOOK_A not in after_queue.books
    assert not any(entry["event_type"] == "book.allocated" for entry in after_queue.events)
    assert any(entry["event_type"] == "book.allocation_queued" for entry in after_queue.events)

    with pytest.raises(NotReady) as refused:
        await control.resume_queued(ACCOUNT)
    assert str(refused.value) == "CONTROL_PROCESS_CANNOT_EXECUTE"

    await execution.resume_queued(ACCOUNT)
    applied = await store.read(ACCOUNT)
    assert applied.commands["alloc-ctrl"].status == "CONFIRMED"
    assert applied.books[BOOK_A].balances[QUOTE] == Decimal("600")
    assert applied.books[UNALLOCATED].balances[QUOTE] == Decimal("400")
    assert applied.balances[QUOTE] == Decimal("1000")
    assert any(entry["event_type"] == "book.allocated" for entry in applied.events)
    assert execution.exchange.place_calls == [], "an allocation never touches the venue"
    assert book_ledger.conservation_mismatch(applied) == {}

    await execution.resume_queued(ACCOUNT)
    replayed = await store.read(ACCOUNT)
    assert replayed.books[BOOK_A].balances[QUOTE] == Decimal("600"), "replay must not re-allocate"


# ============================================ T6: C2 精确原因码 / 其他阻断不回退
async def test_t6_stale_valuation_reason_is_not_masked_by_the_book_migration():
    """C2: migration must not turn VALUATION_STALE_OR_MISSING into ACCOUNT_NOT_RECONCILED."""
    policy = RiskPolicy(
        max_reconciliation_age_ms=600_000, max_mark_age_ms=60_000, fee_rate=FEE_RATE
    )
    store = MemoryTradingStore()
    clock = FixedClock()
    current = build_service(store=store, clock=clock, policy=policy)

    legacy = decode_state(_legacy_snapshot_dict())
    legacy.books = {}
    legacy.orders = {}
    legacy.commands = {}
    legacy.holds = {}
    legacy.reconciliation = {
        "checked_at_ms": T0,
        "generation": 1,
        "stream_healthy": True,
        "unresolved": [],
        "marks": {SYMBOL: {"price": Decimal("100"), "at_ms": T0}},
    }
    legacy.balances = {QUOTE: Decimal("1000"), BASE: Decimal("1")}
    await seed_state(store, legacy)

    # While the mark is fresh, the first write transaction performs the real book
    # migration and the persisted status stays READY.
    await allocate(current, "alloc-migrate", BOOK_A, 1000)
    assert (await store.read(ACCOUNT)).status == "READY"

    clock.advance(120_000)  # mark is stale; the reconciliation snapshot is still fresh
    with pytest.raises(NotReady) as refused:
        await current.place(place_command("cmd-stale", qty="1", price="100"))
    assert str(refused.value) == "VALUATION_STALE_OR_MISSING"
    assert str(refused.value) != "ACCOUNT_NOT_RECONCILED", "the precise code must not be masked"

    view = await current.risk_view(ACCOUNT)
    assert "VALUATION_INCOMPLETE" in view.reasons

    clock.now = T0  # a fresh mark must admit the very same order
    fresh = await current.place(place_command("cmd-fresh", qty="1", price="100"))
    assert fresh.status == "CONFIRMED", "a fresh valuation must not be refused (setup control)"


async def test_t6_unknown_command_still_blocks_new_risk():
    store = MemoryTradingStore()
    exchange = RecordingExchange()
    exchange.place_result = ExchangeOrderResult(ExchangeOutcome.UNAVAILABLE, "", "OPEN", "DOWN")
    current = build_service(store=store, exchange=exchange, clock=FixedClock())
    await current.recover(venue_snapshot())
    await allocate(current, "alloc-a", BOOK_A, 1000)

    assert (await current.place(place_command("cmd-unknown", qty="1", price="100"))).status == (
        "UNKNOWN"
    )
    with pytest.raises(NotReady) as blocked:
        await current.place(place_command("cmd-next", qty="1", price="100"))
    assert str(blocked.value) == "UNKNOWN_COMMAND_PENDING"
    assert len(exchange.place_calls) == 1


async def test_t6_unreconciled_account_still_blocks_new_risk():
    store = MemoryTradingStore()
    current = build_service(store=store, clock=FixedClock())
    await seed_state(store, decode_state(_legacy_snapshot_dict()))
    async with store.transaction(ACCOUNT) as state:
        state.reconciliation = {}

    view = await current.risk_view(ACCOUNT)
    assert "ACCOUNT_NOT_RECONCILED" in view.reasons
    assert view.status == "RECOVERING"

    with pytest.raises(NotReady) as blocked:
        await current.place(place_command("cmd-no-recon", qty="1", price="100"))
    assert str(blocked.value) == "ACCOUNT_NOT_RECONCILED"


# ============================================================ T7: 受控变异实验
async def oracle_two_layer_hold(store, cl_ord_id, expected):
    """T7 oracle: both reservation layers exist with the same amount."""
    state = await store.read(ACCOUNT)
    account_hold = state.holds.get(cl_ord_id)
    book_hold = state.books[BOOK_A].holds.get(cl_ord_id)
    assert account_hold is not None and account_hold.amount == expected
    assert book_hold is not None and book_hold.amount == expected


async def oracle_hold_layers_released_together(store, cl_ord_id):
    """T7 oracle: a completed order releases both reservation layers."""
    state = await store.read(ACCOUNT)
    assert cl_ord_id not in state.holds
    assert cl_ord_id not in state.books[BOOK_A].holds


async def test_t7_normal_two_layer_hold_holds_before_mutation():
    current, _exchange, store = await _prepared_two_book_service()
    receipt = await current.place(place_command("cmd-oracle", qty="0.9", price="100"))
    await oracle_two_layer_hold(store, receipt.cl_ord_id, Decimal("90.09"))


async def test_t7_mutation_book_may_borrow_the_account_balance_is_detected(monkeypatch):
    current, exchange, store = await _prepared_two_book_service()

    def borrowing_book_available(book, asset):
        return Decimal("1000")  # broken: a book may spend the whole account balance

    monkeypatch.setattr(book_ledger, "book_available", borrowing_book_available)

    receipt = await current.place(place_command("cmd-mutant", qty="2", price="100"))
    assert receipt.status == "CONFIRMED"
    assert len(exchange.place_calls) == 1
    state = await store.read(ACCOUNT)
    assert state.books[BOOK_A].holds[receipt.cl_ord_id].amount == Decimal("200.2")
    assert state.books[BOOK_A].balances[QUOTE] == Decimal("100"), "100 backing a 200.2 hold"

    with pytest.raises(AssertionError):
        await oracle_no_borrowing(exchange, store, "cmd-mutant")


async def test_t7_mutation_missing_book_hold_layer_is_detected(monkeypatch):
    current, _exchange, store = await _prepared_two_book_service()

    monkeypatch.setattr(book_ledger, "reserve_book_hold", lambda *args, **kwargs: None)

    receipt = await current.place(place_command("cmd-mutant2", qty="0.9", price="100"))
    state = await store.read(ACCOUNT)
    assert state.holds[receipt.cl_ord_id].amount == Decimal("90.09"), "account layer only"
    assert state.books[BOOK_A].holds == {}, "the book layer is silently missing"

    with pytest.raises(AssertionError):
        await oracle_two_layer_hold(store, receipt.cl_ord_id, Decimal("90.09"))


async def test_t7_mutation_book_hold_not_released_on_fill_is_detected(monkeypatch):
    """A fill that never releases the book layer must break the two-layer invariant.

    The injected defect is deliberately scoped to a *partial* fill. A full fill also
    reaches the terminal cleanup path (``_reconcile_order_evidence`` ->
    ``release_order_hold`` -> ``release_book_hold``), which releases the book layer
    through a function that this experiment does not patch; the first version of this
    test used a full fill and therefore asserted a specific internal residue that the
    broken run does not produce. The assertion below targets the business invariant
    (both reservation layers stay in sync) instead of a guessed residue.
    """
    current, store, cl_ord_id = await _partially_filled_service()
    monkeypatch.setattr(book_ledger, "release_book_execution_hold", lambda *a, **k: None)

    applied = await current.record_execution(
        Execution(
            ACCOUNT,
            cl_ord_id,
            SYMBOL,
            "e-m3",
            Decimal("0.5"),
            Decimal("100"),
            Decimal("0"),
            "",
            T0 + 10,
        )
    )
    assert applied is True, "the injected defect must not swallow a genuine fill"

    state = await store.read(ACCOUNT)
    assert state.orders[cl_ord_id].booked_qty == Decimal("0.5")
    # the account layer released 0.5 * 100 + 0 = 50, so 200.2 -> 150.2
    assert state.holds[cl_ord_id].amount == Decimal("150.2")
    # observable divergence: the book layer is no longer in sync with the account layer
    book_hold = state.books[BOOK_A].holds.get(cl_ord_id)
    assert book_hold is None or book_hold.amount != Decimal("150.2")
    with pytest.raises(AssertionError):
        await oracle_two_layer_hold(store, cl_ord_id, Decimal("150.2"))


async def test_t7_normal_partial_fill_releases_both_layers_in_sync():
    """Unmutated counterpart of the M3 experiment: both layers release together."""
    current, store, cl_ord_id = await _partially_filled_service()
    applied = await current.record_execution(
        Execution(
            ACCOUNT,
            cl_ord_id,
            SYMBOL,
            "e-normal-partial",
            Decimal("0.5"),
            Decimal("100"),
            Decimal("0"),
            "",
            T0 + 10,
        )
    )
    assert applied is True
    await oracle_two_layer_hold(store, cl_ord_id, Decimal("150.2"))


async def test_t7_normal_full_fill_leaves_no_reservation_on_either_layer():
    """A completed order releases both layers, including via the terminal cleanup path.

    This documents the behaviour the first version of the M3 experiment mis-modelled,
    and it is a business invariant in its own right: no stale reservation may survive
    a completed order.
    """
    current, store, cl_ord_id = await _partially_filled_service()
    for exec_id in ("e-normal-full-1", "e-normal-full-2"):
        applied = await current.record_execution(
            Execution(
                ACCOUNT,
                cl_ord_id,
                SYMBOL,
                exec_id,
                Decimal("1"),
                Decimal("100"),
                Decimal("0"),
                "",
                T0 + 10,
            )
        )
        assert applied is True
    await oracle_hold_layers_released_together(store, cl_ord_id)


# ============== T9: 缺口补齐 —— 2.8 同币反向在途冲突 / 2.12 每 book 权益与预算
async def _two_book_service_with_base_asset():
    """account = 2000 USDT + 2 BTC; alpha and beta each receive 1000 USDT + 1 BTC."""
    store = MemoryTradingStore()
    exchange = RecordingExchange()
    clock = FixedClock()
    current = build_service(store=store, exchange=exchange, clock=clock)
    await current.recover(venue_snapshot(balances={QUOTE: Decimal("2000"), BASE: Decimal("2")}))
    for command_id, book_id, amount, asset in (
        ("alloc-a-usdt", BOOK_A, "1000", QUOTE),
        ("alloc-a-btc", BOOK_A, "1", BASE),
        ("alloc-b-usdt", BOOK_B, "1000", QUOTE),
        ("alloc-b-btc", BOOK_B, "1", BASE),
    ):
        await allocate(current, command_id, book_id, amount, asset=asset)
    return current, exchange, store


async def test_t9_same_book_opposite_in_flight_is_refused_without_new_risk():
    """2.8 / 13.9: an open BUY on alpha blocks a SELL on alpha, but not on beta.

    The refusing side is the *book*, not the account: both books hold enough BTC
    for the SELL, so a refusal on alpha can only come from the opposite in-flight
    rule. Nothing may be queued, netted, reserved or sent.
    """
    current, exchange, store = await _two_book_service_with_base_asset()

    buy = await current.place(place_command("cmd-buy-a", book_id=BOOK_A, side="BUY"))
    assert buy.status == "CONFIRMED"
    before = await store.read(ACCOUNT)
    # 1 * 100 * (1 + 0.001) = 100.1 USDT reserved on both layers of alpha.
    assert before.holds[buy.cl_ord_id].amount == Decimal("100.1")
    assert before.books[BOOK_A].holds[buy.cl_ord_id].amount == Decimal("100.1")
    sends_before = len(exchange.place_calls)

    with pytest.raises(RiskRejected) as refused:
        await current.place(place_command("cmd-sell-a", book_id=BOOK_A, side="SELL"))
    assert str(refused.value) == "BOOK_OPPOSITE_ORDER_IN_FLIGHT"

    after = await store.read(ACCOUNT)
    # not queued: no command record and no order intent for the refused command
    assert after.commands.get("cmd-sell-a") is None, "a refused command must not be queued"
    assert set(after.orders) == {buy.cl_ord_id}, "no second order intent may be created"
    # no new reservation on either layer
    assert set(after.holds) == {buy.cl_ord_id}
    assert set(after.books[BOOK_A].holds) == {buy.cl_ord_id}
    assert after.books[BOOK_A].holds[buy.cl_ord_id].amount == Decimal("100.1")
    # not netted: balances, book partitions and positions are untouched
    assert after.balances[QUOTE] == Decimal("2000")
    assert after.balances[BASE] == Decimal("2")
    assert after.books[BOOK_A].balances[QUOTE] == Decimal("1000")
    assert after.books[BOOK_A].balances[BASE] == Decimal("1")
    assert after.positions.get(SYMBOL, Decimal("0")) == Decimal("0")
    assert after.orders[buy.cl_ord_id].booked_qty == Decimal("0")
    # no venue send
    assert len(exchange.place_calls) == sends_before == 1
    assert book_ledger.conservation_mismatch(after) == {}

    # scope control: the same SELL on a different book must be admitted
    sell_b = await current.place(place_command("cmd-sell-b", book_id=BOOK_B, side="SELL"))
    assert sell_b.status == "CONFIRMED"
    final = await store.read(ACCOUNT)
    assert final.holds[sell_b.cl_ord_id].amount == Decimal("1")
    assert final.books[BOOK_B].holds[sell_b.cl_ord_id].amount == Decimal("1")
    # alpha's BUY keeps its reservation: no cross-book netting happened
    assert final.books[BOOK_A].holds[buy.cl_ord_id].amount == Decimal("100.1")
    assert final.positions.get(SYMBOL, Decimal("0")) == Decimal("0")
    assert book_ledger.conservation_mismatch(final) == {}


async def _book_equity_service():
    """account = 3000 USDT + 3 BTC; alpha=1000 USDT/1 BTC, beta=500 USDT/2 BTC.

    UNALLOCATED is left holding the other 1500 USDT, so the three partitions
    differ from each other and from the account aggregate.
    """
    store = MemoryTradingStore()
    exchange = RecordingExchange()
    clock = FixedClock()
    current = build_service(store=store, exchange=exchange, clock=clock)
    await current.recover(venue_snapshot(balances={QUOTE: Decimal("3000"), BASE: Decimal("3")}))
    for command_id, book_id, amount, asset in (
        ("alloc-a-usdt", BOOK_A, "1000", QUOTE),
        ("alloc-a-btc", BOOK_A, "1", BASE),
        ("alloc-b-usdt", BOOK_B, "500", QUOTE),
        ("alloc-b-btc", BOOK_B, "2", BASE),
    ):
        await allocate(current, command_id, book_id, amount, asset=asset)
    return current, exchange, store


async def test_t9_joint_backtest_reads_per_book_equity_and_budget():
    """2.12: a joint multi-strategy read gets each book's own equity and budget.

    Hand-computed with the BTCUSDT mark at 100:
      alpha: 1000 * 1 + 1 * 100 = 1100
      beta :  500 * 1 + 2 * 100 =  700
      unallocated: 1500 * 1      = 1500
      account aggregate: 3000 + 3 * 100 = 3300 = 1100 + 700 + 1500
    An open BUY on alpha reserves 100.1 USDT, so its budget shrinks while its
    equity (which values balances, not net-of-hold) stays 1100.
    """
    current, exchange, store = await _book_equity_service()
    await current.place(place_command("cmd-buy-a", book_id=BOOK_A, side="BUY"))

    snapshots = {item["book_id"]: item for item in await current.books(ACCOUNT)}
    assert set(snapshots) == {BOOK_A, BOOK_B, UNALLOCATED}

    alpha = snapshots[BOOK_A]
    assert alpha["balances"] == {BASE: "1", QUOTE: "1000"}
    assert alpha["positions"] == {}
    # book_snapshot serializes Decimals without normalizing ("100.100" == "100.1"),
    # so compare the values, not their text form.
    assert {asset: Decimal(value) for asset, value in alpha["held"].items()} == {
        BASE: Decimal("0"),
        QUOTE: Decimal("100.1"),
    }
    assert {asset: Decimal(value) for asset, value in alpha["available"].items()} == {
        BASE: Decimal("1"),
        QUOTE: Decimal("899.9"),
    }
    assert alpha["equity"] == "1100"
    assert alpha["valuation_complete"] is True
    assert alpha["valuations"][QUOTE] == {
        "price": "1",
        "at_ms": T0,
        "source": "QUOTE_UNIT",
    }
    assert alpha["valuations"][BASE] == {
        "price": "100",
        "at_ms": T0,
        "source": "MARK",
    }

    beta = snapshots[BOOK_B]
    assert beta["balances"] == {BASE: "2", QUOTE: "500"}
    assert beta["held"] == {BASE: "0", QUOTE: "0"}
    assert beta["available"] == {BASE: "2", QUOTE: "500"}
    assert beta["equity"] == "700"

    unallocated = snapshots[UNALLOCATED]
    assert unallocated["balances"][QUOTE] == "1500"
    assert unallocated["equity"] == "1500"

    # each book reports its own number; no book may return the aggregate
    equities = [alpha["equity"], beta["equity"], unallocated["equity"]]
    assert equities == ["1100", "700", "1500"]
    state = await store.read(ACCOUNT)
    account_equity = state.balances[QUOTE] + state.balances[BASE] * Decimal("100")
    assert account_equity == Decimal("3300")
    assert sum(Decimal(value) for value in equities) == account_equity
    assert book_ledger.conservation_mismatch(state) == {}


async def test_t9_per_book_equity_does_not_borrow_another_books_valuation():
    """2.12: a stale mark nulls only the book that needs it, not its siblings."""
    store = MemoryTradingStore()
    clock = FixedClock()
    current = build_service(
        store=store,
        clock=clock,
        policy=RiskPolicy(fee_rate=FEE_RATE, max_mark_age_ms=60_000),
    )
    await current.recover(venue_snapshot(balances={QUOTE: Decimal("1000"), BASE: Decimal("1")}))
    await allocate(current, "alloc-a", BOOK_A, 1000, asset=QUOTE)
    await allocate(current, "alloc-b", BOOK_B, 1, asset=BASE)

    clock.advance(60_001)  # the BTCUSDT mark is now 60_001 ms old
    snapshots = {item["book_id"]: item for item in await current.books(ACCOUNT)}

    # alpha holds only the quote asset, so it never needed a mark
    assert snapshots[BOOK_A]["balances"][QUOTE] == "1000"
    assert snapshots[BOOK_A]["equity"] == "1000"
    assert snapshots[BOOK_A]["valuation_complete"] is True
    # beta needs BTCUSDT: it must report no equity rather than reuse alpha's
    # valuation or the account-level mark
    assert snapshots[BOOK_B]["balances"][BASE] == "1"
    assert snapshots[BOOK_B]["equity"] is None
    assert snapshots[BOOK_B]["valuation_complete"] is False


# ================================================== G-constraint: 单一状态推进入口
def test_g_single_status_transition_entry_rejects_regression():
    order = _plain_order(status="PARTIALLY_FILLED")
    assert advance_order_status(order, "FILLED") is True
    assert advance_order_status(order, "OPEN") is False, "a terminal status cannot go backwards"
    assert order.status == "FILLED"


def _plain_order(status="PENDING_ACCEPTANCE"):
    from trader.trading.models import OrderAggregate

    return OrderAggregate(
        cl_ord_id="qts-plain",
        command=place_command("cmd-plain"),
        status=status,
    )
