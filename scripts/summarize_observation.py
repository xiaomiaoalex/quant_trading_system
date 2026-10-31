"""Summarize an observation JSONL file produced by observe_trading_runtime.py."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize a trading observation window")
    parser.add_argument("--input", default="artifacts/observation.jsonl")
    parser.add_argument("--min-hours", type=float, default=24.0)
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

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
        if "marker" in item:
            markers.append(item)
        else:
            samples.append(item)

    if not samples:
        raise SystemExit("no observation samples")

    first = samples[0]["ts_ms"]
    last = samples[-1]["ts_ms"]
    duration_hours = (last - first) / 3_600_000
    ready_samples = [s for s in samples if (s.get("health") or {}).get("body", {}).get("ready")]
    ready = sum(1 for s in samples if (s.get("health") or {}).get("body", {}).get("ready"))
    errors = [s for s in samples if s.get("error")]
    transitions = []
    previous = None
    for sample in samples:
        current = bool((sample.get("health") or {}).get("body", {}).get("ready"))
        if previous is not None and current != previous:
            transitions.append({"ts_ms": sample["ts_ms"], "ready": current})
        previous = current

    unresolved = Counter()
    for sample in samples:
        for reason in (sample.get("metrics") or {}).get("unresolved", []):
            unresolved[reason] += 1

    def peak(key: str) -> int:
        return max(
            [int((sample.get("metrics") or {}).get(key) or 0) for sample in samples],
            default=0,
        )

    summary = {
        "input": str(path),
        "samples": len(samples),
        "parse_errors": parse_errors,
        "first_ts_ms": first,
        "last_ts_ms": last,
        "duration_hours": round(duration_hours, 3),
        "meets_min_hours": duration_hours >= args.min_hours,
        "ready_samples": ready,
        "ready_ratio": round(ready / len(samples), 4),
        "readiness_transitions": transitions,
        "request_errors": len(errors),
        "max_reconciliation_age_ms": max(
            [
                int((sample.get("metrics") or {}).get("reconciliation_age_ms") or 0)
                for sample in samples
            ],
            default=0,
        ),
        "max_outbox_backlog": peak("outbox_backlog"),
        "max_unbooked_fills": peak("unbooked_fills"),
        "max_unknown_commands": peak("unknown_commands"),
        "max_balance_anomalies": peak("balance_anomalies"),
        "max_active_holds": peak("active_holds"),
        "unresolved_counts": dict(unresolved),
        "markers": markers,
    }

    final_ready = bool((samples[-1].get("health") or {}).get("body", {}).get("ready"))
    summary["final_ready"] = final_ready

    blockers = []
    if summary["max_balance_anomalies"] > 0:
        blockers.append("BALANCE_ANOMALY_OBSERVED")
    if summary["max_unknown_commands"] > 0:
        blockers.append("UNKNOWN_COMMAND_OBSERVED")
    if unresolved:
        blockers.append("UNRESOLVED_DIFFERENCES_OBSERVED")
    if not final_ready:
        blockers.append("NOT_READY_AT_END")
    if not summary["meets_min_hours"]:
        blockers.append("WINDOW_SHORTER_THAN_MIN")
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
