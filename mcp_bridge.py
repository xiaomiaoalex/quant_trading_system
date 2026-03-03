import json
import os
import subprocess
from datetime import datetime
from mcp.server.fastmcp import FastMCP

# 初始化 FastMCP 服务器
mcp = FastMCP("MissionControl")

DB_FILE = "mcp_mission_control.json"

def check_git_health() -> tuple[bool, str]:
    """内部函数：检查 Git 物理环境"""
    if not os.path.exists(".git"):
        return False, "🚨 严重警告：未检测到 .git 目录！物理环境已损坏。绝对禁止执行任何 Git 命令，请立即停止并通知人类老板！"
    return True, "Git 环境健康"

def ensure_db_exists():
    """内部函数：确保告示板文件存在"""
    if not os.path.exists(DB_FILE):
        initial_data = {
            "current_version": "v3.0.6", 
            "sprint": "Sprint 2", 
            "task_id": "Task10.3-B", 
            "status": "IDLE",
            "completed_milestones": ["Task10.3-A: Risk Events Persistence (Merged)"], 
            "architect_instruction": "等待下发",
            "engineer_report": "",
            "pr_readiness_package": "",
            "last_update": str(datetime.now())
        }
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(initial_data, f, ensure_ascii=False, indent=4)

@mcp.tool()
def read_mission_state() -> str:
    """读取当前的任务全貌和开发状态。任何 AI 在开始工作前都应该调用此工具获取上下文。"""
    ensure_db_exists()
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return json.dumps(data, ensure_ascii=False, indent=2)

@mcp.tool()
def architect_assign_task(task_desc: str) -> str:
    """供首席架构师调用：发布具体开发指令，系统将自动创建并切换到对应的 Git 分支。"""
    ensure_db_exists()
    is_healthy, health_msg = check_git_health()
    if not is_healthy: return health_msg 
    
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    task_id = data.get("task_id", "unknown-task")
    branch_msg = ""
    safe_task_id = task_id.lower().replace('.', '-').replace('_', '-')
    branch_name = f"feature/{safe_task_id}"
    
    try:
        result = subprocess.run(["git", "checkout", "-b", branch_name], capture_output=True, text=True)
        if result.returncode == 0:
            branch_msg = f"\n🌿 已自动创建并切换到新分支 [{branch_name}]。"
        elif "already exists" in result.stderr:
            subprocess.run(["git", "checkout", branch_name], capture_output=True)
            branch_msg = f"\n🌿 已自动切换到现有分支 [{branch_name}]。"
        else:
            branch_msg = f"\n⚠️ Git 辅助异常，请手动切分支。提示：{result.stderr.strip()}"
    except Exception as e:
        branch_msg = f"\n⚠️ Git 脚本失败: {str(e)}"

    data["status"] = "DEVELOPING"
    data["architect_instruction"] = task_desc
    data["last_update"] = str(datetime.now())
    
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        
    return "✅ 任务已发布，状态：DEVELOPING。" + branch_msg

@mcp.tool()
def engineer_submit_work(report: str, pr_package: str) -> str:
    """供首席工程师调用：完成代码开发后，提交工作总结和 PR 就绪包给架构师审核。"""
    ensure_db_exists()
    is_healthy, msg = check_git_health()
    if not is_healthy: return msg 

    with open(DB_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    data["status"] = "REVIEW_PENDING"
    data["engineer_report"] = report
    data["pr_readiness_package"] = pr_package 
    data["last_update"] = str(datetime.now())
    
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    return "✅ 成果已提交，状态变更为 REVIEW_PENDING，请通知架构师审核。"

@mcp.tool()
def architect_finalize(feedback: str, approved: bool) -> str:
    """供首席架构师调用：对工程师的代码进行最终 Review 裁定。approved 传 true 表示通过，false 表示打回。"""
    ensure_db_exists()
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    data["status"] = "APPROVED_FOR_PUSH" if approved else "REVISE_REQUIRED"
    data["architect_instruction"] = feedback
    data["last_update"] = str(datetime.now())
    
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    return f"✅ 裁定完成：{'准予手工提交 PR' if approved else '打回修改，状态为 REVISE_REQUIRED'}"

if __name__ == "__main__":
    # 以 stdio 模式运行，这是 IDE 与本地 MCP 通信的标准方式
    mcp.run(transport='stdio')