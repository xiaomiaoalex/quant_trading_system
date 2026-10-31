# Contract Endpoints

## 1. Base

- Base URL: `http://localhost:8080`
- OpenAPI: `/openapi.json`
- Docs: `/docs`

## 2. Monitor

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/monitor/snapshot` | 系统监控快照 |
| GET | `/v1/monitor/alerts` | 活跃告警 |
| POST | `/v1/monitor/rules` | 新增/更新告警规则 |
| DELETE | `/v1/monitor/rules/{rule_name}` | 删除告警规则 |
| POST | `/v1/monitor/alerts/{rule_name}/clear` | 清除指定告警 |
| POST | `/v1/monitor/alerts/clear-all` | 清除全部告警 |

关键请求示例：
```typescript
interface AlertRuleRequest {
  rule_name: string;
  metric_key: string;
  threshold: number;
  comparison: "gt" | "lt" | "gte" | "lte" | "eq";
  severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  cooldown_seconds?: number;
}
```

## 3. Strategies

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/strategies/registry` | 策略注册列表 |
| POST | `/v1/strategies/registry` | 注册策略 |
| GET | `/v1/strategies/{id}/versions` | 版本列表 |
| POST | `/v1/strategies/{id}/versions` | 创建版本 |
| GET | `/v1/strategies/{id}/params` | 参数读取 |
| POST | `/v1/strategies/{id}/params` | 参数版本新增 |
| PUT | `/v1/strategies/{id}/params` | 运行时参数更新 |
| POST | `/v1/strategies/{id}/load` | 加载 |
| POST | `/v1/strategies/{id}/unload` | 卸载 |
| POST | `/v1/strategies/{id}/start` | 启动 |
| POST | `/v1/strategies/{id}/stop` | 停止 |
| POST | `/v1/strategies/{id}/pause` | 暂停 |
| POST | `/v1/strategies/{id}/resume` | 恢复 |
| GET | `/v1/strategies/{id}/status` | 单策略状态 |
| GET | `/v1/strategies/running` | 已加载策略列表 |

关键请求/响应示例：
```typescript
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
```

## 4. Reconcile / Events / Replay

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/reconciler/report` | 最近对账报告 |
| POST | `/v1/reconciler/trigger` | 手动触发对账 |
| GET | `/v1/events` | 事件查询 |
| GET | `/v1/snapshots/latest` | 最新快照 |
| POST | `/v1/replay` | 触发回放 |

关键请求示例：
```typescript
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
```

## 5. Backtests / Deployments / Risk / KillSwitch

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/backtests` | 创建回测任务 |
| GET | `/v1/backtests/{run_id}` | 查询回测 |
| GET | `/v1/deployments` | 部署列表 |
| POST | `/v1/deployments` | 创建部署 |
| POST | `/v1/deployments/{id}/start` | 启动部署 |
| POST | `/v1/deployments/{id}/stop` | 停止部署 |
| POST | `/v1/deployments/{id}/params` | 更新部署参数 |
| GET | `/v1/killswitch` | 读取熔断状态 |
| POST | `/v1/killswitch` | 设置熔断状态 |
| GET | `/v1/risk/limits` | 读取风险限制 |
| POST | `/v1/risk/limits` | 设置风险限制 |
| POST | `/v1/risk/events` | 风险事件上报 |
| POST | `/v1/risk/recover` | 风险恢复 |
| GET | `/v1/risk/crypto/runtime` | Crypto Risk runtime 状态 |
| PATCH | `/v1/risk/crypto/budget` | Crypto Risk 预算热更新 |
| POST | `/v1/risk/crypto/probe` | Crypto Risk 只读联通性检查 |
| GET | `/v1/risk/crypto/budget/audit` | Crypto Risk 预算审计 |
| GET | `/v1/events?stream_key=risk:crypto` | Crypto Risk 预算与 probe 审计流 |

Crypto Risk 关键请求/响应：
```typescript
interface CryptoRiskProbeRequest {
  symbols: string[];
  requested_by: string;
}

interface CryptoRiskProbeResponse {
  ok: boolean;
  read_only: true;
  mode: "demo" | "live" | "custom";
  execution_env: "demo" | "testnet";
  futures_base_url?: string | null;
  symbols: string[];
  requested_by: string;
  duration_ms: number;
  checks: Record<string, {
    status: "passed" | "failed";
    latency_ms: number;
    message: string;
    details: Record<string, unknown>;
  }>;
}
```

## 5.1 History Data（币安公开归档历史数据）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/data/history/jobs` | 创建历史归档导入任务（202） |
| GET | `/v1/data/history/jobs` | 查询导入任务（`status`/`symbol`/`month`/`limit`） |
| GET | `/v1/data/history/jobs/{job_id}` | 查询单个导入任务 |
| POST | `/v1/data/history/jobs/{job_id}/retry` | 重试 `FAILED` 任务（202） |
| GET | `/v1/data/history/datasets` | 数据集列表（`symbol`/`month`/`only_eligible`/`limit`） |
| GET | `/v1/data/history/datasets/{dataset_id}` | 数据集详情 |

请求体：

```typescript
interface HistoryJobCreateRequest {
  symbol: string          // BTCUSDT | ETHUSDT
  month: string           // YYYY-MM，必须是已结束的月份
  source?: 'binance_vision' // 固定值，省略即默认；提交其它值 → 422
  market?: 'spot'          // 固定值，省略即默认；提交其它值 → 422
  interval?: '1m'          // 固定值，省略即默认；提交其它值 → 422
  requested_by?: string
}
```

错误语义：400 请求非法（不支持的 symbol / 非法月份 / 月份未结束）· 409 状态冲突（重试非 FAILED 任务）·
503 PostgreSQL 不可用（不创建假任务）。

归档是否存在**不在提交阶段判定**（提交路径不做任何远程 HTTP）：提交正常返回 202，
任务在 worker 下载阶段判定，缺失时任务 `FAILED` 且 `error_code=ARCHIVE_NOT_FOUND`。

前端轮询约定：存在 `QUEUED` / `RUNNING` 任务时每 2 秒轮询一次，全部进入终态后停止轮询；
出现 `SUCCEEDED` 任务时刷新数据集缓存。

## 5.2 Research 数据集与事件回测

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/research/datasets` | Research 数据集目录（只读元数据；`symbol`/`month`/`official_only`/`limit`/`offset`） |
| GET | `/v1/research/strategies` | 已安装、允许提交回测的策略与参数 |
| POST | `/v1/research/backtests` | 提交事件回测（202） |
| GET | `/v1/research/backtests?limit=100` | 运行记录列表 |
| GET | `/v1/research/backtests/{run_id}` | 运行记录详情 |
| POST | `/v1/research/backtests/{run_id}/retry` | 重试 FAILED 运行（202；非 FAILED → 409，不存在 → 404） |
| POST | `/v1/research/backtests/{run_id}/cancel` | 取消运行（202） |

参数与前置条件：

- `GET /v1/research/datasets`：`limit` 默认 50、范围 1..200；`offset` 默认 0。
  返回数组，元素只含元数据（见 `contract_models.md` §5.2），不含行情载荷。
- 只有 `official_eligible === true` 的 Research 数据集允许提交回测；前端在
  `official_eligible=false` 或资格查询失败时禁用提交并展示阻断原因。
- 单次提交只允许一个 `dataset_ids` 元素（单币、单月）。

请求体：

```typescript
interface ResearchBacktestCreateRequest {
  manifest: {
    dataset_ids: string[]          // 恰好一个 ds_*
    strategy: { module_path: string; params?: Record<string, unknown> }
    initial_cash: string           // 十进制字符串，> 0
    fee_bps: string                // 0..100
    slippage_bps: string           // 0..1000
    engine: 'event_runtime'        // 固定值，其它值 → 422
    purpose: 'research_screening'  // 固定值
  }
}
```

错误语义：

- 422：manifest 非法（未知字段、非 event_runtime、未知 dataset、冻结 provenance 不一致等），
  `detail` 为字符串。
- 404：运行不存在；409：重试非 FAILED 运行。
- 503：Research store / application 不可用。

前端轮询约定：

- 运行列表在存在 `QUEUED` / `RUNNING` 时每 2 秒轮询；全部进入 `SUCCEEDED` / `FAILED` /
  `CANCELLED` 后停止轮询。
- 运行详情按 `run_id` 查询；选中运行的 `run_id` 写入 URL（`?run_id=`），刷新后按 `run_id`
  重新查询，正在运行或已完成的历史运行都能恢复。
- 报告界面展示数据集身份与月覆盖、来源与 SHA、质量、策略源码 hash、runtime 版本、成本、
  收益与回撤、成交与拒绝统计、限制说明。
- `engine` / `purpose` / `module_path` 由系统固定或来自后端已安装列表，前端不提供手填代码、
  不上传策略源码、不触发任何部署动作。

## 6. AI Lab

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/chat/sessions` | 创建会话 |
| GET | `/api/chat/sessions` | 会话列表 |
| GET | `/api/chat/sessions/{session_id}` | 会话详情 |
| POST | `/api/chat/sessions/{session_id}/messages` | 发送消息（`message` 当前为 query） |
| GET | `/api/chat/sessions/{session_id}/history` | 历史消息 |
| POST | `/api/chat/sessions/{session_id}/approve` | 审批 |
| POST | `/api/chat/sessions/{session_id}/reject` | 拒绝 |
| POST | `/api/portfolio-research/run` | 发起研究 |
| GET | `/api/portfolio-research/runs` | 研究列表 |
| GET | `/api/portfolio-research/runs/{run_id}` | 研究详情 |
| POST | `/api/portfolio-research/runs/{run_id}/submit` | 提交审批 |
| POST | `/api/portfolio-research/runs/{run_id}/approve` | 审批通过 |
| POST | `/api/portfolio-research/runs/{run_id}/reject` | 审批拒绝 |

## 7. 已知契约缺口（统一标注）

统一现货入口：`GET /v1/auth/me` 返回 `{ actor: string, csrf_token: string }`，
会话检查成功后恢复写请求的内存 CSRF token，失败清除旧 token；响应禁止缓存。
开发免登录模式 `csrf_token=""`。登录 Cookie 为 HttpOnly，前端不读取 Cookie。
本地连接统一 Runtime 时，启动 Vite 前显式设置
`QTS_CONTROL_API_TARGET=http://127.0.0.1:8090`；默认仍为旧控制面 8080。

- 缺少回测列表 API
- 缺少标准化报告详情 API
- 缺少审计专用 API（`/api/audit/*`）
- 缺少 replay job 状态 API

```typescript
// TODO: BLOCKED BY BACKEND API
```
