# 阶段 2 独立验收测试报告（独立测试员）

- 角色：独立测试员（tester），`agent_id = 29822745-df5a-4e5e-b5f7-f66230c24ddd`
- 工作流：`workflow_version = 2`，`phase = 2`，`test_attempt = 1`（测试批次，非正式审核轮次）
- 分支：`feat/foundation-refactor`；base/HEAD：`1c587f33c65fda0f95d8992d619b5b1b209f84ed6`
- 输入快照：`artifacts/foundation/phase2/tester-input-snapshot-v4.json`
- 输入 `source_digest`：`17102372d72947eef1b09efe3d1cc694f417dfe9a26491068790cb12c03d195b`
- 本报告文件本身与 `docs/FOUNDATION_WORKFLOW_STATE.json` 均被排除在源摘要之外（父 Agent 运行元数据 / 自引用）

> 本文先记录**独立于实现的测试设计**（第 1 节：契约预期与手算数字），再记录实现与验证证据（第 2 节起）。
> 期望值全部由 `docs/INTERFACE_CONTRACTS.md` 13.1–13.12、13.9、`docs/PHASE2_BOOK_LEDGER_MIGRATION.md`
> 与 `docs/FOUNDATION_REFACTOR_PHASE0.md` 的 2.1–2.12 推导并手工计算，**不调用被测 production
> calculation 生成期望值**，也不采用工程师既有测试的通过结论作为验收依据。

---

## 1. 测试设计（契约 → 手算预期）

### 1.0 统一基线（所有用例共用，手算口径）

| 项 | 值 | 来源 |
|---|---|---|
| 账户 | `acct-indep` | 本测试专属，避免与既有测试/真实账户混淆 |
| symbol / base / quote | `BTCUSDT` / `BTC` / `USDT` | — |
| `fee_rate` | `0.001` | `RiskPolicy` 默认；本测试显式传入 |
| BUY 预约额 | `qty * price * (1 + fee_rate)` | 13.2「卖出可预约资产」+ `core.release_hold` 的 BUY 分支口径；BUY 侧按含费上界预约 |
| 固定时钟 T0 | `1_700_000_000_000` | fake clock，确定性 |
| mark | `BTCUSDT = 100 @ T0` | 手工给定，不取被测实现 |

**BUY 预约额推导（手算）**：买入 `qty=2, price=100` 需支付 `2*100 = 200` quote，
费用上界 `200 * 0.001 = 0.2`，故预约 `200.2`。这是本测试所有"够/不够"边界的分母。

---

### 1.1 T1 — 跨 book 隔离（关键反例：A=100 / B=900 / 账户=1000，A 申请 200）

契约：13.2「同一订单的账户层 hold 与 book 层 hold 在同一事务内创建」，`books.book_available`
只统计该 book 自身余额；2.9「book 不借用其他 book 或账户余额」。

| 步骤 | 手算预期 |
|---|---|
| recover 账户 1000 USDT | `state.balances[USDT] = 1000`；`UNALLOCATED = 1000` |
| 显式划拨 A=100、B=900 | `UNALLOCATED=0`、`alpha=100`、`beta=900`；账户总额仍 1000 |
| A 提交 BUY qty=2 price=100（需 200.2） | **拒绝**。账户层 1000 ≥ 200.2 通过；book 层 100 < 200.2 拒绝 |

断言（返回值 + 副作用分别断言）：
1. 返回值：抛出 `RiskRejected`，消息含 `BOOK_INSUFFICIENT_AVAILABLE_BALANCE`；
2. **venue 零 PLACE**：`exchange.place_calls == []`；
3. 无意外命令/订单/预约残留：`commands` 中无该 command_id、`orders == {}`、
   `state.holds == {}`、`alpha.holds == {}`、`beta.holds == {}`；
4. B 不被借用：`beta.balances[USDT] == 900`；A 不变 `100`；`UNALLOCATED == 0`；账户 `1000`；
5. **逐币种守恒**：`conservation_mismatch(state) == {}`（账户总额守恒不等于 book 归属正确，故单列）。

正向对照（证明边界确实是"本 book 自有余额"而非账户总额）：A 提交 BUY qty=0.9 price=100
需 `90.09 ≤ 100` → 受理，且 `alpha.holds` 与 `state.holds` 均 = `90.09`。

### 1.2 T2 — 双层预约 / 成交记账中途故障原子回滚

契约：13.2「任一层失败都回滚整个账户事务」，`core.reserve_order` 注释「book 层是同一工作单元的一部分」。

**实验 A（预约层）**：book 层 `reserve_book_hold` 注入失败 → 事务内抛错 →
断言 `orders`、`state.holds`、`book.holds` 三者**全部为空**（账户层 hold 也被回滚，不是"半预约"）。

**实验 B（成交记账层）**：`release_book_execution_hold` 注入失败（发生在 ledger/position 已写入之后）→
断言回滚后：`state.balances`、所有 book 余额、`state.ledger`、`state.executions` 均回到成交前快照。

真实 PG 版本见 `test_phase2_independent_postgres.py`（唯一临时 schema、真实事务隔离）。

### 1.3 T3 — 部分成交 / 三种费用币种 / 撤销与 UNKNOWN 剩余预约 / 逐币种守恒

场景：`alpha` 划拨 1000 USDT；BUY LIMIT `qty=2, price=100` → 预约 `200.2`。

| # | 成交 | 手算：账户/alpha 余额 | 剩余 hold |
|---|---|---|---|
| 1 | `qty=1, price=100, fee=0.05 USDT` | USDT `1000-100-0.05 = 899.95`；BTC `0+1 = 1` | 释放 `1*100*1.001=100.1` → `200.2-100.1 = 100.1` |
| 2 | `qty=0.5, price=100, fee=0.02 BTC`（base 币种费） | USDT `899.95-50 = 849.95`；BTC `1+0.5-0.02 = 1.48` | 释放 `0.5*100*1.001=50.05` → `100.1-50.05 = 50.05` |
| 3 | `qty=0.1, price=100, fee=0.01 BNB`（第三币种费） | USDT `849.95-10 = 839.95`；BTC `1.48+0.1 = 1.58`；BNB `0-0.01 = -0.01` | 释放 `0.1*100*1.001=10.01` → `50.05-10.01 = 40.04` |

持仓（BTCUSDT）：成交 1 `+1`（费为 USDT 不减 base）；成交 2 `+0.5-0.02 = +0.48`；
成交 3 费为 BNB 不减 base → `+0.1`。合计 `1 + 0.48 + 0.1 = 1.58`，与 BTC 余额一致。

断言：
- 三层 hold 数字**逐项**等于手算值（`state.holds` 与 `alpha.holds` 相等且同步）；
- 逐币种守恒对 USDT / BTC / **BNB（含 -0.01）** 分别成立（第三币种费不得被漏记或改记到 USDT）；
- **不漏记真实成交**：重复同一 `exec_id` 是 no-op（返回 `False` 且余额不变）；
  同 `exec_id` 不同内容是 `EXECUTION_KEY_CONFLICT`（`Conflict`）；乱序/迟到成交仍入账；
- 撤销：观察 `CANCELLED` → 两层剩余 hold 一起释放，余额不变；
- UNKNOWN：下单返回 `UNKNOWN` → **两层 hold 都保留**（不是只留一层），命令 `UNKNOWN`，
  后续新风险被 `UNKNOWN_COMMAND_PENDING` 拒绝。

### 1.4 T4 — 迁移不猜历史 / 不改写原事实

构造**旧快照**（无 `books`）：余额 USDT 1000、一条 `OPEN` 旧订单（`book_id=""`）、
账户层 hold `100.1` USDT、1 条 ledger（opening_balance）、1 条 event、1 条无 scope 的旧 `UNKNOWN_COMMAND`。

断言：
1. `UNALLOCATED` 承接全部存量：`UNALLOCATED.balances[USDT] == 1000`；
2. **原追加事实保持**：ledger 条目数与 `entry_id` 不变、event 不变、订单 command（意图）不变、余额不变；
3. **不猜归属**：`BOOK_ATTRIBUTION_UNKNOWN` 进入 reasons，且旧订单被记入
   `book_ledger` 标记的 `unattributed_orders`，`UNALLOCATED` 不等于"已归属"；
4. 新风险被阻断（不是静默放行）；
5. 重复迁移幂等：再次 `decode_state` / 再次写事务，`unattributed_orders` 集合不变；
6. 无 scope 旧 `UNKNOWN_COMMAND`：`detail.command_id` 对应不到已持久化 PLACE 命令 →
   保留为 `LEGACY_UNKNOWN_COMMAND_UNATTRIBUTED`，**不清除**；能对应 → 补 scope；
7. **解除与显式划拨边界**：`RESOLVE`（`BOOK_ATTRIBUTION_UNKNOWN`）只清除阻断、**不移动余额**；
   只有显式 `ALLOCATE` 才移动余额。逐单证据不完整（缺订单 / 订单未 FILLED / 仍有 hold）→
   `RESOLVE_EVIDENCE_*` 拒绝。

### 1.5 T5 — 规格显式性 / 价格来源时点 / 幂等终态 / Control→Execution 入口

- `order_type` 缺失 → `ORDER_TYPE_REQUIRED`；`signal.metadata` 携带 `order_type` **不被采纳**（2.2）；
- LIMIT：`price = signal.price`（信号价）、`price_source = SIGNAL_LIMIT`、`price_at_ms = decision_at_ms`；
- MARKET：`price = reference_price`、`price_source` 必须是 4 种 market 来源之一、
  `price_at_ms = reference_at_ms`；把 `SIGNAL_LIMIT` 用于 MARKET → `INVALID_ORDER_PRICE_SOURCE`；
- `price_at_ms = -1` → `ORDER_PRICE_TIME_REQUIRED`；`book_id = UNALLOCATED` → `UNALLOCATED_BOOK_CANNOT_TRADE`；
- 幂等：同 `command_id` 同经济参数返回原 receipt 且不产生第二次 venue 调用；
  同 `command_id` 不同 `book_id`/`price_source` → `COMMAND_ID_REUSED_WITH_DIFFERENT_PARAMETERS`（2.2/13.1）；
- 终态不回退：`FILLED` 后再观察 `CANCELLED` → `Conflict`；重复/迟到观察是幂等 no-op；
- **Control→Execution 入口**：`defer_send=True` 的服务（Control 语义）只把 `ALLOCATE` 落为 `QUEUED`，
  **不写任何经济状态**（`UNALLOCATED` 余额不变、无 `book.allocated` 事件、零 venue 调用）；
  `resume_queued` 在 Control 侧抛 `NotReady(CONTROL_PROCESS_CANNOT_EXECUTE)`；
  由非 defer 服务（Execution 语义）`resume_queued` 才真正划拨并产出 `book.allocated` 事件。
  不声称阶段 3 的 closed-bar/资格链已完成。

### 1.6 T6 — C2 精确估值拒绝码回归（及其他阻断不回退）

C2 缺陷：`place → _migrate_books → _refresh_status` 提前置账户 `BLOCKED`，
使 `risk` 先返回 `ACCOUNT_NOT_RECONCILED`，**掩盖** `VALUATION_STALE_OR_MISSING`。

独立复现设计（不复用工程师用例，自己构造）：
- `policy = RiskPolicy(max_reconciliation_age_ms=600_000, max_mark_age_ms=60_000, fee_rate=0.001)`；
- 直接向 store 种入**旧快照**（`books == {}`，且余额含 `BTC=1`，使 mark 新鲜度具备经济意义）；
- 在 mark 仍新鲜时执行划拨：`_migrate_books` 真正发生（首个写事务），且持久化 `status` 仍为 `READY`；
- 时钟前进 `120_000`：`> max_mark_age_ms(60_000)` 但 `< max_reconciliation_age_ms(600_000)`；
- 再提交 BUY → 期望 **`NotReady("VALUATION_STALE_OR_MISSING")`**，
  并显式断言**不是** `ACCOUNT_NOT_RECONCILED`（精确原因码不被泛化码掩盖）；
- `risk_view().reasons` 含 `VALUATION_INCOMPLETE`；
- 反向对照：把时钟拨回 T0（mark 重新新鲜），**同一笔订单必须被受理**
  → 证明失败原因是估值过期，而不是本用例自身搭错环境。

同时断言其他阻断不回退（2.6/2.10/2.11 相关）：
- 存在未决 `UNKNOWN` PLACE → 新 PLACE 抛 `NotReady("UNKNOWN_COMMAND_PENDING")`，venue 不被二次调用；
- 存在 `BOOK_ATTRIBUTION_UNKNOWN` → 在**准入引擎边界**（`SpotRiskEngine.evaluate`）精确返回
  `BOOK_ATTRIBUTION_UNKNOWN` 并拒绝。注：一旦该差异被落库为 `unresolved`，`place()` 会先命中更早的
  "存在未决差异"检查而返回 `UNRESOLVED_ACCOUNT_DIFFERENCES`；两者都是 `NotReady` 阻断，
  因此用例对 `place()` 只断言 `NotReady`，精确码断言放在引擎边界；
- 未对账（`checked_at_ms` 缺失，即 `reconciliation` 为空）→ `ACCOUNT_NOT_RECONCILED`，
  `risk_view().status == "RECOVERING"`。

> 父 Agent 本轮已在受控环境执行 `test_trading_service.py`（36 passed / 0 skipped，含
> `test_stale_valuation_blocks_new_risk`），作为**环境证据**；该执行 `executed_by=父Agent`，
> 不是独立测试的通过结论，也不替代本项独立复现。

### 1.7 T7 — 受控变异（至少两处），证明独立测试能命中目标业务断言

方法：`monkeypatch` 限定在**测试进程内**的模块函数，自动还原；**不写任何共享生产文件**。
每个变异实验单列一个用例，断言"变异被观察到"（明确的可观测差异），而不是"抛了异常"。

| # | 注入点 | 模拟的错误实现 | 正常实现下 oracle | 变异下必须被观察 |
|---|---|---|---|---|
| M1 | `books.book_available` | 返回账户层可用余额（book 可借用账户/其他 book） | A=100 申请 200.2 → 拒绝、零 venue 调用 | 同一场景被**受理**（venue 收到 PLACE）→ oracle 断言失败 |
| M2 | `books.reserve_book_hold` | 变成 no-op（只建账户层预约） | 两层 hold 同时存在且金额相等 | 账户层 hold 存在而 `alpha.holds` 为空 → oracle 断言失败 |
| M3 | `books.release_book_execution_hold` | 变成 no-op（成交后不释放 book 层预约） | 成交后两层 hold 同步递减 | book 层 hold 不减、`available` 被高估 → oracle 断言失败 |

"正常实现通过"由同一批 oracle 在未注入时的通过结果证明（M1/M2/M3 各配一条正常用例）。

### 1.8 T8 — CI 与可复核产物

- `trader/tests/test_phase2_independent_acceptance.py` → 追加到 `trading-core-gate`（既有 job，只追加命令）；
- `trader/tests/test_phase2_independent_postgres.py` → 追加到 `postgres-integration`（既有 job，只追加命令）；
- **不改动**既有门禁的其它命令、矩阵或触发条件；
- 产物：原始 stdout/stderr、退出码、JUnit XML 保存在 `artifacts/foundation/phase2/`。

已落盘的产物与文件（全部在测试员写入白名单内）：

| 文件 | 说明 |
|---|---|
| `trader/tests/test_phase2_independent_acceptance.py` | T1–T7 + G 约束（内存 store / fake clock / 录制式 venue port） |
| `trader/tests/test_phase2_independent_postgres.py` | 真实 PostgreSQL：唯一临时 schema，重启/回滚/并发/迁移幂等 |
| `.github/workflows/ci-gate.yml` | 仅追加两行 pytest 命令到既有 job |
| `artifacts/foundation/phase2/tester-execution-request-1.json` | 给父 Agent 的 argv 数组、cwd、必需环境变量名、预期与建议输出路径 |

### 1.9 PostgreSQL 用例的隔离与"不跳过"约定

- 每个用例自建 `qts_trading_test_<uuid12>` schema，并用 `search_path` 指向它，`finally` 中 `DROP SCHEMA ... CASCADE`；
  不使用默认 schema，不触碰任何既有运行时/账户数据。
- DSN **不写入测试源码**：优先取 `POSTGRES_CONNECTION_STRING`，未设置时回落到既有套件使用的 DSN。
- 必需 PG 用例在数据库不可达时 **`pytest.fail`**（失败），**绝不 `pytest.skip`**；
  因此"PG 不可达"会以失败呈现，不会被误读为通过。

---

## 2. 实现与验证证据

### 2.0 本轮分工（严格区分 authored / executed / analyzed）

| 环节 | 执行者 | 状态 |
|---|---|---|
| 测试设计与用例编写（**authored_by**） | 独立测试员 `29822745-df5a-4e5e-b5f7-f66230c24ddd` | 已完成，仅用既有文件读写工具 |
| 命令执行（**executed_by**） | 父 Agent `18e99d90-33f5-41f9-8a2f-672d109886bb` | **已完成**（R0–R4，见 `artifacts/foundation/phase2/parent-execution-result-1.json`） |
| 结果分析（**analyzed_by**） | 独立测试员 | **已完成**（见本报告 §4） |

依据 `docs/FOUNDATION_AGENT_WORKFLOW.md` §2.1：子 Agent 的命令执行被 ACL 阻断，测试员不代跑命令。
本轮 R0–R4 由父 Agent 在 `branch=feat/foundation-refactor`、
`base/HEAD=1c587f33c65fda0f95d8992d6195b1b209f84ed6` 上受控执行；执行时源码快照
`artifacts/foundation/phase2/tester-output-snapshot-execution-1.json`，
`source_digest=aedcec5682f4f5b219d4c2f5e0cc3506a815f4146ecee3da771a3af2fa8736c7`。

### 2.1 父 Agent 已执行的命令与真实退出码

| id | 命令（摘要） | 退出码 | 原始证据 |
|---|---|---|---|
| R0 | `pytest --collect-only -q test_phase2_independent_acceptance.py` | **0**（实际收集 **26** 项；报告初稿估计 22 项，以实际为准） | `indep-acceptance-collect.{log,exit}` |
| R1 | `pytest -q --tb=short -rA --junitxml=... test_phase2_independent_acceptance.py` | **1** | `indep-acceptance.{log,xml,exit}` |
| R2 | `pytest -q --tb=short -rA --junitxml=... test_phase2_independent_postgres.py` | **0** | `indep-postgres.{log,xml,exit}` |
| R3 | `black --check --line-length 100 <两个新文件>` | **1**（两份文件均需格式化） | `indep-format.{log,exit}` |
| R4 | `isort --check-only --profile black <两个新文件>` | **0** | `indep-isort.{log,exit}` |

### 2.2 真实计数（取自 JUnit XML，不是估算）

| 批次 | tests | passed | failed | errors | skipped |
|---|---|---|---|---|---|
| R1 验收（内存 store，含变异实验） | 26 | **24** | **2** | 0 | **0** |
| R2 验收（真实 PostgreSQL） | 5 | **5** | 0 | 0 | **0** |

两项失败原文照录（括号内为当时的文件行号）：

1. `test_t3_genuine_fills_are_commutative_and_never_dropped`（`:510`）
   `AssertionError: assert Decimal('1.59') == Decimal('1.58')`
2. `test_t7_mutation_book_hold_not_released_on_fill_is_detected`（`:1057`）
   `KeyError: 'qts-1adde27c50762491d1db6cca'`，
   断言原文 `assert state.books[BOOK_A].holds[receipt.cl_ord_id].amount == Decimal("100.1")`

> 两项失败**均未**使用 skip / xfail / 容差 / 宽泛异常捕获，均为硬失败，符合 G3 的"不吞错"要求。
> 原始 `log` / `xml` / `exit` 文件保持不动，作为本轮唯一已执行证据。

### 2.3 本轮环境的已知事件（**不属于测试失败，也不构成审核轮次**）

- 测试员侧 `pwsh` / Paseo 终端被 ACL 阻断（`SetNamedSecurityInfoW failed (Win32 5)`）；
  按 `docs/FOUNDATION_AGENT_WORKFLOW.md` §2.1 冻结，**未**提权、未做 ACL 修复、未换通道重试。
- 该事件只影响"谁来跑命令"，不影响用例设计；不计入 `consecutive_test_failures`，不推进 `review_rounds`。

---

## 3. 局限与未覆盖（如实声明）

1. **执行归属**：本轮 R0–R4 全部由父 Agent 执行（`executed_by=父Agent`）；
   测试员**从未**运行任何命令（ACL 阻断，未提权/未换通道），只做设计与判读。
2. **未覆盖 / 显式不做**：
   - 不覆盖阶段 3（closed-bar 资格链、策略准入），也不声称其已完成；
   - 不覆盖真实交易所链路与实盘（无网络、无真实账户、无真实下单）；
   - 不覆盖前端与 API 层行为；
   - **2.8「同币反向在途冲突先拒绝（明确拒绝码）」本轮没有专门用例 —— 这是覆盖缺口，不是通过**；
   - **2.12 后半段「多策略联合回测读出每 book 独立权益与预算」未覆盖**（与阶段 4 衔接，本轮未做）；
   - **2.1 的「策略不得直接构造适配器参数」只做了静态阅读核对**
     （`trader/trading/strategy.py:404-425` 走 `build_order_spec` → `spec.to_command`），**没有可执行断言**。
3. **并发用例的性质**：PG 并发用例依赖 `SELECT ... FOR UPDATE` 的行级串行化；
   若被测实现改为非事务化写入，该用例会以失败呈现（这正是它的目的），
   但它不是对"分布式多进程"场景的证明（单进程 + 单连接池）。
4. **变异实验的范围**：M1–M3 是**进程内 `monkeypatch`**，只证明这三条约束各自有能命中的目标断言，
   不构成对所有实现的完备变异覆盖；本轮 M3 装置被判定无效并已重写，**重写后尚未复跑**。
5. **待父 Agent 校验**：交付集合的逐文件磁盘哈希、完整改动/未跟踪枚举与 `source_digest` 重算
   由父 Agent 产出（`output_source_digest_pending_parent_verification = true`）；测试员无法执行哈希命令。
6. **环境证据的归属**：父 Agent 先前受控执行的 `test_trading_service.py`（36 passed / 0 skipped）
   是**环境证据**，`executed_by=父Agent`、且在 v3 快照下执行，**不是**独立测试的通过结论，
   也**不**代表阶段 2 通过，更不能替代本快照下的必需回归。
7. **本轮新增（未解决项）**：
   - 格式闸门 R3（`black --check`）失败**尚未解决**；
   - 两项失败虽已判为测试员自身缺陷并修正，但**修正后未复跑**，因此**不存在"修后通过"**；
   - 必需回归（P0 与阶段 2 列出的既有套件）父 Agent 本轮**未执行** → G1 无法判定。

---

## 4. 独立判读（analyzed_by = 独立测试员）

判读依据：契约（`docs/INTERFACE_CONTRACTS.md` 13.x / 15.4）、阶段验收表
（`docs/FOUNDATION_REFACTOR_PHASE0.md` 2.1–2.12）、以及**手算**；
不把被测实现的输出当作期望值，也不因同文件其他用例通过而推断某条 oracle 正确。

### 4.1 两项失败的定性

#### 失败 1 `test_t3_genuine_fills_are_commutative_and_never_dropped` → **测试员 oracle 手算错误（非生产缺陷）**

- 该用例的 `fill()` 辅助函数给**三笔成交统一** `fee=Decimal("0.01")`，只有 `fee_asset` 不同
  （e1=USDT、e2=BTC、e3=BNB）。
- 同文件 `test_t3_partial_fills_base_quote_third_asset_fees_and_conservation` 用的是
  e1=0.05 USDT、e2=**0.02** BTC、e3=0.01 BNB，其期望值 `1.58` 依赖 **e2 手续费 = 0.02**：
  `1 + (0.5 − 0.02) + 0.1 = 1.58`。
- 我把 `1.58` 照抄到交换律用例，但该用例 e2 手续费是 **0.01**；按契约
  （BUY 计入 `+qty` base；手续费按 `fee_asset` 各自币种扣减，不串币种）应为
  `1 + (0.5 − 0.01) + 0.1 = 1.59`。
- **交叉验证**（不依赖"其他用例通过"这一条理由）：同一实现、同一公式，仅在 e2 手续费 0.02 / 0.01
  两种输入下分别得到 1.58 与 1.59，差值 0.01 恰等于手续费差额 —— 说明 base-asset 手续费确实按
  `fee_asset` 扣减。实现输出的 `1.59` 与契约手算一致。
- 该用例中真正检验交换律的断言（`first.balances == second.balances`、
  `first.books[BOOK_A].balances == second.books[BOOK_A].balances`、两条 `booked_qty == 1.6`）
  **均已通过**，失败点只在我写死的绝对值常量上。

#### 失败 2 `test_t7_mutation_book_hold_not_released_on_fill_is_detected` → **变异装置设计错误（非生产缺陷）**

- 我把 `book_ledger.release_book_execution_hold` 打桩为 no-op，并对一张 **qty=1 的订单做了 qty=1 的完全成交**。
- 完全成交使 `order.booked_qty == order.command.qty`，触发 `trader/trading/service.py:1443-1446` 的终态清理：
  `_reconcile_order_evidence → _release_remaining_hold → trading_core.release_order_hold → book_ledger.release_book_hold`；
  而 `release_book_hold`（`trader/trading/books.py:93-96`）与被打桩的
  `release_book_execution_hold`（`books.py:99-114`）**不是同一个函数**，它会直接 `pop` 掉整个 book 层预约。
- 于是被注入缺陷的运行里 book 层预约**并不残留**，我断言的"book 层仍为 100.1"是我凭空假设的内部残留，
  实际不存在 → `KeyError`。
- 这正是变异测试的禁忌：**不能假定错误实现会产生某个特定内部副作用**。
  实现在这里甚至是**正确**的（完全成交后两层预约都不应残留）。
- 修正方向：把注入范围改到**部分成交**（成交 0.5 / 订单 2），此时不触发终态清理，
  被打桩的函数才真正留下残留；断言改为针对**业务不变量**（两层预约必须同步递减、都等于 150.15），
  而不是某个具体残留值。

### 4.2 已做的修正（保留原始失败证据；**未复跑，不宣称修后通过**）

| # | 修正 | 性质 |
|---|---|---|
| 1 | 交换律用例期望值 `1.58` → `1.59`（契约推导），并补全同口径 USDT `839.99`、BNB `-0.01`、持仓 `1.59`、两层预约 `40.04` | 修正写错的 oracle，**新增**断言而非删除 |
| 2 | M3 变异用例重写：改部分成交 + 不变量断言（`book_hold is None or book_hold.amount != 150.15` 与 `pytest.raises(AssertionError)`） | 修正**注入装置**，业务不变量不变 |
| 3 | 新增 `test_t7_normal_partial_fill_releases_both_layers_in_sync`（未注入，两层同步 150.15） | 补"恢复后正常通过"对照 |
| 4 | 新增 `test_t7_normal_full_fill_leaves_no_reservation_on_either_layer`（完全成交两层都不残留） | 把被误建模的行为固化为不变量断言 |
| 5 | 修正一处超过 100 字符的行（唯一一处） | 纯格式 |

- 收集数由 **26** 变为 **28**（原 26 + 新增 2）。
- **未修改任何生产代码**；**未删除、未弱化任何业务断言**；**未使用 skip / xfail / 容差**。
- 原始 `indep-acceptance.{log,xml,exit}` 等证据文件**保持不动**。

### 4.3 业务项 2.1–2.12 逐项证据

| 验收项 | 判读 | 证据（已执行用例） |
|---|---|---|
| 2.1 唯一订单规格入口 | **partial** | T5 三条规格用例 PASSED；但"策略不得直接构造适配器参数"仅静态阅读核对，**无可执行断言** |
| 2.2 订单类型不由 metadata 默认 | pass | `test_t5_order_type_must_be_explicit_and_metadata_is_not_consulted` PASSED |
| 2.3 价格来源显式记录 | pass | `test_t5_limit_uses_the_signal_price_and_market_traces_its_reference` PASSED |
| 2.4 `book_id` 持久化、稳定可回溯 | pass | PG `test_pg_books_and_two_layer_holds_survive_a_restart`、`test_pg_legacy_row_migration_is_idempotent_and_keeps_append_only_facts` PASSED |
| 2.5 逐币种守恒事务内恒等 | pass | `test_t3_partial_fills_base_quote_third_asset_fees_and_conservation` 内 `conservation_mismatch == {}` PASSED |
| 2.6 显式划拨；未划拨恒在未分配 | pass | `test_t5_control_only_queues_allocation_and_execution_applies_it` PASSED；PG `test_pg_concurrent_allocations_do_not_lose_an_update` PASSED |
| 2.7 双层预约同事务、任一层失败整体回滚 | pass | `test_t2_*` 两条 + PG `test_pg_transaction_rolls_back_every_projection_on_book_hold_failure` PASSED |
| 2.8 同币反向在途冲突先拒绝（明确拒绝码） | **未覆盖** | 本轮无专门用例，**不能记为通过** |
| 2.9 book 不借用其他 book 或账户总余额 | pass | `test_t1_*` 两条 + PG `test_pg_concurrent_places_cannot_double_spend_the_same_book` + M1 变异 PASSED |
| 2.10 归属不明阻断新增风险并记未决原因码；不猜历史 | pass | `test_t3_unknown_place...`、`test_t4_*` 四条、`test_t6_*` 三条 PASSED |
| 2.11 单一 CAS 入口、终态不回退、去重 | pass | `test_t5_terminal_status_never_regresses...`、`test_t3_duplicate_execution...`、`test_g_single_status_transition_entry_rejects_regression` PASSED |
| 2.12 迁移不猜历史/不清库/不改写历史；存量留在未分配 | **partial** | `test_t4_*` + PG 迁移用例 PASSED（迁移部分）；"多策略联合回测读每 book 权益与预算"**未覆盖** |

### 4.4 独立测试重点 T1–T8 逐项

| 编号 | 判读 | 证据 |
|---|---|---|
| T1 | pass | 2/2 PASSED（A=100/B=900/账户=1000，A 申请 200.2 被拒、零发送、无残留；正向对照 90.09 受理且两层预约均为 90.09） |
| T2 | pass | 2 条验收 + 3 条 PG PASSED（含真实 PG 回滚、重启、并发划拨不丢更新） |
| T3 | **fail** | 5 条中 4 条 PASSED；交换律用例失败（**测试员 oracle 错误**，已修正未复跑） |
| T4 | pass | 4 条验收 + 2 条 PG PASSED |
| T5 | pass | 5/5 PASSED |
| T6 | pass | 3/3 PASSED；含 C2 回归：mark 过期但对账新鲜时精确得到 `NotReady("VALUATION_STALE_OR_MISSING")` 且显式断言不是 `ACCOUNT_NOT_RECONCILED` |
| T7 | **fail** | M1、M2 命中并 PASSED；**M3 装置无效**（已重写未复跑）。"至少两处"的最低条数由 M1+M2 满足，但该批次为红 |
| T8 | **fail** | CI 入口已按"只追加"方式追加（是）；但 **black 闸门 R3 exit 1**，且逐文件指纹/完整清单仍待父 Agent 复核 |

### 4.5 通用约束 G1–G6 逐项

| 编号 | 判读 | 依据 |
|---|---|---|
| G1 | **未验证（blocked）** | 必需回归（AGENTS.md P0 五文件；阶段 2 列出的 `test_trading_service.py`、`test_shared_trading_core.py`、`test_oms_idempotency.py`、`test_oms_position_lot_integration.py`、`test_phase0_foundation_safety.py`）父 Agent 本轮**未执行**；先前 36 passed 是 v3 快照下的环境证据，不能替代 |
| G2 | pass | 期望值全部来自契约与手算；两项失败亦按契约/代码结构追因，未拿实现输出当期望。修正后的 1.59 由"BUY 计入 +qty base、手续费按 fee_asset 扣减"推出，并用不同手续费输入的差值交叉验证 |
| G3 | pass（就 G3 自身判据） | 必需检查已实际运行（R0–R4，父 Agent）；PG **5 passed / 0 skipped**；无删断言、无 xfail、无宽泛异常吞错、无故容差。注：R3 格式闸门失败另计于 T8 |
| G4 | **未验证（blocked）** | 父 Agent 已产出 `tester-output-snapshot-execution-1.json`（`source_digest=aedcec5682f4…`）与 43 项 `changed_files`；逐文件磁盘哈希与完整改动/未跟踪枚举仍待父 Agent 产出，测试员无法执行哈希命令 |
| G5 | pass | 测试员未改生产代码/工程师测试/conftest/契约/架构/迁移说明/全局 workflow；PG 只用临时 schema；DSN 未写入源码；无未经授权的账户/部署/历史操作 |
| G6 | **partial** | 仅就独立测试覆盖到的接口做了静态核对（spec 与价格来源字段；CI 入口只追加、未改其他门禁）；完整一致性核对属工程/审核范围 |

### 4.6 overall 结论（第 1 轮判读，**已由 §5 取代**，保留为历史记录）

> **overall = fail**（`workflow_version=2`、`phase=2`、`test_attempt=1`）

- 真实计数：R1 **26 项 / 24 passed / 2 failed / 0 skipped**（exit 1）；R2 **5 passed / 0 skipped**（exit 0）；
  R3 exit 1；R4 exit 0。
- **本轮未发现生产缺陷**：两项失败均判为**测试员自身用例缺陷**（oracle 手算错误 + 变异装置无效），
  已修正但**尚未复跑**，故不构成任何"修后通过"。
- 覆盖缺口：2.8 未覆盖；2.12 回测部分未覆盖；2.1 部分仅静态核对。
- G1（必需回归）与 G4（指纹复核）**未验证**，因此不能支撑阶段 2 进入第 2 轮审核或阶段 3。
- 轮次与计数由父 Agent 维护，测试员不修改：正式审核第 1 轮仍为 fail，`next_review_round=2`；
  本轮是一次独立测试失败批次。

### 4.7 下一步（需父 Agent 实际复跑）

已提交 `artifacts/foundation/phase2/tester-execution-request-2.json`（C0–C7）：

- C0 收集闸门（预期 **28** 项）、C1 验收、C2 PG、C3 `black --check`、C4 `isort --check`；
- C3b（**可选、需回传 diff**）：若 C3 仍失败，由父 Agent 对两个测试员自有测试文件执行
  `python -m black --line-length 100`（**仅格式化写入**），测试员据此核对"只改空白/换行、未改断言"；
- C5 / C6 **必需回归**（G1 判据）；C7 可选关联（工程师自身阶段 2 套件，仅作失败关联，不作独立证据）。

复跑后由测试员再次独立判读并给出最终 verdict。

---

## 5. 复跑后的最终独立判读

### 5.0 本轮分工与证据来源

| 环节 | 执行者 | 状态 |
|---|---|---|
| 测试设计与用例编写（**authored_by**） | 独立测试员 `29822745-df5a-4e5e-b5f7-f66230c24ddd` | 已完成（含 §4.2 的 5 项修正） |
| 命令执行（**executed_by**） | 父 Agent `18e99d90-33f5-41f9-8a2f-672d109886bb` | 已完成（C0–C7，见 `parent-execution-result-2.json`） |
| 结果分析（**analyzed_by**） | 独立测试员 | 已完成（本节） |

- 分支/base：`feat/foundation-refactor` @ `1c587f33c65fda0f95d8992d6195b1b209f84ed6`
- 输出快照：`artifacts/foundation/phase2/tester-output-snapshot-execution-2.json`
- `source_digest = 878ac6185bf2cf3f5e0e884617eea4f67dd3870871b83b07128d53b0f6dadf1a`
  （与 `tester-output-source-digest-execution-2.txt` 独立核对一致）
- 测试员仍**未执行任何命令**（ACL 阻断未解除；未提权、未做 ACL 修复、未换通道重试）。

### 5.1 真实计数（逐份核对原始 log / xml / exit，不采信摘要数字）

| id | 内容 | 退出码 | 计数（原始证据核对结果） |
|---|---|---|---|
| C0 | `--collect-only -q` 验收文件 | 0 | 收集 **28**（`indep-acceptance-collect-2.log`；格式化后复跑见 `-2b.log` 亦为 28） |
| C1 | 验收套件 | **0** | XML 头 `tests="28" failures="0" errors="0" skipped="0"`；`-rA` 逐条 28 行 PASSED |
| C2 | 真实 PostgreSQL | **0** | XML 头 `tests="5" failures="0" errors="0" skipped="0"` |
| C3 | `black --check` 首次 | **1** | 两份文件均需格式化（`indep-format-2.log`，5 行完整） |
| C3b | `black` 格式化写入 | 0 | `reformatted ...acceptance.py` / `...postgres.py`（`indep-format-apply-2.log`） |
| C3' | `black --check` 复验 | **0** | `2 files would be left unchanged.`（`indep-format-2b.log`） |
| C4 | `isort --check-only` | **0** | 输出为空（isort 成功时的正常行为） |
| C5 | P0 回归（5 文件） | **0** | **99** passed（72+27 = 99 个 `.`，无 F/E） |
| C6 | 阶段 2 核心回归 | **0** | **66** passed |
| C6b | `test_phase0_foundation_safety.py` | **0** | **2** passed |
| C6c | 阶段 2 PG 回归 | **0** | **11** passed |
| C7 | 工程师自身阶段 2 套件（仅关联） | **0** | **34** passed |

> 全部批次 `0 skipped`。PG 两批（C2 / C6c）均实跑真实库，未跳过。

### 5.2 格式化 diff 的独立核对（这是测试员本轮的核心责任）

父 Agent 在摘要中声明生成了 `format-diff-acceptance.patch` / `format-diff-postgres.patch`。
**经 `artifacts/foundation/phase2/` 全量枚举，这两个文件在磁盘上并不存在**（详见 §5.4-1）。
因此我改用**更强的替代方式**核对：直接以父 Agent 保留的 `pre-format-*` 副本与当前冻结文件逐项比对。

| 核对项 | acceptance | postgres |
|---|---|---|
| `assert` / `with pytest.raises` 行数 | **194 / 194 完全一致** | **60 / 60 完全一致** |
| 断言中的 `Decimal(...)` 常量序列 | 全部一致（含 `1.59`、`839.99`、`-0.01`、`40.04`、`150.15`、`90.09`、`200.2`） | 全部一致（含 `50.05`、`549.95`、`100.1`、`90.09`） |
| 变异注入点 `monkeypatch.setattr` | **5 / 5 一致**（`reserve_book_hold`×2、`release_book_execution_hold`×2、`book_available`×1） | **1 / 1 一致**（`reserve_book_hold`） |
| 函数/类签名 `def` | **57 / 57 一致** | **24 / 24 一致** |

唯一差异是 black 的排版：换行位置、括号折行（例如把
`assert after_queue... == Decimal("1000"), "control moves nothing"`
折成 `... == Decimal(\n    "1000"\n), "control moves nothing"`
——数值与断言对象不变，仅折行位置不同）。

`indep-format-apply-2.log` 的两行 `reformatted ...` 是 **black 的原生措辞**（与 C3 的
`would reformat` 同一词表），可确认 C3b 使用的是 black，而非自定义改写脚本。

> **结论：格式化仅改动空白与折行，未改动任何业务断言、期望值或变异注入点。
> 未发现格式化引入的语义变化。**

### 5.3 两项旧失败在本轮的处置结果

| 旧失败 | 处置 | 本轮结果 |
|---|---|---|
| T3 交换律 `1.58` → 应为 `1.59`（测试员 oracle 手算错误） | 期望值改为契约推导值并**新增**同口径断言（USDT `839.99` / BNB `-0.01` / 持仓 `1.59` / 两层 `40.04`） | `test_t3_genuine_fills_are_commutative_and_never_dropped` **PASSED** |
| T7 M3 变异装置假定错误实现必留残留（装置设计错误） | 改为部分成交注入 + 业务不变量断言（两层同步 `150.15`，不假定残留值） | `test_t7_mutation_book_hold_not_released_on_fill_is_detected` **PASSED** |
| 无（新增对照） | 新增 2 条未注入对照，把曾被我误建模的行为固化为不变量 | 两条均 **PASSED** |

口径声明：以上是"测试员修正用例后，由父 Agent 实际复跑得到通过"，
**不是**"测试员自称修后通过"。原始失败证据（`indep-acceptance.{log,xml,exit}`、
`indep-format.{log,exit}`）全部保留未改动。

### 5.4 证据链瑕疵（如实记录，不隐瞒）

1. **承诺的 patch 文件缺失**：`format-diff-acceptance.patch`、`format-diff-postgres.patch`
   不存在。我用 `pre-format-*` 副本直接比对替代（§5.2），结论不受影响，但"交付物与声明不符"应记录。
2. **C1 的 JUnit 时间戳早于格式化**：`indep-acceptance-2.xml` 的 `timestamp` 为
   `16:39:58`，早于 `indep-postgres-2.xml` 的 `16:41:11`，而 C3b 格式化发生在 C2 之后。
   因此该 XML **很可能来自格式化前**的那次运行，"格式化后重跑 C1 并生成新 XML"无法由该文件证明。
   缓解：§5.2 已证明冻结文件与格式化前文件在断言层面等价，且 black 语义保真。
   **建议**父 Agent 在下一轮用唯一文件名补跑一次（命令见 §5.6-R1），使证据链闭合。
3. **事实更正**：摘要称"C3 首次未写出 exit 文件"，但 `indep-format-2.log`（5 行完整）与
   `indep-format-2.exit`（内容 `1`）实际存在。这反而更有价值——它完整记录了格式化前的红灯状态。
   不影响任何结论。
4. **冻结集合构成**：57 项含 12 个 `workflow/` 暂存文件（全局 workflow 交付，非阶段 2 业务改动）。
   阶段 2 业务交付实为 **45 项**（36 modified + 23 untracked − 2 `.workbuddy/` − 12 `workflow/`）。
5. **本节追加会使报告自身哈希失效**：冻结值 `docs/PHASE2_INDEPENDENT_TEST_REPORT.md =
   10c19eeb…` 对应追加前的版本。父 Agent 需**仅对该单文件**重算哈希；两个测试文件本轮未再改动，
   `b5e5af87…` / `49f9ed69…` 仍然有效。

### 5.5 逐项最终判读

| 业务项 | 判读 | 说明 |
|---|---|---|
| 2.1 | **partial** | T5 规格用例通过；"策略不得直接构造适配器参数"仍仅静态阅读核对，无可执行断言 |
| 2.2 | pass | 订单类型必须显式、metadata 不被读取 |
| 2.3 | pass | LIMIT 用信号价、MARKET 用参考价 |
| 2.4 | pass | 迁移不猜历史归因，`BOOK_ATTRIBUTION_UNKNOWN` 保留 |
| 2.5 | pass | 三币种手续费 + 逐币种守恒 `conservation_mismatch == {}` |
| 2.6 | pass | Control 只排队、Execution 才生效（含 PG 并发） |
| 2.7 | pass | 双层预约/记账中途故障原子回滚（内存 + PG 各一组） |
| 2.8 | **未覆盖** | 同币反向在途冲突先拒绝（明确拒绝码）本轮仍无专门用例，**不计为通过** |
| 2.9 | pass | 跨 book 隔离 + 禁止借用账户余额 |
| 2.10 | pass | 估值/对账/UNKNOWN 阻断不被掩蔽 |
| 2.11 | pass | 终态不回退、重复执行幂等、单一状态推进入口 |
| 2.12 | **partial** | 迁移部分通过；"多策略联合回测读每 book 权益与预算"仍未覆盖（与阶段 4 衔接） |

| 重点 | 判读 | 依据 |
|---|---|---|
| T1 | pass | 2/2（含 A=100/B=900 反例与 `90.09` 正对照） |
| T2 | pass | 2 验收 + 3 PG |
| T3 | **pass** | 5/5（交换律用例已按契约修正并实际通过） |
| T4 | pass | 4 验收 + 2 PG |
| T5 | pass | 5/5 |
| T6 | pass | 3/3，C2 精确码 `VALUATION_STALE_OR_MISSING` 未被 `ACCOUNT_NOT_RECONCILED` 掩盖 |
| T7 | **pass** | M1 / M2 / M3 三条变异全部命中，另有 2 条未注入对照；满足"至少两处" |
| T8 | **pass** | black / isort 闸门复验 exit 0；CI 入口仅追加；逐文件指纹（57 项）+ 快照 + digest 已交付（瑕疵见 §5.4-1/2/4） |

| 约束 | 判读 | 依据 |
|---|---|---|
| G1 | **pass** | 必需回归全部实跑且全绿：P0 **99**、阶段 2 核心 **66**、phase-0 安全 **2**、阶段 2 PG **11**，全部 `0 skipped` |
| G2 | pass | 期望值来自契约与手算；两处修正均由契约推导，未拿实现输出当期望 |
| G3 | pass | 必需检查均实跑；PG 两批 `0 skipped`；无删断言、无 xfail、无吞错、无故容差 |
| G4 | **pass** | 快照 + `source_digest` + 57 项逐文件哈希 + modified/untracked 全量枚举已交付（构成说明见 §5.4-4） |
| G5 | pass | 未改生产代码/工程师测试/conftest/契约/架构/迁移说明/全局 workflow；PG 仅临时 schema；DSN 仅走环境变量且未落盘 |
| G6 | **partial** | 仅就独立测试覆盖到的接口做静态核对；完整一致性核对属工程与只读审核范围 |

### 5.6 overall

> **overall = pass**（`workflow_version=2`、`phase=2`、`test_attempt=1`）

支撑该结论的硬事实：C1 **28/28 passed / 0 skipped / exit 0**；C2 **5/5 / 0 skipped / exit 0**；
C3/C4 闸门 **exit 0**；C5–C6c 必需回归 **99 + 66 + 2 + 11 全绿 / 0 skipped**；
两项旧失败均为**测试员自身缺陷**（非生产缺陷）且经修正后实际复跑通过；格式化经逐行核对**未改业务断言**。

**仍存在的覆盖缺口与限制（不因 overall=pass 而消失）**：

1. 2.8（同币反向在途冲突显式拒绝）本轮**未覆盖**，需在后续轮次补用例。
2. 2.12 的"多策略联合回测"部分未覆盖，与阶段 4 衔接，本轮不声称已完成。
3. 2.1 的"策略不得直接构造适配器参数"仅有静态阅读核对，缺可执行断言。
4. §5.4-2 的 C1 时间戳问题：建议下一轮补跑一次以闭合证据链。
5. 不覆盖真实交易所链路、实盘下单、前端与 API 层；不覆盖阶段 3。
6. `overall=pass` 仅代表**独立测试这一侧**的结论，**不替代正式只读审核**：
   正式审核第 1 轮仍为 fail，`next_review_round=2`，是否进入第 2 轮由父 Agent 决定。

**R1（可选、一行命令，用于闭合 §5.4-2）**：

```text
python -m pytest -q --tb=short -rA --junitxml=artifacts/foundation/phase2/indep-acceptance-3.xml trader/tests/test_phase2_independent_acceptance.py
```

### 5.7 测试写入所有权

测试员在两个测试文件与本文档上的写入工作**已完成**，现**释放写入所有权**。
本轮之后测试员不再改动 `trader/tests/test_phase2_independent_acceptance.py`、
`trader/tests/test_phase2_independent_postgres.py`；如需再次修改，须由父 Agent 重新授予。
父 Agent 仅需对 `docs/PHASE2_INDEPENDENT_TEST_REPORT.md` 单文件重算哈希（§5.4-5）。

测试员未启动审核员、未提交、未推进阶段 3、未修改全局 workflow 与状态文件、未改动任何生产代码。

---

## 6. 缺口补齐：2.8 与 2.12（新增用例，**尚未执行**）

### 6.0 授权范围与边界

父 Agent 临时恢复的有限写入权仅覆盖两项：

- `trader/tests/test_phase2_independent_acceptance.py`：只为 2.8 / 2.12 **新增**测试与必要 helper；
- `docs/PHASE2_INDEPENDENT_TEST_REPORT.md`：只**追加**，不改写历史（§4、§5 原样保留）。

本轮**未**修改既有用例、生产代码、PG 测试或 CI。新增 3 个用例后收集数为 **31**（原 28）。

> **状态声明：以下两个缺口的新增用例已编写，但测试员无法执行命令（ACL 阻断未解除），
> 因此不得声称 2.8 / 2.12 已通过。它们当前的状态是"已编写、待父 Agent 复跑"。**

### 6.1 2.8 —— 同币反向在途冲突显式拒绝

**契约来源**：`docs/FOUNDATION_REFACTOR_PHASE0.md` 2.8「同币反向在途冲突先拒绝（明确拒绝码），
不排队、不自动净额」；`docs/INTERFACE_CONTRACTS.md` 13.9
「同一 `book_id` 上已有未终结的反向在途订单时，新的反向命令先拒绝（明确拒绝码）」，
13.12 给出稳定码 `BOOK_OPPOSITE_ORDER_IN_FLIGHT`。

**手算场景**（不取实现输出）：
账户 `2000 USDT + 2 BTC`；`book:alpha` 与 `book:beta` 各划入 `1000 USDT + 1 BTC`。
两本书都**各自持有足够 BTC**，所以 alpha 上的 SELL 被拒时，不可能归因于余额不足或 book 预算不足。

| 步骤 | 手算预期 |
|---|---|
| alpha 上 BUY 1 @ 100 | 预约 `1 × 100 × (1 + 0.001) = 100.1 USDT`，账户层与 book 层各一份 |
| alpha 上 SELL 1（反向、同币、同 book） | 拒绝，码恰为 `BOOK_OPPOSITE_ORDER_IN_FLIGHT` |
| 被拒命令的副作用 | 无命令记录、无订单意图、两层均无新预约、无交易所发送、余额/持仓/`booked_qty` 均不变 |
| beta 上 SELL 1（反向、同币、**不同 book**） | 必须**放行**（证明拒绝的作用域是 book 而非账户） |
| 放行后 alpha 的预约 | 仍为 `100.1`，未被跨 book 净额掉 |

新增用例：`test_t9_same_book_opposite_in_flight_is_refused_without_new_risk`。

断言要点：拒绝码用 `str(refused.value) == "BOOK_OPPOSITE_ORDER_IN_FLIGHT"` **精确相等**
（不是子串匹配）；"不排队"用 `state.commands.get("cmd-sell-a") is None` 与
`set(state.orders) == {buy.cl_ord_id}` 断言；"不自动净额"用余额、book 分区、持仓、
`booked_qty` 四条不变式断言；"无发送"用 `place_calls` 计数不变断言；
末尾再用另一 book 上的同向反向单作正对照，排除"全局一律拒绝"这种错误实现。

### 6.2 2.12 —— 多策略联合回测读取每 book 独立权益与预算

**契约来源**：2.12「多策略联合回测能读出每 book 的独立权益与预算（与阶段 4 衔接）」；
13.12「`GET /v1/trading/books` 返回每 book 的权益/预算，供阶段 4 使用」。
读取入口用既有的 `TradingService.books()`（API 路由背后的同一查询）。

**手算场景**：账户 `3000 USDT + 3 BTC`，BTCUSDT mark = 100。

| book | 余额 | 权益（手算） | 预算 / 可用 |
|---|---|---|---|
| `book:alpha` | 1000 USDT + 1 BTC | `1000×1 + 1×100 = 1100` | BUY 1@100 预约 100.1 后：USDT 可用 `899.9`，held `100.1` |
| `book:beta` | 500 USDT + 2 BTC | `500×1 + 2×100 = 700` | USDT 可用 `500`，held `0` |
| `UNALLOCATED` | 1500 USDT | `1500×1 = 1500` | — |
| 账户合计 | 3000 USDT + 3 BTC | `3000 + 3×100 = 3300` | `= 1100 + 700 + 1500` |

要点：**权益按余额估值、不扣预约**（所以 alpha 预约后权益仍是 1100，只有预算缩小）；
三个分区数值互不相同，且都不等于账户合计 3300，可排除"返回账户聚合值"的错误实现；
三个分区权益之和等于账户合计，验证分区完备。

第二条用例 `test_t9_per_book_equity_does_not_borrow_another_books_valuation`：
alpha 只持 USDT、beta 只持 BTC；把时钟推进 `60_001 ms` 使 BTCUSDT mark 过期后，
alpha 权益仍为 `1000`（它本就不需要 mark），beta 必须返回 `equity is None` 与
`valuation_complete is False`，**不得**复用 alpha 的估值或账户级 mark。

新增用例：`test_t9_joint_backtest_reads_per_book_equity_and_budget`、
`test_t9_per_book_equity_does_not_borrow_another_books_valuation`。

### 6.3 覆盖状态更新

| 验收 | §5 时状态 | 现在状态 | 说明 |
|---|---|---|---|
| 2.8 | **未覆盖** | **已编写，待执行** | 新增 1 个用例，含跨 book 正对照；未执行前**仍不计为通过** |
| 2.12 | partial | **已补齐待执行**（迁移部分本就 pass） | 新增 2 个用例，覆盖联合读取与"不借用他 book 估值" |
| 2.1 | partial | **partial（不变）** | "策略不得直接构造适配器参数"仍只有静态核对，本轮未新增可执行断言 |
| G6 | partial | **partial（不变）** | 完整接口一致性仍属工程与只读审核范围 |

### 6.4 未执行与下一步

测试员本轮**未执行任何命令**，因此：

- 上述 3 个新用例**没有**任何通过/失败证据，`overall` 仍停留在 §5.6 的 `pass`（针对 28 项旧集合）；
- 新集合（31 项）的 verdict 必须等父 Agent 复跑后由测试员重新判读；
- 已提交 `artifacts/foundation/phase2/tester-execution-request-3.json`（D0–D3）：
  collect（预期 31）、acceptance（新 JUnit 文件名 `indep-acceptance-4.xml`）、
  `black --check`、`isort --check-only`；
- 未包含 PG 与回归套件：本轮只新增测试文件内容，**未触碰任何生产代码**，
  C2/C5/C6/C6b/C6c 的既有绿灯结论不受影响（如需可另跑）。

**G5/G6 相关自评**：新增代码只用既有公开接口（`place` / `allocate` / `books` /
`book_snapshot` 的返回结构）与项目内既有 fake，未新增 fixture 文件、未联网、未连库、
未放宽任何守恒断言。

---

## 7. 第 3 轮复跑判读（D0–D3）与修订

### 7.0 D1：测试员 oracle 的格式缺陷，非生产契约缺陷

```
trader\tests\test_phase2_independent_acceptance.py:1325
E  AssertionError: assert {'BTC': '0', ...T': '100.100'} == {'BTC': '0', 'USDT': '100.1'}
E    {'USDT': '100.100'} != {'USDT': '100.1'}
```

手算：`hold = Decimal("1") × Decimal("100") × Decimal("1.001")`。Decimal 乘法保留指数，
`Decimal("100")`（exp 0）乘 `Decimal("1.001")`（exp −3）得 `Decimal("100.100")`。
**数值上 `100.100 == 100.1`**，经济数值零偏差；错在我把数值期望写成了字符串期望。

反证：同一用例上一行 `before.holds[...].amount == Decimal("100.1")` **通过**，
正因为它做的是 Decimal 比较。

**同时暴露一处隐性错误**：原 1326 行 `alpha["available"]` 期望 `"899.9"`，
真实值 `Decimal("1000") − Decimal("100.100") = Decimal("899.900")`。它未被观察到，
只是因为 pytest 在 1325 先停。本轮一并修正，避免再消耗一轮复跑。

**阶段 4 提醒（非缺陷）**：`book_snapshot` 序列化不归一化 Decimal 指数。契约 13.9/13.12
只规定数值语义，未规定字符串格式，故不算违约；但下游若用字符串比较会被 `"100.100"` 绊倒，
阶段 4 消费方应以 Decimal 解析。

### 7.1 D2：红灯真实，空 patch 是取证方式错误

`black --check` 报 `would reformat ...acceptance.py`，同时 `black-check-preview-3.patch`
为 0 字节。二者不矛盾——该 patch 由 `git diff --no-index` 对比 `pre-d2b` 副本与当前文件生成，
两者 SHA256 相同，diff **必然**为空。它只能证明"文件未被改动"，不能证明"black 无话可说"。

反证：若文件已 clean，black 会报 `2 files would be left unchanged` 并 exit 0。

`black --check --diff` 实际输出（`black-diff-3.patch`，2353 字节）只有三处，与测试员预测逐处命中：
三处 `recover(...)` 被折成多行，合并后每行 96 字符（≤100），black 要求合回一行。
Black 24.4.2 / Python 3.12.5，`trader/pyproject.toml` 配置 `line-length=100` 与 CLI 一致，排除配置冲突。

**协议已更新**（`docs/FOUNDATION_AGENT_WORKFLOW.md` §2.1）：测试员自有文件的格式检查
必须用 `black --check --diff` 取证；失败时父 Agent 只回传 diff，由测试员手改，
不再由父 Agent 执行 Black 写入。本轮未发生"先格式化再宣称符合"。

### 7.2 本轮修订（仅 T9 段，仅两处）

| 位置 | 修改 | 语义 |
|---|---|---|
| 三处 `recover(...)` | 按 `black-diff-3.patch` 合并为一行 | 纯格式，无行为变化 |
| `alpha["held"] / ["available"]` 两行字典断言 | 改为 `{asset: Decimal(value) for ...}` 与 `Decimal` 期望值比较 | **仍逐项精确相等**；比较域由字符串改为 Decimal，键集合语义不变 |

替换后断言强度不降：仍要求键集合恰为 `{BASE, QUOTE}`、四个数值精确相等，
只是不再把序列化格式写进 oracle。既有用例、期望值、生产代码、其他段落均未改动。

### 7.3 覆盖状态

| 验收 | 状态 | 依据 |
|---|---|---|
| 2.8 | **pass**（D1 通过） | `test_t9_same_book_opposite_in_flight_is_refused_without_new_risk` PASSED，含跨 book 正对照 |
| 2.12 | **blocked** | stale-mark 用例 PASSED；equity/budget 用例被测试员 oracle 拦下，未完成验证。**无任何指向生产缺陷的证据** |
| 2.1 | partial（不变） | 仍只有静态核对 |
| G6 | partial（不变） | 属工程与只读审核范围 |
| G1 | **不受影响** | 生产代码零改动；C5 99 / C6 66 / C6b 2 / C6c 11 建立于同一批生产字节 |

**overall = fail**（D1 exit 1 且 D2 exit 1）。

注：2.8 的 pass 证据来自修订前的字节。本轮对 `_two_book_service_with_base_asset` 只做了
等价的行合并，语义未变，但严格起见 2.8 仍须由 E1 在新字节上复现一次。

### 7.4 未执行与下一步

修订后测试员仍未执行任何命令。已提交 `tester-execution-request-5.json`：

- E0 collect（预期 31）
- E1 acceptance → `indep-acceptance-5.xml`
- E2 `black --check --diff`（预期 exit 0 且 diff 为空）
- E3 `isort --check-only`

未请求 PG 与回归：本轮仅改测试文件与报告，生产代码零改动。

### 7.5 自评

本阶段第二次同类错误：上次 T3 是算术抄错（1.58/1.59），这次是格式假设（`"100.1"` vs `"100.100"`）。
更值得记的是方法问题——我用"没有超过 100 字符的行"当作格式合规的证据，
但 black 会因为"能合并却拆着写"报错；同理，用两份相同文件的 `git diff` 当作 black 的取证，
是把"文件没变"误当成"文件合规"。两次都是**用间接指标替代直接证据**。

---

## 8. 最终判读（E0–E3，test_attempt=1 收口）

### 8.0 判读依据

全部依据落盘原文与退出码，不采信摘要：

| 检查 | 证据 | 结果 |
|---|---|---|
| E0 | `indep-acceptance-collect-4.{log,exit}` | exit 0，`31` |
| E1 | `indep-acceptance-5.{log,xml,exit}` | exit 0；日志 31 个 PASSED、无 FAILED/SKIPPED；JUnit `tests=31 failures=0 errors=0 skipped=0`，`timestamp=2026-11-02T17:01:28` |
| E2 | `indep-format-4.{log,exit}` | exit 0，`All done! / 2 files would be left unchanged.` |
| E3 | `indep-isort-4.{log,exit}` | exit 0，日志为空（isort 成功时的正常输出） |

JUnit 中三个 T9 用例均为自闭合 `<testcase ... />`，无 `<failure>` 子节点。
执行前后 acceptance 文件哈希一致（`9a3cdee0…`），即执行未回写文件。

### 8.1 2.8 —— pass

`test_t9_same_book_opposite_in_flight_is_refused_without_new_risk` PASSED。覆盖：

- 明确拒绝码 `BOOK_OPPOSITE_ORDER_IN_FLIGHT`（精确相等）
- 不排队（无命令记录、无订单意图）、不自动净额（余额/分区/持仓/`booked_qty` 不变）
- 无新预约（两层 holds 键集合不变，仍 `100.1`）、无发送（`place_calls` 不增）
- 跨 book 正对照：同一 SELL 在 beta 放行，且 alpha 的预约未被跨 book 净额

### 8.2 2.12 —— pass

两个用例均 PASSED：

- `test_t9_joint_backtest_reads_per_book_equity_and_budget`：alpha `1100`、beta `700`、
  UNALLOCATED `1500`、合计 `3300`；alpha 预约后 `held=100.1`、`available=899.9`，
  权益仍 `1100`；`valuations` 的 `source`/`at_ms` 逐项核对
- `test_t9_per_book_equity_does_not_borrow_another_books_valuation`：mark 过期后
  alpha 仍 `1000`、beta `equity is None` 且 `valuation_complete False`

**§7.4 残留风险已消除**：上轮该用例在 alpha 段即失败，`beta` / `unallocated` / `equity`
的字符串断言从未执行。本轮测试完整跑通，说明这些断言（`'2'`、`'500'`、`'0'`、`'1500'`、
`'1100'`、`'700'`）全部成立，尾零假设不再是无证据的推理。

### 8.3 全项 verdict（取代 §5.5 与 §6.3 的对应行）

| 验收 | verdict |
|---|---|
| 2.1 | **partial** —— 仅静态核对（策略不直接构造适配器参数），无可执行断言 |
| 2.2 – 2.7 | pass |
| **2.8** | **pass** |
| 2.9 – 2.11 | pass |
| **2.12** | **pass** |
| T1 – T9 | pass（31/31） |
| G1 – G5 | pass |
| G6 | **partial** —— 完整接口一致性属工程与只读审核范围 |

**overall = pass（仅就独立测试侧）。未发现生产缺陷。**

### 8.4 仍未覆盖的范围

1. **2.1** 的"策略不得直接构造适配器参数"——只有静态核对，无运行期断言；
2. **G6** 的完整接口一致性——静态核对，属工程/只读审核；
3. 范围外且未验证：真实交易所与真实 venue 行为、真实下单、前端/API 层、阶段 3。

### 8.5 收口声明

测试写入权已释放。未派审核员、未提交、未推进阶段 3、未改动生产代码。
`next_review_round=2` 与 `test_attempt=1` 不变，本节不重置任何历史计数。
§4/§5/§6/§7 保持原样，本节只追加。

---

## 9. 第 2 轮审核反例复现（重新授权：新增失败复现，未执行）

> `authored_by = 29822745-df5a-4e5e-b5f7-f66230c24ddd`（独立测试员）。
> 背景：第 2 轮只读审核 `overall = fail`（`review_rounds_completed = 2`、`next_review_round = 3`），
> 审核员指出 2.1/2.12 仍被绕过或误测，并给出 B2/B3/B4 三个可复现经济反例。
> 父 Agent 重新授权**仅新增失败复现**：不改既有断言（`test_phase2_independent_acceptance.py`
> 哈希仍为 `9a3cdee0…`，本轮未触碰）、不改生产代码。
> **本节所有结果均为未执行状态**：`test_attempt = 1` 不变，无任何"复现成功"的宣称，
> 红绿结论以父 Agent 执行 `tester-execution-request-6.json` 后的落盘证据为准。

### 9.1 授权边界（本轮实际改动）

| 对象 | 改动 |
|---|---|
| `trader/tests/test_phase2_review2_reproduction.py` | **新增**，5 个用例，自包含 fakes（不复用 acceptance 文件的 fixture，避免跨模块导入——`trader/tests/` 无 `__init__.py`） |
| `docs/PHASE2_INDEPENDENT_TEST_REPORT.md` | 仅追加本节 |
| `artifacts/foundation/phase2/tester-execution-request-6.json` | 新增执行请求 |
| 生产代码 / 工程师测试 / conftest / CI / 契约与迁移文档 / workflow state | **未触碰** |

### 9.2 B2 —— MARKET BUY 按 quote 结算的超量成交被冲突拒绝

**契约推导（手算）**：`OrderSpec` 对 MARKET 取参考价，`qty * price = 100` USDT 是报价预算
（适配器据此发 `quoteOrderQty`）。venue 返回 `1.1 BTC @ 90`，实际支出 `1.1 * 90 = 99 ≤ 100`，
与 `observe()` 已有的 MARKET 豁免注释（"MARKET 订单以 quote 结算，venue 可能给出不同 base 数量"）
同源，是合法结算事实。期望：订单被记录、成交被入账、不抛 `Conflict`。

**代码证据（反例根因）**：
- `models.OrderAggregate.apply()`：`self.booked_qty + execution.qty > self.command.qty` 即抛
  `Conflict("Execution does not match order quantity/symbol")`，无 MARKET 豁免；
- `service.record_execution()` 捕获后把成交存为 `applied=False`（真实成交未入账）、
  `_record_quantity_conflict` 留痕，最终 `raise Conflict("EXECUTION_KEY_CONFLICT")`；
- `models.OrderAggregate.observe()` 前置界检查 `cumulative_qty > command.qty` 同样无 MARKET 豁免，
  与其自身的 FILLED 豁免互相矛盾。

**用例**（期望红，断言写契约期望、**不**写当前行为）：
1. `test_b2_market_buy_overfill_must_be_booked_not_conflicted`：place 后两层预约 `100.1`；
   `record_execution(1.1 @ 90)` **不得**抛 Conflict（当前抛 `EXECUTION_KEY_CONFLICT`，即红）。
   通过后的完整期望：`booked_qty=1.1`、`booked_quote=99`、USDT `901`、BTC `1.1`、
   position `1.1`、两层 holds 清空、`conservation_mismatch == {}`。
2. `test_b2_market_filled_observation_may_exceed_order_qty`：`observe(FILLED, cumulative_qty=1.1)`
   必须返回 True 且状态 FILLED、两层预约释放（当前 observe 返回 False，即红）。

### 9.3 B3 —— MARKET 终态 FILLED 的双层预约残留

**契约推导**：MARKET BUY `qty=1, price=100`（预约 `100.1` 双层），真实成交 `0.5 @ 100`，
随后 observe FILLED `cumulative_qty=0.5`。终态后两层（`state.holds` 与 `books[book].holds`）
都不得残留预约；预约资金不得在订单已结束后继续占用可用额。

**测试顺序设计（区分两种释放）**：用例在 `record_execution` 与 `observe` 之间显式断言
两层各残留 `100.1 - 0.5*100*1.001 = 50.05`（按比例释放、订单仍 PARTIALLY_FILLED），
从而终态后的清空只能来自"终态观察释放"，与"真实成交释放"可区分。

**诚实记录一个分歧**：审核员预期"双层 hold 残留"复现于
`_reconcile_order_evidence` 仅在 `booked_qty == command.qty` 时释放。但本测试员独立读码发现
`service.observe()` 在 `order.status in TERMINAL` 时有一个独立分支直接调用
`release_order_hold`（同时清账户层与 book 层），不依赖 `_reconcile_order_evidence`。
因此本用例**按当前读码预期可能通过（不红）**。若执行结果确为绿，正确结论是：
"B3 在该路径不构成生产缺陷，审核结论里的该条不成立"，而不是把断言改弱；若执行结果为红，
则以红证据为准。用例本身按契约期望书写，长期充当该释放路径的守卫。

用例：`test_b3_market_filled_releases_both_hold_layers`（含中途 `50.05` 残留断言、
终态余额 `950`/`0.5`、守恒断言）。

### 9.4 B4 —— 迁移未把账户层 hold 镜像进 UNALLOCATED，`allocate` 可划走已预约资金

**契约推导（手算）**：遗留快照 `balances = {USDT: 1000}`、账户层
`holds = {"qts-legacy": 100.1}`、`books = {}`。两层预约要求"同一份资金在账户层与 book 层
各记一次"；迁移后资金全部落在 `UNALLOCATED`，则 `UNALLOCATED.holds` 必须含同一笔 `100.1`，
`book_available(UNALLOCATED, USDT) = 899.9`。

**代码证据（反例根因）**：`books.migrate_book_ledger()` 仅复制
`balances/positions`；`store.decode_state()` 内联调用它，因此 decode 完成时
`UNALLOCATED.holds` 已为空。`books.allocate()` 只检查 `book_available(UNALLOCATED)`，
于是 `allocate 1000` 会成功，把仍在预约中的 `100.1` 划走（划走后 UNALLOCATED 余额归零，
而账户层 `100.1` 预约仍在——账面可用与真实可用背离，后续成交将使 book 余额转负）。
`conservation_mismatch` 只对余额求和，**看不到**缺失的 hold，这正是该缺陷隐蔽的原因。

**用例**（期望红）：
3. `test_b4_migration_mirrors_legacy_hold_into_unallocated`：断言
   `book_hold_amount(UNALLOCATED) == 100.1`（当前为 0，即红）、`book_available == 899.9`、
   `conservation_mismatch == {}`（并注明：余额守恒绿恰好证明它检查不到本缺陷）。
4. `test_b4_allocation_cannot_spend_reserved_unallocated_funds`：`allocate 1000` 必须
   `TradingError("ALLOCATION_INSUFFICIENT_UNALLOCATED")` 且余额/预约不动（当前成功划走，即红）；
   正向对照：`allocate 899.9`（未预约部分）应成功，之后 `UNALLOCATED` 恰剩 `100.1` 且
   `book_available == 0`。

### 9.5 说明但不复现（按父 Agent 指示）

- **B1（2.1 语义）**：`backtest.py` 直接构造 `PlaceOrderCommand`、固定 `book:backtest`、
  无逐 book 回测产出。运行期断言需要接入策略/回测装配线，超出本轮"仅新增失败复现"的
  最小边界；最小替代路径是后续在回测装配处加守卫断言（adapter 收到的 `book_id` 恒为
  `book:backtest` 之外还必须有逐 book 产出可见），留待下一轮授权。
- **B5（2.1 策略直接构造命令）**：同源问题——策略绕过 `OrderSpec` 时没有运行期哨兵；
  需要策略 runner 侧注入点，同样超出本轮边界。
- **G4/T8（快照/命令证据）**：按父 Agent 安排在其修复后另行补测；本节不改动 §1–§8 的
  表头与结论，`test_attempt=1` 与 `review_rounds_completed` 均不重置。

### 9.6 执行请求（未执行）

请求文件：`artifacts/foundation/phase2/tester-execution-request-6.json`（collect /
定向 pytest / black --check / isort --check-only，与 §8 相同的命令模板）。
执行账户 `acct-repro` 与 acceptance 的 `acct-indep` 隔离，互不影响。
**结果未跑前不宣称任何一项复现成功**；执行后的红绿判定与证据将在 §10（或父 Agent
指定的位置）由测试员按落盘原文分析，`analyzed_by` 与 `authored_by` 同为测试员，
`executed_by` 为父 Agent。

---

## 10. 复现执行判读（authored_by + analyzed_by = 测试员；executed_by = 父 Agent）

> 依据全部来自落盘原文与退出码（`parent-execution-result-6.json` + raw 文件），
> 不采信摘要。测试文件哈希 `cd39b9da…`（执行前后一致，未回写）；
> acceptance 哈希仍为 `9a3cdee0…`（E4 证明未被新文件影响）。

### 10.1 证据

| 检查 | 证据 | 结果 |
|---|---|---|
| E0 collect | `repro2-collect-1.{log,exit}` | exit 0，收集 5 |
| E1 复现执行 | `repro2-1.{log,xml,exit}` | exit 1；JUnit `tests=5 failures=4 errors=0 skipped=0`，无 skip/xfail |
| E2 black | `repro2-format-1.{log,exit}` | exit 0，`1 file would be left unchanged` |
| E3 isort | `repro2-isort-1.{log,exit}` | exit 0 |
| E4 回归旁证 | `indep-acceptance-6.{log,xml,exit}` | exit 0，JUnit `tests=31 failures=0 skipped=0`，31 PASSED |

### 10.2 判读

| 项 | 用例 | verdict | 判读 |
|---|---|---|---|
| **B2** | `…overfill_must_be_booked_not_conflicted` | **复现成立（红）** | 失败原文即反例本体：`record_execution` 抛 `Conflict("EXECUTION_KEY_CONFLICT")`，1.1 @ 90（花费 99 ≤ 100 预算）的真实成交被存为 `applied=False`、未入账、余额/持仓不动 |
| **B2** | `…filled_observation_may_exceed_order_qty` | **复现成立（红）** | `observe` 返回 False：`models.OrderAggregate.observe()` 的界检查（`cumulative_qty > command.qty`）无 MARKET 豁免，与其自身 FILLED 豁免的 quote-settlement 注释矛盾 |
| **B3** | `…market_filled_releases_both_hold_layers` | **pass（守卫）** | 终态 FILLED 经 `observe()` 的 TERMINAL 分支清空两层预约（中途 50.05 残留断言先执行，证明是终态释放而非成交释放）。**审核员"B3 双层 hold 残留"在该路径不成立**，§9.3 的读码分歧以执行证据裁定 |
| **B4** | `…migration_mirrors_legacy_hold…` | **复现成立（红）** | `book_hold_amount(UNALLOCATED) == 0`（期望 100.1）：`migrate_book_ledger` 只复制 balances/positions，账户层 hold 未镜像进 book 层 |
| **B4** | `…allocation_cannot_spend_reserved…` | **复现成立（红）** | `DID NOT RAISE TradingError`：`allocate` 只看 `book_available`，当前可把仍在预约中的 100.1 全额划走 |

**结论：4/5 复现成立，B2 与 B4 是可交工程师的生产缺陷；B3 不构成缺陷，保留为该释放路径的长期守卫。**

修复后的复验门（测试员将执行，须全绿才算修复完成）：
1. `test_phase2_review2_reproduction.py` 5 例全部 PASSED 且无 skip/xfail；
2. `test_phase2_independent_acceptance.py` 31 例回归全绿（防修复引入回归）；
3. 按修复方案补充边缘覆盖（由测试员在工程师修复后依 diff 独立推导，不预先写死）。

环境备注：E1 日志中的 `PytestCacheWarning: [WinError 5]` 是沙箱拒绝写 `.pytest_cache`，
属环境现象，不影响判读；Pydantic 弃用警告来自 `trader/api/routes/chat.py`，与本测试无关。

### 10.3 收口

- B2/B4 反例交工程师修复，修复依据即本报告 §9.2/§9.4 的根因定位与 §10.2 的失败原文；
- 测试员本轮**未改动**生产代码与既有测试（哈希证据如上）；B3 断言不弱化、不移除；
- `test_attempt=1`、`review_rounds_completed=2`、`next_review_round=3` 不变；
  §1–§9 保持原样，本节只追加；报告哈希因本节追加而变化，由父 Agent 重新计算；
- 未提交、未派审核员、未推进阶段 3。

---

## 11. 第 2 轮限定修复复验：独立 B1/B2/B3/B4/B5 覆盖（未执行）

> `authored_by = 29822745-df5a-4e5e-b5f7-f66230c24ddd`（独立测试员）。
> 背景：工程师写入权已收回并冻结；本轮**同一 `test_attempt=1`** 的限定复验，不消耗正式审核轮次。
> 本节**先声明**新增授权文件：`trader/tests/test_phase2_independent_review2_b1_b5.py`。
> **不采信** `docs/INTERFACE_CONTRACTS.md` §13.13（l.3139-3156）"第 2 轮审核修复"表里的
> "已修复"表述与工程师自报通过计数；oracle 一律先由契约/迁移说明手算推导，再读实现核对。
> 本节所有结论均为**未执行**；红绿以父 Agent 执行
> `artifacts/foundation/phase2/tester-execution-request-7.json` 后的落盘原文为准，
> `executed_by` = 父 Agent，`analyzed_by` = 测试员（§12）。

### 11.1 授权边界与变更集

| 对象 | 改动 |
|---|---|
| `trader/tests/test_phase2_independent_review2_b1_b5.py` | **新增**（31 例，自包含 fakes；不跨模块导入——`trader/tests/` 无 `__init__.py`） |
| `docs/PHASE2_INDEPENDENT_TEST_REPORT.md` | 仅追加本节（§1–§10 不动） |
| `artifacts/foundation/phase2/tester-execution-request-7.json` | 新增执行请求 |
| `trader/tests/test_phase2_review2_reproduction.py`（5 例） | **未改动**，保持冻结断言（B3 不弱化）；仅随工程师 isort 排序后的字节参与复跑 |
| `trader/tests/test_phase2_independent_acceptance.py`（31 例） | **未改动** |
| `trader/tests/test_phase2_independent_postgres.py`（5 例） | 未改动，仅复跑 |
| 生产代码 / 工程师测试 `test_phase2_round2_fixes.py` / 契约与迁移文档 / 全局 fixture / CI / workflow state | **未触碰** |

冻结输入：`artifacts/foundation/phase2/tester-input-snapshot-v5.json`，
`source_digest = 9b3ae191d7dab32b2ce69ad0cec71499c534656f79d3ae3c849d7178d4e87cd1`
（49 个阶段源文件，含 `test_phase2_review2_reproduction.py` 与 `test_phase2_round2_fixes.py`，无删除）。
本轮不复用任何旧冻结哈希作为"已验证"证据。

### 11.2 文件哈希（E11 落盘后由 §12 登记）

| 文件 | 哈希 |
|---|---|
| `trader/tests/test_phase2_independent_review2_b1_b5.py` | 待 E11 |
| `trader/tests/test_phase2_review2_reproduction.py` | 待 E11（v5 快照记录 `f06027ee…`，以 E11 为准） |
| `trader/tests/test_phase2_independent_acceptance.py` | 待 E11（§8 记录 `9a3cdee0…`） |
| `trader/tests/test_phase2_independent_postgres.py` | 待 E11 |
| 本报告 | 待父 Agent 在 §12 追加完成后计算，写入 `artifacts/foundation/phase2/report-hash-11.txt` |

### 11.3 手算 oracle（先于读实现）

| 项 | 推导 | 期望值 |
|---|---|---|
| quote 预算 | `qty * price`，**不含费**（13.13） | `1 * 100 = 100` |
| BUY 预约 | `qty * price * (1 + fee_rate)`（13.2 上界含费） | `100 * 1.001 = 100.1`（双层同额） |
| 入账 | 借 `execution.qty * execution.price` quote、贷 `execution.qty` base；费单独一条 | `1.1 @ 90` → 借 99、贷 1.1 BTC |
| 释放 | BUY 按比例 `qty * command.price * (1 + fee_rate)`，终态清空两层 | `0.5` 后各层余 `50.05` |
| 联合回测（fee_rate 10bps=0.001，平价 bar） | alpha `700-100-0.10` + 1 BTC@100；beta `300-100-0.10` 后卖 2 ETH@50 得 `+100-0.10` | alpha 权益 `699.90`、beta `299.80`、UNALLOCATED `0`、合计 `999.70`、费 `0.30`、cash `899.70` |
| 遗留迁移 | 账户层 hold `100.1` 且归属不可判定 → 镜像进 `UNALLOCATED.holds` | `book_available(UNALLOCATED)=899.9`；`allocate 1000` 拒绝、`899.9` 放行 |

### 11.4 新增用例（31）

| 块 | 用例数 | 覆盖 |
|---|---|---|
| B1 | 5 | 生产包内 `PlaceOrderCommand(` 构造点守卫（自动扫描，非人工 grep）；`order_type` 必填、`price_source` 非法/错配拒绝、`settlement` 显式；旧快照缺字段可解码但缺 `order_type` 在准入处拒绝；**真实 `UnifiedStrategyRunner`**（插件→`build_order_spec`→`to_command`→真实 service 准入→exchange）产出命令的显式性 |
| B2 | 8 | 预算内 base 超量必须入账（`1.1@90` → 99 ≤ 100）；累计 quote 超预算拒绝且**不二次扣款**（`0.5@100`+`0.6@100`）；边界"恰好等于预算"放行；超 1 分拒绝；BASE 结算与 LIMIT 仍受 base 数量硬界；先 observe 后成交的对账闭环；重复成交与乱序（更旧）观察必须无副作用 |
| B3 | 1 | 部分成交残留预约期间同 book 反向单被 `BOOK_OPPOSITE_ORDER_IN_FLIGHT` 拒绝；终态观察释放两层后反向单放行 |
| B4 | 6 | 遗留 hold 镜像进 `UNALLOCATED`；重复迁移幂等且不臆造策略 book；未知 book 仍阻塞（`ORDER_BOOK_NOT_FOUND`，不静默修复）；`allocate` 不得划走已预约资金（含正向对照 `899.9`）；Control 延迟路径下失败分配**原子且可观测**（FAILED + 事件 + 不重试）；Control 进程 `CONTROL_PROCESS_CANNOT_EXECUTE` |
| B5 | 11 | **真实 `EventBacktestRuntime(...).run()` 联合多策略**（非 `TradingService.books()` 替代）：逐 book 权益/余额/可用/预约与总额守恒；按 `strategy_id` 的预算隔离；无初始分配/未配置 book/超额/非正金额/非法 book id 均拒绝；信号缺 `strategy_id` 与未知 strategy 拒绝；**重复 book 配置应拒绝**；单策略旧路径兼容；`manifest_hash` 覆盖账本配置 |

### 11.5 先读实现后确认的实现事实（写用例时的已知非对称）

1. 预算用 `qty*price`（无费），预约/释放用 `qty*price*(1+fee_rate)`——两者不可混用；
2. `record_execution` 的未解决码是 `EXECUTION_QUANTITY_CONFLICT`，但抛出的异常文本是 `EXECUTION_KEY_CONFLICT`；
3. `observe()` 的上界豁免**只**对 quote 结算的 MARKET BUY 生效；FILLED 恰好覆盖豁免只按 `order_type == "MARKET"` 判定；
4. MARKET quote BUY 超量成交后状态仍为 `PARTIALLY_FILLED`，`_reconcile_order_evidence` 的
   `booked_qty == command.qty` 条件不触发，预约只能由终态 `observe`/取消释放；
5. **B5 重复配置无校验**：`backtest.py` 只按 dict 键建立 `configured_books` 集合，两个 strategy
   映射到同一 book 时不会被拒绝——该用例按契约期望书写，**预期红**；若红则记为 blocking finding。

### 11.6 verdict（未执行；§12 依落盘原文裁定）

| 项 | verdict | 说明 |
|---|---|---|
| B1 / B2 / B3 / B4 / B5 | **待执行（unexecuted）** | 断言已按契约期望冻结；未跑前不宣称通过 |
| B5 重复配置 | **预期红** | 读码已确认当前实现无校验（见 11.5.5）；红则列为 blocking finding，交工程师修复后再复验 |
| 2.1 / G6 | 静态核对部分仍为 partial（不因本轮改写判读） | 本轮新增的是运行期守卫（runner/API 路径/回测/适配器/构造点扫描），不是全量接口一致性核对 |
| T8 / G4 | 待执行（执行请求 7 的 E0–E11 全量落盘） | 不复用旧冻结哈希；报告哈希独立 |

### 11.7 未执行检查与局限

1. **CI 未纳入新模块**：本轮无权编辑 `.github/workflows/ci-gate.yml`（l.221-223 现有条目），
   新模块的 CI 登记列为未执行检查，需父 Agent 决定是否另授权；
2. **跨进程真实 Control→Execution 未验证**：本轮用同 store 的 `defer_send=True` +
   `resume_queued()` 语义等价路径，未拉起两个真实进程；
3. **PG 用例依赖环境**：`POSTGRES_CONNECTION_STRING`（或 `POSTGRES_HOST/DB/USER/PASSWORD/PORT`）
   缺失时该批为 blocked，不记为通过；
4. **`scripts/rehearse_trading_runtime.py:79` 直接构造 `PlaceOrderCommand`**：生产包外（演练脚本），
   本轮扫描守卫只覆盖 `trader` 包；此处如实记录为观察项，由父 Agent 判断是否纳入契约范围；
5. 真实交易所/真实下单/前端与阶段 3 均不在范围。

### 11.8 执行请求

`artifacts/foundation/phase2/tester-execution-request-7.json`：E0 collect（预期 31）、
E1 新模块定向执行 + JUnit、E2 `black --check --diff`、E3 `isort --check-only`、
E4 复现 5 例、E5 acceptance 31 例、E6 独立 PG 5 例、E7 P0 99 例、E8 阶段 2 核心、
E9 阶段 0、E10 阶段 2 PG 11 例、E11 文件哈希。
父 Agent 不得回写测试员文件；E2/E3 只做检查不改文件。

### 11.9 阻塞记录（追加于交付后，未改变任何 verdict）

| 事实 | 证据 |
|---|---|
| 父 Agent 会话不可继续 | `get_agent_status(18e99d90-33f5-41f9-8a2f-672d109886bb)`：`status = error`，`lastError = "This chat uses magpie, but your current provider is openai. Switch to magpie to continue."`，`activeTurn = null` |
| 测试员仍不能自执行 | 本会话 `pwsh` 前置失败：`SetNamedSecurityInfoW failed (Win32 5): grantWrite(...)`（沙箱拒绝为工作区授予写权限） |
| 执行证据 | `artifacts/foundation/phase2/` 最新文件仍为 `tester-execution-request-7.json`，无任何 `-7` 的 log/xml/exit |

因此 **E0–E11 全部未执行**，§11.6 的各项 verdict 维持"待执行（unexecuted）"，不因阻塞而改判为
pass/fail/partial；文件哈希（§11.2）维持"待 E11"。

处置与边界（与工作流 §2.1 一致）：
- 不修复沙箱 ACL、不提权、不开旁路执行通道；
- 不由测试员自执行——否则 `authored_by` 与 `executed_by` 同为测试员，破坏证据链的角色分离；
- 不创建第 3 轮审核员、不提交、不推进阶段 3、不改生产代码与工程师断言；
- `test_attempt=1`、`review_rounds_completed=2`、`next_review_round=3` 不变。

需要的外部动作（二选一，均由人/父 Agent 决定）：
1. 修复父 Agent 的 provider（切回 magpie）后由其执行 request-7 并回传 raw 文件；或
2. 由人直接按 request-7 的 E0–E11 命令行执行并把 raw log/xml/exit 回传测试员；此时
   `executed_by` 记为执行者，`authored_by`/`analyzed_by` 仍为测试员。

拿到原文后测试员追加 §12：文件哈希、逐项最终 verdict、blocking_findings、局限与未执行检查，
然后释放写入权。

---

## 12. execution-7 原始证据的独立判读（测试员）

分析人：`29822745-df5a-4e5e-b5f7-f66230c24ddd`（测试员）；执行人：`18e99d90-33f5-41f9-8a2f-672d109886bb`（父 Agent）。
本章所有判定来自磁盘原文：`parent-execution-result-7.json`、`parent-execution-supplement-7.json`、
`indep-r2-7.{log,xml,exit}`、`indep-r2-collect-7.log`、`indep-r2-format-7.log`、`indep-r2-isort-diff-7.log`、
`hashes-7-before.log` / `hashes-7-after.log`、`pg-preflight-7.json`。**未采用任何摘要计数。**

### 12.1 执行口径与完整性

| 项 | 值 |
|---|---|
| request | `tester-execution-request-7.json`（`request_sha256=d9ac8c94…`） |
| request 输入 digest（v5，49 项） | `9b3ae191d7dab32b2ce69ad0cec71499c534656f79d3ae3c849d7178d4e87cd1` |
| **执行全集 digest（50 项）** | `ae12d56577fe0098c7bac68b4ab6133571b726fbe2039d46ab52dce6b7c3d6c7` |
| 两者关系 | 执行全集 = v5 的 49 项 + 测试员获授权新增的 `trader/tests/test_phase2_independent_review2_b1_b5.py`。**request 里的 v5 digest 只代表输入集合，不代表执行后的全集**；登记 verdict 时以 `ae12d565…` 为执行口径 |
| 哈希前后一致 | `hashes-7-before.log` 与 `hashes-7-after.log` 逐字节相同 → 父 Agent 未改写任何测试员文件 |
| PG | 父 Agent 读取既有 `test_trading_postgres.CONNECTION_STRING` 本地默认配置、核验 loopback、`SELECT 1` 预检成功，仅对子进程注入 `POSTGRES_CONNECTION_STRING`；凭据未输出、配置未持久修改（`pg-preflight-7.json`） |

### 12.2 逐条命令与结果（0 skip / 0 error；JUnit 无 xfail）

| id | argv（完整） | exit | 结果 |
|---|---|---|---|
| E0 | `python -m pytest --collect-only -q trader/tests/test_phase2_independent_review2_b1_b5.py` | 0 | 收集 **31** |
| E1 | `python -m pytest -q --tb=short -rA --junitxml=artifacts/foundation/phase2/indep-r2-7.xml trader/tests/test_phase2_independent_review2_b1_b5.py` | 1 | 31 例 = **22 passed / 9 failed / 0 errors / 0 skipped** |
| E2 | `python -m black --check --diff --line-length 100 trader/tests/test_phase2_independent_review2_b1_b5.py` | 1 | 仅 comprehension 折行差异（测试装置问题） |
| E3 | `python -m isort --check-only --profile black trader/tests/test_phase2_independent_review2_b1_b5.py` | 1 | `SETTLEMENTS`、`Execution` 顺序（测试装置问题） |
| E4 | `python -m pytest -q --tb=short trader/tests/test_phase2_review2_reproduction.py` | 0 | **5 全绿**（第2轮修复复验：B2×2、B4×2 由 execution-6 的红转绿；B3 守卫仍绿、未被弱化） |
| E5 | `python -m pytest -q --tb=short --junitxml=… trader/tests/test_phase2_independent_acceptance.py` | 0 | **31 全绿** |
| E6 | `python -m pytest -q --tb=short trader/tests/test_phase2_independent_postgres.py` | 0 | **5 全绿**（依赖注入 DSN；该文件不是原门禁 11 例集合） |
| E7 | `python -m pytest -q --tb=short <P0 5 文件>` | 0 | **99 全绿** |
| E8a | `python -m pytest -q --tb=short trader/tests/test_phase2_foundation_safety.py trader/tests/test_phase2_round2_fixes.py` | 0 | **48 全绿**（测试员文件集=48，**不等于**旧门禁口径 66） |
| E8b | `python -m pytest -q --tb=short <service/runtime/strategy/api/adapter>` | 0 | **89 全绿** |
| E9 | `python -m pytest -q --tb=short trader/tests/test_phase0_foundation_safety.py` | 0 | **2 全绿** |
| E10 | `python -m pytest -q --tb=short trader/tests/test_phase2_postgres.py` | 0 | **5 全绿**（实际 5 例，非请求中假设的 11 例） |
| E11 | `python -c "import hashlib…"`（4 个测试文件 + 报告，前后各一次） | 0 | 前后一致 |
| S1（父补充） | required-core-7：service / shared_core / oms_idempotency / oms_position_lot | 0 | **66 全绿**（补齐旧门禁口径） |
| S2（父补充） | required-pg-7：trading_postgres / projection_rebuild | 0 | **11 全绿**（补齐旧门禁口径） |

### 12.3 九个失败的独立分类（测试员自行判定，非采用工程师/父 Agent 定位）

| # | 用例 | 原文 | 类别 | 契约/代码依据 | 未执行到的断言 | 生产缺陷 |
|---|---|---|---|---|---|---|
| 1 | `test_b1_the_strategy_runner_goes_through_the_spec_factory` | `AttributeError: 'RecordingRuntime' object has no attribute 'store'`（strategy.py:295） | **测试装置错误** | runner 读取 `runtime.store/service/account_id/strategy_start_allowed/require_ownership/place`；我的 stand-in 缺 `store` | receipt 状态、command 各显式字段、`validate_order_command` | 否 |
| 2 | `test_b2_a_terminal_observation_then_the_real_fill_settle_cleanly` | `TypeError: OrderObservation.__init__() missing 1 required positional argument: 'account_id'` | **测试装置错误** | models.py:79-86：`account_id` 是必填位置参数（我此前误删） | 观察先于成交的整条闭环、余额 901 / BTC 1.1、两层 hold 释放 | 否 |
| 3 | `test_b2_duplicate_and_stale_evidence_are_inert` | 同上 | **测试装置错误** | 同上 | 重复成交惰性、乱序（更旧）观察被忽略 | 否 |
| 4 | `test_b3_a_partially_filled_market_buy_blocks_the_same_book_opposite` | 同上 | **测试装置错误** | 同上；risk.py:133-153 同 book 反向在飞单抛 `RiskRejected("BOOK_OPPOSITE_ORDER_IN_FLIGHT")`，且先于余额检查 | 残留预约期间反向单被拒、释放两层后反向单放行 | 否 |
| 5 | `test_b4_a_missing_strategy_book_stays_unattributed` | `KeyError: 1` | **测试装置错误（数据访问）** | books.py:218-231 存 `dict{"requested_book_id","reason"}`，不是元组 | `ORDER_BOOK_NOT_FOUND` 归因、`BOOK_ATTRIBUTION_UNKNOWN` 阻断 | 否 |
| 6 | `test_b4_a_deferred_allocation_failure_is_atomic_and_observable` | `KeyError: 'book:alpha'` | **测试装置错误（断言过度指定）** | service.py:245-250：仅非延迟路径 `_register_book`；延迟排队阶段目标 book 本不存在（与迁移文档 88-89“失败即留空 book”口径不一致，见 12.5） | 排队语义、FAILED 与 error 文本、命令记录、事件留痕、二次 drain | 否 |
| 7 | `test_b5_each_strategy_stays_inside_its_own_budget` | `AssertionError: a book spent beyond its own allocation`（实际 2 笔 ETH 成交） | **测试员 oracle 错误** | 该例主张“alpha 不得花 beta 的钱”；beta 的 2 ETH 往返在其自有 300 预算内，属合法成交 | beta 两笔成交的合法性核对 | **否（生产侧成立）**：fills 中无任何 BTCUSDT，说明 alpha 的越权意图确实被拒 |
| 8 | `test_b5_duplicate_book_configuration_is_refused` | `Failed: DID NOT RAISE <class 'ValueError'>` | **生产缺口（保留红）** | backtest.py:143-155 只校验形态/集合，197-215 `configured_books` 由 dict 键直接构造，未校验重复值；契约 13.13 要求每策略独立预算 | — | **是（blocking）** |
| 9 | `test_b5_the_single_strategy_path_still_runs_and_reports_its_book` | `assert Decimal('1000') == Decimal('1100')`（0 成交、cash 1000） | **测试装置错误（fixture 未走旧路径）** | 旧路径的合法输入是不带 `strategy_id` 的信号；我复用了带 `strategy_id` 的 `_joint_strategy`，在 `strategy_books=None` 下不成交 | 旧 `book:backtest` 的成交与权益 | 否 |

### 12.4 已被证据确认成立的经济结论（22 绿覆盖）

* B1.1 生产包内只有 `trader/trading/order_spec.py`、`trader/trading/store.py` 构造 `PlaceOrderCommand`（扫描守卫真实跑过当前工作区）；
* B1.2 `order_type` 缺失 → `ORDER_TYPE_REQUIRED`；非法/错配 `price_source` → `INVALID_ORDER_PRICE_SOURCE`（含 MARKET 借用 LIMIT 源）；
* B1.3 显式 `settlement`；B1.4 旧快照缺 `settlement` 可解码、缺 `order_type` 在准入处拒绝（兼容不等于猜测）；
* **B2 核心五条全绿**：预算内 base 超量（1.1@90 → 99）入账、累计超预算（0.5@100+0.6@100）拒绝且二次不扣款、边界相等（100 = 100）放行、超 1 分拒绝、BASE 结算与 LIMIT 的数量硬界仍在；
* **B4 四条全绿**：遗留 hold 镜像进 `UNALLOCATED`（可用 899.9）、镜像幂等且不臆造策略 book、已预约资金不可被分配（可用部分 899.9 仍可分配）、Control 进程不可执行（`CONTROL_PROCESS_CANNOT_EXECUTE`）；
* **B5 联合账本手算对照全绿**：alpha 699.90 / beta 299.80 / UNALLOCATED 0 / 合计 999.70 = cash 899.70 + 1 BTC×100、fees 0.30；六类非法配置拒绝；signal 缺/未知 `strategy_id` 被拒（`STRATEGY_ID_REQUIRED` / `UNKNOWN_STRATEGY_BOOK`）；`manifest_hash` 稳定且随分配变更；
* repro 5（含 B3 守卫）与 acceptance 31 全绿；P0 99、phase2 core 66、PG 11、phase0 2 全绿，0 skip。

### 12.5 发现

**Blocking（交工程师，用例保持红）**
`P2-B5-DUPBOOK`：联合回测允许两个 `strategy_id` 共用同一 `book`，预算被静默合并。
证据用例 `test_b5_duplicate_book_configuration_is_refused`（DID NOT RAISE ValueError）；实现位置
`trader/trading/backtest.py:143-155`、`197-215`；契约依据 `docs/INTERFACE_CONTRACTS.md` 13.13。
测试员**不改断言迁就实现**；修复后须由测试员独立复跑，不由工程师自证。

**非阻塞（记录）**
* `P2-B4-DEFER-REGISTER`：延迟路径不登记空 book，与迁移文档“失败即留空 book 可见”口径不一致（service.py:245-250）；仅可见性差异，无资金影响。
* `P2-B2-CODE-TEXT`：冲突异常文本为 `EXECUTION_KEY_CONFLICT`，未解决差异记录为 `EXECUTION_QUANTITY_CONFLICT`（排障一致性问题）。

### 12.6 对本章作者此前事实陈述的更正

§11.7 第 4 条“`scripts/rehearse_trading_runtime.py:79` 存在直接构造 `PlaceOrderCommand` 的站点”**错误**：
该脚本 L65 起调用 `build_order_spec`，L79 为 `return spec.to_command()`，不存在直接构造点。
B1 扫描用例只扫描 `trader` 包，结论不受影响；该条观察撤回。历史段落按要求保留，更正追加于此。

### 12.7 限定修订与待复跑

修订仅落在测试装置/数据访问/测试员 oracle 与格式，逐 hunk 见
`artifacts/foundation/phase2/tester-revision-diff-7.md`（含 old→new 全文，可逆推出修订前内容）。
用例数不变（31），`test_b5_duplicate_book_configuration_is_refused` 断言未动。

| 项 | 值 |
|---|---|
| 修订前 SHA256 | `c36ce8e1f23beb085e5961ff4208eefcfb67265b1993570672c7e9777d9ba9fc` |
| 修订后 SHA256 | **待 `request-8` 的 E0（`hashes-8.log`）回填**（测试员无执行能力，不自算哈希、不写猜测值） |
| 字节级备份 | 测试员无 shell，**未做字节复制备份**；以“修订前哈希 + 逐 hunk 完整 diff（可逐字复原）”替代，并已如实声明 |

复跑请求：`artifacts/foundation/phase2/tester-execution-request-8.json`（全新文件名：
`indep-r2-8.*`、`indep-r2-collect-8.*`、`indep-r2-format-8.*`、`indep-r2-isort-diff-8.*`、`repro2-8.*`、`hashes-8.*`），
范围限定为新模块 31 例 + collect + black/isort 检查（不改我的文件）+ 直接关联守卫 repro 5；
**预期 30 passed / 1 failed**（唯一红仍为重复 book 配置）。

### 12.8 verdict 汇总（不使用 partial / overall pass）

* B1：4 绿 / 1 待复跑 → **待复跑**；B2：6 绿 / 2 待复跑 → **待复跑**；
* B3：0 绿 / 1 待复跑 → **待复跑**（repro 的 B3 守卫另计为绿，未被弱化）；
* B4：4 绿 / 2 待复跑 → **待复跑**；
* B5：8 绿 / 2 待复跑 / **1 生产红** → **fail**；
* 本轮 **overall = not_pass**：存在生产缺口（红且冻结）+ 6 项待独立复跑 + 新模块尚未登记 CI 入口。

### 12.9 局限与未执行检查（不标为通过）

* 测试员会话无命令执行能力（沙箱 ACL 拒绝），所有红绿来自父 Agent 回传原文；
* PG 用例依赖父 Agent 的本地 DSN 与临时 schema；测试员未接触凭据，也未验证真实跨进程 Control→Execution
  （仅以 `defer_send` + 共享 store 的语义等价覆盖）；
* 新模块未登记进 `.github/workflows/ci-gate.yml`（本轮授权不含 CI 编辑）→ **开放项**；
* E8a/E10 文件集与旧门禁口径（66/11）不同，实际数字以本次执行为准，未沿用旧口径；
* 修订后哈希待 `request-8` 的 E0 回填。

### 12.10 修订 diff 的复原校验失败（如实登记）

父 Agent 依据 `tester-revision-diff-7.md` 的 hunk 清单在内存中反向复原修订前内容并计算哈希，结果见
`artifacts/foundation/phase2/revision-verification-8.json`：

| 项 | 值 |
|---|---|
| `current_sha256`（修订后） | `4e04a7b310785635c7e4de88e312ee7f3448b70bed3ee82ca3fb6feb1abf2514` |
| `expected_pre_revision_sha256` | `c36ce8e1f23beb085e5961ff4208eefcfb67265b1993570672c7e9777d9ba9fc` |
| `reconstructed_pre_revision_matches` | **false** |
| `after_test_count` | 31（用例数未变） |

因此 §12.7 与 diff 文件中"可逐字复原修订前内容"的表述**不成立**，降级为"逐 hunk 描述（未经字节复原校验）"。
已知漏项：H14 只记录了 legacy 用例的函数体，漏了其 docstring 的新增段落，已补为 H15。
若补上 H15 后复原仍不匹配，则承认：**修订前字节无法用文档形式复原**，修订前状态只能由
`c36ce8e1…`（hashes-7-before.log）与 execution-7 的 log/JUnit 证据固定。

修订后哈希按父 Agent 计算结果登记为 `4e04a7b3…`（**非测试员自算**），待 request-8 的 E0 交叉核对。

---

## 13. request-8 原始证据的独立判读（测试员）

分析人：`29822745-df5a-4e5e-b5f7-f66230c24ddd`；执行人：`18e99d90-33f5-41f9-8a2f-672d109886bb`。
依据磁盘原文：`parent-execution-result-8.json`、`indep-r2-8.{log,xml,exit}`、`indep-r2-collect-8.log`、
`indep-r2-unexpected-8.log`（`--tb=long`）、`indep-r2-format-8.log`、`indep-r2-isort-diff-8.log`、
`repro2-8.{log,xml,exit}`、`hashes-8.log` / `hashes-8-after.log`、
`tester-output-snapshot-execution-8.json` / `execution-8-after-snapshot.json`、
`revision-verification-8-h15.json`。**实测结果登记为 27 passed / 4 failed / 0 errors / 0 skipped，
不是本报告此前的预期值 30/1；预期错在低估了 fixture 身份错误的影响面（见 13.2）。**

### 13.1 执行口径与完整性

| 项 | 值 |
|---|---|
| 执行全集 source_digest（50 项） | `03bc352af5232480bb771e3b6b612d442d996c1bce2f3fbb9770cd4bb87fe9e2` |
| 新模块 SHA256 | `4e04a7b310785635c7e4de88e312ee7f3448b70bed3ee82ca3fb6feb1abf2514`（与 §12.10 登记值一致，E0 已核对） |
| 报告（§12.10 版本）SHA256 | `16c5897b8839d67edbbc82e34274cc70b9c337d8ac3c4557a11b70c96e5e3f61` |
| 哈希前后 | `hashes-8.log` 与 `hashes-8-after.log` 一致；执行前后 50 源文件相同 → 执行期间源文件与报告均未被回写 |
| E1 collect | 31（用例数不变） |
| **E2 运行** | **31 = 27 passed / 4 failed / 0 errors / 0 skipped** |
| E3 black `--check --diff` | exit 0 |
| E4 isort `--check-only --diff` | exit 0 |
| E5 repro 5 守卫 | 5 绿 / 0 skip（B3 守卫未被弱化） |

### 13.2 四例失败的独立分类（依据 raw + 生产代码原文）

三条意外失败的 raw（`indep-r2-unexpected-8.log`，`--tb=long`）显示失败点都在
`assert accepted/fresh is True` / `assert await current.observe(...)` 得到 **False**，异常未抛出。

| # | 用例 | raw 失败点 | 独立分类 | 依据 |
|---|---|---|---|---|
| 1 | `test_b2_a_terminal_observation_then_the_real_fill_settle_cleanly` | L806 `assert accepted is True` → False | **测试 fixture 错误（观察身份）** | 生产 `models.py:313-317`：`observation.broker_order_id` 与已存 broker 身份不一致 → `Conflict("Exchange order identity changed")`；`service.py:716-725` 捕获并记 `ORDER_OBSERVATION_CONFLICT`、返回 False。我的 fixture 给观察编造了 `b-r2`，而下单经 `RecordingExchange.place_result` 返回的真实身份是 `b-1` —— 订单身份不可变契约被我的假数据违反 |
| 2 | `test_b2_duplicate_and_stale_evidence_are_inert` | L868 `assert fresh is True` → False | 同上（同一身份不一致点） | 同上 |
| 3 | `test_b3_a_partially_filled_market_buy_blocks_the_same_book_opposite` | L934 `observe(...)` → False | 同上 | 同上 |
| 4 | `test_b5_duplicate_book_configuration_is_refused` | DID NOT RAISE ValueError | **生产反例（保留红）** | 即 §12.5 的 `P2-B5-DUPBOOK`，request-7 与 request-8 在同一未修生产字节上稳定复现 |

**修正（仅 4 处观察身份，属本轮授权范围）**：`broker_order_id="b-r2"` →
`broker_order_id=exchange.place_result.broker_order_id`（引用 fixture 真实返回的同一 ID）。
不改生产、不把 `True` 改 `False`、不跳过断言、不动已绿格式；三条用例其后的经济断言
（UNBOOKED_EXECUTIONS 闭环、重复/乱序惰性、B3 双层释放）保持原样，待 request-9 复跑。

### 13.3 H15 复原闭合（不改写失败历史）

`revision-verification-8-h15.json`：`reconstructed_pre_revision_matches = true`；
复原产物 `reconstructed-pre-request8-test_phase2_independent_review2_b1_b5.py` 的 SHA256 精确等于
修订前 `c36ce8e1…`（还需在 H1–H15 之外恢复 legacy 函数 docstring 后的空行）；
`verified-revision-7-to-8.patch` 为真实 diff；31 例前后同名，duplicate-book 函数文本完全相同。
第一次 `revision-verification-8.json` 的失败记录**原样保留**；修订后 `4e04…` 版本已备份为
`pre-request8-test_phase2_independent_review2_b1_b5.py`。§12.10 的"待核对"就此闭合：
修订前/后字节与 hunk 清单三方一致。

### 13.4 verdict 汇总（更正 T/G 口径后；不用 partial 收口 overall）

按 `FOUNDATION_AGENT_WORKFLOW.md` §4 更正（原 §12/analysis-7 中 T1-T8"不适用"、G1-G6"不在测试员
职责内"的表述错误，已在 `tester-analysis-result-7.json` 内更正并在此登记）：

* B1：5 例全绿（含真实 runner 端到端）→ **pass**；B2：6 绿 / 2 待复跑（fixture 修正后）→ **待复跑**；
  B3：1 待复跑 → **待复跑**；B4：6 全绿 → **pass**；B5：10 绿 / **1 生产红** → **fail**；
* T1 pass（B5 per-book 隔离绿）；T2 partial（原子性绿；真实 PG 回滚/重启/并发未覆盖）；
  T3 partial（B2 边界绿；费用币种、取消/UNKNOWN 未覆盖）；T4 pass（B4 全绿）；
  T5 partial（B1 绿；3 例观察闭环待 request-9；跨进程仅语义等价）；T6 pass（E8a 48 绿含 book/UNKNOWN 门禁）；
  T7 partial（有真实红→绿敏感性证据：repro 4 红→绿、duplicate-book 稳定红；未做第二处针对性注入实验）；
  T8 blocked（新模块未进 CI job）；
* G1 pass / G2 pass / G3 pass / G4 pass（13.3 已闭合）/ G5 pass / **G6 fail**（CI 入口未登记 +
  迁移说明与 defer 路径登记空 book 行为不一致）；
* **overall = not_pass**：`P2-B5-DUPBOOK` 生产红 + 3 例待复跑 + T8/G6/T2/T7 开放项阻止收口。

### 13.5 后续

复跑请求：`artifacts/foundation/phase2/tester-execution-request-9.json`（全新文件名；collect +
新模块 31 例 + black/isort `--check --diff` + repro 5 守卫；生产未变，旧回归沿用 execution-7 的
真实字节匹配证据，不做全量重跑）。预期 30 passed / 1 failed（唯一红 = duplicate book）；若再有意外红，
按原文分类、保留断言、不迁就实现。生产修复（`P2-B5-DUPBOOK`）由父 Agent 在测试装置复跑取证之后派工程师，
不与本轮 fixture 修正混在同一次执行取证里。

### 13.6 写入权释放声明

**全部写入权 RELEASED**（测试模块 `trader/tests/test_phase2_independent_review2_b1_b5.py`、本报告
`docs/PHASE2_INDEPENDENT_TEST_REPORT.md`、`artifacts/foundation/phase2/` 下测试员产物）。自本声明起，
测试员不再观察父 Agent 产物、不自行回填或追加文字，等待父 Agent 下一次授权。
正式轮次保持 completed=2 / next=3、test_attempt=1；本轮为同一独立复验周期内的诊断复跑，
不冒充新的生产修复/验收轮次。

---

## 15. 第 4 轮（R3-B2-HOLDRELEASE / R3-B1-FIRETEST）独立 oracle 重算与修订（测试员）

分析人：`29822745-df5a-4e5e-b5f7-f66230c24ddd`；基线执行（父）：result-11（三独立套件 67 = 59 绿 / 8 红 / 0 skip）。
依据：契约 13.14、工作单 `parent-round4-fix-work-order.json`、`indep-round4-baseline-11.log` 原文（8 处失败的
实际值全部与契约新公式吻合）及 `fire_test.py`/`signal.py`/`oms_callback.py` 当前字节。

### 15.1 修订前备份（父 Agent 落盘，SHA256 与其 result-11 冻结值一致）

`pre-round4-test_phase2_review2_reproduction.py`（`f06027ee…`）、
`pre-round4-test_phase2_independent_acceptance.py`（`9a3cdee0…`）、
`pre-round4-test_phase2_independent_review2_b1_b5.py`（`3660d0a9…`），
清单 `pre-round4-independent-backup-manifest.json`。修订后哈希由父在 request-12 计算回填。

### 15.2 8 处失败 = 旧 oracle 编码了旧生产行为（非新生产缺陷），按契约 13.14 独立重算

统一新公式：**释放 = qty × execution.price +（仅当 fee_asset == quote 时的实际 fee）**；
`fee_rate` 仅为 admission 缓冲；BASE/第三币种费不释放 quote 预约；quote-settled MARKET BUY 不以
`booked_qty == qty` 判终态，报价预算耗尽或 venue 终态事实才终态并清真实剩余。

| # | 用例（文件） | 旧值 | 新 oracle（手算） | 断言变化 |
|---|---|---|---|---|
| 1 | repro `test_b2_market_buy_overfill…` | 成交后两层全释放 | 100.1 − 1.1×90 = **1.1** 两层保留，PARTIALLY_FILLED | 改保留断言，并**新增** venue FILLED 观察终态清余断言（增强） |
| 2 | repro `test_b3_market_filled_releases…` | 50.05 | 100.1 − 0.5×100 = **50.1**；随后 FILLED 终态清余（原断言保留） | 常量重算 |
| 3 | acceptance `test_t3_partial_fills…` | 40.04 | 200.2 − (100+0.05) − 50 − 10 = **40.15**（BASE/BNB 费不释放 quote） | 常量重算 |
| 4 | acceptance `test_t3_genuine_fills…` | 40.04 | 200.2 − (100+0.01) − 50 − 10 = **40.19** | 常量重算（4 处） |
| 5 | acceptance `test_t7_mutation_book_hold…` | 150.15 | 200.2 − 0.5×100 = **150.2**（两层失同步断言不变） | 常量重算 |
| 6 | acceptance `test_t7_normal_partial…` | 150.15 | **150.2** | 常量重算 |
| 7 | review2 `test_b2_base_overfill…` | 全释放 | **1.1** 两层保留 + **新增** PARTIALLY_FILLED 断言（增强） | 改保留并增强 |
| 8 | review2 `test_b2_cumulative_quote…` | 50.05 | 100.1 − 0.5×100 = **50.1**（首笔后/被拒后/重放后） | 常量重算（3 处） |

修订均**收紧或保持经济约束**：未删断言、未把 True 改 False、未跳过；两处新增"venue 终态证据控制最终
释放"与"PARTIALLY_FILLED 状态"断言。8 处基线实际值（1.100/50.100/40.150/40.190/150.200）与重算值逐一
吻合；其余 59 例（含 t3_cancel/unknown、b2_two_fills_exactly、b2b FILLED 观察、t7 正常全额）在修复后
字节仍绿——与 13.14 的 LIMIT/BASE 不变、终态清理语义一致。

### 15.3 新增独立模块（R3-B1 此前无独立覆盖）

`trader/tests/test_phase2_independent_round4.py`（4 例，oracle 先于实现从契约推导）：fire_test 显式
MARKET+参考价、`to_order_params` 缺失/非法 fail-closed（精确错误串）、LIMIT 携带声明价而 MARKET 不伪造
限价、旧 OMS `_resolve_signal_order_type` 同一 fail-closed 且不按价格猜类型。broker 零调用级联由工程师
round4 用例覆盖，测试员以解析器层 fail-closed 独立覆盖。

### 15.4 待执行与遗留

request-12（`tester-execution-request-12.json`）待父代跑：四独立文件 collect+运行、black/isort 只检查、
round4 工程师 11、required-core/P0/phase0+safety、PG 三件（唯一临时 schema、DSN 仅环境变量）、60+新文件
哈希前后与 digest。verdict 待 raw 复跑后按新契约重新归并（§14 中受本轮生产语义变更影响的 T 项证据同步
复核），不以改值或新模块绿宣称 overall pass。遗留：新 round4 模块未登记 CI（本轮 CI 对测试员只读）。

---

## 16. request-12 判读与新增模块 fixture 修正（测试员）

执行（父）：result-12，digest `b4a7a03f…`，执行前后 61 源文件与报告一致；父在冻结前把两个 round4
模块追加进既有 CI job（T8 登记，GitHub Actions 实际矩阵仍未运行）。实测：E1 collect=71；**E2 三套件
67 passed / 0 failed / 0 skipped**（8 处重算 oracle 全部按新契约转绿）；E3 新模块 **3 绿 / 1 红**；
E4/E5 exit 0；E6 工程师 round4 11 绿；E7 278 绿；E8 PG 18 绿；补充 required-core 66、P0 99、
phase0+safety+round2 52、PG 三件 21，均 0 fail/0 skip/0 xpass。

**唯一红的独立分类**：`test_fire_test_emits_explicit_market_with_a_reference_price` 在
`MarketData(...)` 构造处抛 `TypeError: missing 1 required positional argument: 'data_type'`
（`strategy_protocol.py:69-70`：`data_type` 为必填）——**测试 fixture 错误**，失败发生在构造、
尚未进入 fire_test 业务断言，不构成生产通过或失败的证据。修正（仅新模块，2 处）：导入
`MarketDataType` 并传 `data_type=MarketDataType.TICKER`；不改生产/工程师断言/已绿三套件。
request-12 的原红记录（`indep-round4-new-12.log`）原样保留。

E2 三套件 67 绿即为 §15.2 重算 oracle 的实测确认；2.1–2.12 / T1–T8 / G1–G6 的重新归并**待新模块
4 例复跑后**一并完成（不以其它全绿忽略此红）。复跑请求：`tester-execution-request-13.json`
（新模块 4 + collect + black/isort 只检查 + 三套件 67 守卫 + 哈希前后；生产与 CI 未再变，
其余回归引用 result-12 的相同字节证据）。修订后哈希以 hashes-13 原文登记。

---

## 17. request-13 判读与第 4 轮最终归并（测试员）

执行（父）：result-13，61 文件 digest `a0d40fb6…`；相对 result-12 唯一源文件变化 =
`trader/tests/test_phase2_independent_round4.py`（SHA256 `f03a0b23…`，父计算）；执行时报告
SHA256 `5c409751…` 且执行前后相同；`failed_check_ids=[]`。raw 核读：E1 collect=71；
**E2 新模块 4 passed / 0 failed / 0 skipped**（`indep-round4-new-13.log` L14-18，fire_test 显式
MARKET 用例随 fixture 修正转绿）；**E3 三套件 67 passed / 0 failed / 0 skipped**；E4/E5 exit 0；
E0/E6 哈希前后一致。生产/CI/工程师测试字节与 result-12 相同，required-core 66 / P0 99 /
phase0+safety+round2 52 / 工程师 round4 11 / PG 21 引用 result-12 真实字节证据（**不是** request-13
新执行）；GitHub Actions 实际矩阵仍未运行。

### 17.1 按第 4 轮新契约重新归并（2.1–2.12 / T1–T8 / G1–G6）

* 2.1–2.12：全部 **pass**。与 §14 相比新增/更新的证据：2.5/2.7 的 hold 释放语义按契约 13.14 重算并在
  E3 67 绿中实测确认（t3_partial 40.15、t3_genuine 40.19、t7 150.2 两层同步、repro 1.1/50.1 + venue
  终态控制清余、review2 1.1/50.1 拒绝与重放不二扣）；2.11 终态判定改为"报价预算耗尽或 venue 终态事实"
  （b2b FILLED 观察、b3 FILLED、overfill 新增终态断言均绿）；其余项沿用 §14 证据（相同字节）。
* T1–T8：全部 **pass**。T2/T3/T4/T6/T7 证据同 §14（相同字节引用）；T3 费用币种语义（BASE/第三币种
  费不释放 quote）新增实测（t3_partial 重算绿）；T5 增补 R3-B1 fail-closed 独立覆盖（新模块 4 绿：
  fire_test 显式 MARKET、to_order_params ORDER_TYPE_REQUIRED/UNSUPPORTED_ORDER_TYPE、LIMIT 声明价/
  MARKET 不伪造限价、旧 OMS 解析器不按价格猜类型）；T7 另有第 3 处真实红→绿（8 处旧 oracle 红在契约
  重算与生产修复后转绿，属旧断言编码旧行为而非人为注入，与 monkeypatch 注入分开列明）；T8 CI 静态
  登记由父完成（round4 两模块入既有 job），GitHub Actions 实际矩阵未运行（限制）。
* G1–G6：全部 **pass**。G1：71 独立绿 + result-12 相同字节回归全绿（66/99/52/11/21/278）0 skip；
  G2：本轮 8 处 oracle 全部先于实现按契约 13.14 手算（§15.2 表），新模块 oracle 先于实现推导；
  G3：实际运行、0 skip、无删断言（修订均为收紧/增强）；G4：digest/哈希前后一致、变更集 =
  授权内 4 文件 + 报告 + 父登记的 CI（第 3 轮 blocked 的指纹重算由父在最终快照独立完成）；G5：职责
  未越界（工程师 5+10 文件、父仅 CI 登记、测试员仅独立资产）；G6：契约 13.14/迁移说明/CI 与实现一致
  （R3-B2/R3-B1 均有独立 + 工程师双侧证据）。

### 17.2 overall 与遗留

**overall = pass**（第 4 轮独立测试员口径：71 例独立测试全绿 + 相同字节回归引用全绿，2.1–2.12、
T1–T8、G1–G6 逐项 pass；旧 oracle 修订均按契约重算并保留/增强经济约束，无 blocking findings）。
非"71 绿即过"：每项落到具名用例与 raw log/XML/exit。遗留限制：GitHub Actions 实际矩阵未运行；
Control→Execution 两进程物理隔离未单独测试；相同字节回归引用边界（result-12 字节 = result-13 字节，
digest 链一致）；Compose/前端/Binance/24h 沙箱未执行；正式第 4 轮只读审核待父生成快照后创建。

### 17.3 写入权释放声明

**全部写入权 RELEASED**（独立测试文件、报告、artifacts 测试员产物）。报告自 §17.3 冻结，新哈希由父
计算。正式轮次 completed=3 / next=4、test_attempt=1；不提交、不派审核、不进阶段 3。

---

## 14. P2-B5-DUPBOOK 修复后的独立复验与全量 verdict（测试员）

分析人：`29822745-df5a-4e5e-b5f7-f66230c24ddd`；执行人（父 Agent）：`18e99d90-33f5-41f9-8a2f-672d109886bb`；
工程师（限定修复）：`d29dfb7b-dfac-4c6d-857c-129a3ed578e5`（报告 message `61fbeee5-2eda-46fe-985c-ae5821700f22`）。
依据磁盘原文：`parent-execution-result-10.json`（16 项检查 + HASHES-before/after）、
`tester-output-snapshot-execution-10.json`、`indep-r2-10.{log,xml,exit}`、`repro2-10.*`、
`indep-acceptance-10.*`、`indep-pg-10.*`、`p0-10.*`、`phase2-core-10.*`、`required-core-10.*`、
`phase0-10.*`、`required-pg-10.*`、`phase2-pg-10.*`、black/isort/diff-check/core-no-io 各 exit 文件、
`hashes-10-before/after.log`，以及生产/文档/CI 的当前字节原文。**判读不依赖摘要。**

### 14.1 执行口径与完整性

| 项 | 值 |
|---|---|
| 分支 / base / HEAD | `feat/foundation-refactor` / `1c587f33c…` / `1c587f33c…`（与全部历史周期一致） |
| 50 文件 source_digest | `a057b7610084928067ff151e511563347ee8ee808a4e02c4ea5b81062b18aad2` |
| 相对 request-9 冻结集的变更 | 仅 5 个文件：`ci-gate.yml`、`INTERFACE_CONTRACTS.md`、`PHASE2_BOOK_LEDGER_MIGRATION.md`、`test_phase2_round2_fixes.py`、`trader/trading/backtest.py` —— 与工程师申报的限定修复范围一致，无越界改动 |
| 执行期间完整性 | `source_unchanged=true`、`metadata_unchanged=true`；报告哈希执行前后同为 `ec479bfd…`（§13 版本），源文件与测试员文件未被回写 |
| harness 事件 | 一次 E15 启动前的本地白名单准备错误（argv[0] 断言先于 git 例外），已修正白名单并 resume 完成 E15/E16 与最终指纹；`counts_as_test_failure=false`，不掩盖任何红证据（E1–E14 均已 exit 0） |
| 检查结果 | 16 项检查 + 前后哈希全部 exit 0，`failed_check_ids: []` |

| 检查 | 内容 | 计数（JUnit） |
|---|---|---|
| E1 | 新独立模块 `test_phase2_independent_review2_b1_b5.py` | **31 passed / 0 failed / 0 skipped** |
| E2/E3 | 新模块 black `--check --diff` / isort `--check-only --diff` | exit 0 |
| E4 | repro 5 守卫 | 5/0/0 |
| E5 | acceptance 31 | 31/0/0 |
| E6 | 独立 PG 5 | 5/0/0 |
| E7 | P0 | 99/0/0 |
| E8 | phase2 safety 29 + 工程师 round2 修复 21 | 50/0/0 |
| E9 | required-core | 66/0/0 |
| E10 | phase0 | 2/0/0 |
| E11/E12 | required-PG / phase2-PG | 11/0/0、5/0/0 |
| E13–E16 | black/isort（trader 全目录）、`git diff --check`、`check_core_no_io` | exit 0 |

所有 JUnit 计数均含 `failures=0 / errors=0 / skipped=0 / xfailed=0 / xpass=0`（`xpass_log_count=0`）。

### 14.2 P2-B5-DUPBOOK 修复的独立确认（生产红 → 绿闭环）

* **修复点原文**（`trader/trading/backtest.py:183-198`）：构造 `owners: dict[str, str]`，逐条校验
  `strategy_books`；空 `strategy_id` 拒绝、非 `book:` 形态拒绝、**同一 `book_id` 已有归属者即抛
  `ValueError`（信息含 book id 与两个 strategy id），发生在建立账户/划拨/事件之前**——与契约要求
  "每策略独立 book 与预算、重复映射在解析阶段拒绝"一致，且拒绝点先于任何状态创建（无部分初始化残留）。
* **独立证据**：我编写的 `test_b5_duplicate_book_configuration_is_refused`（断言自 request-6 起冻结未动）
  在 request-7/8 对未修字节稳定红（DID NOT RAISE），request-10 对修复后字节转绿（`indep-r2-10.log` L42）。
  红→绿由同一未修改断言完成，非工程师自证。
* **正对照未受损**：合法映射下 31 例全绿——联合权益/预算逐 book 输出、per-book 隔离、六类配置拒绝、
  legacy 单策略路径、manifest_hash 随划拨变化均 PASSED；工程师侧 `test_phase2_round2_fixes.py` 21 例
  （含联合回测与正对照，见契约 13.13 引用）随 E8 全绿。
* **文档同步核验**：`INTERFACE_CONTRACTS.md` 13.13（L3151/L3153）明确"两个 strategy_id 映射到同一 book
  时在建立账户/划拨前拒绝（ValueError）…不合并预算；每个值必须唯一"；`PHASE2_BOOK_LEDGER_MIGRATION.md`
  L84/L90 补记 Control/Execution 分工与"直接（非 deferred）划拨在转账前单独登记目标 book"——
  我此前的非阻塞观察 **P2-B4-DEFER-REGISTER 已按文档化行为闭合**。
* **blocking findings：无遗留**。非阻塞观察 P2-B2-CODE-TEXT 本轮无新证据，维持原记录不升级不关闭。

### 14.3 业务项 2.1–2.12 重新归并（逐项证据）

| 项 | verdict | 证据（本轮真实重跑，全部 0 skip） |
|---|---|---|
| 2.1 唯一订单规格入口 | **pass**（由 partial 上调，理由见注） | 构造点扫描（`test_b1_only_the_order_spec_factory_builds_production_order_commands`，可执行，覆盖 trader 全源）+ 真实 runner 端到端走 factory + `StrategySignal` 为封闭 schema（策略无适配器参数通道）；rehearse 脚本静态核对见 §12.6 更正 |
| 2.2 订单类型不由 metadata 默认 | pass | `test_t5_order_type_must_be_explicit_and_metadata_is_not_consulted` |
| 2.3 价格来源显式记录 | pass | `test_t5_limit_uses_the_signal_price_and_market_traces_its_reference` + B1 price_source 用例 |
| 2.4 `book_id` 持久化稳定可回溯 | pass | PG `...survive_a_restart`、`...migration_is_idempotent...` |
| 2.5 逐币种守恒 | pass | `test_t3_partial_fills_base_quote_third_asset_fees_and_conservation`（`conservation_mismatch == {}`） |
| 2.6 显式划拨；未划拨恒在未分配 | pass | `test_t5_control_only_queues...` + PG 并发分配 |
| 2.7 双层预约同事务回滚 | pass | `test_t2_*` 两条 + PG 事务回滚用例 |
| 2.8 同币反向在途先拒绝（明确拒绝码） | pass | `test_t9_same_book_opposite...` + B3（`RiskRejected`，精确 `BOOK_OPPOSITE_ORDER_IN_FLIGHT`） |
| 2.9 book 不借用他 book 或账户 | pass | `test_t1_*` 两条 + PG 并发 place + M1 变异 + B5 per-book |
| 2.10 归属不明阻断且不猜历史 | pass | `test_t3_unknown_place...`、`test_t4_*`×4、`test_t6_*`×3 + B4 |
| 2.11 单一 CAS、终态不回退、去重 | pass | `test_t5_terminal_status...`、`test_t3_duplicate_execution...`、`test_g_single_status...` |
| 2.12 迁移不猜历史 + 联合回测逐 book 权益/预算 | **pass**（由 blocked 上调） | `test_t4_*` + PG 迁移幂等 + B5 联合权益/预算、不借用他 book 估值、manifest_hash 三例全绿；**此前唯一生产阻塞 P2-B5-DUPBOOK 已修复并由冻结断言独立确认（14.2）** |

注：2.1 上调依据——§11 的 partial 判定先于构造点扫描存在；该扫描现为可执行断言且对当前字节绿，
"策略不得直接构造适配器参数"由封闭 `StrategySignal` schema + runner 强制走 factory 结构性排除；
全量接口一致性核对归入 G6 而非 2.1。2.12 的 blocked 曾因我的 oracle 错误与生产缺陷双重原因，前者已于
request-8 修正（§13.2），后者本轮修复并红→绿。

### 14.4 T1–T8（区分真实生产红→绿与受控注入）

| 项 | verdict | 证据 |
|---|---|---|
| T1 | pass | `test_t1_book_cannot_borrow...` + 正对照 + B5 per-book 隔离（无 BTC 成交、alpha 余额不变） |
| T2 | pass | 内存：`test_t2_*` 两条（monkeypatch 注入失败点）；**真实 PG**：事务回滚（`...rolls_back_every_projection...`）、并发分配不丢更新、并发 place 不双花、重启存活（`indep-pg-10` 5/5，0 skip） |
| T3 | pass | 三币种费用+守恒、成交可交换不丢失、重复执行 no-op、取消释放双层、UNKNOWN 保持双层并阻断（`test_t3_*`×5）+ B2 部分成交边界 |
| T5 | pass | 规格显式×3、价格来源、经济幂等含 book_id、终态不回退+重复观察幂等、Control 只排队/Execution 才生效 + B4 Control 不可执行（`NotReady`）；两 OS 进程级物理隔离未单独测试，入口语义以真实服务代码路径覆盖（工程师 split_control 用例随 required-core 66 绿为辅证） |
| T4 | pass | `test_t4_*`×4 + B4×6 + PG 迁移幂等 |
| T6 | pass | `test_t6_*`×3 + E8 safety 29（含 book/UNKNOWN 门禁）+ B4 `book_reasons` 阻断 |
| T7 | pass | **受控注入**：3 处 monkeypatch 变异（book 借用账户余额 / 缺 book 预约层 / 成交未释放 book 层）均被独立断言命中，正常路径两例绿；**真实生产红→绿**：round-1 repro 4 红→绿、P2-B5-DUPBOOK 稳定红→绿（同一冻结断言，§14.2） |
| T8 | pass | CI 静态核验：`ci-gate.yml` L224 登记新独立模块、L186 PG job 含独立 PG 模块、L221–223 acceptance/repro/round2_fixes；完整命令/计数/skip/环境/指纹见 request-1…10 与 result-10；测试设计与实现分开（测试员文件与工程师文件不同源不同作者）。**限制：GitHub Actions 实际矩阵未运行**（本地无法触发，列入 unexecuted） |

### 14.5 G1–G6

| 项 | verdict | 证据 |
|---|---|---|
| G1 | pass | 必需回归套件真实全绿：repro 5 / acceptance 31 / 独立 PG 5 / P0 99 / phase2-core 50 / required-core 66 / phase0 2 / required-PG 11 / phase2-PG 5，全部 0 fail/0 skip/0 xfail/0 xpass |
| G2 | pass | oracle 全部先于读实现由契约条款与手算推导（§3–§10、§11 oracle 表），不从实现生成期望值 |
| G3 | pass | 实际运行、PG 0 skip；无删断言、无宽泛异常吞错、无 xfail 放行；本轮对装置的两类修正（异常类型收紧为精确类型+精确 code；观察身份 fixture 引用真实 venue 身份）均按 raw 独立分类后进行（§12/§13） |
| G4 | pass | 分支/base/HEAD 与 50 文件指纹一致（digest `a057b761…`）；变更集仅工程师申报的 5 文件；执行前后源与报告哈希一致；harness 白名单事件如实登记且非测试失败 |
| G5 | pass | 职责与写入权未越界：工程师仅改其 5 文件；测试员仅测试/报告/artifacts；无未授权账户/部署/历史操作；双方写入权按指令释放 |
| G6 | pass | CI 入口登记并静态核验；契约 13.13 与迁移说明同步重复 book 拒绝及 defer/direct 登记差异；架构无拓扑变化不需改动；接口/实现/文档三者在修复点一致 |

### 14.6 overall 与遗留限制

**overall = pass**（阶段 2 独立测试员口径：2.1–2.12、T1–T8、G1–G6 全部 pass，无 blocking findings；
此前唯一生产阻塞 P2-B5-DUPBOOK 已由工程师限定修复并经同一冻结断言红→绿独立确认）。
这不是"31 绿即过"：每项均落到具名用例与原始 log/XML/exit。

遗留限制与未执行项（不构成门禁失败，如实登记）：
1. GitHub Actions 实际矩阵未运行（CI 为静态核验；首次真实运行在父 Agent 合并时）；
2. Control→Execution 的两 OS 进程级物理隔离未单独测试（入口语义已有真实代码路径证据）；
3. Compose/Docker、前端 typecheck/build、Binance 网络/私流/真实资金、24h 沙箱均未执行（阶段边界外）；
4. 正式第 3 轮只读审核尚未发生（父 Agent 后续动作，不替代亦不被本报告替代）；
5. 测试员无 shell，全部红绿均来自父 Agent 回传的 raw 文件（本周期 10 次执行请求的证据链完整）。

### 14.7 写入权释放声明

**全部写入权 RELEASED**（独立测试文件、本报告、`artifacts/foundation/phase2/` 测试员产物）。
本报告自 §14.7 起冻结；报告新哈希由父 Agent 计算。测试员不再观察父 Agent 产物或自行追加文字。
正式轮次 completed=2 / next=3、test_attempt=1；本轮属同一独立复验周期的修复后终验。

---

## 18. 第 4 轮 fail 后修复复验（R4-F1-LEGACY-HOLD-RELEASE；独立测试员，`post-round4-fix-retest`）

分析人/作者：`af633d31-6e57-43d1-afa3-7d80444610c5`（本轮独立测试员；延续 `29822745…` 的独立证据链，
`test_attempt=1` 不重置）；执行人：**工程师 `d29dfb7b…` 受控代跑（待回传 raw；测试员命令通道仍被
ACL 阻塞，未修 ACL、未换终端）**；审核输入 = `artifacts/foundation/phase2/review-round5-snapshot.json`
（61 文件，source_digest `70763820…`）。本轮不是新阶段，是第 4 轮审核 fail（审核员 `f1de3b20…`，
报告 message `aee47c2c…`，唯一阻塞 finding = R4-F1）后的修复复验。

### 18.1 反例设计（先按契约手算，后读实现；未先读工程师用例）

手算口径（`INTERFACE_CONTRACTS` 13.2/13.12/13.14、`PHASE2_BOOK_LEDGER_MIGRATION` §4.2/§8）：

1. 旧快照：1000 USDT / 0 BTC；一张 OPEN BUY 1 @ 100 的订单 `book_id=""`，账户层 hold
   100.1 USDT（100 quote + 100×0.001 费率缓冲）。
2. `decode_state` 迁移后：`UNALLOCATED` 余额 1000；镜像预约必须在 `UNALLOCATED` 上且恰为 100.1
   （同一经济预约的两层视图，非二次占用）；可用额度 899.9。
3. 终态观察 `CANCELLED`（累计 0）后：账户层与 `UNALLOCATED` 镜像层**同时清空**；余额不动
   （仍 1000）；逐资产守恒成立；`allocate 1000` 必须成功（否则资金搁浅）；归属阻断
   `BOOK_ATTRIBUTION_UNKNOWN` **保留**（新风险仍被拒）。
4. 前修复行为（按 R4-F1 finding 描述复现）：book 层用原始 `command.book_id`（""）定位 →
   `release_book_hold` 静默 no-op → 镜像 100.1 永久滞留 → 可用 899.9 → `allocate 1000` 被拒
   `ALLOCATION_INSUFFICIENT_UNALLOCATED`。此即"红"。

设计完成并写入用例后才读工程师回归用例（`test_phase2_round4_fixes.py`，数值口径与本手算一致，
未借用其实现）。新增 4 例（写入 `trader/tests/test_phase2_independent_round4.py`，参数化计 2）：

* `test_r4f1_migration_mirrors_the_legacy_hold_on_unallocated`（迁移镜像 = 100.1 前置断言）；
* `test_r4f1_old_release_logic_strands_the_mirror_and_blocks_allocation`（前修复逻辑复现：镜像滞留 +
  `allocate 1000` 拒绝——独立"红"证明，不依赖 git checkout 旧代码）；
* `test_r4f1_terminal_observation_releases_both_layers_and_unblocks_funds[CANCELLED|EXPIRED]`
  （服务层绿 oracle：两层清空、余额不动、守恒、归属阻断保留（marker + `book_reasons` +
  `risk_view.reasons/ready=False` + `place` 拒绝）、`allocate 1000` 成功、UNALLOCATED=0 /
  book:gamma=1000、账户总额不变）。

红前/绿后边界如实声明：本轮"红"由前修复逻辑复现用例证明（finding 描述 + 现产代码 diff 佐证），
非对 base commit 的直接 checkout 执行；"绿"由对当前磁盘代码的执行证明。

### 18.2 静态快照核对（测试员直接读两份快照 JSON 逐文件比对）

* round-4 → round-5：61 文件两两比对，**恰 4 文件变化**：`trader/trading/core.py`（39096252→befa241a）、
  `trader/tests/test_phase2_round4_fixes.py`（f3b2a9e5→aaf28a48）、`docs/INTERFACE_CONTRACTS.md`、
  `docs/PHASE2_BOOK_LEDGER_MIGRATION.md`。生产面仅 `core.py` 一文件（`books.py`/`service.py`/
  `store.py` 等全部字节不变），与工程师申报一致。
* `docs/FOUNDATION_WORKFLOW_STATE.json` 单独漂移（d1be022f→cb49d52c，父维护元数据，按交接单列）；
  快照记录的 tester_report_hash `2758020f…` 为本轮编辑前字节。
* 聚合 digest `70763820…` 与磁盘 61 文件哈希的复核在 execution_request-14（测试员无 shell）。
* 预期磁盘 vs 快照差异 = 仅测试员授权两文件（`trader/tests/test_phase2_independent_round4.py`、
  本报告），其余 59 文件必须与快照字节一致，否则停止并回传差异。

### 18.3 文档口径核对（静态）

* 终态释放两层：契约 13.2（2756-2758"终态清理覆盖两层"）与 13.14 R4-F1 行、迁移说明 §4.2
  （"终态会同时释放账户层与 UNALLOCATED 镜像层的预约"）一致；实现 `core.release_order_hold` 经
  `execution_book_id` 解析（core.py:62-72），`mirror_account_holds` 只镜像无策略 book 的 hold。
* 归属阻断保留：迁移说明 §4.2 第 2 步"该订单的归属仍未决"+ §8 已知限制一致；`resolve_attribution`
  仅经 `RESOLVE` 证据清除。
* `RESOLVE` 只接受 FILLED：契约 13.12（3117-3118"只接受已完全成交、无剩余预约的订单"）与迁移
  说明 §4（部分成交后撤销不得清除归属）一致。

### 18.4 执行请求与状态

* `artifacts/foundation/phase2/tester-execution-request-14.json`（authored_by = 本测试员，
  addressed_to = 工程师 `d29dfb7b…`，**非父 Agent**）：新反例 4 例 + 独立三套件 67 + 独立 PG +
  工程师 round4/round2 fixes + foundation_safety + core66 + P0 五件 + phase0 + digest/哈希复核。
* verdict 状态：**pending raw 回传**。2.1–2.12 / T1–T8 / G1–G6 的既有 pass 引用 §17（相同字节边界：
  本轮 4 文件变化不触及既有 71 例所依赖的字节，受影响项 2.7/2.10/2.12/T4/G6 于回传后按 §18.1
  反例 + 复跑结果重判）；R4-F1 待绿后判 pass。
* 未执行（沿用 §17.2 遗留）：GitHub Actions 实际矩阵、Compose/Docker、前端、Binance 网络/私流、
  24h 沙箱、真实资金、Control→Execution 物理进程隔离。

### 18.5 写入权声明

本轮测试员仅写入：`trader/tests/test_phase2_independent_round4.py`（+4 例）、本报告 §18、
`artifacts/foundation/phase2/tester-execution-request-14.json`。未触碰生产/工程师测试/契约/CI/
`.workbuddy/`/`workflow/`；未提交/推送。等 raw 回传后补 §18.6 verdict 归并与最终 JSON。

### 18.6 最终判读（request-14 raw 回传后；authored/analyzed = 测试员，executed = 工程师）

执行：工程师受控代跑 `tester-execution-request-14.json`（E0–E12），raw log/xml/exit 落盘
`artifacts/foundation/phase2/*-14.*`；测试员直读原文核对，不依赖摘要。

**指纹与隔离**：E0/E11 digest `70763820…` match=True；61 文件磁盘比对唯一 MISMATCH =
`trader/tests/test_phase2_independent_round4.py`（快照记录 `f03a0b23…` → 磁盘 `9e50bdf7…`，
即测试员 +4 例的授权变化）；E0 与 E11 完全一致（执行期间无回写）；生产/工程师测试/CI 0 MISMATCH；
4 个修复文件磁盘哈希 = round5 快照记录值（core.py `befa241a…`、round4_fixes `aaf28a48…`、
两 docs `72ddfb7e…`/`f05d168b…`）。报告 metadata 漂移 `2758020f…`→`17772a97…` 为 §18 授权变化；
`FOUNDATION_WORKFLOW_STATE.json` 为父维护单列漂移。git：branch/HEAD 正确、无 staged、
`git diff --check` 仅 CRLF 提示。

**计数（JUnit/log 原文）**：E1 collect=75（5+31+31+8）；E2 独立 round4 模块 **8 passed**（4 旧 + 4 新：
`test_r4f1_migration_mirrors…`、`test_r4f1_old_release_logic_strands…`、
`test_r4f1_terminal_observation…[CANCELLED]`/`[EXPIRED]`）；E3 独立三套件 **67 passed**；
E4 工程师 round4 fixes **12 passed**（含新回归）；E5 safety+round2 **50**；E6 core66 **66**；
E7 P0 **99**；E8 phase0 **2**；E9 真实 PG（PostgreSQL 16，唯一临时 schema，DSN 仅环境变量）
**18 passed / 0 skipped**（含独立 PG 5 例：两 book 重启存活、book 预约失败全投影回滚、并发划拨
不丢更新、同 book 并发下单不双花、旧行迁移幂等 append-only 保留）；E10/E10b black/isort exit 0。
合计 **322 passed / 0 failed / 0 skipped / 0 xfail**。各套件 2 条 warning 为
`trader/api/routes/chat.py` Pydantic V2 弃用提示（既有、与 R4-F1 无关、非门禁）。

**R4-F1-LEGACY-HOLD-RELEASE：pass（红→绿独立闭环）**：
- 红：前修复逻辑复现（book 层用原始 `command.book_id`）→ `UNALLOCATED` 镜像 100.1 滞留 →
  `allocate 1000` 拒绝 `ALLOCATION_INSUFFICIENT_UNALLOCATED`（E2 实测）；
- 绿：终态 CANCELLED/EXPIRED → 账户层与 `UNALLOCATED` 镜像层同清、余额不动、逐资产守恒、
  `allocate 1000` 成功（UNALLOCATED→0 / book:gamma→1000 / 账户总额 1000 不变）、归属阻断保留
  （marker + `book_reasons` + `risk_view.reasons`/`ready=False` + `place` NotReady）；
- 修复面收敛：生产仅 `core.py` 一文件（`release_order_hold` 经 `execution_book_id`），
  `books.py`/`service.py`/`store.py` 字节不变；工程师回归用例同绿（E4）。

**受影响项重判**：2.7 **pass**（E2 4 新例 + E3 67 + E4 12 全部针对新 `core.py` 重跑）；
2.10 **pass**（绿例 `allocate 1000` 成功 + E3 67 + PG 划拨幂等/并发不透支 + 独立 PG 并发划拨）；
2.12 **pass**（镜像前置断言 + 静态快照 4 文件核对 + PG 迁移幂等/append-only 重跑）；
T4 **pass**（不猜归属、终态后 marker 保留、迁移幂等：E2/E3 + PG）；G6 **pass**（契约 13.2/13.12/13.14、
迁移 §4.2/§8、实现三方一致，见 §18.3）。

**其余项（2.1–2.6/2.8/2.9/2.11、T1–T3/T5–T8、G1–G5）**：pass——本轮为**全新重跑**而非同字节引用：
对应套件（独立 75、工程师 round4 12、safety/round2 50、core66 66、P0 99、phase0 2、PG 18）全部
在新 `core.py` 字节上重新执行且全绿；先前的逐项判读依据沿用 §17（测试文件字节除测试员 +4 例外
均不变）。

**overall = pass**（post-round4-fix-retest 口径：R4-F1 独立红→绿闭环 + 322 例全绿 0 skip +
指纹/隔离复核通过 + 文档口径一致；无 blocking findings）。

**局限与未执行（不标为通过）**：E9 集合与 result-12 的 21 差 3 例
（`test_trading_projection_rebuild.py` 不在本轮请求清单——测试员清单遗漏，非环境失败；其生产依赖
store/service/postgres 字节不变，引用 result-12 边界）；GitHub Actions 实际矩阵、Compose/Docker、
前端、Binance 网络/私有 WS、24h 沙箱、真实资金、Control→Execution 物理进程隔离未执行；
测试员无 shell，红绿均来自工程师回传 raw（E0/E11 前后一致性证明执行期间无回写）。

### 18.7 写入权释放声明

**全部写入权 RELEASED**（独立测试文件、本报告、`artifacts/foundation/phase2/` 测试员产物）。
最终 JSON 已发工程师（`d29dfb7b…`）；台账 R4-F1 的 `independent_retest` 由工程师按本结论置
pass。测试员不再写入；后续轮次（round-5 只读审核）由工程师/父 Agent 走既有流程。

---

## 19. evidence-completion-1 证据完整性补齐的独立判读（测试员；只读）

### 19.1 触发、范围与通道

触发：`artifacts/foundation/phase2/parent-final-evidence-gap-return.json`（`production_finding=false`、
`new_fix_attempt=false`）退回 3 个证据缺口：① 第 5 轮审核员 ACL 失败后自行换终端；② 多处 pytest 经
findstr 过滤，exit 0 是计数推断；③ `test_trading_projection_rebuild.py` 未含在 request-14 且 result-12
早于最终 `core.py`。冻结口径：61 文件 digest `58e7a0c947afb33d0caafb49d8fb7104ecdc0cef690be8c6df8babde9e054a47`。

本轮我先用**原有 pwsh 通道**做一次只读重算尝试，仍失败：
`SetNamedSecurityInfoW failed (Win32 5): grantWrite(D:\Code of Python\quant_trading_system)`；
**未修 ACL、未提权、未换终端**。执行全部由工程师常规通道完成；我直读
`run_evidence.py` / `run_evidence_pg_full.py` 源码确认：`subprocess.run(argv, cwd=ROOT, env=env,
stdout=log, stderr=subprocess.STDOUT)` 为直接 pytest 进程、`completed.returncode` 原样写入
`exit_code.txt`，无 shell 管道/findstr；`PYTHONDONTWRITEBYTECODE=1`、`-p no:cacheprovider`。

### 19.2 从 JUnit 自行重算逐组计数 + 原始退出码

各组 `junit.xml` 的 `<testsuite>` 属性 + 逐例 `<testcase>` 与 `pytest.log` 末行、`exit_code.txt` 对照：

| 组 | tests | failures | errors | skipped | passed（重算） | exit_code.txt | pytest.log 末行 |
|---|---|---|---|---|---|---|---|
| E2 | 8 | 0 | 0 | 0 | 8 | 0 | `8 passed, 2 warnings` |
| E3 | 67 | 0 | 0 | 0 | 67 | 0 | `67 passed, 2 warnings` |
| E4 | 12 | 0 | 0 | 0 | 12 | 0 | `12 passed, 2 warnings` |
| E5 | 50 | 0 | 0 | 0 | 50 | 0 | `50 passed, 2 warnings` |
| E6 | 66 | 0 | 0 | 0 | 66 | 0 | `66 passed, 2 warnings` |
| E7 | 99 | 0 | 0 | 0 | 99 | 0 | `99 passed, 2 warnings` |
| E8 | 2 | 0 | 0 | 0 | 2 | 0 | `2 passed, 2 warnings` |
| E9-PG（子集） | 15 | 0 | 0 | 0 | 15 | 0 | `15 passed, 2 warnings` |
| E9-PG-FULL | 23 | 0 | 0 | 0 | 23 | 0 | `23 passed, 2 warnings` |

汇总：离线七组 **304 passed / 0 failed / 0 errors / 0 skipped**（8 组离线+子集共 319，与
`runner.log` 一致）；验收去重口径 **327 = 304 + 23**（E9-PG 三文件完全包含于 E9-PG-FULL）。
全 9 份 `junit.xml` 搜索 `<skipped` / `<failure` / `<error` **0 命中**；9 份 `exit_code.txt` 均为
原文 `0`。`pytest.log` 为 pytest 直接输出（进度点 + warnings summary + `N passed`），无过滤摘要痕迹。
2 条 warning/组为 `trader/api/routes/chat.py` Pydantic V2 弃用提示（既有、非门禁）。

### 19.3 61 文件指纹重核

* `hashes-before.json` / `hashes-after.json` 各 **61 项**，逐行比对**全部相同**；`changed_files=[]`、
  `added=[]`、`removed=[]`（`hashes-diff.json`），两 manifest 与 `runner.log` / `runner-pg-full.log` 均
  `digest_unchanged=True`，前后 digest 均 `58e7a0c9…`。
* `review-round5-final-snapshot.json` 的 `files` 61 项与两份 hash 集**逐项一致**；关键值与既有 raw 交叉一致：
  `core.py` `befa241a…`、`test_phase2_round4_fixes.py` `aaf28a48…`、两 docs `72ddfb7e…`/`f05d168b…`、
  测试员文件 `9e50bdf7…`；`books.py`/`service.py`/`store.py` 与 round5 中间快照同字节。
* digest 口径源码核对：`run_evidence.py:72-74`、`run_evidence_pg_full.py:40-42` 均为
  `sha256(json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))`，
  与快照 `source_digest_format` 声明一致；每文件哈希来自 `read_bytes()`（源码 64-69 / 32-37）。
* 相对 round5 中间快照（`70763820…`）→ 最终快照（`58e7a0c9…`）：61 文件中**唯一变化** =
  `trader/tests/test_phase2_independent_round4.py` `f03a0b23…` → `9e50bdf7…`（我授权的 +4 例），
  其余 60 文件逐项相同——digest 变化的来源单一且可解释。
* **边界（如实）**：sha256 算术本身我无法执行（命令通道 ACL 被拒），我完成的是「集合相等 + 公式源码核对 +
  跨产物一致性」；已随结论提交受控请求 **E-R1**（一条 `python -c` 重算 after 集 digest、逐文件磁盘
  SHA256 比对及 before/after 相等性，回传 raw 到 `artifacts/foundation/phase2/evidence-completion-1/
  digest-recount-1.log`），供需要"已执行算术重算"的场合补齐；本结论不依赖该条算术。

### 19.4 PG 完整集 / projection rebuild / DSN / schema

* `E9-PG-FULL/argv.json` 六文件含 **`trader/tests/test_trading_projection_rebuild.py`**（缺口③补齐）；
  junit `tests=23 failures=0 errors=0 skipped=0`、exit 0、log `23 passed`。
* 该文件恰 **5 个** `async def test_`；5 个用例名逐一在 `E9-PG-FULL/junit.xml` 命中
  （`test_deleted_query_projections_rebuild_without_rebooking_or_renotifying`、
  `test_schema_version_is_recorded_once_and_reinitialization_preserves_state`、
  `test_newer_unknown_schema_cannot_be_silently_opened_by_older_runtime`、
  `test_non_development_bootstrap_refuses_before_opening_database`、
  `test_rebuild_failure_rolls_back_both_query_projections`）；23 = 6 trading_postgres + 5 phase2_postgres
  + 5 independent_postgres + 1 split_control + 1 unified + **5 projection**；0 skip/failure/error 元素。
  运行字节：PG full 前后 digest 均 `58e7a0c9…`、`changed_files=[]`，`core.py` = `befa241a…`（最终字节）。
  §18.6 登记的 result-12 `21` 与 request-14 `18` 相差 3 例属旧字节计数；本轮最终字节上该文件为 5 例、
  完整集 23 例，旧计数不作通过依据。
* DSN：全证据目录 grep `postgresql://|trader_pwd|@127.0.0.1` **0 命中**；
  `POSTGRES_CONNECTION_STRING|password=|pwd=|user=|host=` 仅命中变量名/说明文字，无凭据值；
  `argv.json` 只有 `"with_dsn": true`，无 DSN。
* 唯一临时 schema：`trader/tests/test_trading_postgres.py:44-67` 的 `pg_store()` 生成
  `qts_trading_test_<uuid4().hex[:12]>`、`CREATE SCHEMA`、pool `search_path`，`finally` 中
  `DROP SCHEMA ... CASCADE`；`test_trading_projection_rebuild.py:8` 直接 import 该 fixture；0 skip。

### 19.5 离线集合与 request-14 E2–E8 对照

`run_evidence.py:27-53` 的七组文件清单与 `tester-execution-request-14.json` 的 E2–E8 **逐组完全一致**
（E2 独立 round4；E3 独立三套件；E4 工程师 round4；E5 round2+safety；E6 core66 四件；E7 P0 五件；
E8 phase0）：**无遗漏、无多跑**，无需开立 finding_id。非阻塞差异：证据组未使用 `-rA`（日志不再含逐例
PASSED 清单），但逐例名完整保留在 `junit.xml`，且改用直接进程退出码——正是缺口②所要求。
`E9-PG`（3 文件/15 例）为子集，`E9-PG-FULL`（6 文件/23 例）= request-14 E9 五文件 + 遗漏的第 6 文件。

### 19.6 历史口径更正（不改写 §1–§18）

正式审核轮次：**round1=fail、round2=fail、round3=fail**（`review-round3-parent-record.json`
`overall=fail`，R3-B2-HOLDRELEASE / R3-B1-FIRETEST）、**round4=fail**、**round5=pass**
（`review-round5-final-snapshot.json` 与父退回件 `required_actions[4]`）。
§17.2 的「overall = pass（**第 4 轮独立测试员口径**）」与 §18.6 的「overall = pass
（**post-round4-fix-retest 口径**）」均为测试员/复验范围结论，**不是正式审核 verdict**；§17.2 原文亦
已注明「正式第 4 轮只读审核待父生成快照后创建」。此前工程师交接中「round3 pass」的说法错误，以父
记录为准；本条仅为更正说明，§1–§18 历史原文全部保留。

### 19.7 结论、限制与写入声明

**overall = pass**（证据完整性补齐口径：缺口①②③ 均由工程师常规通道直接执行证据闭合；9 组直接退出码
全 0、JUnit 重算 304 离线 + 23 PG 全绿 0 skip；61 文件前后同字节；projection rebuild 5 例在最终
`core.py` 字节通过）。`blocking_findings=[]`，`production_finding=false`。

限制（不标为通过）：测试员命令通道仍被 ACL 拒绝，算术重算以 E-R1 受控请求补；`-rA` 省略导致
`pytest.log` 无逐例清单（由 junit.xml 覆盖）；各套件 2 条既有 Pydantic 弃用 warning；GitHub Actions
实际矩阵 / Compose / 前端 / Binance 网络与真实资金 / 24h 沙箱 / Control→Execution 物理进程隔离未执行。

写入：本节为**只追加**（§1–§18 不动）+ 测试员分析 JSON
`artifacts/foundation/phase2/tester-evidence-completion-analysis-1.json`；生产、测试、CI、契约、
工作流文档及 `evidence-completion-1/` 证据原件**均未改动**；未提交/推送；`workspace_frozen=true`；
交付仅发工程师 `d29dfb7b…`，未联系父 Agent。

### 19.8 E-R1 受控算术重算归档（补充；结论不变）

* 执行：工程师常规通道按 `tester-evidence-completion-analysis-1.json` 的 `controlled_request_E_R1.argv`
  原样代跑；无 DSN、无 ACL 修改/提权、无换终端。测试员仅直读 raw，执行/分析分离不变。
* raw 路径：`artifacts/foundation/phase2/evidence-completion-1/digest-recount-1.log`、
  `artifacts/foundation/phase2/evidence-completion-1/digest-recount-1.exit`（原文 `0`）。
* 实际输出（6 行）：`file_count 61`；`before_after_equal True`；
  `declared 58e7a0c947afb33d0caafb49d8fb7104ecdc0cef690be8c6df8babde9e054a47`；
  `recomputed 58e7a0c947afb33d0caafb49d8fb7104ecdc0cef690be8c6df8babde9e054a47`；
  `disk_hash_mismatches []`；
  `disk_digest 58e7a0c947afb33d0caafb49d8fb7104ecdc0cef690be8c6df8babde9e054a47`。
  即 declared == recomputed == disk_digest，逐文件磁盘 SHA256 与 after 集 61 项全部一致。
* 判读：§19.3 登记的「sha256 算术未由测试员执行」边界现由受控执行证据补齐（executed_by = 工程师，
  analyzed_by = 测试员）；**§19.7 结论不变：overall = pass**（`production_finding=false`、
  `blocking_findings=[]`、`stable_findings=[]`）。限制项中「算术重算以 E-R1 受控请求补」相应视为已闭环；
  其余未执行项（GitHub Actions 实际矩阵 / Compose / 前端 / Binance / 24h 沙箱 / 物理进程隔离）维持原状。
* 写入：本条为 §19 的**只追加**，§1–§19.7 原文未改动；生产/测试/CI/契约/工作流文档及
  `evidence-completion-1/` 证据原件均未改动；未提交/推送；`workspace_frozen=true`；仅回工程师，
  未联系父 Agent。
