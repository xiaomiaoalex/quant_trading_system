"""Run a real process-crash durability drill against PostgreSQL.

The writer process commits economic facts, signals readiness, and is killed
without cleanup. A fresh verifier process then reloads the same schema: the
fact digests, command statuses and ledger counts must match, and starting a new
runtime must not resend the already-committed command.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402


def _default_dsn() -> str:
    dsn = os.getenv("POSTGRES_CONNECTION_STRING", "").strip()
    if not dsn:
        raise SystemExit("POSTGRES_DSN_NOT_CONFIGURED")
    return dsn


async def _create_schema(dsn: str, schema: str) -> None:
    connection = await asyncpg.connect(dsn, timeout=10)
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await connection.close()


async def _drop_schema(dsn: str, schema: str) -> None:
    connection = await asyncpg.connect(dsn, timeout=10)
    try:
        await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        await connection.close()


def _child_env(base: dict[str, str], **values: str) -> dict[str, str]:
    env = dict(base)
    env.update(values)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _wait_for(path: Path, timeout_seconds: float, process: subprocess.Popen) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if path.is_file():
            return
        if process.poll() is not None:
            raise RuntimeError(f"writer exited before readiness: {process.returncode}")
        time.sleep(0.1)
    raise TimeoutError("writer readiness timeout")


async def run(dsn: str, output: Path, keep_schema: bool) -> dict:
    schema = "qts_crash_" + uuid.uuid4().hex[:12]
    await _create_schema(dsn, schema)
    started_at_ms = int(time.time() * 1000)
    evidence: dict = {
        "drill": "process_crash_durability",
        "schema": schema,
        "dsn_identity": {
            "host": dsn.split("@")[-1].split("/")[0],
            "database": dsn.rsplit("/", 1)[-1],
        },
        "started_at_ms": started_at_ms,
    }
    with tempfile.TemporaryDirectory(prefix="qts-crash-") as temporary:
        directory = Path(temporary)
        ready_file = directory / "ready.json"
        snapshot_file = directory / "before.json"
        result_file = directory / "after.json"
        env = _child_env(
            os.environ,
            POSTGRES_CONNECTION_STRING=dsn,
            QTS_DRILL_SCHEMA=schema,
            QTS_DRILL_READY_FILE=str(ready_file),
            QTS_DRILL_SNAPSHOT_FILE=str(snapshot_file),
            QTS_DRILL_RESULT_FILE=str(result_file),
            QTS_DRILL_MODE="writer",
        )
        writer = subprocess.Popen(
            [sys.executable, "-m", "trader.ops.crash_durability"],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        evidence["writer_pid"] = writer.pid
        try:
            _wait_for(ready_file, 60, writer)
            evidence["committed_at_ms"] = int(time.time() * 1000)
            before = json.loads(snapshot_file.read_text(encoding="utf-8"))
            evidence["before"] = {
                "counts": before["facts"]["counts"],
                "digests": before["facts"]["digests"],
                "commands": before["commands"],
                "execution_keys": before["execution_keys"],
                "ledger_entries": before["ledger_entries"],
                "sent_count": before["sent_count"],
            }
            # A hard kill, not a graceful shutdown: no runtime cleanup runs.
            writer.kill()
            crash_exit = writer.wait(timeout=30)
            evidence["crash_exit_code"] = crash_exit
            evidence["killed_at_ms"] = int(time.time() * 1000)
        finally:
            if writer.poll() is None:
                writer.kill()
                writer.wait(timeout=30)

        verify_env = _child_env(
            os.environ,
            POSTGRES_CONNECTION_STRING=dsn,
            QTS_DRILL_SCHEMA=schema,
            QTS_DRILL_SNAPSHOT_FILE=str(snapshot_file),
            QTS_DRILL_RESULT_FILE=str(result_file),
            QTS_DRILL_MODE="verifier",
        )
        verifier = subprocess.run(
            [sys.executable, "-m", "trader.ops.crash_durability"],
            cwd=str(ROOT),
            env=verify_env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        evidence["verifier_exit_code"] = verifier.returncode
        evidence["verifier_stdout"] = verifier.stdout[-4000:]
        evidence["verifier_stderr"] = verifier.stderr[-4000:]
        if result_file.is_file():
            evidence["after"] = json.loads(result_file.read_text(encoding="utf-8"))
        evidence["completed_at_ms"] = int(time.time() * 1000)
        evidence["passed"] = bool(
            evidence.get("after", {}).get("passed")
            and evidence["after"].get("resend_count") == 0
            and verifier.returncode == 0
        )

    if not keep_schema and evidence.get("passed"):
        await _drop_schema(dsn, schema)
        evidence["schema_dropped"] = True
    else:
        evidence["schema_retained"] = schema
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"passed": evidence["passed"], "output": str(output)}, sort_keys=True))
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description="QTS crash durability drill")
    parser.add_argument("--dsn", default=_default_dsn())
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/foundation/phase5/evidence/crash-durability.json",
    )
    parser.add_argument("--keep-schema", action="store_true")
    args = parser.parse_args()
    evidence = asyncio.run(run(args.dsn, args.output, args.keep_schema))
    raise SystemExit(0 if evidence["passed"] else 1)


if __name__ == "__main__":
    main()
