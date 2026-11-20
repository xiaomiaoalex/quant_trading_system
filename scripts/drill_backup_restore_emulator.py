"""Backup/restore fault drill against a local S3 emulator.

This proves the backup/restore machinery end to end with synthetic facts and a
task-owned database. The emulator runs on the same host, so the resulting
evidence is deliberately marked ``local_emulator``/``off_host=false`` and the
DR gate must reject it. It is not evidence for acceptance item 5.6/5.7.
"""

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

import asyncpg  # noqa: E402

from trader.ops.backup import (  # noqa: E402
    BackupConfig,
    ContainerPgDumpRunner,
    backup_once,
    parse_dsn,
    summarize_rpo,
)
from trader.ops.crash_durability import write_committed_facts  # noqa: E402
from trader.ops.dr_evidence import evaluate_dr_evidence  # noqa: E402
from trader.ops.restore import (  # noqa: E402
    ContainerPgRestoreRunner,
    RestoreConfig,
    restore_and_verify,
)
from trader.ops.s3 import S3Config  # noqa: E402
from trader.ops.s3_emulator import S3Emulator  # noqa: E402


def _base_dsn() -> str:
    dsn = os.getenv("POSTGRES_CONNECTION_STRING", "").strip()
    if not dsn:
        raise SystemExit("POSTGRES_DSN_NOT_CONFIGURED")
    return dsn


def _dsn(database: str) -> str:
    from urllib.parse import quote

    parts = parse_dsn(_base_dsn())
    return (
        f"postgresql://{quote(parts['user'], safe='')}:{quote(parts['password'], safe='')}"
        f"@{parts['host']}:{parts['port']}/{database}"
    )


async def _admin():
    return await asyncpg.connect(_dsn("postgres"), timeout=10)


async def _create_database(suffix: str) -> str:
    name = "qts_p5_bk_" + suffix
    connection = await _admin()
    try:
        await connection.execute(f'CREATE DATABASE "{name}"')
    finally:
        await connection.close()
    return name


async def _drop_database(name: str) -> None:
    connection = await _admin()
    try:
        await connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=$1 AND pid <> pg_backend_pid()",
            name,
        )
        await connection.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await connection.close()


def _create_bucket(config: S3Config) -> None:
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
        region_name=config.region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    client.create_bucket(Bucket=config.bucket)


async def run(args) -> dict:
    suffix = uuid.uuid4().hex[:10]
    source_db = "qts_p5_bk_" + suffix
    restore_db = "qts_p5_rs_" + suffix
    emulator = S3Emulator().start()
    s3_config = S3Config(
        endpoint_url=emulator.endpoint_url,
        bucket="qts-phase5-drill",
        prefix="emulator/backups",
        access_key="local-emulator",
        secret_key="local-emulator-secret",
    )
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence: dict = {
        "drill": "local_s3_emulator",
        "started_at_ms": int(time.time() * 1000),
        "source_database": source_db,
        "restore_database": restore_db,
        "emulator": "in_process_local_s3",
        "emulator_endpoint_host": "127.0.0.1",
        "target_kind": "local_emulator",
        "off_host": False,
    }
    created_source = False
    try:
        _create_bucket(s3_config)

        await _create_database(suffix)
        created_source = True
        source_dsn = _dsn(source_db)
        written = await write_committed_facts("", dsn=source_dsn)
        evidence["source_facts"] = written["facts"]
        evidence["source_invariants"] = written["invariants"]

        backup_config = BackupConfig(
            dsn=source_dsn,
            output_dir=output_dir,
            interval_seconds=args.interval_seconds,
            s3=s3_config,
            off_host_attested=False,
            attested_by="",
            target_kind="local_emulator",
        )
        dump_runner = ContainerPgDumpRunner(
            args.pg_container, database=source_db, user=args.pg_user
        )
        first = await backup_once(backup_config, dump_runner=dump_runner)
        second = await backup_once(backup_config, dump_runner=dump_runner)
        evidence["backups"] = [
            {
                "status": record.get("status"),
                "reason": record.get("reason"),
                "object_key": (record.get("backup") or {}).get("object_key"),
                "sha256": (record.get("backup") or {}).get("sha256"),
                "completed_at_ms": record.get("completed_at_ms"),
                "receipt": (record.get("backup") or {}).get("receipt"),
            }
            for record in (first, second)
        ]
        if first.get("status") != "succeeded" or second.get("status") != "succeeded":
            evidence["passed"] = False
            evidence["reason"] = "BACKUP_DID_NOT_SUCCEED"
            return evidence
        evidence["rpo"] = summarize_rpo(output_dir / "backup-journal.jsonl", args.interval_seconds)
        evidence["rpo_note"] = (
            "Local emulator runs are back-to-back; this proves receipt/readback and repeatability, "
            "not an off-host 5-minute production cadence."
        )

        restore_config = RestoreConfig(
            dsn=source_dsn,
            manifest_path=output_dir / "latest-backup.json",
            target_database=restore_db,
            output_dir=output_dir / "restore",
            drop_after=True,
        )
        restore_runner = ContainerPgRestoreRunner(
            args.pg_container, restore_db, user=args.pg_user, source_db=source_db
        )
        restore_evidence = await restore_and_verify(
            restore_config, restore_runner=restore_runner, s3_config=s3_config
        )
        evidence["restore"] = {
            "started_at_ms": restore_evidence["restore"]["started_at_ms"],
            "completed_at_ms": restore_evidence["restore"]["completed_at_ms"],
            "rto_seconds": restore_evidence["restore"]["rto_seconds"],
            "verified": restore_evidence["restore"]["verified"],
            "checks": {
                "facts_match": restore_evidence["restore"]["checks"]["facts_match"],
                "invariants_ok": restore_evidence["restore"]["checks"]["invariants_ok"],
            },
        }
        gate = evaluate_dr_evidence(restore_evidence)
        evidence["dr_gate"] = gate.as_dict()
        evidence["gate_rejected_local_target"] = (
            gate.ready is False and "DR_TARGET_NOT_VERIFIED_OFF_HOST" in gate.reasons
        )
        evidence["passed"] = bool(
            restore_evidence["restore"]["verified"] and evidence["gate_rejected_local_target"]
        )
        return evidence
    except Exception as exc:
        evidence["passed"] = False
        evidence["reason"] = f"DRILL_FAILED:{type(exc).__name__}"
        evidence["error"] = str(exc)[-1000:]
        return evidence
    finally:
        if created_source:
            await _drop_database(source_db)
            evidence["source_database_dropped"] = True
        await _drop_database(restore_db)
        evidence["emulator_snapshot"] = emulator.snapshot()
        emulator.stop()
        evidence["emulator_stopped"] = True
        evidence["completed_at_ms"] = int(time.time() * 1000)
        (output_dir / "backup-restore-emulator.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="QTS local S3 backup/restore drill")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts/foundation/phase5/evidence/backup-restore",
    )
    parser.add_argument("--pg-container", default="qts-postgres")
    parser.add_argument("--pg-user", default="trader")
    parser.add_argument("--interval-seconds", type=int, default=300)
    args = parser.parse_args()
    evidence = asyncio.run(run(args))
    print(json.dumps({"passed": evidence.get("passed"), "reason": evidence.get("reason")}))
    raise SystemExit(0 if evidence.get("passed") else 1)


if __name__ == "__main__":
    main()
