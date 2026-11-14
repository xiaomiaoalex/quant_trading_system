"""Container health probes and the Execution readiness heartbeat file.

The Execution process has no public HTTP port. It writes a small atomic status
file; the Compose healthcheck verifies that the process is still producing
fresh status. Trading readiness itself remains authoritative through Control's
``/health/trading`` endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


async def execution_health_loop(runtime, path: str, interval_seconds: float = 5.0) -> None:
    target = Path(path)
    interval = max(1.0, float(interval_seconds))
    while True:
        try:
            readiness = await runtime.readiness()
        except Exception:
            readiness = {"ready": False, "reasons": ["READINESS_CHECK_FAILED"]}
        _write_atomic(
            target,
            {
                "mode": "execution",
                "at_ms": int(time.time() * 1000),
                "pid": os.getpid(),
                "ready": bool(readiness.get("ready")),
                "reasons": readiness.get("reasons") or [],
                "stages": list(getattr(runtime, "stages", [])),
                "instance_owner": getattr(runtime, "owner", ""),
                "lease_token": getattr(runtime, "lease_token", None),
            },
        )
        await asyncio.sleep(interval)


def check_execution_health(path: str, max_age_seconds: float = 30.0) -> int:
    candidate = Path(path)
    if not candidate.is_file():
        print(json.dumps({"status": "unhealthy", "reason": "EXECUTION_HEALTH_FILE_MISSING"}))
        return 1
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(json.dumps({"status": "unhealthy", "reason": "EXECUTION_HEALTH_FILE_INVALID"}))
        return 1
    age_seconds = max(0.0, (time.time() * 1000 - int(payload.get("at_ms", 0))) / 1000)
    fresh = age_seconds <= max_age_seconds
    result = {
        "status": "healthy" if fresh else "unhealthy",
        "reason": "" if fresh else "EXECUTION_HEALTH_STALE",
        "age_seconds": round(age_seconds, 3),
        "ready": bool(payload.get("ready")),
        "reasons": payload.get("reasons") or [],
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if fresh else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="QTS container health probe")
    parser.add_argument("mode", choices=["execution"])
    parser.add_argument("path")
    parser.add_argument(
        "--max-age-seconds",
        type=float,
        default=float(os.getenv("QTS_HEALTH_MAX_AGE_SECONDS", "30")),
    )
    args = parser.parse_args()
    if args.mode == "execution":
        raise SystemExit(check_execution_health(args.path, args.max_age_seconds))


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        main()
