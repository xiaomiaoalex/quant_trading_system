# 阶段 2 book 子账簿迁移与操作 Runbook

日期：2026-11-02
适用：`feat/foundation-refactor` 阶段 2（统一订单规格、book 子账簿、双层预约）
契约：`docs/INTERFACE_CONTRACTS.md` 13.9 / 13.12；架构：`docs/PROJECT_ARCHITECTURE.md` 8.2

本阶段**没有独立的数据迁移脚本**。迁移发生在 `trader/trading/store.py::decode_state()`：
读取旧账户快照时，如果缺少 `books`，就把既有 `balances` / `positions` 原样放入
`UNALLOCATED`，并在下一次账户事务提交时持久化 `books` 与归属证据标记。迁移不猜测任何策略归属。

## 1. 不变量

- `trading_accounts.state` 仍是权威快照；`trading_executions` / `trading_ledger` /
  `trading_events` 仍是追加事实，不重写、不清空。
- 对每个资产：`account_total == UNALLOCATED + Σ book:{strategy_id}`。每次写事务在提交前校验，
  失败则整个事务回滚（`BOOK_LEDGER_INCONSISTENT`）。
- 存量资产全部留在 `UNALLOCATED`；只有显式 `ALLOCATE` 命令能把资产移入策略 book。
- 既有在途订单的账户层预约保留；它们的 book 归属未知，系统记录 `BOOK_ATTRIBUTION_UNKNOWN`
  并阻断新增风险，**不会**按 symbol、比例或时间顺序猜测归属。
- 归属仍在未分配账簿的存量预约，会在 `UNALLOCATED` 账簿上**镜像**同一笔经济预留：
  这是同一笔预留的两层视图，不是第二次资产占用。迁移后 `UNALLOCATED` 的可用额度因此
  反映真实可动用资金，划拨不会动用已被在途订单预留的现金。
- 旧的无 scope `UNKNOWN_COMMAND` 只有在 `detail.command_id` 能对应到已持久化的 PLACE 命令时
  才补上 `scope`；否则保留并报告 `LEGACY_UNKNOWN_COMMAND_UNATTRIBUTED`，不得手工清除。

## 2. 升级前

1. 停止 Execution 进程并确认没有其他执行实例持有账户租约（`trading_runtime_leases`）。
   迁移由第一个写事务完成；并发写会串行化在账户行 `FOR UPDATE` 上。
2. 按现有运维流程做一次 PostgreSQL 备份。阶段 2 不包含备份实现，本步骤沿用阶段 5 之前的
   手工/外部备份。
3. 记录升级前基线：账户 `balances`、`positions`、`orders`、`holds`、`ledger` / `events` 计数。
4. 确认新代码的 `QTS_RISK_POLICY_JSON` / `QTS_FEE_RATE`（如使用）合法；未知字段会让 Control /
   Execution 入口拒绝启动。两个进程应使用一致配置；即使不一致，Execution 也会在发送 QUEUED
   订单前按自身 policy 复检并拒绝超限订单。

## 3. 升级

1. 部署新代码并启动 Execution / Control。Execution 首次恢复会对完整快照执行：
   - 旧快照：初始化 `UNALLOCATED = balances`；逐单标记归属不明；
   - 新快照：正常对账，不覆盖未解释的账实差异（阶段 1 语义）。
2. 首次写事务后检查 `GET /v1/trading/books?account_id=...`：
   - 必须存在 `UNALLOCATED`；
   - `UNALLOCATED` 各资产应等于升级前的账户余额（若没有既有预约/差异）；
   - 每个资产满足守恒；若返回 `BOOK_LEDGER_INCONSISTENT`，停止新增风险，保留现场并人工核查。
3. 检查风险视图 `reasons`：
   - `BOOK_ATTRIBUTION_UNKNOWN`：存在旧订单/成交没有 book 归属；
   - `LEGACY_UNKNOWN_COMMAND_UNATTRIBUTED`：存在无法证明归属的旧 UNKNOWN 命令；
   - 其他阶段 1 原因码继续按原语义处理。

## 4. 归属不明订单的处理

1. 不要删除未决项、不要执行 SQL 更新 `trading_accounts.state` 来“修好”归属。
2. 先让交易所证据把订单推进到终态：
   - `FILLED` 且无剩余预约；
   - 对该订单完成撤单/确认：终态会**同时释放账户层与 UNALLOCATED 镜像层的预约**，
     被预留的资金随即恢复可划拨；但该订单的归属仍未决（见第 3 步与第 8 节的已知限制）；
   - 部分成交后撤销的订单在阶段 2 **不允许**清除归属：迟到成交仍可能到来。
3. 使用 `RESOLVE` 控制命令，`code=BOOK_ATTRIBUTION_UNKNOWN`，证据必须逐单列出：
   ```json
   {
     "kind": "operator_attribution",
     "reference": "ticket-2026-11-02-1",
     "reason": "operator verified the venue statement",
     "orders": {"qts-...": "UNALLOCATED"}
   }
   ```
   目标可以是 `UNALLOCATED` 或已存在的 `book:{strategy_id}`；未创建的 book 会被拒绝，
   需先显式划拨创建。
4. 该命令只清除归属阻断并记录审计事件，不移动余额。若历史成交应归入某策略，
   另发 `ALLOCATE` 把等额资产从 `UNALLOCATED` 划入该 book，并保留相同/可追溯的操作原因。

## 5. 显式划拨

- API：`POST /v1/trading/allocations`
  ```json
  {
    "command_id": "alloc-2026-11-02-1",
    "account_id": "binance_spot",
    "book_id": "book:ema_cross_btc",
    "asset": "USDT",
    "amount": "5000",
    "reason": "initial strategy budget"
  }
  ```
- Control 进程只落 `QUEUED`；Execution 在账户事务中应用。重复相同命令是幂等 no-op；
  同 `command_id` 不同金额返回 `Conflict`；余额不足时命令 `FAILED` 并返回
  `ALLOCATION_INSUFFICIENT_UNALLOCATED`。
- 阶段 2 只支持 `UNALLOCATED -> book:{strategy_id}` 的划出方向；反向划拨/再平衡属后续阶段。
- 余额不足的划拨被拒绝（`ALLOCATION_INSUFFICIENT_UNALLOCATED`），账户总额与任何 book 余额
  都不变。目标 book 的可见性取决于执行路径：
  - 直接（非 deferred）划拨在转账前单独登记目标 book；失败后该空 book 可观察，便于运维定位被拒绝的目标。
  - Control 排队阶段不登记 book；Execution 处理失败时保持余额不变，命令置 `FAILED` 并写
    `book.allocation_failed` 事件（不进入 order 通知 outbox）。
- 划拨命令的审计事件是 `book.allocated`（持久化在 `trading_events`；不进入 order 通知 outbox）。

## 6. 验证

```text
python -m pytest -o addopts='' -q trader/tests/test_phase2_foundation_safety.py --tb=short
POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:5432/trading \
  python -m pytest -o addopts='' -q trader/tests/test_phase2_postgres.py --tb=short -ra
```

`test_phase2_postgres.py` 使用唯一临时 schema，不清理现有数据库；覆盖：
旧状态迁移不改写事实、重复迁移幂等、分配幂等/并发不透支、双层预约事务回滚、
重启后 book/权益查询一致、归属证据跨重启持久。

## 7. 回滚

- `books` 与 `book_ledger` 标记是新增字段，旧代码读取时会忽略；但归属阻断项和
  `BOOK_ATTRIBUTION_UNKNOWN` 事件仍留在状态中。
- 推荐回滚方式：停止新进程，恢复升级前的 PostgreSQL 备份；不要手工删除 `books` 或
  未决项，也不要用 SQL 改写 `trading_ledger` / `trading_events`。
- 若没有备份且必须回到旧代码：旧代码可继续读取账户快照，但阶段 1 的 UNKNOWN/余额差异
  门禁仍然有效；保留 `books` 字段与归属阻断，等待人工处理。

## 8. 已知限制

- 阶段 2 不实现反向划拨、自动再平衡、净额或内部撮合。
- 已撤销的旧订单（部分成交或零成交）在阶段 2 都无法解除归属阻断；这是为了避免迟到成交被
  静默记入错误 book。终态会释放两层预约（资金不再搁浅、可继续划拨），但归属项仍需人工确认
  交易所不再有该订单后，按后续阶段提供的显式工具处理。
- 已证据化的账户级余额调整记入 `UNALLOCATED`，可能使其为负；守恒仍成立，
  `GET /v1/trading/books` 会如实显示。需要人工把超配的 book 资产调整回可解释状态。
- 阶段 4 的联合回测调度、manifest、Compose/监控与阶段 3 的 closed-bar/资格链不在本 runbook 范围。
- `EventBacktestRuntime` 已支持显式的多策略 book 映射与初始资金分配，并输出逐 book 权益/预算；
  本 runbook 只覆盖实盘账户的迁移，不描述回测侧的 book 配置。
