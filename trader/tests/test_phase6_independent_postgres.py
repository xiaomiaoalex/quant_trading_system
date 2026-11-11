"""Phase-6 independent acceptance tests - real PostgreSQL half.

Requires an explicit DSN (docs/INTERFACE_CONTRACTS.md 15.1):

    POSTGRES_CONNECTION_STRING=postgresql://... python -m pytest \\
        trader/tests/test_phase6_independent_postgres.py

Every test creates its own schema ``qts_p6ind_<random>`` and drops it on exit.
Nothing here reads or writes the default schema, a running runtime, a
container or an account holding real funds.

Covered here:

* 6.2 durable pre-snapshot ALLOCATE: queued by the control process, kept
  QUEUED across a process restart until reconciliation exists, then applied
  exactly once with the account total unchanged;
* 6.4/6.5 execution booking side effects: one ``trading_executions`` row, one
  cash and one asset ledger entry, one ``execution.booked`` event for a fill
  replay across restarts, with no duplicate rows;
* 6.7 killswitch durability: the block survives a new service/store instance
  and still refuses new risk without calling the venue;
* 6.9-6.12 legacy tables are never written: representative legacy tables are
  created in the task schema with BEFORE-write triggers that raise, so any
  attempt to touch them aborts the transaction instead of passing silently.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest

CONNECTION_STRING = os.getenv("POSTGRES_CONNECTION_STRING", "").strip()

ACCOUNT = "p6ind_pg_acct"
BOOK = "book:phase6-pg"
NOW = 1_000_000_000_000

LEGACY_TABLES = (
    "positions_proj",
    "orders_proj",
    "risk_states_proj",
    "positions_snapshots",
    "orders_snapshots",
    "risk_snapshots",
    "executions",
    "strategies",
    "strategy_positions_proj",
    "account_positions_proj",
    "position_lots",
    "reconciliation_log",
    "strategy_position_snapshots",
    "killswitch_log",
    "strategy_runtime_states",
    "risk_audit_events",
)


def _asyncpg():
    import importlib

    return importlib.import_module("asyncpg")


async def _can_connect() -> bool:
    if not CONNECTION_STRING:
        return False
    try:
        asyncpg = _asyncpg()
        connection = await asyncpg.connect(CONNECTION_STRING, timeout=3)
        await connection.close()
        return True
    except Exception:
        return False


@asynccontextmanager
async def task_schema():
    """A unique schema owned by this test, dropped on exit."""
    try:
        asyncpg = _asyncpg()
    except ImportError:
        pytest.skip("asyncpg is not installed")
    if not await _can_connect():
        pytest.skip("POSTGRES_CONNECTION_STRING is not set or the server is unreachable")
    schema = "qts_p6ind_" + uuid.uuid4().hex[:12]
    admin = await asyncpg.connect(CONNECTION_STRING)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    pool = await asyncpg.create_pool(
        CONNECTION_STRING, min_size=1, max_size=4, server_settings={"search_path": schema}
    )
    try:
        yield schema, pool
    finally:
        await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


class CountingVenue:
    def __init__(self, outcome: str = "ACCEPTED") -> None:
        self.outcome = outcome
        self.sent: list[str] = []

    async def place_order(self, command, cl_ord_id):
        from trader.trading.models import ExchangeOrderResult, ExchangeOutcome

        self.sent.append(cl_ord_id)
        return ExchangeOrderResult(ExchangeOutcome[self.outcome], f"broker-{cl_ord_id}", "OPEN")

    async def cancel_order(self, account_id, cl_ord_id, command_id, symbol):
        from trader.trading.models import ExchangeOrderResult, ExchangeOutcome

        return ExchangeOrderResult(ExchangeOutcome.ACCEPTED, f"broker-{cl_ord_id}", "CANCELLED")

    async def query_order(self, account_id, cl_ord_id, symbol):
        from trader.trading.models import ExchangeOrderResult, ExchangeOutcome

        return ExchangeOrderResult(ExchangeOutcome.NOT_FOUND)


def _snapshot(*, at_ms: int = NOW, balances=None):
    from trader.trading.models import ReconcileSnapshot

    return ReconcileSnapshot(
        account_id=ACCOUNT,
        checked_at_ms=at_ms,
        generation=1,
        balances=balances or {"USDT": Decimal("1000"), "BTC": Decimal("0.5")},
        marks={"BTCUSDT": {"price": Decimal("200"), "at_ms": at_ms}},
    )


def _outgoing_order(command_id: str):
    from trader.trading.order_spec import build_order_spec

    spec = build_order_spec(
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        qty=Decimal("1"),
        limit_price=Decimal("200"),
        reference_price=Decimal("200"),
        reference_price_source="SIGNAL_LIMIT",
        reference_at_ms=NOW,
        decision_at_ms=NOW,
        time_in_force="GTC",
        book_id=BOOK,
        price_source="SIGNAL_LIMIT",
    )
    return spec.to_command(
        command_id=command_id,
        account_id=ACCOUNT,
        base_asset="BTC",
        quote_asset="USDT",
        deployment_id="dep-p6ind-pg",
        decision_id=f"decision-{command_id}",
    )


def _fill(cl_ord_id: str, exec_id: str = "exec-p6ind-1"):
    from trader.trading.models import Execution

    return Execution(
        account_id=ACCOUNT,
        cl_ord_id=cl_ord_id,
        symbol="BTCUSDT",
        exec_id=exec_id,
        qty=Decimal("0.25"),
        price=Decimal("200"),
        fee=Decimal("0"),
        occurred_at_ms=NOW + 1_000,
    )


async def _store(pool):
    from trader.trading.postgres import PostgresTradingStore

    store = PostgresTradingStore(pool)
    await store.initialize()
    return store


async def _service(store, venue, *, defer_send: bool = False):
    from trader.trading.risk import RiskPolicy
    from trader.trading.service import TradingService

    return TradingService(store, venue, RiskPolicy(), clock=lambda: NOW, defer_send=defer_send)


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


async def _seed_legacy_tables(pool) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE OR REPLACE FUNCTION p6ind_legacy_write_guard() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'LEGACY_TABLE_WRITE:%', TG_TABLE_NAME;
            END
            $$;
            """
        )
        for name in LEGACY_TABLES:
            await connection.execute(f'CREATE TABLE "{name}" (id text PRIMARY KEY, data jsonb)')
            await connection.execute(
                f'INSERT INTO "{name}"(id, data) VALUES($1, $2::jsonb)',
                "sentinel",
                json.dumps({}),
            )
            await connection.execute(
                f'CREATE TRIGGER "{name}_write_guard" BEFORE INSERT OR UPDATE OR DELETE '
                f'ON "{name}" FOR EACH ROW EXECUTE FUNCTION p6ind_legacy_write_guard()'
            )


async def _assert_legacy_untouched(pool) -> None:
    async with pool.acquire() as connection:
        for name in LEGACY_TABLES:
            rows = await connection.fetch(f'SELECT id, data FROM "{name}"')
            found = [(row["id"], _json(row["data"])) for row in rows]
            assert found == [("sentinel", {})], name


def test_queued_allocation_survives_restart_until_a_snapshot_exists() -> None:
    async def run() -> None:
        async with task_schema() as (schema, pool):
            store = await _store(pool)
            control = await _service(store, CountingVenue(), defer_send=True)
            queued = await control.allocate(
                "p6ind-alloc-1", ACCOUNT, BOOK, "USDT", Decimal("400"), reason="pg drill"
            )
            assert queued.status == "QUEUED"

            # Process restart: a brand-new store/service over the same database.
            executor = await _service(await _store(pool), CountingVenue())
            before = await executor.resume_queued(ACCOUNT)
            assert [item.status for item in before if item.kind == "ALLOCATE"] == ["QUEUED"]
            state = await executor.store.read(ACCOUNT)
            assert state.reconciliation.get("checked_at_ms") is None
            assert state.books.get(BOOK) is None

            await executor.recover(_snapshot())
            # The first authoritative snapshot books its own opening evidence;
            # ALLOCATE is an internal transfer and must add nothing to it.
            opening_ledger = await pool.fetchval(
                "SELECT count(*) FROM trading_ledger WHERE account_id=$1", ACCOUNT
            )
            assert opening_ledger == 2, "opening USDT and BTC evidence"
            after = await executor.resume_queued(ACCOUNT)
            assert [item.status for item in after if item.kind == "ALLOCATE"] == ["CONFIRMED"]

            row = await pool.fetchrow(
                "SELECT status, data FROM trading_commands WHERE account_id=$1 AND command_id=$2",
                ACCOUNT,
                "p6ind-alloc-1",
            )
            assert row["status"] == "CONFIRMED"
            assert _json(row["data"])["status"] == "CONFIRMED"

            state = await executor.store.read(ACCOUNT)
            assert state.books[BOOK].balances["USDT"] == Decimal("400")
            assert state.books["UNALLOCATED"].balances["USDT"] == Decimal("600")
            assert state.balances["USDT"] == Decimal("1000")

            # The database itself is the only source of the applied fact.
            accounts = await pool.fetchval(
                "SELECT count(*) FROM trading_accounts WHERE account_id=$1", ACCOUNT
            )
            assert accounts == 1
            ledger_rows = await pool.fetchval(
                "SELECT count(*) FROM trading_ledger WHERE account_id=$1", ACCOUNT
            )
            assert ledger_rows == opening_ledger, "ALLOCATE adds no venue ledger entry"

    asyncio.run(run())


def test_fill_side_effects_are_booked_exactly_once_across_restarts() -> None:
    async def run() -> None:
        async with task_schema() as (schema, pool):
            venue = CountingVenue()
            store = await _store(pool)
            service = await _service(store, venue)
            await service.recover(_snapshot())
            opening_ledger = await pool.fetchval(
                "SELECT count(*) FROM trading_ledger WHERE account_id=$1", ACCOUNT
            )
            assert opening_ledger == 2, "opening USDT and BTC evidence"
            await service.allocate(
                "p6ind-alloc-boot", ACCOUNT, BOOK, "USDT", Decimal("400"), reason="fill drill"
            )
            receipt = await service.place(_outgoing_order("p6ind-place-1"))
            assert receipt.status == "CONFIRMED" and venue.sent == [receipt.cl_ord_id]

            fill = _fill(receipt.cl_ord_id)
            assert await service.record_execution(fill) is True
            assert await service.record_execution(fill) is False

            # A restarted owner replaying the same venue fill must not rebook it.
            restarted = await _service(await _store(pool), venue)
            assert await restarted.record_execution(fill) is False

            executions = await pool.fetchval(
                "SELECT count(*) FROM trading_executions WHERE account_id=$1", ACCOUNT
            )
            assert executions == 1
            ledger = await pool.fetchval(
                "SELECT count(*) FROM trading_ledger WHERE account_id=$1", ACCOUNT
            )
            # 0.25 BTC at 200 = 50 USDT: one cash decrease and one asset increase.
            assert ledger == opening_ledger + 2
            booked_events = await pool.fetchval(
                "SELECT count(*) FROM trading_events "
                "WHERE account_id=$1 AND data->>'event_type'='execution.booked'",
                ACCOUNT,
            )
            assert booked_events == 1

            state = await restarted.store.read(ACCOUNT)
            assert state.balances["USDT"] == Decimal("950")
            assert state.balances["BTC"] == Decimal("0.75")
            assert state.books[BOOK].balances["USDT"] == Decimal("350")
            assert len(state.executions) == 1

    asyncio.run(run())


def test_killswitch_block_survives_restart_and_still_refuses_new_risk() -> None:
    async def run() -> None:
        async with task_schema() as (schema, pool):
            venue = CountingVenue()
            service = await _service(await _store(pool), venue)
            await service.recover(_snapshot())
            await service.allocate(
                "p6ind-alloc-ks", ACCOUNT, BOOK, "USDT", Decimal("400"), reason="killswitch drill"
            )

            blocked = await service.control(
                "p6ind-ks-1",
                ACCOUNT,
                "BLOCK_NEW_RISK",
                actor="tester",
                evidence={"reason": "independent pg drill"},
            )
            assert blocked.status == "CONFIRMED"

            restarted = await _service(await _store(pool), venue)
            state = await restarted.store.read(ACCOUNT)
            assert state.control["new_risk_blocked"] is True

            from trader.trading.models import NotReady

            with pytest.raises(NotReady, match="NEW_RISK_BLOCKED"):
                await restarted.place(_outgoing_order("p6ind-place-blocked"))
            assert venue.sent == []

            resumed = await restarted.control(
                "p6ind-ks-2", ACCOUNT, "RESUME_NEW_RISK", actor="tester"
            )
            assert resumed.status == "CONFIRMED"
            assert (await restarted.store.read(ACCOUNT)).control["new_risk_blocked"] is False

    asyncio.run(run())


def test_no_flow_writes_a_legacy_table() -> None:
    async def run() -> None:
        async with task_schema() as (schema, pool):
            store = await _store(pool)
            await _seed_legacy_tables(pool)

            venue = CountingVenue()
            service = await _service(store, venue)
            await service.recover(_snapshot())
            await service.allocate(
                "p6ind-alloc-legacy", ACCOUNT, BOOK, "USDT", Decimal("400"), reason="legacy drill"
            )
            receipt = await service.place(_outgoing_order("p6ind-place-legacy"))
            assert await service.record_execution(_fill(receipt.cl_ord_id)) is True
            await service.control("p6ind-ks-legacy", ACCOUNT, "BLOCK_NEW_RISK", actor="tester")
            await service.control(
                "p6ind-ks-legacy-resume", ACCOUNT, "RESUME_NEW_RISK", actor="tester"
            )

            await _assert_legacy_untouched(pool)
            # The retained legacy tables remain readable (read-only retention).
            async with pool.acquire() as connection:
                assert (
                    await connection.fetchval("SELECT count(*) FROM strategy_runtime_states") == 1
                )

    asyncio.run(run())
