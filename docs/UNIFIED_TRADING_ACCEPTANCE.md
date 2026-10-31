# 统一现货闭环实施与验收记录

## 第四轮补充：可信覆盖证明与重启预热（2026-10-31）

本轮关闭第三轮遗留的两个本地实现缺口；外部验收状态不变。未部署、未提交或push，未运行 `scripts/testnet_e2e.py`，未进行真实资金交易。

### 实现

- **私有流覆盖证明端口**：`PrivateTradingSession` 接收交易所时钟和
  `coverage_provider(generation, first, last, connected_from_ms, buffered_through_ms)`。
  连接边界取交易所服务器时间，不使用本机 REST 接收时间。`RecoveryCoordinator` 额外拒绝
  “已缓冲事实越过已证明窗口”。
- **Binance REST 回填证明**：新增 `trader/adapters/broker/spot_coverage.py`。只有同时满足
  双快照完整且账户一致、交易所时间窗口有效、订单/成交分页完整、每笔成交映射本系统
  `qts-` 订单、窗口内无重复/外部成交、连续两遍回填逐字段一致、快照成交全部出现在回填中，
  才返回 `RecoveryCoverage`；任何异常、超时或不确定性都返回 `None`，会话保持
  `PRIVATE_STREAM_COVERAGE_UNPROVEN` 阻断。没有环境开关可绕过。
- **分页接口公开**：`BinanceSpotSnapshotProvider.fetch_orders/fetch_trades` 返回
  `(items, complete)`，覆盖证明与快照共用同一分页实现。
- **策略标的必须处于覆盖范围**：Worker 启动时校验 `QTS_INSTRUMENTS ⊆ QTS_SYMBOLS`，
  防止策略交易覆盖证明从未扫描的 symbol；不满足直接拒绝启动。Runtime 同时把
  `QTS_SYMBOLS` 作为下单 allowlist，`runtime.place()` 和持久 QUEUED 命令的 IO 边界
  都会拒绝范围外 symbol；撤单/查询不限制，避免无法退出已有仓位。
  `test_trading_runtime.py::test_symbol_outside_coverage_scope_never_reaches_the_venue`
  验证两条路径均未调用交易所且预约已释放。
- **策略重启预热（第四轮加强为显式状态机）**：
  - 插件通过可选 `stateful_indicators` / `warmup_events` 声明指标需求，
    `describe_plugin_warmup()` 读取；**未声明的插件按有状态处理**，沉默不等于
    “可冷启动交易”。预热长度取 `max(部署配置, 插件声明)`，插件声明是下界。
  - 状态为 `PENDING / WARMING_UP / WARM / BLOCKED`，按标的分别计数；所有标的都
    达到预热长度才进入 `WARM`。预热期观察仍喂给插件，但信号一律丢弃，不提交订单。
  - **重新采集而非持久化**：插件内部指标状态不可安全序列化、代码/参数变更后持久快照
    即失效，因此不持久化插件状态；Worker 在恢复通过后按 `QTS_STRATEGY_WARMUP_INTERVAL`
    重新采集 Binance 已收盘 K 线回放（未完成 K 线不可见），预热完成后才进入评估。
  - 阻断原因：`STRATEGY_WARMUP_NOT_CONFIGURED`、`STRATEGY_WARMUP_HISTORY_EMPTY`、
    `STRATEGY_WARMUP_HISTORY_INCOMPLETE`、`STRATEGY_WARMUP_HISTORY_FAILED`、
    `STRATEGY_WARMUP_FAILED`、`STRATEGY_PLUGIN_ERROR`；阻断经 `on_block` 关闭新增风险。
  - 就绪响应新增 `strategy_warmup_state` / `strategy_warmup_reason` /
    `strategy_warmup_remaining` / `strategy_ready`。
  - 内置插件声明：`ema_cross_btc`（`slow_period*3`）、`rsi_grid`（`rsi_period*4`）、
    `dca_btc`（1）、`fire_test`（无状态）。

### 自动化证据

- 新 `trader/tests/test_spot_coverage_proof.py`：完整两遍证明；分页截断、REST 异常、
  外部成交、非本系统前缀、窗口外成交、两遍变化、缓冲越过窗口、快照成交缺失、
  重复成交/订单 ID、symbol 不一致、账户不一致、未知连接边界全部 fail-closed。
  本轮另补：成交时间戳回退（`test_trade_timestamp_regression_is_unproven`）、
  快照成交落在已证明窗口之外
  （`test_snapshot_execution_outside_the_proven_window_is_unproven`）。
- `test_unified_strategy_runner.py` 新增三例：预热期只喂数据不下单、重启重新预热、
  预热不绕过恢复；重复事件仍复用持久命令。
- 新 `trader/tests/test_strategy_warmup.py`（第四轮加强）：
  `test_warmup_spec_treats_undeclared_plugins_as_stateful`、
  `test_undeclared_stateful_plugin_blocks_new_risk`、
  `test_stateless_plugin_is_warm_without_observations`、
  `test_warmup_requires_every_instrument_before_trading`、
  `test_restart_requires_a_fresh_warmup_before_trading`、
  `test_history_replay_warms_the_plugin_without_live_events`、
  `test_incomplete_history_blocks_with_a_precise_reason`、
  `test_history_source_failure_blocks_instead_of_trading_cold`、
  `test_plugin_error_during_warmup_blocks_new_risk`、
  `test_plugin_declaration_is_the_lower_bound_for_the_warmup_length`、
  `test_warmup_never_bypasses_account_recovery`。
- `test_worker_assembly.py` 新增两例：`test_worker_blocks_when_the_strategy_warmup_cannot_be_satisfied`、
  `test_worker_warms_a_declared_plugin_from_the_recollected_window`。
- 定向：`test_spot_coverage_proof.py` 22 passed；`test_strategy_warmup.py` 11 passed；
  `test_unified_strategy_runner.py` + `test_worker_assembly.py` 19 passed。
- 真实 PG（7 文件，`POSTGRES_CONNECTION_STRING` 指向本地 compose 实例，隔离临时 schema）：
  **53 passed、0 skipped**（`artifacts/round4_pg.xml`）。
- P0 五文件：**99 passed、0 skipped**（`artifacts/round4_p0.xml`）。
- 全量 `.venv/Scripts/python.exe -m pytest -q trader/tests --tb=short -p no:cacheprovider
  --basetemp=.pytest_tmp_round4c --junitxml=artifacts/round4_full_final3.xml`：
  **3161 tests、0 failed、0 error、5 skipped、136.4s**（`artifacts/round4_full_final3.xml`）。
  5 项 skip 仍为既有 vectorbt/quantstats 可选依赖；未新增 skip。
- 沙箱内首次全量运行有 15 个 setup error，全部是宿主临时目录权限
  （`PermissionError: [WinError 5] C:\Users\14463\AppData\Local\Temp\pytest-of-14463`），
  与代码无关；改用工作区内 `--basetemp` 后 0 error。该次运行不作为通过证据。
- 曾有一次全量运行因宿主机 socket 缓冲区耗尽（`WinError 10055`）在
  `test_risk_idempotency_persistence` 的 TestClient 建连处失败；该测试单独复跑通过，
  随后全量干净通过。这是宿主机资源问题，不记为代码失败，也不以该次运行为通过证据。
- 前端：`npm run test` 11 文件 81 passed；`npm run typecheck`、`npm run build` 退出0
  （既有 browserslist 过期与 chunk 大小提示）。沙箱内 node 无法读取用户目录，
  前端命令按宿主权限执行。
- 改动 Python 文件 isort、Black `--check --line-length 100 trader/`、`git diff --check` 通过；
 新增 `test_strategy_warmup.py` 已加入 `.github/workflows/ci-gate.yml` 的 Trading Core 步骤。

### 仍未关闭

- **blocked：真实 testnet 覆盖证明与 E2E。** 证明器尚未对真实 Binance 账户运行；
  真实环境的分页上限、权重、符号范围与交易所迟到写入仍需实测。`QTS_SYMBOLS` 必须覆盖
  策略实际交易的每个 symbol，否则证明范围不完整。
- **blocked：24h 观察与主动重连/重启记录。** 2026-10-30 22:17 至 10-31 13:48 的窗口只覆盖
  15.5h 即在账户阻塞后随观察进程一起中断，未达到24h；`active_reconnect` marker 从未写入
  （重连确实发生过，但没有留下可复查标记），最终门禁为
  `MISSING_MARKER:active_reconnect`、`NOT_READY_AT_END`、`WINDOW_SHORTER_THAN_MIN`，不能标通过。
  该窗口暴露的成交重放假冲突已修复（见 `docs/INTERFACE_CONTRACTS.md` 成交重放约定），
  修复本身不能替代重跑窗口。
- 预热默认长度0只表示“不额外加长”；有状态插件必须声明 `warmup_events`（或配置
  `QTS_STRATEGY_WARMUP_EVENTS`），否则以 `STRATEGY_WARMUP_NOT_CONFIGURED` 阻断。
  预热窗口的重新采集仅在真实 testnet 上跑过一次回放路径，尚未做长周期实盘式验证。

## 第三轮装配结果（2026-10-31，历史）

本节记录第三轮结果；第一/第二轮章节保留更早历史。原始13项审查编号仍未提供，继续按批准方案主题映射，不宣称原编号逐项关闭。第三轮未联网、未启动真实交易进程、未部署、未提交或push，未运行 `scripts/testnet_e2e.py`。

### 文件与本地验收证据

| 目标 | 实现/修改文件 | 自动化证据 |
| --- | --- | --- |
| 策略只经统一意图入口，持仓来自账本 | 新 `trader/trading/strategy.py`；Worker/Runtime接线 | `test_unified_strategy_runner.py:51` 买入/真实成交/卖出/再建仓；`:99` 并发重试与runner重建不重发；`:124` 恢复前不调用插件；`:151` 同ID改量冲突；`:176` 成交后库存/基础币费用；`:210` 条件/未完成BAR拒绝 |
| 策略命令进程重启幂等 | 新 `test_unified_strategy_postgres.py` | `:15` 真实PG恢复同一已受理策略事件，零重复plugin调用/发送 |
| Private WS实际解析和逐笔入账 | 新 `trader/trading/private_session.py`，修改 `private_stream.py`、`trading_ingress.py` | `test_private_execution_identity.py` 真实解析→Ingress→service→REST同笔重复无重记；精度、手续费原币种、trade id与成交时间一致 |
| 代际/缓冲/双快照/缺口恢复 | 修改 `recovery.py`；session调用同一RecoveryCoordinator | `test_private_trading_session.py:159` 累计先于真实成交及REST/WS重复；`:175` 缺覆盖/分页缺口阻断；`:183` 重连/旧代迟到拒绝；`:195` 周期同连接核对；`:203` 坏事实不静默丢弃 |
| 实际Worker调用恢复与Runner | 修改 `runtime.py`、`trading_worker_main.py`；真实PrivateStreamManager factory | `test_worker_assembly.py:55` 未装配503原因；`:68` 租约后创建session/恢复后可用；`:80` 断线阻断；`:119` 无覆盖证明精确原因；`:130` 真实session+runner+fakeWS组合，重复成交单次记账，断线停止策略tick |
| REST订单/成交分页补查 | 修改 `binance_spot_demo_broker.py`、`spot_execution_adapter.py`；公开 `fetch_orders`/`fetch_trades` | 新 `test_spot_recovery_pagination.py` 短页结束、重复/不前进页不完整；`test_spot_coverage_proof.py` 分页截断即无证明 |
| 私有流覆盖证明 | 新 `trader/adapters/broker/spot_coverage.py`；`PrivateTradingSession` 接收交易所时钟与覆盖端口；`RecoveryCoordinator` 增加缓冲水位校验 | `test_spot_coverage_proof.py` 两遍完整回填一致才证明；分页截断、REST异常、外部或非本系统前缀成交、窗口外成交、两遍变化、缓冲越过已证明窗口、快照成交缺失、重复ID全部返回无证明；`test_private_trading_session.py` 缺证明保持阻断 |
| 策略重启预热 | `strategy.py` 观察计数门禁；`runtime.py` 暴露剩余预热数；Worker读取 `QTS_STRATEGY_WARMUP_EVENTS` | `test_unified_strategy_runner.py::test_warmup_observations_feed_the_plugin_without_trading`、`test_restart_starts_a_fresh_warmup_period`、`test_warmup_never_bypasses_account_recovery` |
| 断线先阻断 | PrivateStreamManager在socket close/reconnect前发生命周期事件 | `test_private_execution_identity.py::test_private_disconnect_notifies_barrier_before_closing_socket`；session/worker集成验证风险门禁关闭 |
| 旧路径退休/撤单控制入口 | 保留第二轮退休代码，不重启旧OMS发送；新Runner无broker引用 | `test_split_control_runtime.py`、`test_trading_service.py` cancel-all/UNKNOWN/撤单receipt；全量复核 |

退休边界补强：旧API在退休生命周期内拒绝 `/v1/killswitch` 写操作，503 + `LEGACY_EXECUTION_RETIRED`；GET只读保留。`test_split_control_runtime.py::test_retired_legacy_killswitch_write_is_rejected_before_any_effect` 先复现旧入口200及副作用，修后断言未调用旧状态写入、读取状态前后一致。避免旧L2只改查询投影而误报已撤单。

三进程职责：Control API只经PG命令与同账户owner/readiness元数据协作，不实例化第二个恢复/发送器；交易Worker唯一运行私有流session、恢复协调器和策略；Research Worker使用共享经济核心作离线任务，不接入实时账户恢复。不会为了“三个入口”而让三个进程同时恢复、记账同一实时账户。

### 当前边界和阻塞

- **第三轮当时**：真实Binance入口仍为 `coverage_provider=None`，保持 `PRIVATE_STREAM_COVERAGE_UNPROVEN`/503；第四轮已装配 REST 回填证明，见上节，但真实 testnet 证明仍 blocked。
- **第三轮当时**：策略已受理行情通过命令持久状态去重；插件指标状态重启预热尚未实现；第四轮已实现 `QTS_STRATEGY_WARMUP_EVENTS` 门禁，见上节。默认不运行未配置策略；实际使用需显式配置策略模块、deployment、标的与参数。
- BAR仍保留既定撮合模型边界；本轮未改第二轮经济版本。
- **blocked：真实testnet E2E、实际主动重连/执行进程重启记录、24h观察。** 需要隔离测试环境、可用沙箱配置、明确沙箱操作授权及实际观察时间；本轮禁止联网和外部账户操作，未执行这些验证。

### 本轮门禁

- 本地组合 `test_worker_assembly.py test_private_trading_session.py test_unified_strategy_runner.py test_private_execution_identity.py test_spot_recovery_pagination.py`：23 passed。
- 前端 `npm run test`：11文件、81 passed；`npm run typecheck` 和 `npm run build` 退出0（既有浏览器数据库/chunk提示）。Node本地目录权限按宿主升级执行，未安装依赖或联网。
- P0五文件 `--tb=short -o addopts= --junitxml=artifacts/round3_p0.xml`：99 passed、退出0。
- 真实PG五文件（第二轮四文件 + `test_unified_strategy_postgres.py`）`--tb=short -o addopts= --junitxml=artifacts/round3_pg_final.xml`：16 passed、0 skipped、退出0，仅隔离临时schema。
- 首轮全量（旧KillSwitch门禁最后收口前）`--junitxml=artifacts/round3_full.xml`：3113 passed、5个既有skip、0失败。最后门禁新增测试及修复定向28项通过，最终全量另附结果。
- 最终全量命令：`.venv/Scripts/python.exe -m pytest -q trader/tests --tb=short -o addopts= --junitxml=artifacts/round3_full_final.xml`。结果：**3114 passed、5 skipped、0 failures、16 warnings，119.98s，退出0**；JUnit逐条保存测试名称与结果。5项skip仍是既有vectorbt/quantstats可选依赖，不是新增skip。
- CI已纳入本轮新测试；不新增skip或削弱旧断言。新模块/入口/解析/分页修改的isort、Black单worker检查及diffcheck通过。

## 第二轮：批准方案差距 → 证据映射

**原始编号未提供，按批准方案条目映射。** 下列13行是本轮整理的主题，不是原审查编号；不得引用为原始13项审查已关闭。

| 批准方案差距主题 | 证据入口 | 状态边界 |
| --- | --- | --- |
| 统一订单聚合与命令幂等 | `test_trading_service.py` concurrent/place/conflict | 已有自动化；PG命令队列并发样例已验 |
| 命令传输与订单状态分离/UNKNOWN恢复 | service resume/UNKNOWN 测试 | 崩溃所有位置仍非穷尽 |
| 真实逐笔成交、永久去重 | `test_trading_postgres.py` replay/dedup | PG样例通过；WS装配另验 |
| 原币种账本、费用、终态迟到成交 | `test_trading_evidence.py`、service late fill | 共享核心等价性已验 |
| 统一预约、余额与组合敞口 | `test_trading_service.py` reservation/concurrency | 策略实际装配另验 |
| 账户并发、单执行实例 | PG concurrency、Runtime lease测试 | 三入口与PG队列已验，策略/WS装配未完成 |
| 分代恢复、WS缓冲、REST分页与水位 | snapshot consistency、ingress、`test_spot_coverage_proof.py`、`test_recovery_coordinator.py` | 本地全链路端口已实现；真实交易所证明与小页上限、真实主动重连记录仍blocked，不宣称关闭 |
| 风险保护/恢复/紧急动作分离 | Runtime/control/UNKNOWN测试 | 旧发送边界退休已验 |
| 实盘/BAR共用账本与风险 | 本轮共享纯核心及等价性测试 | 9条共享核心等价性样例通过 |
| 不可变数据与历史时钟、指标口径 | research safety/runtime测试 | 执行前hash重验已实现 |
| 持久研究任务、续租/fencing/取消 | research worker lifecycle与真实PG测试 | 自动重启进程策略仍需部署配置 |
| 登录、会话、CSRF与审计 | trading API与前端auth测试 | 已有自动化；新控制入口门禁已验 |
| 启动装配、可观测、CI及切换证据 | runtime、observation、CI、runbook | 真实测试环境E2E/24h记录 blocked |

外部验收状态：**blocked**。本轮仅获本地实现和测试授权，禁止部署、真实交易；未运行真实测试环境E2E，也未生成包含实际主动重连与重启的24h观察记录。完成这些项目需要可用的隔离测试环境、明确的沙箱操作授权和实际观察时间；已有模拟测试不能替代。

## 第一轮历史记录

第二轮实现结果先记录于本节之前的映射与本文末尾；以下历史不覆盖新状态。

以下为第一轮接手与验证记录；第二轮实现及最终状态另见新增章节，不能将第一轮剩余项当作第二轮最终状态。

记录时间：2026-10-31。范围仅本地代码、无网络替身测试、独立临时 schema 的真实 PostgreSQL 集成；本轮未启动交易进程、未发送交易所订单、未生产部署、未 push。

## 接手基线与阶段状态

接手分支 `codex/allocation-and-projection-v2`，HEAD `4e29310`，工作区干净。交接消息所述 service/runtime/API/研究代码缺失状态已过时，已有实现和测试均保留。

| 阶段 | 本轮结果 | 尚未完成 |
| --- | --- | --- |
| A 契约与失败样例 | 新增成交证据、估值、租约、认证、研究冻结、Worker、观察窗口红测并修复；同步接口契约 | 未获得原“13 项审查结论”的编号及失败测试对应表，不能伪造逐项关闭 |
| B 交易核心 | 修复累计观测未入账不阻断、跨订单清除差异、旧终态释放预约、迟到成交终态矛盾 | 投影删除重建及每个 PG 写入步骤故障矩阵未穷尽 |
| C 账户与组合 | 非 GTC 拒绝；所有非零资产都要求可靠 USDT 估值；REST 采集前后稳定性检查 | WS 缓冲代际、水位与稳定快照组合恢复尚未接线 |
| D 研究与数据 | 修复跨标的 SELL 预约、执行前冻结输入重验、完整 manifest hash、证据资格、持续心跳与取消 | BAR 仍自建账务/准入，未共用 TradingService/SpotRiskEngine；risk_config 尚未真正执行；DB 层不可变约束未实现 |
| E 装配与产品 | 修复 API 绕过租约、出站复验、CSRF 刷新恢复、显式 Vite 新 API 目标 | API/交易 Runtime 仍同进程；策略 Runner、private WS 未在新入口完整装配；旧控制 API 仍存在 |
| F 集成与切换 | PG 集成、前端测试、CI 新回归门禁、观察证据完整性检查 | 未执行新环境切换；未完成本轮测试环境 E2E 或重新校验完整 24h 观察窗口；旧执行器尚未统一退出 |

不得据本轮测试通过将全方案或阶段 F 标为完成。禁止同时启动新旧发送器。

## 本轮实现文件

- 交易：`trader/trading/service.py`、`risk.py`、`runtime.py`、`trader/adapters/broker/spot_execution_adapter.py`。
- 研究：`trader/trading/backtest.py`、`jobs.py`、`research.py`。
- API/前端：`trader/api/routes/trading.py`、`Frontend/src/api/auth.ts`、`Frontend/vite.config.ts`。
- 前端构建同步了仓库已跟踪的 `Frontend/vite.config.js`；构建缓存已恢复，不作为任务变更保留。
- 演练：`scripts/summarize_observation.py`。
- 新测试：`test_trading_evidence.py`、`test_spot_snapshot_consistency.py`、`test_research_safety.py`、`test_research_worker_lifecycle.py`、`test_observation_summary.py`、`Frontend/tests/api/auth.test.ts`。
- 既有测试扩展：`test_trading_runtime.py`、`test_trading_api.py`、`test_research_runtime.py`、`test_research_postgres.py`、`Frontend/tests/components/LoginGate.test.tsx`。
- 契约、架构、运行手册、前端接口文档、`.github/workflows/ci-gate.yml` 同步。没有修改 DB schema 或重建开发数据。

## 逐项验收证据

下表“通过”仅指指定自动化样例；“部分”不等于整个验收项关闭。测试位于 `trader/tests/`，测试名可直接搜索定位。

| 验收项 | 状态 | 可复查证据/限制 |
| --- | --- | --- |
| 同命令并发 100 次、不同参数冲突 | 通过 | `test_trading_service.py::test_concurrent_place_creates_one_intent_and_one_send`、`test_place_is_idempotent_and_conflicts_on_changed_payload` |
| REST/WS/历史成交重复与顺序 | 部分 | `test_execution_dedup_survives_restart_and_ordering`；新 `test_trading_evidence.py` 覆盖部分/终态缺成交、REST 接受累计与跨订单隔离，真实 WS 入口尚未装配 |
| TTL/重启重放幂等 | 通过 | `test_trading_postgres.py::test_postgres_execution_dedup_and_restart_replay` |
| 成交每个写入步骤全提交/回滚 | 部分 | PG `test_postgres_transaction_rolls_back_every_table_on_failure` 及 memory ledger/post hooks；尚非每条 SQL 的故障矩阵 |
| 发送崩溃与 UNKNOWN 不盲目重发 | 部分 | `test_resume_queued_never_resends_unknown_commands`、`test_unknown_exchange_result_keeps_hold_and_is_never_auto_resent`；进程 kill 的所有位置未实测 |
| 全部撤单走同入口、不误显示成功 | 部分 | service cancel-all 与 API/前端 command receipt 测试；旧控制入口仍存在 |
| 部分成交→撤单→迟到成交 | 通过 | `test_cancel_confirmation_releases_remaining_hold_but_late_fill_books`；`test_late_fill_is_booked_but_conflicting_cancel_total_blocks_until_corrected` |
| 持久恢复与删除投影重建 | 部分 | PG `test_postgres_recovery_from_persisted_state`；未验证删除全部查询投影后的重建 |
| 1000−300−100=600 与接受不重复扣 | 通过 | `test_available_balance_subtracts_our_holds_and_external_lock_once`、`test_order_acceptance_does_not_create_a_second_reservation` |
| 并发两个 600 最多一个 | 通过 | `test_postgres_concurrent_orders_serialize_on_the_account_row` |
| 部分释放、撤单请求/未知/TTL 不释放 | 部分 | 部分成交/UNKNOWN/撤单状态及旧终态预约回归；无 TTL 清理器集成证据 |
| 建仓→平仓→再建仓预算循环 | 待完成 | 新服务无累计 committed exposure，但未完成策略端循环验证 |
| 旧/延迟/冲突/分页不错误 READY | 部分 | stale/incomplete/unhealthy 恢复测试；`test_spot_snapshot_consistency.py` 两例；WS 覆盖水位尚缺 |
| stale/无效估值/未知订单阻断 | 通过 | `test_stale_valuation_blocks_new_risk`、新 `test_unvalued_asset_blocks_even_without_exposure_limits`、`test_nonfinite_mark_is_incomplete_instead_of_raising_decimal_error`、UNKNOWN 恢复测试 |
| 策略用同账本/预算且恢复前不启动 | 部分 | `test_startup_reconciles_before_becoming_ready_and_allows_strategies` 验证 Runtime 门禁；新入口实际策略接线未完成 |
| 买入 1@100、费用0.1后的现金/净值 | 通过样例 | `test_research_runtime.py::test_buy_fee_and_mark_to_market_match_acceptance_numbers`；BAR 尚未共用 live 账本 |
| 升110再卖出净值1009.79/费用0.21 | 通过样例 | 同上 |
| 无成交估值与不同初始资金 | 通过样例 | `test_estimated_equity_is_produced_without_any_fill` |
| 下一根开盘102/滑点102.102 | 通过样例 | `test_signal_at_close_fills_at_next_open_with_slippage` |
| 数量保留、SHORT/超库存拒绝、成交后风控 | 部分 | `test_short_and_oversell_are_rejected_not_converted`、`test_risk_sees_position_after_the_first_fill`；尚未使用 live RiskPolicy |
| 历史时钟、同 manifest 可复现 | 通过样例 | `test_results_do_not_depend_on_the_machine_clock`、`test_results_are_deterministic_and_do_not_use_wall_clock`；新增完整 manifest/hash 篡改回归 |
| 不同周期市场拒绝、未完成 BAR 不可见 | 通过 | `test_dataset_set_rejects_mixed_calendars_intervals_and_markets`、`test_dataset_rejects_unfinished_or_mixed_bars` |
| Worker 崩溃/重领/取消/旧租约发布 | 通过样例 | `test_research_worker_lifecycle.py`、PG `test_postgres_job_lifecycle_and_fencing_prevents_double_publish`、`test_postgres_worker_failure_retry_and_cancel`；失租后进程自动恢复策略待补 |
| vectorbt 不能成为准入证据 | 通过该门禁 | `test_vectorbt_cannot_produce_admission_evidence`、`test_research_executor_refuses_non_event_engines`、新增 screening/errors 资格回归；event 模型尚未与 live 等价 |
| 登录、审计身份、退出失效 | 部分 | `test_trading_api.py` 与前端 auth/LoginGate；新 API 已验证，旧 API 尚未统一切换 |
| DB/未同步/执行冲突503与阻断 | 通过样例 | runtime outage/not-ready 测试及新增 `test_conflicting_runtime_api_cannot_bypass_owner_using_ready_shared_account`、失效租约/跨账户/出站复验测试 |
| 10万 BAR 下 p99 增幅≤20%/10ms容差 | 通过样例 | `test_trading_performance.py::test_trading_event_p99_under_large_replay_stays_within_tolerance` |
| UNKNOWN/成交缺口/账务/预约/对账/积压/Worker指标 | 部分 | `test_metrics_expose_operational_blockers`、`test_runtime_observability.py`；尚未覆盖所有运行装配告警 |
| 13失败修复与全栈 CI | 部分 | 本轮新增回归纳入 CI；基线全量无失败，5项因已有可选依赖缺失 skip；缺原13项审查编号，不能逐项宣称关闭 |
| 真实PG事务/约束/竞争/恢复 | 通过当前样例 | `test_trading_postgres.py`、`test_research_postgres.py`，临时隔离 schema 运行；不代表全部 SQL 故障位置已覆盖 |
| 测试环境下单/成交/撤单/恢复 E2E | 未在本轮验证 | 不将已有 artifacts 文件存在作为本轮演练成功证据 |

## 验证命令与结果

- 第一轮 `.venv/Scripts/python.exe -m pytest -q trader/tests --tb=short`：退出 0，无失败，5 项可选依赖 skip（vectorbt 3、quantstats 2）；在最终整合前执行。
- 定向整合：交易/Runtime/API/adapter/研究/Worker/观察/PG/性能 16 个测试文件，`-o addopts= --junitxml=artifacts/unified_revision_tests.xml`：155 passed，0 skipped，退出 0。后续研究冻结增强另有定向 50 passed 验证。
- P0 五文件回归：退出 0，99 个测试通过。
- 前端 `npm run test -- --run`：11 文件、81 测试通过；`npm run typecheck` 通过。Node 目录读取 EPERM 按宿主流程升级后执行。
- 最终 `.venv/Scripts/python.exe -m pytest -q trader/tests --tb=short -o addopts= --junitxml=artifacts/unified_full_regression.xml`：**3053 passed、5 skipped、16 warnings，139.23s，退出 0**。包含真实 PG 与 P0；skip 均为既有可选 vectorbt/quantstats 依赖缺失，未新增 skip。原始 JUnit 在 `artifacts/unified_full_regression.xml`（本地生成，不提交）。
- 前端 `npm run build`：退出 0，tsc 与 Vite 构建成功；有既有浏览器数据库过期和大 chunk 提示。
- 改动 Python isort/Black 检查与 `git diff --check` 通过。既有测试 warning 包含 Pydantic deprecated config、未注册 integration marker、onchain AsyncMock 未 await，本轮未改这些模块。

## 剩余关键工作

1. BAR 通过同一订单/账本/风险应用服务，真正执行 `risk_config`，在等价性验收前不能将当前模型称为统一 live/paper/backtest 核心。
2. 三进程分离与 PG 命令协调、实际策略 Runner 和 private WS 接线；恢复需代际缓冲与覆盖证明，REST 稳定性检查仅补其中一项。
3. 主动重连前立即阻断、恢复核对完成后再就绪；旧 API/执行器退出、开发 schema 版本化切换与投影重建演练。
4. 提供原 13 项审查编号，建立真实对应证据；最终 24h 观察必须通过已加强的汇总门禁，并保存主动重连/重启实际记录。

## 第二轮实现与验证（2026-10-31）

### 实现文件与证据

- **共享核心已实现**：新增 `trader/trading/core.py`，修改 `service.py`、`backtest.py`；BAR/live 共用 `reserve_order`、`SpotRiskEngine`、原币种账本、净额策略持仓与预约释放。`risk_config` 严格映射 `RiskPolicy`，未知字段拒绝。证据：`test_shared_trading_core.py:18` 组合/标的限额、`:37` 活动订单限额、`:54` BAR成交流经live服务账务对照、`:100` 历史clock、`:115` 再建仓、`:128` 部分成交和基础币费用永久去重、`:183` 100→110及卖出净值对照。
- **模型版本升级**：新增 `version.py`，`jobs.py`/`research.py` 冻结和验证 `spot_bar_v2_shared`；旧/无版本任务拒绝，旧报告标legacy，不能静默换模型重跑。新增研究安全版本回归；保留第一轮数字验收测试。
- **三进程入口和PG队列已实现**：`control_main.py`、`trading_worker_main.py`、既有 `research_worker_main.py`。修改 `runtime.py`、`service.py`、API交易路由；Control 无发送适配器，仅持久QUEUED；Runtime单账户租约消费。RECONNECT持久命令先阻断再连接/核对。`test_split_control_runtime.py` 和真实PG `test_split_control_postgres.py:15` 覆盖独立实例、100次同ID排队、一次发送、停机readiness。
- **schema和查询投影重建已实现**：新增 `migrations.py`、`003_query_projections.sql`，修改 `postgres.py`、`scripts/bootstrap_dev_schema.py`。版本校验和/事务锁/未来版本拒绝；重建只写派生命令与订单查询表，保留账户state、成交、账本、事件、outbox确认。`test_trading_projection_rebuild.py:13` 删除重建一致、`:56` 幂等迁移、`:73` 未来版本拒绝、`:84` 非dev目标在开库前拒绝、`:96` 重建失败全回滚。
- **恢复屏障模块已实现**：新增 `recovery.py`；`test_recovery_coordinator.py:51` 无证明不READY、`:63` 去重重放/旧代拒绝、`:76` 溢出/变化快照阻断、`:100` 缺口/水位/旧证明拒绝、`:111` 应用失败阻断。该模块不制造交易所覆盖证明。
- **旧发送器退休及兼容面**：修改 `trader/api/main.py`、`routes/strategies.py` 和组合入口；旧查询/研究面保留，发送边界退休；新入口无可信private WS证明时强制不健康并保持blocked。脚本 `testnet_e2e.py` 仅更新契约，未执行。
- 契约、架构、runbook、CI 同步新增测试。第一轮工作区全部保留；未提交、未push、未部署、未执行开发重建或交易所交易。

### 已运行门禁

- 前端 `npm run test`：11文件、81测试通过（`Frontend/tests/`）；`npm run typecheck`、`npm run build` 退出0。构建有既有browser数据过期和chunk大小提示；构建缓存恢复，不留无关变更。
- P0五文件 `-o addopts= --junitxml=artifacts/round2_p0.xml`：99 passed，退出0。
- PG四文件 `test_trading_postgres.py test_research_postgres.py test_trading_projection_rebuild.py test_split_control_postgres.py --tb=short -o addopts= --junitxml=artifacts/round2_pg.xml`：15 passed、0 skipped，退出0，仅隔离临时schema。
- 初次第二轮全量出现4项旧生命周期/退休flag隔离回归（`artifacts/round2_full.xml`），已修复只读装配与finally隔离，未修改旧断言；相关40项定向回归通过。
- 最终 `.venv/Scripts/python.exe -m pytest -q trader/tests --tb=short -o addopts= --junitxml=artifacts/round2_full_final.xml`：**3089 passed、5 skipped、16 warnings，144.32s，退出0**。全部通过证据及测试名保存在该JUnit中；5项skip仍为已有vectorbt/quantstats可选依赖，未新增skip。
- 本轮改动核心/恢复/schema/入口/API文件 Black `--check --workers 1`、isort和 `git diff --check` 通过。Windows Black默认多worker曾挂起，已停止该会话后使用单worker验证。

### 第二轮仍未完成与blocked

1. **未完成（本地实现缺口）**：新交易入口尚未装配策略Runner与Binance private WS；RecoveryCoordinator尚未获实际Adapter连续覆盖证明。REST-only入口明确保持BLOCKED，这不是可运行完整交易闭环已验收。订单分页/已成交分页/WS覆盖完整性仍需实际Adapter接线验证。
2. **保留模型差异**：BAR撮合仍采用后续BAR开盘/触价规则，不模拟排队或冲击；闭合交易统计是报告逻辑。live保留持久事务、证据冲突/outbox编排；BAR使用内存账户，经济函数相同。SELL预估手续费币种契约尚缺，真实费用造成库存负数仍入账并阻断，不能隐藏事实。
3. **blocked（外部验收）**：真实隔离测试环境E2E、主动重连/进程重启实际记录和24h观察窗口；需要外部环境、沙箱操作授权和实际时间。本轮未申请或执行真实账户操作，不得标通过。
