"""Fail-closed Demo release preflight.

The preflight is intentionally independent from the runtime assembly: it reads
configuration and release evidence only and never connects to PostgreSQL,
Docker, the exchange or the Control API.  Every failure is expressed as a
stable reason code plus the configuration key name; secret values are never
printed or serialized.

Exit code 0 means the configuration *may* be deployed by the protected Demo
workflow.  It is not proof that a deployment happened, nor that the running
system is healthy.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # Direct script execution puts scripts/ on sys.path, not the repo root.
    sys.path.insert(0, str(ROOT))
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"[0-9a-f]{40}$")
DEFAULT_PASSWORDS = {"trader_pwd", "postgres", "password", "changeme", "secret"}
INTERNAL_ALERT_HOSTS = {"alert-sink", "localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class Issue:
    code: str
    key: str


@dataclass
class PreflightReport:
    environment: str = ""
    release_id: str = ""
    release_commit: str = ""
    release_digest: str = ""
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    checked_keys: list[str] = field(default_factory=list)
    host_evidence_evaluated: bool = True

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "environment": self.environment,
            "release_id": self.release_id,
            "release_commit": self.release_commit,
            "release_digest": self.release_digest,
            "errors": [{"code": issue.code, "key": issue.key} for issue in self.errors],
            "warnings": [{"code": issue.code, "key": issue.key} for issue in self.warnings],
            "checked_keys": sorted(set(self.checked_keys)),
            "host_evidence_evaluated": self.host_evidence_evaluated,
        }


def _text(source: Mapping[str, str], key: str) -> str:
    return str(source.get(key) or "").strip()


def _normalise_digest(value: object) -> str:
    text = str(value or "").strip()
    if "@" in text:
        text = text.rsplit("@", 1)[-1]
    return text


def _is_placeholder(value: str) -> bool:
    upper = value.upper()
    return "REPLACE_WITH" in upper or "CHANGE_ME" in upper


def _read_json(path: str) -> dict[str, Any] | None:
    candidate = Path(path)
    if not candidate.is_file():
        return None
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def evaluate(
    environ: Mapping[str, str],
    *,
    now_ms: int | None = None,
    release_manifest: dict[str, Any] | None = None,
    host_evidence: bool = True,
) -> PreflightReport:
    report = PreflightReport(host_evidence_evaluated=host_evidence)
    checked: list[str] = report.checked_keys

    def require(key: str, code: str, passed: bool) -> None:
        checked.append(key)
        if not passed:
            report.errors.append(Issue(code, key))

    report.environment = _text(environ, "BINANCE_ENV").lower()
    require("BINANCE_ENV", "BINANCE_ENV_NOT_DEMO", report.environment == "demo")

    postgres_password = _text(environ, "POSTGRES_PASSWORD")
    require(
        "POSTGRES_PASSWORD",
        "POSTGRES_PASSWORD_DEFAULT_OR_MISSING",
        bool(postgres_password)
        and postgres_password.lower() not in DEFAULT_PASSWORDS
        and not _is_placeholder(postgres_password),
    )
    alert_secret = _text(environ, "QTS_ALERT_SIGNING_SECRET")
    require(
        "QTS_ALERT_SIGNING_SECRET",
        "ALERT_SIGNING_SECRET_MISSING",
        bool(alert_secret)
        and alert_secret.lower() not in DEFAULT_PASSWORDS
        and not _is_placeholder(alert_secret),
    )
    require(
        "QTS_AUTH_REQUIRED", "AUTH_REQUIRED_NOT_ENABLED", _text(environ, "QTS_AUTH_REQUIRED") == "1"
    )
    password_hash = _text(environ, "QTS_PASSWORD_HASH")
    require(
        "QTS_PASSWORD_HASH",
        "CONTROL_PASSWORD_HASH_MISSING",
        bool(password_hash) and not _is_placeholder(password_hash),
    )

    account_id = _text(environ, "QTS_ACCOUNT_ID")
    require(
        "QTS_ACCOUNT_ID",
        "ACCOUNT_ID_MISSING",
        bool(account_id) and not _is_placeholder(account_id),
    )
    require(
        "QTS_ACCOUNT_ID",
        "ACCOUNT_ID_NOT_DEDICATED",
        bool(account_id) and account_id != "binance_spot",
    )

    symbols = [
        item.strip().upper() for item in _text(environ, "QTS_SYMBOLS").split(",") if item.strip()
    ]
    require("QTS_SYMBOLS", "SYMBOLS_MISSING", bool(symbols))

    instruments_raw = _text(environ, "QTS_INSTRUMENTS")
    instruments: dict[str, Any] = {}
    if instruments_raw:
        try:
            loaded = json.loads(instruments_raw)
            if isinstance(loaded, dict):
                instruments = loaded
        except ValueError:
            instruments = {}
    require("QTS_INSTRUMENTS", "INSTRUMENTS_MISSING", bool(instruments))
    if symbols and instruments:
        missing = sorted(symbol for symbol in symbols if symbol not in instruments)
        require("QTS_INSTRUMENTS", "INSTRUMENT_SYMBOL_MISMATCH", not missing)
    strategy_module = _text(environ, "QTS_STRATEGY_MODULE")
    require(
        "QTS_STRATEGY_MODULE",
        "STRATEGY_MODULE_MISSING",
        bool(strategy_module) and not _is_placeholder(strategy_module),
    )
    try:
        warmup = int(_text(environ, "QTS_STRATEGY_WARMUP_EVENTS") or "0")
    except ValueError:
        warmup = 0
    require("QTS_STRATEGY_WARMUP_EVENTS", "STRATEGY_WARMUP_MISSING", warmup > 0)

    def _external_alert_ok(url: str) -> bool:
        if not url.startswith("https://"):
            return False
        host = url.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0].lower()
        return host not in INTERNAL_ALERT_HOSTS

    external_alert = _text(environ, "QTS_EXTERNAL_ALERT_WEBHOOK_URL")
    require(
        "QTS_EXTERNAL_ALERT_WEBHOOK_URL",
        "EXTERNAL_ALERT_MISSING",
        _external_alert_ok(external_alert),
    )
    external_heartbeat = _text(environ, "QTS_EXTERNAL_ALERT_HEARTBEAT_URL")
    require(
        "QTS_EXTERNAL_ALERT_HEARTBEAT_URL",
        "EXTERNAL_ALERT_HEARTBEAT_MISSING",
        _external_alert_ok(external_heartbeat),
    )

    require("QTS_DR_REQUIRED", "DR_REQUIRED_NOT_ENABLED", _text(environ, "QTS_DR_REQUIRED") == "1")
    try:
        rpo_seconds = int(_text(environ, "QTS_BACKUP_RPO_SECONDS") or "0")
    except ValueError:
        rpo_seconds = 0
    require("QTS_BACKUP_RPO_SECONDS", "BACKUP_RPO_EXCEEDED", 0 < rpo_seconds <= 300)
    attested_by = _text(environ, "QTS_BACKUP_ATTESTED_BY")
    require(
        "QTS_BACKUP_OFF_HOST_ATTESTED",
        "OFF_HOST_BACKUP_NOT_ATTESTED",
        _text(environ, "QTS_BACKUP_OFF_HOST_ATTESTED") == "1"
        and bool(attested_by)
        and not _is_placeholder(attested_by),
    )
    dr_path = _text(environ, "QTS_DR_EVIDENCE_PATH")
    if host_evidence:
        require("QTS_DR_EVIDENCE_PATH", "DR_EVIDENCE_PATH_MISSING", bool(dr_path))
        if dr_path:
            from trader.ops.dr_evidence import evaluate_dr_evidence_file

            dr_result = evaluate_dr_evidence_file(
                dr_path,
                now_ms=now_ms,
                rpo_seconds=rpo_seconds or 300,
            )
            checked.append("QTS_DR_EVIDENCE_PATH")
            if not dr_result.ready:
                for reason in dr_result.reasons:
                    report.errors.append(Issue(str(reason), "QTS_DR_EVIDENCE_PATH"))
        else:
            checked.append("QTS_DR_EVIDENCE_PATH")
    else:
        checked.append("QTS_DR_EVIDENCE_PATH")
        report.warnings.append(Issue("HOST_EVIDENCE_DEFERRED", "QTS_DR_EVIDENCE_PATH"))

    release_id = _text(environ, "QTS_RELEASE_ID")
    release_commit = _text(environ, "QTS_RELEASE_COMMIT").lower()
    release_digest = _normalise_digest(_text(environ, "QTS_RELEASE_IMAGE"))
    expected_digest = _normalise_digest(_text(environ, "QTS_EXPECTED_DIGEST"))
    report.release_id = release_id
    report.release_commit = release_commit
    report.release_digest = release_digest
    require("QTS_RELEASE_ID", "RELEASE_ID_MISSING", bool(release_id))
    require(
        "QTS_RELEASE_COMMIT", "RELEASE_COMMIT_INVALID", bool(COMMIT_RE.fullmatch(release_commit))
    )
    require(
        "QTS_RELEASE_IMAGE",
        "RELEASE_IMAGE_DIGEST_MISSING",
        bool(DIGEST_RE.fullmatch(release_digest)),
    )
    require(
        "QTS_EXPECTED_DIGEST", "EXPECTED_DIGEST_MISSING", bool(DIGEST_RE.fullmatch(expected_digest))
    )
    require(
        "QTS_EXPECTED_DIGEST",
        "RELEASE_IMAGE_DIGEST_MISMATCH",
        bool(expected_digest) and expected_digest == release_digest,
    )

    if release_manifest is None:
        # The manifest evidence check is a local, explicit use case: without a
        # manifest the preflight can only check the configuration.  The
        # protected Demo workflows always pass one; the gap is reported
        # instead of being silent.
        report.warnings.append(Issue("RELEASE_MANIFEST_NOT_PROVIDED", "QTS_RELEASE_MANIFEST"))
    if release_manifest is not None:
        checked.append("QTS_RELEASE_MANIFEST")
        require(
            "QTS_RELEASE_MANIFEST",
            "RELEASE_MANIFEST_COMMIT_MISMATCH",
            str(release_manifest.get("commit") or "").lower() == release_commit,
        )
        require(
            "QTS_RELEASE_MANIFEST",
            "RELEASE_MANIFEST_DIGEST_MISMATCH",
            _normalise_digest(release_manifest.get("image_digest")) == release_digest,
        )
        require(
            "QTS_RELEASE_MANIFEST",
            "RELEASE_MANIFEST_ID_MISMATCH",
            str(release_manifest.get("release_id") or "") == release_id,
        )
        dependencies = release_manifest.get("dependencies") or {}
        evidence = release_manifest.get("evidence") or {}
        required_artifacts = {
            "ci_constraints": dependencies.get("ci_constraints"),
            "runtime_constraints": dependencies.get("runtime_constraints"),
            "junit": evidence.get("junit"),
            "coverage": evidence.get("coverage"),
            "frontend": evidence.get("frontend"),
            "sbom": evidence.get("sbom"),
            "scan": evidence.get("scan"),
            "startup_smoke": evidence.get("startup_smoke"),
        }
        for key, value in sorted(required_artifacts.items()):
            if key == "junit":
                present = (
                    isinstance(value, list)
                    and bool(value)
                    and all(
                        isinstance(item, dict) and item.get("present") is True for item in value
                    )
                )
            else:
                present = isinstance(value, dict) and value.get("present") is True
            require("QTS_RELEASE_MANIFEST", f"RELEASE_MANIFEST_EVIDENCE_MISSING:{key}", present)
        smoke_summary = release_manifest.get("startup_smoke_summary") or {}
        require(
            "QTS_RELEASE_MANIFEST",
            "RELEASE_MANIFEST_STARTUP_SMOKE_FAILED",
            smoke_summary.get("passed") is True
            and smoke_summary.get("image_digest_ok") is True
            and _normalise_digest(smoke_summary.get("image")) == release_digest,
        )

    try:
        schema_version = int(_text(environ, "QTS_SCHEMA_VERSION") or "0")
    except ValueError:
        schema_version = 0
    require("QTS_SCHEMA_VERSION", "SCHEMA_VERSION_MISSING", schema_version > 0)
    schema_path = _text(environ, "QTS_SCHEMA_COMPAT_EVIDENCE_PATH")
    if host_evidence:
        schema_evidence = _read_json(schema_path) if schema_path else None
        require(
            "QTS_SCHEMA_COMPAT_EVIDENCE_PATH",
            "SCHEMA_COMPAT_EVIDENCE_MISSING",
            bool(schema_path) and Path(schema_path).is_file(),
        )
        schema_ok = (
            schema_evidence is not None
            and schema_evidence.get("expand_only") is True
            and schema_evidence.get("old_runtime_compatible") is True
            and int(schema_evidence.get("schema_version") or 0) == schema_version
        )
        require("QTS_SCHEMA_COMPAT_EVIDENCE_PATH", "SCHEMA_COMPAT_EVIDENCE_INVALID", schema_ok)
    else:
        checked.append("QTS_SCHEMA_COMPAT_EVIDENCE_PATH")
        report.warnings.append(Issue("HOST_EVIDENCE_DEFERRED", "QTS_SCHEMA_COMPAT_EVIDENCE_PATH"))

    previous_digest = _normalise_digest(_text(environ, "QTS_PREVIOUS_IMAGE_DIGEST"))
    require(
        "QTS_PREVIOUS_IMAGE_DIGEST",
        "PREVIOUS_DIGEST_MISSING",
        bool(DIGEST_RE.fullmatch(previous_digest)),
    )
    rollback_path = _text(environ, "QTS_ROLLBACK_COMPAT_EVIDENCE_PATH")
    if host_evidence:
        rollback = _read_json(rollback_path) if rollback_path else None
        require(
            "QTS_ROLLBACK_COMPAT_EVIDENCE_PATH",
            "ROLLBACK_COMPAT_EVIDENCE_MISSING",
            bool(rollback_path) and Path(rollback_path).is_file(),
        )
        rollback_ok = (
            rollback is not None
            and rollback.get("compatible") is True
            and rollback.get("verified") is True
            and int(rollback.get("schema_version") or 0) == schema_version
        )
        require(
            "QTS_ROLLBACK_COMPAT_EVIDENCE_PATH",
            "ROLLBACK_COMPAT_EVIDENCE_INVALID",
            rollback_ok,
        )
        if rollback is not None:
            require(
                "QTS_ROLLBACK_COMPAT_EVIDENCE_PATH",
                "ROLLBACK_DIGEST_MISMATCH",
                _normalise_digest(rollback.get("previous_image_digest")) == previous_digest,
            )
            require(
                "QTS_ROLLBACK_COMPAT_EVIDENCE_PATH",
                "ROLLBACK_RELEASE_DIGEST_MISMATCH",
                _normalise_digest(rollback.get("release_image_digest")) == release_digest,
            )
    else:
        checked.append("QTS_ROLLBACK_COMPAT_EVIDENCE_PATH")
        report.warnings.append(Issue("HOST_EVIDENCE_DEFERRED", "QTS_ROLLBACK_COMPAT_EVIDENCE_PATH"))

    return report


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--release-manifest", type=Path, default=None)
    parser.add_argument("--now-ms", type=int, default=None)
    parser.add_argument(
        "--no-host-evidence",
        action="store_true",
        help="Defer host-local DR/schema/rollback file checks (remote dry-run only)",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    environ = dict(os.environ)
    if args.env_file:
        if not args.env_file.is_file():
            raise SystemExit(f"env file not found: {args.env_file}")
        environ.update(_load_env_file(args.env_file))
    manifest = None
    if args.release_manifest:
        manifest = _read_json(str(args.release_manifest))
        if manifest is None:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "errors": [{"code": "RELEASE_MANIFEST_INVALID", "key": "release_manifest"}],
                    }
                )
            )
            return 1

    report = evaluate(
        environ,
        now_ms=args.now_ms,
        release_manifest=manifest,
        host_evidence=not args.no_host_evidence,
    )
    text = json.dumps(report.to_dict(), indent=2, sort_keys=True)
    print(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
