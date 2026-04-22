# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## First Step Rules — 项目扫描

进入项目后，先扫描：
- 目录结构：`trader/` (核心) / `trader/adapters/` (IO) / `trader/api/` (控制面) / `trader/services/` (业务逻辑) / `trader/tests/` / `Frontend/`
- 关键配置文件：`.env`（API Key）、`compose.yml`（Postgres）
- 启动方式：`uvicorn trader.api.main:app --port 8080`
- 数据流主路径：Binance WS → Connector → OMS → Portfolio → API → Frontend
- 验证命令：P0 回归测试（见下方）

未理解项目结构前，不直接大改。

## Communication

- 默认中文；代码、命令、变量名、报错保留英文。
- 先给结论，再给步骤和理由。不铺垫空话，不谄媚。
- 发现设计问题直接指出，不绕弯。多个方案时明确"推荐方案"。

## Commands

### Install dependencies
```bash
pip install -r trader/requirements-ci.txt
```

### Run tests
```bash
# All tests
python -m pytest -q trader/tests/ --tb=short

# P0 regression suite (must pass before any PR)
python -m pytest -q trader/tests/test_binance_connector.py trader/tests/test_binance_private_stream.py trader/tests/test_binance_degraded_cascade.py trader/tests/test_deterministic_layer.py trader/tests/test_hard_properties.py --tb=short

# Single test file
python -m pytest -q trader/tests/test_binance_connector.py --tb=short

# Single test by name
python -m pytest -q trader/tests/test_hard_properties.py -k "fail_closed" --tb=short
```

### Postgres integration tests (requires running Postgres)
```bash
docker compose up -d
POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:5432/trading \
  python -m pytest -q trader/tests/test_postgres_storage.py trader/tests/test_risk_idempotency_persistence.py --tb=short
```

### Type checking and formatting
```bash
mypy trader/
black trader/ --line-length 100
isort trader/ --profile black
```

### Start API server
```bash
uvicorn trader.api.main:app --port 8080
```

## Architecture — 五层架构

```
┌─────────────────────────────────────────────────────────────┐
│  Control Plane  (trader/api/, trader/services/)             │
│  FastAPI + 业务逻辑 + KillSwitch + OMS回调                   │
├─────────────────────────────────────────────────────────────┤
│  Adapter Plane  (trader/adapters/binance/)                  │
│  Connector + PublicStream + PrivateStream + REST Alignment │
│  DegradedCascadeController (级联保护)                        │
├─────────────────────────────────────────────────────────────┤
│  Core Plane  (trader/core/)                                │
│  OMS (单调状态机) + RiskEngine + DeterministicLayer         │
│  Position + Order (纯领域模型，无IO)                         │
├─────────────────────────────────────────────────────────────┤
│  Persistence Plane  (trader/storage/)                       │
│  InMemory (当前) / PostgreSQL (事件溯源)                     │
├─────────────────────────────────────────────────────────────┤
│  Policy Plane  (trader/core/application/, trader/services/) │
│  KillSwitch (L0-L3) + 风险规则 + TimeWindow                 │
└─────────────────────────────────────────────────────────────┘
```

### Core Plane (`trader/core/`) — 无 IO，纯确定性
- `application/oms.py`: 单调状态机 + CAS 更新 + `cl_ord_id`/`exec_id` 幂等
- `application/risk_engine.py`: 风险状态机
- `application/deterministic_layer.py`: Replay/recovery，事件日志是真相源
- `domain/models/`: Order, Position, Money, Signal, Events

### Adapter Plane (`trader/adapters/`) — 所有 IO + 清洗
- `binance/connector.py`: 主入口，健康检查循环
- `binance/public_stream.py` / `private_stream.py`: 物理隔离 WS FSM
- `binance/rest_alignment.py`: 重连后 REST 纠偏
- `binance/degraded_cascade.py`: 级联保护 → KillSwitch L1 上报
- `binance/rate_limit.py` / `backoff.py`: 令牌桶 + 全抖动退避

### Persistence Plane
- 事件溯源：append-only 事件日志是主存储，读模型是投影
- `memory/event_store.py`: 内存存储（当前默认）
- `postgres/`: PostgreSQL（风险事件优先）

### Policy Plane
- KillSwitch: `NORMAL(0)` → `NO_NEW_POSITIONS(1)` → `CANCEL_ALL_AND_HALT(2)` → `LIQUIDATE_AND_DISCONNECT(3)`
- 所有 KillSwitch 升级在 session 内不可逆（Fail-Closed）

### Control Plane
- FastAPI on port 8080
- 关键路由：health, strategies, orders, portfolio, risk, killswitch, monitor

## Key Invariants — 硬性约束

1. **单调状态机**：订单状态只能前进，终态（FILLED/CANCELLED/REJECTED/EXPIRED）不可被覆盖
2. **全链路幂等**：`cl_ord_id` + `exec_id` 去重，WS 和 REST 并发到达不重复记账
3. **Fail-Closed**：无法确认一致性时进入 `DEGRADED_MODE` 并触发 KillSwitch。禁止裸 `except: pass`
4. **确定性并发**：同一 `cl_ord_id` 并发处理必须加锁，不依赖时序假设
5. **Broker 是真相源**：WS 低延迟驱动，REST 纠偏恢复。重连后必须先 REST Alignment

## Monitor Rules — 监控数据异常排查

监控页面数据异常时，按以下链路顺序排查：

1. **前端展示** — `Frontend/src/pages/Monitor.tsx`
2. **前端请求** — `useMonitorSnapshot` hook
3. **API 响应** — `GET /v1/monitor/snapshot`
4. **服务层聚合** — `trader/api/routes/monitor.py` → `MonitorService.get_snapshot()`
5. **领域计算** — `PortfolioService.list_positions()` / `OrderService.list_orders()`
6. **原始数据来源** — `OMSCallbackHandler` 成交回调 → Position 更新

若页面显示 0 / 空 / null，必须区分：
- **真正为 0** — 业务上确实没有持仓/订单
- **数据缺失** — OMS 或 Portfolio 数据未更新
- **尚未更新** — 成交回报还未被处理
- **转换失败** — Pydantic 字段名不匹配（silent 数据丢失）
- **前端渲染问题** — API 有数据但前端未正确展示

**常见根因**：字段名不匹配（如 `quantity` vs `qty`）会导致 Pydantic 静默丢弃数据。

## Portfolio and PnL Rules

- Position、Exposure、Daily PnL、Unrealized PnL 必须明确：
  - **输入数据**：成交回报中的 qty/price
  - **计算公式**：qty × price
  - **更新时间点**：on_fill 回调时
  - **展示口径**：`total_positions`（数量）、`total_exposure`（数量×当前价）
- 存在估算值和真实值时，必须明确区分
- PnL 相关逻辑改动后，必须验证：空仓、单仓、多仓、部分成交、多次成交、费用影响、行情波动

## Engineering Discipline — 工程纪律

- 改多个文件时，先说明每个文件为什么要改
- 大改动前先给方案、范围、风险、验证方式
- 能局部修复就不要全局翻修
- 涉及规范变更时，先改文档，再改代码
- **涉及前后端联调时，优先画出最小数据流**：
  ```
  请求发出 → API层(如 /v1/monitor/snapshot) → Service层(如 MonitorService.get_snapshot)
    → 原始数据(如 PortfolioService.list_positions) → 返回响应 → 前端Hook → 页面渲染
  ```
  在图上标注每个节点的字段名、类型、实际值。能快速定位在哪一层变形或丢失。

## Testing Requirements

测试必须密集。AI 生成的代码若无法被测试验证，不得合并。

### 单元测试（必须覆盖）
- 状态机：每个状态迁移路径，合法/非法迁移拒绝、终态不可覆盖
- 边界输入：空值、零值、最大值、负数、重复 ID、乱序序列号
- 错误路径：异常抛出、Fail-Closed 行为、降级触发条件
- 幂等性：同一操作两次结果一致
- 并发安全：hashed lock 分区、CAS 竞争
- 核心模块覆盖率 ≥ 90%
- 使用 `trader/tests/fakes/` 里的 `fake_clock` / `fake_http` / `fake_websocket`，禁止在单测中发起真实网络请求

### 集成测试（必须覆盖）
- 持久化层：PG event_store 幂等 append、乱序读取、快照恢复
- 适配器：REST Alignment 重连后恢复 OMS 状态
- 风险仓储：PG 写入、读取、并发幂等

### E2E 测试（必须覆盖核心闭环）
- 正常闭环：下单 → 成交回报 → OMS FILLED → Position 更新 → 事件写入
- 失败回退：DEGRADED_MODE → KillSwitch L1 → 新开仓拒绝
- 重连恢复：WS 断线 → 重连 → REST Alignment → OMS 与交易所一致
- KillSwitch 升级：L0 → L1 → L2 触发条件与行为

### 补充要求
- 每次新增功能必须同步补充测试
- 乱序/重复/丢失场景凡涉及序列号、事件流必须覆盖
- 时间相关测试使用 `fake_clock`，禁止 `time.sleep`
- 测试命名：`test_<场景>_<预期结果>`

## Documentation Updates — 必须流程

### PLAN.md (开发计划)
- 开始开发动作前，必须更新 PLAN.md

### PROJECT_STATUS.md (滚动追踪)
- 开发前状态 → 本次开发动作 → 下一步计划
- 更新 `## 最后更新时间` 时间戳
- 将完成 issue 从"待确认"移到"已验证"

### EXPERIENCE_SUMMARY.md (经验总结)
- 问题 + 解决方案
- 可复用设计模式
- 踩坑记录

## CI Gate Order

顺序执行：`import-gate` → `p0-gate` → `control-gate` → `postgres-integration`。全部通过方可合并。

## Code Style

- Python 3.12.5，async-first（全程 `asyncio`，无阻塞网络调用）
- 严格类型注解；使用 `str | None` 风格
- Black, line-length 100；isort with black profile
- `@dataclass(slots=True)` 优先
- 所有锁必须用 `asyncio.Lock` 或 actor/queue 模式

## Observability Requirements

每个外部消息必须携带元数据：
- `local_receive_ts_ms` / `exchange_event_ts_ms`
- `seq/update_id` / `source` / `out_of_order` / `gap_detected`

关键状态转换必须打印结构化日志，包含 `trace_id` / `stream_key` / `schema_version`。

## Logging Rules

关键链路必须可观测：
- 信号生成 → 下单请求 → 订单回报 → 成交回报 → 持仓更新 → PnL 更新 → Monitor 聚合

报错时尽量带：输入参数、关键中间状态、异常位置、影响对象。

## Teach While Building

在完成任务时，解释：
1. 这个模块在系统中的作用
2. 数据从哪里来，到哪里去
3. 为什么这样设计
4. 容易踩什么坑

把用户当作"有理解能力的初学者"。优先建立直觉和领域模型。

## Red Lines — 必须先问用户

- 删除文件、目录、git 历史
- 修改 `.env`、密钥、CI/CD 配置
- 数据库 schema 变更
- `git push`、`git rebase`、`git reset --hard`、强制推送
- 安装新的全局依赖或修改系统配置
- 生产部署、公开发布、真实账户操作
