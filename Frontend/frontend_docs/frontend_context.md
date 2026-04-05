# Frontend Context — quant_trading_system

## 1. 控制台的真实目标（不是营销站）

本前端是 **交易控制台（Control Console）**，核心目标是把“系统真实状态”和“可执行治理动作”透明呈现给操作者，而不是展示品牌或增长漏斗。

前端必须服务以下闭环：
- 监控系统健康与风险状态（Monitor / Health / KillSwitch）
- 呈现策略运行态与阻断原因（Strategies）
- 呈现并处理订单对账漂移（Reconcile）
- 呈现 AI proposal / HITL / 审批流（AI Lab）
- 呈现回测任务状态与结果引用（Backtests / Reports）
- 支撑审计追溯与回放入口（Audit / Replay）

不在当前优先范围：
- 营销落地页
- 与真实控制面无关的视觉展示页面
- 弱化风险语义的“只看收益”面板

## 2. 页面优先级（基于当前后端能力）

### P0（第一阶段必须可用）
- Monitor
- Strategies
- Reconcile

### P1（第二阶段）
- Backtests
- Reports
- AI Lab

### P2（第三阶段）
- Audit
- Replay

## 3. 必须完整表达的状态语义

### UI 通用态（前端职责）
- `loading`
- `empty`
- `error`

### 控制面关键态（后端语义 + 前端映射）
- `stale`
  - 后端无统一 `stale` 字段，前端需基于 `timestamp` / `last_heartbeat_ts_ms` 推导。
- `degraded`
  - 来源：`AdapterHealthStatus.status=DEGRADED|DOWN`、`/health/*` 返回 `degraded`。
- `blocked`
  - 来源：策略状态里的 `blocked_reason`（KillSwitch 或限频等）。
- `killed / halted`
  - 来源：KillSwitch `level >= 2`（L2 close-only / L3 full stop）。
- `reconciling / drifted`
  - 来源：Reconcile drifts（`GHOST/PHANTOM/DIVERGED`）和 `grace_period_remaining_sec`。
- `approved / pending / rejected`
  - 来源：Chat Session 状态、HITL 决策、Committee run / proposal 状态。

## 4. 危险操作确认规则（必须二次确认）

以下操作必须至少二次确认（确认弹窗 + 明确影响范围）：
- 所有 `POST/PUT/DELETE` 写操作
- 策略控制：`load/unload/start/stop/pause/resume`
- 策略参数变更：`POST/PUT /v1/strategies/{id}/params`
- KillSwitch 变更：`POST /v1/killswitch`
- 风险事件与恢复：`POST /v1/risk/events`、`POST /v1/risk/recover`
- 手动对账触发：`POST /v1/reconciler/trigger`
- 回放触发：`POST /v1/replay`
- 部署启停与参数变更：`/v1/deployments/*`
- AI/HITL 审批动作：`/api/chat/*approve|reject`、`/api/portfolio-research/*submit|approve|reject`

## 5. 前端不能弱化的系统边界

- AI 不能直接下单（AI-clean 边界必须可见）
- Policy 是硬约束，不是建议（拒绝/缩仓/阻断必须明显表达）
- Reconcile drift 是一级控制状态，不可降级为日志细节
- Fail-Closed 是默认行为，异常时前端不能“假设系统正常”
- 审计/回放是控制台核心能力，不是附属页

## 6. Truth Gaps（文档与代码不一致）

- 路径前缀不一致：文档大量写 `/api/v1/*`，代码多数是 `/v1/*`（chat 与 portfolio research 是 `/api/*`）。
- 策略手册中的 `/api/v1/strategies/{id}/metrics`、`/api/v1/strategies/{id}/backtest`、`/api/v1/deployments/{id}/hotswap|rollback` 在当前路由中不存在。
- AI workflow 文档中的 `/api/audit/entries*` 路由在当前后端不存在。
- `GET /v1/monitor/snapshot` 不是完整后端聚合快照：多个关键值来自 query 参数（默认值为 0）。
- 当前无控制面 websocket/SSE，前端需轮询。

