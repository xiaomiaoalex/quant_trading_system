"""Engineer tests for the phase-6 startup guard.

Covers P6-R10-DUPLICATE-WINDOW-START-20261108: an atomic, non-blocking
exclusive guard that every entry path shares, taken before any
Demo/account/Compose/DR side effect, plus orphan detection after an owner
crash. The two-process tests use a real OS lock in separate processes; the
real-entry tests execute ``scripts/sandbox_supervisor.py --guard-probe`` and
assert that no run directory or task container is created.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from trader.ops.startup_guard import (
    GuardHeldError,
    OrphanCheckUnavailable,
    OrphanDetectedError,
    StartupGuard,
    default_guard_path,
    probe_orphans,
)

ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).resolve().parent / "_guard_probe_worker.py"
WINDOWS = ROOT / "artifacts" / "foundation" / "phase6" / "windows"


def _docker_available() -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=30,
            ).returncode
            == 0
        )
    except Exception:
        return False


def _running_task_containers() -> list[str]:
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return [n for n in (result.stdout or "").splitlines() if "qts_p6_sandbox" in n]


def _window_dirs() -> set[str]:
    if not WINDOWS.exists():
        return set()
    return {p.name for p in WINDOWS.iterdir() if p.is_dir()}


def _require_clean_production_guard() -> None:
    """Skip only when a real window is running; fail on an orphan."""

    if not _docker_available():
        pytest.skip("docker daemon unavailable for the real-entry guard probe")
    probe = StartupGuard(
        None, run_id="guard-test-precheck", entry="test-precheck", orphan_probe=lambda: []
    )
    try:
        probe.acquire()
    except GuardHeldError:
        pytest.skip("a real supervisor currently holds the production guard")
    finally:
        if probe.held:
            probe.release()


def _start_worker(mode: str, path: Path, barrier: Path, out: Path, marker: Path | None = None):
    argv = [sys.executable, str(WORKER), mode, str(path), str(barrier), str(out)]
    if marker is not None:
        argv.append(str(marker))
    return subprocess.Popen(
        argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def _read_out(path: Path, timeout: float = 20.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        time.sleep(0.01)
    raise AssertionError(f"worker produced no result: {path}")


def test_default_guard_path_is_stable_and_run_id_free():
    path = default_guard_path()
    assert path.name == "phase6-sandbox-start.lock"
    assert "2026" not in path.name
    assert default_guard_path() == path


def test_acquire_release_cycle(tmp_path):
    path = tmp_path / "guard.lock"
    first = StartupGuard(path, run_id="r1", entry="unit", orphan_probe=lambda: [])
    first.acquire()
    assert first.held
    metadata = json.loads(path.read_text(encoding="utf-8"))
    assert metadata["run_id"] == "r1"
    assert metadata["pid"] == os.getpid()
    first.release()
    assert not first.held

    second = StartupGuard(path, run_id="r2", entry="unit", orphan_probe=lambda: [])
    second.acquire()
    assert second.held
    second.release()


def test_metadata_carries_no_secret_material(tmp_path):
    secret = os.environ.get("BINANCE_SECRET_KEY", "")
    path = tmp_path / "guard.lock"
    guard = StartupGuard(path, run_id="r1", entry="unit", orphan_probe=lambda: [])
    guard.acquire()
    raw = path.read_text(encoding="utf-8")
    guard.release()
    assert "BINANCE" not in raw
    if secret:
        assert secret not in raw


def test_two_process_contention_exactly_one_owner(tmp_path):
    path = tmp_path / "guard.lock"
    barrier = tmp_path / "go"
    out1 = tmp_path / "one"
    out2 = tmp_path / "two"
    first = _start_worker("race", path, barrier, out1)
    second = _start_worker("race", path, barrier, out2)
    barrier.write_text("go", encoding="utf-8")
    first.wait(timeout=30)
    second.wait(timeout=30)
    assert first.returncode == 0
    assert second.returncode == 0
    assert sorted([_read_out(out1), _read_out(out2)]) == ["acquired", "held"]


def test_owner_crash_releases_os_lock(tmp_path):
    path = tmp_path / "guard.lock"
    barrier = tmp_path / "go"
    out = tmp_path / "out"
    worker = _start_worker("crash", path, barrier, out)
    barrier.write_text("go", encoding="utf-8")
    worker.wait(timeout=30)
    assert worker.returncode == 0
    assert _read_out(out) == "acquired"

    successor = StartupGuard(path, run_id="r2", entry="unit", orphan_probe=lambda: [])
    successor.acquire()
    assert successor.held
    successor.release()


def test_orphan_after_owner_crash_fails_closed(tmp_path):
    path = tmp_path / "guard.lock"
    barrier = tmp_path / "go"
    out = tmp_path / "out"
    marker = tmp_path / "orphan-marker"
    worker = _start_worker("crash-orphan", path, barrier, out, marker)
    barrier.write_text("go", encoding="utf-8")
    worker.wait(timeout=30)
    assert worker.returncode == 0
    assert _read_out(out) == "acquired"
    assert marker.exists()

    successor = StartupGuard(
        path,
        run_id="r2",
        entry="unit",
        orphan_probe=lambda: ["orphan_process:999"] if marker.exists() else [],
    )
    with pytest.raises(OrphanDetectedError):
        successor.acquire()
    assert not successor.held

    # The failed acquisition releases the lock; a clean probe can proceed.
    clean = StartupGuard(path, run_id="r3", entry="unit", orphan_probe=lambda: [])
    clean.acquire()
    assert clean.held
    clean.release()


def test_probe_orphans_classifies_side_effect_survivors_only():
    processes = [
        (1, "python.exe", "python scripts/sandbox_supervisor.py --duration-hours 24"),
        (2, "python.exe", "python scripts/observe_trading_runtime.py --out observation.jsonl"),
        (
            3,
            "ssh.exe",
            "ssh -o StrictHostKeyChecking=yes -o ConnectTimeout=15 -N "
            "-L 1234:127.0.0.1:19002 alex@192.168.3.4",
        ),
        (
            4,
            "ssh.exe",
            "ssh -o ConnectTimeout=10 -N -L 19119:127.0.0.1:9119 alex@192.168.3.4",
        ),
        (5, "bash.exe", "bash -c 'python scripts/sandbox_supervisor.py'"),
        (99, "python.exe", "python scripts/sandbox_supervisor.py"),
    ]
    findings = probe_orphans(
        process_lister=lambda: processes,
        container_lister=lambda: ["qts_p6_sandbox_x-control-1", "qts-postgres"],
        self_pid=99,
    )
    # A live non-owner supervisor is a contender or a finishing owner; the OS
    # lock decides ownership, so it must not be classified as an orphan.
    assert all("supervisor_process" not in item for item in findings)
    assert "observer_process:2" in findings
    assert "dr_tunnel_process:3" in findings
    assert "container:qts_p6_sandbox_x-control-1" in findings
    assert all(":4" not in item for item in findings)
    assert all(":5" not in item for item in findings)
    assert all(":99" not in item for item in findings)
    assert all("qts-postgres" not in item for item in findings)


def test_probe_orphans_fails_closed_when_docker_unavailable():
    def unavailable() -> list[str]:
        raise OrphanCheckUnavailable("docker down")

    with pytest.raises(OrphanCheckUnavailable):
        probe_orphans(process_lister=lambda: [], container_lister=unavailable)


def test_real_supervisor_entry_probe_acquires_without_side_effects():
    _require_clean_production_guard()
    before = _window_dirs()
    result = subprocess.run(
        [sys.executable, "scripts/sandbox_supervisor.py", "--guard-probe"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["guard"] == "acquired"
    assert payload["entry"] == "scripts/sandbox_supervisor.py"
    assert _window_dirs() == before
    assert _running_task_containers() == []


@pytest.mark.parametrize("offset", [0.0, 0.1, 0.25, 0.5, 1.0])
def test_real_supervisor_entry_concurrent_exactly_one_owner(offset):
    """Exactly one real entry owner at several start offsets.

    Regression for P6-R14-GUARD-CONTENDER-MISCLASSIFIED: a contender that is
    still starting must never make the lock owner exit with
    STARTUP_ORPHAN_DETECTED.
    """

    _require_clean_production_guard()
    before = _window_dirs()
    first = subprocess.Popen(
        [sys.executable, "scripts/sandbox_supervisor.py", "--guard-probe"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if offset:
        time.sleep(offset)
    second = subprocess.Popen(
        [sys.executable, "scripts/sandbox_supervisor.py", "--guard-probe"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out1, _ = first.communicate(timeout=90)
    out2, _ = second.communicate(timeout=90)
    codes = sorted([first.returncode, second.returncode])
    assert codes == [0, 3], (offset, first.returncode, out1, second.returncode, out2)
    assert "STARTUP_ORPHAN_DETECTED" not in (out1 + out2)
    winner = out1 if first.returncode == 0 else out2
    loser = out2 if first.returncode == 0 else out1
    assert json.loads(winner.strip().splitlines()[-1])["guard"] == "acquired"
    assert "STARTUP_GUARD_HELD" in loser
    assert _window_dirs() == before
    assert _running_task_containers() == []


def _load_supervisor_module():
    path = ROOT / "scripts" / "sandbox_supervisor.py"
    spec = importlib.util.spec_from_file_location("engineer_phase6_supervisor_guard", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_lookback_covers_runtime_lookback():
    module = _load_supervisor_module()
    assert (
        module._preflight_lookback_ms(
            SimpleNamespace(lookback_ms=300000, runtime_lookback_ms=3600000)
        )
        == 3600000
    )
    assert module._preflight_lookback_ms(SimpleNamespace(lookback_ms=300000)) == 300000
    assert (
        module._preflight_lookback_ms(
            SimpleNamespace(lookback_ms=7200000, runtime_lookback_ms=3600000)
        )
        == 7200000
    )


def _cleanup_supervisor(module, tmp_path):
    supervisor = object.__new__(module.SandboxSupervisor)
    supervisor.project = "qts_p6_sandbox_cleanup_test"
    supervisor.supervisor_journal = tmp_path / "supervisor.jsonl"
    return supervisor


def test_stop_stack_uses_checked_string_and_stops_listed_containers(tmp_path, monkeypatch):
    """Regression for P6-R11-GUARD-CLEANUP-STDOUT.

    ``_checked`` returns stdout as ``str``; the cleanup must use that string
    and reach ``docker stop`` for the listed task containers.
    """

    module = _load_supervisor_module()
    calls: list[list[str]] = []

    def fake_checked(argv, timeout=120, env=None):
        calls.append(list(argv))
        if argv[:2] == ["docker", "ps"]:
            return "cid1\ncid2\n"
        return ""

    monkeypatch.setattr(module, "_checked", fake_checked)
    supervisor = _cleanup_supervisor(module, tmp_path)
    supervisor._stop_stack_best_effort()

    stop_calls = [call for call in calls if call[:2] == ["docker", "stop"]]
    assert len(stop_calls) == 1
    assert "cid1" in stop_calls[0]
    assert "cid2" in stop_calls[0]
    journal = (tmp_path / "supervisor.jsonl").read_text(encoding="utf-8")
    assert '"status": "stopped"' in journal


def test_stop_stack_no_containers_does_not_stop(tmp_path, monkeypatch):
    module = _load_supervisor_module()
    calls: list[list[str]] = []

    def fake_checked(argv, timeout=120, env=None):
        calls.append(list(argv))
        return ""

    monkeypatch.setattr(module, "_checked", fake_checked)
    supervisor = _cleanup_supervisor(module, tmp_path)
    supervisor._stop_stack_best_effort()
    assert all(call[:2] != ["docker", "stop"] for call in calls)


def test_stop_stack_records_failure_without_raising(tmp_path, monkeypatch):
    module = _load_supervisor_module()

    def fake_checked(argv, timeout=120, env=None):
        if argv[:2] == ["docker", "ps"]:
            return "cid1\n"
        raise RuntimeError("COMMAND_FAILED:docker:1:boom")

    monkeypatch.setattr(module, "_checked", fake_checked)
    supervisor = _cleanup_supervisor(module, tmp_path)
    supervisor._stop_stack_best_effort()
    journal = (tmp_path / "supervisor.jsonl").read_text(encoding="utf-8")
    assert '"status": "failed"' in journal
