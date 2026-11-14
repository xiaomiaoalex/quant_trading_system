"""Independent PostgreSQL counterexamples for the CI/CD delivery (workflow v3).

Every database test owns its connection and a disposable ``qts_cicd_tester_*``
schema created for that test alone; the pre-existing ``qts-postgres`` container
and ``quant_trading_system_qts_pgdata`` volume are never opened.  The DSN must
point at the task-owned, loopback-only instance described in
``docs/CI_CD_INDEPENDENT_TEST_REPORT.md`` (engineer-provisioned
``127.0.0.1:55435``); the guard below refuses any other host.

The module is pre-classified as the ``postgres`` group and is meant to run with
``REQUIRE_POSTGRES=1`` and ``QTS_TEST_NETWORK_POLICY=loopback``.  A skip under
``REQUIRE_POSTGRES=1`` is rewritten to a failure by conftest, so a missing
dependency can never turn this job green.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from trader.trading import migrations as migrations_module

ROOT = Path(__file__).resolve().parents[2]
CONFTEST_PATH = ROOT / "trader" / "tests" / "conftest.py"

SIX_TABLES = (
    "trading_accounts",
    "trading_commands",
    "trading_executions",
    "trading_ledger",
    "trading_events",
    "trading_outbox",
)


def _load_tests_conftest():
    spec = importlib.util.spec_from_file_location("qts_tests_conftest_pg", CONFTEST_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _connection_string() -> str:
    dsn = os.environ.get("POSTGRES_CONNECTION_STRING", "").strip()
    if dsn:
        return dsn
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    database = os.environ.get("POSTGRES_DB", "trading")
    user = os.environ.get("POSTGRES_USER", "trader")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


# --------------------------------------------------------------------------- #
# C2: loopback-only network policy and fail-closed REQUIRE_POSTGRES handling.
# --------------------------------------------------------------------------- #


def test_loopback_policy_reaches_only_the_task_owned_postgres():
    assert os.environ.get("QTS_TEST_NETWORK_POLICY") == "loopback"
    parsed = urlparse(_connection_string())
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432
    assert host in {"127.0.0.1", "localhost", "::1"}, "task-owned loopback instance only"
    with socket.create_connection((host, port), timeout=5):
        pass
    with pytest.raises(PermissionError, match="QTS_NETWORK_BLOCKED"):
        socket.create_connection(("93.184.216.34", 443), timeout=0.2)
    with pytest.raises(PermissionError, match="QTS_NETWORK_BLOCKED"):
        socket.getaddrinfo("example.com", 443)


def _pytest_child(env_overrides: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for key in (
        "POSTGRES_CONNECTION_STRING",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ):
        env.pop(key, None)
    env.update(
        {
            "REQUIRE_POSTGRES": "1",
            "QTS_TEST_GROUP": "postgres",
            "QTS_TEST_NETWORK_POLICY": "loopback",
        }
    )
    env.update(env_overrides)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "trader/tests/test_cicd_independent_postgres.py",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def test_require_postgres_without_dsn_is_a_usage_error():
    completed = _pytest_child({})
    combined = completed.stdout + completed.stderr
    assert completed.returncode == 4, combined
    assert "no PostgreSQL DSN is configured" in combined


def test_require_postgres_with_unreachable_dsn_is_a_usage_error():
    completed = _pytest_child(
        {"POSTGRES_CONNECTION_STRING": "postgresql://trader:pwd@127.0.0.1:1/trading"}
    )
    combined = completed.stdout + completed.stderr
    assert completed.returncode == 4, combined
    assert "unreachable or the DSN is invalid" in combined


def _drive_skip_hook(item, report) -> None:
    conftest = _load_tests_conftest()
    outcome = SimpleNamespace(get_result=lambda: report)
    generator = conftest.pytest_runtest_makereport(item, None)
    next(generator)
    with contextlib.suppress(StopIteration):
        generator.send(outcome)


def _postgres_item() -> SimpleNamespace:
    return SimpleNamespace(
        nodeid="probe::test_x",
        get_closest_marker=lambda name: object() if name == "postgres" else None,
    )


def test_postgres_skip_is_converted_to_failure_when_strict(monkeypatch):
    monkeypatch.setenv("REQUIRE_POSTGRES", "1")
    report = SimpleNamespace(skipped=True, outcome="skipped", longrepr=None)
    _drive_skip_hook(_postgres_item(), report)
    assert report.outcome == "failed"
    message = str(report.longrepr)
    assert "REQUIRE_POSTGRES=1" in message
    assert "probe::test_x" in message


def test_postgres_skip_stays_visible_when_not_strict(monkeypatch):
    monkeypatch.setenv("REQUIRE_POSTGRES", "0")
    report = SimpleNamespace(skipped=True, outcome="skipped", longrepr=None)
    _drive_skip_hook(_postgres_item(), report)
    assert report.outcome == "skipped"

    strict = SimpleNamespace(
        nodeid="other::test_y",
        get_closest_marker=lambda name: None,
    )
    report = SimpleNamespace(skipped=True, outcome="skipped", longrepr=None)
    _drive_skip_hook(strict, report)
    assert report.outcome == "skipped"


# --------------------------------------------------------------------------- #
# C5: expand-only v3 migration conserves rows; old runtimes refuse newer
# schemas without rolling anything back.  All inside one disposable schema.
# --------------------------------------------------------------------------- #


@contextlib.asynccontextmanager
async def _task_schema():
    asyncpg = pytest.importorskip("asyncpg")
    dsn = _connection_string()
    parsed = urlparse(dsn)
    host = parsed.hostname or ""
    assert host in {"127.0.0.1", "localhost", "::1"}, "task-owned loopback instance only"
    admin = await asyncpg.connect(dsn, timeout=10)
    schema = "qts_cicd_tester_" + uuid.uuid4().hex[:12]
    pool = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=2,
            server_settings={"search_path": schema},
        )
        yield pool, schema
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        remaining = await admin.fetchval(
            "SELECT count(*) FROM information_schema.schemata WHERE schema_name = $1", schema
        )
        await admin.close()
        assert remaining == 0


async def _seed_v2_rows(connection) -> None:
    state = json.dumps({"cash": "10000", "positions": {}, "status": "ACTIVE"})
    await connection.execute(
        "INSERT INTO trading_accounts(account_id, version, state) VALUES($1, $2, $3::jsonb)",
        "acct",
        3,
        state,
    )
    await connection.execute(
        "INSERT INTO trading_commands(account_id, command_id, cl_ord_id, kind, status,"
        " payload_hash, data) VALUES('acct', 'cmd-1', 'cl-1', 'PLACE', 'CONFIRMED',"
        " 'hash-1', '{}'::jsonb)"
    )
    await connection.execute(
        "INSERT INTO trading_executions(execution_key, account_id, cl_ord_id, data)"
        " VALUES('acct:exec-1', 'acct', 'cl-1', '{\"price\": \"50000\"}'::jsonb)"
    )
    await connection.execute(
        "INSERT INTO trading_ledger(account_id, entry_id, data)"
        " VALUES('acct', 'entry-1', '{\"delta\": \"-100\"}'::jsonb)"
    )
    await connection.execute(
        "INSERT INTO trading_events(account_id, event_id, data)"
        " VALUES('acct', 'event-1', '{\"type\": \"FILL\"}'::jsonb)"
    )
    await connection.execute(
        "INSERT INTO trading_outbox(account_id, event_id, delivered, data)"
        " VALUES('acct', 'event-1', false, '{}'::jsonb)"
    )


async def _digest_tables(connection) -> dict:
    digests = {}
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


def _only_versions(original, versions: set) -> tuple:
    return tuple(entry for entry in original if entry[0] in versions)


async def _projection_exists(connection) -> int:
    return await connection.fetchval(
        "SELECT count(*) FROM information_schema.tables"
        " WHERE table_schema = current_schema()"
        " AND table_name = 'trading_order_projection'"
    )


@pytest.mark.asyncio
async def test_expand_only_v3_migration_conserves_six_table_rows(monkeypatch):
    original = migrations_module.MIGRATIONS
    assert [version for version, _ in original] == [2, 3]
    async with _task_schema() as (pool, schema):
        monkeypatch.setattr(migrations_module, "MIGRATIONS", _only_versions(original, {2}))
        async with pool.acquire() as connection:
            await migrations_module.initialize_trading_schema(connection)
        async with pool.acquire() as connection:
            await _seed_v2_rows(connection)
        async with pool.acquire() as connection:
            before = await _digest_tables(connection)
            assert (
                await connection.fetchval("SELECT max(version) FROM trading_schema_migrations") == 2
            )
            assert await _projection_exists(connection) == 0

        monkeypatch.setattr(migrations_module, "MIGRATIONS", _only_versions(original, {2, 3}))
        async with pool.acquire() as connection:
            await migrations_module.initialize_trading_schema(connection)
            after = await _digest_tables(connection)
            assert after == before
            assert all(item["rows"] == 1 for item in after.values())
            version = await connection.fetchval(
                "SELECT max(version) FROM trading_schema_migrations"
            )
            applied = {
                record["version"]: record["checksum"]
                for record in await connection.fetch(
                    "SELECT version, checksum FROM trading_schema_migrations ORDER BY version"
                )
            }
            projection = await _projection_exists(connection)
        assert version == 3
        assert projection == 1
        assert schema.startswith("qts_cicd_tester_")
        expected = {
            entry_version: hashlib.sha256(
                (ROOT / "trader" / "trading" / filename).read_bytes()
            ).hexdigest()
            for entry_version, filename in original
        }
        assert applied == expected


@pytest.mark.asyncio
async def test_old_runtime_refuses_newer_schema_without_rollback(monkeypatch):
    original = migrations_module.MIGRATIONS
    async with _task_schema() as (pool, schema):
        monkeypatch.setattr(migrations_module, "MIGRATIONS", _only_versions(original, {2, 3}))
        async with pool.acquire() as connection:
            await migrations_module.initialize_trading_schema(connection)
        async with pool.acquire() as connection:
            await _seed_v2_rows(connection)
            before = await _digest_tables(connection)

        monkeypatch.setattr(migrations_module, "MIGRATIONS", _only_versions(original, {2}))
        with pytest.raises(RuntimeError, match="newer"):
            async with pool.acquire() as connection:
                await migrations_module.initialize_trading_schema(connection)

        async with pool.acquire() as connection:
            after = await _digest_tables(connection)
            assert after == before
            assert (
                await connection.fetchval("SELECT max(version) FROM trading_schema_migrations") == 3
            )
            assert await connection.fetchval("SELECT count(*) FROM trading_schema_migrations") == 2
            assert await _projection_exists(connection) == 1
        assert schema.startswith("qts_cicd_tester_")


def test_network_guard_classifies_loopback_hosts_correctly():
    conftest = _load_tests_conftest()
    assert conftest._loopback_host("127.0.0.1") is True
    assert conftest._loopback_host("::1") is True
    assert conftest._loopback_host("93.184.216.34") is False
    assert asyncio.iscoroutinefunction(migrations_module.initialize_trading_schema)
