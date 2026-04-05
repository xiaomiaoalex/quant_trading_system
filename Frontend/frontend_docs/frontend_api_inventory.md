# Frontend API Inventory

说明：
- 状态标签仅使用：`已存在` / `可复用` / `缺失但可占位` / `缺失且阻塞`
- 只列当前代码中真实存在的接口；对缺失能力统一标注阻塞注释
- 当前控制面无 websocket/SSE，默认轮询

---

## 页面：Monitor（P0）

| API | 方法 | 状态 | 用途 | 刷新模式 |
|---|---|---|---|---|
| `/v1/monitor/snapshot` | GET | 已存在 | 监控快照主数据 | 轮询 |
| `/v1/monitor/alerts` | GET | 已存在 | 活跃告警列表 | 轮询 |
| `/v1/monitor/rules` | POST | 已存在 | 新增/更新告警规则（危险操作） | 按需 |
| `/v1/monitor/rules/{rule_name}` | DELETE | 已存在 | 删除告警规则（危险操作） | 按需 |
| `/v1/monitor/alerts/{rule_name}/clear` | POST | 已存在 | 清除单条告警（危险操作） | 按需 |
| `/v1/monitor/alerts/clear-all` | POST | 已存在 | 清除全部告警（危险操作） | 按需 |
| `/health/ready` | 可复用 | 可复用 | 服务 readiness / degraded 补充 | 轮询 |
| `/health/dependency` | 可复用 | 可复用 | 依赖健康状态补充 | 轮询 |
| `/v1/killswitch` | GET | 可复用 | 展示全局熔断状态 | 轮询 |

关键字段与状态：
- `MonitorSnapshot.timestamp`
- `killswitch_level`（0-3）
- `adapters[*].status`（HEALTHY/DEGRADED/DOWN）
- `adapters[*].last_heartbeat_ts_ms`
- `active_alerts[]`

stale / degraded 语义：
- `degraded`：来自 `adapters.status` 与 `/health/*` 状态
- `stale`：后端无统一字段，前端按 `timestamp` 和 `last_heartbeat_ts_ms` 推导

Truth Gaps：
- `GET /v1/monitor/snapshot` 的 `open_orders_count/daily_pnl/...` 多数来自 query 入参，不是完整后端实时聚合
- 缺少“统一系统健康度枚举”字段（当前是 string 语义）

请求/响应示例（关键）：
```typescript
// GET /v1/monitor/snapshot
interface MonitorSnapshotResponse {
  timestamp: string;
  open_orders_count: number;
  pending_orders_count: number;
  daily_pnl: string;
  killswitch_level: number;
  adapters: Record<string, {
    adapter_name: string;
    status: "HEALTHY" | "DEGRADED" | "DOWN";
    last_heartbeat_ts_ms?: number;
  }>;
}

// POST /v1/monitor/rules
interface AlertRuleRequest {
  rule_name: string;
  metric_key: string;
  threshold: number;
  comparison: "gt" | "lt" | "gte" | "lte" | "eq";
  severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  cooldown_seconds?: number;
}
```

缺失能力：
- 统一监控聚合快照（无需 query 拼装）
  - `// TODO: BLOCKED BY BACKEND API`

---

## 页面：Strategies（P0）

| API | 方法 | 状态 | 用途 | 刷新模式 |
|---|---|---|---|---|
| `/v1/strategies/registry` | GET | 已存在 | 策略注册列表 | 轮询 |
| `/v1/strategies/registry` | POST | 已存在 | 注册策略（危险操作） | 按需 |
| `/v1/strategies/registry/{strategy_id}` | GET | 已存在 | 策略元数据详情 | 轮询 |
| `/v1/strategies/{strategy_id}/versions` | GET | 已存在 | 版本列表 | 轮询 |
| `/v1/strategies/{strategy_id}/versions` | POST | 已存在 | 创建版本（危险操作） | 按需 |
| `/v1/strategies/{strategy_id}/versions/{version}` | GET | 已存在 | 版本详情 | 轮询 |
| `/v1/strategies/{strategy_id}/params` | GET | 已存在 | 参数版本查询 | 轮询 |
| `/v1/strategies/{strategy_id}/params` | POST | 已存在 | 参数新版本（危险操作） | 按需 |
| `/v1/strategies/{strategy_id}/params` | PUT | 已存在 | 运行时参数更新（危险操作） | 按需 |
| `/v1/strategies/{strategy_id}/load` | POST | 已存在 | 加载策略（危险操作） | 按需 |
| `/v1/strategies/{strategy_id}/unload` | POST | 已存在 | 卸载策略（危险操作） | 按需 |
| `/v1/strategies/{strategy_id}/start` | POST | 已存在 | 启动策略（危险操作） | 按需 |
| `/v1/strategies/{strategy_id}/stop` | POST | 已存在 | 停止策略（危险操作） | 按需 |
| `/v1/strategies/{strategy_id}/pause` | POST | 已存在 | 暂停策略（危险操作） | 按需 |
| `/v1/strategies/{strategy_id}/resume` | POST | 已存在 | 恢复策略（危险操作） | 按需 |
| `/v1/strategies/{strategy_id}/status` | GET | 已存在 | 单策略运行态 | 轮询 |
| `/v1/strategies/running` | GET | 已存在 | 已加载策略列表 | 轮询 |
| `/v1/killswitch` | GET | 可复用 | 关联全局阻断态 | 轮询 |

关键字段与状态：
- `StrategyStatusResponse.status`：`IDLE/LOADED/RUNNING/PAUSED/STOPPED/ERROR`
- `blocked_reason`
- `tick_count/signal_count/error_count/last_error`
- `config`

stale / degraded 语义：
- 策略接口本身无 `stale/degraded` 字段；需联动 Monitor/Health 衍生显示

Truth Gaps：
- `/v1/strategies/running` 实际返回“已加载策略”，不严格等于 RUNNING
- 文档中的 `/api/v1/strategies/{id}/metrics`、`/api/v1/strategies/{id}/backtest` 当前路由不存在

请求/响应示例（关键）：
```typescript
// PUT /v1/strategies/{strategy_id}/params
interface UpdateStrategyParamsRequest {
  config: Record<string, unknown>;
  validate_only?: boolean;
}

interface UpdateStrategyParamsResponse {
  success: boolean;
  strategy_id: string;
  updated_config?: Record<string, unknown>;
  validation_result?: {
    status: string;
    errors: Array<{ field: string; message: string; code: string }>;
    warnings: string[];
  };
  error?: string;
}

// POST /v1/strategies/{strategy_id}/load
interface LoadStrategyRequest {
  module_path: string;
  version?: string;
  config?: Record<string, unknown>;
  max_position_size?: number;
  max_daily_loss?: number;
  max_orders_per_minute?: number;
  timeout_seconds?: number;
}
```

缺失能力：
- 策略运行指标 API（PnL/Sharpe/drawdown 等）
  - `// TODO: BLOCKED BY BACKEND API`
- 策略级回测触发 API（按策略入口）
  - `// TODO: BLOCKED BY BACKEND API`

---

## 页面：Reconcile（P0）

| API | 方法 | 状态 | 用途 | 刷新模式 |
|---|---|---|---|---|
| `/v1/reconciler/report` | GET | 已存在 | 最近对账报告 | 轮询 |
| `/v1/reconciler/trigger` | POST | 已存在 | 手动触发对账（危险操作） | 按需 |
| `/v1/events?stream_key=order_drifts` | GET | 可复用 | 漂移事件时间线补充 | 轮询 |

关键字段与状态：
- `drifts[].drift_type`：`GHOST/PHANTOM/DIVERGED`
- `drifts[].grace_period_remaining_sec`
- `ghost_count/phantom_count/diverged_count/within_grace_period_count`

stale / degraded / drift 语义：
- `reconciling`：`within_grace_period_count > 0`
- `drifted`：`ghost_count + phantom_count + diverged_count > 0` 且超宽限
- `stale`：报告 `timestamp` 老化超阈值（前端规则）

Truth Gaps：
- `POST /v1/reconciler/trigger` 需要前端提交 `local_orders + exchange_orders`，不是“无参触发”

请求/响应示例（关键）：
```typescript
// POST /v1/reconciler/trigger
interface TriggerReconcileRequest {
  local_orders: Array<{
    client_order_id: string;
    status: string;
    symbol?: string;
    quantity?: string | number;
    filled_quantity?: string | number;
    created_at?: string;
    updated_at?: string;
  }>;
  exchange_orders: Array<{
    client_order_id: string;
    status: string;
    symbol?: string;
    quantity?: string | number;
    filled_quantity?: string | number;
    updated_at?: string;
  }>;
}

interface ReconcileReportResponse {
  timestamp: string;
  total_orders_checked: number;
  ghost_count: number;
  phantom_count: number;
  diverged_count: number;
  within_grace_period_count: number;
  drifts: Array<{
    cl_ord_id: string;
    drift_type: "GHOST" | "PHANTOM" | "DIVERGED";
    grace_period_remaining_sec?: number;
  }>;
}
```

缺失能力：
- 无参触发周期对账（由后端自行拉取本地+交易所快照）
  - `// TODO: BLOCKED BY BACKEND API`
- 漂移确认/处置动作 API（ack/resolve）
  - `// TODO: BLOCKED BY BACKEND API`

---

## 页面：Backtests（P1）

| API | 方法 | 状态 | 用途 | 刷新模式 |
|---|---|---|---|---|
| `/v1/backtests` | POST | 已存在 | 创建回测任务（危险操作） | 按需 |
| `/v1/backtests/{run_id}` | GET | 已存在 | 查询单个回测运行状态/结果 | 轮询 |

关键字段与状态：
- `run_id`
- `status`（当前示例：RUNNING/COMPLETED）
- `metrics`
- `artifact_ref`

Truth Gaps：
- 无列表接口，无法直接构建“回测任务列表页”
- 无任务进度流 / 事件流接口

请求/响应示例（关键）：
```typescript
// POST /v1/backtests
interface CreateBacktestRequest {
  strategy_id: string;
  version: number;
  params?: Record<string, unknown>;
  symbols: string[];
  start_ts_ms: number;
  end_ts_ms: number;
  venue: string;
  requested_by: string;
}

interface BacktestRunResponse {
  run_id: string;
  status: string;
  strategy_id: string;
  version: number;
  symbols: string[];
  metrics?: Record<string, unknown>;
  artifact_ref?: string;
}
```

缺失能力：
- 回测任务列表 API
  - `// TODO: BLOCKED BY BACKEND API`
- 回测任务进度/状态流 API
  - `// TODO: BLOCKED BY BACKEND API`

---

## 页面：Reports（P1）

| API | 方法 | 状态 | 用途 | 刷新模式 |
|---|---|---|---|---|
| `/v1/backtests/{run_id}` | GET | 可复用 | 读取 `metrics` 与 `artifact_ref` 的最小报告信息 | 轮询 |

Truth Gaps：
- 后端已有 `report_formatter` 等能力，但无公开 report 查询路由

缺失能力：
- 标准化报告详情 API（returns/risk/trades/equity_curve 分段）
  - `// TODO: BLOCKED BY BACKEND API`
- 报告列表与过滤 API
  - `// TODO: BLOCKED BY BACKEND API`

---

## 页面：AI Lab（P1）

| API | 方法 | 状态 | 用途 | 刷新模式 |
|---|---|---|---|---|
| `/api/chat/sessions` | POST | 已存在 | 创建会话（危险操作） | 按需 |
| `/api/chat/sessions` | GET | 已存在 | 会话列表 | 轮询 |
| `/api/chat/sessions/{session_id}` | GET | 已存在 | 会话详情 | 轮询 |
| `/api/chat/sessions/{session_id}` | DELETE | 已存在 | 删除会话（危险操作） | 按需 |
| `/api/chat/sessions/{session_id}/messages` | POST | 已存在 | 发送消息（危险操作） | 按需 |
| `/api/chat/sessions/{session_id}/history` | GET | 已存在 | 会话历史 | 轮询 |
| `/api/chat/sessions/{session_id}/approve` | POST | 已存在 | 审批并注册（危险操作） | 按需 |
| `/api/chat/sessions/{session_id}/reject` | POST | 已存在 | 拒绝（危险操作） | 按需 |
| `/api/portfolio-research/run` | POST | 已存在 | 运行 committee workflow（危险操作） | 按需 |
| `/api/portfolio-research/runs` | GET | 已存在 | run 列表 | 轮询 |
| `/api/portfolio-research/runs/{run_id}` | GET | 已存在 | run 详情 | 轮询 |
| `/api/portfolio-research/runs/{run_id}/submit` | POST | 已存在 | 提交审批（危险操作） | 按需 |
| `/api/portfolio-research/runs/{run_id}/approve` | POST | 已存在 | 审批通过（危险操作） | 按需 |
| `/api/portfolio-research/runs/{run_id}/reject` | POST | 已存在 | 审批拒绝（危险操作） | 按需 |

关键字段与状态：
- Chat Session：`active/waiting_approval/approved/rejected/completed/expired`
- CommitteeRun：`pending/running/completed/failed/cancelled`
- ProposalStatus：`pending/in_review/passed/rejected/approved/archived`

Truth Gaps：
- `send_message` 的 `message` 来自 query 参数，不是 JSON body
- `approve/reject` 多为 query 参数（chat 与 portfolio research）

请求/响应示例（关键）：
```typescript
// POST /api/chat/sessions
interface CreateSessionRequest {
  initial_message?: string;
  risk_level?: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
}

// POST /api/chat/sessions/{session_id}/messages?message=...
// 注意：message 当前是 query 参数，不是 body

interface SessionResponse {
  session_id: string;
  status: "active" | "waiting_approval" | "approved" | "rejected" | "completed" | "expired";
  message_count: number;
  has_strategy: boolean;
}
```

缺失能力：
- AI 审计日志查询 API（按 strategy/status/time）
  - `// TODO: BLOCKED BY BACKEND API`

---

## 页面：Audit（P2）

| API | 方法 | 状态 | 用途 | 刷新模式 |
|---|---|---|---|---|
| `/v1/events` | GET | 可复用 | 事件级审计时间线（通用） | 轮询 |

Truth Gaps：
- 文档提到 `/api/audit/entries*`，当前无对应路由
- `AIAuditLog` 存在领域服务，但无前端可用 API

缺失能力：
- 审计条目列表/详情/过滤 API
  - `// TODO: BLOCKED BY BACKEND API`
- HITL 审批历史查询 API
  - `// TODO: BLOCKED BY BACKEND API`

---

## 页面：Replay（P2）

| API | 方法 | 状态 | 用途 | 刷新模式 |
|---|---|---|---|---|
| `/v1/events` | GET | 已存在 | 查询事件流 | 轮询 |
| `/v1/snapshots/latest` | GET | 已存在 | 查询最新快照 | 轮询 |
| `/v1/replay` | POST | 已存在 | 触发 replay（危险操作） | 按需 |

关键字段与状态：
- `EventEnvelope`: `stream_key/event_type/trace_id/ts_ms/payload`
- `SnapshotEnvelope`: `stream_key/snapshot_type/ts_ms/payload`

Truth Gaps：
- replay 触发后仅返回 `ActionResult`，无 job/progress/result 查询
- 无历史快照列表 API（仅 latest）

缺失能力：
- replay 任务状态查询 API
  - `// TODO: BLOCKED BY BACKEND API`
- 快照历史列表 API
  - `// TODO: BLOCKED BY BACKEND API`

---

## 全局 Truth Gaps Summary

- 文档路径与代码路径存在偏差：`/api/v1/*` vs `/v1/*`
- 部分文档宣称接口未在代码实现（metrics/backtest/hotswap/rollback/audit）
- 控制面无 push 通道（websocket/SSE），前端需轮询
- stale 语义无统一字段，需要前端规则化推导
