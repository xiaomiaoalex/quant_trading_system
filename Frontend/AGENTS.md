# 前端开发

API、模型或状态语义变更先查 `frontend_docs/FRONTEND_CONTRACT.md`，按需读 `contract_models.md` 和 `contract_endpoints.md` 并核对后端实现。缺失能力记录具体 Truth Gap；不要编造接口或把 mock 当真实返回。

沿用 API Client、TanStack Query 缓存/轮询和 Zod 运行时校验。保持相关页面的 loading/empty/error/stale/degraded/blocked 状态；风险、熔断和漂移状态必须清晰。乐观更新失败需回滚。

实际交易、策略启停、风控及破坏性操作保留产品既有确认机制；不要仅凭 HTTP 方法把普通可逆交互一律加上二次确认。本次开发遵守现有产品契约，改变确认语义需要对应产品需求。

在本目录运行 `npm run typecheck`、受影响的 `npm run test -- <测试路径>`；按变更运行 lint/build，合并检查以 CI 为准。当前 package.json 没有 `test:e2e`，不要宣称运行过。只有缺少依赖时安装，使用现有 lockfile；不重新 npm init。

行为变更测试相关状态、失败路径及回滚；纯文档不强制覆盖率门槛或全量单测。共享行为改变再扩大回归。

已有任务编号沿用 Task 9.x，映射查 `frontend_docs/FRONTEND_PROJECT_TRACKER.md` 与 `truth_gap_priority.md`。任务状态、契约或阻塞发生变化时更新对应文档；无需每次写会议纪要或燃尽图。
