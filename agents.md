# AGENTS.md

本文件只定义角色边界与规则优先级，避免多份规则互相冲突。

## Rule Priority (最高优先)

1. `.traerules`：工程师实现与审查的唯一执行规范。
2. 本文件：仅做角色说明与入口指引，不新增与 `.traerules` 冲突的实现规则。

如本文件任一条款与 `.traerules` 冲突，以 `.traerules` 为准。

## Role Scoping

- 角色按职责定义，与具体大模型无关。
- **Engineer（默认）**：开始工作前必须先读取并遵循 `.traerules`。
- **Chief Architect**：负责任务拆解、门禁标准和审查裁定，不替代工程师实现规范。
- `Chief Architect` 和 `Engineer` 可由任意模型承担，不应在规则中写死为某个模型名称。

## Engineer Entry Rule

工程师每次开始任务时执行顺序：

1. 读取 `.traerules`
2. 按 `.traerules` 实现与测试
3. 遵循分支和提交规范进行代码管理

## Commit Policy

- 遵循 `.traerules` 中的 Conventional Commits 规范
- Commit message 必须包含 task ID
- 分支命名必须包含 task ID

## Environment

- 必须使用项目虚拟环境执行命令：`.\\.venv\\Scripts\\python.exe ...`
