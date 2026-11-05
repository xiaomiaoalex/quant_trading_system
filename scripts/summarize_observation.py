"""Summarize an observation JSONL file produced by observe_trading_runtime.py.

Historical evidence stays readable with the default (compatibility) mode.
``--strict-release`` additionally binds the window to an immutable release
manifest (commit + image digest + Demo environment) and requires the explicit
operational thresholds to be present.  No threshold is lowered silently: every
value is a CLI parameter with the defaults documented below.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release_binding import (  # noqa: E402
    parse_timestamp_ms,
    validate_binding,
    validate_sample_identity,
    validate_window_anchor,
)

# A drill marker may be written in one piece or as an explicit start/end pair.
# Only the completed pair proves the runtime actually came back.
REQUIRED_MARKERS: dict[str, frozenset[str]] = {
    "runtime_restart": frozenset({"runtime_restart", "runtime_restart_end"}),
    "active_reconnect": frozenset({"active_reconnect"}),
}

# Initial approved floors.  The accepted 2026-11-01/02 Demo window recorded
# ready_ratio=0.6252, so 0.60 is a conservative floor that does not retroactively
# fail the historical evidence while still catching a real regression.
DEFAULT_MIN_READY_RATIO = 0.60
DEFAULT_STRICT_MIN_HOURS = 24.0
DEFAULT_MAX_RECONCILIATION_AGE_MS = 60_000.0
DEFAULT_MAX_OUTBOX_BACKLOG = 0
DEFAULT_MAX_UNBOOKED_FILLS = 0
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"[0-9a-f]{40}$")
DUPLICATE_KEYS = (
    "duplicate_fills",
    "duplicate_executions",
    "duplicate_orders",
    "resend_attempts",
    "resends",
    "overwrite_detected",
    "overwritten_fills",
    "replayed_fills",
)
DUPLICATE_CODE_HINTS = ("DUPLICATE", "RESEND", "OVERWRITE", "REPLAY")
STRICT_METRIC_KEYS = (
    "reconciliation_age_ms",
    "outbox_backlog",
    "unbooked_fills",
    "unknown_commands",
    "balance_anomalies",
)


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _ratio(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def _counter(value: object) -> int:
    """Normalize a metric that may be an int, a numeric string or a bool."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 1 if value else 0
    return 1 if value else 0


def _normalise_digest(value: object) -> str:
    text = str(value or "").strip()
    if "@" in text:
        text = text.rsplit("@", 1)[-1]
    return text


def _load_release_evidence(
    path: Path | None,
    *,
    expected_commit: str,
    expected_digest: str,
    expected_environment: str,
    expected_release_id: str = "",
    strict: bool,
) -> tuple[dict[str, Any] | None, list[str]]:
    blockers: list[str] = []
    if path is None:
        if strict:
            blockers.append("RELEASE_EVIDENCE_MISSING")
        return None, blockers
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, ["RELEASE_EVIDENCE_INVALID"]
    if not isinstance(payload, dict):
        return None, ["RELEASE_EVIDENCE_INVALID"]

    commit = str(payload.get("commit") or "").strip().lower()
    digest = _normalise_digest(payload.get("image_digest"))
    environment = str(payload.get("environment") or "").strip().lower()
    release_id = str(payload.get("release_id") or "").strip()

    if environment != expected_environment:
        blockers.append("RELEASE_ENVIRONMENT_NOT_DEMO")
    if expected_commit:
        if commit != expected_commit.strip().lower():
            blockers.append("RELEASE_COMMIT_MISMATCH")
    elif strict and not COMMIT_RE.fullmatch(commit):
        blockers.append("RELEASE_COMMIT_INVALID")
    if expected_digest:
        if digest != _normalise_digest(expected_digest):
            blockers.append("RELEASE_DIGEST_MISMATCH")
    elif strict and not DIGEST_RE.fullmatch(digest):
        blockers.append("RELEASE_DIGEST_INVALID")
    if strict and not release_id:
        blockers.append("RELEASE_ID_MISSING")
    if strict:
        # The declared flags are not evidence: the binding is checked against
        # the facts it carries (inspected containers, labels, image digest,
        # collector errors, health responses and a real timestamp).
        blockers.extend(
            validate_binding(
                payload,
                # An externally supplied release id anchors the label check;
                # without it the binding only proves internal consistency.
                expected_release_id=expected_release_id or release_id,
                expected_commit=expected_commit,
                expected_digest=expected_digest,
                expected_environment=expected_environment,
            )
        )

    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    binding = {
        "release_id": release_id,
        "commit": commit,
        "image_digest": digest,
        "environment": environment,
        "matched_expected_commit": bool(expected_commit)
        and commit == expected_commit.strip().lower(),
        "matched_expected_digest": bool(expected_digest)
        and digest == _normalise_digest(expected_digest),
        "runtime_inspected": payload.get("runtime_inspected") is True,
        "runtime_checks": payload.get("checks") if isinstance(payload.get("checks"), dict) else {},
        "runtime": runtime,
        "generated_at": payload.get("generated_at"),
        "generated_at_ms": parse_timestamp_ms(payload.get("generated_at")),
    }
    return binding, blockers


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize a trading observation window")
    parser.add_argument("--input", default="artifacts/observation.jsonl")
    parser.add_argument("--min-hours", type=float, default=24.0)
    parser.add_argument(
        "--max-gap-seconds",
        type=float,
        default=180.0,
        help="Maximum allowed gap between recorded samples (default: 180 seconds)",
    )
    parser.add_argument(
        "--min-ready-ratio",
        type=_ratio,
        default=DEFAULT_MIN_READY_RATIO,
        help="Minimum ready-sample ratio (default: 0.60; accepted Demo baseline 0.6252)",
    )
    parser.add_argument(
        "--max-reconciliation-age-ms",
        type=_positive_float,
        default=DEFAULT_MAX_RECONCILIATION_AGE_MS,
        help="Maximum reconciliation age in milliseconds (default: 60000)",
    )
    parser.add_argument(
        "--max-outbox-backlog",
        type=_non_negative_int,
        default=DEFAULT_MAX_OUTBOX_BACKLOG,
        help="Maximum outbox backlog in the final sample (default: 0)",
    )
    parser.add_argument(
        "--max-unbooked-fills",
        type=_non_negative_int,
        default=DEFAULT_MAX_UNBOOKED_FILLS,
        help="Maximum unbooked fills in the final sample (default: 0)",
    )
    parser.add_argument("--json-out", default="")
    parser.add_argument(
        "--release-evidence",
        type=Path,
        default=None,
        help="Release binding JSON: release_id, commit, image_digest, environment",
    )
    parser.add_argument("--expected-commit", default="")
    parser.add_argument("--expected-digest", default="")
    parser.add_argument("--expected-environment", default="demo")
    parser.add_argument(
        "--expected-release-id",
        default="",
        help="Release id from the verified manifest; anchors the binding label check",
    )
    parser.add_argument(
        "--strict-min-hours",
        type=_positive_float,
        default=DEFAULT_STRICT_MIN_HOURS,
        help="Minimum window for --strict-release (default and floor: 24 hours)",
    )
    parser.add_argument(
        "--strict-release",
        action="store_true",
        help="Require release evidence, explicit thresholds and zero duplicate evidence",
    )
    args = parser.parse_args()
    if args.strict_release and args.strict_min_hours < DEFAULT_STRICT_MIN_HOURS:
        parser.error(
            f"--strict-min-hours must be >= {DEFAULT_STRICT_MIN_HOURS:g} for --strict-release"
        )
    if not math.isfinite(args.min_hours) or args.min_hours <= 0:
        parser.error("--min-hours must be finite and positive")
    if not math.isfinite(args.max_gap_seconds) or args.max_gap_seconds <= 0:
        parser.error("--max-gap-seconds must be finite and positive")

    path = Path(args.input)
    if not path.exists():
        raise SystemExit(f"observation file not found: {path}")

    samples = []
    markers = []
    parse_errors = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        if (
            not isinstance(item, dict)
            or type(item.get("ts_ms")) is not int
            or item["ts_ms"] < 0
            or ("marker" in item and not isinstance(item["marker"], str))
        ):
            parse_errors += 1
            continue
        if "marker" in item:
            markers.append(item)
        else:
            samples.append(item)

    if not samples:
        raise SystemExit("no observation samples")

    first = samples[0]["ts_ms"]
    last = samples[-1]["ts_ms"]
    duration_hours = (last - first) / 3_600_000
    effective_min_hours = (
        max(args.min_hours, args.strict_min_hours) if args.strict_release else args.min_hours
    )
    ready = sum(1 for s in samples if (s.get("health") or {}).get("body", {}).get("ready"))
    errors = [s for s in samples if s.get("error")]
    sample_gaps = [right["ts_ms"] - left["ts_ms"] for left, right in zip(samples, samples[1:])]
    max_sample_gap_ms = max(sample_gaps, default=0)
    observed_markers = {marker["marker"] for marker in markers if first <= marker["ts_ms"] <= last}
    transitions = []
    previous = None
    for sample in samples:
        current = bool((sample.get("health") or {}).get("body", {}).get("ready"))
        if previous is not None and current != previous:
            transitions.append({"ts_ms": sample["ts_ms"], "ready": current})
        previous = current

    unresolved: Counter[str] = Counter()
    for sample in samples:
        for reason in (sample.get("metrics") or {}).get("unresolved", []):
            unresolved[reason] += 1

    def peak(key: str) -> int:
        return max(
            [_counter((sample.get("metrics") or {}).get(key)) for sample in samples],
            default=0,
        )

    def final(key: str) -> int:
        return _counter((samples[-1].get("metrics") or {}).get(key))

    ages = [
        int(value)
        for sample in samples
        if isinstance(value := (sample.get("metrics") or {}).get("reconciliation_age_ms"), int)
        and not isinstance(value, bool)
    ]
    age_evidence_present = any(
        "reconciliation_age_ms" in (sample.get("metrics") or {}) for sample in samples
    )
    final_age = (samples[-1].get("metrics") or {}).get("reconciliation_age_ms")
    final_age_value = (
        int(final_age) if isinstance(final_age, int) and not isinstance(final_age, bool) else None
    )
    strict_metrics_missing: set[str] = set()
    strict_metrics_invalid: set[str] = set()
    if args.strict_release:
        for sample in samples:
            metrics = sample.get("metrics")
            if not isinstance(metrics, dict):
                strict_metrics_missing.update(STRICT_METRIC_KEYS)
                continue
            for key in STRICT_METRIC_KEYS:
                if key not in metrics:
                    strict_metrics_missing.add(key)
                elif key == "reconciliation_age_ms":
                    if metrics[key] is not None and (
                        not isinstance(metrics[key], int) or isinstance(metrics[key], bool)
                    ):
                        strict_metrics_invalid.add(key)
                elif not isinstance(metrics[key], int) or isinstance(metrics[key], bool):
                    strict_metrics_invalid.add(key)

    duplicate_samples = 0
    for sample in samples:
        metrics = sample.get("metrics") or {}
        if any(_counter(metrics.get(key)) > 0 for key in DUPLICATE_KEYS):
            duplicate_samples += 1
            continue
        if any(
            hint in str(code).upper()
            for code in metrics.get("unresolved", [])
            for hint in DUPLICATE_CODE_HINTS
        ):
            duplicate_samples += 1

    release_binding, release_blockers = _load_release_evidence(
        args.release_evidence,
        expected_commit=args.expected_commit,
        expected_digest=args.expected_digest,
        expected_environment=args.expected_environment,
        expected_release_id=args.expected_release_id,
        strict=args.strict_release,
    )
    # Every sample must have been served by the bound runtime; the identity is
    # compared before the summary is assembled so that a mismatch is reported
    # as a blocker and not as a missing field.
    sample_identity_blockers: list[str] = []
    if args.strict_release and release_binding is not None:
        reference = release_binding.get("runtime") or {}
        for sample in samples:
            for code in validate_sample_identity(sample, binding_runtime=reference):
                if code not in sample_identity_blockers:
                    sample_identity_blockers.append(code)
            if len(sample_identity_blockers) >= 12:
                break
    summary = {
        "input": str(path),
        "samples": len(samples),
        "parse_errors": parse_errors,
        "required_markers": sorted(REQUIRED_MARKERS),
        "observed_markers": sorted(observed_markers),
        "max_sample_gap_ms": max_sample_gap_ms,
        "allowed_sample_gap_ms": args.max_gap_seconds * 1000,
        "first_ts_ms": first,
        "last_ts_ms": last,
        "duration_hours": round(duration_hours, 3),
        "min_hours": args.min_hours,
        "strict_min_hours": args.strict_min_hours if args.strict_release else None,
        "effective_min_hours": effective_min_hours,
        "meets_min_hours": duration_hours >= effective_min_hours,
        "ready_samples": ready,
        "ready_ratio": round(ready / len(samples), 4),
        "min_ready_ratio": args.min_ready_ratio,
        "readiness_transitions": transitions,
        "request_errors": len(errors),
        "max_reconciliation_age_ms": max(ages, default=0),
        "max_allowed_reconciliation_age_ms": args.max_reconciliation_age_ms,
        "reconciliation_age_evidence_present": age_evidence_present,
        "final_reconciliation_age_ms": final_age_value,
        "max_outbox_backlog": peak("outbox_backlog"),
        "final_outbox_backlog": final("outbox_backlog"),
        "max_allowed_outbox_backlog": args.max_outbox_backlog,
        "max_unbooked_fills": peak("unbooked_fills"),
        "final_unbooked_fills": final("unbooked_fills"),
        "max_allowed_unbooked_fills": args.max_unbooked_fills,
        "max_unknown_commands": peak("unknown_commands"),
        "max_balance_anomalies": peak("balance_anomalies"),
        "max_active_holds": peak("active_holds"),
        "duplicate_or_resend_samples": duplicate_samples,
        "strict_metrics_checked": sorted(STRICT_METRIC_KEYS) if args.strict_release else [],
        "sample_identity_checked": bool(args.strict_release),
        "sample_identity_blockers": sample_identity_blockers,
        "strict_metrics_missing": sorted(strict_metrics_missing),
        "strict_metrics_invalid": sorted(strict_metrics_invalid),
        "unresolved_counts": dict(unresolved),
        "markers": markers,
    }

    final_ready = bool((samples[-1].get("health") or {}).get("body", {}).get("ready"))
    summary["final_ready"] = final_ready
    summary["release_binding"] = release_binding
    summary["strict_release"] = bool(args.strict_release)

    blockers = []
    if parse_errors:
        blockers.append("INVALID_EVIDENCE_LINES")
    if any(gap <= 0 for gap in sample_gaps):
        blockers.append("NON_MONOTONIC_SAMPLES")
    if max_sample_gap_ms > args.max_gap_seconds * 1000:
        blockers.append("SAMPLE_GAP_EXCEEDED")
    for marker, aliases in sorted(REQUIRED_MARKERS.items()):
        if not aliases & observed_markers:
            blockers.append(f"MISSING_MARKER:{marker}")
    if summary["max_balance_anomalies"] > 0:
        blockers.append("BALANCE_ANOMALY_OBSERVED")
    if summary["max_unknown_commands"] > 0:
        blockers.append("UNKNOWN_COMMAND_OBSERVED")
    if unresolved:
        blockers.append("UNRESOLVED_DIFFERENCES_OBSERVED")
    if summary["ready_ratio"] < args.min_ready_ratio:
        blockers.append("READY_RATIO_BELOW_THRESHOLD")
    if age_evidence_present or args.strict_release:
        if final_age_value is None:
            blockers.append("RECONCILIATION_AGE_UNKNOWN")
        if summary["max_reconciliation_age_ms"] > args.max_reconciliation_age_ms:
            blockers.append("RECONCILIATION_AGE_EXCEEDED")
    if summary["final_outbox_backlog"] > args.max_outbox_backlog:
        blockers.append("OUTBOX_BACKLOG_AT_END")
    if summary["final_unbooked_fills"] > args.max_unbooked_fills:
        blockers.append("UNBOOKED_FILLS_AT_END")
    if duplicate_samples:
        blockers.append("DUPLICATE_OR_RESEND_EVIDENCE")
    if strict_metrics_missing:
        blockers.append("STRICT_METRICS_MISSING:" + ",".join(sorted(strict_metrics_missing)))
    if strict_metrics_invalid:
        blockers.append("STRICT_METRICS_INVALID:" + ",".join(sorted(strict_metrics_invalid)))
    for key in sorted(strict_metrics_missing):
        blockers.append(f"STRICT_METRICS_MISSING:{key}")
    for key in sorted(strict_metrics_invalid):
        blockers.append(f"STRICT_METRICS_INVALID:{key}")
    blockers.extend(release_blockers)
    blockers.extend(sample_identity_blockers)
    if args.strict_release and release_binding is not None:
        blockers.extend(
            validate_window_anchor(
                generated_at_ms=release_binding.get("generated_at_ms"),
                first_sample_ts_ms=first,
                last_sample_ts_ms=last,
            )
        )
    if not final_ready:
        blockers.append("NOT_READY_AT_END")
    if not summary["meets_min_hours"]:
        blockers.append("WINDOW_SHORTER_THAN_MIN")
    if args.strict_release and duration_hours < args.strict_min_hours:
        blockers.append("STRICT_WINDOW_SHORTER_THAN_24H")
    summary["blockers"] = blockers
    summary["ok"] = not blockers

    print(json.dumps(summary, indent=2))
    if args.json_out:
        target = Path(args.json_out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
