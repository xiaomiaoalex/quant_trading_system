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

# 状态定义与流转保持不变
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

# --- 优化 1: 调大超时时间，并提供更具引导性的错误日志 ---
LOCK_TIMEOUT_SEC = float(os.getenv("MCP_LOCK_TIMEOUT_SEC", "10.0")) # 增加到10秒，减少AI焦虑
LOCK_RETRY_INTERVAL_SEC = float(os.getenv("MCP_LOCK_RETRY_INTERVAL_SEC", "0.1"))

mcp = FastMCP("Dual_AI_Communicator")

# --- 新增辅助函数：Git 完整性校验 ---
def _get_current_branch() -> str:
    """获取当前 Git 分支名称"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, check=True
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def _db_path() -> str:
    return os.path.join(PROJECT_ROOT, DB_FILE)

def _lock_path(path: str) -> str:
    return f"{path}.lock"

def _default_state() -> dict[str, Any]:
    return {
        "current_version": "v3.0.9", # 版本微调
        "sprint": "Sprint 3",
        "task_id": "UNASSIGNED",
        "active_branch": "main",      # 新增：记录该任务应当在哪个分支执行
        "status": "IDLE",
        "completed_milestones": [],
        "architect_instruction": "等待下发",
        "engineer_report": "",
        "pr_readiness_package": {},
        "last_update": _utc_now_iso(),
    }

# (省略 _normalize_pr_package 和 _normalize_state，逻辑保持不变)
# ... [保留原有的数据规格化逻辑] ...

@contextmanager
def _with_file_lock(lockfile_path: str):
    os.makedirs(os.path.dirname(lockfile_path) or ".", exist_ok=True)
    with open(lockfile_path, "a+b") as lock_file:
        if os.path.getsize(lockfile_path) == 0:
            lock_file.write(b"0")
            lock_file.flush()
        lock_file.seek(0)
        deadline = time.monotonic() + LOCK_TIMEOUT_SEC
        
        # 优化锁定逻辑
        locked = False
        while time.monotonic() < deadline:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except (OSError, BlockingIOError):
                time.sleep(LOCK_RETRY_INTERVAL_SEC)
        
        if not locked:
            # --- 优化 2: 明确告知 AI 禁止手动修改文件 ---
            error_msg = (
                f"❌ [MISSION_CONTROL] 无法获得文件锁 ({lockfile_path})。\n"
                "⚠️ 请勿尝试手动编辑 mcp_mission_control.json！\n"
                "👉 原因：系统检测到并发冲突或操作过快。请稍后重试或检查 Git 工作区状态。"
            )
            raise TimeoutError(error_msg)
            
        try:
            yield
        finally:
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                try: msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError: pass
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

# --- 优化 3: 核心逻辑 read_mission_state 增加完整性校验 ---
@mcp.tool()
def read_mission_state() -> str:
    """读取任务状态。增加分支一致性校验，防止 AI 在错误的分支上工作。"""
    try:
        state = _load_state()
        current_branch = _get_current_branch()
        expected_branch = state.get("active_branch", "main")

        # 强制性校验：如果状态不是 IDLE 且分支不匹配，发出警报
        if state["status"] != "IDLE" and current_branch != expected_branch:
            warning = (
                f"\n🚨 [CRITICAL_WARNING] 运行环境异常！\n"
                f"- 当前 Git 分支: {current_branch}\n"
                f"- 任务锁定分支: {expected_branch}\n"
                f"⚠️ 禁止在当前分支进行任何代码修改。请先切换回 {expected_branch}。"
            )
            state["_runtime_alert"] = warning 
            
        return json.dumps(state, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"❌ 读取任务状态失败: {str(e)}"

@mcp.tool()
def architect_assign_task(task_desc: str) -> str:
    """供首席架构师调用：发布具体开发指令，系统将自动绑定并切换 Git 分支。"""
    is_healthy, health_msg = check_git_health()
    if not is_healthy: return f"❌ {health_msg}"

    clean, clean_msg = _check_clean_worktree()
    if not clean: return f"❌ {clean_msg}"

    try:
        with _locked_state() as (locked_state, commit):
            can_transit, transition_msg = _validate_transition(locked_state["status"], "DEVELOPING")
            if not can_transit: return f"❌ {transition_msg}"

            # 自动生成分支并记录在 JSON 中
            branch_name = _safe_branch_name(locked_state.get("task_id", "UNASSIGNED"))
            git_ok, git_msg = _run_git_checkout(branch_name)
            if not git_ok: return f"❌ {git_msg}"

            locked_state["status"] = "DEVELOPING"
            locked_state["active_branch"] = branch_name # 记录当前绑定的分支
            locked_state["architect_instruction"] = task_desc
            locked_state["last_update"] = _utc_now_iso()
            commit(locked_state)
            
            return f"✅ 任务已发布。状态：DEVELOPING。\n绑定分支：{branch_name}\n{git_msg}"
    except Exception as e:
        return f"❌ 写入任务状态失败: {str(e)}"

# (其余工具函数保持逻辑一致，但会自动集成 _with_file_lock 的新报错逻辑)
# ... [保留 engineer_submit_work 和 architect_finalize] ...

if __name__ == "__main__":
    mcp.run(transport="stdio")