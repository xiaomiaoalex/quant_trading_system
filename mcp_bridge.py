import os
import sys
import json
import time
import tempfile
import re
import subprocess
from datetime import datetime, timezone
from typing import Any, Optional
from contextlib import contextmanager

from mcp.server.fastmcp import FastMCP

# 基础常量定义
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


def _parse_pr_package(pr_package: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        parsed_package = json.loads(pr_package)
    except json.JSONDecodeError:
        return None, "❌ pr_package必须是合法的JSON字符串。"
    if not isinstance(parsed_package, dict):
        return None, "❌ pr_package必须是有效的JSON对象。"
    return parsed_package, None


def _validate_pr_package_content(pr_package: dict[str, Any]) -> str | None:
    required_string_fields = {
        "pr_title": "PR 标题",
        "pr_description": "PR 描述草稿",
        "rollback": "回滚方案",
    }
    for field, label in required_string_fields.items():
        value = pr_package.get(field)
        if not isinstance(value, str) or not value.strip():
            return f"❌ pr_package缺少必填项：{label}。"

    changes = pr_package.get("changes")
    if not isinstance(changes, list) or not changes:
        return "❌ pr_package缺少必填项：变更点（changes）。"
    for change in changes:
        if not isinstance(change, dict):
            return "❌ pr_package.changes 中的每一项都必须是对象。"
        file_path = change.get("file")
        change_type = change.get("type")
        description = change.get("description")
        if not isinstance(file_path, str) or not file_path.strip():
            return "❌ pr_package.changes 缺少必填项：file。"
        if not isinstance(change_type, str) or not change_type.strip():
            return "❌ pr_package.changes 缺少必填项：type。"
        if not isinstance(description, str) or not description.strip():
            return "❌ pr_package.changes 缺少必填项：description。"
        if file_path == "mcp_mission_control.json" or file_path.endswith(".lock"):
            return f"❌ pr_package.changes 包含受保护运行态文件：{file_path}。"

    test_results = pr_package.get("test_results")
    if not isinstance(test_results, dict) or not test_results:
        return "❌ pr_package缺少必填项：测试结果（test_results）。"

    risks = pr_package.get("risks")
    if not isinstance(risks, list):
        return "❌ pr_package缺少必填项：风险说明（risks）。"

    return None


def _collect_worktree_changes() -> tuple[set[str] | None, str | None]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=10,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            return None, f"无法收集工作区改动: {err}"
        dirty_files: set[str] = set()
        for raw_line in result.stdout.splitlines():
            if not raw_line:
                continue
            path = raw_line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1].strip()
            if path:
                dirty_files.add(path)
        return dirty_files, None
    except subprocess.TimeoutExpired:
        return None, "收集工作区改动超时，请稍后重试。"
    except Exception as e:
        return None, f"收集工作区改动失败: {str(e)}"


def _declared_change_files(pr_package: dict[str, Any]) -> set[str]:
    files: set[str] = set()
    for change in pr_package.get("changes", []):
        if isinstance(change, dict):
            file_path = change.get("file")
            if isinstance(file_path, str) and file_path.strip():
                files.add(file_path.strip())
    return files


def _sanitize_commit_message(message: str) -> str:
    cleaned = " ".join(message.splitlines()).strip()
    return cleaned or "chore: auto commit before engineer_submit_work"


def _auto_commit_declared_changes(task_id: str, pr_package: dict[str, Any]) -> tuple[bool, str]:
    dirty_files, dirty_error = _collect_worktree_changes()
    if dirty_error is not None or dirty_files is None:
        return False, dirty_error or "无法识别工作区改动。"
    if not dirty_files:
        return True, ""

    declared_files = _declared_change_files(pr_package)
    undeclared_files = sorted(dirty_files - declared_files)
    if undeclared_files:
        return False, (
            "检测到未在 pr_package.changes 声明的工作区改动，请先补全 PR 包或清理后重试："
            + ", ".join(undeclared_files)
        )

    try:
        add_cmd = ["git", "add", "-A", "--", *sorted(declared_files)]
        add_result = subprocess.run(
            add_cmd,
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=10,
        )
        if add_result.returncode != 0:
            err = (add_result.stderr or add_result.stdout or "").strip()
            return False, f"自动暂存改动失败: {err}"

        commit_message = _sanitize_commit_message(pr_package.get("pr_title", f"chore({task_id}): auto commit"))
        commit_result = subprocess.run(
            ["git", "commit", "-m", commit_message],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=15,
        )
        if commit_result.returncode != 0:
            err = (commit_result.stderr or commit_result.stdout or "").strip()
            return False, f"自动提交本地 commit 失败: {err}"

        head_result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=5,
            check=True,
        )
        return True, head_result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "自动提交本地 commit 超时，请稍后重试。"
    except Exception as e:
        return False, f"自动提交本地 commit 异常: {str(e)}"


def _format_pr_ready_package(pr_package: dict[str, Any]) -> str:
    branch = pr_package.get("branch", "")
    target = pr_package.get("target", "")
    title = pr_package.get("pr_title", "")
    description = pr_package.get("pr_description", "")
    changes = pr_package.get("changes", [])
    test_results = pr_package.get("test_results", {})
    risks = pr_package.get("risks", [])
    rollback = pr_package.get("rollback", "")

    change_lines = []
    for change in changes:
        if isinstance(change, dict):
            file_path = change.get("file", "")
            desc = change.get("description", "")
            change_lines.append(f"- {file_path}: {desc}")

    test_lines = [f"- {name}: {result}" for name, result in test_results.items()]
    risk_lines = [f"- {risk}" for risk in risks] if risks else ["- 无"]

    lines = [
        "PR 就绪包",
        f"- branch: {branch}",
        f"- target: {target}",
        f"- title: {title}",
        "- description:",
        description,
        "- changes:",
        *change_lines,
        "- test_results:",
        *test_lines,
        "- risks:",
        *risk_lines,
        f"- rollback: {rollback}",
    ]
    return "\n".join(lines)


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

    def _try_acquire_fallback_lock(lp_path: str, deadline: float) -> bool:
        fallback_lock = f"{lp_path}.fallback"
        while time.monotonic() < deadline:
            try:
                fd = os.open(fallback_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    with open(fallback_lock, "r", encoding="utf-8") as f:
                        payload = json.load(f)
                    lock_time = float(payload.get("monotonic", 0.0))
                    lock_pid = int(payload.get("pid", -1))
                except (ValueError, TypeError, OSError, json.JSONDecodeError):
                    lock_time = 0.0
                    lock_pid = -1

                if time.monotonic() - lock_time >= LOCK_TIMEOUT_SEC:
                    try:
                        os.remove(fallback_lock)
                    except OSError:
                        pass
                    continue

                if lock_pid == os.getpid():
                    return False

                time.sleep(LOCK_RETRY_INTERVAL_SEC)
                continue
            except OSError:
                return False

            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"pid": os.getpid(), "monotonic": time.monotonic()}, f)
                    f.flush()
                    os.fsync(f.fileno())
                return True
            except OSError:
                try:
                    os.remove(fallback_lock)
                except OSError:
                    pass
                return False

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
            if fallback_allowed and _try_acquire_fallback_lock(lockfile_path, deadline):
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


def _extract_task_id(task_desc: str) -> str | None:
    match = re.search(r"\btask\d+(?:\.\d+)*(?:-[A-Za-z0-9]+)*\b", task_desc, re.IGNORECASE)
    if match is None:
        return None
    matched = match.group(0)
    return f"Task{matched[4:]}"


def _run_git_checkout(
    branch_name: str,
    *,
    base_branch: str = "main",
    create_from_base: bool = False,
) -> tuple[bool, str]:
    try:
        create_err = ""
        if create_from_base:
            create = subprocess.run(
                ["git", "checkout", "-b", branch_name, base_branch],
                capture_output=True,
                text=True,
                cwd=PROJECT_ROOT,
                timeout=10,
            )
            if create.returncode == 0:
                return True, f"🌿 已自动从 [{base_branch}] 创建并切换到新分支 [{branch_name}]。"
            create_err = (create.stderr or create.stdout or "").strip()

        switch = subprocess.run(
            ["git", "checkout", branch_name],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=10,
        )
        if switch.returncode == 0:
            return True, f"🌿 已自动切换到现有分支 [{branch_name}]。"

        err_switch = (switch.stderr or switch.stdout or "").strip()
        return False, f"Git 辅助异常。create: {create_err}; switch: {err_switch}"
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
    current_branch = _get_current_branch()
    if current_branch == "HEAD":
        return False, "🚨 警告：当前处于 detached HEAD 状态，禁止继续执行任务流转，请先由人类修复 Git 环境。"
    if current_branch == "unknown":
        return False, "🚨 警告：无法识别当前 Git 分支状态，请先检查 Git 环境。"
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
def architect_assign_task(task_desc: str, task_id: str = "") -> str:
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

            current_task_id = state.get("task_id", "UNASSIGNED")
            next_task_id = task_id.strip() if isinstance(task_id, str) else ""
            if next_task_id:
                normalized_task_id = next_task_id
            else:
                normalized_task_id = _extract_task_id(task_desc) or current_task_id

            branch_name = _safe_branch_name(normalized_task_id)
            git_ok, git_msg = _run_git_checkout(
                branch_name,
                create_from_base=state["status"] == "IDLE" or normalized_task_id != current_task_id,
            )
            if not git_ok:
                return f"❌ {git_msg}"

            state["task_id"] = normalized_task_id
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

    parsed_package, parse_error = _parse_pr_package(pr_package)
    if parse_error is not None:
        return parse_error
    package_error = _validate_pr_package_content(parsed_package)
    if package_error is not None:
        return package_error

    current_state = _load_state()
    can_transit, transition_msg = _validate_transition(current_state["status"], "REVIEW_PENDING")
    if not can_transit:
        return f"❌ {transition_msg}"

    task_id = current_state.get("task_id", "UNASSIGNED")
    commit_ok, commit_info = _auto_commit_declared_changes(task_id, parsed_package)
    if not commit_ok:
        return f"❌ {commit_info}"

    clean, clean_msg = _check_clean_worktree()
    if not clean:
        return f"❌ {clean_msg}"

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
        commit_suffix = f"\n📝 已自动生成本地 commit: {commit_info}" if commit_info else ""
        return (
            "✅ SUCCESS: 成果已提交，状态已锁定为 REVIEW_PENDING。"
            "工程师任务已结束，请立即停止任何代码提交动作，并进入待命(IDLE)状态等待架构师审核。"
            f"{commit_suffix}"
        )
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
        if approved:
            package_summary = _format_pr_ready_package(state.get("pr_readiness_package", {}))
            return f"✅ 裁定完成：准予手工提交 PR\n\n{package_summary}"
        return "✅ 裁定完成：打回修改，状态为 REVISE_REQUIRED"
    except Exception as e:
        return f"❌ 写入任务状态失败: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
