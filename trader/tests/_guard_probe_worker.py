"""Two-process worker for the phase-6 startup-guard tests.

Modes:
  race          acquire, report, hold briefly, release
  crash         acquire, report, exit without release (OS drops the lock)
  crash-orphan  acquire, report, leave a marker, exit without release

The worker touches only the guard file and its own result/marker files. It
never imports the venue, Compose or DR code paths.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trader.ops.startup_guard import GuardHeldError, StartupGuard  # noqa: E402


def main() -> int:
    mode, path, barrier, out = sys.argv[1:5]
    marker = sys.argv[5] if len(sys.argv) > 5 else None
    guard = StartupGuard(
        Path(path),
        run_id=f"worker-{os.getpid()}",
        entry="test-worker",
        orphan_probe=lambda: [],
    )
    while not Path(barrier).exists():
        time.sleep(0.005)
    try:
        guard.acquire()
    except GuardHeldError:
        Path(out).write_text("held", encoding="utf-8")
        return 0

    Path(out).write_text("acquired", encoding="utf-8")
    if mode == "race":
        time.sleep(1.0)
        guard.release()
        return 0
    if mode == "crash":
        os._exit(0)
    if mode == "crash-orphan":
        if marker is None:
            raise SystemExit("crash-orphan requires a marker path")
        Path(marker).write_text("orphan", encoding="utf-8")
        os._exit(0)
    raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    raise SystemExit(main())
