# 阶段 0：基础重构边界冻结与可复核基线

日期：2026-11-02
状态：**阶段 0 已完成，二轮独立审核 A1–A7 全部通过**。本阶段只交付本文件、`docs/PROJECT_ARCHITECTURE.md` 第 8 节增量、
`docs/INTERFACE_CONTRACTS.md` 第 13/15 节增量，以及一份新的基线测试
`trader/tests/test_phase0_foundation_safety.py`。**未修改任何交易行为实现**。

本文件的唯一目的是：把后续 6 个阶段的边界、依赖、验收口径和"哪些旧路径必须消失"固定下来，
并为两处已核实的安全缺陷留下**可重复复现**的证据。后续阶段必须以本文件为准绳。

---

## 1. 本阶段做了什么 / 没做什么

| 做 | 不做 |
|---|---|
| 冻结业务与部署边界 | 不改交易行为、不改风控阈值、不改接口签名 |
| 记录真实基线（分支、commit、工作区、运行过的检查） | 不建表、不实现多策略子账本、不写迁移脚本 |
| 记录目标进程 / 凭据 / 命令 / 事实 / 查询责任矩阵 | 不引入消息中间件、集群、自动主备 |
| 建立两处安全缺陷的 fake 回归基线（严格 xfail） | 不修这两处缺陷（阶段 1 修） |
| 冻结 `book_id` 子账本数据契约 | 不建 `book` 表、不实现划拨流程 |
| 给出阶段 1–6 的验收项 / 约束 / out_of_scope / 检查命令 | 不启动阶段 1 及之后的任何实现 |

---

## 2. 基线事实（可复核）

### 2.1 版本与工作区状态

| 项 | 值 |
|---|---|
| 当前实施分支 | `feat/foundation-refactor` |
| 阶段 0 原始分支 | `fix/execution-rebooking-observation-gate`（历史基线，后续开发不再使用） |
| 基准 commit | `317b8d92ef49babb7a009a87276e989570e1312e`（`317b8d9 docs: record the observation-window order-blocking fixes`） |
| 阶段 0 开始前的工作区状态 | 仅一个未跟踪文件 `.workbuddy/memory/2026-11-01.md`；该文件**未被读取、未被修改、不属于本任务**，也不得纳入任何提交 |
| 阶段 0 产生的改动 | 本文件、`docs/PROJECT_ARCHITECTURE.md`、`docs/INTERFACE_CONTRACTS.md`（增量）、`trader/tests/test_phase0_foundation_safety.py`（新增） |
| 提交 | `9b01f3a docs(foundation): freeze phase 0 refactor baseline`；独立审核通过后由父 Agent 于 2026-11-02 提交，未 push / 部署 |

阶段 0 之前的历史提交（`317b8d9`、`eff2601`、`01ae777`、`cf84cce`、`4dc7402`）属于观察窗口的
订单阻断修复，与本阶段无关，本阶段不重写、不回退这些历史。

#### 2.1.1 专用重构分支迁移记录（2026-11-02）

- 按用户批准的迁移方案，从当时 HEAD
  `723664ac86e750401c2a431c86413fa2a2831811` 创建并切换到 `feat/foundation-refactor`。
  原始阶段 0 基准 `317b8d9` 保留为历史证据，不改写成迁移时的 HEAD。
- 本阶段四个未提交文件随工作区原样保留；切换前后逐一比较 SHA-256，内容一致。
  随后仅在本文件更新实施分支、审核状态并追加本记录。
- 原分支仍指向 `723664a`；其后续历史提交 `1168270`（代理连接修复）和
  `723664a`（观察窗口验收记录）随新分支继承，但不计入本次基础重构改动。
- 迁移时未使用 stash、复制或删除文件，未提交、推送或改写历史；既有 `.workbuddy/` 不属于本任务，
  未读取、未修改、未暂存。
- 后续开发与审核均使用新分支。原架构师、工程师和审核员代理均已关闭；后续交接须明确
  新分支及当前产物。

#### 2.1.2 阶段 0 独立审核与分支对应关系

- 独立审核在原分支 `fix/execution-rebooking-observation-gate` 上完成，早于本次分支迁移。
  第一轮 A4/A7 未通过，退回同一工程师修正适配路径定位与文档引用；第二轮 A1–A7 全部通过。
- 执行标识：workspace `wks_d919696e0ced8ace`、server `srv_3QDZImHo82uk`、
  架构师 `9b2fc96e-4262-465a-8f4b-2e590eb5ba5e`、工程师
  `1812777d-3595-40a0-b53e-1d89b2421077`、审核员
  `85df3daa-5171-44ef-9f7b-cfdb8bbfa4fd`；工具未提供独立 run ID。
  工程师审核修复记录为 Paseo message `386f161b-835a-4143-a133-2d3cd65a8bf5`，
  二轮审核通过记录为 `691b5543-c0d5-40d4-a7a6-89949e2a6dc4`。
- 审核证据：两个安全基线测试严格 `2 xfailed`，`--runxfail` 的 `2 failed` 准确命中目标缺陷；
  入口/API/装配定向测试 `32 passed`，交易 service 测试 `36 passed`，`git diff --check` 通过。
- 迁移后核对了审核涉及的四份文件和状态，并只追加分支及审核元数据；未再次运行测试，
  也不把原分支的审核描述成新分支上的独立复审。
- 阶段 1 仍需新的工程师实施与独立审核；真实 PG、Compose、备份恢复和外部沙箱验收未运行，
  不因阶段 0 通过而视为已通过。

- 下节记录的是阶段 0 当时的检查结果，不表示迁移后重新运行了这些测试。

### 2.2 阶段 0 实际运行的检查

```
python -m pytest -q trader/tests/test_phase0_foundation_safety.py --tb=short
  -> 2 xfailed（期望值：2 xfailed；XPASS 视为基线失败）

python -m pytest -q trader/tests/test_phase0_foundation_safety.py --runxfail --tb=short
  -> 2 failed（用于确认失败点是目标断言而非 setup 错误）

python -m pytest -q trader/tests/test_runtime_entrypoint.py trader/tests/test_worker_assembly.py \
    trader/tests/test_split_control_runtime.py trader/tests/test_trading_api.py --tb=short
  -> 32 passed（仅既有 Pydantic 弃用警告）

python -m pytest -q trader/tests/test_trading_service.py --tb=short
  -> 36 passed（仅既有 Pydantic 弃用警告）

git diff --check
  -> 无输出，退出码 0

git status --short --untracked-files=all
  -> M docs/INTERFACE_CONTRACTS.md
     M docs/PROJECT_ARCHITECTURE.md
  ?? docs/FOUNDATION_REFACTOR_PHASE0.md
  ?? trader/tests/test_phase0_foundation_safety.py
  ?? .workbuddy/memory/2026-11-01.md   <- 既有未跟踪文件，未读取/未修改/不属于本任务
```

第 2.2 节之外的检查（真实 PostgreSQL 集成、Compose、交易所网络、沙箱长跑）**本阶段未运行**，
不得记为通过，见第 8 节。

### 2.3 持久化模型边界（不是完整事件溯源）

新栈的持久化形态必须被准确描述，**不得**宣称"完整事件溯源"：

| 类别 | 表 | 写入方式 | 权威性 |
|---|---|---|---|
| 账户快照 | `trading_accounts.state`（jsonb） | 每事务 `UPDATE ... SET version, state`（`trader/trading/postgres.py:78`） | **权威**。`read()` 只读这一列（`trader/trading/postgres.py:203-212`），余额、持仓、预约、订单聚合、命令状态、控制状态都在其中 |
| 追加事实 | `trading_executions`、`trading_ledger`、`trading_events` | 事务内 `INSERT`（`trader/trading/postgres.py:48-76`） | 审计与重放证据，**不是**重建账户状态的唯一来源 |
| 追加投递 | `trading_outbox` | `INSERT` + `delivered` 标记更新 | 通知投递队列 |
| 可重建投影 | `trading_commands`、`trading_order_projection` | `ON CONFLICT DO UPDATE`，`rebuild_query_projections()` 可整体重建（`trader/trading/postgres.py:85-131`） | 仅查询投影，可丢弃重建 |
| 协调 | `trading_runtime_leases` | 租约 + fencing token | 唯一执行实例归属 |

结论：**账户快照 + 追加事实 + 可重建查询投影**。账户状态无法仅靠回放 `trading_executions` /
`trading_ledger` 重建（余额、持仓、预约不在追加表中），因此阶段 1–6 不做事件溯源重写，
也不引入事件溯源框架。

### 2.4 目标进程 / 端口 / 凭据 / 责任矩阵（冻结）

| 进程 | 入口 | 监听 | 交易所写凭据 | 账户经济状态写入 | 命令 | 事实入口 | 查询 |
|---|---|---|---|---|---|---|---|
| Control API | `trader/runtime/control_main.py` | `QTS_TRADING_API_PORT`，默认 `8090`，默认绑 `127.0.0.1` | **无**。装配 `NoExchangeIO()`（`control_main.py:27-32`），任何下单调用抛 `CONTROL_PROCESS_CANNOT_EXECUTE` | **无**。只写 `QUEUED` 命令与账户内预约（`defer_send=True`，`control_main.py:110-112`） | 受理 PLACE / CANCEL / CONTROL，落库为 `QUEUED` | **不得**暴露（阶段 1 移除公开事实入口） | 命令状态、订单、风险视图、指标、SSE |
| Execution | `trader/runtime/trading_worker_main.py` | 无 HTTP | **有**。`BINANCE_API_KEY` / `BINANCE_SECRET_KEY`，缺任一即 `BINANCE_CREDENTIALS_MISSING` 拒绝启动（`trading_worker_main.py:176-181`） | **唯一**。账本、持仓、预约、成交、余额 | 消费 `QUEUED` 并发送；持有 `trading_runtime_leases` 租约 | **唯一**。REST 对账快照、WS 成交、订单观测 | 就绪状态、内部指标 |
| Research Worker | `trader/runtime/research_worker_main.py` | 无 | 无（不引用 Binance 凭据） | 无 | 无 | 无 | 发布冻结回测结果（fencing token） |
| History Worker | `trader/runtime/history_worker_main.py` | 无 | 无（只读公开归档） | 无 | 无 | 无 | 导入历史数据集 |
| PostgreSQL | `compose.yml` → `postgres:16` | `5432` | — | 存储 | 协调 | 协调 | 协调 |

坐标约定：

- Control 与 Execution **只通过 PostgreSQL 协调**，不引入消息中间件。
- 生产部署按进程拆分；Control API 与 Trading Runtime 同进程仅限本地开发
  （`docs/PROJECT_ARCHITECTURE.md` 第 8 节既有约束保持不变）。
- 只有 Execution 持有交易所写凭据、只有 Execution 写账户经济状态。
- 旧 `trader/api/main.py`（端口 `8080`）保留只读查询，不保留第二个发送者。

---

## 3. 已核实的两处安全缺陷（阶段 1 修）

两处缺陷都用**公开 service API + 确定性 fake**复现，证据固化在
`trader/tests/test_phase0_foundation_safety.py`。测试断言的是**正确安全行为**，
用 `@pytest.mark.xfail(strict=True)` 固定"当前必须失败"；XPASS 说明缺陷已被意外修掉或断言写错，
按失败处理。

### 3.1 缺陷 A：余额差异被覆盖后，仅清除原因码即可恢复 READY

**当前行为**

1. 首份完整快照 `USDT=1000` 初始化余额与 opening ledger。
2. 第二份新鲜完整快照 `USDT=990` 产生 `BALANCE_MISMATCH`（差异 10 USDT），
   但 `_apply_reconcile_snapshot` 仍把 `state.balances` 覆盖成 `990`
   （`trader/trading/service.py:1199`）。**代码事实**：opening 账目只在首份快照时写
   （`_record_opening_balances` 由 `had_balances` 为假触发，`:1194-1197`），后续快照只覆写
   `state.balances`（`:1199`）而**不写任何 ledger 条目** → 本地 ledger 仍为 `1000`，
   10 USDT 的账实差异被静默吸收且无账目条目解释。
3. `resolve(account_id, "BALANCE_MISMATCH")` 只调用 `_resolve_unresolved(state, code)`
   按**原因码**删除条目（`trader/trading/service.py:519-532`、`1413-1415`），
   没有任何解释证据要求。此后 marks / 对账代际仍新鲜 → 账户回到 `READY`，`reasons=()`。

**正确安全行为**：余额差异在得到解释（可核对的事实或显式调整账目）之前必须保持阻断；
仅按原因码清除不得恢复 `READY`。

**复现**：`trader/tests/test_phase0_foundation_safety.py::test_balance_mismatch_keeps_blocking_until_the_difference_is_explained`
（`--runxfail` 实测：`AssertionError: an unexplained balance difference must keep the account blocked; ledger=1000 venue=990 reasons=()`）。

**正确安全行为的断言位置**（`trader/tests/test_phase0_foundation_safety.py`）：

- `:155-156`：差异产生后 `BALANCE_MISMATCH` 必须在未决原因码中、账户非 `READY`。
- `:163-174`：差异未被解释时，`resolve` 之后账户必须保持非 `READY` 且仍带未决原因码。

**阶段 1 修复方向**（不在本阶段实施）：`resolve` 必须要求解释证据并按差异定向解除；
`_apply_reconcile_snapshot` 不得把未解释的差异写进权威余额。

### 3.2 缺陷 B：一个 UNKNOWN 之后仍继续发送新增风险，且阻断被全局清除

**当前行为**

1. Control 进程（`defer_send=True`）排队两笔 PLACE 命令，两笔都是 `QUEUED`，交易所未收到任何请求。
2. Execution 进程 `resume_queued` 依次发送：第一笔返回 `UNKNOWN`，第二笔返回 `ACCEPTED`。
3. 第一笔进入 `UNKNOWN` 并写入 `UNKNOWN_COMMAND` 未决项、保留预约
   （`trader/trading/service.py:738-754`）。
4. 但 `resume_queued` 的循环**不检查已有 UNKNOWN**，继续发送第二笔（`trader/trading/service.py:299-341`）。
5. 第二笔 `ACCEPTED` 分支调用 `_resolve_unresolved(state, "UNKNOWN_COMMAND")`
   （`trader/trading/service.py:695`），按**原因码账户级**清除了第一笔的阻断；
   `REJECTED` 分支同样如此（`trader/trading/service.py:726`）。

**可复核证据**（全部可用交付命令复现）：

- 第二笔确实被发送：`--runxfail` 实测失败信息为
  `AssertionError: no further new risk may be sent while a command is UNKNOWN; sent=['cmd-1', 'cmd-2']`
  （交付命令：`python -m pytest -q trader/tests/test_phase0_foundation_safety.py --runxfail --tb=short`）。
- 第一笔的阻断被全局清除：**代码事实**。`trader/trading/service.py:695`（`ACCEPTED` 分支）调用
  `_resolve_unresolved(state, "UNKNOWN_COMMAND")`，而 `_resolve_unresolved`（`:1413-1415`）按**原因码**
  删除全部同码条目、不区分 `command_id` → 第一笔写入的 `UNKNOWN_COMMAND`（`:741-745`）被删除，
  账户上不再有该未决原因码。`REJECTED` 分支同理（`:726`）。
- 第一笔命令状态为 `UNKNOWN`、预约保留：**代码事实**。`UNKNOWN` 分支（`:738-754`）只写命令状态与
  未决项，不调用 `_release_remaining_hold`；`ACCEPTED` 分支只写 `record.status = "CONFIRMED"`
  （`:694`）与自身订单观测。

**正确安全行为的断言位置**（`trader/tests/test_phase0_foundation_safety.py`）：

- `:217-220`：UNKNOWN 存在时不得继续发送新增风险（`len(exchange.placed) == 1`）。
- `:222-223`：第一笔命令必须保持 `UNKNOWN`。
- `:225-227`：账户必须非 `READY`，且 `UNKNOWN_COMMAND` 仍在未决原因码中。

这三组断言当前因 `:217` 先失败而未执行到；阶段 1 修复后按顺序全部生效。

**正确安全行为**：存在 UNKNOWN 命令时不得继续发送新增风险；另一条命令成功不得清除它的阻断；
UNKNOWN 的解除必须按 `command_id` 定向。

**复现**：`trader/tests/test_phase0_foundation_safety.py::test_unknown_command_blocks_further_sends_and_keeps_its_own_block`
（`--runxfail` 实测：`AssertionError: no further new risk may be sent while a command is UNKNOWN; sent=['cmd-1', 'cmd-2']`）。

**阶段 1 修复方向**（不在本阶段实施）：`resume_queued` 在存在未决 UNKNOWN 命令时停止发送新增风险；
`_resolve_unresolved` 增加按 `command_id` 定向解除的入口，账户级按码清除只保留给确实全局适用的码。

### 3.3 阶段 1 解除基线的操作

阶段 1 修完后：删除两个 `@pytest.mark.xfail` 标记，两条安全断言转为普通通过断言。
**不得**把当前错误行为改写成通过断言。

---

## 4. 边界冻结

### 4.1 业务范围（冻结，不再扩大）

- 交易所：Binance **现货**、**单账户**、计价资产 **USDT**。
- 订单类型：`MARKET` 与 `GTC LIMIT`；现货，`SHORT` / `CLOSE_SHORT` 明确拒绝。
- 策略：首轮 2–3 个；标的 `BTCUSDT` / `ETHUSDT`；信号只基于 **1 分钟及以上已收盘 K 线**。
- 多策略：固定预算、**独立虚拟资金 / 持仓**；可同币，但**不得借用其他策略资产**；
  同币反向在途冲突**先拒绝**。
- 不做：内部撮合、自动净额、自动再平衡、多账户、合约、组合优化、自动主备。
- 真实资金、生产部署、外部账户操作**不在授权范围**。

### 4.2 一致性与恢复约定（冻结）

- 正常崩溃：已提交记录零丢失（账户快照与追加事实同事务提交）。
- 主机灾难目标：`RPO <= 5m`、`RTO <= 60m`。**证据不足不得标记 READY**。
- 崩溃后已提交命令的 `SENDING`/`UNKNOWN` 不自动重发；只查询、只对账、只解除。
- 无法确认一致性 → `DEGRADED_MODE` 并经 Control Plane 升级 KillSwitch；session 内升级不可逆。

### 4.3 环境

- 开发：Windows（Windows 11 / PowerShell）。
- 运行：Linux 单机 Docker Compose 持续运行。
- 允许：一个 S3 兼容异机备份目标 + 一个支持 webhook / 外部心跳的告警服务。
- 不允许：消息中间件、集群、自动主备、两个并行交易栈。

---

## 5. 保留 / 替换 / 删除清单

每项指向后续阶段；**本阶段不执行任何一项**。

| # | 现状（位置） | 处置 | 目标阶段 | 判据 |
|---|---|---|---|---|
| L1 | 旧 Control API `trader/api/main.py`（端口 8080），`reject_legacy_order_mutation` 中间件（`main.py:1029-1049`）已拒绝 `/v1/orders/*/cancel` 与 `/v1/killswitch` 写；旧 broker 代理与自动恢复已退休 | 保留只读查询 → 删除进程 | 阶段 6 删除；阶段 5 前只读运行 | `trader.api.main` 不再暴露任何写入口；`/v1/killswitch` 写返回 `503 LEGACY_EXECUTION_RETIRED` |
| L2 | 旧 OMS 发送路径 `trader/core/application/oms.py:143` `OMS.submit_order`（当前无生产调用点，仅测试引用） | 删除 | 阶段 6 | 全仓无 `submit_order` 调用点；`trader/tests/test_oms_*` 随之下线 |
| L3 | 旧预算 / 订单服务 `trader/services/execution_budget.py`（仍被 `trader/api/routes/strategies.py` 注入）、`trader/services/order.py`（`OrderService`，被 `orders.py` / `monitor.py` / `reconciler.py` / `killswitch.py` 使用） | 删除 | 阶段 6 | 新栈命令不经过 `ExecutionBudget` / `OrderService`；旧表停写；旧查询迁入 Control API |
| L4 | 旧策略 runner `trader/services/strategy_runner.py`：信号经 `_oms_callback` 下单（`strategy_runner.py:1251`、`:1294-1299`，其中 `:1299` 为 `await self._oms_callback(strategy_id, signal)`）。**该文件不涉及 ticker**；运行期已由 `strategies._legacy_sender_retired`（`trader/api/main.py:1010`）退休，代码路径仍在 | 删除 | 阶段 6 | 只有 `UnifiedStrategyRunner` 产生新风险命令；旧 runner 无生产可达的发送路径；ticker 只属新栈路径（见 L6、L11） |
| L5 | **公开事实入口** `trader/api/routes/trading.py`：`POST /v1/trading/executions`（:302）、`POST /v1/trading/observations`（:284）、`POST /v1/trading/reconcile`（:323）、`POST /v1/trading/resolve/{code}`（:369） | 替换为 Execution 进程内部通道；Control API 不再暴露 | 阶段 1 | Control API 路由表无事实写入口；Execution 是唯一事实写入者；操作员会话无法注入成交/余额事实 |
| L6 | **单策略行情路径** `trader/runtime/trading_worker_main.py:140-162` `feed_market_once` 读 `broker.get_ticker_prices`（实时 ticker，非已收盘 K 线） | 替换为已收盘 K 线（closed bar） | 阶段 3 | 策略只消费 `available_at_ms <= 决策时刻` 的已收盘 bar；未完成 bar 不可见 |
| L7 | **适配路径** `trader/adapters/broker/spot_execution_adapter.py`：symbol 规则缓存读取与写入在 `:97-116`（**缓存写入在 `:115`** `self._rules[symbol] = rules`），下单字段转换在 `:118-168`（MARKET BUY 以 quote 金额结算） | 保留适配职责，替换为统一订单规格入口 | 阶段 2（统一规格） | 策略 → 统一 spec → 适配器 单向；适配器不改本地账本 |
| L8 | **数据资格回退** `trader/runtime/control_main.py:157-159`：`except Exception:` 分支构造 `BacktestApplication(research)`（`eligibility=None`），而 `jobs.py:607-610` 只在 `eligibility is not None` 时校验可信 provenance → 历史存储异常时**资格门禁被整体跳过** | 替换为 fail-closed | 阶段 1（fail-closed），阶段 3（完整资格链） | 历史存储不可用时回测提交被拒，且有明确拒绝码；无 `official_eligible` 的数据集不可提交 |
| L9 | 风险限额来源分裂：新栈准入风控是 `trader/trading/risk.py` 的 `SpotRiskEngine`（订单准入），而 `trader/services/risk.py` 的 `RiskService` 是限额 / 风险事件的配置与持久化服务（`trader/api/routes/risk.py` 在用），两者职责不同但命名易混 | 明确边界 | 阶段 2 | 新栈准入只走 `SpotRiskEngine`；`RiskService` 的限额配置有明确路径进入新栈策略（否则 `max_gross_notional` / `max_symbol_notional` 无配置来源），且不得存在第二套订单准入判断 |
| L10 | 旧账户状态 / 投影表（`account_balances`、`position_lots`、`strategy_positions_proj` 等 `trader/adapters/persistence/*`） | 保留只读 → 删除 | 阶段 6 | 新栈不写旧表；旧表停写后只读保留至阶段 6 |
| L11 | **新栈策略信号 → 订单规格路径** `trader/trading/strategy.py`：`on_market_data` 要求 `signal.symbol == data.symbol`，否则抛 `STRATEGY_SIGNAL_INSTRUMENT_MISMATCH`（`:321-322`，调用点在 `:323-324`）；`_submit_signal` 只接受 runner 声明的 `self.instruments`，否则抛 `UNKNOWN_STRATEGY_INSTRUMENT`（`:349-350`）；`order_type` 取 `signal.metadata.get("order_type", "MARKET")`，**信号未指定时默认 MARKET**（`:352`）；价格按类型分岔——LIMIT 用 `signal.price`，MARKET 用 `reference_price` **取代**信号自带价格（`:353`）；`reference_price` 取自触发该信号的市场数据 `data.price`（`:323-324`），生产路径由 `trader/runtime/trading_worker_main.py:149-162` 的 REST **ticker** 提供 | 替换：统一订单规格 + closed-bar 决策输入 | 阶段 2（统一订单规格）、阶段 3（closed-bar 价格输入） | ① 订单类型与价格来源由统一订单规格入口决定，不再由信号 metadata 隐式默认 MARKET；② LIMIT 的价格仍取信号价、MARKET 的价格来源被显式记录且可审计；③ MARKET 参考价可回溯到具体已收盘 bar 的收盘价与时点，不是不带时点的 ticker 价；④ `signal.symbol == data.symbol` 与 instruments allowlist 语义保持不变 |

---

## 6. `book_id` 数据契约（阶段 2 落地；本阶段只冻结）

### 6.1 标识

- `book_id`：**稳定**的策略子账本标识，形如 `book:{strategy_id}`。
  稳定性含义：`strategy_id` 不随部署、重启、回测批次改变；同一个策略在实盘与回测中
  使用**同一个** `book_id`，使联合回测与实盘可直接对照。
- `deployment_id` 保持"命令归属 / 幂等字段"语义，**不**承担资产隔离职责。
  现状核实：`deployment_id` 只出现在命令与订单归属上，余额 / 持仓 / 预约是账户级
  （`trader/trading/models.py` 的 `AccountState`，:281-297），因此策略间资金隔离当前**不存在**，必须由本契约补齐。

### 6.2 账户与子账本结构

```
账户总额(account_total)
  = 未分配账簿(book_id = UNALLOCATED)
  + 各策略子账本(book_id = book:{strategy_id})
```

- **未分配账簿**：账户初始余额与尚未划拨的资金永久留在未分配账簿。
- **显式划拨**：只有显式划拨命令能把资产从未分配账簿移入某个 `book_id`；
  隐式按策略归集被禁止。
- **逐币种守恒**：对每个资产 `A`，`account_total[A] == UNALLOCATED[A] + Σ book[A]`，
  在同一事务内成立，任一时刻都不得出现差额。
- **双层预约**：一笔新订单在**同一账户事务**内同时完成账户层预约与 book 层预约；
  两层都由同一 `cl_ord_id` 派生，任一层失败则整体回滚。
- **同币反向在途冲突**：同一 `book_id` 上已有未终结的反向在途订单时，新的反向命令**先拒绝**
  （返回明确拒绝码），不排队、不自动净额。
- **不得借用**：某 `book_id` 的可用资产只由该 book 自身的余额与持仓决定，
  不得从账户总余额或其他 book 借用。

### 6.3 归属不明的事实

- 无法明确归属到某个 `book_id` 的成交 / 余额差异：进入**归属不明**状态并**阻断**新增风险，
  记入未决原因码。**不得猜测历史归属**，不得按 symbol、比例、时间顺序等启发式规则分摊。

### 6.4 迁移规则

- 迁移**不猜测历史归属**：存量资产一律留在未分配账簿，直到人工显式划拨。
- 迁移**不清库、不改写历史**：既有 `trading_accounts` 快照、`trading_ledger`、`trading_events`
  原样保留；`book_id` 以新增字段 / 新增投影方式落地。
- 本阶段**不建表、不写迁移脚本、不实现多策略**。

---

## 7. 阶段 1–6 执行计划

通用约定：

- 每阶段必须在其**目标分支**上独立交付，遵循 `AGENTS.md` 的定向测试与 P0 回归要求。
- 每阶段结束前运行其"检查命令"，未运行或环境阻塞的检查**如实记录**，不得标记通过。
- 阶段 1 必须解除 `trader/tests/test_phase0_foundation_safety.py` 的两个 xfail 标记。
- 阶段之间**串行**：后一阶段的验收依赖前一阶段已通过。

### 7.1 分阶段交接工作流（2026-11-02，版本 3）

用户已批准增加独立测试员。完整职责、通知协议、验收证据和重试门禁见
[基础重构代理工作流](FOUNDATION_AGENT_WORKFLOW.md)；运行状态见
[工作流状态](FOUNDATION_WORKFLOW_STATE.json)。本节采用低成本工程师局部闭环，
不改变阶段 1–6 的业务目标与验收编号，也不改写版本 2 已完成的审核历史。

1. **父 Agent → 工程师**：阶段开始时冻结验收与范围，授予本阶段实现和基础测试的写入权。
2. **工程师 → 独立测试员**：实现与基础测试完成后冻结，工程师直接派测试员，不经过父 Agent。
3. **测试/审核结果 → 工程师**：日常通过、失败、blocked 和证据只 handoff 工程师。
4. **测试通过 → 工程师 → 新审核员**：工程师直接创建本轮新审核员；审核 fail 直接回工程师，
   修复后先交测试员复验，再派新审核员。父 Agent 不参与日常转发。
5. **同一 finding 三轮未解 → 父 Agent**：工程师冻结三轮修复与复验证据，父 Agent 分析并给方案；
   换名称、换会话或区分测试/审核轮次均不能重置同一问题的计数。
6. **阶段最终完成 → 父 Agent**：独立测试通过、最新审核 `overall=pass`、指纹与交付齐全后，
   工程师仅一次 handoff 完整阶段结果。父 Agent 核验源文件和必需验证后才创建本地提交。

工程师是阶段内派工协调者，父 Agent 仍是唯一提交者。测试员/审核员是工程师的子 Agent，
完成通知也返回工程师，不能隐性唤醒父 Agent。使用 `create_agent` / `send_agent_prompt`
与完成通知推进，不增加轮询、heartbeat 或常驻服务。同一 workspace 始终只有一个写入者。
不新建 worktree，不 push、merge、部署、清库或触碰 `.workbuddy/`。

版本 2 切换时阶段 2 第 1 轮审核已经 `fail`（原因码回归 C2），历史不得清零。
版本 3 于阶段 2 第 4 轮独立测试通过后、正式审核派工前启用；本次审核开始由工程师接收。

### 阶段 1 — 账务与 UNKNOWN、事实入口与数据资格安全修复

依赖：阶段 0 基线。约束：不引入新表；不改订单规格；不改部署拓扑。

| # | 验收项（可客观判定） |
|---|---|
| 1.1 | `test_phase0_foundation_safety.py` 的两个 xfail 标记已移除且两条安全断言通过 |
| 1.2 | 存在未决 UNKNOWN 命令时，`resume_queued` 不发送任何 PLACE；新命令返回明确拒绝码 |
| 1.3 | UNKNOWN 的解除按 `command_id` 定向；另一条命令成功/失败都不影响它 |
| 1.4 | `resolve` 解除余额差异必须携带解释证据；无证据时账户保持非 READY |
| 1.5 | 余额差异未被解释时，权威余额不得被交易所值覆盖（账实差异保持可见） |
| 1.6 | Control API 路由表不含成交 / 观测 / 对账 / resolve 事实写入口；Execution 是唯一事实写入者 |
| 1.7 | Control 进程不持有交易所写凭据；缺凭据或凭据存在都不影响其只排队不下单 |
| 1.8 | 数据资格回退改为 fail-closed：历史存储不可用时回测提交被拒并有明确拒绝码 |
| 1.9 | 重复 / 乱序 / 并发：同 `exec_id` 重复成交幂等、乱序回报不二次记账、并发 place 只产生一次发送 |
| 1.10 | 四种交易所结果（`UNKNOWN` / `UNAVAILABLE` / `REJECTED` / `NOT_FOUND`）的命令状态、预约与阻断语义均有测试 |
| 1.11 | 真实 PostgreSQL：同账户并发事务下快照与追加表一致，重启后 `read()` 语义不变 |

约束：禁止为了让测试通过而放宽风控；禁止在 Control 侧新增任何交易所 IO。
out_of_scope：多策略子账本、数据资格完整实现、closed-bar 行情。

检查命令：

```text
python -m pytest -q trader/tests/test_phase0_foundation_safety.py --tb=short
python -m pytest -q trader/tests/test_trading_service.py trader/tests/test_trading_api.py \
    trader/tests/test_runtime_entrypoint.py trader/tests/test_worker_assembly.py --tb=short
python -m pytest -q trader/tests/test_binance_connector.py trader/tests/test_binance_private_stream.py \
    trader/tests/test_binance_degraded_cascade.py trader/tests/test_deterministic_layer.py \
    trader/tests/test_hard_properties.py --tb=short
POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:5432/trading \
    python -m pytest -q trader/tests/test_trading_postgres.py trader/tests/test_trading_projection_rebuild.py \
    trader/tests/test_execution_recovery.py --tb=short
```

### 阶段 2 — 统一订单规格 / 状态入口、book 子账本、双层预约与风控

依赖：阶段 1。约束：只推进新栈；不为兼容旧路径新增桥接。

| # | 验收项 |
|---|---|
| 2.1 | 存在唯一的订单规格入口：策略 → 统一 spec → 适配器；策略不得直接构造适配器参数 |
| 2.2 | 订单类型不再由信号 metadata 隐式默认：`trader/trading/strategy.py:352` 当前语义（`signal.metadata.get("order_type", "MARKET")`）被统一 spec 取代，订单类型由显式字段决定并写入命令 |
| 2.3 | 价格来源被显式记录：LIMIT 仍取信号价、MARKET 的参考价来源写入命令 / 事件，可审计复现（替换 `strategy.py:353` 的隐式二选一） |
| 2.4 | `book_id` 契约落地为持久化结构（表 / 投影），`book_id` 稳定且可回溯 |
| 2.5 | 账户总额 = 未分配账簿 + 各策略子账本，逐币种守恒在事务内恒等 |
| 2.6 | 显式划拨命令可用；未划拨资金恒在未分配账簿；无隐式按策略归集 |
| 2.7 | 同一笔订单在**同一事务**内完成账户层与 book 层双层预约，任一层失败整体回滚 |
| 2.8 | 同币反向在途冲突先拒绝（明确拒绝码），不排队、不自动净额 |
| 2.9 | 某 book 不得借用其他 book 或账户总余额的资产 |
| 2.10 | 归属不明的事实阻断新增风险并记入未决原因码；不猜测历史归属 |
| 2.11 | 订单状态只有一个 CAS 入口前进；终态不可回退；`cl_ord_id` / `exec_id` 去重不受影响 |
| 2.12 | 迁移不猜测历史归属、不清库、不改写历史；存量资产留在未分配账簿；多策略联合回测能读出每 book 的独立权益与预算（与阶段 4 衔接） |

约束：禁止自动净额 / 内部撮合 / 自动再平衡；禁止为测试放宽守恒断言。
out_of_scope：数据资格完整链路、Compose 拓扑、遗留清理。

检查命令：

```text
python -m pytest -q trader/tests/test_trading_service.py trader/tests/test_shared_trading_core.py \
    trader/tests/test_oms_idempotency.py trader/tests/test_oms_position_lot_integration.py --tb=short
python -m pytest -q trader/tests/test_phase0_foundation_safety.py --tb=short
POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:5432/trading \
    python -m pytest -q trader/tests/test_trading_postgres.py trader/tests/test_trading_projection_rebuild.py --tb=short
```

### 阶段 3 — closed-Bar / 数据资格 / 预热

依赖：阶段 2。约束：预热失败关闭新增风险；未完成 K 线不可见。

| # | 验收项 |
|---|---|
| 3.1 | 策略只能消费已收盘 K 线；未完成 bar 在 `available_at_ms` 之前不可见（测试证明） |
| 3.2 | MARKET 订单的参考价来自已收盘 bar 的收盘价与时点：`UnifiedStrategyRunner` 的 `reference_price`（`trader/trading/strategy.py:323-324`）不再取自 `trader/runtime/trading_worker_main.py:149-162` 的 REST ticker；参考价可回溯到具体 bar |
| 3.3 | ticker 实时价不再作为决策输入（L6 已替换） |
| 3.4 | 插件预热长度取 `max(部署 warmup_events, 插件声明 warmup_events)`，插件声明是下界 |
| 3.5 | 预热状态机 `PENDING / WARMING_UP / WARM / BLOCKED` 逐标的计数，全部达标才 `WARM` |
| 3.6 | 预热窗口重新采集（不持久化插件内部指标状态）；预热失败 → 阻断新增风险并给出明确原因码 |
| 3.7 | 无可信 provenance（`official_eligible`）的数据集不可提交回测 |
| 3.8 | 数据资格回退路径已删除（无 `eligibility=None` 的提交入口） |
| 3.9 | 数据集共享同一已收盘 bar 日历与计价资产；不满足时提交被拒 |
| 3.10 | 阶段 1 的 fail-closed 拒绝码在真实 PG 上被验证（需按下方说明新增真实 PG 用例） |
| 3.11 | 数据资格失败不会回退到"只看 source 字符串"的宽松判断 |

约束：不得用 REST 快照代替已收盘 bar；不得静默降级为宽松资格。
out_of_scope：多策略联合回测、生产 Compose、遗留清理。

检查命令：

```text
python -m pytest -q trader/tests/test_strategy_warmup.py trader/tests/test_backtest_dataset_resolution.py \
    trader/tests/test_backtesting_data_pipeline.py trader/tests/test_research_safety.py --tb=short
python -m pytest -q trader/tests/test_unified_strategy_runner.py trader/tests/test_worker_assembly.py --tb=short
POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:5432/trading \
    python -m pytest -q trader/tests/test_history_dataset_store_pg.py \
    trader/tests/test_research_history_bridge_pg.py --tb=short
```

真实 PG 覆盖现状（已核对现有文件，未虚构）：

- `trader/tests/test_history_dataset_store_pg.py::test_dataset_requires_published_and_persisted_state`
  ——真实 PG 中数据集未 `PUBLISHED` / `persistence_ok` / `backtest_eligible` 时抛 `DatasetNotEligibleError`。
- `trader/tests/test_research_history_bridge_pg.py::test_publish_writes_dataset_link_and_history_state_in_one_transaction`
  ——真实 PG 中发布后 `official_eligible=true`、`backtest_eligible=true`、provenance hash 一致。

**阶段 1 / 3 新增的 fail-closed 拒绝码（历史存储不可用或无可信 provenance 时拒绝回测提交）当前没有
真实 PG 用例覆盖**。阶段 3 必须新增一条真实 PG 用例断言该拒绝码在 PG 上生效，并把
`trader/tests/test_research_history_bridge_pg.py` 纳入 `.github/workflows/ci-gate.yml` 的
`postgres-integration` job（当前 CI 只跑 `test_history_dataset_store_pg.py`）。

### 阶段 4 — 多策略联合回测 / 冻结 manifest / 一致性

依赖：阶段 2、阶段 3。约束：manifest 冻结后不可变；同一冻结输入必须产生同一结果。

| # | 验收项 |
|---|---|
| 4.1 | 联合回测同时运行 ≥2 个策略，共享账户现金但按 book 隔离资金与持仓 |
| 4.2 | 联合回测的每 book 权益、预算占用、手续费可单独导出 |
| 4.3 | 冻结 manifest 包含 book 分配、初始资金、成本参数、runtime 版本与 dataset 集合 |
| 4.4 | 相同冻结 manifest 重复运行 `manifest_hash` 恒等 |
| 4.5 | manifest 与实盘命令参数一一对应（symbol、side、qty、price 口径一致） |
| 4.6 | 联合回测与实盘共用同一纯核心准入规则（`trader/trading/core.py`），无第二套经济实现 |
| 4.7 | 联合回测中某策略触发预算上限时，别的策略不受影响，且该策略被明确拒绝 |
| 4.8 | 跨进程一致性：Control 提交 → Research Worker 执行 → 结果发布，在真实 PG 上可复核 |
| 4.9 | 回测不可访问实盘账户经济状态（无真实订单写入） |
| 4.10 | 数据集与 manifest 的溯源字段齐全（dataset id、provenance hash、runtime version） |

约束：回测不得写实盘账本；manifest 一旦冻结不得就地修改。
out_of_scope：生产 Compose、遗留清理、实盘切换。

检查命令：

```text
python -m pytest -q trader/tests/test_backtest_risk_integration.py trader/tests/test_backtest_risk_replay.py \
    trader/tests/test_event_driven_risk_replay.py trader/tests/test_backtesting_validation.py --tb=short
python -m pytest -q trader/tests/test_research_runtime.py trader/tests/test_research_worker_lifecycle.py --tb=short
POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:5432/trading \
    python -m pytest -q trader/tests/test_research_postgres.py trader/tests/test_unified_strategy_postgres.py --tb=short
```

### 阶段 5 — Compose / 监控 / 备份 / 故障恢复

依赖：阶段 1–4。约束：单机 Linux Compose；不引入消息中间件；证据不足不得 READY。

| # | 验收项 |
|---|---|
| 5.1 | `compose.yml` 覆盖 postgres + control + execution + research + history 五个服务 |
| 5.2 | Control 容器内**没有**交易所写凭据（环境变量缺席即 fail-closed，有检查脚本或测试证明） |
| 5.3 | Execution 容器持有凭据且缺凭据时拒绝启动并给出明确原因码 |
| 5.4 | `/health/live`、`/health/ready`、`/health/trading` 语义区分；交易未就绪返回 `503 + reasons` |
| 5.5 | 告警服务收到 webhook / 外部心跳；阻断类原因码（`UNKNOWN_COMMAND`、`BALANCE_MISMATCH`、租约失效）必达 |
| 5.6 | PG 备份落到一个 S3 兼容异机目标，备份频率满足 `RPO <= 5m` |
| 5.7 | 恢复演练实测 `RTO <= 60m`，并留下可复核的演练记录（时间戳 + 证据） |
| 5.8 | 崩溃后已提交记录零丢失：重启后命令 / 成交 / 账本条目数量与崩溃前一致 |
| 5.9 | 灾难恢复证据不足时系统保持非 READY（不得自动放行） |
| 5.10 | 演练脚本可重复执行且不访问真实资金（`scripts/rehearse_trading_runtime.py` 扩展） |
| 5.11 | 租约失效立即关闭本实例 ready；冲突实例不得进入对账或发送 |

约束：单机；不引入集群 / 自动主备；不得为通过验收而放宽就绪判定。
out_of_scope：实盘切换、遗留清理、外部沙箱长跑。

检查命令：

```text
docker compose up -d && docker compose ps
python -m pytest -q trader/tests/test_runtime_observability.py trader/tests/test_monitor_service.py \
    trader/tests/test_runtime_entrypoint.py trader/tests/test_split_control_runtime.py --tb=short
python scripts/rehearse_trading_runtime.py
POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:5432/trading \
    python -m pytest -q trader/tests/test_split_control_postgres.py trader/tests/test_recovery_coordinator.py --tb=short
```

### 阶段 6 — 持续沙箱与遗留清理

依赖：阶段 5。约束：只保留新栈；两套并行即失败。

| # | 验收项 |
|---|---|
| 6.1 | 连续 ≥24h 沙箱运行，期间无未解释的 `unresolved` 累积、无重复记账、无非预期重发 |
| 6.2 | 私有 WS 覆盖证明在沙箱窗口内成立（`PRIVATE_STREAM_COVERAGE_UNPROVEN` 不出现） |
| 6.3 | L1 旧 `trader/api/main.py` 进程删除，只读查询迁入 Control API |
| 6.4 | L2 旧 OMS `submit_order` 路径删除，生产代码零引用 |
| 6.5 | L3 旧 `ExecutionBudget` / `OrderService` 路径删除 |
| 6.6 | L4 旧策略 runner 删除，只有 `UnifiedStrategyRunner` 产生新风险命令 |
| 6.7 | L5 事实入口在 Control API 上确认不存在（路由表断言） |
| 6.8 | L8 数据资格回退在代码库中确认不存在（无 `eligibility=None` 提交入口） |
| 6.9 | L10 旧账户 / 投影表停写，只读保留或删除有明确结论与记录 |
| 6.10 | 沙箱演练命令可重复：`scripts/observe_trading_runtime.py` 产出可复核 JSONL |
| 6.11 | 全仓无第二套下单 / 账务实现（架构一致性检查通过） |
| 6.12 | 遗留清理后 P0 + 交易核心回归全绿 |

约束：清理前必须有对应的新栈能力；不得"先删后补"。
out_of_scope：实盘资金、生产部署、组合优化、多账户 / 合约。

检查命令：

```text
python -m pytest -q trader/tests/test_architecture.py --tb=short
python -m pytest -q trader/tests/test_binance_connector.py trader/tests/test_binance_private_stream.py \
    trader/tests/test_binance_degraded_cascade.py trader/tests/test_deterministic_layer.py \
    trader/tests/test_hard_properties.py --tb=short
python -m pytest -q trader/tests/test_trading_service.py trader/tests/test_trading_api.py \
    trader/tests/test_runtime_entrypoint.py trader/tests/test_worker_assembly.py \
    trader/tests/test_split_control_runtime.py trader/tests/test_phase0_foundation_safety.py --tb=short
python scripts/observe_trading_runtime.py --help
```

---

## 8. 本阶段未运行的检查（如实记录，不得称为通过）

| 检查 | 原因 | 归属阶段 |
|---|---|---|
| 真实 PostgreSQL 集成测试 | 本阶段不涉及持久化改动，本机未准备带 DSN 的集成环境 | 阶段 1 起按各阶段检查命令执行 |
| Docker Compose 启停与进程拓扑 | 本阶段不改部署拓扑 | 阶段 5 |
| 备份 / 恢复演练（RPO / RTO） | 本阶段无备份与恢复实现 | 阶段 5 |
| Binance 网络 / 私有 WS / 覆盖证明 | 真实资金与外部账户不在授权范围；本阶段无网络测试 | 阶段 6 |
| 24h 持续沙箱 | 需要外部沙箱长跑 | 阶段 6 |

阶段 0 实际运行的检查见第 2.2 节；其结果以第 2.2 节记录的输出为准。

---

## 9. 阶段 1 审核与提交前复验记录（2026-11-02）

本节是父 Agent 在独立审核通过后追加的交付记录，不改写阶段 0 的历史基线，
不修改已审核实现，也不表示审核员再次审查过本节元数据。

### 9.1 审核对象与结论

- 分支：`feat/foundation-refactor`；阶段 1 base：
  `9b01f3af43bfdcd7c44a571c312d76a547e6f37a`。
- 父 Agent：`18e99d90-33f5-41f9-8a2f-672d109886bb`；工程师：
  `1b9469de-4468-4819-b1e7-6c5f80698eea`；独立审核员：
  `64d99943-8e6e-4185-ad69-cbb6fc7cbede`。
- 审核报告：Paseo message `91a2ee26-ddfb-4ac9-8bcc-680fe7021eb2`，
  **第 1 轮 overall=pass，1.1–1.11 全部通过**。工程师确认冻结文件的报告为
  `3533495e-5455-4b5e-a432-2f23e0dfaa1e`。
- 关闭 D1 余额覆盖与 D2 UNKNOWN 门禁缺陷；余额调整通过携带证据的 `RESOLVE`
  命令完成；公开事实写入口已移除；Control 的历史存储装配及资格检查异常均拒绝研究提交。
- 本次交付共 14 个文件：6 个实现、4 个测试、4 个文档，包含新增
  `trader/tests/test_phase1_foundation_safety.py` 和父 Agent 的本文件工作流增量。
  工程师与审核员均未提交；父 Agent 按 7.1 节只创建本地新提交，不 push。
  `.workbuddy/` 未读取、未修改、未纳入提交范围。

### 9.2 验证来源与实际结果

| 执行者 | 检查 | 结果 |
|---|---|---|
| 独立审核员 | 阶段 0/1 安全测试 | 23 passed |
| 独立审核员 | service / API / runtime entrypoint / worker assembly | 58 passed |
| 独立审核员 | P0 五套件 | 99 passed |
| 独立审核员 | split control / research safety / trading runtime | 42 passed |
| 独立审核员 | 其余 CI trading-core 17 套件 | 144 passed |
| 独立审核员 | PG / 投影重建 / execution recovery 三套件 | 20 passed，0 skipped，包含真实 PostgreSQL 16 验证 |
| 独立审核员 | Core 无 IO、全 `trader/` isort / Black、`git diff --check` | 通过 |
| 父 Agent | 阶段 0/1 安全测试复跑 | 23 passed；两个原 xfail 已转普通断言 |
| 父 Agent | PG / 投影重建 / execution recovery 三套件复跑 | 20 passed，0 skipped，真实本地 PG 可用 |
| 父 Agent | Core 无 IO、10 个变更 Python 文件 isort / Black、`git diff --check` | 通过 |

父 Agent 的复跑命令：

```powershell
python -m pytest -o addopts='' -q trader/tests/test_phase0_foundation_safety.py trader/tests/test_phase1_foundation_safety.py --tb=short
$env:POSTGRES_CONNECTION_STRING = 'postgresql://trader:trader_pwd@127.0.0.1:5432/trading'
python -m pytest -o addopts='' -p no:cacheprovider -q trader/tests/test_trading_postgres.py trader/tests/test_trading_projection_rebuild.py trader/tests/test_execution_recovery.py --tb=short -ra
python scripts/check_core_no_io.py
git diff --check
```

isort / Black 只检查本次变更的 10 个 Python 文件；父 Agent 没有重新运行上表中的
58 / 99 / 42 / 144 项套件，采用独立审核时已冻结代码的验证证据。
父 Agent 的安全测试有两条既有 Pydantic 弃用警告及一条 pytest 缓存目录写入权限警告，
退出码仍为 0；PG 复跑禁用缓存插件，20 项执行完成，无 skip。

### 9.3 留给后续阶段的限制

- 组合开发入口 `trader/runtime/trading_main.py` 的无 checker 构造，以及
  `trader/api/routes/research.py` 的 retry 资格复核，仍属阶段 3 / 6；
  不能把阶段 1 的 Control 修复解释为全仓资格路径均已收敛。
- 独立审核记录的覆盖缺口：`RESOLVE_EVIDENCE_STALE` 专项、UNKNOWN 下撤单可用、
  CANCEL 的 UNKNOWN / UNAVAILABLE 参数化、带凭据 Control lifespan 的 HTTP 负向链路。
  这些不是已通过的测试；后续涉及相关逻辑时补足。
- 旧快照中不带 `scope` 的 `UNKNOWN_COMMAND` 需在后续迁移中处理；
  不得猜测命令归属或直接清除阻断。
- Compose、前端构建、完整 CI、Binance 网络、私有流及 24h 沙箱没有在本阶段运行，
  也不因本阶段通过而视为验收通过。
- 下一阶段仅在本阶段本地提交成功后启动，执行 2.1–2.12；
  保留“审核通过后父 Agent 提交，再交接下一阶段；三轮未通过即暂停”的门禁。
