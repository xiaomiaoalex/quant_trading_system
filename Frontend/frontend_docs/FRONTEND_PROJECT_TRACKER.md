# 前端项目开发追踪器 (Frontend Project Tracker)

> **文档用途**: 动态追踪前端开发进度、协调前后端联调、管理 Truth Gap 修复
> **维护方式**: 每日更新，任务完成后立即刷新状态
> **执行模式**: AI Agent 责任域协作（无固定个人负责人）

---

## 📊 当前开发状态总览

### 最后更新时间
- **更新日期**: 2026-04-10 (北京时间)
- **更新人**: AI Agent
- **当前阶段**: Phase A - App Shell + Monitor + Strategies + Reconcile

### 分支状态
- **主分支**: `frontend/main` (待创建)
- **开发分支**: `frontend/phase-a-shell` (待创建)
- **最新提交**: 待更新

---

## 🎯 当前执行计划：Truth Gap 修复

基于 [truth_gap_priority.md](./truth_gap_priority.md) 与 [frontend_master_plan.md](./frontend_master_plan.md) 执行。

### Week 1（P0 核心修复）- 2026-04-10 至 2026-04-17

| ID | 修复项 | 优先级 | 执行状态 | 主责任域 | 协同责任域 | 审核门禁 | 预计完成 | 实际完成 |
|----|--------|--------|----------|----------|------------|----------|----------|----------|
| Task 9.1 | 统一 API 前缀与文档路径 | P0 | 🔄 执行中 | contract-doc | backend-api, frontend-console | 自测通过 + 契约一致 + 联调通过 | 0.6 天 | - |
| Task 9.2 | Monitor Snapshot 真聚合化 | P0 | ✅ 已完成 (前端适配) | frontend-console | backend-api, contract-doc, qa-validation | 自测通过 + 契约一致 + 联调通过 | 1.25 天 | 2026-04-10 |
| Task 9.3 | Reconciler 无参触发 | P0 | 🔄 执行中 | backend-api | frontend-console, qa-validation | 自测通过 + 契约一致 + 联调通过 | 1 天 | - |
| Task 9.8 | strategies/running 语义 | P2 | 🔄 执行中 | contract-doc | backend-api, frontend-console | 自测通过 + 契约一致 + 联调通过 | 0.4 天 | - |

**Week 1 状态**: 🟡 进行中 (0/4 完成)

### Week 2（P1 功能补全）- 2026-04-17 至 2026-04-24

| ID | 修复项 | 优先级 | 执行状态 | 主责任域 | 协同责任域 | 审核门禁 | 预计完成 | 实际完成 |
|----|--------|--------|----------|----------|------------|----------|----------|----------|
| Task 9.4 | Backtests 列表与进度 | P1 | ⏳ 待执行 | backend-api | frontend-console, qa-validation | 自测通过 + 契约一致 + 联调通过 | 1.1 天 | - |
| Task 9.5 | Reports 详情接口 | P1 | ⏳ 待执行 | backend-api | frontend-console, contract-doc | 自测通过 + 契约一致 + 联调通过 | 1.1 天 | - |
| Task 9.6 | Audit 专用查询接口 | P1 | ⏳ 待执行 | backend-api | frontend-console, contract-doc | 自测通过 + 契约一致 + 联调通过 | 1.1 天 | - |
| Task 9.7 | Replay 任务状态接口 | P1 | ⏳ 待执行 | backend-api | frontend-console, qa-validation | 自测通过 + 契约一致 + 联调通过 | 1.1 天 | - |

**Week 2 状态**: ⚪ 未开始 (0/4 完成)

### 并行推进（P2 一致性优化）

| ID | 修复项 | 优先级 | 执行状态 | 主责任域 | 协同责任域 | 审核门禁 | 预计完成 | 实际完成 |
|----|--------|--------|----------|----------|------------|----------|----------|----------|
| Task 9.9 | Chat 参数风格统一 | P2 | ⏳ 待执行 | backend-api | contract-doc, frontend-console | 自测通过 + 契约一致 + 联调通过 | 0.5 天 | - |
| Task 9.10 | Stale/Degraded 枚举 | P2 | ⏳ 待执行 | backend-api | frontend-console, contract-doc | 自测通过 + 契约一致 + 联调通过 | 0.75 天 | - |
| Task 9.11 | 快照历史查询接口 | P2 | ⏳ 待执行 | backend-api | frontend-console, qa-validation | 自测通过 + 契约一致 + 联调通过 | 0.9 天 | - |

**P2 状态**: ⚪ 未开始 (0/3 完成)

---

## 🏗️ Phase A：App Shell + Monitor + Strategies + Reconcile

### 工期预估
- **预计**: 2-3 周
- **前提**: 仅消费现有 API，不等待新增后端接口
- **当前状态**: 🟡 准备中

### 页面范围与状态

| 页面 | 优先级 | 设计 | 开发 | 联调 | 测试 | 状态 | 主责任域 |
|------|--------|------|------|------|------|------|----------|
| App Shell | P0 | ⏳ 待开始 | ⏳ 待开始 | ⏸️ 阻塞 | ⏸️ 阻塞 | 🔴 阻塞 | frontend-console |
| Monitor | P0 | ⏳ 待开始 | ⏳ 待开始 | ⏸️ 阻塞 | ⏸️ 阻塞 | 🔴 阻塞 | frontend-console |
| Strategies | P0 | ⏳ 待开始 | ⏳ 待开始 | ⏸️ 阻塞 | ⏸️ 阻塞 | 🔴 阻塞 | frontend-console |
| Reconcile | P0 | ⏳ 待开始 | ⏳ 待开始 | ⏸️ 阻塞 | ⏸️ 阻塞 | 🔴 阻塞 | frontend-console |

### 组件开发清单

#### 核心组件（Phase A）

| 组件名 | 用途 | 复杂度 | 状态 | 依赖 API | 备注 |
|--------|------|--------|------|----------|------|
| `AppShell` | 应用主框架 | 中 | ⏳ 待开始 | 无 | 导航、状态条 |
| `Sidebar` | 侧边导航栏 | 低 | ⏳ 待开始 | 无 | 路由导航 |
| `Topbar` | 顶部工具栏 | 低 | ⏳ 待开始 | 无 | 全局操作 |
| `StatusBadge` | 状态徽章 | 低 | ✅ 已完成 | 无 | normal/degraded/stale/blocked |
| `MetricCard` | 指标卡片 | 低 | ✅ 已完成 | Monitor | 监控指标展示 |
| `AdapterHealthTable` | 适配器健康表 | 中 | ✅ 已完成 | Monitor | 健康状态表格 |
| `StrategyTable` | 策略列表 | 中 | ⏳ 待开始 | Strategies | 策略管理 |
| `StrategyActionPanel` | 策略操作面板 | 中 | ⏳ 待开始 | Strategies | 危险操作确认 |
| `ReconcileSummaryCard` | 对账摘要卡片 | 低 | ⏳ 待开始 | Reconcile | 漂移统计 |
| `ReconcileDriftTable` | 漂移明细表 | 中 | ⏳ 待开始 | Reconcile | 漂移详情 |
| `ConfirmDialog` | 确认对话框 | 低 | ✅ 已完成 | 无 | 危险操作二次确认 |

### API 依赖状态

#### Monitor API

| API | 方法 | 后端状态 | 前端适配 | 联调状态 | 备注 |
|-----|------|----------|----------|----------|------|
| `/v1/monitor/snapshot` | GET | 🔴 需修复 (Task 9.2) | ✅ 已完成 | ⏸️ 阻塞 | 真聚合化（前端已适配） |
| `/v1/monitor/alerts` | GET | 🟢 可用 | ✅ 已完成 | ⏸️ 阻塞 | - |
| `/v1/monitor/rules` | POST | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | 危险操作 |
| `/v1/monitor/rules/{rule_name}` | DELETE | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | 危险操作 |
| `/v1/monitor/alerts/{rule_name}/clear` | POST | 🟢 可用 | ✅ 已完成 | ⏸️ 阻塞 | 危险操作 |
| `/v1/monitor/alerts/clear-all` | POST | 🟢 可用 | ✅ 已完成 | ⏸️ 阻塞 | 危险操作 |
| `/health/ready` | GET | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | - |
| `/health/dependency` | GET | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | - |
| `/v1/killswitch` | GET | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | - |

#### Strategies API

| API | 方法 | 后端状态 | 前端适配 | 联调状态 | 备注 |
|-----|------|----------|----------|----------|------|
| `/v1/strategies/registry` | GET | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | - |
| `/v1/strategies/registry` | POST | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | 危险操作 |
| `/v1/strategies/{id}/versions` | GET | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | - |
| `/v1/strategies/{id}/params` | GET/POST/PUT | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | 危险操作 |
| `/v1/strategies/{id}/load` | POST | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | 危险操作 |
| `/v1/strategies/{id}/unload` | POST | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | 危险操作 |
| `/v1/strategies/{id}/start` | POST | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | 危险操作 |
| `/v1/strategies/{id}/stop` | POST | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | 危险操作 |
| `/v1/strategies/{id}/pause` | POST | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | 危险操作 |
| `/v1/strategies/{id}/resume` | POST | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | 危险操作 |
| `/v1/strategies/{id}/status` | GET | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | - |
| `/v1/strategies/running` | GET | 🟡 需澄清 (Task 9.8) | ⏳ 待开始 | ⏸️ 阻塞 | 语义澄清 |

#### Reconcile API

| API | 方法 | 后端状态 | 前端适配 | 联调状态 | 备注 |
|-----|------|----------|----------|----------|------|
| `/v1/reconciler/report` | GET | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | - |
| `/v1/reconciler/trigger` | POST | 🔴 需修复 (Task 9.3) | ⏳ 待开始 | ⏸️ 阻塞 | 无参触发 |
| `/v1/events?stream_key=order_drifts` | GET | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | - |

---

## 🏗️ Phase B：Backtests + Reports + AI Lab

### 工期预估
- **预计**: 2 周
- **前提**: 后端补齐 P1 接口
- **当前状态**: ⚪ 未开始

### 页面范围与状态

| 页面 | 优先级 | 设计 | 开发 | 联调 | 测试 | 状态 | 主责任域 |
|------|--------|------|------|------|------|------|----------|
| Backtests | P1 | ⏳ 待开始 | ⏳ 待开始 | ⏸️ 阻塞 | ⏸️ 阻塞 | 🔴 阻塞 | frontend-console |
| Reports | P1 | ⏳ 待开始 | ⏳ 待开始 | ⏸️ 阻塞 | ⏸️ 阻塞 | 🔴 阻塞 | frontend-console |
| AI Lab | P1 | ⏳ 待开始 | ⏳ 待开始 | ⏸️ 阻塞 | ⏸️ 阻塞 | 🔴 阻塞 | frontend-console |

### 关键组件（Phase B）

| 组件名 | 用途 | 复杂度 | 状态 | 依赖 API | 备注 |
|--------|------|--------|------|----------|------|
| `BacktestCreateForm` | 回测创建表单 | 中 | ⏳ 待开始 | Backtests | - |
| `BacktestRunDetail` | 回测任务详情 | 中 | ⏳ 待开始 | Backtests | - |
| `BacktestStatusBadge` | 回测状态徽章 | 低 | ⏳ 待开始 | Backtests | - |
| `ReportSummaryPanel` | 报告摘要面板 | 中 | ⏳ 待开始 | Reports | 基于已有 metrics |
| `ChatSessionList` | 会话列表 | 低 | ⏳ 待开始 | Chat | - |
| `ChatThreadPanel` | 会话线程面板 | 中 | ⏳ 待开始 | Chat | - |
| `ProposalDecisionPanel` | 决策面板 | 中 | ⏳ 待开始 | Chat | - |
| `CommitteeRunList` | 委员会任务列表 | 中 | ⏳ 待开始 | Chat | - |
| `CommitteeRunDetail` | 委员会任务详情 | 中 | ⏳ 待开始 | Chat | - |

### API 依赖状态（Phase B）

| API | 方法 | 后端状态 | 前端适配 | 联调状态 | 备注 |
|-----|------|----------|----------|----------|------|
| `POST /v1/backtests` | POST | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | - |
| `GET /v1/backtests/{run_id}` | GET | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | - |
| `GET /v1/backtests` | GET | 🔴 缺失 (Task 9.4) | ⏳ 待开始 | ⏸️ 阻塞 | 列表接口 |
| `GET /v1/backtests/{run_id}/report` | GET | 🔴 缺失 (Task 9.5) | ⏳ 待开始 | ⏸️ 阻塞 | 报告详情 |
| `/api/chat/sessions/*` | Various | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | - |
| `/api/portfolio-research/*` | Various | 🟢 可用 | ⏳ 待开始 | ⏸️ 阻塞 | - |

---

## 🏗️ Phase C：Audit + Replay + Visual Polish

### 工期预估
- **预计**: 1-2 周
- **前提**: 后端补齐 P2 接口
- **当前状态**: ⚪ 未开始

### 页面范围与状态

| 页面 | 优先级 | 设计 | 开发 | 联调 | 测试 | 状态 | 主责任域 |
|------|--------|------|------|------|------|------|----------|
| Audit | P2 | ⏳ 待开始 | ⏳ 待开始 | ⏸️ 阻塞 | ⏸️ 阻塞 | 🔴 阻塞 | frontend-console |
| Replay | P2 | ⏳ 待开始 | ⏳ 待开始 | ⏸️ 阻塞 | ⏸️ 阻塞 | 🔴 阻塞 | frontend-console |
| Visual Polish | P2 | ⏳ 待开始 | ⏳ 待开始 | ⏸️ 阻塞 | ⏸️ 阻塞 | 🔴 阻塞 | frontend-console |

### 关键组件（Phase C）

| 组件名 | 用途 | 复杂度 | 状态 | 依赖 API | 备注 |
|--------|------|--------|------|----------|------|
| `AuditEventTimeline` | 审计事件时间线 | 高 | ⏳ 待开始 | Audit | - |
| `AuditFilterBar` | 审计过滤器 | 中 | ⏳ 待开始 | Audit | - |
| `ReplayTriggerPanel` | 回放触发面板 | 中 | ⏳ 待开始 | Replay | - |
| `SnapshotViewer` | 快照查看器 | 中 | ⏳ 待开始 | Replay | - |
| `EventTable` | 事件表格 | 中 | ⏳ 待开始 | Replay | - |
| `GlobalStatusRibbon` | 全局状态条 | 低 | ⏳ 待开始 | 无 | - |

---

## 🐛 Truth Gap 阻塞清单

### 当前阻塞问题

| ID | 问题描述 | 影响范围 | 严重性 | 状态 | 解决方案 | 主责任域 |
|----|----------|----------|--------|------|----------|----------|
| TG-001 | Monitor Snapshot 非真聚合 | Monitor 页面 | 🔴 高 | 🔄 修复中 | Task 9.2 | backend-api |
| TG-002 | Reconciler Trigger 需前端提交数据 | Reconcile 页面 | 🔴 高 | 🔄 修复中 | Task 9.3 | backend-api |
| TG-003 | API 前缀不一致 | 全局 | 🟡 中 | 🔄 修复中 | Task 9.1 | contract-doc |
| TG-004 | strategies/running 语义不符 | Strategies 页面 | 🟡 中 | 🔄 修复中 | Task 9.8 | contract-doc |
| TG-005 | 缺少 Backtests 列表 API | Backtests 页面 | 🔴 高 | ⏳ 待开始 | Task 9.4 | backend-api |
| TG-006 | 缺少 Reports 详情 API | Reports 页面 | 🔴 高 | ⏳ 待开始 | Task 9.5 | backend-api |
| TG-007 | 缺少 Audit 专用 API | Audit 页面 | 🔴 高 | ⏳ 待开始 | Task 9.6 | backend-api |
| TG-008 | 缺少 Replay job API | Replay 页面 | 🔴 高 | ⏳ 待开始 | Task 9.7 | backend-api |

### 已解决 Truth Gap

| ID | 问题描述 | 解决日期 | 解决方案 | 备注 |
|----|----------|----------|----------|------|
| - | - | - | - | - |

---

## 📅 每日站会记录

### 2026-04-10

**参会人员**: -

**昨日完成**:
- [AI Agent / `contract-doc`] 创建 Truth Gap 开发计划文档

**今日计划**:
- [AI Agent / `backend-api`] 开始 Task 9.1: 统一 API 前缀与文档路径
- [AI Agent / `backend-api`] 开始 Task 9.2: Monitor Snapshot 真聚合化
- [AI Agent / `backend-api`] 开始 Task 9.3: Reconciler 无参触发
- [AI Agent / `frontend-console`] 环境搭建与技术选型确认

**阻塞问题**:
- 无

**备注**:
- Truth Gap 开发计划已创建
- 前端技术栈确认：Vite + React + TypeScript + TanStack Query + Zod + Tailwind CSS

---

## 📋 技术栈决策记录

### 核心框架

| 技术 | 版本 | 用途 | 决策日期 | 备注 |
|------|------|------|----------|------|
| Vite | Latest | 构建工具 | 2026-04-10 | 快速 HMR |
| React | 18+ | UI 框架 | 2026-04-10 | 组件化 |
| TypeScript | 5+ | 类型系统 | 2026-04-10 | 类型安全 |
| React Router | 6+ | 路由管理 | 2026-04-10 | - |
| TanStack Query | 5+ | 数据获取 | 2026-04-10 | 缓存、轮询 |
| Zod | 3+ | 运行时验证 | 2026-04-10 | 契约兜底 |
| Tailwind CSS | 3+ | 样式框架 | 2026-04-10 | 高密度控制台 |

### 开发工具

| 工具 | 用途 | 备注 |
|------|------|------|
| ESLint | 代码检查 | - |
| Prettier | 代码格式化 | - |
| Vitest | 单元测试 | - |
| React Testing Library | 组件测试 | - |
| Playwright | E2E 测试 | 可选 |

---

## 📊 开发进度指标

### 燃尽图数据（手动更新）

| 日期 | Week 1 剩余任务 | Week 2 剩余任务 | P2 剩余任务 | 备注 |
|------|----------------|----------------|-------------|------|
| 2026-04-10 | 4 | 4 | 3 | 计划启动 |
| - | - | - | - | - |

### 任务完成率

| 阶段 | 总任务 | 已完成 | 进行中 | 待开始 | 完成率 |
|------|--------|--------|--------|--------|--------|
| Week 1 | 4 | 0 | 4 | 0 | 0% |
| Week 2 | 4 | 0 | 0 | 4 | 0% |
| P2 | 3 | 0 | 0 | 3 | 0% |
| **总计** | **11** | **0** | **4** | **7** | **0%** |

### 前端组件开发进度

| 阶段 | 总组件 | 已完成 | 进行中 | 待开始 | 完成率 |
|------|--------|--------|--------|--------|--------|
| Phase A | 11 | 0 | 0 | 11 | 0% |
| Phase B | 9 | 0 | 0 | 9 | 0% |
| Phase C | 6 | 0 | 0 | 6 | 0% |
| **总计** | **26** | **0** | **0** | **26** | **0%** |

---

## 🧪 测试覆盖率追踪

### 单元测试

| 模块 | 目标覆盖率 | 当前覆盖率 | 状态 |
|------|-----------|-----------|------|
| Components | 80% | 0% | 🔴 未开始 |
| Hooks | 80% | 0% | 🔴 未开始 |
| Utils | 90% | 0% | 🔴 未开始 |
| API Client | 90% | 0% | 🔴 未开始 |

### E2E 测试

| 页面 | 关键流程 | 状态 |
|------|---------|------|
| Monitor | 查看监控快照、清除告警 | ⏳ 待开始 |
| Strategies | 加载/卸载策略、修改参数 | ⏳ 待开始 |
| Reconcile | 触发对账、查看漂移 | ⏳ 待开始 |
| Backtests | 创建回测、查看进度 | ⏳ 待开始 |
| Reports | 查看报告详情 | ⏳ 待开始 |
| Audit | 检索审计条目 | ⏳ 待开始 |
| Replay | 触发回放、查看状态 | ⏳ 待开始 |

---

## 📝 会议纪要

### 项目启动会 (2026-04-10)

**参会人员**: AI Agent（backend-api/frontend-console/contract-doc/qa-validation）

**会议目标**:
1. 对齐 Truth Gap 修复优先级
2. 确认跨责任域协作流程
3. 明确 Phase A 交付标准

**会议决议**:
1. **P0 优先修复**: Task 9.1/9.2/9.3 为 Week 1 核心任务
2. **跨责任域并行**: `backend-api` 修复接口同时，`frontend-console` 搭建框架
3. **联调时间点**: Week 1 末进行首次联调

**行动项**:
- [ ] AI Agent / `backend-api`：Task 9.1/9.2/9.3 修复（截止：2026-04-17）
- [ ] AI Agent / `frontend-console`：App Shell 框架搭建（截止：2026-04-17）
- [ ] AI Agent / `qa-validation`：联调环境与验收脚本准备（截止：2026-04-15）

---

## 🔗 参考文档

### 内部文档
- [frontend_master_plan.md](./frontend_master_plan.md) - 前端总开发计划（单一执行入口）
- [truth_gap_priority.md](./truth_gap_priority.md) - Truth Gap 优先级清单
- [FRONTEND_CONTRACT.md](./FRONTEND_CONTRACT.md) - 前端数据契约
- [frontend_delivery_plan.md](./frontend_delivery_plan.md) - 前端交付计划
- [contract_endpoints.md](./contract_endpoints.md) - API 端点清单
- [frontend_api_inventory.md](./frontend_api_inventory.md) - API 清单

### 外部文档
- [React 官方文档](https://react.dev/)
- [TanStack Query 文档](https://tanstack.com/query/latest)
- [TypeScript 文档](https://www.typescriptlang.org/)
- [Tailwind CSS 文档](https://tailwindcss.com/)

---

## 🤝 协作域

| 责任域 | 执行主体 | 说明 |
|------|--------|------|
| backend-api | AI Agent | 路由、DTO、状态语义与兼容策略 |
| frontend-console | AI Agent | 控制台页面与 API 适配 |
| contract-doc | AI Agent | 契约文档与 truth gap 一致性 |
| qa-validation | AI Agent | 自测、联调与验收清单 |

---

## 📌 更新日志

| 日期 | 版本 | 更新内容 | 更新人 |
|------|------|---------|--------|
| 2026-04-10 | v1.0 | 初始版本，创建追踪器框架 | 架构师 |
| 2026-04-10 | v1.1 | 新增 frontend_master_plan.md，统一阶段目标/依赖/验收入口 | 架构师 |
| 2026-04-10 | v1.2 | truth_gap_development_plan.md 升级为 AI Agent 执行模型（责任域+门禁+状态流转） | 架构师 |
| 2026-04-10 | v1.3 | FRONTEND_PROJECT_TRACKER 全面切换为 AI Agent 责任域模型（移除个人负责人字段） | 架构师 |
| 2026-04-10 | v1.4 | 站会记录与会议纪要术语统一为责任域协作表述 | AI Agent |
| 2026-04-10 | v1.5 | 任务编号统一为后端同款 Task 9.x（替换旧 P0-1/P1-4/P2-8 风格） | AI Agent |
| 2026-04-10 | v1.6 | 修复执行基线引用，改为 truth_gap_priority.md + frontend_master_plan.md | AI Agent |
| 2026-04-10 | v1.7 | 清理失效文档引用，移除 truth_gap_development_plan.md 链接 | AI Agent |
| - | - | - | - |

---

## 🎯 下一步行动

### 立即行动（Today）
1. [AI Agent / `backend-api`] 开始 Task 9.1: 统一 API 前缀与文档路径
2. [AI Agent / `backend-api`] 开始 Task 9.2: Monitor Snapshot 真聚合化
3. [AI Agent / `backend-api`] 开始 Task 9.3: Reconciler 无参触发
4. [AI Agent / `frontend-console`] 初始化 Vite + React + TypeScript 项目
5. [AI Agent / `frontend-console`] 安装依赖：TanStack Query, Zod, Tailwind CSS

### 本周行动（Week 1）
1. [AI Agent / `backend-api`] 完成 Task 9.1/9.2/9.3 + Task 9.8
2. [AI Agent / `frontend-console`] 完成 App Shell 框架
3. [AI Agent / `frontend-console`] 完成 Monitor 页面基础结构
4. [AI Agent / `qa-validation`] 首次联调（预计：2026-04-17）

### 下周行动（Week 2）
1. [AI Agent / `backend-api`] 完成 Task 9.4/9.5/9.6/9.7
2. [AI Agent / `frontend-console`] 完成 Strategies + Reconcile 页面
3. [AI Agent / `frontend-console`] 开始 Backtests + Reports 页面
4. [AI Agent / `qa-validation`] Phase A 验收（预计：2026-04-24）

---

**文档维护说明**:
1. 每日站会后立即更新"每日站会记录"
2. 任务状态变化时立即更新对应表格
3. 每周五更新"开发进度指标"
4. 重大决策记录到"技术栈决策记录"或"会议纪要"
5. Truth Gap 修复完成后移动到"已解决 Truth Gap"
