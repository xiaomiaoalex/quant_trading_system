"""Real PG rehearsal: derived query rows can be lost without losing economics."""

import json
from decimal import Decimal

import pytest

from trader.tests.test_trading_postgres import pg_store, service
from trader.tests.test_trading_service import bootstrap, command
from trader.trading.models import Execution


@pytest.mark.asyncio
async def test_deleted_query_projections_rebuild_without_rebooking_or_renotifying():
    async with pg_store() as (store, pool):
        current = service(store)
        await bootstrap(current)
        receipt = await current.place(command())
        await current.record_execution(
            Execution(
                "acct",
                receipt.cl_ord_id,
                "BTCUSDT",
                "exec-1",
                Decimal("0.5"),
                Decimal("100"),
                Decimal("0.05"),
                "USDT",
            )
        )
        delivered = (await store.pending_outbox("acct"))[0]["event_id"]
        await store.mark_outbox_delivered("acct", [delivered])
        before = await store.read("acct")
        async with pool.acquire() as conn:
            commands = await conn.fetch("SELECT * FROM trading_commands ORDER BY command_id")
            orders = await conn.fetch("SELECT * FROM trading_order_projection ORDER BY cl_ord_id")
            outbox = await conn.fetch("SELECT * FROM trading_outbox ORDER BY seq")
            await conn.execute("DELETE FROM trading_commands")
            await conn.execute("DELETE FROM trading_order_projection")
        assert await store.rebuild_query_projections("acct") == {"commands": 1, "orders": 1}
        assert await store.read("acct") == before
        async with pool.acquire() as conn:
            assert (
                await conn.fetch("SELECT * FROM trading_commands ORDER BY command_id") == commands
            )
            assert (
                await conn.fetch("SELECT * FROM trading_order_projection ORDER BY cl_ord_id")
                == orders
            )
            assert await conn.fetch("SELECT * FROM trading_outbox ORDER BY seq") == outbox
            assert await conn.fetchval("SELECT count(*) FROM trading_executions") == 1
            # One opening balance plus the three legs of a single fill.
            ledger_kinds = [
                json.loads(row["data"])["kind"]
                for row in await conn.fetch("SELECT data FROM trading_ledger")
            ]
            assert sorted(ledger_kinds) == ["fee", "opening_balance", "trade", "trade"]
        assert await store.rebuild_query_projections("acct") == {"commands": 1, "orders": 1}


@pytest.mark.asyncio
async def test_schema_version_is_recorded_once_and_reinitialization_preserves_state():
    async with pg_store() as (store, pool):
        current = service(store)
        await bootstrap(current)
        before = await store.read("acct")
        await store.initialize()
        async with pool.acquire() as conn:
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM trading_schema_migrations WHERE version=3"
                )
                == 1
            )
        assert await store.read("acct") == before


@pytest.mark.asyncio
async def test_newer_unknown_schema_cannot_be_silently_opened_by_older_runtime():
    async with pg_store() as (store, pool):
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO trading_schema_migrations(version,checksum) VALUES(999,'future')"
            )
        with pytest.raises(RuntimeError, match="newer"):
            await store.initialize()


@pytest.mark.asyncio
async def test_non_development_bootstrap_refuses_before_opening_database(monkeypatch):
    import scripts.bootstrap_dev_schema as bootstrap_script

    async def forbidden(**kwargs):
        raise AssertionError("database must not be touched")

    monkeypatch.setattr(bootstrap_script, "create_pool", forbidden)
    with pytest.raises(SystemExit, match="non-development"):
        await bootstrap_script.bootstrap("production", True)


@pytest.mark.asyncio
async def test_rebuild_failure_rolls_back_both_query_projections(monkeypatch):
    async with pg_store() as (store, pool):
        current = service(store)
        await bootstrap(current)
        await current.place(command())
        async with pool.acquire() as conn:
            commands = await conn.fetch("SELECT * FROM trading_commands")
            orders = await conn.fetch("SELECT * FROM trading_order_projection")
        original = store._write_query_projections

        async def failing(conn, state):
            await original(conn, state)
            raise RuntimeError("projection writer failed")

        monkeypatch.setattr(store, "_write_query_projections", failing)
        with pytest.raises(RuntimeError, match="projection writer failed"):
            await store.rebuild_query_projections("acct")
        async with pool.acquire() as conn:
            assert await conn.fetch("SELECT * FROM trading_commands") == commands
            assert await conn.fetch("SELECT * FROM trading_order_projection") == orders
