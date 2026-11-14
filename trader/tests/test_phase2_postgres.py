"""Real PostgreSQL acceptance for phase 2 books, allocation and migration."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from trader.tests.test_phase2_foundation_safety import ACCOUNT_ID, BOOK_A, BOOK_B, venue_snapshot
from trader.tests.test_trading_postgres import _can_connect, pg_store, service
from trader.tests.test_trading_service import Clock, command, fund_book
from trader.trading import books as book_ledger
from trader.trading import core as trading_core
from trader.trading.models import (
    AccountState,
    Execution,
    Hold,
    OrderAggregate,
    PlaceOrderCommand,
    TradingError,
    json_value,
)
from trader.trading.service import TradingService


async def _require_postgres():
    if not await _can_connect():
        pytest.skip("PostgreSQL not available")


def _legacy_state() -> AccountState:
    state = AccountState(
        account_id=ACCOUNT_ID,
        status="READY",
        balances={"USDT": Decimal("1000"), "BTC": Decimal("0")},
    )
    state.orders["qts-legacy"] = OrderAggregate(
        cl_ord_id="qts-legacy",
        command=PlaceOrderCommand(
            command_id="legacy-command",
            account_id=ACCOUNT_ID,
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            side="BUY",
            qty=Decimal("1"),
            price=Decimal("100"),
            deployment_id="legacy",
            order_type="LIMIT",
            decision_id="legacy",
        ),
        status="OPEN",
    )
    state.holds["qts-legacy"] = Hold("USDT", Decimal("100.1"))
    state.ledger = [
        {
            "entry_id": "opening:USDT",
            "execution_key": "",
            "account_id": ACCOUNT_ID,
            "cl_ord_id": "",
            "asset": "USDT",
            "delta": "1000",
            "kind": "opening_balance",
            "occurred_at_ms": 1_000_000,
        }
    ]
    state.events = [
        {
            "event_id": "legacy-event-1",
            "event_type": "order.queued",
            "schema_version": 1,
            "trace_id": "legacy",
            "account_id": ACCOUNT_ID,
            "cl_ord_id": "qts-legacy",
            "occurred_at_ms": 1_000_000,
            "source": "api",
            "payload": {"book_id": ""},
        }
    ]
    state.reconciliation = {
        "checked_at_ms": 1_000_000,
        "generation": 1,
        "stream_healthy": True,
        "marks": {"BTCUSDT": {"price": Decimal("100"), "at_ms": 1_000_000}},
    }
    return state


@pytest.mark.asyncio
async def test_postgres_books_allocation_and_order_survive_restart():
    await _require_postgres()
    async with pg_store() as (store, pool):
        current = service(store)
        await current.recover(venue_snapshot())
        first = await current.allocate(
            "alloc-pg-a", ACCOUNT_ID, BOOK_A, "USDT", Decimal("600"), reason="pg a"
        )
        second = await current.allocate(
            "alloc-pg-b", ACCOUNT_ID, BOOK_B, "USDT", Decimal("400"), reason="pg b"
        )
        assert first.status == second.status == "CONFIRMED"
        receipt = await current.place(
            command(book_id=BOOK_A, qty=Decimal("2"), price=Decimal("100"))
        )
        await current.record_execution(
            Execution(
                ACCOUNT_ID,
                receipt.cl_ord_id,
                "BTCUSDT",
                "pg-fill-1",
                Decimal("1"),
                Decimal("100"),
                Decimal("0.1"),
                "USDT",
                1_000_100,
            )
        )

        restarted = service(store)
        state = await restarted.store.read(ACCOUNT_ID)
        assert state.orders[receipt.cl_ord_id].command.book_id == BOOK_A
        assert state.books[BOOK_A].balances["USDT"] == Decimal("499.9")
        assert state.books[BOOK_B].balances["USDT"] == Decimal("400")
        assert state.books[book_ledger.UNALLOCATED].balances["USDT"] == Decimal("0")
        assert state.books[BOOK_A].holds[receipt.cl_ord_id].amount == Decimal("100.1")
        assert state.holds[receipt.cl_ord_id].amount == Decimal("100.1")
        assert book_ledger.conservation_mismatch(state) == {}

        books = {item["book_id"]: item for item in await restarted.books(ACCOUNT_ID)}
        assert books[BOOK_A]["positions"]["BTCUSDT"] == "1"
        assert books[BOOK_A]["equity"] == "599.9"
        assert books[BOOK_B]["equity"] == "400"

        async with pool.acquire() as conn:
            raw = json.loads(await conn.fetchval("SELECT state FROM trading_accounts"))
            assert "books" in raw and BOOK_A in raw["books"]
            ledger_books = await conn.fetch(
                "SELECT data FROM trading_ledger WHERE account_id=$1 ORDER BY entry_id",
                ACCOUNT_ID,
            )
            assert any(
                (json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]).get(
                    "book_id"
                )
                == BOOK_A
                for row in ledger_books
            )
            events = await conn.fetch(
                "SELECT data FROM trading_events WHERE account_id=$1", ACCOUNT_ID
            )
            booked = [
                json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
                for row in events
            ]
            assert any(
                item["event_type"] == "execution.booked"
                and item["payload"].get("book_id") == BOOK_A
                for item in booked
            )

        # Rebuilding disposable query projections must not change book
        # economics or the deterministic per-book query.
        books_before = await restarted.books(ACCOUNT_ID)
        assert await store.rebuild_query_projections(ACCOUNT_ID) == {"commands": 3, "orders": 1}
        assert await restarted.books(ACCOUNT_ID) == books_before
        after_projection = await store.read(ACCOUNT_ID)
        assert after_projection.books[BOOK_A].balances == state.books[BOOK_A].balances
        assert book_ledger.conservation_mismatch(after_projection) == {}


@pytest.mark.asyncio
async def test_postgres_allocation_commands_are_idempotent_and_never_overdraw():
    await _require_postgres()
    async with pg_store() as (store, pool):
        current = service(store)
        await current.recover(venue_snapshot())
        control = TradingService(store, current.exchange, clock=current.clock, defer_send=True)
        execution = service(store)
        await control.allocate("alloc-c1", ACCOUNT_ID, BOOK_A, "USDT", Decimal("600"), reason="a")
        await control.allocate("alloc-c2", ACCOUNT_ID, BOOK_B, "USDT", Decimal("600"), reason="b")
        await execution.resume_queued(ACCOUNT_ID)
        state = await store.read(ACCOUNT_ID)
        statuses = sorted(
            record.status for record in state.commands.values() if record.kind == "ALLOCATE"
        )
        assert statuses == ["CONFIRMED", "FAILED"]
        assert state.books[book_ledger.UNALLOCATED].balances["USDT"] == Decimal("400")
        assert book_ledger.conservation_mismatch(state) == {}
        assert await execution.resume_queued(ACCOUNT_ID) == []
        restarted = service(store)
        again = await restarted.store.read(ACCOUNT_ID)
        assert again.books[book_ledger.UNALLOCATED].balances["USDT"] == Decimal("400")


@pytest.mark.asyncio
async def test_postgres_two_layer_reservation_and_execution_roll_back_atomically(monkeypatch):
    await _require_postgres()
    async with pg_store() as (store, pool):
        current = service(store)
        await current.recover(venue_snapshot())
        await fund_book(current, BOOK_A, {"USDT": Decimal("1000")})

        def explode_reserve(*args, **kwargs):
            raise RuntimeError("book reservation unavailable")

        monkeypatch.setattr(book_ledger, "reserve_book_hold", explode_reserve)
        with pytest.raises(RuntimeError):
            await current.place(command(book_id=BOOK_A))
        monkeypatch.undo()
        state = await store.read(ACCOUNT_ID)
        assert state.orders == {} and state.holds == {}
        assert state.books[BOOK_A].holds == {}
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM trading_commands") == 0
            assert await conn.fetchval("SELECT count(*) FROM trading_order_projection") == 0

        receipt = await current.place(
            command(book_id=BOOK_A, qty=Decimal("1"), price=Decimal("100"))
        )
        state = await store.read(ACCOUNT_ID)
        assert state.holds[receipt.cl_ord_id].amount == Decimal("100.100")
        assert state.books[BOOK_A].holds[receipt.cl_ord_id].amount == Decimal("100.100")

        def explode_position(*args, **kwargs):
            raise RuntimeError("book position unavailable")

        monkeypatch.setattr(trading_core, "apply_position", explode_position)
        with pytest.raises(RuntimeError):
            await current.record_execution(
                Execution(
                    ACCOUNT_ID,
                    receipt.cl_ord_id,
                    "BTCUSDT",
                    "pg-rollback",
                    Decimal("1"),
                    Decimal("100"),
                    Decimal("0.1"),
                    "USDT",
                    1_000_100,
                )
            )
        monkeypatch.undo()
        state = await store.read(ACCOUNT_ID)
        assert state.executions == {}
        assert state.balances == {"USDT": Decimal("1000"), "BTC": Decimal("0")}
        assert state.books[BOOK_A].balances == {"USDT": Decimal("1000")}
        assert state.books[BOOK_A].positions == {}
        assert receipt.cl_ord_id in state.holds
        assert receipt.cl_ord_id in state.books[BOOK_A].holds
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM trading_executions") == 0


@pytest.mark.asyncio
async def test_postgres_legacy_state_migrates_without_rewriting_history():
    await _require_postgres()
    async with pg_store() as (store, pool):
        legacy = _legacy_state()
        encoded = json_value(legacy)
        encoded.pop("books", None)
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO trading_accounts(account_id,version,state) VALUES($1,$2,$3::jsonb)",
                ACCOUNT_ID,
                0,
                json.dumps(encoded),
            )
            await conn.execute(
                "INSERT INTO trading_ledger(account_id,entry_id,data) VALUES($1,$2,$3::jsonb)",
                ACCOUNT_ID,
                "opening:USDT",
                json.dumps(legacy.ledger[0]),
            )
            await conn.execute(
                "INSERT INTO trading_events(account_id,event_id,data) VALUES($1,$2,$3::jsonb)",
                ACCOUNT_ID,
                "legacy-event-1",
                json.dumps(legacy.events[0]),
            )
            ledger_before = await conn.fetch("SELECT * FROM trading_ledger ORDER BY entry_id")
            events_before = await conn.fetch("SELECT * FROM trading_events ORDER BY event_id")

        migrated = await store.read(ACCOUNT_ID)
        assert migrated.balances == legacy.balances
        assert migrated.ledger == legacy.ledger
        assert migrated.events == legacy.events
        assert migrated.holds.keys() == legacy.holds.keys()
        assert migrated.books[book_ledger.UNALLOCATED].balances == legacy.balances
        assert migrated.orders["qts-legacy"].command.book_id == ""
        assert migrated.reconciliation[book_ledger.BOOK_LEDGER_KEY]["unattributed_orders"]

        # Persist the migration explicitly; old ledger/event/order facts remain.
        async with store.transaction(ACCOUNT_ID) as state:
            book_ledger.migrate_book_ledger(state)
        persisted = await store.read(ACCOUNT_ID)
        assert persisted.balances == legacy.balances
        assert persisted.ledger == legacy.ledger
        assert persisted.events == legacy.events
        assert persisted.books[book_ledger.UNALLOCATED].balances == legacy.balances
        async with pool.acquire() as conn:
            assert (
                await conn.fetch("SELECT * FROM trading_ledger ORDER BY entry_id") == ledger_before
            )
            assert (
                await conn.fetch("SELECT * FROM trading_events ORDER BY event_id") == events_before
            )

        # A second migration pass is a no-op for the partition and facts.
        async with store.transaction(ACCOUNT_ID) as state:
            book_ledger.migrate_book_ledger(state)
        again = await store.read(ACCOUNT_ID)
        assert again.books[book_ledger.UNALLOCATED].balances == legacy.balances
        assert again.orders["qts-legacy"].command == legacy.orders["qts-legacy"].command


@pytest.mark.asyncio
async def test_postgres_unknown_book_fill_blocks_and_evidence_persists_across_restart():
    await _require_postgres()
    async with pg_store() as (store, pool):
        legacy = _legacy_state()
        encoded = json_value(legacy)
        encoded.pop("books", None)
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO trading_accounts(account_id,version,state) VALUES($1,$2,$3::jsonb)",
                ACCOUNT_ID,
                0,
                json.dumps(encoded),
            )

        current = service(store)
        view = await current.risk_view(ACCOUNT_ID)
        assert view.ready is False
        assert "BOOK_ATTRIBUTION_UNKNOWN" in view.reasons
        await current.record_execution(
            Execution(
                ACCOUNT_ID,
                "qts-legacy",
                "BTCUSDT",
                "legacy-pg-fill",
                Decimal("1"),
                Decimal("100"),
                Decimal("0.1"),
                "USDT",
                1_000_100,
            )
        )

        restarted = service(store)
        state = await restarted.store.read(ACCOUNT_ID)
        assert state.books[book_ledger.UNALLOCATED].balances["USDT"] == Decimal("899.9")
        assert all(
            book.balances == {}
            for book_id, book in state.books.items()
            if book_id != book_ledger.UNALLOCATED
        )
        assert book_ledger.conservation_mismatch(state) == {}
        assert "BOOK_ATTRIBUTION_UNKNOWN" in (await restarted.risk_view(ACCOUNT_ID)).reasons

        settled = await restarted.resolve(
            ACCOUNT_ID,
            "BOOK_ATTRIBUTION_UNKNOWN",
            actor="owner",
            evidence={
                "kind": "operator_attribution",
                "reference": "pg-ticket-1",
                "reason": "operator verified the legacy fill belongs to the unallocated bucket",
                "orders": {"qts-legacy": book_ledger.UNALLOCATED},
            },
        )
        assert settled.ready is True
        after = await service(store).store.read(ACCOUNT_ID)
        assert after.reconciliation[book_ledger.BOOK_LEDGER_KEY]["unattributed_orders"] == {}
        assert "qts-legacy" in after.reconciliation[book_ledger.BOOK_LEDGER_KEY]["resolved_orders"]
