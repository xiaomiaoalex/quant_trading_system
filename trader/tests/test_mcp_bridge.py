"""Unit tests for mcp_bridge mission state management."""

from __future__ import annotations

import builtins
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

import mcp_bridge


def _write_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=4), encoding="utf-8")


def _read_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _base_state(status: str = "IDLE") -> dict:
    return {
        "current_version": "v3.0.8",
        "sprint": "Sprint 3",
        "task_id": "Task10.3-C",
        "status": status,
        "completed_milestones": [],
        "architect_instruction": "等待下发",
        "engineer_report": "",
        "pr_readiness_package": {},
        "last_update": "2026-03-03T00:00:00Z",
    }


@pytest.fixture
def mission_file(monkeypatch: pytest.MonkeyPatch) -> Path:
    base_tmp = Path(".mcp_bridge_test_tmp")
    base_tmp.mkdir(exist_ok=True)
    case_tmp = base_tmp / f"case_{uuid.uuid4().hex}"
    case_tmp.mkdir(parents=True, exist_ok=True)
    db_path = (case_tmp / "mission.json").resolve()
    monkeypatch.setattr(mcp_bridge, "DB_FILE", str(db_path))
    try:
        yield db_path
    finally:
        shutil.rmtree(case_tmp, ignore_errors=True)


@pytest.fixture
def temp_git_repo(monkeypatch: pytest.MonkeyPatch) -> Path:
    base_tmp = Path(".mcp_bridge_test_tmp")
    base_tmp.mkdir(exist_ok=True)
    repo_dir = base_tmp / f"git_repo_{uuid.uuid4().hex}"
    repo_dir.mkdir(parents=True, exist_ok=True)

    init = subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, text=True)
    if init.returncode != 0:
        shutil.rmtree(repo_dir, ignore_errors=True)
        pytest.skip("git unavailable in test environment")

    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
    (repo_dir / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

    monkeypatch.setattr(mcp_bridge, "PROJECT_ROOT", str(repo_dir))
    try:
        yield repo_dir
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)


def test_read_mission_state_normalizes_legacy_string_package(mission_file: Path) -> None:
    legacy = _base_state("DEVELOPING")
    legacy["pr_readiness_package"] = '{"branch":"feature/x","status":"ready"}'
    _write_state(mission_file, legacy)

    state = json.loads(mcp_bridge.read_mission_state())
    assert isinstance(state["pr_readiness_package"], dict)
    assert state["pr_readiness_package"]["branch"] == "feature/x"

    # Read path should not rewrite file.
    persisted = _read_state(mission_file)
    assert isinstance(persisted["pr_readiness_package"], str)


def test_architect_assign_task_success_updates_state(
    mission_file: Path, temp_git_repo: Path
) -> None:
    _write_state(mission_file, _base_state("IDLE"))

    msg = mcp_bridge.architect_assign_task("new mission")
    assert msg.startswith("✅")

    state = _read_state(mission_file)
    assert state["status"] == "DEVELOPING"
    assert state["architect_instruction"] == "new mission"

    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=temp_git_repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert branch == "feature/task10-3-c"


def test_architect_assign_task_git_failure_does_not_mutate_state(
    mission_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _base_state("IDLE")
    original["architect_instruction"] = "old instruction"
    _write_state(mission_file, original)

    monkeypatch.setattr(mcp_bridge, "check_git_health", lambda: (True, "ok"))
    monkeypatch.setattr(mcp_bridge, "_run_git_checkout", lambda _branch: (False, "git failed"))

    msg = mcp_bridge.architect_assign_task("new mission")
    assert msg.startswith("❌")

    state = _read_state(mission_file)
    assert state == original


def test_engineer_submit_work_rejects_invalid_pr_package(mission_file: Path) -> None:
    original = _base_state("DEVELOPING")
    original["engineer_report"] = "old report"
    _write_state(mission_file, original)

    msg = mcp_bridge.engineer_submit_work("report", "not-json")
    assert msg == "❌ pr_package必须是合法的JSON字符串。"

    state = _read_state(mission_file)
    assert state == original


def test_engineer_submit_work_rejects_non_object_json(mission_file: Path) -> None:
    original = _base_state("DEVELOPING")
    _write_state(mission_file, original)

    msg = mcp_bridge.engineer_submit_work("report", '["not", "an", "object"]')
    assert msg == "❌ pr_package必须是有效的JSON对象。"

    state = _read_state(mission_file)
    assert state == original


def test_engineer_submit_work_valid_package_transitions_to_review_pending(mission_file: Path) -> None:
    _write_state(mission_file, _base_state("DEVELOPING"))

    package = json.dumps({"branch": "feature/task10-3-c", "status": "ready"}, ensure_ascii=False)
    msg = mcp_bridge.engineer_submit_work("done", package)
    assert msg.startswith("✅")

    state = _read_state(mission_file)
    assert state["status"] == "REVIEW_PENDING"
    assert state["engineer_report"] == "done"
    assert isinstance(state["pr_readiness_package"], dict)
    assert state["pr_readiness_package"]["branch"] == "feature/task10-3-c"


def test_architect_finalize_only_allowed_from_review_pending(mission_file: Path) -> None:
    _write_state(mission_file, _base_state("DEVELOPING"))

    msg = mcp_bridge.architect_finalize("approve", True)
    assert msg.startswith("❌")

    state = _read_state(mission_file)
    assert state["status"] == "DEVELOPING"


def test_invalid_transition_is_rejected_without_write(mission_file: Path) -> None:
    original = _base_state("IDLE")
    _write_state(mission_file, original)

    package = json.dumps({"branch": "feature/task10-3-c"}, ensure_ascii=False)
    msg = mcp_bridge.engineer_submit_work("should fail", package)
    assert msg.startswith("❌")

    state = _read_state(mission_file)
    assert state == original


def test_atomic_write_path_used(mission_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    original_replace = mcp_bridge.os.replace

    def _tracking_replace(src: str, dst: str) -> None:
        calls.append((src, dst))
        original_replace(src, dst)

    monkeypatch.setattr(mcp_bridge.os, "replace", _tracking_replace)

    payload = {"status": "IDLE", "marker": "atomic"}
    mcp_bridge._atomic_write_json(str(mission_file), payload)

    assert calls, "os.replace should be used for atomic write"
    assert calls[0][1] == str(mission_file)
    assert _read_state(mission_file)["marker"] == "atomic"


def test_file_lock_timeout_does_not_fallback_on_contention(
    mission_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = mcp_bridge._lock_path(str(mission_file))
    fallback_path = Path(f"{lock_path}.fallback")
    monkeypatch.setattr(mcp_bridge, "LOCK_TIMEOUT_SEC", 0.01)
    monkeypatch.setattr(mcp_bridge.time, "sleep", lambda _seconds: None)

    ticks = iter([0.0, 0.0, 0.02, 0.02])
    monkeypatch.setattr(mcp_bridge.time, "monotonic", lambda: next(ticks))

    if os.name == "nt":
        import msvcrt

        def _busy_locking(_fd: int, mode: int, _nbytes: int) -> None:
            if mode == msvcrt.LK_NBLCK:
                raise OSError("busy")

        monkeypatch.setattr(msvcrt, "locking", _busy_locking)
    else:
        import fcntl

        def _busy_flock(_fd: int, _op: int) -> None:
            raise BlockingIOError("busy")

        monkeypatch.setattr(fcntl, "flock", _busy_flock)

    with pytest.raises(TimeoutError):
        with mcp_bridge._with_file_lock(lock_path):
            pass

    assert not fallback_path.exists()


def test_file_lock_uses_fallback_only_when_platform_lock_unsupported(
    mission_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = mcp_bridge._lock_path(str(mission_file))
    fallback_path = Path(f"{lock_path}.fallback")
    lock_module = "msvcrt" if os.name == "nt" else "fcntl"
    original_import = builtins.__import__

    def _fake_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
        if name == lock_module:
            raise ImportError("lock module unavailable")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with mcp_bridge._with_file_lock(lock_path):
        assert fallback_path.exists()
        payload = json.loads(fallback_path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
        assert isinstance(payload["monotonic"], float)

    assert not fallback_path.exists()
