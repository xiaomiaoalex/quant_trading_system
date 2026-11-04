"""Round-3 independent counterexamples for the two read-only review blockers.

BF-1 (5.9/5.10): the DR gate must require ``restore.checks`` to be an object
whose ``facts_match`` and ``invariants_ok`` are explicitly ``True``.  A missing
object, a non-object, a missing key, or any non-``True`` value must keep the
gate closed with ``DR_RESTORE_FACTS_UNVERIFIED``; ``verified: true`` alone is
not enough.  A positive case and the frozen off-host evidence must still pass.

BF-2 (5.11) is the real-PostgreSQL half and lives next to the existing lease
tests in ``test_phase5_independent_postgres.py``.

Expectations come from contract 13.16 and the review finding, not from the
engineer's test counts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIGEST = "b" * 64
EVIDENCE_GLOB = "artifacts/foundation/phase5/evidence/offhost/*/dr-evidence.json"
FACTS_UNVERIFIED = "DR_RESTORE_FACTS_UNVERIFIED"


def _evidence(checks, verified: bool = True) -> dict:
    """A DR evidence object that is complete except for the checks under test."""
    now = 1_800_000_000_000
    restore = {
        "drill_id": "round3",
        "started_at_ms": now - 50_000,
        "completed_at_ms": now - 40_000,
        "rto_seconds": 10,
        "backup_sha256": DIGEST,
        "verified": verified,
        "source_database": {"host": "127.0.0.1", "database": "qts_src"},
        "target_database": {"host": "127.0.0.1", "database": "qts_rst"},
    }
    if checks is not _MISSING:
        restore["checks"] = checks
    return {
        "schema_version": 1,
        "backup": {
            "completed_at_ms": now - 60_000,
            "cadence_seconds": 120,
            "sha256": DIGEST,
            "object_key": "phase5/round3/qts.dump",
            "target": {
                "kind": "s3_compatible",
                "endpoint_host": "192.168.3.4",
                "remote_host": "192.168.3.4",
                "tunnel_endpoint_host": "127.0.0.1",
                "transport": "ssh_tunnel",
                "ssh_host_key_verified": True,
                "off_host": True,
                "identity_verified": True,
                "attested_by": "user-designated alex@192.168.3.4",
            },
        },
        "restore": restore,
    }


class _Missing:
    pass


_MISSING = _Missing()


def _gate(evidence, now_ms: int = 1_800_000_000_000):
    from trader.ops.dr_evidence import evaluate_dr_evidence

    return evaluate_dr_evidence(evidence, now_ms=now_ms)


def test_5_9_a_complete_evidence_with_both_checks_still_passes():
    result = _gate(_evidence({"facts_match": True, "invariants_ok": True}))

    assert result.ready is True, result.reasons
    assert result.reasons == ()
    assert result.details["target_transport"] == "ssh_tunnel"


def test_5_9_a_missing_checks_object_keeps_the_gate_closed():
    result = _gate(_evidence(_MISSING))

    assert result.ready is False
    assert FACTS_UNVERIFIED in result.reasons


@pytest.mark.parametrize("checks", [None, [], "true", 1, True, False, {}])
def test_5_9_a_non_object_or_empty_checks_is_refused(checks):
    result = _gate(_evidence(checks))

    assert result.ready is False, checks
    assert FACTS_UNVERIFIED in result.reasons


@pytest.mark.parametrize(
    "checks",
    [
        {"facts_match": True},
        {"invariants_ok": True},
        {"facts_match": True, "invariants_ok": None},
        {"facts_match": None, "invariants_ok": True},
    ],
)
def test_5_9_a_partial_checks_object_is_refused(checks):
    result = _gate(_evidence(checks))

    assert result.ready is False, checks
    assert FACTS_UNVERIFIED in result.reasons


@pytest.mark.parametrize(
    "checks",
    [
        {"facts_match": False, "invariants_ok": True},
        {"facts_match": True, "invariants_ok": False},
        {"facts_match": False, "invariants_ok": False},
    ],
)
def test_5_9_a_false_check_is_refused(checks):
    result = _gate(_evidence(checks))

    assert result.ready is False
    assert FACTS_UNVERIFIED in result.reasons


@pytest.mark.parametrize(
    "checks",
    [
        {"facts_match": "true", "invariants_ok": True},
        {"facts_match": True, "invariants_ok": 1},
        {"facts_match": 1, "invariants_ok": 1},
    ],
)
def test_5_9_truthy_non_booleans_are_not_accepted(checks):
    # Contract 13.16 says the fields must be explicitly true, so an integer or
    # a string that merely looks true must not open the gate.
    result = _gate(_evidence(checks))

    assert result.ready is False, checks
    assert FACTS_UNVERIFIED in result.reasons


def test_5_9_checks_do_not_substitute_for_verified():
    result = _gate(_evidence({"facts_match": True, "invariants_ok": True}, verified=False))

    assert result.ready is False
    assert "DR_RESTORE_UNVERIFIED" in result.reasons


def test_5_9_the_frozen_offhost_evidence_still_passes_the_stricter_gate():
    candidates = sorted(
        ROOT.glob(EVIDENCE_GLOB), key=lambda path: path.stat().st_mtime, reverse=True
    )
    if not candidates:
        pytest.skip("no off-host DR evidence is present in this checkout")
    evidence = json.loads(candidates[0].read_text(encoding="utf-8"))
    now_ms = int(evidence["restore"]["completed_at_ms"]) + 1_000

    result = _gate(evidence, now_ms=now_ms)

    assert evidence["restore"]["checks"]["facts_match"] is True
    assert evidence["restore"]["checks"]["invariants_ok"] is True
    assert result.ready is True, result.reasons
    assert result.reasons == ()
    assert result.details["target_transport"] == "ssh_tunnel"
    assert result.details["target_remote_host"] == "192.168.3.4"
