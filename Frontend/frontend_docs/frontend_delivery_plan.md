# Frontend Delivery Plan

## 推荐前端栈（基于当前 API 复杂度）

- `Vite + React + TypeScript`
- `React Router`
- `TanStack Query`
- `Zod`（运行时契约兜底）
- `Tailwind CSS`（高密度控制台样式）

选择原因：
- 当前控制面以 REST + 轮询为主，`TanStack Query` 的缓存、重试、轮询与 stale 管理最匹配。
- 后端存在“文档与代码不一致”的 truth gap，`TypeScript + Zod` 能降低契约漂移风险。
- 页面优先级明确，适合按路由增量交付，不需要先引入重型状态框架。

任务编号约定（与后端一致）：
- 统一使用 `Task X.Y` 风格；前端控制台主线使用 `Task 9.x`
- `P0/P1/P2` 仅表示优先级，不作为任务编号

---

## Phase A：App Shell + Monitor + Strategies + Reconcile

### 工期预估（1 名前端）
- 预计 `2-3 周`
- 前提：仅消费现有 API，不等待新增后端接口

### 页面范围
- App Shell（导航、全局状态条、全局错误边界）
- Monitor
- Strategies
- Reconcile

### 组件范围
- `AppShell`
- `Sidebar`
- `Topbar`
- `StatusBadge`（normal/degraded/stale/blocked）
- `MetricCard`
- `AdapterHealthTable`
- `StrategyTable`
- `StrategyActionPanel`
- `ReconcileSummaryCard`
- `ReconcileDriftTable`
- `ConfirmDialog`（危险操作）

### API 依赖
- Monitor：
  - `GET /v1/monitor/snapshot`
  - `GET /v1/monitor/alerts`
  - `GET /health/ready`
  - `GET /health/dependency`
  - `GET /v1/killswitch`
- Strategies：
  - `GET /v1/strategies/registry`
  - `GET /v1/strategies/running`
  - `GET /v1/strategies/{id}/status`
  - `POST /v1/strategies/{id}/load|unload|start|stop|pause|resume`
  - `GET/POST/PUT /v1/strategies/{id}/params`
- Reconcile：
  - `GET /v1/reconciler/report`
  - `POST /v1/reconciler/trigger`
  - `GET /v1/events?stream_key=order_drifts`

### 风险点
- `monitor/snapshot` 当前并非完整聚合，部分关键值来自 query 参数
- `reconciler/trigger` 需要提交 local/exchange orders，控制台难以直接驱动
- `strategies/running` 返回语义是“loaded”，不是严格 running
- 无 push，只能轮询；高频轮询会放大后端压力

### 验收标准
- 页面全部具备 `loading/empty/error/stale/degraded` 展示
- 策略 `blocked_reason`、KillSwitch 等阻断态可见且不可弱化
- `reconciling/drifted`（含宽限期）语义可见
- 所有危险操作必须二次确认
- 不发明任何后端接口

---

## Phase B：Backtests + Reports + AI Lab

### 工期预估（1 名前端 + 后端配合）
- 预计 `2 周`
- 前提：接受“缺失接口先占位”的交付方式

### 页面范围
- Backtests
- Reports
- AI Lab（Chat + Portfolio Research）

### 组件范围
- `BacktestCreateForm`
- `BacktestRunDetail`
- `BacktestStatusBadge`
- `ReportSummaryPanel`（基于已有 metrics）
- `ChatSessionList`
- `ChatThreadPanel`
- `ProposalDecisionPanel`
- `CommitteeRunList`
- `CommitteeRunDetail`

### API 依赖
- Backtests：
  - `POST /v1/backtests`
  - `GET /v1/backtests/{run_id}`
- Reports（可复用）：
  - `GET /v1/backtests/{run_id}`（仅读取最小 report 信息）
- AI Lab：
  - `/api/chat/sessions*`
  - `/api/portfolio-research/run`
  - `/api/portfolio-research/runs*`

### 风险点
- 缺少回测任务列表 API，Backtests 页只能“按 run_id 查询”
- 缺少标准化 report API，Reports 页无法完整落地
- Chat/Approve/Reject 多使用 query 参数，调用封装要做严格约束
- 无 AI 审计 API，AI Lab 与 Audit 的联动会中断

### 验收标准
- 可创建回测并查询单任务状态
- 可展示 `status/metrics/artifact_ref` 的最小报告
- AI Lab 能完整表达 `pending/approved/rejected` 相关流程
- 缺失接口区域明确标注阻塞，不伪造数据结构

缺失且阻塞项：
- 回测任务列表、报告详情、AI 审计查询
  - `// TODO: BLOCKED BY BACKEND API`

---

## Phase C：Audit + Replay + Visual Polish

### 工期预估（1 名前端 + 后端配合）
- 预计 `1-2 周`
- 前提：后端补齐 Audit/Replay 阻塞接口中的至少一部分

### 页面范围
- Audit
- Replay
- 全局视觉统一与可用性打磨

### 组件范围
- `AuditEventTimeline`
- `AuditFilterBar`
- `ReplayTriggerPanel`
- `SnapshotViewer`
- `EventTable`
- `GlobalStatusRibbon`

### API 依赖
- Audit（可复用）：
  - `GET /v1/events`
- Replay：
  - `GET /v1/events`
  - `GET /v1/snapshots/latest`
  - `POST /v1/replay`

### 风险点
- 无 `/api/audit/entries*`，Audit 页面只能做“事件级审计”，无法做 AI 审计主视图
- replay 无 job/progress/result，无法做完整任务生命周期
- 仅有 latest snapshot，无快照历史回看能力

### 验收标准
- 支持按 `trace_id / stream_key / event_type / time` 检索事件
- 可触发 replay 并展示 ActionResult 与相关事件回写
- 全局状态样式统一：风险态优先、信息密度优先、视觉克制

缺失且阻塞项：
- 审计专用 API、replay job API、snapshot history API
  - `// TODO: BLOCKED BY BACKEND API`

---

## Truth Gap 收敛顺序（建议并行由 `backend-api` 责任域补齐）

1. Task 9.1：统一 API 前缀与文档（`/v1` vs `/api/v1`）
2. Task 9.4 + Task 9.5：补回测列表与报告详情 API（支撑 Phase B）
3. Task 9.6 + Task 9.7：补 Audit API 与 replay job API（支撑 Phase C）
4. Task 9.2：为 Monitor 提供真实聚合 snapshot（去掉 query 拼装）
5. Task 9.10：统一 stale/degraded 枚举语义（减少前端推导歧义）

---

## 错误处理规范（新增）

统一错误结构（前端归一化）：
```typescript
interface APIError {
  code: string;         // 例如: "STRATEGY_NOT_FOUND"
  message: string;
  details?: unknown;
  request_id?: string;
}
```

HTTP 状态码映射（控制台默认策略）：
- `400`：参数或状态校验失败，表单内联报错
- `404`：资源不存在，显示可恢复提示（刷新/返回列表）
- `409`：状态冲突，提示用户先刷新再重试
- `422`：请求格式合法但语义不满足，显示字段级错误
- `500`：服务端异常，保留 request context 并提示稍后重试

危险操作失败处理：
- 写操作失败后必须回滚 optimistic UI
- 保留失败请求摘要（endpoint + payload hash + ts）用于审计复盘
- 对 `KillSwitch` / `Strategy start-stop` 失败弹出高优先级告警

---

## 是否现在适合初始化前端工程

**YES（限定为 Phase A 启动）**

原因：
- P0 页面所需核心 API 已存在，可实现“可运行、可监控、可操作”的控制台骨架。
- Phase B/C 存在阻塞 API，但不影响先启动工程并完成 Phase A。
- 应在初始化时即内置“契约漂移防护”（API typed client + zod 校验 + truth gap 标注）。
