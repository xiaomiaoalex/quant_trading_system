"""PostgreSQL backup with verifiable object-store receipt.

A backup is only recorded as successful after the dump was taken from an
exported snapshot, uploaded, read back from the S3-compatible target and
hashed again. The manifest carries the fact digests of that same snapshot, so
a later restore can prove it reloaded the exact economic facts instead of only
matching row counts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

import asyncpg

from trader.ops.dr_evidence import DEFAULT_RPO_SECONDS
from trader.ops.s3 import S3Config, S3Target, S3TargetError
from trader.trading.store import decode_state

SNAPSHOT_ID_RE = re.compile(r"^[0-9A-Fa-f-]{8,64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")

FACT_TABLES: tuple[tuple[str, str], ...] = (
    (
        "trading_accounts",
        "SELECT account_id, state FROM trading_accounts ORDER BY account_id",
    ),
    (
        "trading_commands",
        "SELECT account_id, command_id, cl_ord_id, kind, status, payload_hash, data "
        "FROM trading_commands ORDER BY account_id, command_id",
    ),
    (
        "trading_executions",
        "SELECT execution_key, account_id, cl_ord_id, data "
        "FROM trading_executions ORDER BY execution_key",
    ),
    (
        "trading_ledger",
        "SELECT account_id, entry_id, data FROM trading_ledger ORDER BY account_id, entry_id",
    ),
    (
        "trading_events",
        "SELECT account_id, event_id, data FROM trading_events ORDER BY account_id, event_id",
    ),
    (
        "trading_outbox",
        "SELECT account_id, event_id, data, delivered, seq "
        "FROM trading_outbox ORDER BY account_id, event_id",
    ),
)


class BackupError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def parse_dsn(dsn: str) -> dict[str, str]:
    parts = urlsplit(dsn)
    if parts.scheme not in {"postgres", "postgresql"}:
        raise BackupError("POSTGRES_DSN_INVALID")
    database = parts.path.lstrip("/")
    if not database:
        raise BackupError("POSTGRES_DSN_DATABASE_MISSING")
    return {
        "host": parts.hostname or "127.0.0.1",
        "port": str(parts.port or 5432),
        "user": unquote(parts.username or "trader"),
        "password": unquote(parts.password or ""),
        "database": database,
    }


def database_identity(dsn: str) -> dict[str, str]:
    parsed = parse_dsn(dsn)
    return {"host": parsed["host"], "database": parsed["database"]}


def _redact(text: str, dsn: str) -> str:
    try:
        password = parse_dsn(dsn)["password"]
    except BackupError:
        password = ""
    if password:
        text = text.replace(password, "***")
    return text[-2000:]


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    if isinstance(value, Decimal):
        return str(value)
    return value


def _normalise_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []
    for row in rows:
        item = {key: _json_value(value) for key, value in dict(row).items()}
        normalised.append(item)
    return normalised


def digest_rows(rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def collect_fact_snapshot(conn) -> dict[str, Any]:
    """Canonical digests and counts for every economic-fact table."""
    counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    for table, query in FACT_TABLES:
        rows = _normalise_rows(await conn.fetch(query))
        counts[table] = len(rows)
        digests[table] = digest_rows(rows)
    return {"counts": counts, "digests": digests}


async def account_invariants(conn) -> dict[str, Any]:
    """Verify book/account conservation on the rows that will be backed up."""
    violations: list[str] = []
    rows = await conn.fetch("SELECT account_id, state FROM trading_accounts ORDER BY account_id")
    accounts = 0
    for row in rows:
        accounts += 1
        account_id = str(row["account_id"])
        raw = _json_value(row["state"])
        if not isinstance(raw, dict):
            violations.append(f"{account_id}:ACCOUNT_STATE_NOT_OBJECT")
            continue
        try:
            state = decode_state(raw)
        except Exception:
            violations.append(f"{account_id}:ACCOUNT_STATE_DECODE_FAILED")
            continue
        if state.books:
            assets = set(state.balances) | set(state.positions)
            for book in state.books.values():
                assets |= set(book.balances) | set(book.positions)
            for asset in sorted(assets):
                total = state.balances.get(asset, Decimal(0))
                allocated = sum(
                    (book.balances.get(asset, Decimal(0)) for book in state.books.values()),
                    Decimal(0),
                )
                if total != allocated:
                    violations.append(f"{account_id}:BALANCE_CONSERVATION:{asset}")
                position = state.positions.get(asset, Decimal(0))
                book_position = sum(
                    (book.positions.get(asset, Decimal(0)) for book in state.books.values()),
                    Decimal(0),
                )
                if position != book_position:
                    violations.append(f"{account_id}:POSITION_CONSERVATION:{asset}")
        elif any(value != 0 for value in state.balances.values()):
            violations.append(f"{account_id}:BOOK_LEDGER_MISSING")
        for asset, value in state.balances.items():
            if value < 0:
                violations.append(f"{account_id}:NEGATIVE_BALANCE:{asset}")
        for book in state.books.values():
            for asset, value in book.balances.items():
                if value < 0:
                    violations.append(f"{account_id}:NEGATIVE_BOOK_BALANCE:{book.book_id}:{asset}")
            for hold in book.holds.values():
                if hold.amount < 0:
                    violations.append(f"{account_id}:NEGATIVE_BOOK_HOLD:{book.book_id}")
        for cl_ord_id, hold in state.holds.items():
            if hold.amount < 0:
                violations.append(f"{account_id}:NEGATIVE_HOLD:{cl_ord_id}")
    return {"ok": not violations, "accounts": accounts, "violations": violations[:50]}


def _pg_env(dsn: str) -> dict[str, str]:
    parsed = parse_dsn(dsn)
    env = dict(os.environ)
    env.update(
        {
            "PGHOST": parsed["host"],
            "PGPORT": parsed["port"],
            "PGUSER": parsed["user"],
            "PGPASSWORD": parsed["password"],
            "PGDATABASE": parsed["database"],
        }
    )
    return env


class LocalPgDumpRunner:
    """Run the image's ``pg_dump`` against an already-exported snapshot."""

    def __init__(self, dsn: str):
        self.dsn = dsn

    async def dump(self, snapshot_id: str, output_path: Path) -> None:
        binary = shutil.which("pg_dump")
        if binary is None:
            raise BackupError("PG_DUMP_UNAVAILABLE")
        if not SNAPSHOT_ID_RE.match(snapshot_id):
            raise BackupError("PG_DUMP_SNAPSHOT_INVALID")
        process = await asyncio.create_subprocess_exec(
            binary,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--snapshot={snapshot_id}",
            f"--file={output_path}",
            env=_pg_env(self.dsn),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise BackupError(f"PG_DUMP_FAILED:{process.returncode}")


class ContainerPgDumpRunner:
    """Run ``pg_dump`` inside a task-owned PostgreSQL container.

    The password stays inside the container's own ``POSTGRES_PASSWORD``
    environment; it is never placed on a command line.
    """

    def __init__(self, container: str, database: str, user: str = "trader"):
        if not CONTAINER_RE.match(container):
            raise BackupError("PG_CONTAINER_NAME_INVALID")
        if not IDENTIFIER_RE.match(database) or not IDENTIFIER_RE.match(user):
            raise BackupError("PG_CONTAINER_IDENTIFIER_INVALID")
        self.container = container
        self.database = database
        self.user = user

    async def dump(self, snapshot_id: str, output_path: Path) -> None:
        if not SNAPSHOT_ID_RE.match(snapshot_id):
            raise BackupError("PG_DUMP_SNAPSHOT_INVALID")
        script = (
            'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump --format=custom --no-owner '
            "--no-privileges "
            f"--username={self.user} --dbname={self.database} "
            f"--snapshot={snapshot_id}"
        )
        process = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "-i",
            self.container,
            "sh",
            "-c",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise BackupError(f"PG_DUMP_FAILED:{process.returncode}")
        output_path.write_bytes(stdout)


@dataclass(frozen=True)
class BackupConfig:
    dsn: str
    output_dir: Path
    interval_seconds: int = DEFAULT_RPO_SECONDS
    s3: S3Config | None = None
    off_host_attested: bool = False
    attested_by: str = ""
    target_kind: str = "s3_compatible"
    transport: str = "direct"
    remote_host: str = ""
    ssh_host_key_verified: bool = False

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise BackupError("BACKUP_INTERVAL_INVALID")
        if self.interval_seconds > DEFAULT_RPO_SECONDS:
            raise BackupError("BACKUP_INTERVAL_EXCEEDS_RPO")
        if self.transport not in {"direct", "ssh_tunnel"}:
            raise BackupError("BACKUP_TRANSPORT_INVALID")

    def target_identity(self) -> dict[str, Any]:
        if self.s3 is None:
            return {"kind": self.target_kind, "endpoint_host": "", "bucket": ""}
        identity = self.s3.identity()
        if self.transport == "ssh_tunnel":
            # The client endpoint is a local SSH forward. Evidence records the
            # actual remote storage host and tunnel endpoint separately so a
            # loopback URL is never itself treated as the off-host target.
            identity.update(
                {
                    "endpoint_host": self.remote_host or identity.get("endpoint_host", ""),
                    "remote_host": self.remote_host,
                    "tunnel_endpoint_host": self.s3.endpoint_host,
                    "transport": "ssh_tunnel",
                    "ssh_host_key_verified": bool(self.ssh_host_key_verified),
                }
            )
        else:
            identity["transport"] = "direct"
        identity.update(
            {
                "kind": self.target_kind,
                "off_host": bool(self.off_host_attested),
                "identity_verified": bool(
                    self.off_host_attested
                    and self.attested_by
                    and (self.transport != "ssh_tunnel" or self.ssh_host_key_verified)
                ),
                "attested_by": self.attested_by,
            }
        )
        return identity


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _append_journal(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


def _failure_record(config: BackupConfig, run_id: str, started_at_ms: int, reason: str) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": "failed",
        "reason": reason,
        "started_at_ms": started_at_ms,
        "completed_at_ms": int(time.time() * 1000),
        "cadence_seconds": config.interval_seconds,
        "source_database": database_identity(config.dsn),
        "target": config.target_identity(),
    }


async def backup_once(
    config: BackupConfig,
    *,
    dump_runner: Any | None = None,
    s3_target: S3Target | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + os.urandom(4).hex()
    started_at_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    dump_path = output_dir / f"qts-{run_id}.dump"
    verify_path = output_dir / f"qts-{run_id}.verify.dump"
    journal_path = output_dir / "backup-journal.jsonl"
    latest_path = output_dir / "latest-backup.json"

    if config.s3 is None:
        record = _failure_record(config, run_id, started_at_ms, "S3_TARGET_NOT_CONFIGURED")
        _write_json(latest_path, record)
        _append_journal(journal_path, record)
        return record

    target = s3_target or S3Target(config.s3)
    runner = dump_runner or LocalPgDumpRunner(config.dsn)
    object_key = f"{config.s3.prefix}/qts-{run_id}.dump"

    conn = None
    transaction = None
    try:
        conn = await asyncpg.connect(config.dsn, timeout=10)
        transaction = conn.transaction(isolation="repeatable_read")
        await transaction.start()
        snapshot_id = await conn.fetchval("SELECT pg_export_snapshot()")
        facts = await collect_fact_snapshot(conn)
        invariants = await account_invariants(conn)
        if not invariants["ok"]:
            raise BackupError("BACKUP_INVARIANT_FAILED")

        await runner.dump(snapshot_id, dump_path)
        await transaction.commit()
        transaction = None

        payload = dump_path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        size_bytes = len(payload)
        if size_bytes == 0:
            raise BackupError("PG_DUMP_EMPTY")

        target.upload(
            dump_path,
            object_key,
            metadata={
                "sha256": digest,
                "run-id": run_id,
                "source-database": database_identity(config.dsn)["database"],
                "created-at-ms": str(started_at_ms),
            },
        )
        head = target.head(object_key)
        if head["content_length"] != size_bytes:
            raise BackupError("S3_RECEIPT_SIZE_MISMATCH")
        if head["metadata"].get("sha256", "").lower() != digest:
            raise BackupError("S3_RECEIPT_DIGEST_MISMATCH")
        downloaded = target.download_and_hash(object_key, verify_path)
        if downloaded["sha256"] != digest or downloaded["size_bytes"] != size_bytes:
            raise BackupError("S3_READBACK_DIGEST_MISMATCH")

        completed_at_ms = int(time.time() * 1000)
        record = {
            "schema_version": 1,
            "run_id": run_id,
            "status": "succeeded",
            "started_at_ms": started_at_ms,
            "completed_at_ms": completed_at_ms,
            "duration_seconds": max(0, (completed_at_ms - started_at_ms) // 1000),
            "cadence_seconds": config.interval_seconds,
            "source_database": database_identity(config.dsn),
            "facts": facts,
            "invariants": invariants,
            "backup": {
                "completed_at_ms": completed_at_ms,
                "cadence_seconds": config.interval_seconds,
                "sha256": digest,
                "size_bytes": size_bytes,
                "object_key": object_key,
                "source_database": database_identity(config.dsn),
                "target": config.target_identity(),
                "receipt": {
                    "head_content_length": head["content_length"],
                    "head_metadata_sha256": head["metadata"].get("sha256", ""),
                    "readback_sha256": downloaded["sha256"],
                    "verified_at_ms": downloaded["verified_at_ms"],
                },
                "facts": facts,
            },
        }
        _write_json(latest_path, record)
        _append_journal(journal_path, record)
        return record
    except BackupError as exc:
        record = _failure_record(config, run_id, started_at_ms, exc.reason)
        _write_json(latest_path, record)
        _append_journal(journal_path, record)
        return record
    except S3TargetError as exc:
        record = _failure_record(config, run_id, started_at_ms, str(exc))
        _write_json(latest_path, record)
        _append_journal(journal_path, record)
        return record
    except Exception as exc:
        record = _failure_record(
            config,
            run_id,
            started_at_ms,
            f"BACKUP_FAILED:{type(exc).__name__}",
        )
        record["error_detail"] = _redact(str(exc), config.dsn)
        _write_json(latest_path, record)
        _append_journal(journal_path, record)
        return record
    finally:
        if transaction is not None:
            try:
                await transaction.rollback()
            except Exception:
                pass
        if conn is not None:
            await conn.close()
        for path in (dump_path, verify_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


async def backup_loop(
    config: BackupConfig,
    *,
    max_runs: int | None = None,
    stop_event: asyncio.Event | None = None,
    dump_runner: Any | None = None,
    s3_target: S3Target | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    stop = stop_event or asyncio.Event()
    # Fixed-rate scheduling: the next run is anchored to the previous start,
    # not to its completion, so a slow dump does not silently extend the RPO.
    next_start = time.monotonic()
    while not stop.is_set():
        records.append(await backup_once(config, dump_runner=dump_runner, s3_target=s3_target))
        if max_runs is not None and len(records) >= max_runs:
            break
        next_start += config.interval_seconds
        delay = next_start - time.monotonic()
        if delay <= 0:
            next_start = time.monotonic()
            delay = 0.0
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            continue
    return records


def load_manifest(path: str | os.PathLike[str]) -> dict[str, Any]:
    candidate = Path(path)
    if not candidate.is_file():
        raise BackupError("BACKUP_MANIFEST_MISSING")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BackupError("BACKUP_MANIFEST_INVALID") from None
    if payload.get("status") != "succeeded" or not isinstance(payload.get("backup"), dict):
        raise BackupError("BACKUP_MANIFEST_NOT_SUCCESSFUL")
    return payload


def summarize_rpo(
    journal_path: str | os.PathLike[str],
    rpo_seconds: int,
    *,
    window_start_ms: int | None = None,
    window_end_ms: int | None = None,
) -> dict[str, Any]:
    """Compute real completed-backup intervals from the journal.

    Without a window the historical contract is kept: only the interval
    between adjacent successful completions is judged. With a window the RPO
    is bound to the observed window: a fresh verified backup must exist at or
    before the start, every interval inside the window must stay inside the
    RPO, and the last backup inside the window must still be fresh at the
    end. Completions after the window never cover a past gap.

    Every comparison is done in whole milliseconds; a 300_999 ms interval is
    not a 300 s interval.
    """
    path = Path(journal_path)
    completions: list[int] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("status") != "succeeded":
                continue
            backup = record.get("backup") or {}
            completed = backup.get("completed_at_ms")
            if isinstance(completed, int):
                completions.append(completed)
    completions.sort()
    rpo_ms = int(rpo_seconds) * 1000
    if window_start_ms is None or window_end_ms is None:
        gaps_ms = [max(0, later - earlier) for earlier, later in zip(completions, completions[1:])]
        gaps = [gap // 1000 for gap in gaps_ms]
        return {
            "rpo_seconds": rpo_seconds,
            "successful_backups": len(completions),
            "gaps_seconds": gaps,
            "gaps_ms": gaps_ms,
            "max_gap_seconds": max(gaps) if gaps else None,
            "max_gap_ms": max(gaps_ms) if gaps_ms else None,
            "rpo_satisfied": bool(gaps_ms) and max(gaps_ms) <= rpo_ms,
            "first_completed_at_ms": completions[0] if completions else None,
            "last_completed_at_ms": completions[-1] if completions else None,
            "window_start_ms": None,
            "window_end_ms": None,
            "head_age_ms": None,
            "tail_age_ms": None,
            "head_covered": None,
            "tail_covered": None,
        }

    start = int(window_start_ms)
    end = int(window_end_ms)
    before_start = [completed for completed in completions if completed <= start]
    head = before_start[-1] if before_start else None
    head_age = None if head is None else max(0, start - head)
    head_covered = head_age is not None and head_age <= rpo_ms
    inside = [completed for completed in completions if start < completed <= end]
    gaps_ms: list[int] = []
    previous = head
    for completed in inside:
        if previous is not None:
            gaps_ms.append(max(0, completed - previous))
        previous = completed
    last = inside[-1] if inside else head
    tail_age = None if last is None else max(0, end - last)
    tail_covered = tail_age is not None and tail_age <= rpo_ms
    gaps = [gap // 1000 for gap in gaps_ms]
    satisfied = bool(
        completions and head_covered and tail_covered and all(gap <= rpo_ms for gap in gaps_ms)
    )
    return {
        "rpo_seconds": rpo_seconds,
        "successful_backups": len(completions),
        "gaps_seconds": gaps,
        "gaps_ms": gaps_ms,
        "max_gap_seconds": max(gaps) if gaps else None,
        "max_gap_ms": max(gaps_ms) if gaps_ms else None,
        "rpo_satisfied": satisfied,
        "first_completed_at_ms": completions[0] if completions else None,
        "last_completed_at_ms": completions[-1] if completions else None,
        "window_start_ms": start,
        "window_end_ms": end,
        "head_age_ms": head_age,
        "tail_age_ms": tail_age,
        "head_covered": head_covered,
        "tail_covered": tail_covered,
    }
