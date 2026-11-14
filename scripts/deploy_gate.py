"""Pre/post deployment quality gate for the protected Demo stack.

`pre` proves the operator stopped new risk, commands converged and a single
execution owner holds a valid lease before replacing containers.  `post`
proves the started stack is healthy, runs the expected immutable digest, and
has no unknown/unbooked/outbox backlog after startup.

The collector inspects the real Compose containers and PostgreSQL facts; it
does not trust a hand-written evidence file.  No secret value is emitted.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.collect_runtime_release_binding import (
    APP_SERVICES,
    EXPECTED_SERVICES,
    LABEL_ENVIRONMENT,
    LABEL_RELEASE_COMMIT,
    LABEL_RELEASE_ID,
    _digest_of,
    _http,
    collect_containers,
)

ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

# `psql -tAc` renders a boolean as `t`/`f`; the container images are not
# guaranteed to spell it out.  Unknown spellings stay unknown (None) so the
# gate can fail closed instead of guessing.
TRUE_VALUES = frozenset({"t", "true", "1", "yes", "on"})
FALSE_VALUES = frozenset({"f", "false", "0", "no", "off"})

# Command states that mean "the order flow has not converged yet".
CONVERGENCE_STATUSES = ("QUEUED", "SENDING")


def parse_boolean(value: object) -> bool | None:
    """Parse a PostgreSQL boolean rendered by ``psql -tAc``."""
    text = str(value if value is not None else "").strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return None


def parse_count(value: object) -> int | None:
    """Parse a PostgreSQL count rendered by ``psql -tAc``."""
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _compose_exec(
    compose_files: list[str], project: str, service: str, command: list[str]
) -> subprocess.CompletedProcess:
    argv = ["docker", "compose"]
    for path in compose_files:
        argv.extend(["-f", path])
    argv.extend(["-p", project, "exec", "-T", service, *command])
    return subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)


def _psql(compose_files: list[str], project: str, query: str) -> str:
    script = f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc {shlex.quote(query)}'
    result = _compose_exec(compose_files, project, "postgres", ["sh", "-lc", script])
    if result.returncode != 0:
        raise RuntimeError(f"PSQL_FAILED:{result.returncode}")
    return result.stdout.strip()


def collect_db_facts(compose_files: list[str], project: str, account_id: str) -> dict[str, Any]:
    if not ACCOUNT_RE.fullmatch(account_id):
        raise ValueError("ACCOUNT_ID_INVALID")
    facts: dict[str, Any] = {}
    # `psql -tAc` prints booleans as `t`/`f`; an empty answer means the row
    # was not found and must not be read as "false".
    facts["new_risk_blocked"] = parse_boolean(
        _psql(
            compose_files,
            project,
            "SELECT COALESCE((state->'control'->>'new_risk_blocked')::boolean,false)"
            f" FROM trading_accounts WHERE account_id='{account_id}'",
        )
    )
    facts["unknown_commands"] = parse_count(
        _psql(
            compose_files,
            project,
            "SELECT count(*) FROM trading_commands WHERE status='UNKNOWN'",
        )
    )
    facts["queued_commands"] = parse_count(
        _psql(
            compose_files,
            project,
            "SELECT count(*) FROM trading_commands WHERE status='QUEUED'",
        )
    )
    facts["sending_commands"] = parse_count(
        _psql(
            compose_files,
            project,
            "SELECT count(*) FROM trading_commands WHERE status='SENDING'",
        )
    )
    facts["outbox_pending"] = parse_count(
        _psql(
            compose_files,
            project,
            "SELECT count(*) FROM trading_outbox WHERE delivered=false",
        )
    )
    facts["unbooked_fills"] = parse_count(
        _psql(
            compose_files,
            project,
            "SELECT count(*) FROM trading_accounts a,"
            " jsonb_each(COALESCE(a.state->'orders','{}'::jsonb)) o"
            f" WHERE a.account_id='{account_id}'"
            " AND COALESCE((o.value->>'observed_qty')::numeric,0)"
            " > COALESCE((o.value->>'booked_qty')::numeric,0)",
        )
    )
    lease = _psql(
        compose_files,
        project,
        "SELECT owner || '|' || fencing_token || '|' ||"
        " (expires_at > clock_timestamp())"
        f" FROM trading_runtime_leases WHERE scope='trading:{account_id}'",
    )
    owner, token, valid = (lease.split("|") + ["", "", "false"])[:3]
    facts["lease"] = {
        "owner": owner,
        "fencing_token": token,
        # `psql -tAc` renders a bare boolean as t/f, while a boolean cast to
        # text by concatenation renders true/false.  Accept both spellings.
        "valid": parse_boolean(valid) is True,
    }
    runtime = _psql(
        compose_files,
        project,
        "SELECT COALESCE(state->'control'->'runtime'->>'owner','') || '|' ||"
        " COALESCE(state->'control'->'runtime'->>'token','') || '|' ||"
        " COALESCE(state->'control'->'runtime'->>'ready','false')"
        f" FROM trading_accounts WHERE account_id='{account_id}'",
    )
    runtime_owner, runtime_token, runtime_ready = (runtime.split("|") + ["", "", "false"])[:3]
    facts["runtime"] = {
        "owner": runtime_owner,
        "token": runtime_token,
        "ready": parse_boolean(runtime_ready) is True,
    }
    return facts


def evaluate(
    *,
    mode: str,
    containers: dict[str, dict[str, Any]],
    health: dict[str, Any],
    facts: dict[str, Any],
    expected_release_id: str,
    expected_commit: str,
    expected_digest: str,
) -> list[str]:
    errors: list[str] = []
    if mode not in {"pre", "post"}:
        return ["MODE_INVALID"]
    for service in EXPECTED_SERVICES:
        item = containers.get(service)
        if item is None or not item.get("present"):
            errors.append(f"CONTAINER_MISSING:{service}")
            continue
        if not item.get("running"):
            errors.append(f"CONTAINER_NOT_RUNNING:{service}")
        if item.get("health") != "healthy":
            errors.append(f"CONTAINER_NOT_HEALTHY:{service}:{item.get('health')}")
    if mode == "post":
        for service in APP_SERVICES:
            item = containers.get(service) or {}
            labels = item.get("labels") or {}
            if labels.get(LABEL_RELEASE_ID) != expected_release_id:
                errors.append(f"LABEL_RELEASE_ID_MISMATCH:{service}")
            if str(labels.get(LABEL_RELEASE_COMMIT) or "").lower() != expected_commit.lower():
                errors.append(f"LABEL_RELEASE_COMMIT_MISMATCH:{service}")
            if labels.get(LABEL_ENVIRONMENT) != "demo":
                errors.append(f"LABEL_ENVIRONMENT_MISMATCH:{service}")
            if _digest_of(str(item.get("image") or "")) != expected_digest:
                errors.append(f"IMAGE_DIGEST_MISMATCH:{service}")
    live = (health.get("live") or {}).get("status")
    ready = (health.get("ready") or {}).get("status")
    trading = health.get("trading") or {}
    if live != 200:
        errors.append("HEALTH_LIVE_NOT_200")
    if ready != 200:
        errors.append("HEALTH_READY_NOT_200")
    if mode == "post":
        body = trading.get("body") or {}
        reasons = set(body.get("reasons") or [])
        ready = trading.get("status") == 200 and body.get("ready") is True
        # The pre gate intentionally blocks new risk.  Post-deploy readiness may
        # therefore be 503 with exactly that reason; any other reason is a real
        # failure.  The operator resumes new risk only after this gate passes.
        intentional_block = (
            trading.get("status") == 503 and bool(reasons) and reasons <= {"NEW_RISK_BLOCKED"}
        )
        if not ready and not intentional_block:
            errors.append("HEALTH_TRADING_NOT_READY:" + ",".join(sorted(reasons)))
    # Every convergence counter is required.  A missing or unreadable fact is
    # a failure, never "zero": the gate may not pass because a query did not
    # answer.
    counter_codes = {
        "unknown_commands": "UNKNOWN_COMMANDS_PRESENT",
        "queued_commands": "COMMANDS_NOT_CONVERGED:QUEUED",
        "sending_commands": "COMMANDS_NOT_CONVERGED:SENDING",
        "outbox_pending": "OUTBOX_PENDING",
        "unbooked_fills": "UNBOOKED_FILLS_PRESENT",
    }
    for key, code in counter_codes.items():
        value = facts.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"FACTS_INVALID:{key}")
        elif value > 0:
            errors.append(code)
    lease = facts.get("lease")
    if not isinstance(lease, dict) or not lease:
        errors.append("FACTS_INVALID:lease")
        lease = {}
    runtime = facts.get("runtime")
    if not isinstance(runtime, dict) or not runtime:
        errors.append("FACTS_INVALID:runtime")
        runtime = {}
    if lease.get("valid") is not True:
        errors.append("EXECUTION_LEASE_INVALID")
    if not str(lease.get("owner") or "").strip():
        errors.append("FACTS_INVALID:lease.owner")
    if not str(lease.get("fencing_token") or "").strip():
        errors.append("FACTS_INVALID:lease.fencing_token")
    if not str(runtime.get("owner") or "").strip():
        errors.append("FACTS_INVALID:runtime.owner")
    if not str(runtime.get("token") or "").strip():
        errors.append("FACTS_INVALID:runtime.token")
    if str(lease.get("owner") or "") != str(runtime.get("owner") or ""):
        errors.append("EXECUTION_OWNER_NOT_ALIGNED")
    if str(lease.get("fencing_token") or "") != str(runtime.get("token") or ""):
        errors.append("EXECUTION_FENCING_NOT_ALIGNED")
    if mode == "pre":
        blocked = facts.get("new_risk_blocked")
        if blocked is None:
            errors.append("NEW_RISK_STATE_UNKNOWN")
        elif blocked is not True:
            errors.append("NEW_RISK_NOT_BLOCKED")
    return errors


def collect(
    *,
    project: str,
    compose_files: list[str],
    control_url: str,
    account_id: str,
    expected_release_id: str,
    expected_commit: str,
    expected_digest: str,
) -> dict[str, Any]:
    containers = collect_containers(project)
    health = {
        "live": _http(control_url.rstrip("/") + "/health/live"),
        "ready": _http(control_url.rstrip("/") + "/health/ready"),
        "trading": _http(control_url.rstrip("/") + "/health/trading"),
    }
    facts = collect_db_facts(compose_files, project, account_id)
    return {
        "schema_version": 1,
        "runtime_inspected": True,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "compose_project": project,
        "environment": "demo",
        "release_id": expected_release_id,
        "commit": expected_commit.lower(),
        "image_digest": expected_digest,
        "containers": containers,
        "health": health,
        "facts": facts,
    }


def run_gate(
    *,
    mode: str,
    collect_snapshot,
    evaluate_snapshot,
    wait_timeout: float = 0.0,
    poll_interval: float = 15.0,
    sleep_fn=time.sleep,
    clock=time.monotonic,
) -> tuple[dict[str, Any], list[str]]:
    """Collect and evaluate, polling the post gate until it converges.

    ``up -d`` returns while containers are still ``starting``.  A single
    immediate evaluation would report a failure that is really a startup
    race, so the post gate retries inside a bounded timeout and only then
    decides.  The pre gate never waits: it must fail fast.
    """
    deadline = clock() + max(0.0, wait_timeout)
    attempts = 0
    while True:
        snapshot = collect_snapshot()
        errors = evaluate_snapshot(snapshot)
        attempts += 1
        if not errors or mode != "post" or clock() >= deadline:
            break
        sleep_fn(max(0.0, poll_interval))
    snapshot["attempts"] = attempts
    return snapshot, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("pre", "post"), required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--compose-file", action="append", default=None)
    parser.add_argument("--control-url", default="http://127.0.0.1:18090")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--expected-release-id", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-digest", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=0.0,
        help="Bounded seconds the post gate waits for the stack to converge (0 = single attempt)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=15.0,
        help="Seconds between post gate attempts (default: 15)",
    )
    args = parser.parse_args()
    compose_files = args.compose_file or ["compose.yml", "deploy/compose.demo.yml"]

    def collect_snapshot() -> dict[str, Any]:
        return collect(
            project=args.project,
            compose_files=compose_files,
            control_url=args.control_url,
            account_id=args.account_id,
            expected_release_id=args.expected_release_id,
            expected_commit=args.expected_commit,
            expected_digest=args.expected_digest,
        )

    def evaluate_snapshot(snapshot: dict[str, Any]) -> list[str]:
        return evaluate(
            mode=args.mode,
            containers=snapshot["containers"],
            health=snapshot["health"],
            facts=snapshot["facts"],
            expected_release_id=args.expected_release_id,
            expected_commit=args.expected_commit,
            expected_digest=args.expected_digest,
        )

    try:
        snapshot, errors = run_gate(
            mode=args.mode,
            collect_snapshot=collect_snapshot,
            evaluate_snapshot=evaluate_snapshot,
            wait_timeout=args.wait_timeout,
            poll_interval=args.poll_interval,
        )
    except Exception as exc:
        snapshot = {"runtime_inspected": False, "error": type(exc).__name__}
        errors = [f"COLLECT_FAILED:{type(exc).__name__}"]
    snapshot["mode"] = args.mode
    snapshot["errors"] = errors
    snapshot["passed"] = not errors
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"passed": not errors, "errors": errors}, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
