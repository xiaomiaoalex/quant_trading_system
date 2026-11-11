"""Fail-closed Demo release preflight (C4).

Every check is expressed as a reason code plus the configuration key name.
Secret values are never included in the report; tests assert that directly.
"""

from __future__ import annotations

import json

import pytest
from scripts.deploy_preflight import evaluate

NOW_MS = 1_800_000_000_000
RELEASE_DIGEST = "sha256:" + "a" * 64
PREVIOUS_DIGEST = "sha256:" + "b" * 64
COMMIT = "c6fe450260eeb59d46c9caee591fb1625510b443"
SECRET_MARKER = "SUPER-SECRET-VALUE-MUST-NOT-LEAK"


def _dr_evidence() -> dict:
    return {
        "schema_version": 1,
        "backup": {
            "completed_at_ms": NOW_MS - 60_000,
            "cadence_seconds": 300,
            "sha256": "c" * 64,
            "object_key": "qts/backups/demo.dump",
            "target": {
                "kind": "s3_compatible",
                "endpoint_host": "backup.example.internal",
                "bucket": "qts-backups",
                "off_host": True,
                "identity_verified": True,
                "attested_by": "operator",
            },
        },
        "restore": {
            "completed_at_ms": NOW_MS - 3_600_000,
            "rto_seconds": 600,
            "backup_sha256": "c" * 64,
            "verified": True,
            "source_database": {"host": "db-a", "database": "trading"},
            "target_database": {"host": "db-b", "database": "qts_restore_drill"},
            "checks": {"facts_match": True, "invariants_ok": True},
        },
    }


@pytest.fixture
def evidence_files(tmp_path):
    dr = tmp_path / "dr.json"
    dr.write_text(json.dumps(_dr_evidence()), encoding="utf-8")
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "expand_only": True,
                "old_runtime_compatible": True,
                "checksum": "d" * 64,
            }
        ),
        encoding="utf-8",
    )
    rollback = tmp_path / "rollback.json"
    rollback.write_text(
        json.dumps(
            {
                "previous_image_digest": PREVIOUS_DIGEST,
                "release_image_digest": RELEASE_DIGEST,
                "schema_version": 3,
                "compatible": True,
                "verified": True,
            }
        ),
        encoding="utf-8",
    )
    return {"dr": dr, "schema": schema, "rollback": rollback}


def _env(files) -> dict[str, str]:
    return {
        "BINANCE_ENV": "demo",
        "POSTGRES_PASSWORD": SECRET_MARKER + "-pg",
        "QTS_ALERT_SIGNING_SECRET": SECRET_MARKER + "-alert",
        "QTS_AUTH_REQUIRED": "1",
        "QTS_PASSWORD_HASH": SECRET_MARKER + "-hash",
        "QTS_ACCOUNT_ID": "binance_spot_demo",
        "QTS_SYMBOLS": "BTCUSDT,ETHUSDT",
        "QTS_INSTRUMENTS": '{"BTCUSDT":["BTC","USDT"],"ETHUSDT":["ETH","USDT"]}',
        "QTS_STRATEGY_MODULE": "trader.strategies.momentum",
        "QTS_STRATEGY_WARMUP_EVENTS": "200",
        "QTS_EXTERNAL_ALERT_WEBHOOK_URL": "https://alerts.example.com/qts",
        "QTS_EXTERNAL_ALERT_HEARTBEAT_URL": "https://alerts.example.com/qts/heartbeat",
        "QTS_DR_REQUIRED": "1",
        "QTS_DR_EVIDENCE_PATH": str(files["dr"]),
        "QTS_BACKUP_RPO_SECONDS": "300",
        "QTS_BACKUP_OFF_HOST_ATTESTED": "1",
        "QTS_BACKUP_ATTESTED_BY": "operator",
        "QTS_RELEASE_ID": "rel-2026-11-04-1",
        "QTS_RELEASE_COMMIT": COMMIT,
        "QTS_RELEASE_IMAGE": "ghcr.io/example/qts@" + RELEASE_DIGEST,
        "QTS_EXPECTED_DIGEST": RELEASE_DIGEST,
        "QTS_PREVIOUS_IMAGE_DIGEST": PREVIOUS_DIGEST,
        "QTS_SCHEMA_VERSION": "3",
        "QTS_SCHEMA_COMPAT_EVIDENCE_PATH": str(files["schema"]),
        "QTS_ROLLBACK_COMPAT_EVIDENCE_PATH": str(files["rollback"]),
    }


def _codes(report):
    return {issue.code for issue in report.errors}


def test_complete_demo_configuration_passes(evidence_files):
    report = evaluate(_env(evidence_files), now_ms=NOW_MS)
    assert report.ok is True
    assert report.errors == []
    assert report.release_digest == RELEASE_DIGEST


def test_remote_preflight_defers_host_evidence(evidence_files):
    env = _env(evidence_files)
    evidence_files["dr"].unlink()
    evidence_files["schema"].unlink()
    evidence_files["rollback"].unlink()
    report = evaluate(env, now_ms=NOW_MS, host_evidence=False)
    assert report.ok is True
    assert report.host_evidence_evaluated is False
    deferred = {issue.key for issue in report.warnings if issue.code == "HOST_EVIDENCE_DEFERRED"}
    assert {
        "QTS_DR_EVIDENCE_PATH",
        "QTS_SCHEMA_COMPAT_EVIDENCE_PATH",
        "QTS_ROLLBACK_COMPAT_EVIDENCE_PATH",
    } <= deferred


def test_non_demo_environment_is_rejected(evidence_files):
    env = _env(evidence_files) | {"BINANCE_ENV": "testnet"}
    report = evaluate(env, now_ms=NOW_MS)
    assert "BINANCE_ENV_NOT_DEMO" in _codes(report)


def test_default_or_missing_credentials_are_rejected(evidence_files):
    env = _env(evidence_files) | {
        "POSTGRES_PASSWORD": "trader_pwd",
        "QTS_ALERT_SIGNING_SECRET": "",
        "QTS_AUTH_REQUIRED": "0",
        "QTS_PASSWORD_HASH": "",
    }
    report = evaluate(env, now_ms=NOW_MS)
    codes = _codes(report)
    assert "POSTGRES_PASSWORD_DEFAULT_OR_MISSING" in codes
    assert "ALERT_SIGNING_SECRET_MISSING" in codes
    assert "AUTH_REQUIRED_NOT_ENABLED" in codes
    assert "CONTROL_PASSWORD_HASH_MISSING" in codes


def test_missing_demo_allowlist_and_warmup_are_rejected(evidence_files):
    env = _env(evidence_files) | {
        "QTS_ACCOUNT_ID": "",
        "QTS_SYMBOLS": "",
        "QTS_INSTRUMENTS": "",
        "QTS_STRATEGY_MODULE": "",
        "QTS_STRATEGY_WARMUP_EVENTS": "0",
    }
    report = evaluate(env, now_ms=NOW_MS)
    codes = _codes(report)
    assert "ACCOUNT_ID_MISSING" in codes
    assert "SYMBOLS_MISSING" in codes
    assert "INSTRUMENTS_MISSING" in codes
    assert "STRATEGY_MODULE_MISSING" in codes
    assert "STRATEGY_WARMUP_MISSING" in codes


def test_placeholder_operational_values_are_rejected(evidence_files):
    env = _env(evidence_files) | {
        "QTS_ACCOUNT_ID": "REPLACE_WITH_ACCOUNT",
        "QTS_STRATEGY_MODULE": "REPLACE_WITH_APPROVED_STRATEGY_MODULE",
        "QTS_BACKUP_ATTESTED_BY": "REPLACE_WITH_OPERATOR_NAME",
    }
    report = evaluate(env, now_ms=NOW_MS)
    codes = _codes(report)
    assert "ACCOUNT_ID_MISSING" in codes
    assert "STRATEGY_MODULE_MISSING" in codes
    assert "OFF_HOST_BACKUP_NOT_ATTESTED" in codes


def test_symbol_without_instrument_mapping_is_rejected(evidence_files):
    env = _env(evidence_files) | {"QTS_INSTRUMENTS": '{"BTCUSDT":["BTC","USDT"]}'}
    report = evaluate(env, now_ms=NOW_MS)
    assert "INSTRUMENT_SYMBOL_MISMATCH" in _codes(report)


def test_missing_external_alert_is_rejected(evidence_files):
    env = _env(evidence_files) | {
        "QTS_EXTERNAL_ALERT_WEBHOOK_URL": "http://alert-sink:8085",
        "QTS_EXTERNAL_ALERT_HEARTBEAT_URL": "http://alert-sink:8085/heartbeat",
    }
    report = evaluate(env, now_ms=NOW_MS)
    codes = _codes(report)
    assert "EXTERNAL_ALERT_MISSING" in codes
    assert "EXTERNAL_ALERT_HEARTBEAT_MISSING" in codes


def test_invalid_or_stale_dr_evidence_is_rejected(evidence_files):
    env = _env(evidence_files) | {
        "QTS_DR_REQUIRED": "0",
        "QTS_DR_EVIDENCE_PATH": str(evidence_files["dr"]),
        "QTS_BACKUP_RPO_SECONDS": "900",
        "QTS_BACKUP_OFF_HOST_ATTESTED": "0",
    }
    report = evaluate(env, now_ms=NOW_MS)
    codes = _codes(report)
    assert "DR_REQUIRED_NOT_ENABLED" in codes
    assert "BACKUP_RPO_EXCEEDED" in codes
    assert "OFF_HOST_BACKUP_NOT_ATTESTED" in codes


def test_wrong_or_mismatched_release_digest_is_rejected(evidence_files):
    env = _env(evidence_files) | {
        "QTS_RELEASE_IMAGE": "ghcr.io/example/qts:latest",
        "QTS_EXPECTED_DIGEST": "sha256:" + "e" * 64,
        "QTS_RELEASE_COMMIT": "not-a-commit",
    }
    report = evaluate(env, now_ms=NOW_MS)
    codes = _codes(report)
    assert "RELEASE_IMAGE_DIGEST_MISSING" in codes
    assert "RELEASE_IMAGE_DIGEST_MISMATCH" in codes
    assert "RELEASE_COMMIT_INVALID" in codes


def test_missing_schema_and_rollback_evidence_is_rejected(evidence_files):
    env = _env(evidence_files) | {
        "QTS_SCHEMA_COMPAT_EVIDENCE_PATH": str(evidence_files["schema"]),
        "QTS_ROLLBACK_COMPAT_EVIDENCE_PATH": str(evidence_files["rollback"]),
    }
    evidence_files["schema"].unlink()
    evidence_files["rollback"].unlink()
    report = evaluate(env, now_ms=NOW_MS)
    codes = _codes(report)
    assert "SCHEMA_COMPAT_EVIDENCE_MISSING" in codes
    assert "ROLLBACK_COMPAT_EVIDENCE_MISSING" in codes


def test_rollback_digest_mismatch_is_rejected(evidence_files):
    rollback = json.loads(evidence_files["rollback"].read_text(encoding="utf-8"))
    rollback["previous_image_digest"] = "sha256:" + "f" * 64
    evidence_files["rollback"].write_text(json.dumps(rollback), encoding="utf-8")
    report = evaluate(_env(evidence_files), now_ms=NOW_MS)
    assert "ROLLBACK_DIGEST_MISMATCH" in _codes(report)


def test_report_never_contains_secret_values(evidence_files):
    env = _env(evidence_files) | {"POSTGRES_PASSWORD": SECRET_MARKER}
    report = evaluate(env, now_ms=NOW_MS)
    assert SECRET_MARKER not in json.dumps(report.to_dict())
    assert all(issue.key for issue in report.errors) is True


def _release_manifest():
    present = {"present": True, "sha256": "a" * 64}
    return {
        "release_id": "rel-2026-11-04-1",
        "commit": COMMIT,
        "image_digest": RELEASE_DIGEST,
        "environment": "demo",
        "dependencies": {"ci_constraints": present, "runtime_constraints": present},
        "evidence": {
            "junit": [present],
            "coverage": present,
            "frontend": present,
            "sbom": present,
            "scan": present,
            "startup_smoke": present,
        },
        "startup_smoke_summary": {
            "passed": True,
            "image_digest_ok": True,
            "image": "ghcr.io/example/qts@" + RELEASE_DIGEST,
        },
    }


def test_release_manifest_evidence_is_required(evidence_files):
    report = evaluate(_env(evidence_files), now_ms=NOW_MS, release_manifest=_release_manifest())
    assert report.ok is True

    missing = _release_manifest()
    missing["evidence"]["startup_smoke"] = {"present": False}
    report = evaluate(_env(evidence_files), now_ms=NOW_MS, release_manifest=missing)
    assert "RELEASE_MANIFEST_EVIDENCE_MISSING:startup_smoke" in _codes(report)

    failed = _release_manifest()
    failed["startup_smoke_summary"]["passed"] = False
    report = evaluate(_env(evidence_files), now_ms=NOW_MS, release_manifest=failed)
    assert "RELEASE_MANIFEST_STARTUP_SMOKE_FAILED" in _codes(report)

    no_junit = _release_manifest()
    no_junit["evidence"]["junit"] = []
    report = evaluate(_env(evidence_files), now_ms=NOW_MS, release_manifest=no_junit)
    assert "RELEASE_MANIFEST_EVIDENCE_MISSING:junit" in _codes(report)


def test_manifest_omitted_is_reported_as_a_warning(evidence_files):
    report = evaluate(_env(evidence_files), now_ms=NOW_MS)
    assert any(issue.code == "RELEASE_MANIFEST_NOT_PROVIDED" for issue in report.warnings)
