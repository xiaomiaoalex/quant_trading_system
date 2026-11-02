"""Phase 2 independent PostgreSQL integration tests (authored by the independent tester).

These cases are the durability half of the phase: everything the in-memory
acceptance suite asserts must also hold when the account state is written to a
real database and read back by a different store object.

Isolation: every test creates its own throwaway schema
(``qts_trading_test_<uuid12>``) and points ``search_path`` at it, then drops it.
No default schema, no existing runtime or account data is touched.

The DSN is never written into this file: it comes from
``POSTGRES_CONNECTION_STRING`` or, failing that, from the DSN the existing suite
already uses. A required case that cannot reach PostgreSQL **fails**; it is
never skipped.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest

from trader.trading import books as book_ledger
from trader.trading.models import (
    Execution,
    NotReady,
    OrderObservation,
    PlaceOrderCommand,
    ReconcileSnapshot,
    TradingError,
)
from trader.trading.postgres import PostgresTradingStore
from trader.trading.risk import RiskPolicy, RiskRejected
from trader.trading.service import TradingService
from trader.trading.store import decode_state

# --------------------------------------------------------------------- baseline
ACCOUNT = "acct-indep-pg"
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


def _connection_string() -> str:
    """The DSN lives in the environment or in the existing suite, never here."""
    from_env = os.getenv("POSTGRES_CONNECTION_STRING")
    if from_env:
        return from_env
    try:
        existing = importlib.import_module("trader.tests.test_trading_postgres")
    except ImportError as exc:  # pragma: no cover - only if the suite is removed
        pytest.fail(f"no PostgreSQL DSN available: {exc}")
    return getattr(existing, "CONNECTION_STRING")


def _asyncpg():
    try:
        return importlib.import_module("asyncpg")
    except ImportError as exc:
        pytest.fail(f"asyncpg is required for this PostgreSQL case: {exc}")


async def _require_postgres() -> None:
    """Fail, never skip: a required durability case without a database is a failure."""
    asyncpg = _asyncpg()
    try:
        connection = await asyncpg.connect(_connection_string(), timeout=3)
    except Exception as exc:
        pytest.fail(f"PostgreSQL is required for this case but is unreachable: {exc}")
    await connection.close()


@asynccontextmanager
async def pg_store():
    """A store backed by a throwaway schema that is dropped in every path."""
    asyncpg = _asyncpg()
    schema = "qts_trading_test_" + uuid.uuid4().hex[:12]
    admin = await asyncpg.connect(_connection_string())
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    pool = None
    try:
        pool = await asyncpg.create_pool(
            _connection_string(),
            min_size=1,
            max_size=6,
            server_settings={"search_path": schema},
        )
        store = PostgresTradingStore(pool)
        await store.initialize()
        yield store, pool
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


class FixedClock:
    def __init__(self, now: int = T0) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


class RecordingExchange:
    def __init__(self) -> None:
        self.place_calls: list[str] = []

    async def place_order(self, command: PlaceOrderCommand, cl_ord_id: str):
        from trader.trading.models import ExchangeOrderResult, ExchangeOutcome

        self.place_calls.append(cl_ord_id)
        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-pg", "OPEN")

    async def cancel_order(self, account_id: str, cl_ord_id: str, command_id: str, symbol: str):
        from trader.trading.models import ExchangeOrderResult, ExchangeOutcome

        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, "b-pg", "OPEN")

    async def query_order(self, account_id: str, cl_ord_id: str, symbol: str):
        from trader.trading.models import ExchangeOrderResult, ExchangeOutcome

        return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)


def build_service(store, clock=None, exchange=None):
    return TradingService(
        store=store,
        exchange=exchange or RecordingExchange(),
        policy=RiskPolicy(fee_rate=FEE_RATE),
        clock=clock or FixedClock(),
    )


def venue_snapshot(balances=None):
    return ReconcileSnapshot(
        account_id=ACCOUNT,
        checked_at_ms=T0,
        generation=1,
        balances=(
            balances if balances is not None else {QUOTE: Decimal("1000"), BASE: Decimal("0")}
        ),
        marks={SYMBOL: {"price": Decimal("100"), "at_ms": T0}},
    )


def place_command(command_id="cmd-pg-1", *, book_id=BOOK_A, qty="1", price="100"):
    return PlaceOrderCommand(
        command_id=command_id,
        account_id=ACCOUNT,
        symbol=SYMBOL,
        base_asset=BASE,
        quote_asset=QUOTE,
        side="BUY",
        qty=Decimal(qty),
        price=Decimal(price),
        deployment_id="dep-indep-pg",
        decision_id=f"dec-{command_id}",
        order_type="LIMIT",
        time_in_force="GTC",
        book_id=book_id,
        price_source="SIGNAL_LIMIT",
        price_at_ms=T0,
    )


async def allocate(current, command_id, book_id, amount, reason="independent postgres test"):
    return await current.allocate(
        command_id,
        ACCOUNT,
        book_id,
        QUOTE,
        Decimal(str(amount)),
        actor="independent-tester",
        reason=reason,
    )


def _legacy_state():
    """A snapshot written before the book contract existed."""
    return decode_state(
        {
            "account_id": ACCOUNT,
            "status": "READY",
            "version": 2,
            "balances": {QUOTE: "1000", BASE: "0"},
            "positions": {},
            "external_locked": {},
            "orders": {
                "qts-pg-legacy": {
                    "cl_ord_id": "qts-pg-legacy",
                    "command": {
                        "command_id": "pg-legacy-command",
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
                        "price_source": "SIGNAL_LIMIT",
                        "price_at_ms": T0,
                    },
                    "status": "OPEN",
                    "broker_order_id": "b-pg-legacy",
                    "booked_qty": "0",
                    "booked_quote": "0",
                    "observed_qty": "0",
                    "observed_at_ms": 0,
                }
            },
            "commands": {
                "pg-legacy-command": {
                    "command_id": "pg-legacy-command",
                    "cl_ord_id": "qts-pg-legacy",
                    "kind": "PLACE",
                    "payload_hash": "pg-legacy-hash",
                    "status": "QUEUED",
                    "actor": "legacy",
                }
            },
            "holds": {"qts-pg-legacy": {"asset": QUOTE, "amount": "100.1"}},
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
                    "event_id": "pg-legacy-event-1",
                    "event_type": "order.queued",
                    "schema_version": 1,
                    "trace_id": "legacy",
                    "account_id": ACCOUNT,
                    "cl_ord_id": "qts-pg-legacy",
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
                "unresolved": [],
                "marks": {SYMBOL: {"price": "100", "at_ms": T0}},
            },
        }
    )


async def _seed_legacy(store):
    legacy = _legacy_state()
    async with store.transaction(ACCOUNT) as target:
        for name in _STATE_FIELDS:
            setattr(target, name, getattr(legacy, name))


# =================================================== 持久化: 重启后 book 事实仍在
async def test_pg_books_and_two_layer_holds_survive_a_restart():
    await _require_postgres()
    async with pg_store() as (store, pool):
        current = build_service(store)
        await current.recover(venue_snapshot())
        await allocate(current, "alloc-a", BOOK_A, 600)
        await allocate(current, "alloc-b", BOOK_B, 400)
        receipt = await current.place(place_command("cmd-pg-restart", qty="1", price="100"))
        await current.record_execution(
            Execution(
                ACCOUNT,
                receipt.cl_ord_id,
                SYMBOL,
                "exec-pg-1",
                Decimal("0.5"),
                Decimal("100"),
                Decimal("0.05"),
                QUOTE,
                T0 + 10,
            )
        )
        written = await store.read(ACCOUNT)

        # A brand-new store object over the same pool: nothing may be cached in memory.
        restarted = PostgresTradingStore(pool)
        again = await restarted.read(ACCOUNT)

        # USDT 1000 - 50 - 0.05 = 949.95; BTC 0.5; remaining hold 100.1 - 50.05 = 50.05
        assert again.balances[QUOTE] == Decimal("949.95")
        assert again.balances[BASE] == Decimal("0.5")
        assert again.books[BOOK_A].balances[QUOTE] == Decimal("549.95")
        assert again.books[BOOK_B].balances[QUOTE] == Decimal("400")
        assert again.books[UNALLOCATED].balances[QUOTE] == Decimal("0")
        assert again.holds[receipt.cl_ord_id].amount == Decimal("50.05")
        assert again.books[BOOK_A].holds[receipt.cl_ord_id].amount == Decimal("50.05")
        assert again.books[BOOK_A].balances[BASE] == Decimal("0.5")
        assert book_ledger.conservation_mismatch(again) == {}

        assert sorted(again.books) == sorted(written.books)
        assert sorted(again.orders) == sorted(written.orders)
        assert sorted(again.executions) == sorted(written.executions)
        assert len(again.ledger) == len(written.ledger)
        assert len(again.events) == len(written.events)
        assert again.orders[receipt.cl_ord_id].booked_qty == Decimal("0.5")


# ============================================ 持久化: 失败事务不留下任何投影行
async def test_pg_transaction_rolls_back_every_projection_on_book_hold_failure(monkeypatch):
    await _require_postgres()
    async with pg_store() as (store, pool):
        current = build_service(store)
        await current.recover(venue_snapshot())
        await allocate(current, "alloc-a", BOOK_A, 1000)

        before = await store.read(ACCOUNT)
        ledger_rows_before = await pool.fetchval(
            "SELECT count(*) FROM trading_ledger WHERE account_id=$1", ACCOUNT
        )
        event_rows_before = await pool.fetchval(
            "SELECT count(*) FROM trading_events WHERE account_id=$1", ACCOUNT
        )

        def refuse_book_hold(state, book_id, cl_ord_id, hold):
            raise TradingError("INJECTED_PG_BOOK_HOLD_FAILURE")

        monkeypatch.setattr(book_ledger, "reserve_book_hold", refuse_book_hold)

        with pytest.raises(TradingError) as exc:
            await current.place(place_command("cmd-pg-atomic", qty="2", price="100"))
        assert "INJECTED_PG_BOOK_HOLD_FAILURE" in str(exc.value)

        after = await store.read(ACCOUNT)
        assert after.balances == before.balances
        assert after.books == before.books
        assert after.holds == before.holds
        assert after.orders == before.orders
        assert after.version == before.version

        ledger_rows_after = await pool.fetchval(
            "SELECT count(*) FROM trading_ledger WHERE account_id=$1", ACCOUNT
        )
        event_rows_after = await pool.fetchval(
            "SELECT count(*) FROM trading_events WHERE account_id=$1", ACCOUNT
        )
        assert ledger_rows_after == ledger_rows_before, "no orphan ledger row may survive"
        assert event_rows_after == event_rows_before, "no orphan event row may survive"
        order_rows_after = await pool.fetchval(
            "SELECT count(*) FROM trading_order_projection WHERE account_id=$1", ACCOUNT
        )
        assert order_rows_after == 0, "no order projection row may survive a failed reservation"
        command_rows_after = await pool.fetchval(
            "SELECT count(*) FROM trading_commands WHERE account_id=$1", ACCOUNT
        )
        assert command_rows_after == 1, "only the committed allocation command may exist"


# ================================================== 持久化: 并发写入不丢更新
async def test_pg_concurrent_allocations_do_not_lose_an_update():
    await _require_postgres()
    async with pg_store() as (store, pool):
        current = build_service(store)
        await current.recover(venue_snapshot())

        results = await asyncio.gather(
            allocate(current, "alloc-c1", BOOK_A, 300),
            allocate(current, "alloc-c2", BOOK_B, 200),
            return_exceptions=True,
        )
        assert all(not isinstance(item, BaseException) for item in results), results

        state = await store.read(ACCOUNT)
        assert state.books[BOOK_A].balances[QUOTE] == Decimal("300")
        assert state.books[BOOK_B].balances[QUOTE] == Decimal("200")
        assert state.books[UNALLOCATED].balances[QUOTE] == Decimal("500")
        assert state.balances[QUOTE] == Decimal("1000")
        assert book_ledger.conservation_mismatch(state) == {}
        assert state.version >= 3, "every committed transaction must bump the version"
        assert sorted(state.commands) == ["alloc-c1", "alloc-c2"]


async def test_pg_concurrent_places_cannot_double_spend_the_same_book():
    await _require_postgres()
    async with pg_store() as (store, pool):
        current = build_service(store)
        await current.recover(venue_snapshot())
        await allocate(current, "alloc-a", BOOK_A, 100)

        async def attempt(command_id):
            return await current.place(place_command(command_id, qty="0.9", price="100"))

        results = await asyncio.gather(
            attempt("cmd-pg-c1"), attempt("cmd-pg-c2"), return_exceptions=True
        )
        admitted = [item for item in results if not isinstance(item, BaseException)]
        refused = [item for item in results if isinstance(item, RiskRejected)]
        assert len(admitted) == 1, results
        assert len(refused) == 1, results
        assert str(refused[0]) == "BOOK_INSUFFICIENT_AVAILABLE_BALANCE"

        state = await store.read(ACCOUNT)
        assert state.books[BOOK_A].balances[QUOTE] == Decimal("100")
        held = sum(hold.amount for hold in state.books[BOOK_A].holds.values())
        assert held == Decimal("90.09"), "only one 90.09 reservation may exist"
        assert sum(hold.amount for hold in state.holds.values()) == Decimal("90.09")
        assert book_ledger.conservation_mismatch(state) == {}


# ============================================ 持久化: 迁移幂等且不改写 append-only 事实
async def test_pg_legacy_row_migration_is_idempotent_and_keeps_append_only_facts():
    await _require_postgres()
    async with pg_store() as (store, pool):
        await _seed_legacy(store)

        first = await store.read(ACCOUNT)
        assert list(first.books) == [UNALLOCATED], "no strategy book may be invented"
        assert first.books[UNALLOCATED].balances[QUOTE] == Decimal("1000")
        assert [entry["entry_id"] for entry in first.ledger] == ["opening:USDT"]
        assert [entry["event_id"] for entry in first.events] == ["pg-legacy-event-1"]
        assert first.orders["qts-pg-legacy"].command.book_id == ""
        assert first.holds["qts-pg-legacy"].amount == Decimal("100.1")
        marker = first.reconciliation[book_ledger.BOOK_LEDGER_KEY]
        assert "qts-pg-legacy" in marker["unattributed_orders"]

        # A second read is a second decode: migration must be idempotent.
        second = await store.read(ACCOUNT)
        assert sorted(second.books) == sorted(first.books)
        assert second.books[UNALLOCATED].balances == first.books[UNALLOCATED].balances
        assert len(second.ledger) == len(first.ledger)
        assert len(second.events) == len(first.events)
        assert (
            second.reconciliation[book_ledger.BOOK_LEDGER_KEY]["unattributed_orders"]
            == marker["unattributed_orders"]
        )

        # A service write path persists the migration without rewriting old facts.
        current = build_service(store)
        await current.observe(
            OrderObservation(ACCOUNT, "qts-pg-legacy", "OPEN", Decimal("0"), observed_at_ms=T0)
        )
        third = await store.read(ACCOUNT)
        assert list(third.books) == [UNALLOCATED]
        assert third.books[UNALLOCATED].balances[QUOTE] == Decimal("1000")
        assert [entry["entry_id"] for entry in third.ledger] == ["opening:USDT"]
        assert "pg-legacy-event-1" in {entry["event_id"] for entry in third.events}
        assert "BOOK_ATTRIBUTION_UNKNOWN" in book_ledger.book_reasons(third)
        assert book_ledger.conservation_mismatch(third) == {}

        with pytest.raises(NotReady):
            await current.place(place_command("cmd-pg-blocked", qty="1", price="100"))
