# 开发记录

> 本文件记录每次开发/修复/验证的过程性信息，用于补足 `PLAN.md` 与
> `PROJECT_STATUS.md` 之间的空白。

## 维护规则

- 每次代码或架构性文档变更后，都追加一条记录。
- 记录重点是“为什么改、改了什么、怎么验证、还剩什么风险”。
- 只追加，不重写历史；如果后续推翻前一条判断，在新记录中说明。
- 简短任务可以记录 3-5 行；复杂任务应包含完整模板。

## 记录模板

```markdown
### YYYY-MM-DD HH:mm - 任务标题

- 背景:
- 决策:
- 改动:
- 验证:
- 风险/遗留:
- 关联文档:
```

## 最近记录

### 2026-05-04 10:16 - 数字货币独立风控 P1 快照采集与 OMS 接线

- 背景: P0 已完成 crypto 风控纯计算与 `CryptoPreTradeRiskPlugin`，但真实账户/交易所规则/在途订单快照还没有 Adapter/Service 实现，OMS 下单链路也没有独立风控硬闸。
- 决策: 保持 Core 无 IO，把 Binance 原始字段清洗放在 Adapter mapper/source，把快照聚合放在 Service provider，把最终阻断点接到 OMS 的 `pre_trade_risk_check` 注入回调。
- 改动: 新增 `crypto_risk_mapper.py`、`crypto_risk_source.py`、`crypto_risk_snapshot.py`；`OMSCallbackHandler` 支持 pre-trade 风控拒绝/异常 fail-closed；策略路由增加 `set_pre_trade_risk_check()`；同步接口契约和架构图。
- 验证: P1 新增测试 13 passed；受影响 OMS/crypto/risk 回归 28 passed；P0 回归集 99 passed；`py_compile` passed；scoped `black --check` passed。
- 风险/遗留: 生产环境还需要在 lifespan/配置层实例化 Binance USD-M source、风险预算与 risk check，并用真实 testnet/live key 做联调；当前单测不访问网络。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/PLAN.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-04 09:40 - 安装并固定 Black 格式化工具

- 背景: 上次 crypto 风控 P0 落地后需要按项目约定运行 `black --line-length 100`，但当前 Python 环境未安装 Black。
- 决策: 将 Black 写入 `pyproject.toml` 和 `trader/requirements-ci.txt`，并选择与仓库固定 Python 3.12.5 实际兼容的版本。
- 改动: 安装 `black==24.4.2`；依赖文件新增同版本 pin；对 crypto 风控相关 Python 文件与 `risk_engine.py` 运行 scoped black，避免格式化无关工作区改动。
- 验证: `python -m black --version` 显示 24.4.2；`black --check` 8 files unchanged；`py_compile` passed；`test_crypto_risk_p0.py` 7 passed；风控核心回归 157 passed。
- 风险/遗留: Black 25.1.0/26.3.1 在 Python 3.12.5 上会因 AST safety check 风险硬阻断；若项目未来升级到 Python 3.12.6+，可再评估升级 Black。
- 关联文档: `PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-03 00:00 - 数字货币独立风控 P0 模块落地

- 背景: 用户要求按“风控系统独立于策略”的原则执行完善计划；现有通用风控框架已经存在，但数字货币合约特有的交易所规则、在途订单风险、mark price、leverage bracket 和保证金风险还没有统一门禁。
- 决策: 先落 P0 生存级纯计算能力，不直接接 Binance IO；Adapter/Service 后续负责构建 `CryptoRiskSnapshot`，Core/Policy 只做确定性审批，策略不能绕过。
- 改动: 更新接口契约与架构图；新增 `crypto_risk.py` DTO；新增 `ExchangeRuleGuard`、`OpenOrderExposureCalculator`、`MarginRiskCalculator`；新增 `CryptoPreTradeRiskPlugin` 接入 `RiskEngine` pre-trade 插件体系；扩展 crypto 风控拒绝原因与 KillSwitch 推荐。
- 验证: `test_crypto_risk_p0.py` 7 passed；风险相关回归 157 passed；新增/修改 Python 文件 `py_compile` passed；P0 回归集 99 passed；`black` 未安装，格式化命令未执行，已做 100 字符长行扫描。
- 风险/遗留: 当前尚未接入真实 Binance futures/account/exchangeInfo/leverage bracket 快照，下一步应实现 `CryptoRiskSnapshotProvider` 并把规则/mark price/open orders/account margin 从 Adapter 边界转换为 Core DTO。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/PLAN.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-30 16:48 - 修复 Strategy Management 空白页

- 背景: 用户反馈 `http://localhost:5173/strategies` 打开为空白。
- 决策: 修复运行时空值渲染，而不是改动代码展示 API；详情弹窗只有在选中策略时才需要挂载。
- 改动: `Strategies.tsx` 将 `StrategyDetailModal` 包裹在 `detailStrategy && (...)` 中，移除运行时 `null` 被非空断言掩盖的问题。
- 验证: Frontend `npm run typecheck` passed；`/strategies` dev server 路由返回 200。
- 风险/遗留: 诊断时发现 `npm run build` 仍受既有 `tsconfig.node.json` 配置影响失败，后续可单独处理。
- 关联文档: `PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-30 16:45 - Strategy Details Info 展示最新策略代码

- 背景: 用户希望在 Strategy Management 页面的 Strategy Details 卡片 Info 项下直接看到策略代码，方便核对运行/管理对象对应的源码。
- 决策: 复用已有 `/v1/strategies/{strategy_id}/code/latest` API，不新增后端接口；在前端 hook 层增加 `useLatestStrategyCode`，由详情弹窗 Info 页签按需加载。
- 改动: `StrategyDetailModal` 新增 Strategy Code 区块，显示最新 code version、checksum、创建时间和代码内容；未保存代码的 entrypoint 策略显示无保存代码提示。
- 验证: Frontend `npm run typecheck` passed。
- 风险/遗留: 当前只展示最新版本；如果需要审查历史版本和变更差异，后续应增加 code version selector 和 diff 视图。
- 关联文档: `PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-30 16:37 - Research 页面支持删除 StrategyCandidate

- 背景: Research 页面只有 Create Candidate 和列表，没有删除误建/废弃候选的操作；用户明确指出这一前端缺口。
- 决策: 增加安全删除而不是无条件删除；只删除研究候选实体，不删除策略模板、代码版本、回测报告或 deployment，并保护已进入运行关系的状态。
- 改动: 新增 `DELETE /v1/strategy-candidates/{candidate_id}`、storage 删除方法、service 删除保护和 `strategy_candidate.deleted` 事件；Research 页面增加 Delete 按钮，受保护状态禁用；接口契约补充删除语义。
- 验证: `test_strategy_candidate_workflow.py` 6 passed；相关后端模块 `py_compile` passed；Frontend `npm run typecheck` passed。
- 风险/遗留: 未来切到 PG 持久化时，需要把 hard delete/soft delete 策略定清楚；当前第一版按控制面列表删除并保留审计事件。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-30 17:29 - Strategy Details 支持模块 entrypoint 源码视图

- 背景: Strategy Management 的 Strategy Details 已能展示最新 saved code，但内置 `trader.*` module entrypoint 策略没有 saved code version，用户打开后仍只能看到无代码提示。
- 决策: 不改变 `/code/latest` 的版本语义，新增只读 `StrategyCodeView`；优先返回 saved code，缺省时安全读取本仓库 `trader.*` 模块源码。
- 改动: 新增 `StrategyCodeView` DTO 与 `GET /v1/strategies/{strategy_id}/code/view`；限制源码读取在 `trader` 包内 `.py` 文件；Frontend Strategy Details 改用 code view 展示 `saved code` 或 `module entrypoint`。
- 验证: `test_strategy_code_view.py` 2 passed；后端 `py_compile` passed；Frontend `npm run typecheck` passed；`git diff --check` passed。
- 风险/遗留: 当前只显示最新 saved code 或完整模块源码；后续若需要历史审查，应新增版本选择、diff 和权限控制。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-30 16:17 - 端到端研究与自动组合运行第一版纵向切片

- 背景: 用户目标升级为从 Crypto 多源数据、策略开发、回测、门禁、部署、仓位管理到组合自动启停的流畅工作台；现有仓库已有很多后端能力，但 Strategy Lab 前端契约断裂，生命周期、仓位和自动组合控制未形成统一入口。
- 决策: 先做可验证纵向切片，而不是一次性重写全系统；新增 `StrategyCandidate` 作为研究到运行主实体，`dev_smoke` 回测默认不能作为部署准入，仓位分配和 Portfolio Autopilot 先落控制面 API 与审计事件。
- 改动: 更新接口契约和架构图；新增 strategy candidates、allocations、portfolio autopilot、data catalog 后端路由与服务；扩展 backtest DTO/metrics；修复 Frontend Strategy Lab 的 `deployment_id` 链路；新增 Data、Research、Portfolio Allocation、Portfolio Autopilot 页面入口。
- 验证: `test_strategy_candidate_workflow.py` 4 passed；`test_api_strategy_runner_endpoints.py` 3 passed；新增后端模块 `py_compile` passed；Frontend `npm run typecheck` passed。
- 风险/遗留: 生产级 PG 持久化、真实 FeatureStore 回测读取、自动组合控制器对 live runtime 的安全执行、Allocation/RiskSizer 在 OMS 主下单路径的强制接入仍是后续任务。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-29 23:18 - 新增项目架构图文档与维护约束

- 背景: 仓库已有长篇架构说明，但缺少快速查看当前层级边界、主数据流和关键闭环的架构图入口；架构变更时也没有明确要求同步图文。
- 决策: 新增 `docs/PROJECT_ARCHITECTURE.md` 作为当前架构图文真相源，并将架构图同步要求写入 `AGENTS.md`、`CLAUDE.md`、`.traerules`。
- 改动: 增加五层平面架构图、主数据流图、策略下单闭环、对账恢复闭环、文档契约关系和架构变更更新规则；同步三份规则入口和项目状态。
- 验证: 文档规范变更，无代码测试。
- 风险/遗留: 架构图需要在后续架构性改动中持续维护，否则会退化为静态说明。
- 关联文档: `docs/PROJECT_ARCHITECTURE.md`、`AGENTS.md`、`CLAUDE.md`、`.traerules`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-29 23:13 - 新增 AI TDD 防幻觉流程约束

- 背景: AI 修改代码时容易先臆造函数、DTO 或接口，再围绕虚构实现补测试；仅要求“有测试”不足以防止这种幻觉。
- 决策: 将 TDD 写入 `AGENTS.md`、`CLAUDE.md`、`.traerules` 的公共工程约束，要求行为变更先写基于真实接口的失败测试，再做最小实现和重构。
- 改动: 新增 Red / Green / Refactor 流程、No Hallucinated API 规则和 Verification 要求；明确不存在的新接口必须先更新 `docs/INTERFACE_CONTRACTS.md`。
- 验证: 文档规范变更，无代码测试。
- 风险/遗留: TDD 约束需要在后续实际编码任务中执行；文档本身不能替代测试执行。
- 关联文档: `AGENTS.md`、`CLAUDE.md`、`.traerules`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-29 23:04 - 同步 `.traerules` 与 AI 规则入口

- 背景: `.traerules` 也是 Trae/Kilo 等工具入口的工程规则，但它与 `AGENTS.md`、`CLAUDE.md` 在架构边界、测试规范、文档闭环和技术栈约束上存在口径漂移。
- 决策: 保留 `.traerules` 原有分支/PR/AI 集成规则，同时把公共工程约束收敛到与 `AGENTS.md`、`CLAUDE.md` 一致，并在三份规则入口都加入同步检查要求。
- 改动: 扩展 `.traerules` 的任务处理原则、项目扫描、常用命令、五层架构、测试规范、工程纪律、文档闭环和红线操作；更新 `AGENTS.md`、`CLAUDE.md` 的规则入口同步要求。
- 验证: 文档规范变更，无代码测试。
- 风险/遗留: 三份规则文档仍存在面向不同工具的专属章节；后续只要求公共工程约束一致，不要求逐字相同。
- 关联文档: `.traerules`、`AGENTS.md`、`CLAUDE.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-29 22:50 - 新增接口契约与命名规范约束

- 背景: AI 与多人协作修改代码时，函数签名、DTO、事件字段和跨层命名容易漂移，导致同一概念出现多个名称或同名不同义。
- 决策: 新增 `docs/INTERFACE_CONTRACTS.md` 作为接口契约与命名规范单一真相源，并把“先契约、后实现”纳入 `AGENTS.md`、`CLAUDE.md` 与 `.traerules`。
- 改动: 新增标准领域词汇、外部字段映射、跨层接口规则、事件 Schema 规则、接口变更流程和 AI 修改前检查清单；同步更新协作约束和项目状态。
- 验证: 文档规范变更，无代码测试。
- 风险/遗留: 契约文档需要在后续接口改名时持续维护，否则会再次退化为过期说明。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`AGENTS.md`、`CLAUDE.md`、`.traerules`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-28 14:44 - 同步 Claude 与 Agents 文档闭环要求

- 背景: `AGENTS.md` 已纳入 `DEVELOPMENT_LOG.md` 和计划文档新鲜度要求，但 `CLAUDE.md` 仍使用旧版 Documentation Updates 规则。
- 决策: 以 `AGENTS.md` 的 Mandatory Workflow 为准，同步更新 `CLAUDE.md`，让不同大模型入口遵守一致的项目文档闭环。
- 改动: 替换 `CLAUDE.md` 的 Documentation Updates 段落，明确 `PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`、`DEVELOPMENT_LOG.md` 与计划文档更新条件。
- 验证: 文档规范变更，无代码测试。
- 风险/遗留: 后续若再次修改协作规范，需要同时检查 `AGENTS.md` 与 `CLAUDE.md`。
- 关联文档: `CLAUDE.md`、`PROJECT_STATUS.md`

### 2026-04-28 14:41 - 测试全局状态污染隔离与全量回归恢复

- 背景: P0 回归和多个目标测试单独运行通过，但全量测试受全局状态、环境变量、logger 与 collection-time mock 污染影响，出现顺序相关失败。
- 决策: 把测试隔离收敛到 pytest autouse fixture，显式重置控制面/服务层单例、敏感环境变量和 `asyncpg` module；同时修复会触发真实 broker 或旧 API 兼容失败的控制面入口。
- 改动: 新增 `trader/tests/conftest.py` 隔离夹具；扩展 strategy routes reset hook 与 live=false broker 短路；补充 strategy event reset；兼容 int/string version；恢复 legacy deployment start/stop；将控制面 backtest 改为确定性 synthetic 路径；调整少量旧测试断言到当前 API 语义。
- 验证: `python -m pytest -q trader/tests/ --tb=short` passed；P0 回归集 passed。
- 风险/遗留: 仍有既有 warnings 待后续清理，包括 Pydantic V2 deprecated config、unknown integration mark 和 onchain AsyncMock await warning。
- 关联文档: `PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-28 07:49 - OMS Pre-trade 余额 Gate 完善

- 背景: 策略运行时持续出现交易所 `insufficient balance` 拒单，需要把交易所拒单从常态控制流降级为最后防线。
- 决策: 在 OMS 下单前执行本地余额 gate，账户余额不可获取时 fail-closed，并用短 TTL reservation 降低连续信号超额提交风险。
- 改动: 更新 `trader/services/oms_callback.py`，新增 `trader/tests/test_oms_pretrade_balance.py`，并补齐 E2E fake broker 的账户/行情接口。
- 验证: `test_oms_pretrade_balance.py`、`test_runtime_observability.py`、`test_oms_idempotency.py`、`test_automated_trading_e2e.py` 合计 56 passed。
- 风险/遗留: 当前 reservation 仍是进程内短 TTL 防抖，不替代独立 AccountState/ExecutionBudget 服务。
- 关联文档: `PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-28 07:55 - 增加开发记录文档规范

- 背景: `PLAN.md` 适合记录计划，`PROJECT_STATUS.md` 适合记录阶段状态，但缺少按时间追加的开发过程记录。
- 决策: 新增 `DEVELOPMENT_LOG.md` 作为开发流水账，后续开发除更新计划/状态/经验文档外，也追加开发记录。
- 改动: 新增开发记录模板与维护规则。
- 验证: 文档变更，无代码测试。
- 风险/遗留: 需要在协作规范中同步要求，避免后续遗漏。
- 关联文档: `AGENTS.md`、`PROJECT_STATUS.md`
