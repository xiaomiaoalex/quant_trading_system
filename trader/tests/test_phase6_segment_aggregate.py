"""Engineer tests for the qualified-v3 segment aggregate (WP1)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASE = 1_794_200_000_000
EXEC_HASH = "a" * 64
LEDGER_HASH = "b" * 64
EVENTS_HASH = "c" * 64


def _module():
    path = ROOT / "scripts" / "phase6_segment_aggregate.py"
    spec = importlib.util.spec_from_file_location("phase6_segment_aggregate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sample(index: int, *, ts: int, mono: int, ready: bool = True, reasons=None, **overrides):
    metrics = {
        "evidence": {
            "executions": {"count": index, "keys_sha256": EXEC_HASH},
            "ledger": {"count": index, "entry_ids_sha256": LEDGER_HASH},
            "events": {"count": index, "event_ids_sha256": EVENTS_HASH},
        },
        "unresolved_entries": [],
        "unresolved": [],
        "coverage": {
            "contiguous": True,
            "recorded_at_ms": ts - 10_000,
            "source": "rest",
            "connected_from": ts - 60_000,
            "buffered_through": ts - 20_000,
            "covered_through": ts - 10_000,
            "generation": 1,
        },
        "reconciliation_age_ms": 10_000,
        "balance_anomalies": 0,
        "balance_differences": [],
    }
    risk = {"status": "READY", "valuation_complete": True, "checked_at_ms": ts - 5_000}
    for key, value in overrides.items():
        if key in metrics:
            metrics[key] = value
        elif key in risk:
            risk[key] = value
        else:
            raise AssertionError(f"unknown override {key}")
    return {
        "kind": "sample",
        "seq": index,
        "ts_ms": ts,
        "monotonic_ms": mono,
        "health": {"body": {"ready": ready, "reasons": reasons or []}},
        "metrics_status_code": 200,
        "metrics": metrics,
        "risk": risk,
    }


def _write_run(
    tmp_path: Path,
    samples,
    backups,
    *,
    run_id: str = "run",
    segment_index=None,
    resume_from=None,
    original_evidence_digest=None,
):
    run = tmp_path / "run"
    (run / "dr" / "backup").mkdir(parents=True)
    (run / "observation.jsonl").write_text(
        "\n".join(json.dumps(s) for s in samples) + "\n", encoding="utf-8"
    )
    (run / "window.json").write_text(
        json.dumps({"run_id": run_id, "passed": True}) + "\n", encoding="utf-8"
    )
    (run / "dr" / "backup" / "backup-journal.jsonl").write_text(
        "\n".join(
            json.dumps(
                {"status": "succeeded", "backup": {"completed_at_ms": value, "verified": True}}
            )
            for value in backups
        )
        + "\n",
        encoding="utf-8",
    )
    if segment_index is not None:
        (run / "resume-preflight.json").write_text(
            json.dumps(
                {
                    "counts": {
                        "accounts": 1,
                        "commands": 0,
                        "events": 0,
                        "executions": 0,
                        "ledger": 0,
                        "outbox": 0,
                    },
                    "identity_hashes": {
                        "executions": EXEC_HASH,
                        "ledger": LEDGER_HASH,
                        "events": EVENTS_HASH,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (run / "segment.json").write_text(
            json.dumps(
                {
                    "kind": "phase6_segment",
                    "segment_run_id": run_id,
                    "resume_from": resume_from,
                    "segment_index": segment_index,
                    "observation_only": True,
                    "original_evidence_digest": original_evidence_digest,
                    "db_identity_hashes": {
                        "executions": EXEC_HASH,
                        "ledger": LEDGER_HASH,
                        "events": EVENTS_HASH,
                    },
                    "lease_before": 0,
                    "lease_after": [],
                    "dr_ready_cycles": 3,
                    "qualified_rule_version": "qualified-v3",
                }
            )
            + "\n",
            encoding="utf-8",
        )
    return run


def _healthy_run(
    tmp_path: Path,
    count: int = 100,
    step_ms: int = 30_000,
    *,
    run_id: str = "run",
    segment_index=None,
    resume_from=None,
    original_evidence_digest=None,
):
    samples = [_sample(i, ts=BASE + i * step_ms, mono=i * step_ms) for i in range(count)]
    backups = [BASE + i * 120_000 for i in range(0, (count * step_ms) // 120_000 + 2)]
    return _write_run(
        tmp_path,
        samples,
        backups,
        run_id=run_id,
        segment_index=segment_index,
        resume_from=resume_from,
        original_evidence_digest=original_evidence_digest,
    )


def test_all_ready_interval_is_fully_qualified(tmp_path):
    run = _healthy_run(tmp_path)
    result = _module().compute_run(run)
    assert result["active_ms"] == result["qualified_ms"] == 99 * 30_000
    assert result["excluded_union_ms"] == 0
    assert result["primary_sum_ms"] == 0


def test_not_ready_interval_is_excluded(tmp_path):
    samples = [_sample(i, ts=BASE + i * 30_000, mono=i * 30_000) for i in range(20)]
    for i in range(5, 10):
        samples[i] = _sample(
            i,
            ts=BASE + i * 30_000,
            mono=i * 30_000,
            ready=False,
            reasons=["PRIVATE_STREAM_UNHEALTHY"],
        )
    run = _write_run(tmp_path, samples, [BASE + i * 120_000 for i in range(10)])
    result = _module().compute_run(run)
    assert result["primary_bucket_ms"]["not_ready"] > 0
    assert result["qualified_ms"] < result["active_ms"]
    assert result["primary_sum_ms"] == result["excluded_union_ms"]


def test_both_endpoints_required(tmp_path):
    samples = [
        _sample(0, ts=BASE, mono=0),
        _sample(1, ts=BASE + 30_000, mono=30_000, ready=False, reasons=["PRIVATE_STREAM_ALIGNING"]),
        _sample(2, ts=BASE + 60_000, mono=60_000),
    ]
    run = _write_run(tmp_path, samples, [BASE, BASE + 120_000])
    result = _module().compute_run(run)
    assert result["qualified_ms"] == 0
    assert result["primary_bucket_ms"]["not_ready"] == 60_000


def test_gap_over_180s_is_excluded(tmp_path):
    samples = [
        _sample(0, ts=BASE, mono=0),
        _sample(1, ts=BASE + 200_000, mono=200_000),
        _sample(2, ts=BASE + 230_000, mono=230_000),
    ]
    run = _write_run(tmp_path, samples, [BASE, BASE + 120_000, BASE + 240_000])
    result = _module().compute_run(run)
    assert result["primary_bucket_ms"]["gap_over_180s"] == 200_000
    assert result["qualified_ms"] == 30_000


def test_dr_stale_is_excluded(tmp_path):
    samples = [_sample(i, ts=BASE + 400_000 + i * 30_000, mono=i * 30_000) for i in range(10)]
    run = _write_run(tmp_path, samples, [BASE])
    result = _module().compute_run(run)
    assert result["primary_bucket_ms"]["dr_stale"] > 0
    assert result["qualified_ms"] == 0


def test_reconciliation_and_risk_stale_are_excluded(tmp_path):
    samples = [_sample(i, ts=BASE + i * 30_000, mono=i * 30_000) for i in range(5)]
    samples[2] = _sample(2, ts=BASE + 60_000, mono=60_000, reconciliation_age_ms=500_000)
    samples[3] = _sample(3, ts=BASE + 90_000, mono=90_000, checked_at_ms=BASE + 90_000 - 500_000)
    run = _write_run(tmp_path, samples, [BASE + i * 120_000 for i in range(4)])
    result = _module().compute_run(run)
    assert result["primary_bucket_ms"]["reconciliation_stale"] > 0
    assert result["primary_bucket_ms"]["risk_snapshot_stale"] > 0


def test_coverage_unresolved_balance_are_excluded(tmp_path):
    samples = [_sample(i, ts=BASE + i * 30_000, mono=i * 30_000) for i in range(7)]
    samples[2] = _sample(
        2, ts=BASE + 60_000, mono=60_000, coverage={"contiguous": False, "recorded_at_ms": BASE}
    )
    samples[3] = _sample(3, ts=BASE + 90_000, mono=90_000, unresolved_entries=["UNKNOWN_EXECUTION"])
    samples[4] = _sample(
        4, ts=BASE + 120_000, mono=120_000, balance_differences=[{"difference": "1"}]
    )
    run = _write_run(tmp_path, samples, [BASE + i * 120_000 for i in range(4)])
    result = _module().compute_run(run)
    assert result["primary_bucket_ms"]["coverage"] > 0
    assert result["primary_bucket_ms"]["unresolved"] > 0
    assert result["primary_bucket_ms"]["balance_unproven"] > 0


def test_primary_sum_equals_excluded_union(tmp_path):
    run = _healthy_run(tmp_path, count=30)
    result = _module().compute_run(run)
    assert result["primary_sum_ms"] == result["excluded_union_ms"]


def test_original_archive_reproduces_canonical():
    run = ROOT / "artifacts/foundation/phase6/windows-aborted/20261109T011441Z-efbba899"
    if not run.exists():
        return
    result = _module().compute_run(run)
    assert result["qualified_ms"] == 19_874_125
    assert result["active_ms"] == 39_598_016
    assert result["remaining_ms"] == 66_525_875
    assert result["primary_bucket_ms"]["not_ready"] == 9_741_442
    assert result["primary_bucket_ms"]["balance_unproven"] == 9_982_449
    assert result["primary_sum_ms"] == result["excluded_union_ms"] == 19_723_891
    assert (
        result["evidence_digest"]
        == "6498dad2cd7b28e10587a99e3a6c20c6dc16fee4d817e4660abb9ed05122f150"
    )
    assert result["run_valid"] is True
    assert result["dr_internal_breach"] is False


def test_final_three_segment_canonical():
    original = ROOT / "artifacts/foundation/phase6/windows-aborted/20261109T011441Z-efbba899"
    validation = ROOT / "artifacts/foundation/phase6/windows/20261110T030232Z-7a911829"
    segment5 = ROOT / "artifacts/foundation/phase6/windows/20261110T075417Z-cc17f251"
    if not all(path.exists() for path in (original, validation, segment5)):
        return
    module = _module()
    original_result = module.compute_run(original)
    validation_result = module.compute_run(validation, require_segment=True)
    segment5_result = module.compute_run(segment5, require_segment=True)
    payload = module.aggregate(original_result, [validation_result, segment5_result])
    assert validation_result["qualified_ms"] == 872_313
    assert segment5_result["qualified_ms"] == 73_664_222
    assert payload["cumulative"]["qualified_ms"] == 94_410_660
    assert payload["acceptance_satisfied"] is True


def test_wall_and_pause_across_two_runs(tmp_path):
    first = _healthy_run(tmp_path / "a", count=10, run_id="orig")
    module = _module()
    first_result = module.compute_run(first)
    second_start = BASE + 1_000_000
    second = _write_run(
        tmp_path / "b",
        [_sample(i, ts=second_start + i * 30_000, mono=i * 30_000) for i in range(10)],
        [second_start + i * 120_000 for i in range(4)],
        run_id="seg",
        segment_index=1,
        resume_from="orig",
        original_evidence_digest=first_result["evidence_digest"],
    )
    second_result = module.compute_run(second, require_segment=True)
    aggregate = module.aggregate(first_result, [second_result])
    assert aggregate["cumulative"]["wall_ms"] == 540_000
    assert aggregate["cumulative"]["elapsed_with_pauses_ms"] == 1_270_000
    assert aggregate["cumulative"]["pause_ms"] == 730_000
    assert aggregate["cumulative"]["qualified_ms"] == 540_000


def test_repeated_send_invalidates_acceptance(tmp_path):
    samples = [_sample(i, ts=BASE + i * 30_000, mono=i * 30_000) for i in range(5)]
    samples[2] = _sample(
        2,
        ts=BASE + 60_000,
        mono=60_000,
        evidence={
            "executions": {"count": 2, "keys_sha256": EXEC_HASH},
            "send_outcomes": {"repeated_send_commands": ["dup"]},
        },
    )
    run = _write_run(tmp_path, samples, [BASE + i * 120_000 for i in range(3)])
    module = _module()
    result = module.compute_run(run)
    assert result["strong_invariants"]["repeated_sends"] is True
    assert result["run_valid"] is False
    aggregate = module.aggregate(result, [])
    assert aggregate["cumulative"]["all_runs_valid"] is False
    assert aggregate["acceptance_satisfied"] is False


def test_dr_internal_breach_is_detected(tmp_path):
    samples = [
        _sample(0, ts=BASE, mono=0),
        _sample(1, ts=BASE + 400_000, mono=400_000),
        _sample(2, ts=BASE + 430_000, mono=430_000),
        _sample(3, ts=BASE + 460_000, mono=460_000),
    ]
    run = _write_run(tmp_path, samples, [BASE, BASE + 420_000])
    result = _module().compute_run(run)
    assert result["dr_internal_breach"] is True
    assert result["run_valid"] is False


def test_missing_run_dir_rejected(tmp_path):
    module = _module()
    with pytest.raises(module.AggregateInputError):
        module.compute_run(tmp_path / "missing")


def test_empty_observation_rejected(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "window.json").write_text('{"run_id": "r"}\n', encoding="utf-8")
    (run / "observation.jsonl").write_text("", encoding="utf-8")
    module = _module()
    with pytest.raises(module.AggregateInputError):
        module.compute_run(run)


def test_segment_manifest_required(tmp_path):
    run = _healthy_run(tmp_path, run_id="seg")
    module = _module()
    with pytest.raises(module.AggregateInputError):
        module.compute_run(run, require_segment=True)


def test_duplicate_segment_rejected(tmp_path):
    first = _healthy_run(tmp_path / "a", run_id="orig")
    module = _module()
    first_result = module.compute_run(first)
    second = _healthy_run(
        tmp_path / "b",
        run_id="seg",
        segment_index=1,
        resume_from="orig",
        original_evidence_digest=first_result["evidence_digest"],
    )
    segment = module.compute_run(second, require_segment=True)
    with pytest.raises(module.AggregateInputError):
        module.aggregate(first_result, [segment, segment])


def test_overlapping_runs_rejected(tmp_path):
    first = _healthy_run(tmp_path / "a", run_id="orig")
    module = _module()
    first_result = module.compute_run(first)
    second = _healthy_run(
        tmp_path / "b",
        run_id="seg",
        segment_index=1,
        resume_from="orig",
        original_evidence_digest=first_result["evidence_digest"],
    )
    segment = module.compute_run(second, require_segment=True)
    with pytest.raises(module.AggregateInputError):
        module.aggregate(first_result, [segment])


def test_expected_digest_mismatch_rejected(tmp_path):
    run = _healthy_run(tmp_path, run_id="orig")
    module = _module()
    with pytest.raises(module.AggregateInputError):
        module.compute_run(run, expected_digests={"orig": "0" * 64})


def test_dr_gap_300001_invalidates_without_sample_hit(tmp_path):
    samples = [_sample(i, ts=BASE + i * 30_000, mono=i * 30_000) for i in range(21)]
    run = _write_run(
        tmp_path,
        samples,
        [BASE, BASE + 300_001, BASE + 420_001, BASE + 540_001],
        run_id="dr",
    )
    result = _module().compute_run(run)
    assert result["max_backup_gap_ms"] == 300_001
    assert result["dr_internal_breach"] is True
    assert result["run_valid"] is False


def test_dr_gap_300000_boundary_is_allowed(tmp_path):
    samples = [_sample(i, ts=BASE + i * 30_000, mono=i * 30_000) for i in range(21)]
    run = _write_run(
        tmp_path,
        samples,
        [BASE, BASE + 300_000, BASE + 420_000, BASE + 540_000],
        run_id="dr",
    )
    result = _module().compute_run(run)
    assert result["max_backup_gap_ms"] == 300_000
    assert result["dr_internal_breach"] is False


def test_missing_monotonic_invalidates_whole_run(tmp_path):
    samples = [_sample(i, ts=BASE + i * 30_000, mono=i * 30_000) for i in range(10)]
    samples[5].pop("monotonic_ms")
    run = _write_run(tmp_path, samples, [BASE + i * 120_000 for i in range(4)])
    result = _module().compute_run(run)
    assert result["strong_invariants"]["clock_invalid"] is True
    assert result["run_valid"] is False


def test_coverage_unproven_during_not_ready_invalidates_whole_run(tmp_path):
    samples = [_sample(i, ts=BASE + i * 30_000, mono=i * 30_000) for i in range(10)]
    samples[3] = _sample(
        3,
        ts=BASE + 90_000,
        mono=90_000,
        ready=False,
        reasons=["PRIVATE_STREAM_COVERAGE_UNPROVEN"],
        coverage={},
    )
    run = _write_run(tmp_path, samples, [BASE + i * 120_000 for i in range(4)])
    result = _module().compute_run(run)
    assert result["strong_invariants"]["coverage_proof_missing"] is True
    assert result["run_valid"] is False
