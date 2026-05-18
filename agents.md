# AGENTS.md

此文件为 AI 助手（Agents）提供在本仓库进行开发时的核心准则、架构背景及操作规范。**在开始任何任务前，请先完整阅读此文档。**

---

## 🤖 任务处理原则

1.  **复杂任务追踪**：面对多步骤任务，必须先创建 `TODO` 列表，并在执行过程中实时更新状态。
2.  **上下文对齐**：在修改代码前，优先检索 `trader/core/` 下的相关逻辑，确保不违反确定性原则。
3.  **接口契约优先**：凡涉及函数签名、DTO、事件 Schema、API 字段、跨层调用或命名重构，必须先查阅并按需更新 `docs/INTERFACE_CONTRACTS.md`，再修改实现。
4.  **架构图同步**：凡涉及层级边界、模块职责、跨层调用、主数据流、持久化路径、风控闭环或运行拓扑变更，必须先查阅并按需更新 `docs/PROJECT_ARCHITECTURE.md`。
5.  **文档闭环**：任何功能性变更必须同步更新 `PROJECT_STATUS.md`、`DEVELOPMENT_LOG.md` 和 `docs/EXPERIENCE_SUMMARY.md`。
6.  **文档新鲜度**：任何影响“当前状态、下一步计划、阶段完成度、优先级判断”的任务，必须在交付前同步刷新相关文档，禁止让 `PROJECT_STATUS.md`、`PLAN.md`、`DEVELOPMENT_LOG.md`、阶段计划文档与仓库现状脱节。
7.  **规则入口同步**：修改 `AGENTS.md`、`CLAUDE.md`、`.traerules` 任一 AI/工具规则入口时，必须检查另外两个入口，保持公共工程约束一致。
8.  **TDD 防幻觉流程**：涉及代码行为变更时，必须优先写出能复现目标行为/缺陷的测试；测试必须基于真实已检索接口或先更新接口契约，禁止先臆造实现 API。
9.  **Skills 按需加载**：涉及回测、风控、Binance适配、OMS等模块时，先查阅 `skills/_meta/index.yaml` 加载对应Skill；复杂需求执行 `skills/spec_rfc/SPEC.md` 中的Spec RFC流程。

---

## 🛠️ 常用指令 (Commands)

### 环境配置与检查
* **安装依赖**: `pip install -r trader/requirements-ci.txt`
* **类型检查**: `mypy trader/`
* **格式化**: `black trader/ --line-length 100` | `isort trader/ --profile black`

### 测试执行
* **全量测试**: `python -m pytest -q trader/tests/ --tb=short`
* **P0 回归集**: `python -m pytest -q trader/tests/test_binance_connector.py trader/tests/test_binance_private_stream.py trader/tests/test_binance_degraded_cascade.py trader/tests/test_deterministic_layer.py trader/tests/test_hard_properties.py --tb=short`
* **Postgres 集成测试**: (需启动 Docker)
    ```bash
    docker compose up -d
    POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:5432/trading python -m pytest -q trader/tests/test_postgres_storage.py trader/tests/test_risk_idempotency_persistence.py --tb=short
    ```

---

## 🏗️ 架构说明 (Architecture)

本系统采用 **五层平面架构 (Five-Plane Architecture)**，严禁跨层直接调用或状态污染。

### 1. Core Plane (`trader/core/`) - 核心层
* **特性**：无 IO，完全确定性，Monotonic State Machine（单调状态机）。
* **OMS**: 处理订单状态迁移，利用 `cl_ord_id` 实现幂等。
* **Deterministic Layer**: 负责事件回放与恢复，Event Log 是唯一的真理来源。

### 2. Adapter Plane (`trader/adapters/`) - 适配器层
* **特性**：所有 IO 发生地，负责数据清洗与隔离。
* **隔离原则**：Public Stream 与 Private Stream 必须物理隔离，严禁共享状态。
* **REST Alignment**: 重连后必须通过 REST 接口进行状态对齐。

### 3. Persistence Plane (`trader/adapters/persistence/`) - 持久化层
* **Event Sourcing**: 采用追加式日志。
* **PG-First for Risk**: 风险相关事件优先写入 PostgreSQL。

### 4. Policy Plane (`trader/core/application/`, `trader/services/risk.py`) - 策略层
* **KillSwitch 机制**: 
    * `NORMAL(0)` → `NO_NEW_POSITIONS(1)` → `CANCEL_ALL_AND_HALT(2)` → `LIQUIDATE_AND_DISCONNECT(3)`
    * **注意**：所有升级操作在当前 Session 内不可逆（Fail-closed）。

### 5. Control Plane (`trader/api/`, `trader/services/`) - 控制层
* 基于 FastAPI (Port 8080)，负责外部接口、生命周期管理及全局风险触发。

---

## ⚠️ 核心不变性约束 (Hard Invariants)

> **重要提示**：违反以下任何一条约束的代码都将被视为严重 Bug。

1.  **单调状态机**：订单状态只能前进（如：NEW -> FILLED），禁止从终端状态（CANCELLED/REJECTED/FILLED）回退。
2.  **绝对幂等**：基于 `cl_ord_id` + `exec_id` 进行去重。WS 和 REST 消息可能并发到达，严禁重复记账。
3.  **Fail-Closed（故障关闭）**：遇到无法解析的不一致时，必须触发 `DEGRADED_MODE` 并升级 KillSwitch。**严禁使用 `except: pass`**。
4.  **确定性并发**：禁止依赖 `asyncio` 的执行顺序，必须使用基于 `cl_ord_id` 的哈希锁（Hashed Locks）。
5.  **交易所为准**：WS 用于低延迟驱动，REST 用于最终一致性校准。
6.  **契约命名一致性**：内部字段名必须遵守 `docs/INTERFACE_CONTRACTS.md`。外部字段（如 Binance `clientOrderId`）只能在 Adapter/API 边界转换，禁止泄漏到 Core Plane。

---

## 🧪 测试规范 (Testing Requirements)

AI 生成的代码必须伴随高密度的测试覆盖。

### TDD 开发流程（防止 AI 幻觉）

* **Red**: 先检索现有接口、类型和调用点，再编写失败测试。测试必须失败在目标行为上，而不是 import error、拼写错误或虚构接口。
* **Green**: 只写让测试通过的最小实现，优先复用既有模块和契约，不新增无证据抽象。
* **Refactor**: 测试通过后再做小步重构，并保持核心不变性和接口契约不变。
* **No Hallucinated API**: 如果测试需要的新函数、字段或 DTO 尚不存在，必须先更新 `docs/INTERFACE_CONTRACTS.md` 并说明兼容策略，再修改测试和实现。
* **Verification**: 至少运行新增/修改测试；涉及共享核心逻辑时，继续运行相关回归或 P0 回归集。

* **Unit Tests**: 必须覆盖所有状态迁移路径和边界值。使用 `trader/tests/fakes/` (fake_clock/http/ws)，**严禁在单测中访问网络**。
* **Integration Tests**: 覆盖 PG 写入、乱序读取及断线重连后的 REST Alignment 逻辑。
* **E2E Tests**: 验证从下单到成交回报再到事件持久化的完整闭环。
* **异常场景**: 必须模拟序列号跳变、重复事件、WS 静默断流（Silence gap）场景。

---

## 📝 必须执行的流程 (Mandatory Workflow)

完成任务后，AI 必须按以下模板更新文档：

### 0. 更新 `docs/PROJECT_ARCHITECTURE.md`
* 任何影响层级边界、模块职责、跨层调用、主数据流、持久化路径、风控闭环、部署/运行拓扑的架构变更，必须同步更新架构图文档。
* 图文必须反映当前仓库现状，禁止让架构图停留在旧阶段。

### 1. 更新 `docs/INTERFACE_CONTRACTS.md`
* 任何涉及函数签名、DTO、事件 Schema、API 字段、跨层调用或命名重构的变更，必须先更新接口契约。
* 明确标准字段名、旧字段兼容策略、转换边界和对应测试。

### 2. 更新 `PROJECT_STATUS.md`
* 记录开发前后状态对比。
* 将 Issue 从 "待确认" 移至 "已验证"。
* 更新最后操作时间戳。
* 如果本次任务改变了下一步优先级、阶段目标或完成度判断，必须同步更新对应段落。

### 3. 更新 `docs/EXPERIENCE_SUMMARY.md`
* **踩坑记录**：记录本次开发中遇到的逻辑陷阱。
* **设计模式**：记录本次实现的优秀代码片段或设计模式。

### 4. 更新 `DEVELOPMENT_LOG.md`
* 追加本次开发记录，说明背景、决策、改动、验证、风险/遗留和关联文档。
* `DEVELOPMENT_LOG.md` 只追加不重写历史；如果后续推翻前一条判断，在新记录中说明。

### 5. 保持计划文档新鲜
* 涉及排期、阶段切换、优先级重排时，必须同步更新 `PLAN.md`。
* 如果存在独立阶段计划文档（如 `plans/` 下文件），必须一并更新，确保只有一个当前执行入口。
* 严禁出现同一任务在不同文档中同时处于“已完成”和“待开始”状态。

### 6. 保持 AI/工具规则入口同步
* 修改 `AGENTS.md`、`CLAUDE.md`、`.traerules` 任一文件时，必须检查另外两个文件是否需要同步。
* 公共工程约束（架构、接口契约、测试、文档闭环、红线操作）不得在不同入口中出现冲突。

---

## 💻 技术栈约束
* **Language**: Python 3.12.5 (Async-first)
* **Type Hinting**: 严格类型标注，使用 `str | None` 风格。
* **Classes**: 优先使用 `@dataclass(slots=True)`。
* **Concurrency**: 仅使用 `asyncio.Lock` 或 Actor 模式。
