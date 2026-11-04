"""Disaster-recovery evidence validation.

Phase-5 requires the trading runtime to stay non-READY when backup/restore
evidence is absent, stale, out-of-contract or not proven to target a different
machine. This module is intentionally pure apart from reading one JSON file, so
the same rules are used by Control, Execution and the drill scripts.

Evidence shape (schema_version 1)::

    {
      "schema_version": 1,
      "backup": {
        "completed_at_ms": 1760000000000,
        "cadence_seconds": 300,
        "sha256": "<64 hex>",
        "object_key": "qts/backups/....dump",
        "target": {
          "kind": "s3_compatible",
          "endpoint_host": "backup.example.internal",
          "bucket": "qts-backups",
          "off_host": true,
          "identity_verified": true,
          "attested_by": "operator-name"
        }
      },
      "restore": {
        "completed_at_ms": 1760000000000,
        "rto_seconds": 600,
        "backup_sha256": "<same as backup.sha256>",
        "verified": true,
        "source_database": {"host": "db-a", "database": "trading"},
        "target_database": {"host": "db-b", "database": "qts_restore_x"},
        "checks": {"commands": 12, ...}
      }
    }
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

SCHEMA_VERSION = 1
DEFAULT_RPO_SECONDS = 300
DEFAULT_MAX_RESTORE_AGE_SECONDS = 7 * 24 * 60 * 60
DEFAULT_MAX_RTO_SECONDS = 60 * 60

DR_EVIDENCE_PATH_NOT_CONFIGURED = "DR_EVIDENCE_PATH_NOT_CONFIGURED"
DR_EVIDENCE_MISSING = "DR_EVIDENCE_MISSING"
DR_EVIDENCE_INVALID = "DR_EVIDENCE_INVALID"
DR_BACKUP_EVIDENCE_MISSING = "DR_BACKUP_EVIDENCE_MISSING"
DR_BACKUP_STALE = "DR_BACKUP_STALE"
DR_BACKUP_CADENCE_EXCEEDS_RPO = "DR_BACKUP_CADENCE_EXCEEDS_RPO"
DR_TARGET_NOT_VERIFIED_OFF_HOST = "DR_TARGET_NOT_VERIFIED_OFF_HOST"
DR_RESTORE_EVIDENCE_MISSING = "DR_RESTORE_EVIDENCE_MISSING"
DR_RESTORE_UNVERIFIED = "DR_RESTORE_UNVERIFIED"
DR_RTO_EXCEEDED = "DR_RTO_EXCEEDED"
DR_RESTORE_BACKUP_MISMATCH = "DR_RESTORE_BACKUP_MISMATCH"
DR_RESTORE_EVIDENCE_STALE = "DR_RESTORE_EVIDENCE_STALE"
DR_RESTORE_TARGET_NOT_SEPARATE = "DR_RESTORE_TARGET_NOT_SEPARATE"
DR_BACKUP_TIMESTAMP_INVALID = "DR_BACKUP_TIMESTAMP_INVALID"
DR_RESTORE_TIMESTAMP_INVALID = "DR_RESTORE_TIMESTAMP_INVALID"
DR_RESTORE_FACTS_UNVERIFIED = "DR_RESTORE_FACTS_UNVERIFIED"
CLOCK_SKEW_TOLERANCE_MS = 5 * 60 * 1000

_LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1", "0.0.0.0"})


@dataclass(frozen=True)
class DrGateResult:
    required: bool
    ready: bool
    reasons: tuple[str, ...] = ()
    backup_age_seconds: int | None = None
    restore_age_seconds: int | None = None
    rto_seconds: int | None = None
    evidence_path: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "ready": self.ready,
            "reasons": list(self.reasons),
            "backup_age_seconds": self.backup_age_seconds,
            "restore_age_seconds": self.restore_age_seconds,
            "rto_seconds": self.rto_seconds,
            "evidence_path": self.evidence_path,
            "details": self.details,
        }


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _database_identity(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        return ("", "")
    return (_text(value.get("host")), _text(value.get("database")))


def _local_host(host: str) -> bool:
    lowered = host.lower()
    return lowered in _LOCAL_HOSTS or lowered.startswith("127.")


def evaluate_dr_evidence(
    evidence: Any,
    *,
    now_ms: int | None = None,
    rpo_seconds: int = DEFAULT_RPO_SECONDS,
    max_restore_age_seconds: int = DEFAULT_MAX_RESTORE_AGE_SECONDS,
    max_rto_seconds: int = DEFAULT_MAX_RTO_SECONDS,
    evidence_path: str = "",
) -> DrGateResult:
    """Return a fail-closed DR verdict from an already-parsed evidence object."""
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    reasons: list[str] = []

    if not isinstance(evidence, dict):
        return DrGateResult(
            required=True,
            ready=False,
            reasons=(DR_EVIDENCE_INVALID,),
            evidence_path=evidence_path,
        )
    if _int(evidence.get("schema_version")) != SCHEMA_VERSION:
        reasons.append(DR_EVIDENCE_INVALID)

    backup = evidence.get("backup")
    if not isinstance(backup, dict):
        reasons.append(DR_BACKUP_EVIDENCE_MISSING)
        backup = {}
    completed_at_ms = _int(backup.get("completed_at_ms"))
    cadence_seconds = _int(backup.get("cadence_seconds"))
    digest = _text(backup.get("sha256")).lower()
    target = backup.get("target") if isinstance(backup.get("target"), dict) else {}

    backup_age_seconds = None
    if completed_at_ms is None or completed_at_ms <= 0:
        reasons.append(DR_BACKUP_EVIDENCE_MISSING)
    elif completed_at_ms > current_ms + CLOCK_SKEW_TOLERANCE_MS:
        reasons.append(DR_BACKUP_TIMESTAMP_INVALID)
    else:
        backup_age_seconds = max(0, (current_ms - completed_at_ms) // 1000)
        if backup_age_seconds > rpo_seconds:
            reasons.append(DR_BACKUP_STALE)
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        reasons.append(DR_BACKUP_EVIDENCE_MISSING)
    if cadence_seconds is None or cadence_seconds <= 0 or cadence_seconds > rpo_seconds:
        reasons.append(DR_BACKUP_CADENCE_EXCEEDS_RPO)
    target_transport = _text(target.get("transport")) or "direct"
    target_endpoint = _text(target.get("endpoint_host"))
    if target_transport == "ssh_tunnel":
        remote_host = _text(target.get("remote_host"))
        transport_identity_ok = bool(
            remote_host
            and not _local_host(remote_host)
            and target.get("ssh_host_key_verified") is True
        )
    else:
        remote_host = ""
        transport_identity_ok = bool(target_endpoint and not _local_host(target_endpoint))
    if not (
        target.get("off_host") is True
        and target.get("identity_verified") is True
        and _text(target.get("attested_by"))
        and target_endpoint
        and transport_identity_ok
        and _text(target.get("kind")) != "local_emulator"
    ):
        reasons.append(DR_TARGET_NOT_VERIFIED_OFF_HOST)

    restore = evidence.get("restore")
    if not isinstance(restore, dict):
        reasons.append(DR_RESTORE_EVIDENCE_MISSING)
        restore = {}
    restore_completed_ms = _int(restore.get("completed_at_ms"))
    restore_age_seconds = None
    if restore_completed_ms is None or restore_completed_ms <= 0:
        reasons.append(DR_RESTORE_EVIDENCE_MISSING)
    elif restore_completed_ms > current_ms + CLOCK_SKEW_TOLERANCE_MS:
        reasons.append(DR_RESTORE_TIMESTAMP_INVALID)
    else:
        restore_age_seconds = max(0, (current_ms - restore_completed_ms) // 1000)
        if restore_age_seconds > max_restore_age_seconds:
            reasons.append(DR_RESTORE_EVIDENCE_STALE)
    if restore.get("verified") is not True:
        reasons.append(DR_RESTORE_UNVERIFIED)
    checks = restore.get("checks")
    if (
        not isinstance(checks, dict)
        or checks.get("facts_match") is not True
        or checks.get("invariants_ok") is not True
    ):
        # The frozen evidence shape requires both facts and invariant checks.
        # A bare verified:true (or a missing checks object) is insufficient.
        reasons.append(DR_RESTORE_FACTS_UNVERIFIED)
    rto_seconds = _int(restore.get("rto_seconds"))
    if rto_seconds is None or rto_seconds < 0 or rto_seconds > max_rto_seconds:
        reasons.append(DR_RTO_EXCEEDED)
    backup_sha = _text(restore.get("backup_sha256")).lower()
    if not digest or backup_sha != digest:
        reasons.append(DR_RESTORE_BACKUP_MISMATCH)
    source_host, source_db = _database_identity(restore.get("source_database"))
    target_host, target_db = _database_identity(restore.get("target_database"))
    if not source_host or not source_db or not target_host or not target_db:
        reasons.append(DR_RESTORE_EVIDENCE_MISSING)
    elif (source_host, source_db) == (target_host, target_db):
        reasons.append(DR_RESTORE_TARGET_NOT_SEPARATE)

    return DrGateResult(
        required=True,
        ready=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        backup_age_seconds=backup_age_seconds,
        restore_age_seconds=restore_age_seconds,
        rto_seconds=rto_seconds,
        evidence_path=evidence_path,
        details={
            "target_kind": _text(target.get("kind")),
            "target_endpoint_host": _text(target.get("endpoint_host")),
            "target_transport": target_transport,
            "target_remote_host": remote_host,
            "object_key": _text(backup.get("object_key")),
        },
    )


def evaluate_dr_evidence_file(
    path: str | os.PathLike[str],
    *,
    now_ms: int | None = None,
    rpo_seconds: int = DEFAULT_RPO_SECONDS,
    max_restore_age_seconds: int = DEFAULT_MAX_RESTORE_AGE_SECONDS,
    max_rto_seconds: int = DEFAULT_MAX_RTO_SECONDS,
) -> DrGateResult:
    evidence_path = str(path)
    candidate = Path(path)
    if not candidate.is_file():
        return DrGateResult(
            required=True,
            ready=False,
            reasons=(DR_EVIDENCE_MISSING,),
            evidence_path=evidence_path,
        )
    try:
        evidence = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DrGateResult(
            required=True,
            ready=False,
            reasons=(DR_EVIDENCE_INVALID,),
            evidence_path=evidence_path,
        )
    return evaluate_dr_evidence(
        evidence,
        now_ms=now_ms,
        rpo_seconds=rpo_seconds,
        max_restore_age_seconds=max_restore_age_seconds,
        max_rto_seconds=max_rto_seconds,
        evidence_path=evidence_path,
    )


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def dr_gate_from_env(
    environ: Mapping[str, str] | None = None,
) -> Callable[[], DrGateResult] | None:
    """Build the production gate, or ``None`` for dev/test assemblies.

    The gate is opt-in by configuration (Compose always enables it). A dev or
    unit-test assembly that never configured DR evidence does not silently
    claim phase-5 readiness; it simply is not the production assembly.
    """
    source = os.environ if environ is None else environ
    required = _truthy(source.get("QTS_DR_REQUIRED"))
    path = _text(source.get("QTS_DR_EVIDENCE_PATH"))
    if not required and not path:
        return None
    rpo_seconds = int(source.get("QTS_BACKUP_RPO_SECONDS", str(DEFAULT_RPO_SECONDS)))
    max_restore_age = int(
        source.get("QTS_DR_MAX_RESTORE_AGE_SECONDS", str(DEFAULT_MAX_RESTORE_AGE_SECONDS))
    )
    max_rto = int(source.get("QTS_DR_MAX_RTO_SECONDS", str(DEFAULT_MAX_RTO_SECONDS)))

    def gate() -> DrGateResult:
        if not path:
            return DrGateResult(
                required=True,
                ready=False,
                reasons=(DR_EVIDENCE_PATH_NOT_CONFIGURED,),
            )
        return evaluate_dr_evidence_file(
            path,
            rpo_seconds=rpo_seconds,
            max_restore_age_seconds=max_restore_age,
            max_rto_seconds=max_rto,
        )

    return gate
