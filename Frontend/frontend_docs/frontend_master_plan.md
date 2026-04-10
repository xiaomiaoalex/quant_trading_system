# Frontend Master Plan（总开发计划）

> 文档目标：作为前端控制台开发的单一执行入口（Master Entry），统一阶段目标、依赖关系、Truth Gap 收敛顺序与验收标准。  
> 适用范围：`quant_trading_system` 前端控制台（Control Console），不包含营销站与展示型页面。

---

## 1. 目标与边界

### 1.1 真实目标
前端是交易控制台，不是营销站。核心职责是：
- 真实呈现系统状态（健康、风险、策略、对账）
- 提供受控人工干预入口（危险操作必须确认）
- 承接 AI/HITL、回测、审计、回放的治理闭环

### 1.2 非目标（当前阶段）
- 营销落地页与品牌叙事页面
- 脱离控制面语义的纯收益展示
- 脑补后端未实现 API 的“假联调”

---

## 2. 真相源与文档治理

### 2.1 真相源优先级
1. 后端实际路由与 DTO / Pydantic 模型（代码）
2. `frontend_api_inventory.md`
3. `FRONTEND_CONTRACT.md` + 拆分契约文档
4. `frontend_context.md` / `frontend_delivery_plan.md`
5. `FRONTEND_PROJECT_TRACKER.md`

若文档与代码冲突，以代码契约为准，并在追踪器登记 Truth Gap。

### 2.2 文档新鲜度规则
- 任何影响阶段目标、优先级、阻塞关系的变更，必须同步刷新：
  - `frontend_master_plan.md`（本文件）
  - `FRONTEND_PROJECT_TRACKER.md`
  - 受影响的契约/清单文档（`frontend_api_inventory.md`、`FRONTEND_CONTRACT.md` 等）
- 禁止同一任务在不同文档中出现“已完成/未开始”并存。

---

## 3. AI Agent 协作模型

本计划采用“责任域协作”，不采用个人负责人模式。

### 3.1 执行主体
- 默认执行主体：`AI Agent`
- 分工单位：`backend-api` / `frontend-console` / `contract-doc` / `qa-validation`

### 3.2 审核门禁
- `自测通过`
- `契约一致`
- `联调通过`

### 3.3 状态流转
- `待执行` -> `执行中` -> `待审核` -> `已完成`

---

## 4. 控制台信息架构（IA）

### 4.1 页面优先级
- P0：`Monitor`、`Strategies`、`Reconcile`
- P1：`Backtests`、`Reports`、`AI Lab`
- P2：`Audit`、`Replay`

### 4.2 控制台核心状态语义（必须完整表达）
- 通用：`loading` / `empty` / `error`
- 控制：`stale` / `degraded` / `blocked` / `killed|halted`
- 对账：`reconciling` / `drifted`
- HITL：`approved` / `pending` / `rejected`

### 4.3 危险操作确认规则（强制）
以下操作必须“二次确认 + 影响说明 + 成功/失败反馈”：
- 所有 `POST/PUT/DELETE`
- 策略控制（`load/unload/start/stop/pause/resume`）
- 策略参数写入（`POST/PUT /v1/strategies/{id}/params`）
- KillSwitch / 风险恢复
- 手动对账触发（`POST /v1/reconciler/trigger`）
- 回放触发（`POST /v1/replay`）
- AI/HITL 审批动作（`/api/chat/*approve|reject`、`/api/portfolio-research/*submit|approve|reject`）

---

## 5. 工程策略与推荐技术栈

### 5.1 推荐栈
- `Vite + React + TypeScript`
- `React Router`
- `TanStack Query`
- `Zod`
- `Tailwind CSS`

### 5.2 采用原因（结合当前后端形态）
- 当前以 REST + 轮询为主，`TanStack Query` 适配 stale/retry/polling。
- 文档与代码存在契约漂移风险，`TypeScript + Zod` 可做运行时兜底。
- 分阶段交付明确，路由驱动增量开发优于一次性重型状态框架。

---

## 6. 三阶段交付路线

## Phase A（P0）：App Shell + Monitor + Strategies + Reconcile

### 页面范围
- App Shell（导航/全局状态条/错误边界）
- Monitor
- Strategies
- Reconcile

### API 依赖（仅真实已存在接口）
- Monitor：`GET /v1/monitor/snapshot`、`GET /v1/monitor/alerts`、`GET /health/ready`、`GET /health/dependency`、`GET /v1/killswitch`
- Strategies：`GET /v1/strategies/registry`、`GET /v1/strategies/running`、`GET /v1/strategies/{id}/status`、`POST /v1/strategies/{id}/load|unload|start|stop|pause|resume`、`GET/POST/PUT /v1/strategies/{id}/params`
- Reconcile：`GET /v1/reconciler/report`、`POST /v1/reconciler/trigger`、`GET /v1/events?stream_key=order_drifts`

### 关键风险
- `/v1/monitor/snapshot` 不是完整聚合（关键值依赖 query）
- `/v1/reconciler/trigger` 目前非无参触发
- `/v1/strategies/running` 语义偏“loaded”
- 无 websocket/SSE，只能轮询

### 验收标准
- 全页具备 `loading/empty/error/stale/degraded`
- `blocked_reason` / KillSwitch / drift 状态不被弱化
- 危险写操作全部二次确认
- 不发明接口

## Phase B（P1）：Backtests + Reports + AI Lab

### 页面范围
- Backtests
- Reports
- AI Lab（Chat + Committee/HITL）

### API 依赖
- Backtests：`POST /v1/backtests`、`GET /v1/backtests/{run_id}`
- Reports（可复用最小能力）：`GET /v1/backtests/{run_id}`
- AI Lab：`/api/chat/sessions*`、`/api/portfolio-research/*`

### 阻塞点（缺失且阻塞）
- 回测任务列表与进度接口
- 标准化报告详情接口
- AI 审计查询接口
- 统一标注：`// TODO: BLOCKED BY BACKEND API`

### 验收标准
- 可创建回测、查询单任务状态
- 可展示最小报告（`status/metrics/artifact_ref`）
- AI 页面完整表达 `pending/approved/rejected`
- 缺失能力必须显式阻塞占位

## Phase C（P2）：Audit + Replay + Visual Polish

### 页面范围
- Audit
- Replay
- 全局视觉与状态一致性收敛

### API 依赖
- Audit（退化方案）：`GET /v1/events`
- Replay：`GET /v1/events`、`GET /v1/snapshots/latest`、`POST /v1/replay`

### 阻塞点（缺失且阻塞）
- `/api/audit/entries*` 审计专用接口缺失
- replay job/progress/result 查询缺失
- 快照历史列表缺失（只有 latest）
- 统一标注：`// TODO: BLOCKED BY BACKEND API`

### 验收标准
- 支持按 `trace_id/stream_key/event_type/time` 查询
- 支持触发 replay 并展示回写结果
- 视觉规范统一，且风险态优先于装饰性信息

---

## 7. Truth Gap 收敛计划（执行顺序）

### P0（立即修复）
1. Task 9.1：API 前缀与文档统一（`/v1` vs `/api/v1`）
2. Task 9.2：Monitor snapshot 真聚合化
3. Task 9.3：Reconciler trigger 无参触发

### P1（高优先）
4. Task 9.4：Backtests 列表与进度接口
5. Task 9.5：Reports 标准化详情接口
6. Task 9.6：Audit 专用查询接口
7. Task 9.7：Replay job 状态接口

### P2（一致性优化）
8. Task 9.8：`strategies/running` 语义澄清
9. Task 9.9：Chat/Research 参数风格统一（query -> body）
10. Task 9.10：stale/degraded 统一枚举
11. Task 9.11：快照历史查询接口

---

## 8. 质量门禁与 Definition of Done

### 8.1 质量门禁
- API Client 全量类型化（TS）+ 关键响应 Zod 校验
- 查询类接口统一 stale 策略与轮询间隔
- 危险写操作统一确认弹窗与失败回滚策略
- 错误结构归一（`APIError`：`code/message/details/request_id`）
- 页面必须覆盖状态：`loading/empty/error/stale/degraded`

### 8.2 DoD（每阶段）
- 页面范围完成并通过冒烟联调
- Truth Gap 阻塞项已在页面显式暴露，不伪造数据
- 关键操作具备审计可追溯（至少保存请求摘要与时间）
- 追踪器状态、计划文档、契约文档三方一致

---

## 9. 节奏与协作机制

### 9.1 周节奏
- Week 1：P0 Truth Gap 修复 + Phase A 框架搭建
- Week 2：P1 接口补齐 + Phase B 启动准备
- P2 项并行，不阻塞主线

### 9.2 协作机制
- 每日更新 `FRONTEND_PROJECT_TRACKER.md`
- 每次联调后更新“可用/阻塞/占位”三态
- `backend-api` 责任域接口变更必须触发契约文档同步

---

## 10. 当前结论：是否适合初始化前端工程

**YES（限定 Phase A）**

原因：
- P0 所需核心接口已存在，可先完成控制台骨架与关键监控页。
- Phase B/C 的阻塞主要是“功能完整性”阻塞，不是“工程启动”阻塞。
- 初始化时必须内置契约漂移防护（typed client + zod + truth gap 标注）。
