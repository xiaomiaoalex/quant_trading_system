"""Restore a backup into a separate task-owned database and verify its facts."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trader.ops.restore import (  # noqa: E402
    ContainerPgRestoreRunner,
    LocalPgRestoreRunner,
    RestoreConfig,
    RestoreError,
    restore_and_verify,
    write_dr_evidence,
)


def _default_dsn() -> str:
    dsn = os.getenv("POSTGRES_CONNECTION_STRING", "").strip()
    if dsn:
        return dsn
    from urllib.parse import quote

    host = os.getenv("POSTGRES_HOST", "").strip()
    database = os.getenv("POSTGRES_DB", "").strip()
    user = os.getenv("POSTGRES_USER", "").strip()
    password = os.getenv("POSTGRES_PASSWORD", "")
    if not host or not database or not user or not password:
        raise SystemExit("POSTGRES_DSN_NOT_CONFIGURED")
    port = os.getenv("POSTGRES_PORT", "5432").strip()
    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{database}"
    )


async def run(args) -> tuple[dict, int]:
    target_database = args.target_database or ("qts_restore_" + uuid.uuid4().hex[:10])
    config = RestoreConfig(
        dsn=args.dsn,
        manifest_path=args.manifest,
        target_database=target_database,
        output_dir=args.output_dir,
        drop_after=args.drop_after,
    )
    runner = (
        ContainerPgRestoreRunner(args.pg_container, target_database, source_db=args.pg_database)
        if args.pg_container
        else LocalPgRestoreRunner(config.dsn, target_database)
    )
    started = int(time.time() * 1000)
    try:
        evidence = await restore_and_verify(config, restore_runner=runner)
        if args.dr_evidence:
            write_dr_evidence(evidence, args.dr_evidence)
        return evidence, 0
    except (RestoreError, Exception) as exc:
        reason = getattr(exc, "reason", f"RESTORE_FAILED:{type(exc).__name__}")
        failure = {
            "schema_version": 1,
            "drill_id": "failed-" + uuid.uuid4().hex[:12],
            "restore": {
                "started_at_ms": started,
                "completed_at_ms": int(time.time() * 1000),
                "target_database": {
                    "host": args.dsn.split("@")[-1].split("/")[0],
                    "database": target_database,
                },
                "verified": False,
                "reason": reason,
            },
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "latest-restore-failure.json").write_text(
            json.dumps(failure, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return failure, 1


def main() -> None:
    parser = argparse.ArgumentParser(description="QTS PostgreSQL restore drill")
    parser.add_argument("--dsn", default=_default_dsn())
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "artifacts/foundation/phase5/evidence/backup/latest-backup.json",
    )
    parser.add_argument("--target-database", default="")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts/foundation/phase5/evidence/restore",
    )
    parser.add_argument("--pg-container", default="")
    parser.add_argument("--pg-database", default="trading")
    parser.add_argument("--drop-after", action="store_true")
    parser.add_argument("--dr-evidence", type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    evidence, code = asyncio.run(run(args))
    rendered = json.dumps(evidence, indent=2, sort_keys=True)
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
