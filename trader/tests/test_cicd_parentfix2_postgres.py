"""Independent round-2 PostgreSQL counterexamples (F-P3 real psql path).

Authored by the fresh independent tester for parent round 2, before reading
the implementation (design: ``artifacts/cicd/parentfix2-tester-r2/test-design.md``).
Pre-classified in ``ci/test_manifest.json`` (postgres group).

Two layers, kept strictly separate:

1. The **real** ``docker compose exec -T postgres psql -tAc`` path is proven by
   ``scripts/verify_deploy_gate_live.py`` on a task-owned, disposable Compose
   project (its own container/volume/port; never ``qts-postgres``/``qts_pgdata``).
   The engineer runs it on request; this file independently cross-checks the
   recorded report against hand-computed values and refuses to pass on a
   missing or stale report.

2. The gate's own SQL text is executed against the **task-owned loopback
   instance** (default ``127.0.0.1:55436`` via the DSN environment) inside a
   disposable schema, seeding a hand-computed dirty state.  ``psql`` output
   rendering itself is *not* re-faked here - that is layer 1's job.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import deploy_gate  # noqa: E402
from scripts.collect_runtime_release_binding import EXPECTED_SERVICES  # noqa: E402

from trader.trading import migrations as migrations_module  # noqa: E402

LIVE_REPORT = ROOT / "artifacts" / "cicd" / "raw" / "deploy-gate-live.json"

COMMIT = "c6fe450260eeb59d46c9caee591fb1625510b443"
DIGEST = "sha256:" + "a" * 64
RELEASE_ID = "parentfix2-tester-r2"
ACCOUNT = "acct-1"

# Hand-computed gate errors for the dirty state seeded below.
EXPECTED_ERRORS = {
    "COMMANDS_NOT_CONVERGED:QUEUED",
    "COMMANDS_NOT_CONVERGED:SENDING",
    "UNKNOWN_COMMANDS_PRESENT",
    "OUTBOX_PENDING",
    "UNBOOKED_FILLS_PRESENT",
}


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
    assert (parsed.hostname or "") in {
        "127.0.0.1",
        "localhost",
        "::1",
    }, "only the task-owned loopback PostgreSQL instance may be used"


# --------------------------------------------------------------------------
# Layer 1: the recorded real docker compose exec / psql -tAc report
# --------------------------------------------------------------------------
class TestVerifyDeployGateLiveReport:
    def test_live_report_exists_and_passes(self):
        assert LIVE_REPORT.exists(), (
            "artifacts/cicd/raw/deploy-gate-live.json is missing: run "
            "`python scripts/verify_deploy_gate_live.py "
            "--output artifacts/cicd/raw/deploy-gate-live.json` "
            "(execution_request); the real psql path may not be claimed "
            "without this recorded run"
        )
        report = json.loads(LIVE_REPORT.read_text(encoding="utf-8"))
        assert "error" not in report, report.get("error")
        assert report["ok"] is True
        # `psql -tAc` renders booleans as t/f; the gate parsed the real
        # output back to True for a state that was seeded as `true`.
        assert report["psql_boolean_is_t"] is True
        assert report["teardown_returncode"] == 0

        facts = report["facts"]
        # Hand-computed seed: 1 QUEUED, 1 SENDING, 1 UNKNOWN, 1 undelivered
        # outbox row, 1 order with observed_qty(2) > booked_qty(1).
        assert facts["new_risk_blocked"] is True
        assert facts["queued_commands"] == 1
        assert facts["sending_commands"] == 1
        assert facts["unknown_commands"] == 1
        assert facts["outbox_pending"] == 1
        assert facts["unbooked_fills"] == 1
        assert facts["lease"] == {"owner": "exec-live", "fencing_token": "7", "valid": True}
        assert facts["runtime"]["owner"] == "exec-live"
        assert facts["runtime"]["token"] == "7"
        assert facts["runtime"]["ready"] is True

        # The pre gate rejected the dirty state with exactly the hand-computed
        # error set and nothing else (fail closed, no swallowed failures).
        assert set(report["pre_gate_errors"]) == EXPECTED_ERRORS
        assert sorted(report["expected_errors_present"]) == sorted(EXPECTED_ERRORS)
        assert report["unexpected_errors"] == []


# --------------------------------------------------------------------------
# Layer 2: the gate's SQL against the real schema in a disposable schema
# --------------------------------------------------------------------------
async def _connect(search_path: str | None = None):
    kwargs = {"timeout": 10}
    if search_path:
        kwargs["server_settings"] = {"search_path": search_path}
    return await asyncpg.connect(_connection_string(), **kwargs)


async def _setup_schema(schema: str) -> None:
    connection = await _connect()
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await connection.close()
    connection = await _connect(search_path=schema)
    try:
        await migrations_module.initialize_trading_schema(connection)
        await connection.execute(
            "INSERT INTO trading_accounts(account_id, version, state) VALUES"
            " ('acct-1', 3,"
            ' \'{"control": {"new_risk_blocked": true,'
            ' "runtime": {"owner": "exec-1", "token": "7", "ready": "true"}},'
            ' "orders": {"ord-1": {"observed_qty": "2", "booked_qty": "1"}}}\'::jsonb);'
        )
        await connection.execute(
            "INSERT INTO trading_commands(account_id, command_id, cl_ord_id, kind, status,"
            " payload_hash, data) VALUES"
            " ('acct-1', 'cmd-1', 'cl-1', 'PLACE', 'CONFIRMED', 'h1', '{}'::jsonb),"
            " ('acct-1', 'cmd-2', 'cl-2', 'PLACE', 'QUEUED', 'h2', '{}'::jsonb),"
            " ('acct-1', 'cmd-3', 'cl-3', 'PLACE', 'SENDING', 'h3', '{}'::jsonb),"
            " ('acct-1', 'cmd-4', 'cl-4', 'PLACE', 'UNKNOWN', 'h4', '{}'::jsonb);"
        )
        await connection.execute(
            "INSERT INTO trading_outbox(account_id, event_id, delivered, data) VALUES"
            " ('acct-1', 'event-1', false, '{}'::jsonb);"
        )
        await connection.execute(
            "INSERT INTO trading_runtime_leases(scope, owner, fencing_token, expires_at) VALUES"
            " ('trading:acct-1', 'exec-1', 7, clock_timestamp() + interval '1 hour');"
        )
    finally:
        await connection.close()


async def _drop_schema(schema: str) -> None:
    connection = await _connect()
    try:
        await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        await connection.close()


@pytest.fixture()
def gate_schema():
    _assert_task_owned_loopback()
    schema = f"qts_parentfix2_tester_{uuid.uuid4().hex[:10]}"
    asyncio.run(_setup_schema(schema))
    try:
        yield schema
    finally:
        asyncio.run(_drop_schema(schema))


class TestGateSqlAgainstRealSchema:
    def test_collect_db_facts_returns_hand_computed_state(self, gate_schema, monkeypatch):
        """Run the gate's own queries through the real schema (disposable)."""

        def fake_psql(compose_files, project, query):
            # The gate's literal SQL text, executed unmodified.  Values come
            # back as the database returned them; the t/f *rendering* of
            # psql -tAc is proven by the live report, not re-faked here.
            async def run():
                connection = await _connect(search_path=gate_schema)
                try:
                    return await connection.fetchval(query)
                finally:
                    await connection.close()

            return asyncio.run(run())

        monkeypatch.setattr(deploy_gate, "_psql", fake_psql)
        facts = deploy_gate.collect_db_facts(["unused-compose.yml"], "unused-project", ACCOUNT)

        assert facts["new_risk_blocked"] is True
        assert facts["unknown_commands"] == 1
        assert facts["queued_commands"] == 1
        assert facts["sending_commands"] == 1
        assert facts["outbox_pending"] == 1
        assert facts["unbooked_fills"] == 1
        assert facts["lease"] == {"owner": "exec-1", "fencing_token": "7", "valid": True}
        assert facts["runtime"] == {"owner": "exec-1", "token": "7", "ready": True}

        errors = deploy_gate.evaluate(
            mode="pre",
            containers={
                service: {"present": True, "running": True, "health": "healthy"}
                for service in EXPECTED_SERVICES
            },
            health={
                "live": {"status": 200},
                "ready": {"status": 200},
                "trading": {
                    "status": 503,
                    "body": {"ready": False, "reasons": ["NEW_RISK_BLOCKED"]},
                },
            },
            facts=facts,
            expected_release_id=RELEASE_ID,
            expected_commit=COMMIT,
            expected_digest=DIGEST,
        )
        assert set(errors) == EXPECTED_ERRORS
