"""Phase-6 segmented cumulative acceptance aggregate (qualified-v3).

Reads raw observation/dr/backup evidence (never trusting self-reported window
booleans), computes qualified runtime with integer monotonic milliseconds, and
emits a fail-closed aggregate for the original run plus continuation segments.

A sample pair (a -> b) counts only when both endpoints satisfy every condition.
marker -> first sample and last sample -> declared stop are not counted.

Fail-closed rules (reviewer round14):
* a run directory, observation file, or required segment/window manifest that
  is missing/empty raises AggregateInputError instead of producing a valid
  zero-sample run;
* duplicate run ids/directories, duplicate segment indexes, overlapping run
  intervals, a segment whose resume_from/original_evidence_digest does not
  match the original, and an expected-digest mismatch are rejected;
* internal/head verified-backup gaps are read directly from the backup journal;
  a gap >300000ms invalidates the run even when no sample lands in it;
* missing/non-monotonic clock fields, incomplete coverage proof, or a
  PRIVATE_STREAM_COVERAGE_UNPROVEN reason invalidate the whole run at any
  readiness state; the affected intervals may not simply be skipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

COVERAGE_FRESH_MS = 780_000
DR_RPO_MS = 300_000
RECONCILE_FRESH_MS = 450_000
SAMPLE_GAP_MS = 180_000
TARGET_MS = 86_400_000
RULE_VERSION = "qualified-v3"

PRIMARY_ORDER = [
    "not_ready",
    "gap_over_180s",
    "dr_stale",
    "reconciliation_stale",
    "risk_snapshot_stale",
    "coverage",
    "unresolved",
    "balance_unproven",
    "field_invalid",
    "metrics_missing",
]

SEGMENT_REQUIRED_FIELDS = {
    "kind",
    "segment_run_id",
    "resume_from",
    "segment_index",
    "observation_only",
    "original_evidence_digest",
    "db_identity_hashes",
    "lease_before",
    "lease_after",
    "dr_ready_cycles",
    "qualified_rule_version",
}


class AggregateInputError(RuntimeError):
    """An input/identity/manifest failure that must not produce acceptance."""


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AggregateInputError(f"MANIFEST_INVALID:{path}:{type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise AggregateInputError(f"MANIFEST_NOT_OBJECT:{path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_digest(files: list[dict]) -> str:
    pairs = [[item["path"], item["sha256"]] for item in files]
    return hashlib.sha256(
        json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _evidence_files(run_dir: Path) -> list[dict]:
    files = []
    for path in sorted(run_dir.rglob("*")):
        if path.is_file():
            files.append({"path": path.relative_to(run_dir).as_posix(), "sha256": _sha256(path)})
    return files


def _backup_times(run_dir: Path) -> list[int]:
    """Canonical RPO source: verified successful backup completed_at_ms."""
    times: list[int] = []
    for row in _load_jsonl(run_dir / "dr" / "backup" / "backup-journal.jsonl"):
        if row.get("status") != "succeeded":
            continue
        backup = row.get("backup") or {}
        if backup.get("verified") is False:
            continue
        value = backup.get("completed_at_ms", row.get("completed_at_ms"))
        if isinstance(value, int):
            times.append(value)
    return sorted(set(times))


def _supervisor_flags(run_dir: Path) -> dict[str, bool]:
    flags = {"abort": False, "restart": False}
    for row in _load_jsonl(run_dir / "supervisor.jsonl"):
        if row.get("kind") == "abort":
            flags["abort"] = True
        if row.get("kind") == "stack":
            for count in (row.get("restart_counts") or {}).values():
                if int(count or 0) > 0:
                    flags["restart"] = True
    return flags


def _last_at_or_before(times: list[int], value: int) -> int | None:
    lo, hi = 0, len(times) - 1
    answer = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if times[mid] <= value:
            answer = times[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return answer


def _dr_metrics(run_dir: Path, start_ms: int, end_ms: int) -> dict[str, Any]:
    """Head/internal/tail from the verified backup journal, not from samples."""
    times = _backup_times(run_dir)
    before_start = _last_at_or_before(times, start_ms)
    head_ms = None if before_start is None else start_ms - before_start
    inside = [value for value in times if start_ms <= value <= end_ms]
    gaps = [later - earlier for earlier, later in zip(inside, inside[1:])]
    max_gap_ms = max(gaps) if gaps else 0
    last = _last_at_or_before(times, end_ms)
    tail_ms = None if last is None else end_ms - last
    return {
        "head_ms": head_ms,
        "tail_ms": tail_ms,
        "max_backup_gap_ms": max_gap_ms,
        "head_breach": before_start is None or head_ms > DR_RPO_MS,
        "internal_breach": max_gap_ms > DR_RPO_MS,
        "tail_breach": last is None or tail_ms > DR_RPO_MS,
    }


def _coverage_complete(coverage: Any) -> bool:
    if not isinstance(coverage, dict):
        return False
    return (
        coverage.get("contiguous") is True
        and isinstance(coverage.get("recorded_at_ms"), int)
        and bool(coverage.get("source"))
        and bool(coverage.get("connected_from"))
        and bool(coverage.get("buffered_through"))
        and bool(coverage.get("covered_through"))
        and bool(coverage.get("generation"))
    )


def _sample_reasons(sample: dict, backup_times: list[int]) -> set[str]:
    reasons: set[str] = set()
    ts = int(sample.get("ts_ms") or 0)
    health = (sample.get("health") or {}).get("body") or {}
    top_ready = sample.get("ready")
    ready = health.get("ready")
    if top_ready is not None and top_ready != ready:
        reasons.add("field_invalid")
    if ready is not True or (health.get("reasons") or []):
        reasons.add("not_ready")
    metrics = sample.get("metrics")
    if (
        sample.get("metrics_status_code") != 200
        or not isinstance(metrics, dict)
        or not metrics.get("evidence")
    ):
        reasons.add("metrics_missing")
        metrics = metrics if isinstance(metrics, dict) else {}
    if (metrics.get("unresolved_entries") or []) or (metrics.get("unresolved") or []):
        reasons.add("unresolved")
    coverage = metrics.get("coverage") or {}
    recorded = coverage.get("recorded_at_ms")
    if not _coverage_complete(coverage) or not (0 <= ts - recorded <= COVERAGE_FRESH_MS):
        reasons.add("coverage")
    last = _last_at_or_before(backup_times, ts)
    if last is None or not (0 <= ts - last <= DR_RPO_MS):
        reasons.add("dr_stale")
    recon_age = metrics.get("reconciliation_age_ms")
    if not isinstance(recon_age, int) or not (0 <= recon_age <= RECONCILE_FRESH_MS):
        reasons.add("reconciliation_stale")
    risk = sample.get("risk") or {}
    checked = risk.get("checked_at_ms")
    if (
        risk.get("status") != "READY"
        or risk.get("valuation_complete") is not True
        or not isinstance(checked, int)
        or not (0 <= ts - checked <= RECONCILE_FRESH_MS)
    ):
        reasons.add("risk_snapshot_stale")
    if int(metrics.get("balance_anomalies") or 0) != 0:
        reasons.add("balance_unproven")
    if metrics.get("balance_differences"):
        reasons.add("balance_unproven")
    return reasons


def _primary(reasons: set[str]) -> str:
    for name in PRIMARY_ORDER:
        if name in reasons:
            return name
    return "field_invalid"


def _validate_identity(segment: dict, run_dir: Path, samples: list[dict]) -> None:
    identity = segment.get("db_identity_hashes") or {}
    if not isinstance(identity, dict) or not identity:
        raise AggregateInputError("SEGMENT_IDENTITY_MISSING")
    resume_path = run_dir / "resume-preflight.json"
    if not resume_path.is_file():
        raise AggregateInputError(f"RESUME_PREFLIGHT_MISSING:{resume_path}")
    resume = _read_json(resume_path)
    pre_identity = resume.get("identity_hashes") or {}
    pre_counts = resume.get("counts") or {}
    if identity != pre_identity:
        raise AggregateInputError("SEGMENT_IDENTITY_PREFLIGHT_MISMATCH")
    metrics = samples[0].get("metrics") or {}
    evidence = metrics.get("evidence") or {}
    for name, key in (
        ("executions", "keys_sha256"),
        ("ledger", "entry_ids_sha256"),
    ):
        expected = pre_identity.get(name)
        actual = (evidence.get(name) or {}).get(key)
        if not expected or not actual or expected != actual:
            raise AggregateInputError(f"SEGMENT_IDENTITY_MISMATCH:{name}")
    # Events are append-only: a legitimate account.reconciled may land between
    # the preflight snapshot and the first observation sample. The first sample
    # may therefore have more events than the anchor; executions/ledger stay exact.
    pre_events = pre_counts.get("events")
    first_events = (evidence.get("events") or {}).get("count")
    if not isinstance(pre_events, int) or not isinstance(first_events, int):
        raise AggregateInputError("SEGMENT_EVENT_COUNT_MISSING")
    if first_events < pre_events:
        raise AggregateInputError(f"SEGMENT_EVENT_COUNT_REGRESSION:{pre_events}->{first_events}")


def compute_run(
    run_dir: Path,
    *,
    require_segment: bool = False,
    expected_digests: dict[str, str] | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise AggregateInputError(f"RUN_DIR_MISSING:{run_dir}")
    observation = run_dir / "observation.jsonl"
    if not observation.is_file():
        raise AggregateInputError(f"OBSERVATION_MISSING:{observation}")
    samples = [r for r in _load_jsonl(observation) if r.get("kind") == "sample"]
    if not samples:
        raise AggregateInputError(f"OBSERVATION_EMPTY:{observation}")
    # The historical original archive predates the window.json convention; the
    # segment manifests are the required proofs. The window, when present, only
    # supplies the run id; it is never trusted as a pass/fail boolean.
    window_path = run_dir / "window.json"
    window = _read_json(window_path) if window_path.is_file() else {}

    segment = None
    if require_segment:
        segment_path = run_dir / "segment.json"
        if not segment_path.is_file():
            raise AggregateInputError(f"SEGMENT_MANIFEST_MISSING:{segment_path}")
        segment = _read_json(segment_path)
        missing = SEGMENT_REQUIRED_FIELDS - set(segment)
        if missing:
            raise AggregateInputError(
                "SEGMENT_MANIFEST_FIELDS_MISSING:" + ",".join(sorted(missing))
            )
        _validate_identity(segment, run_dir, samples)

    run_id = (segment or {}).get("segment_run_id") or (window or {}).get("run_id") or run_dir.name
    files = _evidence_files(run_dir)
    evidence_digest = _canonical_digest(files)
    if expected_digests and run_id in expected_digests:
        if expected_digests[run_id] != evidence_digest:
            raise AggregateInputError(
                f"EVIDENCE_DIGEST_MISMATCH:{run_id}:{expected_digests[run_id]}:{evidence_digest}"
            )

    backups = _backup_times(run_dir)
    active = qualified = 0
    primary: dict[str, int] = {name: 0 for name in PRIMARY_ORDER}
    overlaps: dict[str, int] = {}
    intervals: list[dict] = []
    last_qualified_end_ms: int | None = None
    clock_invalid = False
    for a, b in zip(samples, samples[1:]):
        ma, mb = a.get("monotonic_ms"), b.get("monotonic_ms")
        if not isinstance(ma, int) or not isinstance(mb, int) or mb <= ma:
            clock_invalid = True
            continue
        duration = mb - ma
        active += duration
        reasons_a = _sample_reasons(a, backups)
        reasons_b = _sample_reasons(b, backups)
        reasons = reasons_a | reasons_b
        if duration > SAMPLE_GAP_MS:
            reasons.add("gap_over_180s")
        if not reasons:
            qualified += duration
            last_qualified_end_ms = int(b.get("ts_ms") or 0)
            continue
        bucket = _primary(reasons)
        primary[bucket] += duration
        for reason in reasons:
            overlaps[reason] = overlaps.get(reason, 0) + duration
        intervals.append(
            {
                "start_ms": int(a.get("ts_ms") or 0),
                "end_ms": int(b.get("ts_ms") or 0),
                "duration_ms": duration,
                "reasons": sorted(reasons),
                "primary": bucket,
                "seq_start": a.get("seq"),
                "seq_end": b.get("seq"),
            }
        )
    if any(not isinstance(sample.get("monotonic_ms"), int) for sample in samples):
        clock_invalid = True
    excluded = active - qualified
    first_ts = int(samples[0].get("ts_ms") or 0)
    last_ts = int(samples[-1].get("ts_ms") or 0)
    dr = _dr_metrics(run_dir, first_ts, last_ts)
    supervisor = _supervisor_flags(run_dir)
    repeated_sends = exec_regression = ready_unresolved = coverage_proof_missing = False
    field_invalid = False
    last_exec = -1
    for sample in samples:
        metrics = sample.get("metrics") or {}
        evidence = metrics.get("evidence") or {}
        if (evidence.get("send_outcomes") or {}).get("repeated_send_commands"):
            repeated_sends = True
        count = (evidence.get("executions") or {}).get("count")
        if isinstance(count, int):
            if count < last_exec:
                exec_regression = True
            last_exec = max(last_exec, count)
        health = (sample.get("health") or {}).get("body") or {}
        health_reasons = health.get("reasons") or []
        if health.get("ready") is True and (
            (metrics.get("unresolved_entries") or []) or (metrics.get("unresolved") or [])
        ):
            ready_unresolved = True
        if "PRIVATE_STREAM_COVERAGE_UNPROVEN" in health_reasons:
            coverage_proof_missing = True
        if not _coverage_complete(metrics.get("coverage")):
            coverage_proof_missing = True
        top_ready = sample.get("ready")
        if top_ready is not None and top_ready != health.get("ready"):
            field_invalid = True
        if "field_invalid" in _sample_reasons(sample, backups):
            field_invalid = True
    strong = {
        "repeated_sends": repeated_sends,
        "execution_regression": exec_regression,
        "ready_unresolved": ready_unresolved,
        "coverage_proof_missing": coverage_proof_missing,
        "supervisor_abort": supervisor["abort"],
        "container_restart": supervisor["restart"],
        "clock_invalid": clock_invalid,
        "field_invalid": field_invalid,
    }
    dr_internal_breach = bool(dr["internal_breach"])
    run_valid = not any(strong.values()) and not dr_internal_breach and not bool(dr["head_breach"])
    return {
        "run_dir": str(run_dir),
        "run_id": run_id,
        "segment_index": (segment or {}).get("segment_index"),
        "resume_from": (segment or {}).get("resume_from"),
        "original_evidence_digest": (segment or {}).get("original_evidence_digest"),
        "rule_version": RULE_VERSION,
        "samples": len(samples),
        "first_ts_ms": first_ts,
        "last_ts_ms": last_ts,
        "wall_ms": max(0, last_ts - first_ts),
        "active_ms": active,
        "qualified_ms": qualified,
        "excluded_union_ms": excluded,
        "remaining_ms": max(0, TARGET_MS - qualified),
        "primary_bucket_ms": primary,
        "primary_sum_ms": sum(primary.values()),
        "overlap_ms": {key: overlaps[key] for key in sorted(overlaps)},
        "intervals": intervals,
        "last_qualified_end_ms": last_qualified_end_ms,
        "dr_head_ms": dr["head_ms"],
        "dr_tail_ms": dr["tail_ms"],
        "max_backup_gap_ms": dr["max_backup_gap_ms"],
        "dr_head_breach": bool(dr["head_breach"]),
        "dr_internal_breach": dr_internal_breach,
        "dr_tail_breach": bool(dr["tail_breach"]),
        "strong_invariants": strong,
        "run_valid": run_valid,
        "evidence_digest": evidence_digest,
        "evidence_files": files,
        "satisfied": qualified >= TARGET_MS,
    }


def aggregate(original: dict, segments: list[dict]) -> dict:
    runs = [original, *segments]
    run_ids = [run["run_id"] for run in runs]
    if len(run_ids) != len(set(run_ids)):
        raise AggregateInputError("DUPLICATE_RUN_ID:" + ",".join(run_ids))
    dirs = [run["run_dir"] for run in runs]
    if len(dirs) != len(set(dirs)):
        raise AggregateInputError("DUPLICATE_RUN_DIR")
    for segment in segments:
        if not segment.get("resume_from"):
            raise AggregateInputError("SEGMENT_RESUME_FROM_MISSING:" + str(segment.get("run_id")))
        if segment["resume_from"] != original["run_id"]:
            raise AggregateInputError(
                f"SEGMENT_RESUME_FROM_MISMATCH:{segment['run_id']}:{segment['resume_from']}"
            )
        if (
            segment.get("original_evidence_digest")
            and segment["original_evidence_digest"] != original["evidence_digest"]
        ):
            raise AggregateInputError(f"SEGMENT_ORIGINAL_DIGEST_MISMATCH:{segment['run_id']}")
        if not isinstance(segment.get("segment_index"), int):
            raise AggregateInputError("SEGMENT_INDEX_MISSING:" + str(segment.get("run_id")))
    indexes = [segment["segment_index"] for segment in segments]
    if len(indexes) != len(set(indexes)):
        raise AggregateInputError("DUPLICATE_SEGMENT_INDEX")
    ordered = sorted(runs, key=lambda run: run["first_ts_ms"])
    for earlier, later in zip(ordered, ordered[1:]):
        if later["first_ts_ms"] <= earlier["last_ts_ms"]:
            raise AggregateInputError(
                f"OVERLAPPING_RUN_INTERVALS:{earlier['run_id']}:{later['run_id']}"
            )
    cumulative = sum(run["qualified_ms"] for run in runs)
    active = sum(run["active_ms"] for run in runs)
    wall = sum(run["wall_ms"] for run in runs)
    starts = [run["first_ts_ms"] for run in runs if run["first_ts_ms"]]
    ends = [run["last_ts_ms"] for run in runs if run["last_ts_ms"]]
    elapsed = (max(ends) - min(starts)) if starts and ends else 0
    all_valid = all(run["run_valid"] for run in runs)
    cumulative_block = {
        "active_ms": active,
        "wall_ms": wall,
        "elapsed_with_pauses_ms": elapsed,
        "pause_ms": max(0, elapsed - wall),
        "qualified_ms": cumulative,
        "remaining_ms": max(0, TARGET_MS - cumulative),
        "qualified_satisfied": cumulative >= TARGET_MS,
        "all_runs_valid": all_valid,
        "dr_internal_breach": any(run["dr_internal_breach"] for run in runs),
        "dr_head_breach": any(run["dr_head_breach"] for run in runs),
        "dr_tail_breach": any(run["dr_tail_breach"] for run in runs),
    }
    acceptance = (
        cumulative_block["qualified_satisfied"]
        and all_valid
        and not cumulative_block["dr_internal_breach"]
        and not cumulative_block["dr_head_breach"]
    )
    return {
        "kind": "phase6_segment_aggregate",
        "schema_version": 4,
        "rule_version": RULE_VERSION,
        "original": original,
        "segments": segments,
        "cumulative": cumulative_block,
        "acceptance_satisfied": acceptance,
        "note": "acceptance_satisfied requires all invariants and independent tester/reviewer sign-off; this tool never advances phase 6.1/6.2 by itself",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase-6 segment aggregate (qualified-v3)")
    parser.add_argument("--original", required=True, type=Path)
    parser.add_argument("--segment", action="append", default=[], type=Path)
    parser.add_argument(
        "--expect-digest",
        action="append",
        default=[],
        metavar="RUN_ID=SHA256",
        help="reject the run when its evidence digest differs",
    )
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    expected: dict[str, str] = {}
    for item in args.expect_digest:
        run_id, separator, digest = item.partition("=")
        if not separator or not run_id or len(digest) != 64:
            parser.error(f"--expect-digest must be RUN_ID=64-hex: {item}")
        expected[run_id] = digest
    try:
        original = compute_run(args.original, expected_digests=expected)
        segments = [
            compute_run(path, require_segment=True, expected_digests=expected)
            for path in args.segment
        ]
        payload = aggregate(original, segments)
    except AggregateInputError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "rule_version": RULE_VERSION,
                "original_qualified_ms": original["qualified_ms"],
                "original_active_ms": original["active_ms"],
                "original_remaining_ms": original["remaining_ms"],
                "primary": original["primary_bucket_ms"],
                "primary_sum_ms": original["primary_sum_ms"],
                "evidence_digest": original["evidence_digest"],
                "cumulative": payload["cumulative"],
                "acceptance_satisfied": payload["acceptance_satisfied"],
                "out": str(args.out),
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
