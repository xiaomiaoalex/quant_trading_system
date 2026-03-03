import json
import os
from datetime import datetime

DB_FILE = "mcp_mission_control.json"

def init_db():
    """初始化 MCP 桥接器，同步至 Sprint 2 状态"""
    if not os.path.exists(DB_FILE):
        initial_data = {
            # 按照 .traerules 要求，完成 10.3-A 后版本号递增
            "current_version": "v3.0.6", 
            "sprint": "Sprint 2", # 进入下一阶段
            "task_id": "Task10.3-B", # 聚焦升级幂等持久化
            "status": "IDLE",
            "completed_milestones": ["Task10.3-A: Risk Events Persistence"], # 记录已完成工作
            "architect_instruction": "请开始 Sprint 2 任务：实现 risk_upgrades 表及其 repository 接口，确保升级 key 唯一约束。",
            "engineer_report": "",
            "pr_readiness_package": None,
            "last_update": str(datetime.now())
        }
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(initial_data, f, ensure_ascii=False, indent=4)

def architect_assign_task(task_desc):
    """首席架构师调用：发布指令"""
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    data["status"] = "DEVELOPING"
    data["architect_instruction"] = task_desc
    data["last_update"] = str(datetime.now())
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    return "✅ 指令已送达工程师，状态切换至：开发中"

def engineer_submit_work(report, pr_package):
    """工程师调用：提交成果和 PR 就绪包"""
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    data["status"] = "REVIEW_PENDING"
    data["engineer_report"] = report
    data["pr_readiness_package"] = pr_package # 包含变更清单、测试结果等
    data["last_update"] = str(datetime.now())
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    return "✅ 成果已提交，PR 就绪包已存档，等待架构师 Review"

def architect_finalize(feedback, approved=False):
    """首席架构师调用：最终裁定"""
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