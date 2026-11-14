"""Run PostgreSQL backups to a configured S3-compatible target."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trader.ops.backup import (  # noqa: E402
    BackupConfig,
    BackupError,
    ContainerPgDumpRunner,
    LocalPgDumpRunner,
    backup_loop,
    backup_once,
    summarize_rpo,
)
from trader.ops.s3 import S3Config, S3TargetError  # noqa: E402


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


async def run(args) -> dict:
    s3_config = None
    try:
        s3_config = S3Config.from_env()
    except S3TargetError as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, sort_keys=True))
        return {"ok": False, "records": [], "reason": str(exc)}
    config = BackupConfig(
        dsn=args.dsn,
        output_dir=args.output_dir,
        interval_seconds=args.interval_seconds,
        s3=s3_config,
        off_host_attested=args.off_host_attested,
        attested_by=args.attested_by,
        target_kind=args.target_kind,
        transport=args.transport,
        remote_host=args.remote_host,
        ssh_host_key_verified=args.ssh_host_key_verified,
    )
    runner = (
        ContainerPgDumpRunner(
            args.pg_container,
            database=args.pg_database,
            user=args.pg_user,
        )
        if args.pg_container
        else LocalPgDumpRunner(config.dsn)
    )
    if args.loop:
        records = await backup_loop(config, max_runs=args.max_runs, dump_runner=runner)
    else:
        records = [await backup_once(config, dump_runner=runner)]
    summary = summarize_rpo(args.output_dir / "backup-journal.jsonl", args.interval_seconds)
    return {
        "ok": all(record.get("status") == "succeeded" for record in records),
        "records": records,
        "rpo": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="QTS PostgreSQL backup runner")
    parser.add_argument("--dsn", default=_default_dsn())
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--max-runs", type=int, default=None)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts/foundation/phase5/evidence/backup",
    )
    parser.add_argument("--pg-container", default="")
    parser.add_argument("--pg-database", default="trading")
    parser.add_argument("--pg-user", default="trader")
    parser.add_argument(
        "--off-host-attested",
        action="store_true",
        default=os.getenv("QTS_BACKUP_OFF_HOST_ATTESTED", "").lower() in {"1", "true", "yes", "on"},
    )
    parser.add_argument("--attested-by", default=os.getenv("QTS_BACKUP_ATTESTED_BY", ""))
    parser.add_argument(
        "--target-kind", default=os.getenv("QTS_BACKUP_TARGET_KIND", "s3_compatible")
    )
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    if not args.loop and not args.once:
        args.once = True
    result = asyncio.run(run(args))
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
