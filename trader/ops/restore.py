"""Restore drill: load a backup into a separate task-owned database and
verify the reloaded economic facts against the manifest of the same snapshot.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import asyncpg

from trader.ops.backup import (
    CONTAINER_RE,
    BackupError,
    _pg_env,
    account_invariants,
    collect_fact_snapshot,
    database_identity,
    load_manifest,
    parse_dsn,
)
from trader.ops.s3 import S3Config, S3Target

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
RESERVED_DATABASES = frozenset({"postgres", "template0", "template1"})


class RestoreError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class LocalPgRestoreRunner:
    def __init__(self, dsn: str, target_database: str):
        self.dsn = dsn
        self.target_database = target_database

    async def restore(self, dump_path: Path) -> None:
        binary = shutil.which("pg_restore")
        if binary is None:
            raise RestoreError("PG_RESTORE_UNAVAILABLE")
        env = _pg_env(self.dsn)
        env["PGDATABASE"] = self.target_database
        process = await asyncio.create_subprocess_exec(
            binary,
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            str(dump_path),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, _ = await process.communicate()
        if process.returncode != 0:
            raise RestoreError(f"PG_RESTORE_FAILED:{process.returncode}")


class ContainerPgRestoreRunner:
    def __init__(
        self, container: str, target_database: str, user: str = "trader", source_db: str = ""
    ):
        if not CONTAINER_RE.match(container):
            raise RestoreError("PG_CONTAINER_NAME_INVALID")
        if not IDENTIFIER_RE.match(target_database) or not IDENTIFIER_RE.match(user):
            raise RestoreError("PG_CONTAINER_IDENTIFIER_INVALID")
        self.container = container
        self.target_database = target_database
        self.user = user
        self.source_db = source_db

    async def restore(self, dump_path: Path) -> None:
        script = (
            'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore --no-owner --no-privileges '
            "--exit-on-error "
            f"--username={self.user} --dbname={self.target_database}"
        )
        process = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "-i",
            self.container,
            "sh",
            "-c",
            script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate(dump_path.read_bytes())
        if process.returncode != 0:
            raise RestoreError(f"PG_RESTORE_FAILED:{process.returncode}")


@dataclass(frozen=True)
class RestoreConfig:
    dsn: str
    manifest_path: Path
    target_database: str
    output_dir: Path
    drop_after: bool = False


def _admin_dsn(dsn: str) -> str:
    parts = parse_dsn(dsn)
    return (
        f"postgresql://{quote(parts['user'], safe='')}:{quote(parts['password'], safe='')}@"
        f"{parts['host']}:{parts['port']}/postgres"
    )


def _target_dsn(dsn: str, target_database: str) -> str:
    parts = parse_dsn(dsn)
    return (
        f"postgresql://{quote(parts['user'], safe='')}:{quote(parts['password'], safe='')}@"
        f"{parts['host']}:{parts['port']}/{target_database}"
    )


async def _create_database(dsn: str, target_database: str) -> None:
    if not IDENTIFIER_RE.match(target_database) or target_database in RESERVED_DATABASES:
        raise RestoreError("RESTORE_TARGET_DATABASE_INVALID")
    connection = await asyncpg.connect(_admin_dsn(dsn), timeout=10)
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname=$1", target_database
        )
        if exists:
            raise RestoreError("RESTORE_TARGET_EXISTS")
        await connection.execute(f'CREATE DATABASE "{target_database}"')
    finally:
        await connection.close()


async def _drop_database(dsn: str, target_database: str) -> None:
    connection = await asyncpg.connect(_admin_dsn(dsn), timeout=10)
    try:
        await connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=$1 AND pid <> pg_backend_pid()",
            target_database,
        )
        await connection.execute(f'DROP DATABASE IF EXISTS "{target_database}"')
    finally:
        await connection.close()


def _write_evidence(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def restore_and_verify(
    config: RestoreConfig,
    *,
    restore_runner: Any | None = None,
    s3_target: S3Target | None = None,
    s3_config: S3Config | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(config.manifest_path)
    backup = manifest["backup"]
    source = backup.get("source_database") or database_identity(config.dsn)
    if config.target_database == source.get("database"):
        raise RestoreError("RESTORE_TARGET_SAME_AS_SOURCE")
    target = s3_target or S3Target(s3_config or S3Config.from_env())
    runner = restore_runner or LocalPgRestoreRunner(config.dsn, config.target_database)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    drill_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + os.urandom(4).hex()
    dump_path = output_dir / f"qts-restore-{drill_id}.dump"
    started_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    created = False
    evidence: dict[str, Any] = {}
    try:
        downloaded = target.download_and_hash(backup["object_key"], dump_path)
        if downloaded["sha256"] != backup.get("sha256"):
            raise RestoreError("RESTORE_OBJECT_DIGEST_MISMATCH")

        await _create_database(config.dsn, config.target_database)
        created = True
        await runner.restore(dump_path)

        conn = await asyncpg.connect(_target_dsn(config.dsn, config.target_database), timeout=10)
        try:
            facts = await collect_fact_snapshot(conn)
            invariants = await account_invariants(conn)
        finally:
            await conn.close()

        expected = backup.get("facts") or {}
        facts_match = facts.get("counts") == expected.get("counts") and facts.get(
            "digests"
        ) == expected.get("digests")
        verified = bool(facts_match and invariants.get("ok"))
        completed_at_ms = int(time.time() * 1000)
        evidence = {
            "schema_version": 1,
            "drill_id": drill_id,
            "backup": {
                "completed_at_ms": backup.get("completed_at_ms"),
                "cadence_seconds": backup.get("cadence_seconds"),
                "sha256": backup.get("sha256"),
                "object_key": backup.get("object_key"),
                "source_database": source,
                "target": backup.get("target") or {},
            },
            "restore": {
                "drill_id": drill_id,
                "started_at_ms": started_at_ms,
                "completed_at_ms": completed_at_ms,
                "rto_seconds": max(0, (completed_at_ms - started_at_ms) // 1000),
                "backup_sha256": backup.get("sha256"),
                "verified": verified,
                "source_database": source,
                "target_database": {
                    "host": parse_dsn(config.dsn)["host"],
                    "database": config.target_database,
                },
                "checks": {
                    "facts_match": facts_match,
                    "invariants_ok": bool(invariants.get("ok")),
                    "actual": facts,
                    "expected": expected,
                    "invariants": invariants,
                },
            },
        }
        _write_evidence(output_dir / "latest-restore-evidence.json", evidence)
        if not verified:
            raise RestoreError(
                "RESTORE_FACTS_MISMATCH" if not facts_match else "RESTORE_INVARIANT_FAILED"
            )
        return evidence
    finally:
        try:
            dump_path.unlink()
        except FileNotFoundError:
            pass
        if created and config.drop_after:
            try:
                await _drop_database(config.dsn, config.target_database)
            except Exception:
                pass


def write_dr_evidence(evidence: dict[str, Any], path: str | os.PathLike[str]) -> None:
    """Persist the combined backup+restore evidence consumed by the DR gate."""
    _write_evidence(Path(path), evidence)


def verify_evidence_for_gate(evidence: dict[str, Any], *, now_ms: int | None = None) -> dict:
    from trader.ops.dr_evidence import evaluate_dr_evidence

    result = evaluate_dr_evidence(evidence, now_ms=now_ms)
    return result.as_dict()


__all__ = [
    "RestoreConfig",
    "RestoreError",
    "restore_and_verify",
    "write_dr_evidence",
    "verify_evidence_for_gate",
    "LocalPgRestoreRunner",
    "ContainerPgRestoreRunner",
    "BackupError",
]
