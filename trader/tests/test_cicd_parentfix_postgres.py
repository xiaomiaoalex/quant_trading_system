"""Independent PostgreSQL counterexamples for the parent-review fixes.

Pre-classified in ``ci/test_manifest.json`` (postgres group).  The tests cover
the F-P3 ``deploy_gate.collect_db_facts`` dimension: the gate's SQL predicates
must return hand-computed counts against the real trading schema and those
facts must drive ``evaluate`` exactly as the contract requires.

Only the task-owned, loopback-only instance from the handoff (default
``127.0.0.1:55436`` via the DSN environment) is used, always inside a
disposable ``qts_parentfix_*`` schema that is dropped afterwards.  The
pre-existing ``qts-postgres`` container and ``qts_pgdata`` volume are never
touched.  ``docker compose exec`` is not available inside a unit test, so the
``_psql`` shell-out is monkeypatched to run the *gate's own queries* via
asyncpg; everything else (query text, psql output semantics, ``evaluate``) is
the real implementation.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest
import scripts.deploy_gate as deploy_gate
from scripts.collect_runtime_release_binding import (
    APP_SERVICES,
    EXPECTED_SERVICES,
    LABEL_ENVIRONMENT,
    LABEL_RELEASE_COMMIT,
    LABEL_RELEASE_ID,
)
from scripts.deploy_gate import evaluate as evaluate_deploy_gate

from trader.trading import migrations as migrations_module

ROOT = Path(__file__).resolve().parents[2]

RELEASE_ID = "parentfix-rel-0001"
COMMIT = "c6fe450260eeb59d46c9caee591fb1625510b443"
DIGEST = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
IMAGE = "ghcr.io/example/qts@" + DIGEST
ACCOUNT = "acct-1"


def _connection_string() -> str:
    dsn = os.environ.get("POSTGRES_CONNECTION_STRING", "").strip()
    if dsn:
        return dsn
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "55436")
    database = os.environ.get("POSTGRES_DB", "trading")
    user = os.environ.get("POSTGRES_USER", "trader")
    password = os.environ.get("POSTGRES_PASSWORD", "trader_pwd")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def _assert_task_owned_loopback() -> None:
    parsed = urlparse(_connection_string())
    host = parsed.hostname or ""
    assert host in {
        "127.0.0.1",
        "localhost",
        "::1",
    }, "only the task-owned loopback PostgreSQL instance may be used"


def _admin_exec(statement: str) -> None:
    async def _run() -> None:
        connection = await asyncpg.connect(_connection_string(), timeout=10)
        try:
            await connection.execute(statement)
        finally:
            await connection.close()

    asyncio.run(_run())


def _db_call(schema: str, function) -> object:
    async def _run() -> object:
        connection = await asyncpg.connect(
            _connection_string(),
            timeout=10,
            server_settings={"search_path": schema},
        )
        try:
            return await function(connection)
        finally:
            await connection.close()

    return asyncio.run(_run())


async def _initialize_schema(connection) -> None:
    await migrations_module.initialize_trading_schema(connection)


async def _seed_dirty_state(connection) -> None:
    state = {
        "control": {
            "new_risk_blocked": True,
            "runtime": {"owner": "worker-a", "token": "7", "ready": "true"},
        },
        "orders": {
            "ord-clean": {"observed_qty": "1", "booked_qty": "1"},
            "ord-partial": {"observed_qty": "2", "booked_qty": "1"},
        },
    }
    await connection.execute(
        "INSERT INTO trading_accounts(account_id, version, state)" " VALUES($1, $2, $3::jsonb)",
        ACCOUNT,
        3,
        json.dumps(state),
    )
    await connection.execute(
        "INSERT INTO trading_commands(account_id, command_id, cl_ord_id, kind,"
        " status, payload_hash, data) VALUES"
        " ('acct-1', 'cmd-1', 'cl-1', 'PLACE', 'CONFIRMED', 'hash-1', '{}'::jsonb),"
        " ('acct-1', 'cmd-2', 'cl-2', 'PLACE', 'UNKNOWN', 'hash-2', '{}'::jsonb),"
        " ('acct-1', 'cmd-3', 'cl-3', 'PLACE', 'UNKNOWN', 'hash-3', '{}'::jsonb)"
    )
    await connection.execute(
        "INSERT INTO trading_outbox(account_id, event_id, delivered, data)"
        " VALUES('acct-1', 'event-1', false, '{}'::jsonb)"
    )
    await connection.execute(
        "INSERT INTO trading_runtime_leases(scope, owner, fencing_token, expires_at)"
        " VALUES('trading:acct-1', 'worker-a', 7, clock_timestamp() + interval"
        " '1 hour')"
    )


async def _seed_clean_state(connection) -> None:
    state = {
        "control": {
            "new_risk_blocked": True,
            "runtime": {"owner": "worker-a", "token": "7", "ready": "true"},
        },
        "orders": {"ord-clean": {"observed_qty": "1", "booked_qty": "1"}},
    }
    await connection.execute(
        "INSERT INTO trading_accounts(account_id, version, state)" " VALUES($1, $2, $3::jsonb)",
        ACCOUNT,
        3,
        json.dumps(state),
    )
    await connection.execute(
        "INSERT INTO trading_commands(account_id, command_id, cl_ord_id, kind,"
        " status, payload_hash, data) VALUES"
        " ('acct-1', 'cmd-1', 'cl-1', 'PLACE', 'CONFIRMED', 'hash-1', '{}'::jsonb)"
    )
    await connection.execute(
        "INSERT INTO trading_outbox(account_id, event_id, delivered, data)"
        " VALUES('acct-1', 'event-1', true, '{}'::jsonb)"
    )
    await connection.execute(
        "INSERT INTO trading_runtime_leases(scope, owner, fencing_token, expires_at)"
        " VALUES('trading:acct-1', 'worker-a', 7, clock_timestamp() + interval"
        " '1 hour')"
    )


async def _block_new_risk(connection, blocked: bool) -> None:
    await connection.execute(
        "UPDATE trading_accounts SET state = jsonb_set(state,"
        " '{control,new_risk_blocked}', $1::jsonb) WHERE account_id = 'acct-1'",
        "true" if blocked else "false",
    )


async def _expire_lease(connection) -> None:
    await connection.execute(
        "UPDATE trading_runtime_leases SET expires_at = clock_timestamp()"
        " - interval '1 hour' WHERE scope = 'trading:acct-1'"
    )


async def _reassign_lease_owner(connection) -> None:
    await connection.execute(
        "UPDATE trading_runtime_leases SET owner = 'worker-b',"
        " expires_at = clock_timestamp() + interval '1 hour'"
        " WHERE scope = 'trading:acct-1'"
    )


async def _rotate_runtime_token(connection) -> None:
    await connection.execute(
        "UPDATE trading_accounts SET state = jsonb_set(state,"
        " '{control,runtime,token}', '\"8\"'::jsonb) WHERE account_id = 'acct-1'"
    )


async def _leave_fill_unbooked(connection) -> None:
    await connection.execute(
        "UPDATE trading_accounts SET state = jsonb_set(state,"
        " '{orders,ord-clean,observed_qty}', '\"2\"'::jsonb)"
        " WHERE account_id = 'acct-1'"
    )


def _make_fake_psql(schema: str):
    def fake_psql(compose_files, project, query):
        async def _run():
            connection = await asyncpg.connect(
                _connection_string(),
                timeout=10,
                server_settings={"search_path": schema},
            )
            try:
                return await connection.fetchval(query)
            finally:
                await connection.close()

        value = asyncio.run(_run())
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    return fake_psql


def _collect_facts(monkeypatch, schema: str) -> dict:
    monkeypatch.setattr(deploy_gate, "_psql", _make_fake_psql(schema))
    return deploy_gate.collect_db_facts(["unused-compose.yml"], "unused-project", ACCOUNT)


def _healthy_containers() -> dict:
    containers = {}
    for service in EXPECTED_SERVICES:
        is_app = service in APP_SERVICES
        containers[service] = {
            "name": f"{service}-1",
            "present": True,
            "running": True,
            "health": "healthy",
            "image": IMAGE if is_app else f"registry.example/infrastructure@{DIGEST}",
            "image_id": "image-id-" + service,
            "labels": (
                {
                    LABEL_RELEASE_ID: RELEASE_ID,
                    LABEL_RELEASE_COMMIT: COMMIT,
                    LABEL_ENVIRONMENT: "demo",
                }
                if is_app
                else {}
            ),
        }
    return containers


def _healthy_control_health() -> dict:
    return {
        "live": {"status": 200},
        "ready": {"status": 200},
        "trading": {"status": 200, "body": {"ready": True}},
    }


def _evaluate(mode: str, facts: dict) -> list:
    return evaluate_deploy_gate(
        mode=mode,
        containers=_healthy_containers(),
        health=_healthy_control_health(),
        facts=facts,
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )


def _new_schema() -> str:
    _assert_task_owned_loopback()
    schema = "qts_parentfix_" + uuid.uuid4().hex[:12]
    _admin_exec(f'CREATE SCHEMA "{schema}"')
    _db_call(schema, _initialize_schema)
    return schema


def _drop_schema(schema: str) -> None:
    _admin_exec(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    async def _count() -> int:
        connection = await asyncpg.connect(_connection_string(), timeout=10)
        try:
            return await connection.fetchval(
                "SELECT count(*) FROM information_schema.schemata" " WHERE schema_name = $1",
                schema,
            )
        finally:
            await connection.close()

    assert asyncio.run(_count()) == 0


# --------------------------------------------------------------------------- #
# F-P3: real facts must match hand-computed counts and drive the gate.
# --------------------------------------------------------------------------- #


def test_collect_db_facts_matches_hand_computed_counts(monkeypatch):
    schema = _new_schema()
    try:
        _db_call(schema, _seed_dirty_state)
        facts = _collect_facts(monkeypatch, schema)
        assert facts["new_risk_blocked"] is True
        assert facts["unknown_commands"] == 2
        assert facts["outbox_pending"] == 1
        assert facts["unbooked_fills"] == 1
        assert facts["lease"] == {
            "owner": "worker-a",
            "fencing_token": "7",
            "valid": True,
        }
        assert facts["runtime"] == {
            "owner": "worker-a",
            "token": "7",
            "ready": True,
        }
    finally:
        _drop_schema(schema)


def test_pre_gate_blocks_seeded_faults_via_real_facts(monkeypatch):
    schema = _new_schema()
    try:
        _db_call(schema, _seed_dirty_state)
        errors = _evaluate("pre", _collect_facts(monkeypatch, schema))
        assert "UNKNOWN_COMMANDS_PRESENT" in errors
        assert "OUTBOX_PENDING" in errors
        assert "NEW_RISK_NOT_BLOCKED" not in errors

        _db_call(schema, lambda connection: _block_new_risk(connection, False))
        errors = _evaluate("pre", _collect_facts(monkeypatch, schema))
        assert "NEW_RISK_NOT_BLOCKED" in errors
    finally:
        _drop_schema(schema)


def test_lease_and_owner_mismatch_detected_via_real_facts(monkeypatch):
    schema = _new_schema()
    try:
        _db_call(schema, _seed_dirty_state)

        _db_call(schema, _expire_lease)
        errors = _evaluate("pre", _collect_facts(monkeypatch, schema))
        assert "EXECUTION_LEASE_INVALID" in errors

        _db_call(schema, _reassign_lease_owner)
        errors = _evaluate("pre", _collect_facts(monkeypatch, schema))
        assert "EXECUTION_LEASE_INVALID" not in errors
        assert "EXECUTION_OWNER_NOT_ALIGNED" in errors

        _db_call(schema, _rotate_runtime_token)
        errors = _evaluate("pre", _collect_facts(monkeypatch, schema))
        assert "EXECUTION_FENCING_NOT_ALIGNED" in errors
    finally:
        _drop_schema(schema)


def test_post_gate_passes_on_converged_state_and_flags_unbooked_fills(monkeypatch):
    schema = _new_schema()
    try:
        _db_call(schema, _seed_clean_state)
        assert _evaluate("post", _collect_facts(monkeypatch, schema)) == []

        _db_call(schema, _leave_fill_unbooked)
        errors = _evaluate("post", _collect_facts(monkeypatch, schema))
        assert "UNBOOKED_FILLS_PRESENT" in errors
    finally:
        _drop_schema(schema)


def test_account_id_is_validated_fail_closed():
    with pytest.raises(ValueError, match="ACCOUNT_ID_INVALID"):
        deploy_gate.collect_db_facts(["unused-compose.yml"], "unused-project", "bad id")
