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

- 缺少回测列表 API
- 缺少标准化报告详情 API
- 缺少审计专用 API（`/api/audit/*`）
- 缺少 replay job 状态 API

```typescript
// TODO: BLOCKED BY BACKEND API
```

