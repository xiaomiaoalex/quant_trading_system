import json
import os
from datetime import datetime
import subprocess

# 告示板文件的路径
DB_FILE = "mcp_mission_control.json"

def check_git_health():
    """
    物理防线：确保当前目录是一个健康的 Git 仓库。
    防止 AI 在 .git 丢失时执行破坏性的 git init。
    """
    if not os.path.exists(".git"):
        return False, "🚨 严重警告：未检测到 .git 目录！物理环境已损坏，可能是目录切换错误或仓库丢失。绝对禁止执行任何 Git 命令（尤其是 git init），请立即停止当前任务并通知人类老板！"
    return True, "Git 环境健康"

def init_db():
    """初始化 MCP 桥接器，默认进入 Sprint 2 状态"""
    if not os.path.exists(DB_FILE):
        initial_data = {
            "current_version": "v3.0.6", 
            "sprint": "Sprint 2", 
            "task_id": "Task10.3-B", 
            "status": "IDLE",
            "completed_milestones": ["Task10.3-A: Risk Events Persistence"], 
            "architect_instruction": "请开始 Sprint 2 任务：实现 risk_upgrades 表及其 repository 接口，确保升级 key 唯一约束。",
            "engineer_report": "",
            "pr_readiness_package": None,
            "last_update": str(datetime.now())
        }
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(initial_data, f, ensure_ascii=False, indent=4)

def read_mission_state():
    """供工程师/架构师调用：读取当前的任务全貌和状态"""
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"error": "任务控制文件不存在，请先让系统初始化。"}

def architect_assign_task(task_desc):
    """供首席架构师调用：发布具体开发指令，并安全地自动创建/切换分支"""
    
    # 1. 先检查 Git 健康度（物理防线）
    is_healthy, health_msg = check_git_health()
    if not is_healthy:
        return health_msg # 如果 Git 坏了，直接拒绝下发任务
    
    # 2. 读取当前状态
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    task_id = data.get("task_id", "unknown-task")
    branch_msg = ""
    
    # 3. 自动化 Git 分支操作
    # 清洗分支名：比如 "Task10.3-B" 会变成 "feature/task10-3-b"
    safe_task_id = task_id.lower().replace('.', '-').replace('_', '-')
    branch_name = f"feature/{safe_task_id}"
    
    try:
        # 尝试创建并切换到新分支
        result = subprocess.run(
            ["git", "checkout", "-b", branch_name], 
            capture_output=True, text=True
        )
        if result.returncode == 0:
            branch_msg = f"\n🌿 Git 辅助：已安全为您创建并切换到新分支 [{branch_name}]。"
        elif "already exists" in result.stderr:
            # 如果分支已存在，尝试直接切换过去
            subprocess.run(["git", "checkout", branch_name], capture_output=True)
            branch_msg = f"\n🌿 Git 辅助：已自动切换到现有分支 [{branch_name}]。"
        elif "overwritten by checkout" in result.stderr:
            branch_msg = f"\n⚠️ Git 辅助拦截：您当前工作区有未提交的代码，自动切分支失败，请手动处理。"
        else:
            branch_msg = f"\n⚠️ Git 辅助异常：请手动创建分支。提示：{result.stderr.strip()}"
    except Exception as e:
        branch_msg = f"\n⚠️ Git 自动化脚本运行失败: {str(e)}"

    # 4. 更新告示板状态
    data["status"] = "DEVELOPING"
    data["architect_instruction"] = task_desc
    data["last_update"] = str(datetime.now())
    
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
    # 5. 返回给 AI 的结果（AI 会把这个结果展示给你看）
    return "✅ 指令已送达工程师，状态切换至：开发中。" + branch_msg

def engineer_submit_work(report, pr_package):
    """供首席工程师调用：提交成果和 PR 就绪包"""
    # 【核心防御点】：AI 提交工作前，强制检查 Git 健康度
    is_healthy, msg = check_git_health()
    if not is_healthy:
        return msg  # 阻断执行，直接向 AI 抛出严重警告

    with open(DB_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    data["status"] = "REVIEW_PENDING"
    data["engineer_report"] = report
    data["pr_readiness_package"] = pr_package 
    data["last_update"] = str(datetime.now())
    
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    return "✅ 成果已提交，PR 就绪包已存档，等待架构师 Review"

def architect_finalize(feedback, approved=False):
    """供首席架构师调用：最终代码 Review 裁定"""
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    if approved:
        data["status"] = "APPROVED_FOR_PUSH"
    else:
        data["status"] = "REVISE_REQUIRED"
        
    data["architect_instruction"] = feedback
    data["last_update"] = str(datetime.now())
    
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    return f"✅ 裁定完成：{'准予提交 PR' if approved else '需要进一步修改'}"

if __name__ == "__main__":
    init_db()
    print("MCP 桥接服务器已就绪，当前进入 Sprint 2 (Task10.3-B) 状态，已开启 Git 物理防线。")