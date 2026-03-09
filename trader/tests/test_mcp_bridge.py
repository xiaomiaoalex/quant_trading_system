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
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo_dir, check=True)

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


def test_architect_assign_task_auto_updates_task_id_and_branch_from_description(
    mission_file: Path, temp_git_repo: Path
) -> None:
    _write_state(mission_file, _base_state("APPROVED_FOR_PUSH"))

    msg = mcp_bridge.architect_assign_task("Task10.3-E: next stage mission")
    assert msg.startswith("✅")

    state = _read_state(mission_file)
    assert state["status"] == "DEVELOPING"
    assert state["task_id"] == "Task10.3-E"
    assert state["active_branch"] == "feature/task10-3-e"
    assert state["architect_instruction"] == "Task10.3-E: next stage mission"

    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=temp_git_repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert branch == "feature/task10-3-e"


def test_architect_assign_task_new_task_branches_from_main_not_current_head(
    mission_file: Path, temp_git_repo: Path
) -> None:
    subprocess.run(["git", "checkout", "-b", "feature/task10-3-c"], cwd=temp_git_repo, check=True)
    (temp_git_repo / "polluted.txt").write_text("polluted\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "polluted task branch"], cwd=temp_git_repo, check=True)

    _write_state(mission_file, _base_state("APPROVED_FOR_PUSH"))

    msg = mcp_bridge.architect_assign_task("Task10.3-E: clean branch mission")
    assert msg.startswith("✅")
    assert "从 [main] 创建并切换到新分支" in msg

    state = _read_state(mission_file)
    assert state["task_id"] == "Task10.3-E"
    assert state["active_branch"] == "feature/task10-3-e"

    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=temp_git_repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert branch == "feature/task10-3-e"
    assert not (temp_git_repo / "polluted.txt").exists()


def test_architect_assign_task_explicit_task_id_overrides_description(
    mission_file: Path, temp_git_repo: Path
) -> None:
    _write_state(mission_file, _base_state("APPROVED_FOR_PUSH"))

    msg = mcp_bridge.architect_assign_task("next stage mission", task_id="Task10.3-F")
    assert msg.startswith("✅")

    state = _read_state(mission_file)
    assert state["status"] == "DEVELOPING"
    assert state["task_id"] == "Task10.3-F"
    assert state["active_branch"] == "feature/task10-3-f"
    assert state["architect_instruction"] == "next stage mission"

    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=temp_git_repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert branch == "feature/task10-3-f"


def test_architect_assign_task_git_failure_does_not_mutate_state(
    mission_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _base_state("IDLE")
    original["architect_instruction"] = "old instruction"
    _write_state(mission_file, original)

    monkeypatch.setattr(mcp_bridge, "check_git_health", lambda: (True, "ok"))
    monkeypatch.setattr(
        mcp_bridge,
        "_run_git_checkout",
        lambda _branch, **_kwargs: (False, "git failed"),
    )

    msg = mcp_bridge.architect_assign_task("new mission")
    assert msg.startswith("❌")

    state = _read_state(mission_file)
    assert state == original


def test_architect_assign_task_dirty_worktree_includes_git_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_bridge, "check_git_health", lambda: (True, "ok"))
    monkeypatch.setattr(
        mcp_bridge,
        "_check_clean_worktree",
        lambda: (False, "工作区不干净，请先 commit 或 stash 现有改动后再执行任务发布。"),
    )

    msg = mcp_bridge.architect_assign_task("Task10.3-Z: sample")
    assert msg.startswith("❌ 工作区不干净")
    assert "git status --short" in msg
    assert "git stash" in msg


def test_check_git_health_rejects_detached_head(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_bridge, "PROJECT_ROOT", ".")
    monkeypatch.setattr(mcp_bridge.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(mcp_bridge, "_get_current_branch", lambda: "HEAD")

    ok, msg = mcp_bridge.check_git_health()
    assert not ok
    assert "detached HEAD" in msg


def test_check_git_health_rejects_none_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_bridge, "PROJECT_ROOT", ".")
    monkeypatch.setattr(mcp_bridge.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(mcp_bridge, "_get_current_branch", lambda: None)

    ok, msg = mcp_bridge.check_git_health()
    assert not ok
    assert "detached HEAD" in msg


def test_check_git_health_rejects_unknown_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_bridge, "PROJECT_ROOT", ".")
    monkeypatch.setattr(mcp_bridge.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(mcp_bridge, "_get_current_branch", lambda: "unknown")

    ok, msg = mcp_bridge.check_git_health()
    assert not ok
    assert "无法识别当前 Git 分支状态" in msg


def test_architect_sync_branch_ff_only_uses_fetch_then_ff_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def _fake_run_git(args: list[str], timeout: int = 15):
        calls.append(args)
        if args[:2] == ["fetch", "origin"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        if args[:2] == ["merge", "--ff-only"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="Already up to date.\n", stderr="")
        if args == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="abc123\n", stderr="")
        raise AssertionError(f"unexpected git args: {args}")

    monkeypatch.setattr(mcp_bridge, "check_git_health", lambda: (True, "ok"))
    monkeypatch.setattr(mcp_bridge, "_get_current_branch", lambda: "main")
    monkeypatch.setattr(mcp_bridge, "_check_clean_worktree", lambda: (True, ""))
    monkeypatch.setattr(mcp_bridge, "_run_git", _fake_run_git)

    msg = mcp_bridge.architect_sync_branch_ff_only()
    assert msg.startswith("✅ 已同步 main <- origin/main")
    assert "HEAD: abc123" in msg
    assert calls == [
        ["fetch", "origin", "main"],
        ["merge", "--ff-only", "origin/main"],
        ["rev-parse", "HEAD"],
    ]


def test_architect_sync_branch_ff_only_rejects_wrong_current_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_bridge, "check_git_health", lambda: (True, "ok"))
    monkeypatch.setattr(mcp_bridge, "_get_current_branch", lambda: "feature/x")

    msg = mcp_bridge.architect_sync_branch_ff_only()
    assert msg.startswith("❌ 当前分支是 feature/x")


def test_engineer_submit_work_rejects_invalid_pr_package(
    mission_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _base_state("DEVELOPING")
    original["engineer_report"] = "old report"
    _write_state(mission_file, original)
    monkeypatch.setattr(mcp_bridge, "_check_clean_worktree", lambda: (True, ""))

    msg = mcp_bridge.engineer_submit_work("report", "not-json")
    assert msg == "❌ pr_package必须是合法的JSON字符串。"

    state = _read_state(mission_file)
    assert state == original


def test_engineer_submit_work_rejects_non_object_json(
    mission_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _base_state("DEVELOPING")
    _write_state(mission_file, original)
    monkeypatch.setattr(mcp_bridge, "_check_clean_worktree", lambda: (True, ""))

    msg = mcp_bridge.engineer_submit_work("report", '["not", "an", "object"]')
    assert msg == "❌ pr_package必须是有效的JSON对象。"

    state = _read_state(mission_file)
    assert state == original


def test_engineer_submit_work_rejects_missing_required_pr_fields(
    mission_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _base_state("DEVELOPING")
    _write_state(mission_file, original)
    monkeypatch.setattr(mcp_bridge, "_check_clean_worktree", lambda: (True, ""))

    msg = mcp_bridge.engineer_submit_work("report", '{"branch":"feature/task10-3-c"}')
    assert msg == "❌ pr_package缺少必填项：PR 标题。"

    state = _read_state(mission_file)
    assert state == original


def test_engineer_submit_work_valid_package_transitions_to_review_pending(
    mission_file: Path
) -> None:
    _write_state(mission_file, _base_state("DEVELOPING"))

    package = json.dumps(
        {
            "branch": "feature/task10-3-c",
            "status": "ready",
            "pr_title": "fix: sample",
            "pr_description": "desc",
            "changes": [{"file": "x.py", "type": "modify", "description": "sample"}],
            "test_results": {"pytest": "1 passed"},
            "risks": [],
            "rollback": "git checkout main",
        },
        ensure_ascii=False,
    )
    msg = mcp_bridge.engineer_submit_work("done", package)
    assert msg.startswith("✅")

    state = _read_state(mission_file)
    assert state["status"] == "REVIEW_PENDING"
    assert state["engineer_report"] == "done"
    assert isinstance(state["pr_readiness_package"], dict)
    assert state["pr_readiness_package"]["branch"] == "feature/task10-3-c"


def test_engineer_submit_work_allows_refresh_while_review_pending(
    mission_file: Path
) -> None:
    existing = _base_state("REVIEW_PENDING")
    existing["engineer_report"] = "old"
    existing["pr_readiness_package"] = {"branch": "feature/task10-3-c", "status": "ready"}
    _write_state(mission_file, existing)

    package = json.dumps(
        {
            "branch": "feature/task10-3-c",
            "status": "updated",
            "pr_title": "fix: sample",
            "pr_description": "desc",
            "changes": [{"file": "x.py", "type": "modify", "description": "sample"}],
            "test_results": {"pytest": "1 passed"},
            "risks": [],
            "rollback": "git checkout main",
        },
        ensure_ascii=False,
    )
    msg = mcp_bridge.engineer_submit_work("new snapshot", package)
    assert msg.startswith("✅")
    assert "再次调用 engineer_submit_work" in msg

    state = _read_state(mission_file)
    assert state["status"] == "REVIEW_PENDING"
    assert state["engineer_report"] == "new snapshot"
    assert state["pr_readiness_package"]["status"] == "updated"


def test_engineer_submit_work_allows_direct_resubmit_from_revise_required(
    mission_file: Path
) -> None:
    existing = _base_state("REVISE_REQUIRED")
    _write_state(mission_file, existing)

    package = json.dumps(
        {
            "branch": "feature/task10-3-c",
            "status": "ready",
            "pr_title": "fix: sample",
            "pr_description": "desc",
            "changes": [{"file": "x.py", "type": "modify", "description": "sample"}],
            "test_results": {"pytest": "1 passed"},
            "risks": [],
            "rollback": "git checkout main",
        },
        ensure_ascii=False,
    )
    msg = mcp_bridge.engineer_submit_work("fixed after review", package)
    assert msg.startswith("✅")

    state = _read_state(mission_file)
    assert state["status"] == "REVIEW_PENDING"
    assert state["engineer_report"] == "fixed after review"


def test_architect_begin_review_sets_review_lock_and_notice(mission_file: Path) -> None:
    existing = _base_state("REVIEW_PENDING")
    _write_state(mission_file, existing)

    msg = mcp_bridge.architect_begin_review("请基于当前版本开始审查。")
    assert msg.startswith("✅")
    assert "我现在开始正式审核，请停止继续修改，直到我给出结论。" in msg

    state = _read_state(mission_file)
    assert state["status"] == "REVIEW_PENDING"
    assert state["review_lock"]["active"] is True
    assert "我现在开始正式审核，请停止继续修改，直到我给出结论。" in state["review_lock"]["notice"]


def test_architect_begin_review_rejects_duplicate_start(mission_file: Path) -> None:
    existing = _base_state("REVIEW_PENDING")
    existing["review_lock"] = {
        "active": True,
        "notice": "我现在开始正式审核，请停止继续修改，直到我给出结论。",
        "locked_at": "2026-03-08T00:00:00Z",
    }
    _write_state(mission_file, existing)

    msg = mcp_bridge.architect_begin_review("重复开始")
    assert msg == "❌ 审核已在进行中，请勿重复开始。"

    state = _read_state(mission_file)
    assert state["review_lock"]["active"] is True
    assert state["review_lock"]["locked_at"] == "2026-03-08T00:00:00Z"


def test_engineer_submit_work_rejects_updates_after_architect_begins_review(
    mission_file: Path
) -> None:
    existing = _base_state("REVIEW_PENDING")
    existing["review_lock"] = {
        "active": True,
        "notice": "我现在开始正式审核，请停止继续修改，直到我给出结论。",
        "locked_at": "2026-03-08T00:00:00Z",
    }
    _write_state(mission_file, existing)

    package = json.dumps(
        {
            "branch": "feature/task10-3-c",
            "status": "updated",
            "pr_title": "fix: sample",
            "pr_description": "desc",
            "changes": [{"file": "x.py", "type": "modify", "description": "sample"}],
            "test_results": {"pytest": "1 passed"},
            "risks": [],
            "rollback": "git checkout main",
        },
        ensure_ascii=False,
    )
    msg = mcp_bridge.engineer_submit_work("new snapshot", package)
    assert msg.startswith("❌")
    assert "架构师已开始正式审核" in msg
    assert "已停止修改，当前审查版本为最新一次 engineer_submit_work 对应版本。" in msg

    state = _read_state(mission_file)
    assert state["status"] == "REVIEW_PENDING"
    assert state["review_lock"]["active"] is True


def test_engineer_submit_work_keeps_local_changes_uncommitted_while_review_pending(
    mission_file: Path, temp_git_repo: Path
) -> None:
    state = _base_state("DEVELOPING")
    state["active_branch"] = "main"
    _write_state(mission_file, state)

    target_file = temp_git_repo / "feature_change.txt"
    target_file.write_text("changed\n", encoding="utf-8")

    package = json.dumps(
        {
            "branch": "main",
            "target": "main",
            "pr_title": "fix: sample auto commit",
            "pr_description": "desc",
            "changes": [{"file": "feature_change.txt", "type": "modify", "description": "sample"}],
            "test_results": {"pytest": "1 passed"},
            "risks": [],
            "rollback": "git checkout main",
        },
        ensure_ascii=False,
    )
    msg = mcp_bridge.engineer_submit_work("done", package)
    assert msg.startswith("✅")
    assert "不会自动 commit" in msg
    assert "\n当前阶段不会自动 commit" in msg

    state = _read_state(mission_file)
    assert state["status"] == "REVIEW_PENDING"

    status = subprocess.run(
        ["git", "status", "--short"], cwd=temp_git_repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert status != ""
    assert "feature_change.txt" in status


def test_architect_finalize_only_allowed_from_review_pending(mission_file: Path) -> None:
    _write_state(mission_file, _base_state("DEVELOPING"))

    msg = mcp_bridge.architect_finalize("approve", True)
    assert msg.startswith("❌")

    state = _read_state(mission_file)
    assert state["status"] == "DEVELOPING"


def test_architect_finalize_outputs_pr_ready_package(mission_file: Path) -> None:
    state = _base_state("REVIEW_PENDING")
    state["pr_readiness_package"] = {
        "branch": "feature/task10-3-c",
        "target": "main",
        "pr_title": "fix: sample",
        "pr_description": "desc",
        "changes": [{"file": "x.py", "type": "modify", "description": "sample"}],
        "test_results": {"pytest": "1 passed"},
        "risks": [],
        "rollback": "git checkout main",
    }
    _write_state(mission_file, state)

    msg = mcp_bridge.architect_finalize("approve", True)
    assert msg.startswith("✅ 裁定完成：准予手工提交 PR")
    assert "PR 就绪包" in msg
    assert "feature/task10-3-c" in msg
    assert "fix: sample" in msg
    assert "Git 收尾建议" in msg
    assert "git status --short" in msg
    assert "git push -u origin feature/task10-3-c" in msg


def test_architect_finalize_closes_verification_task_without_pr(mission_file: Path) -> None:
    state = _base_state("REVIEW_PENDING")
    state["pr_readiness_package"] = {
        "branch": "feature/task10-3-j",
        "target": "main",
        "task_type": "verification",
        "pr_title": "[验证] Task10.3-J",
        "pr_description": "验证型任务",
        "changes": [],
        "test_results": {"pytest": "10 passed"},
        "risks": [],
        "rollback": "N/A",
    }
    _write_state(mission_file, state)

    msg = mcp_bridge.architect_finalize("verified", True)
    assert msg.startswith("✅ 裁定完成：核销关闭，无需 PR")
    assert "PR 就绪包" in msg
    assert "Git 收尾建议" not in msg

    new_state = _read_state(mission_file)
    assert new_state["status"] == "CLOSED_NO_PR"


def test_architect_assign_task_allows_new_work_from_closed_no_pr(
    mission_file: Path, temp_git_repo: Path
) -> None:
    state = _base_state("CLOSED_NO_PR")
    state["task_id"] = "Task10.3-J"
    state["active_branch"] = "feature/task10-3-j"
    _write_state(mission_file, state)

    msg = mcp_bridge.architect_assign_task("Task10.3-K: next mission")
    assert msg.startswith("✅")

    new_state = _read_state(mission_file)
    assert new_state["status"] == "DEVELOPING"
    assert new_state["task_id"] == "Task10.3-K"
    assert new_state["active_branch"] == "feature/task10-3-k"


def test_architect_mark_pr_opened_records_manual_pr_info(mission_file: Path) -> None:
    state = _base_state("APPROVED_FOR_PUSH")
    state["active_branch"] = "feature/task10-3-l"
    state["pr_readiness_package"] = {
        "branch": "feature/task10-3-l",
        "target": "main",
        "pr_title": "fix: sample",
        "pr_description": "desc",
        "changes": [{"file": "x.py", "type": "modify", "description": "sample"}],
        "test_results": {"pytest": "1 passed"},
        "risks": [],
        "rollback": "git checkout main",
    }
    _write_state(mission_file, state)

    msg = mcp_bridge.architect_mark_pr_opened(
        pr_url="https://github.com/example/repo/pull/123",
        pr_number="123",
    )
    assert msg.startswith("✅ 已记录 PR 创建状态。")
    assert "PR #123" in msg

    new_state = _read_state(mission_file)
    assert new_state["status"] == "PR_OPENED"
    assert new_state["pr_tracking"]["pr_number"] == "123"
    assert new_state["pr_tracking"]["pr_url"].endswith("/pull/123")
    assert new_state["pr_readiness_package"]["pr_number"] == "123"


def test_architect_commit_for_pr_commits_declared_changes(
    mission_file: Path, temp_git_repo: Path
) -> None:
    state = _base_state("APPROVED_FOR_PUSH")
    state["active_branch"] = "main"
    state["pr_readiness_package"] = {
        "branch": "main",
        "target": "main",
        "pr_title": "fix: sample commit",
        "pr_description": "desc",
        "changes": [{"file": "feature_change.txt", "type": "modify", "description": "sample"}],
        "test_results": {"pytest": "1 passed"},
        "risks": [],
        "rollback": "git checkout main",
    }
    _write_state(mission_file, state)

    target_file = temp_git_repo / "feature_change.txt"
    target_file.write_text("changed\n", encoding="utf-8")

    msg = mcp_bridge.architect_commit_for_pr()
    assert msg.startswith("✅ 已完成本地提交。")

    new_state = _read_state(mission_file)
    assert new_state["status"] == "APPROVED_FOR_PUSH"
    assert new_state["pr_tracking"]["local_commit"] != ""

    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=temp_git_repo, capture_output=True, text=True, check=True
    ).stdout
    assert "fix: sample commit" in log


def test_architect_commit_for_pr_rejects_undeclared_changes(
    mission_file: Path, temp_git_repo: Path
) -> None:
    state = _base_state("APPROVED_FOR_PUSH")
    state["active_branch"] = "main"
    state["pr_readiness_package"] = {
        "branch": "main",
        "target": "main",
        "pr_title": "fix: sample commit",
        "pr_description": "desc",
        "changes": [{"file": "declared.txt", "type": "modify", "description": "sample"}],
        "test_results": {"pytest": "1 passed"},
        "risks": [],
        "rollback": "git checkout main",
    }
    _write_state(mission_file, state)

    (temp_git_repo / "declared.txt").write_text("ok\n", encoding="utf-8")
    (temp_git_repo / "extra.txt").write_text("unexpected\n", encoding="utf-8")

    msg = mcp_bridge.architect_commit_for_pr()
    assert msg.startswith("❌")
    assert "extra.txt" in msg


def test_architect_mark_pr_opened_uses_gh_when_available(
    mission_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _base_state("APPROVED_FOR_PUSH")
    state["active_branch"] = "feature/task10-3-l"
    state["pr_readiness_package"] = {
        "branch": "feature/task10-3-l",
        "target": "main",
        "pr_title": "fix: sample",
        "pr_description": "desc",
        "changes": [{"file": "x.py", "type": "modify", "description": "sample"}],
        "test_results": {"pytest": "1 passed"},
        "risks": [],
        "rollback": "git checkout main",
    }
    _write_state(mission_file, state)

    monkeypatch.setattr(
        mcp_bridge,
        "_read_pr_info_from_gh",
        lambda _branch: (
            {
                "pr_number": "20",
                "pr_url": "https://github.com/example/repo/pull/20",
                "pr_state": "OPEN",
                "merge_commit": "",
                "merged_at": "",
                "base_ref": "main",
                "head_ref": "feature/task10-3-l",
            },
            None,
        ),
    )

    msg = mcp_bridge.architect_mark_pr_opened()
    assert msg.startswith("✅ 已记录 PR 创建状态。")
    assert "PR #20" in msg

    new_state = _read_state(mission_file)
    assert new_state["status"] == "PR_OPENED"
    assert new_state["pr_tracking"]["pr_number"] == "20"


def test_architect_create_pr_creates_remote_pr_and_marks_opened(
    mission_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _base_state("APPROVED_FOR_PUSH")
    state["active_branch"] = "feature/task10-3-l"
    state["pr_readiness_package"] = {
        "branch": "feature/task10-3-l",
        "target": "main",
        "pr_title": "fix: sample",
        "pr_description": "desc",
        "changes": [{"file": "x.py", "type": "modify", "description": "sample"}],
        "test_results": {"pytest": "1 passed"},
        "risks": [],
        "rollback": "git checkout main",
    }
    _write_state(mission_file, state)

    monkeypatch.setattr(
        mcp_bridge,
        "_run_gh",
        lambda _args, timeout=60: (
            subprocess.CompletedProcess(args=_args, returncode=0, stdout="https://github.com/example/repo/pull/55\n", stderr=""),
            None,
        ),
    )

    msg = mcp_bridge.architect_create_pr()
    assert msg.startswith("✅ 已创建 PR。")
    assert "/pull/55" in msg

    new_state = _read_state(mission_file)
    assert new_state["status"] == "PR_OPENED"
    assert new_state["pr_tracking"]["pr_number"] == "55"


def test_architect_mark_merged_records_merge_and_milestone(
    mission_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _base_state("PR_OPENED")
    state["task_id"] = "Task10.3-L"
    state["active_branch"] = "feature/task10-3-l"
    state["pr_tracking"] = {
        "pr_number": "20",
        "pr_url": "https://github.com/example/repo/pull/20",
        "opened_at": "2026-03-08T00:00:00Z",
        "merged_at": "",
        "merge_commit": "",
    }
    state["pr_readiness_package"] = {
        "branch": "feature/task10-3-l",
        "target": "main",
        "pr_title": "fix: sample",
        "pr_description": "desc",
        "changes": [{"file": "x.py", "type": "modify", "description": "sample"}],
        "test_results": {"pytest": "1 passed"},
        "risks": [],
        "rollback": "git checkout main",
    }
    _write_state(mission_file, state)

    monkeypatch.setattr(
        mcp_bridge,
        "_read_pr_info_from_gh",
        lambda _branch: (
            {
                "pr_number": "20",
                "pr_url": "https://github.com/example/repo/pull/20",
                "pr_state": "MERGED",
                "merge_commit": "abc123",
                "merged_at": "2026-03-08T12:00:00Z",
                "base_ref": "main",
                "head_ref": "feature/task10-3-l",
            },
            None,
        ),
    )

    msg = mcp_bridge.architect_mark_merged("","Task10.3-L: Timezone-Aware UTC Cleanup (Merged)")
    assert msg.startswith("✅ 已记录 PR 合并状态。")
    assert "merge_commit: abc123" in msg

    new_state = _read_state(mission_file)
    assert new_state["status"] == "MERGED"
    assert new_state["pr_tracking"]["merge_commit"] == "abc123"
    assert "Task10.3-L: Timezone-Aware UTC Cleanup (Merged)" in new_state["completed_milestones"]


def test_architect_merge_pr_merges_remote_pr_and_marks_merged(
    mission_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _base_state("PR_OPENED")
    state["task_id"] = "Task10.3-L"
    state["active_branch"] = "feature/task10-3-l"
    state["pr_tracking"] = {
        "pr_number": "20",
        "pr_url": "https://github.com/example/repo/pull/20",
        "opened_at": "2026-03-08T00:00:00Z",
        "merged_at": "",
        "merge_commit": "",
        "local_commit": "deadbeef",
    }
    state["pr_readiness_package"] = {
        "branch": "feature/task10-3-l",
        "target": "main",
        "pr_title": "fix: sample",
        "pr_description": "desc",
        "changes": [{"file": "x.py", "type": "modify", "description": "sample"}],
        "test_results": {"pytest": "1 passed"},
        "risks": [],
        "rollback": "git checkout main",
    }
    _write_state(mission_file, state)

    monkeypatch.setattr(
        mcp_bridge,
        "_run_gh",
        lambda _args, timeout=90: (
            subprocess.CompletedProcess(args=_args, returncode=0, stdout="merged\n", stderr=""),
            None,
        ),
    )
    monkeypatch.setattr(
        mcp_bridge,
        "_read_pr_info_from_gh",
        lambda _branch: (
            {
                "pr_number": "20",
                "pr_url": "https://github.com/example/repo/pull/20",
                "pr_state": "MERGED",
                "merge_commit": "abc123",
                "merged_at": "2026-03-08T12:00:00Z",
                "base_ref": "main",
                "head_ref": "feature/task10-3-l",
            },
            None,
        ),
    )

    msg = mcp_bridge.architect_merge_pr(summary="Task10.3-L: merged by architect")
    assert msg.startswith("✅ 已执行 PR merge。")
    assert "merge_commit: abc123" in msg

    new_state = _read_state(mission_file)
    assert new_state["status"] == "MERGED"
    assert "Task10.3-L: merged by architect" in new_state["completed_milestones"]


@pytest.mark.parametrize("merge_method", ["", None, " invalid "])
def test_architect_merge_pr_rejects_invalid_merge_method(
    mission_file: Path,
    merge_method: str | None,
) -> None:
    state = _base_state("PR_OPENED")
    _write_state(mission_file, state)

    msg = mcp_bridge.architect_merge_pr(merge_method=merge_method)
    assert msg.startswith("❌ merge_method 必须是")


def test_architect_assign_task_allows_new_work_from_merged(
    mission_file: Path, temp_git_repo: Path
) -> None:
    state = _base_state("MERGED")
    state["task_id"] = "Task10.3-L"
    state["active_branch"] = "feature/task10-3-l"
    _write_state(mission_file, state)

    msg = mcp_bridge.architect_assign_task("Task10.3-M: next mission")
    assert msg.startswith("✅")

    new_state = _read_state(mission_file)
    assert new_state["status"] == "DEVELOPING"
    assert new_state["task_id"] == "Task10.3-M"
    assert new_state["active_branch"] == "feature/task10-3-m"


def test_gh_command_env_uses_default_proxy_when_shell_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.setattr(mcp_bridge, "DEFAULT_GITHUB_PROXY", "http://127.0.0.1:4780")

    env = mcp_bridge._gh_command_env()
    assert env["HTTP_PROXY"] == "http://127.0.0.1:4780"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:4780"


def test_git_command_env_uses_windows_openssh_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.setattr(mcp_bridge.os.path, "exists", lambda path: path == mcp_bridge.DEFAULT_WINDOWS_OPENSSH)

    env = mcp_bridge._git_command_env()
    assert env["GIT_SSH_COMMAND"] == mcp_bridge.DEFAULT_WINDOWS_OPENSSH


def test_git_command_env_prefers_existing_ssh_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_SSH_COMMAND", "custom-ssh.exe")

    env = mcp_bridge._git_command_env()
    assert env["GIT_SSH_COMMAND"] == "custom-ssh.exe"


def test_gh_command_env_injects_explicit_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "token-123")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    env = mcp_bridge._gh_command_env()
    assert env["GH_TOKEN"] == "token-123"
    assert env["GITHUB_TOKEN"] == "token-123"


@pytest.mark.parametrize(
    ("remote_url", "expected"),
    [
        ("https://github.com/example/repo", "git@github.com:example/repo.git"),
        ("https://github.com/example/repo.git", "git@github.com:example/repo.git"),
        ("https://github.com:443/example/repo", "git@github.com:example/repo.git"),
        ("https://user@github.com/example/repo", "git@github.com:example/repo.git"),
    ],
)
def test_suggest_ssh_remote_supports_common_github_https_variants(
    remote_url: str, expected: str
) -> None:
    assert mcp_bridge._suggest_ssh_remote(remote_url) == expected


def test_architect_github_preflight_reports_login_guidance_with_auto_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_bridge, "_gh_executable", lambda: "gh")
    monkeypatch.setattr(mcp_bridge, "_read_gh_auth_status", lambda: (False, "not logged in"))
    monkeypatch.setattr(mcp_bridge, "_git_origin_url", lambda: ("https://github.com/example/repo.git", None))
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(mcp_bridge, "DEFAULT_GITHUB_PROXY", "http://127.0.0.1:4780")

    msg = mcp_bridge.architect_github_preflight()
    assert "GitHub 预检结果" in msg
    assert "- gh: gh" in msg
    assert "- HTTP_PROXY: http://127.0.0.1:4780" in msg
    assert "- auth_mode: keyring" in msg
    assert "- git_transport: https" in msg
    assert "设置用户级 GH_TOKEN" in msg
    assert "git remote set-url origin git@github.com:example/repo.git" in msg


def test_architect_github_preflight_reports_ready_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_bridge, "_gh_executable", lambda: r"C:\Program Files\GitHub CLI\gh.exe")
    monkeypatch.setattr(mcp_bridge, "_read_gh_auth_status", lambda: (True, "Logged in to github.com"))
    monkeypatch.setattr(mcp_bridge, "_git_origin_url", lambda: ("git@github.com:example/repo.git", None))
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:4780")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:4780")
    monkeypatch.setenv("GH_TOKEN", "token-123")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    msg = mcp_bridge.architect_github_preflight()
    assert "- auth: ok" in msg
    assert "- auth_mode: token" in msg
    assert "- token_source: GH_TOKEN" in msg
    assert "gh pr status / gh pr create --fill" in msg


def test_architect_github_preflight_recommends_moving_off_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_bridge, "_gh_executable", lambda: "gh")
    monkeypatch.setattr(mcp_bridge, "_read_gh_auth_status", lambda: (True, "Logged in to github.com"))
    monkeypatch.setattr(mcp_bridge, "_git_origin_url", lambda: ("git@github.com:example/repo.git", None))
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:4780")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:4780")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    msg = mcp_bridge.architect_github_preflight()
    assert "- auth_mode: keyring" in msg
    assert "当前 gh 仍依赖 keyring" in msg


def test_read_gh_auth_status_passes_proxy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_bridge, "_gh_executable", lambda: "gh")
    monkeypatch.setattr(mcp_bridge, "DEFAULT_GITHUB_PROXY", "http://127.0.0.1:4780")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)

    captured: dict[str, str] = {}

    def _fake_run(args, **kwargs):
        captured["http_proxy"] = kwargs["env"]["HTTP_PROXY"]
        captured["https_proxy"] = kwargs["env"]["HTTPS_PROXY"]
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="Logged in")

    monkeypatch.setattr(mcp_bridge.subprocess, "run", _fake_run)

    ok, _ = mcp_bridge._read_gh_auth_status()
    assert ok is True
    assert captured["http_proxy"] == "http://127.0.0.1:4780"
    assert captured["https_proxy"] == "http://127.0.0.1:4780"


def test_invalid_transition_is_rejected_without_write(
    mission_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _base_state("IDLE")
    _write_state(mission_file, original)
    monkeypatch.setattr(mcp_bridge, "_check_clean_worktree", lambda: (True, ""))

    package = json.dumps(
        {
            "branch": "feature/task10-3-c",
            "pr_title": "fix: sample",
            "pr_description": "desc",
            "changes": [{"file": "x.py", "type": "modify", "description": "sample"}],
            "test_results": {"pytest": "1 passed"},
            "risks": [],
            "rollback": "git checkout main",
        },
        ensure_ascii=False,
    )
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
