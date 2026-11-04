"""Phase-5 DR evidence gate: missing, stale or unverified evidence stays non-READY."""

from __future__ import annotations

import json

from trader.ops.dr_evidence import (
    DR_BACKUP_STALE,
    DR_BACKUP_TIMESTAMP_INVALID,
    DR_EVIDENCE_INVALID,
    DR_EVIDENCE_MISSING,
    DR_RESTORE_BACKUP_MISMATCH,
    DR_RESTORE_FACTS_UNVERIFIED,
    DR_RESTORE_TIMESTAMP_INVALID,
    DR_RESTORE_UNVERIFIED,
    DR_RTO_EXCEEDED,
    DR_TARGET_NOT_VERIFIED_OFF_HOST,
    dr_gate_from_env,
    evaluate_dr_evidence,
    evaluate_dr_evidence_file,
)

NOW_MS = 1_700_000_000_000
DIGEST = "a" * 64


def _valid() -> dict:
    return {
        "schema_version": 1,
        "backup": {
            "completed_at_ms": NOW_MS - 60_000,
            "cadence_seconds": 300,
            "sha256": DIGEST,
            "object_key": "qts/backups/one.dump",
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
            "backup_sha256": DIGEST,
            "verified": True,
            "source_database": {"host": "db-a", "database": "trading"},
            "target_database": {"host": "db-b", "database": "qts_restore_drill"},
            "checks": {"facts_match": True, "invariants_ok": True},
        },
    }


def test_complete_recent_evidence_is_ready():
    result = evaluate_dr_evidence(_valid(), now_ms=NOW_MS)
    assert result.ready is True
    assert result.reasons == ()
    assert result.backup_age_seconds == 60
    assert result.rto_seconds == 600


def test_missing_and_invalid_files_fail_closed(tmp_path):
    missing = evaluate_dr_evidence_file(tmp_path / "absent.json", now_ms=NOW_MS)
    assert missing.ready is False
    assert missing.reasons == (DR_EVIDENCE_MISSING,)
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{not json", encoding="utf-8")
    invalid = evaluate_dr_evidence_file(invalid_path, now_ms=NOW_MS)
    assert invalid.ready is False
    assert invalid.reasons == (DR_EVIDENCE_INVALID,)


def test_stale_backup_blocks_readiness():
    evidence = _valid()
    evidence["backup"]["completed_at_ms"] = NOW_MS - 301_000
    result = evaluate_dr_evidence(evidence, now_ms=NOW_MS)
    assert result.ready is False
    assert DR_BACKUP_STALE in result.reasons


def test_local_emulator_is_never_an_off_host_target():
    evidence = _valid()
    evidence["backup"]["target"].update({"kind": "local_emulator", "off_host": False})
    result = evaluate_dr_evidence(evidence, now_ms=NOW_MS)
    assert result.ready is False
    assert DR_TARGET_NOT_VERIFIED_OFF_HOST in result.reasons


def test_unverified_restore_rto_and_backup_mismatch_block_readiness():
    evidence = _valid()
    evidence["restore"]["verified"] = False
    evidence["restore"]["rto_seconds"] = 3_601
    evidence["restore"]["backup_sha256"] = "b" * 64
    result = evaluate_dr_evidence(evidence, now_ms=NOW_MS)
    assert result.ready is False
    assert DR_RESTORE_UNVERIFIED in result.reasons
    assert DR_RTO_EXCEEDED in result.reasons
    assert DR_RESTORE_BACKUP_MISMATCH in result.reasons


def test_restore_must_target_a_separate_database():
    evidence = _valid()
    evidence["restore"]["target_database"] = dict(evidence["restore"]["source_database"])
    result = evaluate_dr_evidence(evidence, now_ms=NOW_MS)
    assert result.ready is False
    assert "DR_RESTORE_TARGET_NOT_SEPARATE" in result.reasons


def test_gate_from_env_is_opt_in_and_required_path_fails_closed(tmp_path):
    assert dr_gate_from_env({}) is None
    gate = dr_gate_from_env({"QTS_DR_REQUIRED": "1"})
    assert gate is not None
    assert gate().reasons == ("DR_EVIDENCE_PATH_NOT_CONFIGURED",)
    path = tmp_path / "dr.json"
    current_ms = int(__import__("time").time() * 1000)
    evidence = _valid()
    evidence["backup"]["completed_at_ms"] = current_ms - 60_000
    evidence["restore"]["completed_at_ms"] = current_ms - 3_600_000
    path.write_text(json.dumps(evidence), encoding="utf-8")
    ready = dr_gate_from_env({"QTS_DR_REQUIRED": "1", "QTS_DR_EVIDENCE_PATH": str(path)})()
    assert ready.ready is True


def test_future_backup_and_restore_timestamps_are_rejected():
    evidence = _valid()
    evidence["backup"]["completed_at_ms"] = NOW_MS + 3_600_000
    evidence["restore"]["completed_at_ms"] = NOW_MS + 3_600_000
    result = evaluate_dr_evidence(evidence, now_ms=NOW_MS)
    assert result.ready is False
    assert DR_BACKUP_TIMESTAMP_INVALID in result.reasons
    assert DR_RESTORE_TIMESTAMP_INVALID in result.reasons


def test_contradictory_restore_checks_are_not_trusted():
    evidence = _valid()
    evidence["restore"]["checks"] = {"facts_match": False, "invariants_ok": True}
    result = evaluate_dr_evidence(evidence, now_ms=NOW_MS)
    assert result.ready is False
    assert DR_RESTORE_FACTS_UNVERIFIED in result.reasons


def test_ssh_tunnel_target_records_a_real_remote_identity():
    evidence = _valid()
    target = evidence["backup"]["target"]
    target.update(
        {
            "transport": "ssh_tunnel",
            "remote_host": "192.168.3.4",
            "endpoint_host": "192.168.3.4",
            "tunnel_endpoint_host": "127.0.0.1",
            "ssh_host_key_verified": True,
        }
    )
    result = evaluate_dr_evidence(evidence, now_ms=NOW_MS)
    assert result.ready is True
    assert result.details["target_transport"] == "ssh_tunnel"
    assert result.details["target_remote_host"] == "192.168.3.4"


def test_ssh_tunnel_without_verified_remote_identity_is_rejected():
    evidence = _valid()
    target = evidence["backup"]["target"]
    target.update(
        {
            "transport": "ssh_tunnel",
            "remote_host": "",
            "endpoint_host": "192.168.3.4",
            "tunnel_endpoint_host": "127.0.0.1",
            "ssh_host_key_verified": False,
        }
    )
    result = evaluate_dr_evidence(evidence, now_ms=NOW_MS)
    assert result.ready is False
    assert DR_TARGET_NOT_VERIFIED_OFF_HOST in result.reasons


def test_missing_restore_checks_keeps_the_gate_non_ready():
    evidence = _valid()
    evidence["restore"].pop("checks")
    result = evaluate_dr_evidence(evidence, now_ms=NOW_MS)
    assert result.ready is False
    assert DR_RESTORE_FACTS_UNVERIFIED in result.reasons
