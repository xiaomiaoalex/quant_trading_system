# AGENTS.md

本文件只定义角色边界与规则优先级，避免多份规则互相冲突。

## Rule Priority (最高优先)

1. `.traerules`：工程师实现与审查的唯一执行规范。
2. `mcp_mission_control.json` 当前任务指令（通过 MCP 下发）。
3. 本文件：仅做角色说明与入口指引，不新增与 `.traerules` 冲突的实现规则。

如本文件任一条款与 `.traerules` 冲突，以 `.traerules` 为准。

## Role Scoping

- 角色按职责定义，与具体大模型无关。
- **Engineer（默认）**：开始工作前必须先读取并遵循 `.traerules`。
- **Chief Architect**：负责任务拆解、门禁标准、MCP 状态流转和审查裁定，不替代工程师实现规范。
- `Chief Architect` 和 `Engineer` 可由任意模型承担，不应在规则中写死为某个模型名称。

## Engineer Entry Rule

工程师每次开始任务时执行顺序：

1. 读取 `.traerules`
2. 调用 MCP `read_mission_state()` 获取当前任务
3. 按 `.traerules` 实现与测试
4. 调用 `engineer_submit_work()` 提交证据链

## Pre-Review Commit Policy (强制)

- 在架构师 `architect_finalize(approved=true)` 之前，工程师不得创建 commit。
- `engineer_submit_work()` 是“审查快照提交”，不是最终代码提交；在未冻结阶段允许反复修改并重复提交。
- 只有当架构师调用 `architect_begin_review()` 后才进入冻结；冻结后若需修改，必须等待打回结论后再提交新快照。


## Submit Stability Policy (强制)

- `pr_description` 与 `report` 只保留摘要，详细证据写入 `changes` / `test_results` / `spec_alignment`。
- 每次 `engineer_submit_work()` 后必须立刻调用 `read_mission_state()`。
- 仅当 `status == REVIEW_PENDING` 才判定提交成功；否则停止后续动作并报告架构师。
## MCP Workflow (固定四步)

- `architect_assign_task` / `architect_reassign_active_task`
- `engineer_submit_work`
- `architect_begin_review`
- `architect_finalize`

## Hard Constraints (from workflow)

- 禁止手工编辑 `mcp_mission_control.json`
- 工程师禁止用临时脚本绕过 IDE 原生 MCP 工具
- 审查冻结后不得继续修改，需按协议重新提交

## Environment

- 必须使用项目虚拟环境执行命令：`.\\.venv\\Scripts\\python.exe ...`
