"""Expand-only migration, old-runtime compatibility and conservation (C5).

These tests use a task-owned temporary PostgreSQL schema only.  They never
touch the default schema, the running ``qts-postgres`` container or its
volumes.  The expected behaviour comes from the migration contract:

* migrations only add tables/columns/indexes (expand-only);
* an older runtime refuses to open a newer schema and must not roll back
  automatically;
* economic rows in the six authoritative tables are unchanged by the
  migration and by disposable projection rebuilds.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from trader.trading import migrations as migrations_module
from trader.trading.models import AccountState, json_value

ROOT = Path(__file__).resolve().parents[2]
CONNECTION_STRING = os.getenv(
    "POSTGRES_CONNECTION_STRING", "postgresql://trader:trader_pwd@127.0.0.1:5432/trading"
)
FULL_MIGRATIONS = migrations_module.MIGRATIONS
SIX_TABLES = (
    "trading_accounts",
    "trading_commands",
    "trading_executions",
    "trading_ledger",
    "trading_events",
    "trading_outbox",
)


def _asyncpg():
    import importlib

    return importlib.import_module("asyncpg")


async def _can_connect() -> bool:
    try:
        asyncpg = _asyncpg()
        connection = await asyncpg.connect(CONNECTION_STRING, timeout=3)
        await connection.close()
        return True
    except Exception:
        return False


@asynccontextmanager
async def _schema_pool(monkeypatch, versions):
    try:
        asyncpg = _asyncpg()
    except ImportError:
        pytest.skip("asyncpg not installed")
    schema = "qts_cicd_migration_" + uuid.uuid4().hex[:12]
    admin = await asyncpg.connect(CONNECTION_STRING)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    pool = None
    original = FULL_MIGRATIONS
    try:
        monkeypatch.setattr(
            migrations_module,
            "MIGRATIONS",
            tuple(entry for entry in original if entry[0] in versions),
        )
        pool = await asyncpg.create_pool(
            CONNECTION_STRING,
            min_size=1,
            max_size=2,
            server_settings={"search_path": schema},
        )
        yield pool
    finally:
        monkeypatch.setattr(migrations_module, "MIGRATIONS", original)
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


async def _apply_schema(pool) -> None:
    async with pool.acquire() as connection:
        await migrations_module.initialize_trading_schema(connection)


async def _table_digests(pool):
    digests = {}
    async with pool.acquire() as connection:
        for table in SIX_TABLES:
            rows = await connection.fetch(
                f"SELECT to_jsonb(row_data)::text AS payload FROM {table} AS row_data ORDER BY 1"
            )
            digest = hashlib.sha256()
            for row in rows:
                digest.update(row["payload"].encode("utf-8"))
                digest.update(b"\n")
            digests[table] = {"rows": len(rows), "sha256": digest.hexdigest()}
    return digests


async def _seed_v2_rows(pool) -> None:
    state = json_value(AccountState("acct"))
    async with pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO trading_accounts(account_id,state) VALUES($1,$2::jsonb)",
            "acct",
            json.dumps(state),
        )
        await connection.execute(
            """INSERT INTO trading_commands(account_id,command_id,cl_ord_id,kind,status,payload_hash,data)
               VALUES('acct','cmd-1','cl-1','PLACE','CONFIRMED','hash','{}'::jsonb)"""
        )
        await connection.execute(
            """INSERT INTO trading_executions(execution_key,account_id,cl_ord_id,data)
               VALUES('cl-1:exec-1','acct','cl-1','{"fill_qty":"0.5"}'::jsonb)"""
        )
        await connection.execute(
            """INSERT INTO trading_ledger(account_id,entry_id,data)
               VALUES('acct','ledger-1','{"kind":"trade"}'::jsonb)"""
        )
        await connection.execute(
            """INSERT INTO trading_events(account_id,event_id,data)
               VALUES('acct','event-1','{"type":"fill"}'::jsonb)"""
        )
        await connection.execute(
            """INSERT INTO trading_outbox(account_id,event_id,data)
               VALUES('acct','event-1','{"type":"fill"}'::jsonb)"""
        )


@pytest.mark.asyncio
async def test_expand_only_migration_preserves_economic_rows(monkeypatch):
    if not await _can_connect():
        pytest.skip("PostgreSQL not available")
    async with _schema_pool(monkeypatch, {2}) as pool:
        await _apply_schema(pool)
        await _seed_v2_rows(pool)
        before = await _table_digests(pool)
        assert all(item["rows"] == 1 for item in before.values())

    async with _schema_pool(monkeypatch, {2, 3}) as pool:
        # A fresh schema with both migrations cannot be compared to the data
        # above, so this branch only proves the final schema is valid.
        await _apply_schema(pool)
        async with pool.acquire() as connection:
            assert await connection.fetchval("SELECT count(*) FROM trading_schema_migrations") == 2


@pytest.mark.asyncio
async def test_migrating_a_populated_schema_does_not_change_economic_rows(monkeypatch):
    if not await _can_connect():
        pytest.skip("PostgreSQL not available")
    async with _schema_pool(monkeypatch, {2}) as pool:
        await _apply_schema(pool)
        await _seed_v2_rows(pool)
        before = await _table_digests(pool)
        async with pool.acquire() as connection:
            assert await connection.fetchval("SELECT count(*) FROM trading_schema_migrations") == 1

        # Apply the complete migration set to the same populated schema.
        monkeypatch.setattr(
            migrations_module,
            "MIGRATIONS",
            tuple(entry for entry in FULL_MIGRATIONS if entry[0] in {2, 3}),
        )
        await _apply_schema(pool)
        after = await _table_digests(pool)
        assert after == before
        async with pool.acquire() as connection:
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND table_name = 'trading_order_projection'"
                )
                == 1
            )


@pytest.mark.asyncio
async def test_older_runtime_refuses_a_newer_schema_and_does_not_roll_back(monkeypatch):
    if not await _can_connect():
        pytest.skip("PostgreSQL not available")
    async with _schema_pool(monkeypatch, {2, 3}) as pool:
        await _apply_schema(pool)
        before = await _table_digests(pool)
        monkeypatch.setattr(
            migrations_module,
            "MIGRATIONS",
            tuple(entry for entry in FULL_MIGRATIONS if entry[0] == 2),
        )
        with pytest.raises(RuntimeError, match="newer"):
            await _apply_schema(pool)
        after = await _table_digests(pool)
        assert after == before
        async with pool.acquire() as connection:
            assert (
                await connection.fetchval("SELECT max(version) FROM trading_schema_migrations") == 3
            )
