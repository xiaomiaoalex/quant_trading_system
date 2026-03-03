# quant_trading_system v3.0.8-个人开发者版 技术规范
> 角色：个人开发者 / 架构师（AI 辅助研发）  
> 版本：v3.0.8（基于 v3.0.7 修订）  
> 核心战略：轻量核心，AI 友好，多交易所扩展，不重复造轮子，细节落地  
> 核心原则：五平面隔离进化（Core / Adapter / Persistence / Policy / Insight）保持不变  
> 关键裁定：AI 只做大脑与治理，不触碰 Core 确定性执行路径（Core AI-clean）  
> 本次修订重点：PostgreSQL-First、自研 Binance Adapter 主线、重启后幂等 CI 门禁、实现状态三态跟踪

---

## 0) v3.0.5 的五个硬承诺（Non-Negotiable）
1) **确定性**：OMS 状态机 Rank 单调、不回滚；终态不可逆  
2) **幂等性**：成交/回报严格去重；重复事件不重复记账与重复副作用  
3) **可回放**：Event Sourcing + Snapshot，可快速重建状态  
4) **Fail-Closed**：不确定即降级/熔断；控制面不可达优先本地锁死  
5) **隔离进化**：AI 仅在 Policy/Insight/Control 侧输出洞察/提案；Core 不运行任何推理

### 0.1 规范状态标记（强制）
后续章节统一使用以下三态标记，避免“现状与目标态混写”：
1. `As-Is`：当前仓库已实现并可运行的能力  
2. `In-Progress`：已立项、正在开发中的能力  
3. `Target`：目标能力（未实现或仅部分实现）

---

## 1) 产品说明（Product Brief）

### 1.1 产品定位
v3.0.5 延续 v3.0.4 的 Trading OS 轻量架构，但改为“**以当前仓库可运行实现为真相**”的工程化规范：

- **Core Plane（肌肉）**：确定性执行与状态机，风控分层，严格幂等
- **Adapter Plane（感知与执行I/O）**：沿用现有自研 Binance WS/REST 适配层
- **Persistence Plane（记忆）**：PostgreSQL 事件溯源与幂等记录（InMemory 仅测试）
- **Policy Plane（法律）**：KillSwitch 分级策略 + 风险治理规则
- **Insight Plane（大脑）**：可观测、复盘、策略评估接口，AI 只读为主

### 1.2 交易链路（Sense -> Understand -> Decide -> Execute -> Verify -> Learn）
- Sense：Adapter 采集行情/账户/健康状态并标准化为内部事件
- Understand：基于快照/事件做状态理解与诊断上下文构建
- Decide：Policy/风控规则校验，生成动作建议与约束
- Execute：Core/Adapter 确定性执行
- Verify：Reconciler 与对齐流程校正本地状态
- Learn：通过回放与回测形成策略迭代闭环

---

## 2) 技术架构（Five-Plane Architecture）—— 项目对齐版

**Status**：  
- As-Is：五平面分层、Binance 自研适配器、Control 风险闭环、基础 Postgres 事件/快照能力  
- In-Progress：Task10.3 风险幂等持久化（`risk_events/risk_upgrades`）  
- Target：完整跨平面契约一致性（事件字段、时钟、事务原子性）

### 2.1 Core Plane（确定性肌肉 | AI-clean）
**职责**：
- 顺序处理关键事件：OMS 状态推进、成交记账、组合更新
- 严格保证 Rank 单调、幂等、终态不可逆
- 风控三层：Pre/In/Post（插件化接口已落地）

**硬约束**：
- **Float Ban**：资金/仓位/均价/费率/滑点禁 float，统一 `Decimal`
- **Clock Discipline**：关键业务时间字段统一以 UTC aware 或 `ts_ms` 传递

**实现细节**：
- 状态机单调：`PENDING -> SUBMITTED -> PARTIALLY_FILLED -> FILLED/CANCELLED/REJECTED`
- 幂等键：订单 `cl_ord_id`，成交 `exec_id/exec_key`
- 风控异常必须 Fail-Closed

### 2.2 Adapter Plane（强韧感知层 | 多源适配）
**职责**：
- 与交易所交互：WS/REST、重连、限流、对齐、降级级联
- 向 Core 输出 canonical 事件流（订单更新、成交、健康等）

**v3.0.5 裁定**：
- 默认主线：仓库现有自研 Binance Adapter 栈（`public_stream/private_stream/rest_alignment/degraded_cascade`）
- CCXT/CCXT Pro：可选方案，不作为当前主线改造目标

**必备能力**：
- Public/Private 物理隔离
- Stale 检测 + 单飞重连
- Alignment Gate（对齐前不放行业务事件）
- Token Bucket + Backoff
- 控制面上报失败时本地 Fail-Closed 防护

### 2.3 Persistence Plane（溯源与时序存储 | PostgreSQL-First）
**职责**：
- 事件真理源（append-only）
- 快照恢复
- 风险幂等与升级幂等记录

**v3.0.5 裁定**：
- 生产首选 PostgreSQL
- InMemory 仅用于测试/本地快速迭代
- 不再以 SQLite 作为主线落地前提

**Status**：  
- As-Is：`event_log/snapshots` 已有 PostgreSQL 实现  
- In-Progress：`risk_events/risk_upgrades` 持久化与接口接线  
- Target：迁移体系（Alembic）+ 事务原子语义 + 重启后幂等契约全绿

**最小表设计（建议）**：
```sql
CREATE TABLE IF NOT EXISTS event_log (
  event_id         VARCHAR(255) PRIMARY KEY,
  event_type       VARCHAR(255) NOT NULL,
  aggregate_id     VARCHAR(255) NOT NULL,
  aggregate_type   VARCHAR(255) NOT NULL,
  ts_utc           TIMESTAMPTZ NOT NULL,
  data             JSONB NOT NULL,
  metadata         JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS snapshots (
  snapshot_id      VARCHAR(255) PRIMARY KEY,
  stream_key       VARCHAR(255) NOT NULL,
  aggregate_id     VARCHAR(255) NOT NULL,
  aggregate_type   VARCHAR(255) NOT NULL,
  ts_utc           TIMESTAMPTZ NOT NULL,
  state            JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_events (
  dedup_key        VARCHAR(255) PRIMARY KEY,
  scope            VARCHAR(128) NOT NULL,
  severity         VARCHAR(32) NOT NULL,
  reason           TEXT NOT NULL,
  recommended_level INT NOT NULL,
  payload          JSONB NOT NULL,
  ingested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS risk_upgrades (
  upgrade_key      VARCHAR(255) PRIMARY KEY,
  scope            VARCHAR(128) NOT NULL,
  level            INT NOT NULL,
  dedup_key        VARCHAR(255) NOT NULL,
  reason           TEXT,
  recorded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 2.4 Policy Plane（治理与法律层）
**职责**：
- 风险规则判定与操作约束
- KillSwitch 分级动作管理
- 关键治理行为可审计

**KillSwitch 级别定义**：
- L0: NORMAL
- L1: NO_NEW_POSITIONS
- L2: CANCEL_ALL_AND_HALT
- L3: LIQUIDATE_AND_DISCONNECT

### 2.5 Insight Plane（大脑与推理层）
**职责**：
- 聚合事件、快照、健康状态生成诊断上下文
- 以只读分析为主，输出可审计洞察事件
- 为回测和复盘提供统一查询接口

---

## 3) 跨平面通信与事件定义

**Status**：  
- As-Is：基础 EventEnvelope 与事件查询链路可用  
- In-Progress：字段收敛（`trace_id/source/ts`）与上下游一致性  
- Target：冻结字段全集在 API/存储/日志三处完全对齐

### 3.1 事件封包（EventEnvelope v3.0.5）
跨平面事件建议统一字段：
- `event_id`
- `schema_version`
- `event_type`
- `stream_key`
- `trace_id`
- `causation_id` / `correlation_id`
- `exchange_event_ts_ms`
- `local_receive_ts_ms`
- `source`
- `seq`
- `flags`
- `payload`

### 3.2 事件类型最小全集
- `OrderIntentCreated`
- `OrderSubmitted`
- `OrderStateUpdated`
- `ExecutionFilled`
- `BalanceSnapshot`
- `PositionsSnapshot`
- `OpenOrdersSnapshot`
- `AdapterHealth`
- `ReconcileDiffCandidate`
- `ReconcileDiffConfirmed`
- `KillSwitchSet`
- `RiskEventIngested`
- `AIInsightEvent`

### 3.3 结构化日志规范
每条关键日志至少包含：
- `ts_ms`, `level`, `trace_id`, `stream_key`, `component`, `msg`, `ctx`

---

## 4) 接口定义（Ports & Adapters）

**Status**：  
- As-Is：核心接口和控制面路由可运行  
- In-Progress：接口契约与实现映射表补全  
- Target：接口冻结表 + 破坏性变更治理（版本化）

### 4.1 Adapter 抽象接口（建议）
```python
class AbstractExchangeAdapter(ABC):
    venue: str

    async def connect(self): ...
    async def disconnect(self): ...

    async def submit_order(self, intent): ...
    async def cancel_order(self, cl_ord_id: str, broker_order_id: str | None = None): ...

    async def get_open_orders_snapshot(self): ...
    async def get_balance_snapshot(self): ...
    async def get_positions_snapshot(self): ...

    def on_order_update(self, callback): ...
    def on_fill(self, callback): ...
    def on_market(self, callback): ...
    def on_health(self, callback): ...
```

### 4.2 Core 端口（建议）
- `ExecutionPort`：Core -> Adapter（submit/cancel）
- `ReportsPort`：Adapter -> Core（order/fill/health）
- `EventStorePort` / `SnapshotStorePort`
- `ClockPort`

---

## 5) Reconciler（对账器）

**Status**：  
- As-Is：有对齐与健康恢复相关能力基础  
- In-Progress：Reconciler 主干设计  
- Target：Candidate/Confirmed 全流程自动化与回归集

### 5.1 核心原则
- 采用确认窗口（Grace Window）避免瞬时抖动误判
- 窗口内差异只记 Candidate，不立即熔断
- 超窗且多次确认差异才升级为 Confirmed

### 5.2 最小流程
1. 拉取 `open_orders / positions / balance` 快照
2. 与本地状态对比
3. 产出 `Candidate` 或 `Confirmed`
4. Confirmed 严重差异触发 L1/L2 策略

---

## 6) Control API（FastAPI 最小集合）

**Status**：  
- As-Is：`/health*`、`/v1/risk/events`、`/v1/killswitch`、`/v1/events`、`/v1/snapshots/latest`  
- In-Progress：风险事件持久化接线  
- Target：AI proposal 审批链路与契约测试

### 6.1 必备端点（项目基线）
- `GET /health`
- `GET /health/live`
- `GET /health/ready`
- `GET /health/dependency`
- `POST /v1/risk/events`
- `GET /v1/killswitch`
- `POST /v1/killswitch`
- `GET /v1/events`
- `GET /v1/snapshots/latest`

### 6.2 幂等语义（强约束）
- `POST /v1/risk/events`
  - 新 `dedup_key`：`201`
  - 重复 `dedup_key`：`409`
- KillSwitch 仅升级不降级（自动路径）

---

## 7) 测试门禁（Solo 也必须严格）

**Status**：  
- As-Is：P0 Gate 与 Postgres 基础集成测试框架  
- In-Progress：重启后幂等契约测试  
- Target：事务一致性、故障注入、影子运行评估全量门禁

### 7.1 必测范围
- OMS 状态机与单调性
- 成交/回报幂等
- 风控分层与 Fail-Closed
- 适配器鲁棒性（断流、429、重连、对齐）
- PostgreSQL 事件与快照能力

### 7.2 新增强制项：重启后幂等契约测试
建议新增：`trader/tests/test_risk_idempotency_persistence.py`

必须覆盖：
1. 首次 `dedup_key` 上报返回 201
2. 重启后重复上报同一 `dedup_key` 返回 409
3. 同一 `upgrade_key` 只写一次，不重复升级
4. KillSwitch 状态不出现重复升级副作用

### 7.3 CI Gate（必须阻断）
- `p0-gate`：保持现有 P0 回归
- `control-gate`：覆盖风险闭环关键断言
- `postgres-integration`：强制包含“重启后幂等”契约测试

### 7.4 Task10.3 事务时序与失败恢复矩阵（强制）

**目标**：`/v1/risk/events` 的“事件去重 + 升级幂等 + 副作用”具备可验证原子语义。  

**推荐事务时序（单请求）**：
1. `BEGIN`  
2. `INSERT risk_events(dedup_key, ...) ON CONFLICT DO NOTHING`  
3. 若第2步未插入（重复）：`ROLLBACK` 并返回 `409`  
4. 读取当前 KillSwitch 级别  
5. 若 `recommended_level <= current_level`：`COMMIT` 并返回 `201`（事件接收，未升级）  
6. 生成 `upgrade_key` 并 `INSERT risk_upgrades(upgrade_key, ...) ON CONFLICT DO NOTHING`  
7. 若第6步未插入（重复升级）：`COMMIT` 并返回 `201`（事件接收，升级已存在）  
8. 执行 KillSwitch 升级写入  
9. `COMMIT` 并返回 `201`

**失败恢复矩阵**：

| 失败点 | 风险 | 恢复策略 | 验收断言 |
|---|---|---|---|
| Step2 前崩溃 | 无写入 | 重试同请求 | 最终 201/409 语义正确 |
| Step2 后 Step6 前崩溃 | 仅事件入库，未升级 | 重放补偿任务扫描 `risk_events` | 不重复插入 `risk_events`，最多一次升级 |
| Step6 后 Step8 前崩溃 | 升级记录已落库，副作用未执行 | 启动恢复任务按 `risk_upgrades` 补执行 | 升级副作用最终一致，不重复升级 |
| Step8 前后网络失败 | 客户端重试导致重复请求 | 依赖双唯一键防重 | 多次重试无重复副作用 |
| 提交后响应丢失 | 客户端未知结果重试 | 幂等重放 | 第二次请求可稳定返回重复语义 |

**测试要求（必须入 CI）**：
1. 重启后重复 `dedup_key` 返回 `409`  
2. 重启后重复 `upgrade_key` 不产生二次升级  
3. 注入 Step2/Step6/Step8 故障后恢复到一致状态  
4. KillSwitch 级别仅单调升级，不发生回滚

---

## 8) 依赖栈与轮子复用策略（v3.0.5 项目对齐）

**Status**：  
- As-Is：FastAPI/Pydantic/aiohttp/websockets/pytest/asyncpg  
- In-Progress：OTel/Prometheus/Testcontainers/Alembic 最小闭环  
- Target：策略治理与长流程能力按需引入（OPA/Temporal）

| 组件 | 选型 | 说明 |
|------|------|------|
| Core | Python 原生 + 现有领域模型 | 确定性执行 |
| Adapter | 自研 Binance Adapter（aiohttp/websockets） | 主线实现 |
| Persistence | PostgreSQL + asyncpg | 生产首选 |
| Policy | Python 规则 + RiskEngine 分层 | 风险治理 |
| Control API | FastAPI | 控制面 |
| Testing | pytest + 集成测试 | CI 门禁 |
| Insight | Pandas/可选分析组件 | 复盘与诊断 |

### 8.1 避免重复造轮子（执行准则）
1. **业务差异化层自研**：OMS 状态机、幂等语义、对齐与降级策略保持自研。  
2. **基础设施层优先复用**：迁移、可观测、测试编排、工作流、策略引擎优先采用成熟开源组件。  
3. **一条判断线**：若模块不直接创造交易策略优势，默认不自研。  
4. **引入前提**：必须能被最小化接入并通过现有 CI Gate 验证。

### 8.2 业界领先思想架构（采纳策略）
1. **Event Sourcing + Snapshot**：已采纳，继续作为第一性数据架构。  
2. **Outbox + Idempotent Consumer**：P1 收口目标，强化跨进程/重启一致性。  
3. **CQRS**：选择性采纳，仅在读写压力明显分离时启用，避免过度设计。  
4. **Fail-Closed by Design**：持续贯彻到 Adapter 异常路径和 Control 不可达路径。

### 8.3 开源工具引入优先级（按投入产出）
**P1 优先引入（高性价比）**：  
1. OpenTelemetry（Tracing）  
2. Prometheus Python Client（Metrics）  
3. Testcontainers Python（PostgreSQL 集成测试）  
4. Alembic（数据库迁移管理）

**P2 评估引入（复杂度上升时）**：  
1. OPA（策略治理外置）  
2. Temporal（长流程 durable workflow）

**P3 再引入（跨进程/多节点阶段）**：  
1. NATS JetStream（事件总线与流式持久化）

### 8.4 非目标（当前阶段不做）
1. 不为“抽象而抽象”强行引入 CQRS/消息总线。  
2. 不在 P1 阶段重构为全新 Adapter 技术栈（例如整体切换到 CCXT Pro）。  
3. 不在没有门禁测试的前提下引入新基础设施组件。

---

## 9) 当前实现状态与路线图

**Status**：  
- As-Is：P0 与控制面最小闭环稳定  
- In-Progress：Task10.3 与幂等持久化  
- Target：P2 主线（Runner/Reconciler/可观测）

### 9.1 已完成（Completed）
1. P0 阻塞清零（connector/private_stream/deterministic layer）  
2. 风险闭环基础（`/v1/risk/events` + `/v1/killswitch`）  
3. 风控分层（Pre/In/Post）与 KillSwitch 映射  
4. 三级健康检查（live/ready/dependency）与 PostgreSQL 探测  
5. CI Gate 基线固化

### 9.2 进行中（In Progress）
1. Task10.3：`risk_upgrades` 内存 -> PostgreSQL 迁移  
2. 补齐 `risk_events/risk_upgrades` 持久化契约  
3. 新增“重启后幂等”测试并并入 CI

### 9.3 计划中（Planned）
1. Strategy Registry + Runner 主线落地  
2. Reconciler 主干完善  
3. Compose 首发拓扑完善  
4. 可观测性（日志/指标/告警/runbook）  
5. 引入 OTel + Prometheus + Testcontainers + Alembic 的最小闭环

---

## 10) PostgreSQL 轻量部署（个人开发者）

**Status**：  
- As-Is：Compose 样例可直接使用  
- In-Progress：与 CI 的一键联动脚本  
- Target：本地/CI/预发一致化部署模板

### 10.1 Docker Compose 最小示例
```yaml
version: "3.9"
services:
  postgres:
    image: postgres:16
    container_name: qts-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: trading
      POSTGRES_USER: trader
      POSTGRES_PASSWORD: trader_pwd
    ports:
      - "5432:5432"
    volumes:
      - qts_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U trader -d trading"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  qts_pgdata:
```

### 10.2 本地环境变量
```bash
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=trading
POSTGRES_USER=trader
POSTGRES_PASSWORD=trader_pwd
# 或
POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:5432/trading
```

### 10.3 使用建议
1. 本地优先 Compose 启 PostgreSQL，降低环境搭建成本  
2. 集成测试默认接 PostgreSQL，关键幂等契约不得在 CI 中跳过

---

## 11) 开发阶段计划（P0/P1/P2）

**Status**：  
- As-Is：P0 已完成  
- In-Progress：P1 收口  
- Target：P2 展开并形成稳定发布节奏

### P0（已完成）
目标：生产阻塞缺陷清零，回归稳定。

### P1（收口中）
目标：存储收敛、PostgreSQL 事件溯源最小可用、风险幂等持久化完成。  
当前关键任务：Task10.3（升级幂等持久化 + 重启后幂等）。

### P2（待展开）
目标：Strategy Runner、Reconciler、可观测性与部署能力补齐；按需评估 OPA/Temporal。

---

## 12) AI接入战略与框架选型（AI只做大脑与治理）

**Status**：  
- As-Is：AI 边界原则与治理框架已定义  
- In-Progress：AI proposal + 审批链路接口设计  
- Target：离线评测 -> 影子运行 -> 灰度发布闭环

### 12.1 战略边界（强约束）
1. AI 不直接下单，不直接改写 Core 状态机，不绕过 Risk/Policy。  
2. AI 仅输出“建议、评分、解释、诊断、参数提案”，执行必须走确定性链路。  
3. 高风险动作（风控阈值变更、KillSwitch 降级、参数发布）必须 HITL（人工确认）。  
4. 所有 AI 输出必须可审计（trace_id、输入快照、输出结构、审批人、执行结果）。

### 12.2 AI 参与场景（当前版本目标）
1. 策略研发：生成/比较策略候选、参数提案、实验总结。  
2. 交易监控：异常解释、告警聚类、根因候选、处置建议。  
3. 交易分析：回放摘要、PnL 归因、执行质量诊断、风险复盘。  
4. 运营治理：发布前检查清单、变更影响评估、Runbook 辅助生成。

### 12.3 业界实践流程（推荐）
1. 离线评测（Evals）  
2. 影子运行（Shadow Mode，不影响真实执行）  
3. 小流量灰度（Canary）  
4. 正式放量（持续评估 + 回归门禁）

### 12.4 框架与工具选型（分层引入）
**编排与 Agent 层**：  
1. OpenAI Agents SDK（轻量接入）  
2. LangGraph（需要 durable workflow / HITL 中断恢复时）

**评测与治理层**：  
1. OpenAI Evals / Agent Evals / Trace Grading  
2. MLflow GenAI Eval（可选，做长期评估看板）  
3. Guardrails（结构化输出与校验）

**策略与合规层**：  
1. OPA（策略规则外置，P2+）  
2. 审批链（HITL）与审计日志（强制）

**可观测与工程层**：  
1. OpenTelemetry + Prometheus  
2. Testcontainers（AI 相关集成测试与回归环境）  
3. Alembic（AI 配置/治理表迁移）

### 12.5 最小可执行落地（MVP）
1. 增加 `AIInsightEvent` 与 `AIDecisionProposal` 两类事件（只读建议）。  
2. 增加 `POST /v1/ai/proposals`（写建议，不执行）。  
3. 增加 `POST /v1/ai/proposals/{id}/approve`（人工审批后进入确定性执行链）。  
4. 增加 AI 评测任务：固定数据集 + 固定 rubric + 回归分数阈值。  
5. 未达评测阈值的模型版本禁止进入灰度。

### 12.6 AI 质量门禁（CI / CD）
1. 结构化输出校验通过率 >= 99%。  
2. 关键诊断任务误报率/漏报率在阈值内。  
3. 影子运行阶段建议采纳后收益与风险指标满足门槛。  
4. 所有 AI 版本必须可回滚，且保留版本化评测报告。

---

## 13) 变更记录（Spec Changelog）

| 日期 | 版本 | 变更摘要 |
|------|------|----------|
| 2026-03-03 | v3.0.8 | Sprint 3 (Task10.3-C): 原子语义与故障恢复 - 新增 `risk_upgrade_effects` 表作为恢复锚点（status: PENDING/APPLIED/FAILED）；新增 `try_record_upgrade_with_effect` 事务方法在同一事务内写入 upgrade 和 effect intent；新增 `mark_effect_applied/mark_effect_failed/get_pending_effects` 恢复接口；新增断点测试验证幂等性 |
| 2026-03-03 | v3.0.7 | Sprint 2 (Task10.3-B): 升级幂等持久化 - 新增 `try_record_upgrade` 原子接口（首次 True/重复 False），PostgreSQL 使用 `INSERT ... ON CONFLICT DO NOTHING`，InMemory 回退提供同等语义；升级流程以 `try_record_upgrade` 返回值为门闩控制副作用执行；新增并发测试验证幂等性 |
| 2026-03-03 | v3.0.7 | Sprint 1: risk_events 持久化（含 dedup_key 唯一约束），RiskService 桥接到持久层；修复包：幂等返回一致性（重复返回已有 event_id）、PG/内存语义一致性（完整保存事件快照）；命名规范对齐：统一 `upgrade_records` -> `risk_upgrades` |
| 2026-03-03 | v3.0.5 | Sprint 1: 实现 risk_events PostgreSQL 持久化（含 dedup_key 唯一约束），RiskService 桥接到持久层（保留回退机制），确保 POST /v1/risk/events 语义不变（201 新建 / 409 重复） |
| 2026-03-02 | v3.0.5 | 全文新增 As-Is/In-Progress/Target 三态标记；新增 Task10.3 事务时序与失败恢复矩阵 |
| 2026-03-02 | v3.0.5 | 新增“AI接入战略与框架选型”章节，明确 AI 边界、场景、框架选型、HITL 审批与评测门禁 |
| 2026-03-02 | v3.0.5 | 新增“避免重复造轮子执行准则”与“业界思想/开源工具引入优先级”，明确 P1/P2 工具化路线 |
| 2026-03-02 | v3.0.5 | 在 v3.0.4 五平面架构基础上修订为项目对齐版：PostgreSQL-First、自研 Binance Adapter 主线、新增“当前实现状态与路线图”、新增 PostgreSQL 轻量部署、新增“重启后幂等”CI 强制门禁 |

