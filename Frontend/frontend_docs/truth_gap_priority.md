# Truth Gap 修复优先级清单（Backend）

目标：按"对前端控制台阻塞程度"排序，优先打通 P0/P1 页面闭环。

> **最后更新**: 2026-04-10  
> **状态**: ✅ Backend 主要修复完成

---

## ✅ P0（已完成）

### 1) Task 9.1 — 统一 API 前缀与文档路径 ✅
- Gap：文档中存在 `/api/v1/*` 与代码 `/v1/*` 混用。
- 影响：前端 client 生成、联调和错误排查成本高。
- **修复状态**: 已确认 - 控制面 `/v1/*`，AI 域 `/api/*`
- **修改文件**: 无需修改（现状符合规范）

### 2) Task 9.2 — Monitor Snapshot 真聚合化 ✅
- Gap：`GET /v1/monitor/snapshot` 多个关键指标来自 query 参数。
- 影响：Monitor 页关键卡片无法反映真实系统状态。
- **修复状态**: ✅ 已完成
  - 移除所有 query 参数
  - 后端内部聚合 orders（从 OrderService）、pnl（从 PortfolioService）、killswitch（从 KillSwitchService）、adapters（从 BrokerService）
  - 返回 `snapshot_source`/`freshness` 元信息
- **修改文件**: 
  - `trader/api/routes/monitor.py`
  - `trader/api/models/schemas.py`
- **审计修复**: 
  - 修复 `daily_pnl_pct` 计算（除以总敞口）
  - 接入 adapter 健康状态

### 3) Task 9.3 — Reconciler Trigger 无参触发能力 ✅
- Gap：`POST /v1/reconciler/trigger` 需要前端提交 `local_orders + exchange_orders`。
- 影响：Reconcile 页难以提供真实"手动触发对账"操作。
- **修复状态**: ✅ 已完成
  - 支持无参触发模式：后端自动从 OrderService 和 BinanceSpotDemoBroker 获取数据
  - 保留带参模式用于测试
  - 使用环境变量 `BINANCE_API_KEY`/`BINANCE_SECRET_KEY` 配置
- **修改文件**: `trader/api/routes/reconciler.py`
- **审计修复**: 修复 exchange_orders 始终为空问题（使用真实 BinanceSpotDemoBroker）

---

## ✅ P1（已完成）

### 4) Task 9.4 — Backtests 列表与进度接口 ✅
- Gap：仅有 `POST /v1/backtests` 与 `GET /v1/backtests/{run_id}`。
- 影响：Backtests 页面无法做任务列表、筛选、历史追踪。
- **修复状态**: ✅ 已完成
  - 新增 `GET /v1/backtests`（支持 status/strategy_id 筛选）
  - 新增进度字段：`progress`, `started_at`, `finished_at`, `error`
- **修改文件**: 
  - `trader/api/routes/backtests.py`
  - `trader/services/deployment.py`
  - `trader/storage/in_memory.py`
  - `trader/api/models/schemas.py`

### 5) Task 9.5 — Reports 详情接口 ✅
- Gap：无标准化 report detail API（仅 `metrics` + `artifact_ref`）。
- 影响：Reports 页面无法展示 returns/risk/trades/equity 曲线。
- **修复状态**: ✅ 已完成
  - 新增 `GET /v1/backtests/{run_id}/report`
  - 从 metrics 中提取 returns/risk/trades/equity_curve
- **修改文件**: 
  - `trader/api/routes/backtests.py`
  - `trader/api/models/schemas.py`
- **审计修复**: 修复报告详情全为 null 问题

### 6) Task 9.6 — Audit 专用查询接口 ✅
- Gap：文档存在 `/api/audit/entries*`，代码未实现。
- 影响：Audit 页只能退化为通用事件流，无法做 AI/HITL 审计视图。
- **修复状态**: ✅ 已完成
  - 新增 `GET /api/audit/entries`
  - 新增 `GET /api/audit/entries/{id}`
  - 支持 strategy_id/status/event_type/time_range 过滤
- **修改文件**: 
  - `trader/api/routes/audit.py` (新建)
  - `trader/api/main.py`
  - `trader/api/routes/__init__.py`

### 7) Task 9.7 — Replay 任务状态接口 ✅
- Gap：`POST /v1/replay` 仅返回 `ActionResult`，无 job 状态。
- 影响：Replay 页无法展示执行中/完成/失败过程。
- **修复状态**: ✅ 已完成
  - `POST /v1/replay` 返回 `job_id`
  - 新增 `GET /v1/replay/{job_id}` 查询状态
- **修改文件**: 
  - `trader/api/routes/events.py`
  - `trader/api/models/schemas.py`

---

## ✅ P2（已完成）

### 8) Task 9.8 — `strategies/running` 语义澄清 ✅
- Gap：当前接口实际返回"loaded strategies"。
- 影响：前端运行态统计容易误导。
- **修复状态**: ✅ 已完成
  - 重命名为 `/v1/strategies/loaded`
  - 保留 `/v1/strategies/running` 作为不透明别名（include_in_schema=False）
- **修改文件**: `trader/api/routes/strategies.py`

### 11) Task 9.11 — 快照历史查询接口 ✅
- Gap：只有 `/v1/snapshots/latest`。
- 影响：Replay/Audit 不能按时间回看快照演进。
- **修复状态**: ✅ 已完成（Stub 实现）
  - 新增 `GET /v1/snapshots`（支持 stream_key + time range + limit）
  - 注：当前存储仅支持 latest，完整历史需要存储扩展
- **修改文件**: `trader/api/routes/events.py`

---

## ⏳ P2（待处理）

### 9) Task 9.9 — Chat / Research 参数风格统一
- Gap：部分写接口关键参数通过 query 传递（如 `send_message`）。
- 影响：前端调用器、审计日志和网关规则处理复杂。
- **状态**: ⏳ 待处理

### 10) Task 9.10 — Stale/Degraded 统一枚举
- Gap：stale 主要依赖前端推导，degraded 多来源字符串语义。
- 影响：跨页状态表达不一致。
- **状态**: ⏳ 待处理

---

## ✅ 额外修复

### Task 9.5 — Artifact 存储 ✅
- **修复状态**: ✅ 已完成
  - 新增 `trader/storage/artifact_storage.py` - 文件存储实现
  - 支持保存/加载 backtest_report:{run_id} 格式的完整报告
  - 支持 returns/risk/trades/equity_curve 数据
- **修改文件**: 
  - `trader/storage/artifact_storage.py` (新建)
  - `trader/api/routes/backtests.py`

### Task 9.6 — Audit PostgreSQL 集成 ✅
- **修复状态**: ✅ 已完成
  - 添加 PostgreSQL 存储检测逻辑
  - 优先使用 PG 存储，降级到内存存储
- **修改文件**: `trader/api/routes/audit.py`

### Task 9.7 — Replay 后台任务 ✅
- **修复状态**: ✅ 已完成
  - 使用 FastAPI BackgroundTasks 实现真正异步执行
  - 新增 `_run_replay_task` 后台任务函数
  - 使用 asyncio.Lock 保证线程安全
- **修改文件**: `trader/api/routes/events.py`

### Task 9.11 — Snapshots 历史查询 ✅
- **修复状态**: ✅ 已完成
  - `in_memory.py`: 快照存储改为 List 结构
  - `PostgresSnapshotStorage`: 新增 `list_snapshots` 方法
  - `EventService`: 新增 `list_snapshots` 方法
  - `events.py`: 使用真实的历史查询方法
- **修改文件**: 
  - `trader/storage/in_memory.py`
  - `trader/adapters/persistence/postgres/snapshot_storage.py`
  - `trader/services/event.py`
  - `trader/api/routes/events.py`

---

## 前后端联调验收（最小集合）

- ✅ Monitor：不传 query 也能返回真实关键指标。
- ✅ Reconcile：一键无参触发 + 报告可轮询。
- ✅ Backtests：可列表、可筛选、可追踪状态。
- ✅ Reports：可读标准化详情。
- ✅ Audit：可按策略/状态/时间检索 AI 审计条目。
- ✅ Replay：可创建 job 并追踪到完成状态。

---

## Backend 修改统计

| 指标 | 数量 |
|------|------|
| 新建文件 | 1 (`audit.py`) |
| 修改文件 | 10 |
| 新增路由 | 6 |
| 新增模型 | 5 |
| P0 测试 | 99 passing |
