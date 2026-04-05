# Contract Realtime And Errors

## 1. 实时策略（当前）

当前控制面无前端 WebSocket/SSE 推送接口，默认采用轮询：
- 高频页（Monitor / Strategies / Reconcile）：`3-5s`
- 中频页（Backtests / AI Lab 列表）：`5-10s`
- 低频页（Audit / Replay 检索）：手动刷新 + 可选 `10-30s`

前端必须显式表达：
- `fresh`: 数据在阈值内
- `stale`: 超过阈值但可展示
- `degraded`: 服务/适配器降级
- `blocked`: 风险或熔断阻断

## 2. Stale/Degraded 推导规则

```typescript
interface DataFreshnessPolicy {
  staleAfterMs: number;
  hardExpireAfterMs: number;
}
```

建议默认：
- Monitor：`staleAfterMs=10000`
- Strategies：`staleAfterMs=15000`
- Reconcile：`staleAfterMs=30000`

degraded 来源：
- `AdapterHealthStatus.status` 为 `DEGRADED` 或 `DOWN`
- `/health/ready` 或 `/health/dependency` 返回 `status=degraded`

## 3. 错误模型（前端统一）

```typescript
interface APIError {
  code: string;         // 例如: "STRATEGY_NOT_FOUND"
  message: string;
  details?: unknown;
  request_id?: string;
}
```

错误映射：
- `400`: 参数错误或状态不满足，显示字段级提示
- `404`: 资源不存在，提示刷新或回到列表
- `409`: 并发/状态冲突，要求用户刷新后重试
- `422`: 语义校验失败，展示 validation 明细
- `500`: 服务端失败，记录 request context 并触发告警条

## 4. 危险操作失败回滚规范

- 对所有 `POST/PUT/DELETE` 默认禁用 optimistic success
- 如做 optimistic UI，失败后必须回滚并标记失败原因
- 失败时记录：
  - endpoint
  - payload hash
  - response status
  - timestamp

## 5. 与后端契约的 Truth Gaps

- 路径前缀混用：`/v1/*` 与 `/api/v1/*`
- `send_message` 的 `message` 当前为 query，而非 body
- 无审计专用 API、无 replay job API、无回测列表 API

```typescript
// TODO: BLOCKED BY BACKEND API
```

