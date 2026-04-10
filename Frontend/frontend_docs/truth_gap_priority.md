# Truth Gap 修复优先级清单（Backend）

目标：按“对前端控制台阻塞程度”排序，优先打通 P0/P1 页面闭环。

---

## P0（立即修复，直接阻塞控制台核心闭环）

### 1) Task 9.1 — 统一 API 前缀与文档路径
- Gap：文档中存在 `/api/v1/*` 与代码 `/v1/*` 混用。
- 影响：前端 client 生成、联调和错误排查成本高。
- 建议修复：
  - 明确 canonical 前缀（建议保留现状：控制面 `/v1/*`，AI 域 `/api/*`）。
  - 同步修订所有对外文档与示例。

### 2) Task 9.2 — Monitor Snapshot 真聚合化
- Gap：`GET /v1/monitor/snapshot` 多个关键指标来自 query 参数。
- 影响：Monitor 页关键卡片无法反映真实系统状态。
- 建议修复：
  - 后端内部聚合 `orders/pnl/killswitch/adapters/alerts`，去除 query 注入依赖。
  - 返回统一 `snapshot_source`/`freshness` 元信息（可选）。

### 3) Task 9.3 — Reconciler Trigger 无参触发能力
- Gap：`POST /v1/reconciler/trigger` 需要前端提交 `local_orders + exchange_orders`。
- 影响：Reconcile 页难以提供真实“手动触发对账”操作。
- 建议修复：
  - 新增无参触发模式：后端自行拉取本地与交易所快照。
  - 保留现有“带 payload”模式用于测试。

---

## P1（高优先，阻塞 Phase B 功能完整性）

### 4) Task 9.4 — Backtests 列表与进度接口
- Gap：仅有 `POST /v1/backtests` 与 `GET /v1/backtests/{run_id}`。
- 影响：Backtests 页面无法做任务列表、筛选、历史追踪。
- 建议修复：
  - 新增 `GET /v1/backtests`（支持 status/time/strategy filter）。
  - 新增进度字段（`progress`, `started_at`, `finished_at`, `error`）。

### 5) Task 9.5 — Reports 详情接口
- Gap：无标准化 report detail API（仅 `metrics` + `artifact_ref`）。
- 影响：Reports 页面无法展示 returns/risk/trades/equity 曲线。
- 建议修复：
  - 新增 `GET /v1/reports/{run_id}`（或 `GET /v1/backtests/{run_id}/report`）。
  - 统一返回结构与字段稳定性（避免前端二次猜测）。

### 6) Task 9.6 — Audit 专用查询接口
- Gap：文档存在 `/api/audit/entries*`，代码未实现。
- 影响：Audit 页只能退化为通用事件流，无法做 AI/HITL 审计视图。
- 建议修复：
  - 新增 `GET /api/audit/entries`、`GET /api/audit/entries/{id}`。
  - 支持 `strategy_id/status/time_range/approver` 过滤。

### 7) Task 9.7 — Replay 任务状态接口
- Gap：`POST /v1/replay` 仅返回 `ActionResult`，无 job 状态。
- 影响：Replay 页无法展示执行中/完成/失败过程。
- 建议修复：
  - `POST /v1/replay` 返回 `job_id`。
  - 新增 `GET /v1/replay/{job_id}` 查询状态与结果摘要。

---

## P2（中优先，影响一致性与可维护性）

### 8) Task 9.8 — `strategies/running` 语义澄清
- Gap：当前接口实际返回“loaded strategies”。
- 影响：前端运行态统计容易误导。
- 建议修复：
  - 方案A：改名为 `/v1/strategies/loaded`（推荐）。
  - 方案B：保留旧路由但新增参数 `status=RUNNING` 精确过滤。

### 9) Task 9.9 — Chat / Research 参数风格统一
- Gap：部分写接口关键参数通过 query 传递（如 `send_message`）。
- 影响：前端调用器、审计日志和网关规则处理复杂。
- 建议修复：
  - 改为 JSON body 入参（保留 query 兼容窗口期）。

### 10) Task 9.10 — Stale/Degraded 统一枚举
- Gap：stale 主要依赖前端推导，degraded 多来源字符串语义。
- 影响：跨页状态表达不一致。
- 建议修复：
  - 新增统一健康字段：`health_state: healthy|degraded|stale|down`。
  - 提供 `generated_at` 与 `ttl_ms`。

### 11) Task 9.11 — 快照历史查询接口
- Gap：只有 `/v1/snapshots/latest`。
- 影响：Replay/Audit 不能按时间回看快照演进。
- 建议修复：
  - 新增 `GET /v1/snapshots`（stream_key + time range + limit）。

---

## 建议执行顺序（两周窗口）

1. Week 1：Task 9.1/9.2/9.3 + Task 9.8（语义澄清）
2. Week 2：Task 9.4/9.5/9.6/9.7
3. 并行：Task 9.9/9.10/9.11（不阻塞主线）

---

## 前后端联调验收（最小集合）

- Monitor：不传 query 也能返回真实关键指标。
- Reconcile：一键无参触发 + 报告可轮询。
- Backtests：可列表、可筛选、可追踪状态。
- Reports：可读标准化详情。
- Audit：可按策略/状态/时间检索 AI 审计条目。
- Replay：可创建 job 并追踪到完成状态。
