"""Verify the deployment gate against a real ``docker compose exec``/``psql``.

The deploy gate reads its facts from PostgreSQL through
``docker compose exec -T postgres psql -tAc``.  Two properties of that path
cannot be proven with a mocked client:

* ``psql -tAc`` renders a boolean as ``t``/``f``, not ``true``/``false``;
* a missing row or an unreadable answer must not be read as "zero" or
  "false" - the gate has to fail closed.

This script creates a **task-owned** Compose project with its own postgres
service, volume and port, applies the trading schema, seeds known states and
runs ``scripts/deploy_gate.collect_db_facts`` through the real path.  The
project is torn down afterwards.  It never touches an existing
``qts-postgres`` container or the ``qts_pgdata`` volume.

Usage:
    python scripts/verify_deploy_gate_live.py --output artifacts/cicd/raw/deploy-gate-live.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.deploy_gate import collect_db_facts, evaluate  # noqa: E402

POSTGRES_IMAGE = (
    "postgres:16@sha256:5db6dac2ef5dcff512f4f00a009c3c332b9f2f28f24277175bf3e4a3d02ef83a"
)
ACCOUNT = "acct-live"


def _compose(compose_file: Path, project: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "-p", project, *args],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _write_compose(path: Path, project: str, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "services:",
                "  postgres:",
                f"    image: {POSTGRES_IMAGE}",
                "    environment:",
                "      POSTGRES_USER: trader",
                "      POSTGRES_DB: trading",
                "      POSTGRES_PASSWORD: live-verification-only",
                "    ports:",
                f'      - "127.0.0.1:{port}:5432"',
                "    healthcheck:",
                '      test: ["CMD-SHELL", "pg_isready -U trader -d trading"]',
                "      interval: 2s",
                "      timeout: 3s",
                "      retries: 30",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _psql(compose_file: Path, project: str, sql: str) -> None:
    result = _compose(
        compose_file,
        project,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "trader",
        "-d",
        "trading",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        sql,
    )
    if result.returncode != 0:
        raise RuntimeError(f"PSQL_FAILED:{result.stderr.strip()[:400]}")


def _psql_capture(compose_file: Path, project: str, sql: str) -> str:
    result = _compose(
        compose_file,
        project,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "trader",
        "-d",
        "trading",
        "-tAc",
        sql,
    )
    if result.returncode != 0:
        raise RuntimeError(f"PSQL_CAPTURE_FAILED:{result.stderr.strip()[:400]}")
    return result.stdout.strip()


def _seed(compose_file: Path, project: str, *, blocked: bool) -> None:
    state = {
        "control": {
            "new_risk_blocked": blocked,
            "runtime": {"owner": "exec-live", "token": "7", "ready": "true"},
        },
        "orders": {"ord-1": {"observed_qty": "2", "booked_qty": "1"}},
    }
    payload = json.dumps(state)
    _psql(
        compose_file,
        project,
        "INSERT INTO trading_accounts(account_id, version, state) VALUES"
        f" ('{ACCOUNT}', 3, '{payload}'::jsonb);",
    )
    _psql(
        compose_file,
        project,
        "INSERT INTO trading_commands(account_id, command_id, cl_ord_id, kind, status,"
        " payload_hash, data) VALUES"
        f" ('{ACCOUNT}', 'cmd-1', 'cl-1', 'PLACE', 'CONFIRMED', 'h1', '{{}}'::jsonb),"
        f" ('{ACCOUNT}', 'cmd-2', 'cl-2', 'PLACE', 'QUEUED', 'h2', '{{}}'::jsonb),"
        f" ('{ACCOUNT}', 'cmd-3', 'cl-3', 'PLACE', 'SENDING', 'h3', '{{}}'::jsonb),"
        f" ('{ACCOUNT}', 'cmd-4', 'cl-4', 'PLACE', 'UNKNOWN', 'h4', '{{}}'::jsonb);",
    )
    _psql(
        compose_file,
        project,
        "INSERT INTO trading_outbox(account_id, event_id, delivered, data) VALUES"
        f" ('{ACCOUNT}', 'event-1', false, '{{}}'::jsonb);",
    )
    _psql(
        compose_file,
        project,
        "INSERT INTO trading_runtime_leases(scope, owner, fencing_token, expires_at) VALUES"
        f" ('trading:{ACCOUNT}', 'exec-live', 7, clock_timestamp() + interval '1 hour');",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--project", default="qts-cicd-livepg")
    parser.add_argument("--port", type=int, default=55439)
    args = parser.parse_args()

    workdir = ROOT / "artifacts" / "cicd" / "livepg"
    compose_file = workdir / "compose.yml"
    _write_compose(compose_file, args.project, args.port)
    report: dict = {"project": args.project, "compose_file": str(compose_file)}
    try:
        up = _compose(compose_file, args.project, "up", "-d", "postgres")
        if up.returncode != 0:
            raise RuntimeError(f"COMPOSE_UP_FAILED:{up.stderr.strip()[:400]}")
        for _ in range(60):
            probe = _compose(
                compose_file,
                args.project,
                "exec",
                "-T",
                "postgres",
                "pg_isready",
                "-U",
                "trader",
                "-d",
                "trading",
            )
            if probe.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("POSTGRES_NOT_READY")

        for script in ("001_trading.sql", "003_query_projections.sql"):
            sql = (ROOT / "trader" / "trading" / script).read_text(encoding="utf-8")
            _psql(compose_file, args.project, sql)
        _seed(compose_file, args.project, blocked=True)

        # Record the *raw* psql rendering.  A bare boolean column is
        # rendered as t/f, while a boolean cast to text by concatenation is
        # rendered as true/false; the gate must accept both.
        report["raw_psql_bare_boolean"] = _psql_capture(
            compose_file,
            args.project,
            "SELECT (clock_timestamp() > clock_timestamp() - interval '1 hour')",
        )
        report["raw_psql_concat_boolean"] = _psql_capture(
            compose_file,
            args.project,
            "SELECT 'x|' || (clock_timestamp() > clock_timestamp() - interval '1 hour')",
        )
        report["raw_lease_query"] = _psql_capture(
            compose_file,
            args.project,
            "SELECT owner || '|' || fencing_token || '|' ||"
            " (expires_at > clock_timestamp())"
            " FROM trading_runtime_leases"
            f" WHERE scope='trading:{ACCOUNT}'",
        )
        report["psql_boolean_is_t"] = report["raw_psql_bare_boolean"] == "t"
        report["psql_concat_boolean_is_true"] = report["raw_psql_concat_boolean"] == "x|true"

        facts = collect_db_facts([str(compose_file)], args.project, ACCOUNT)
        report["facts"] = facts
        report["queued_commands"] = facts["queued_commands"]
        report["sending_commands"] = facts["sending_commands"]
        report["unknown_commands"] = facts["unknown_commands"]
        report["outbox_pending"] = facts["outbox_pending"]
        report["unbooked_fills"] = facts["unbooked_fills"]

        errors = evaluate(
            mode="pre",
            containers={
                name: {"present": True, "running": True, "health": "healthy"}
                for name in (
                    "postgres",
                    "alert-sink",
                    "control",
                    "execution",
                    "research",
                    "history",
                    "backup",
                )
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
            expected_release_id="rel-live",
            expected_commit="c6fe450260eeb59d46c9caee591fb1625510b443",
            expected_digest="sha256:" + "a" * 64,
        )
        report["pre_gate_errors"] = errors
        expected_errors = {
            "COMMANDS_NOT_CONVERGED:QUEUED",
            "COMMANDS_NOT_CONVERGED:SENDING",
            "UNKNOWN_COMMANDS_PRESENT",
            "OUTBOX_PENDING",
            "UNBOOKED_FILLS_PRESENT",
        }
        report["expected_errors_present"] = sorted(expected_errors & set(errors))
        report["unexpected_errors"] = sorted(set(errors) - expected_errors)
        report["ok"] = (
            report["psql_boolean_is_t"] is True
            and report["psql_concat_boolean_is_true"] is True
            and report["raw_lease_query"] == "exec-live|7|true"
            and facts["new_risk_blocked"] is True
            and facts["queued_commands"] == 1
            and facts["sending_commands"] == 1
            and facts["unknown_commands"] == 1
            and facts["outbox_pending"] == 1
            and facts["unbooked_fills"] == 1
            and expected_errors <= set(errors)
        )
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["ok"] = False
    finally:
        down = _compose(compose_file, args.project, "down", "--volumes", "--remove-orphans")
        report["teardown_returncode"] = down.returncode

    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
