# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working on Complex Tasks

When working on complex or multi-step tasks, create a todo list to track progress and remain on track. Update item status as work proceeds.

## Commands

### Install dependencies
```bash
pip install -r trader/requirements-ci.txt
```

### Run tests
```bash
# All tests (from repo root)
python -m pytest -q trader/tests/ --tb=short

# Single test file
python -m pytest -q trader/tests/test_binance_connector.py --tb=short

# Single test by name
python -m pytest -q trader/tests/test_hard_properties.py -k "fail_closed" --tb=short
```

## Documentation Updates (必须流程)

### PLAN.md (开发计划)
- 开始开发动作前,必须更新 PLAN.md 中的开发计划.


完成开发任务后，必须更新以下文档：

### PROJECT_STATUS.md (滚动式追踪)
- **开发前状态**：记录本次开发开始前的状态
- **本次开发动作**：记录本次完成的工作
- **下一步计划**：根据 PLAN.md 记录下一步开发动作
- 更新 `## 最后更新时间` 时间戳
- 将完成的 issue 从"待确认任务"移到"已验证任务"
- 更新 CI 门禁状态

### EXPERIENCE_SUMMARY.md (经验总结)
- 记录本次开发中遇到的问题和解决方案
- 记录可复用的设计模式或代码片段
- 记录踩坑记录和教训


### P0 regression suite (must pass before any PR)
```bash
python -m pytest -q trader/tests/test_binance_connector.py trader/tests/test_binance_private_stream.py trader/tests/test_binance_degraded_cascade.py trader/tests/test_deterministic_layer.py trader/tests/test_hard_properties.py --tb=short
```

### Postgres integration tests (requires running Postgres)
```bash
# Start Postgres via Docker Compose
docker compose up -d

# Run with connection string
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

## Architecture

This is a medium/low-frequency crypto trading system (Binance Demo) built on a **five-plane architecture** with strict separation of concerns.

### Five Planes

**Core Plane** (`trader/core/`) — No IO, fully deterministic
- `application/oms.py`: Order Management System — monotonic state machine, CAS-based updates, idempotency via `cl_ord_id`/`exec_id`
- `application/risk_engine.py`: Risk state progression
- `application/deterministic_layer.py`: Replay/recovery logic; event log is the source of truth
- `domain/models/`: `Order`, `Position`, `Money`, `Signal`, `Events` — pure domain types

**Adapter Plane** (`trader/adapters/`) — All IO + sanitization
- `binance/connector.py`: Main Binance integration entry point
- `binance/public_stream.py` / `private_stream.py`: Physically isolated WebSocket FSMs — public and private streams must never share state or affect each other's reconnect logic
- `binance/rest_alignment.py`: REST reconciliation after reconnect (openOrders + account snapshot)
- `binance/degraded_cascade.py`: Handles connection failures; triggers KillSwitch L1 on unresolvable inconsistency
- `binance/rate_limit.py` / `backoff.py`: Token bucket + full-jitter exponential backoff

**Persistence Plane** (`trader/adapters/persistence/`)
- Event sourcing: append-only event log is primary; read models are projections
- `memory/event_store.py`: In-memory store (current default)
- `postgres/`: PostgreSQL-backed store for risk events (PG-First for risk)
- Snapshots accelerate recovery but never replace the event log

**Policy Plane** (`trader/core/application/`, `trader/services/risk.py`)
- KillSwitch levels: `NORMAL(0)` → `NO_NEW_POSITIONS(1)` → `CANCEL_ALL_AND_HALT(2)` → `LIQUIDATE_AND_DISCONNECT(3)`
- Risk gates: exposure limits, liquidity depth checks, time-window constraints
- All KillSwitch escalations are irreversible within a session (fail-closed)

**Control Plane** (`trader/api/`, `trader/services/`)
- FastAPI on port 8080
- Routes: health, strategies, deployments, backtests, risk, orders, portfolio, events, killswitch, brokers
- Environmental risk events from Adapter must flow through Control Plane API to trigger global KillSwitch L1

### Key Invariants (Hard Constraints)

1. **Monotonic state machine**: Order states only advance (CANCELLED/REJECTED/FILLED/EXPIRED are terminal). Never overwrite a terminal state with a lower-rank state.
2. **Idempotency everywhere**: Deduplicate on `cl_ord_id` + `exec_id`. WS and REST may deliver the same event concurrently — never double-book.
3. **Fail-closed**: On unresolvable inconsistency, enter `DEGRADED_MODE` and escalate KillSwitch. Never silently swallow exceptions (`except: pass` is forbidden).
4. **Deterministic concurrency**: Use hashed locks or actor partitioning per `cl_ord_id`. Never rely on timing assumptions.
5. **Broker is source of truth**: WS is low-latency driver; REST is correction/recovery. After reconnect, always run REST Alignment before resuming.

### Testing Fakes
`trader/tests/fakes/`: `fake_clock.py`, `fake_http.py`, `fake_websocket.py` — use these for all unit tests involving time, HTTP, or WebSocket IO.

### Observability Requirements
Every external message must carry metadata: `local_receive_ts_ms`, `exchange_event_ts_ms`, `seq/update_id`, `source`, `out_of_order`, `gap_detected`. Key state transitions must emit structured logs with `trace_id`, `stream_key`, `schema_version`.

## Testing Requirements

Testing is mandatory and must be dense. AI-generated code that cannot be verified by tests must not be merged.

### Unit Tests (必须覆盖)
- **状态机**：每个状态迁移路径，包括合法迁移、非法迁移拒绝、终态不可覆盖
- **边界输入**：空值、零值、最大值、负数、重复 ID、乱序序列号
- **错误路径**：异常抛出、Fail-Closed 行为、降级触发条件
- **幂等性**：同一操作执行两次结果一致，不重复记账
- **并发安全**：hashed lock 分区、CAS 竞争场景
- 核心模块（OMS、DeterministicLayer、RiskEngine）覆盖率 ≥ 90%
- 使用 `trader/tests/fakes/` 中的 fake_clock / fake_http / fake_websocket，禁止在单测中发起真实网络请求

### Integration Tests (必须覆盖)
- **持久化层**：PostgreSQL event_store 的幂等 append、乱序读取、快照恢复；PG 不可用时自动 fallback 到内存
- **适配器依赖**：Binance REST Alignment 在重连后能正确恢复 OMS 状态；rate_limit + backoff 在 429 场景下的降级行为
- **风险仓储**：risk_repository 的 PG 写入、读取、并发幂等
- 集成测试须在 `docker compose up -d` 启动 Postgres 后运行，连接串通过环境变量注入

### E2E Tests (必须覆盖核心闭环)
- **正常闭环**：下单 → 成交回报 → OMS 状态 FILLED → Position 更新 → 事件写入 event_log
- **失败回退**：Adapter 进入 DEGRADED_MODE → KillSwitch L1 触发 → 新开仓被拒绝
- **重连恢复**：WS 断线 → 重连 → REST Alignment → OMS 状态与交易所一致
- **KillSwitch 升级**：L0 → L1 → L2 的触发条件与行为验证，L2 下所有挂单被撤销

### Smoke Tests (涉及外部认证时必须执行)
- 任何涉及真实 Binance API Key / 数据库连接的功能，上线前至少执行一次真实环境 smoke 验证
- Smoke 测试结果须记录在 PR 包的 `test_results` 字段中
- Binance Demo 环境：验证 listenKey 获取、私有流订阅、REST 下单接口可达性

### 补充要求
- **回归保护**：每次新增功能必须同步补充对应测试，禁止只写实现不写测试
- **乱序/重复/丢失场景**：凡涉及序列号、事件流、WS 消息处理的模块，必须覆盖乱序、重复、静默断流三种异常场景
- **时间相关测试**：使用 `fake_clock` 控制时间，禁止 `time.sleep` 或依赖真实时钟
- **测试命名**：测试函数名须清晰描述场景，格式 `test_<场景>_<预期结果>`，例如 `test_duplicate_fill_ignored`

## CI Gate Order

CI runs sequentially: `import-gate` → `p0-gate` → `control-gate` → `postgres-integration`. All stages must pass for a PR to merge.

## Code Style

- Python 3.12.5, async-first (`asyncio` throughout; no blocking network calls)
- Strict type hints on all function signatures and class attributes; use `str | None` style (Python 3.10+)
- Black, line-length 100; isort with black profile
- `@dataclass(slots=True)` preferred where applicable
- All locks must be `asyncio.Lock` or actor/queue pattern
