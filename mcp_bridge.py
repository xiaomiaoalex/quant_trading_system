import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_FILE = "mcp_mission_control.json"

VALID_STATUSES = {
    "IDLE",
    "DEVELOPING",
    "REVIEW_PENDING",
    "REVISE_REQUIRED",
    "APPROVED_FOR_PUSH",
}

ALLOWED_TRANSITIONS = {
    ("IDLE", "DEVELOPING"),
    ("REVISE_REQUIRED", "DEVELOPING"),
    ("APPROVED_FOR_PUSH", "DEVELOPING"),
    ("DEVELOPING", "REVIEW_PENDING"),
    ("REVIEW_PENDING", "APPROVED_FOR_PUSH"),
    ("REVIEW_PENDING", "REVISE_REQUIRED"),
}

LOCK_TIMEOUT_SEC = float(os.getenv("MCP_LOCK_TIMEOUT_SEC", "10.0"))
LOCK_RETRY_INTERVAL_SEC = float(os.getenv("MCP_LOCK_RETRY_INTERVAL_SEC", "0.1"))

mcp = FastMCP("Dual_AI_Communicator")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _db_path() -> str:
    if os.path.isabs(DB_FILE):
        return DB_FILE
    return os.path.join(PROJECT_ROOT, DB_FILE)


def _lock_path(path: str) -> str:
    return f"{path}.lock"


def _default_state() -> dict[str, Any]:
    return {
        "current_version": "v3.0.9",
        "sprint": "Sprint 3",
        "task_id": "UNASSIGNED",
        "active_branch": "main",
        "status": "IDLE",
        "completed_milestones": [],
        "architect_instruction": "等待下发",
        "engineer_report": "",
        "pr_readiness_package": {},
        "last_update": _utc_now_iso(),
    }


def _normalize_pr_package(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in ("", None):
        return {}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"raw_text": value}
        if isinstance(parsed, dict):
            return parsed
        return {"raw_text": value}
    return {"raw_text": str(value)}


def _normalize_state(raw: Any) -> dict[str, Any]:
    state = raw if isinstance(raw, dict) else {}
    normalized: dict[str, Any] = _default_state()
    normalized.update(state)

    if not isinstance(normalized.get("completed_milestones"), list):
        normalized["completed_milestones"] = []
    if not isinstance(normalized.get("status"), str):
        normalized["status"] = "IDLE"
    if not isinstance(normalized.get("task_id"), str) or not normalized.get("task_id"):
        normalized["task_id"] = "UNASSIGNED"
    if not isinstance(normalized.get("active_branch"), str) or not normalized.get("active_branch"):
        normalized["active_branch"] = "main"
    if not isinstance(normalized.get("architect_instruction"), str):
        normalized["architect_instruction"] = "等待下发"
    if not isinstance(normalized.get("engineer_report"), str):
        normalized["engineer_report"] = ""
    if not isinstance(normalized.get("last_update"), str):
        normalized["last_update"] = _utc_now_iso()
    normalized["pr_readiness_package"] = _normalize_pr_package(normalized.get("pr_readiness_package"))
    return normalized


@contextmanager
def _with_file_lock(lockfile_path: str):
    os.makedirs(os.path.dirname(lockfile_path) or ".", exist_ok=True)

    def _try_acquire_platform_lock(lock_file) -> str:
        if os.name == "nt":
            try:
                import msvcrt
            except ImportError:
                return "unsupported"
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                return "acquired"
            except OSError:
                return "contended"

        try:
            import fcntl
        except ImportError:
            return "unsupported"
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return "acquired"
        except BlockingIOError:
            return "contended"

    def _try_acquire_fallback_lock(lp_path: str) -> bool:
        fallback_lock = f"{lp_path}.fallback"
        if os.path.exists(fallback_lock):
            try:
                with open(fallback_lock, "r") as f:
                    lock_time = float(f.read().strip())
                if time.monotonic() - lock_time < LOCK_TIMEOUT_SEC:
                    return False
            except (ValueError, IOError):
                pass
        try:
            with open(fallback_lock, "w") as f:
                f.write(str(time.monotonic()))
            return True
        except IOError:
            return False

    def _release_platform_lock(lock_file):
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _release_fallback_lock(lp_path: str):
        fallback_lock = f"{lp_path}.fallback"
        try:
            os.remove(fallback_lock)
        except OSError:
            pass

    use_fallback = False
    
    with open(lockfile_path, "a+b") as lock_file:
        if os.path.getsize(lockfile_path) == 0:
            lock_file.write(b"0")
            lock_file.flush()
        lock_file.seek(0)
        deadline = time.monotonic() + LOCK_TIMEOUT_SEC
        locked = False
        fallback_allowed = False

        while time.monotonic() < deadline:
            lock_result = _try_acquire_platform_lock(lock_file)
            if lock_result == "acquired":
                locked = True
                break
            if lock_result == "unsupported":
                fallback_allowed = True
                break
            time.sleep(LOCK_RETRY_INTERVAL_SEC)

        if not locked:
            if fallback_allowed and _try_acquire_fallback_lock(lockfile_path):
                use_fallback = True
                locked = True
            if not locked:
                raise TimeoutError(
                    f"❌ [MISSION_CONTROL] 无法获得文件锁 ({lockfile_path})。\n"
                    "⚠️ 请勿手动编辑 mcp_mission_control.json。\n"
                    "👉 请稍后重试或检查是否有并发 MCP 进程占用。"
                )

        try:
            yield
        finally:
            if use_fallback:
                _release_fallback_lock(lockfile_path)
            else:
                _release_platform_lock(lock_file)


def _atomic_write_json(path: str, data: dict[str, Any]) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".mission_state.", suffix=".tmp", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _ensure_db_exists_unlocked(path: str) -> None:
    if not os.path.exists(path):
        _atomic_write_json(path, _default_state())


def _load_state() -> dict[str, Any]:
    path = _db_path()
    with _with_file_lock(_lock_path(path)):
        _ensure_db_exists_unlocked(path)
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return _normalize_state(raw)


@contextmanager
def _locked_state():
    path = _db_path()
    with _with_file_lock(_lock_path(path)):
        _ensure_db_exists_unlocked(path)
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        state = _normalize_state(raw)

        def _commit(new_state: dict[str, Any]) -> None:
            _atomic_write_json(path, _normalize_state(new_state))

        yield state, _commit


def _validate_transition(current: str, target: str) -> tuple[bool, str]:
    if current not in VALID_STATUSES:
        return False, f"非法状态值: {current}"
    if target not in VALID_STATUSES:
        return False, f"非法目标状态: {target}"
    if (current, target) not in ALLOWED_TRANSITIONS:
        return False, f"非法状态流转: {current} -> {target}"
    return True, ""


def _safe_branch_name(task_id: str) -> str:
    normalized = task_id.lower().replace(".", "-").replace("_", "-")
    normalized = re.sub(r"[^a-z0-9-]", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    if not normalized:
        normalized = "unknown-task"
    return f"feature/{normalized}"


def _run_git_checkout(branch_name: str) -> tuple[bool, str]:
    try:
        create = subprocess.run(
            ["git", "checkout", "-b", branch_name],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=10,
        )
        if create.returncode == 0:
            return True, f"🌿 已自动创建并切换到新分支 [{branch_name}]。"

        switch = subprocess.run(
            ["git", "checkout", branch_name],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=10,
        )
        if switch.returncode == 0:
            return True, f"🌿 已自动切换到现有分支 [{branch_name}]。"

        err_create = (create.stderr or create.stdout or "").strip()
        err_switch = (switch.stderr or switch.stdout or "").strip()
        return False, f"Git 辅助异常。create: {err_create}; switch: {err_switch}"
    except subprocess.TimeoutExpired:
        return False, "Git checkout 超时，请检查仓库状态后重试。"
    except Exception as e:
        return False, f"Git 执行异常: {str(e)}"


def _get_current_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=5,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _check_clean_worktree() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=10,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            return False, f"无法检查工作区状态: {err}"
        if result.stdout.strip():
            return False, "工作区不干净，请先 commit 或 stash 现有改动后再执行任务发布。"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "检查工作区状态超时，请稍后重试。"
    except Exception as e:
        return False, f"检查工作区失败: {str(e)}"


def check_git_health() -> tuple[bool, str]:
    git_dir = os.path.join(PROJECT_ROOT, ".git")
    if not os.path.exists(git_dir):
        return False, f"🚨 警告：在 {PROJECT_ROOT} 未检测到 .git 目录！"
    return True, "Git 环境健康"


@mcp.tool()
def read_mission_state() -> str:
    """读取任务状态并校验当前分支是否与任务绑定分支一致。"""
    try:
        state = _load_state()
        current_branch = _get_current_branch()
        expected_branch = state.get("active_branch", "main")
        if state.get("status") != "IDLE" and current_branch != expected_branch:
            state["_runtime_alert"] = (
                "🚨 [CRITICAL_WARNING] 当前分支与任务绑定分支不一致。\n"
                f"- 当前分支: {current_branch}\n"
                f"- 绑定分支: {expected_branch}"
            )
        return json.dumps(state, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"❌ 读取任务状态失败: {str(e)}"


@mcp.tool()
def architect_assign_task(task_desc: str) -> str:
    """发布开发任务并切换到任务分支。"""
    is_healthy, health_msg = check_git_health()
    if not is_healthy:
        return f"❌ {health_msg}"

    clean, clean_msg = _check_clean_worktree()
    if not clean:
        return f"❌ {clean_msg}"

    try:
        with _locked_state() as (state, commit):
            can_transit, transition_msg = _validate_transition(state["status"], "DEVELOPING")
            if not can_transit:
                return f"❌ {transition_msg}"

            branch_name = _safe_branch_name(state.get("task_id", "UNASSIGNED"))
            git_ok, git_msg = _run_git_checkout(branch_name)
            if not git_ok:
                return f"❌ {git_msg}"

            state["status"] = "DEVELOPING"
            state["active_branch"] = branch_name
            state["architect_instruction"] = task_desc
            state["last_update"] = _utc_now_iso()
            commit(state)
        return f"✅ 任务已发布，状态：DEVELOPING。\n{git_msg}"
    except Exception as e:
        return f"❌ 写入任务状态失败: {str(e)}"


@mcp.tool()
def engineer_submit_work(report: str, pr_package: str) -> str:
    """工程师提交开发成果与 PR 就绪包。"""
    is_healthy, msg = check_git_health()
    if not is_healthy:
        return f"❌ {msg}"

    try:
        parsed_package = json.loads(pr_package)
    except json.JSONDecodeError:
        return "❌ pr_package 必须是 JSON 对象字符串。"
    if not isinstance(parsed_package, dict):
        return "❌ pr_package 必须是 JSON 对象字符串。"

    try:
        with _locked_state() as (state, commit):
            can_transit, transition_msg = _validate_transition(state["status"], "REVIEW_PENDING")
            if not can_transit:
                return f"❌ {transition_msg}"

            state["status"] = "REVIEW_PENDING"
            state["engineer_report"] = report
            state["pr_readiness_package"] = parsed_package
            state["last_update"] = _utc_now_iso()
            commit(state)
        return "✅ 成果已提交，状态变更为 REVIEW_PENDING，请通知架构师审核。"
    except Exception as e:
        return f"❌ 写入任务状态失败: {str(e)}"


@mcp.tool()
def architect_finalize(feedback: str, approved: bool) -> str:
    """架构师给出最终审核结论。"""
    target_status = "APPROVED_FOR_PUSH" if approved else "REVISE_REQUIRED"
    try:
        with _locked_state() as (state, commit):
            can_transit, transition_msg = _validate_transition(state["status"], target_status)
            if not can_transit:
                return f"❌ {transition_msg}"

            state["status"] = target_status
            state["architect_instruction"] = feedback
            state["last_update"] = _utc_now_iso()
            commit(state)
        return f"✅ 裁定完成：{'准予手工提交 PR' if approved else '打回修改，状态为 REVISE_REQUIRED'}"
    except Exception as e:
        return f"❌ 写入任务状态失败: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
