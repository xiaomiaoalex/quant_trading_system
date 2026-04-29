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
