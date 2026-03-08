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
    "PR_OPENED",
    "MERGED",
    "CLOSED_NO_PR",
}

ALLOWED_TRANSITIONS = {
    ("IDLE", "DEVELOPING"),
    ("REVISE_REQUIRED", "DEVELOPING"),
    ("APPROVED_FOR_PUSH", "DEVELOPING"),
    ("MERGED", "DEVELOPING"),
    ("CLOSED_NO_PR", "DEVELOPING"),
    ("DEVELOPING", "REVIEW_PENDING"),
    ("REVIEW_PENDING", "REVIEW_PENDING"),
    ("REVISE_REQUIRED", "REVIEW_PENDING"),
    ("REVIEW_PENDING", "APPROVED_FOR_PUSH"),
    ("REVIEW_PENDING", "CLOSED_NO_PR"),
    ("REVIEW_PENDING", "REVISE_REQUIRED"),
    ("APPROVED_FOR_PUSH", "PR_OPENED"),
    ("APPROVED_FOR_PUSH", "MERGED"),
    ("PR_OPENED", "MERGED"),
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
        "review_lock": {
            "active": False,
            "notice": "",
            "locked_at": "",
        },
        "pr_tracking": {
            "pr_number": "",
            "pr_url": "",
            "opened_at": "",
            "merged_at": "",
            "merge_commit": "",
        },
        "last_update": _utc_now_iso(),
    }


def _normalize_review_lock(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"active": False, "notice": "", "locked_at": ""}
    return {
        "active": bool(value.get("active", False)),
        "notice": str(value.get("notice", "") or ""),
        "locked_at": str(value.get("locked_at", "") or ""),
    }


def _normalize_pr_tracking(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "pr_number": "",
            "pr_url": "",
            "opened_at": "",
            "merged_at": "",
            "merge_commit": "",
        }
    return {
        "pr_number": str(value.get("pr_number", "") or ""),
        "pr_url": str(value.get("pr_url", "") or ""),
        "opened_at": str(value.get("opened_at", "") or ""),
        "merged_at": str(value.get("merged_at", "") or ""),
        "merge_commit": str(value.get("merge_commit", "") or ""),
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
    normalized["review_lock"] = _normalize_review_lock(normalized.get("review_lock"))
    normalized["pr_tracking"] = _normalize_pr_tracking(normalized.get("pr_tracking"))
    return normalized


def _parse_pr_package(pr_package: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        parsed_package = json.loads(pr_package)
    except json.JSONDecodeError:
        return None, "❌ pr_package必须是合法的JSON字符串。"
    if not isinstance(parsed_package, dict):
        return None, "❌ pr_package必须是有效的JSON对象。"
    return parsed_package, None


def _can_engineer_submit(current_status: str) -> tuple[bool, str]:
    if current_status in {"DEVELOPING", "REVIEW_PENDING", "REVISE_REQUIRED"}:
        return True, ""
    if current_status not in VALID_STATUSES:
        return False, f"非法状态值: {current_status}"
    return False, f"非法状态流转: {current_status} -> REVIEW_PENDING"


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
    if not current_branch or current_branch in {"", "HEAD"}:
        return False, "🚨 警告：当前 Git 分支处于 detached HEAD 状态。"
    if current_branch == "unknown":
        return False, "🚨 警告：无法识别当前 Git 分支状态。"
    return True, "Git 环境健康"


def _validate_pr_package_content(pr_package: dict[str, Any]) -> str | None:
    required_fields = [
        ("pr_title", "PR 标题"),
        ("pr_description", "PR 描述"),
        ("changes", "变更列表"),
        ("test_results", "测试结果"),
        ("risks", "风险说明"),
        ("rollback", "回滚方案"),
    ]
    for key, label in required_fields:
        if key not in pr_package:
            return f"❌ pr_package缺少必填项：{label}。"

    changes = pr_package.get("changes")
    if not isinstance(changes, list):
        return "❌ pr_package.changes 必须是数组。"
    for change in changes:
        if not isinstance(change, dict):
            return "❌ pr_package.changes 中每一项都必须是对象。"
        for key, label in (("file", "文件路径"), ("type", "变更类型"), ("description", "变更说明")):
            if key not in change or not isinstance(change.get(key), str) or not change.get(key).strip():
                return f"❌ pr_package.changes 缺少必填项：{label}。"

    if not isinstance(pr_package.get("test_results"), dict):
        return "❌ pr_package.test_results 必须是对象。"
    if not isinstance(pr_package.get("risks"), list):
        return "❌ pr_package.risks 必须是数组。"
    if not isinstance(pr_package.get("rollback"), str) or not pr_package.get("rollback", "").strip():
        return "❌ pr_package.rollback 必须是非空字符串。"
    return None


def _format_pr_ready_package(pr_package: dict[str, Any]) -> str:
    if not isinstance(pr_package, dict) or not pr_package:
        return "PR 就绪包：<empty>"

    changes = pr_package.get("changes", [])
    lines = [
        "PR 就绪包",
        f"- branch: {pr_package.get('branch', '')}",
        f"- target: {pr_package.get('target', '')}",
        f"- title: {pr_package.get('pr_title', '')}",
        f"- description: {pr_package.get('pr_description', '')}",
        f"- changes: {len(changes)}",
        f"- test_results: {json.dumps(pr_package.get('test_results', {}), ensure_ascii=False)}",
        f"- risks: {json.dumps(pr_package.get('risks', []), ensure_ascii=False)}",
        f"- rollback: {pr_package.get('rollback', '')}",
    ]
    return "\n".join(lines)


def _is_close_no_pr_package(pr_package: dict[str, Any]) -> bool:
    if not isinstance(pr_package, dict):
        return False
    task_type = str(pr_package.get("task_type", "") or "").strip().lower()
    changes = pr_package.get("changes", [])
    return task_type == "verification" and isinstance(changes, list) and len(changes) == 0


def _git_guidance_for_assign_failure(reason: str) -> str:
    if reason == "health":
        lines = [
            "Git 自检建议",
            "1. git branch --show-current",
            "2. git status --short",
            "3. 若处于 detached HEAD 或分支状态异常，请先回到正常分支后再继续。",
        ]
    elif reason == "dirty":
        lines = [
            "Git 清理建议",
            "1. git status --short",
            "2. 若只是暂存现场：git stash",
            "3. 若准备保留当前修改：先 git add / git commit，再重新分派任务。",
        ]
    else:
        lines = [
            "Git 排查建议",
            "1. git branch --show-current",
            "2. git status --short",
            "3. 确认当前分支与目标基线无误后再重试。",
        ]
    return "\n".join(lines)


def _git_guidance_for_pr_submission(pr_package: dict[str, Any]) -> str:
    branch = str(pr_package.get("branch", "") or _get_current_branch())
    pr_title = str(pr_package.get("pr_title", "") or "填写本次提交信息")
    lines = [
        "Git 收尾建议",
        "1. git status --short",
        "2. git add <目标文件>",
        f'3. git commit -m "{pr_title}"',
        f"4. git push -u origin {branch}",
    ]
    return "\n".join(lines)


def _review_start_notice(extra_note: str = "") -> str:
    base_notice = "我现在开始正式审核，请停止继续修改，直到我给出结论。"
    extra = extra_note.strip()
    if not extra:
        return base_notice
    return f"{base_notice}\n\n补充说明：{extra}"


def _review_stop_notice() -> str:
    return "已停止修改，当前审查版本为最新一次 engineer_submit_work 对应版本。"


def _gh_executable() -> str | None:
    candidates = [
        "gh",
        r"C:\Program Files\GitHub CLI\gh.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "GitHub CLI", "gh.exe"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isabs(candidate):
            if os.path.exists(candidate):
                return candidate
            continue
        try:
            result = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                text=True,
                cwd=PROJECT_ROOT,
                timeout=5,
            )
            if result.returncode == 0:
                return candidate
        except Exception:
            continue
    return None


def _read_pr_info_from_gh(branch_name: str) -> tuple[dict[str, str] | None, str | None]:
    gh = _gh_executable()
    if gh is None:
        return None, "GitHub CLI 不可用，请手工提供 PR 信息。"
    try:
        result = subprocess.run(
            [
                gh,
                "pr",
                "view",
                branch_name,
                "--json",
                "number,url,state,mergeCommit,mergedAt,baseRefName,headRefName",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=15,
        )
    except Exception as exc:
        return None, f"GitHub CLI 查询异常: {exc}"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        return None, f"GitHub CLI 无法读取 PR 信息: {err}"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return None, f"GitHub CLI 返回了无效 JSON: {exc}"
    merge_commit = payload.get("mergeCommit") or {}
    return {
        "pr_number": str(payload.get("number", "") or ""),
        "pr_url": str(payload.get("url", "") or ""),
        "pr_state": str(payload.get("state", "") or ""),
        "merge_commit": str(merge_commit.get("oid", "") or ""),
        "merged_at": str(payload.get("mergedAt", "") or ""),
        "base_ref": str(payload.get("baseRefName", "") or ""),
        "head_ref": str(payload.get("headRefName", "") or ""),
    }, None


def _append_completed_milestone(state: dict[str, Any], summary: str) -> None:
    milestones = list(state.get("completed_milestones", []))
    if summary not in milestones:
        milestones.append(summary)
    state["completed_milestones"] = milestones


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
        return f"❌ {health_msg}\n\n{_git_guidance_for_assign_failure('health')}"

    clean, clean_msg = _check_clean_worktree()
    if not clean:
        return f"❌ {clean_msg}\n\n{_git_guidance_for_assign_failure('dirty')}"

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
                return f"❌ {git_msg}\n\n{_git_guidance_for_assign_failure('checkout')}"

            state["task_id"] = normalized_task_id
            state["status"] = "DEVELOPING"
            state["active_branch"] = branch_name
            state["architect_instruction"] = task_desc
            state["review_lock"] = _normalize_review_lock(None)
            state["pr_tracking"] = _normalize_pr_tracking(None)
            state["last_update"] = _utc_now_iso()
            commit(state)
        return f"✅ 任务已发布，状态：DEVELOPING。\n{git_msg}"
    except Exception as e:
        return f"❌ 写入任务状态失败: {str(e)}"


@mcp.tool()
def architect_begin_review(note: str = "") -> str:
    """架构师开始正式审核，发出冻结当前审查版本的标准通知。"""
    try:
        with _locked_state() as (state, commit):
            current_status = state["status"]
            if current_status != "REVIEW_PENDING":
                return f"❌ 非法状态流转: {current_status} -> REVIEW_PENDING(开始审核)"
            if state["review_lock"]["active"]:
                return "❌ 审核已在进行中，请勿重复开始。"

            notice = _review_start_notice(note)
            state["review_lock"] = {
                "active": True,
                "notice": notice,
                "locked_at": _utc_now_iso(),
            }
            state["architect_instruction"] = notice
            state["last_update"] = _utc_now_iso()
            commit(state)
        return f"✅ 已开始正式审核。\n{notice}"
    except Exception as e:
        return f"❌ 写入任务状态失败: {str(e)}"


@mcp.tool()
def engineer_submit_work(report: str, pr_package: str) -> str:
    """工程师提交当前可审快照；待审核阶段不自动 commit，便于继续人工/IDE 审查。"""
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
    if current_state["review_lock"]["active"]:
        return (
            "❌ 架构师已开始正式审核，请先停止继续修改并等待结论。\n"
            f"{current_state['review_lock']['notice']}\n\n"
            f"工程师确认语：{_review_stop_notice()}"
        )

    try:
        with _locked_state() as (state, commit):
            can_transit, transition_msg = _can_engineer_submit(state["status"])
            if not can_transit:
                return f"❌ {transition_msg}"
            if state["review_lock"]["active"]:
                return (
                    "❌ 架构师已开始正式审核，请先停止继续修改并等待结论。\n"
                    f"{state['review_lock']['notice']}\n\n"
                    f"工程师确认语：{_review_stop_notice()}"
                )

            state["status"] = "REVIEW_PENDING"
            state["engineer_report"] = report
            state["pr_readiness_package"] = parsed_package
            state["last_update"] = _utc_now_iso()
            commit(state)
        return (
            "✅ 已提交当前可审快照，状态为 REVIEW_PENDING。\n"
            "当前阶段不会自动 commit，便于继续使用 Trae 智能审查或人工指导后迭代修改。\n"
            "如后续仍有新修改，请再次调用 engineer_submit_work() 覆盖更新。"
        )
    except Exception as e:
        return f"❌ 写入任务状态失败: {str(e)}"


@mcp.tool()
def architect_finalize(feedback: str, approved: bool) -> str:
    """架构师给出最终审核结论。"""
    target_status = "APPROVED_FOR_PUSH" if approved else "REVISE_REQUIRED"
    try:
        with _locked_state() as (state, commit):
            if approved and _is_close_no_pr_package(state.get("pr_readiness_package", {})):
                target_status = "CLOSED_NO_PR"
            can_transit, transition_msg = _validate_transition(state["status"], target_status)
            if not can_transit:
                return f"❌ {transition_msg}"

            state["status"] = target_status
            state["architect_instruction"] = feedback
            state["review_lock"] = _normalize_review_lock(None)
            state["last_update"] = _utc_now_iso()
            commit(state)
        if approved:
            package_summary = _format_pr_ready_package(state.get("pr_readiness_package", {}))
            if target_status == "CLOSED_NO_PR":
                return f"✅ 裁定完成：核销关闭，无需 PR\n\n{package_summary}"
            git_guidance = _git_guidance_for_pr_submission(state.get("pr_readiness_package", {}))
            followup = (
                "后续 MCP 动作\n"
                "1. PR 创建后调用 architect_mark_pr_opened()\n"
                "2. PR merge 后调用 architect_mark_merged()"
            )
            return f"✅ 裁定完成：准予手工提交 PR\n\n{package_summary}\n\n{git_guidance}\n\n{followup}"
        return "✅ 裁定完成：打回修改，状态为 REVISE_REQUIRED"
    except Exception as e:
        return f"❌ 写入任务状态失败: {str(e)}"


@mcp.tool()
def architect_mark_pr_opened(pr_url: str = "", pr_number: str = "") -> str:
    """标记 PR 已创建；若安装了 gh，且参数为空，则尝试自动读取 PR 信息。"""
    try:
        with _locked_state() as (state, commit):
            can_transit, transition_msg = _validate_transition(state["status"], "PR_OPENED")
            if not can_transit:
                return f"❌ {transition_msg}"

            branch_name = str(state.get("pr_readiness_package", {}).get("branch", "") or state.get("active_branch", ""))
            detected, detected_err = (None, None)
            if not pr_url.strip() or not pr_number.strip():
                detected, detected_err = _read_pr_info_from_gh(branch_name)

            resolved_number = pr_number.strip() or (detected or {}).get("pr_number", "")
            resolved_url = pr_url.strip() or (detected or {}).get("pr_url", "")
            pr_state = (detected or {}).get("pr_state", "")

            if detected is not None and pr_state and pr_state != "OPEN":
                return f"❌ 当前 PR 状态不是 OPEN，而是 {pr_state}。请直接使用 architect_mark_merged() 或检查远端状态。"
            if not resolved_number and not resolved_url:
                if detected_err:
                    return f"❌ 无法自动读取 PR 信息，也未提供 pr_url/pr_number。\n{detected_err}"
                return "❌ 请提供 pr_url 或 pr_number，或确保 gh 可读取当前分支 PR。"

            state["status"] = "PR_OPENED"
            state["architect_instruction"] = "PR 已创建，等待合并。"
            state["pr_tracking"] = {
                "pr_number": resolved_number,
                "pr_url": resolved_url,
                "opened_at": _utc_now_iso(),
                "merged_at": "",
                "merge_commit": "",
            }
            package = dict(state.get("pr_readiness_package", {}))
            if resolved_number:
                package["pr_number"] = resolved_number
            if resolved_url:
                package["pr_url"] = resolved_url
            state["pr_readiness_package"] = package
            state["last_update"] = _utc_now_iso()
            commit(state)
        lines = ["✅ 已记录 PR 创建状态。"]
        if resolved_number:
            lines.append(f"PR #{resolved_number}")
        if resolved_url:
            lines.append(resolved_url)
        lines.append("后续在 PR merge 后调用 architect_mark_merged()。")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 写入任务状态失败: {str(e)}"


@mcp.tool()
def architect_mark_merged(merge_commit: str = "", summary: str = "") -> str:
    """标记当前任务对应 PR 已合并；若安装了 gh，则尝试自动读取 merge 信息。"""
    try:
        with _locked_state() as (state, commit):
            current_status = state["status"]
            if current_status not in {"APPROVED_FOR_PUSH", "PR_OPENED"}:
                return f"❌ 非法状态流转: {current_status} -> MERGED"

            branch_name = str(state.get("pr_readiness_package", {}).get("branch", "") or state.get("active_branch", ""))
            detected, detected_err = _read_pr_info_from_gh(branch_name)
            detected_state = (detected or {}).get("pr_state", "")
            resolved_commit = merge_commit.strip() or (detected or {}).get("merge_commit", "")
            resolved_number = (detected or {}).get("pr_number", "") or state.get("pr_tracking", {}).get("pr_number", "")
            resolved_url = (detected or {}).get("pr_url", "") or state.get("pr_tracking", {}).get("pr_url", "")
            resolved_merged_at = (detected or {}).get("merged_at", "") or _utc_now_iso()

            if detected is not None and detected_state and detected_state != "MERGED":
                return f"❌ 当前 PR 状态不是 MERGED，而是 {detected_state}。"
            if not resolved_commit and detected_err and current_status == "PR_OPENED":
                return f"❌ 无法确认 merge commit。\n{detected_err}"

            task_id = str(state.get("task_id", "UNASSIGNED"))
            milestone_summary = summary.strip() or f"{task_id}: Merged"

            state["status"] = "MERGED"
            state["architect_instruction"] = summary.strip() or "PR 已合并到 main。"
            state["review_lock"] = _normalize_review_lock(None)
            state["pr_tracking"] = {
                "pr_number": resolved_number,
                "pr_url": resolved_url,
                "opened_at": state.get("pr_tracking", {}).get("opened_at", ""),
                "merged_at": resolved_merged_at,
                "merge_commit": resolved_commit,
            }
            _append_completed_milestone(state, milestone_summary)
            state["last_update"] = _utc_now_iso()
            commit(state)
        lines = ["✅ 已记录 PR 合并状态。", milestone_summary]
        if resolved_number:
            lines.append(f"PR #{resolved_number}")
        if resolved_url:
            lines.append(resolved_url)
        if resolved_commit:
            lines.append(f"merge_commit: {resolved_commit}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 写入任务状态失败: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
