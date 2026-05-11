# Interface Contracts - 接口契约与命名规范

> 本文档是多人协作和 AI 修改代码时的接口单一真相源。
> `docs/DATA_CONTRACT.md` 负责研究数据字段契约；本文负责代码接口、DTO、事件、跨层命名与变更流程。

---

## 1. 契约原则

1. **先契约，后实现**：新增、删除、重命名跨模块接口前，必须先更新本文档。
2. **边界转换，内部统一**：外部 API / Binance 原始字段只允许在 Adapter 或 API 边界出现，进入 Core / Service 后必须转换为内部标准命名。
3. **类型即契约**：跨模块接口必须有明确类型，优先使用 `@dataclass(slots=True)`、Pydantic model、`Protocol` 或显式 DTO。
4. **兼容性显式化**：破坏性接口变更必须记录旧字段、迁移策略、兼容窗口和测试覆盖。
5. **契约测试兜底**：任何接口改名或签名变更必须补充或更新对应 contract / unit / integration tests。

---

## 2. 标准领域词汇

| 标准名称 | 含义 | 允许出现层 | 禁止混用 |
|----------|------|------------|----------|
| `cl_ord_id` | 客户端订单 ID；系统订单幂等主键 | Core / Service / Persistence / internal DTO | `client_order_id`, `clientOrderId`, `cid` |
| `exec_id` | 成交执行 ID；成交去重键 | Core / Service / Persistence / internal DTO | `execution_id`, `trade_id` |
| `broker_order_id` | 交易所订单 ID | Adapter / Service / Persistence | `order_id` 表示内部订单 |
| `symbol` | 交易对，如 `BTCUSDT` | 全层 | `ticker`, `pair` |
| `side` | 下单方向，值域为 `BUY` / `SELL` | API / Service / Adapter | `direction` 表示订单方向 |
| `signal_type` | 策略信号方向或意图 | Strategy / Signal domain | `direction` |
| `qty` | 数量，内部订单与成交统一字段 | Core / Service / Persistence | `quantity`, `amount` |
| `price` | 价格 | 全层 | `px` |
| `trace_id` | 跨链路追踪 ID | 全层 | `request_id` 替代业务追踪 |
| `deployment_id` | 策略运行实例 ID | Control / Service / Event stream | `strategy_id` 表示运行实例 |
| `strategy_id` | 策略模板或逻辑策略 ID | Control / Service / Event stream | `deployment_id` 表示模板 |
| `candidate_id` | 策略研究/开发生命周期实体 ID | Control / Service / Persistence / Event stream | `strategy_id` 或 `deployment_id` 表示研究候选 |
| `feature_version` | 研究数据与特征版本 | Control / Service / Persistence / Research data DTO | `version` 表示代码或部署版本 |
| `decision_trace_id` | 单次策略信号/风控/下单决策链路 ID；用于跨事件追踪为什么下单、缩量或拒绝 | 全层 | `trace_id` 作为存储/日志字段时必须与其兼容或由其派生 |

### 外部字段映射

| 外部来源 | 外部字段 | 内部标准字段 | 转换位置 |
|----------|----------|--------------|----------|
| Binance REST / WS | `clientOrderId`, `c` | `cl_ord_id` | `trader/adapters/binance/` |
| Binance REST / WS | `orderId`, `i` | `broker_order_id` | `trader/adapters/binance/` |
| Binance executionReport | `I` 或交易所执行编号 | `exec_id` | `trader/adapters/binance/private_stream.py` |
| Binance executionReport | `L` | `price` 或 `fill_price` | `trader/adapters/binance/private_stream.py` |
| Binance executionReport | `l` | `qty` 或 `fill_qty` | `trader/adapters/binance/private_stream.py` |
| Frontend/API legacy | `quantity` | `qty` | `trader/api/` 边界模型 |

---

## 3. 跨层接口规则

### 3.1 Core Plane

- Core 内禁止出现交易所原始字段名，如 `clientOrderId`、`orderId`、`executionReport`。
- Core 接口必须保持无 IO、确定性和单调状态机语义。
- 订单状态变更接口必须以 `cl_ord_id` 为主键，成交去重必须包含 `exec_id`。
- Core 暴露给上层的对象必须使用内部标准字段。

### 3.2 Adapter Plane

- Adapter 是外部字段进入系统的唯一清洗边界。
- Adapter 必须把 Binance / HTTP / WS 字段映射为内部 DTO 后再传给 Service 或 Core。
- Adapter 不得把原始 payload 直接传入 Core；如需保留原始数据，只能作为审计字段附加在 Adapter / Persistence 层。

### 3.3 Persistence Plane

- 持久化事件字段必须使用内部标准字段。
- Event Log 是回放真相源，字段重命名必须提供迁移或兼容读取逻辑。
- 风险、订单、成交相关表的唯一键必须与幂等语义一致：订单用 `cl_ord_id`，成交用 `cl_ord_id + exec_id`。

### 3.4 Policy / Service Plane

- Service 方法参数应优先接收 DTO，而不是松散的 `dict[str, Any]`。
- Risk / KillSwitch 接口必须显式返回决策状态，不得用 `None` 隐式表示通过或失败。
- 涉及预算、余额、风控、对账的接口必须定义 fail-closed 行为。

### 3.5 Control Plane / API

- API 可以兼容外部或历史字段名，但进入 Service 前必须转换为内部标准 DTO。
- API response 字段调整必须同步前端 hook / 页面 / 类型定义。
- 对外兼容字段必须在模型注释或文档中标记为 legacy。

---

## 4. 事件 Schema 规则

所有新事件必须记录：

| 字段 | 要求 |
|------|------|
| `event_type` | 稳定字符串，不随类名随意变化 |
| `schema_version` | 从 `1` 开始，破坏性变更递增 |
| `trace_id` | 必填，贯穿信号、订单、成交、风控 |
| `cl_ord_id` | 订单相关事件必填 |
| `exec_id` | 成交相关事件必填 |
| `source` | 事件来源，如 `ws`, `rest_alignment`, `api`, `risk` |
| `occurred_at` | 事件发生时间，统一 UTC 或毫秒时间戳，并在类型中明确 |

事件兼容规则：

- 新增可选字段：允许，必须有默认值或兼容读取。
- 删除字段：破坏性变更，必须提升 `schema_version`。
- 字段重命名：必须同时支持旧字段读取一段兼容窗口，并补充回放测试。
- 语义变化：即使字段名不变，也视为破坏性变更。

---

## 5. 接口变更流程

修改函数、类、DTO、事件、API 字段或跨模块调用前，按以下顺序执行：

1. 查找现有定义：`rg "<name>" trader docs Frontend`
2. 确认概念归属：领域词汇表是否已有标准名称。
3. 更新本文档：新增或调整标准名称、接口规则、兼容策略。
4. 更新类型定义：dataclass / Pydantic model / Protocol / TypedDict。
5. 更新实现：只在必要层做字段映射，不跨层泄漏外部名称。
6. 更新测试：覆盖旧名兼容、新名主路径、重复/乱序/缺字段场景。
7. 更新状态文档：按 `AGENTS.md` / `CLAUDE.md` 文档闭环要求记录。

---

## 6. AI 修改代码前检查清单

AI 在改动涉及接口、命名、DTO、事件或跨层调用时，必须先回答：

- 这个概念在本文档中是否已有标准名称？
- 新字段属于外部字段、内部字段、展示字段还是持久化字段？
- 字段转换发生在哪一层，是否污染 Core？
- 是否存在旧字段兼容需求？
- 是否会影响幂等键、回放、REST Alignment、前端类型或 PG schema？
- 对应测试是否能防止同名不同义、同义不同名的问题再次出现？

---

## 7. 当前强制约束

- `cl_ord_id` 是内部客户端订单 ID 标准名；只有 Adapter/API 边界可以接收或输出 `clientOrderId` / `client_order_id`。
- `exec_id` 是成交幂等标准名；成交写入不得只依赖 `broker_order_id`。
- `deployment_id` 与 `strategy_id` 不得混用：前者是运行实例，后者是策略模板。
- `candidate_id` 是策略从草稿、调试、回测、门禁到部署的生命周期主键；不得用 `strategy_id` 或 `deployment_id` 代替。
- `feature_version` 必须贯穿数据目录、回测、门禁、候选策略和部署审计；代码版本继续使用 `code_version`，部署版本继续使用 `version`。
- `signal_type` 是策略信号字段；不得新增 `direction` 表示同一含义。
- `qty` 是内部数量字段；API legacy 的 `quantity` 必须在边界转换。
- Core Plane 不允许引入外部字段名或原始交易所 payload。

---

## 8. 研究到运行工作流接口

### 8.1 StrategyCandidate

`StrategyCandidate` 是策略研究/开发生命周期的控制面实体，状态机固定为：

`DRAFT -> DEBUG_PASSED -> BACKTEST_RUNNING -> BACKTEST_PASSED -> VALIDATION_PASSED -> APPROVED_FOR_PAPER -> PAPER_RUNNING -> STOPPED`

异常状态：

- `PAUSED_BY_RISK`: 由组合自动控制器或风险系统暂停。
- `REJECTED`: 调试、回测、门禁或人工/系统决策失败。

关键字段：

| 字段 | 含义 |
|------|------|
| `candidate_id` | 候选策略生命周期 ID |
| `strategy_id` | 策略模板 ID |
| `code_version` | 已保存策略代码版本 |
| `backtest_run_id` | 关联回测运行 ID |
| `deployment_id` | 通过门禁后生成或绑定的运行实例 ID |
| `feature_version` | 回测和门禁使用的数据/特征版本 |
| `validation` | 门禁结果，包含 `passed`、`failed_rules`、`metrics`、`evidence_refs` |

所有状态迁移必须写入 `strategy_candidate.lifecycle` 事件，payload 至少包含 `candidate_id`、`strategy_id`、`from_status`、`to_status`、`reason`。

候选策略删除接口为 `DELETE /v1/strategy-candidates/{candidate_id}`，仅删除研究候选实体，不删除策略模板、代码版本、回测报告或部署实例。处于 `APPROVED_FOR_PAPER`、`PAPER_RUNNING`、`PAUSED_BY_RISK` 的候选必须先停止/解除运行关系后才能删除；删除必须写入 `strategy_candidate.deleted` 审计事件。

### 8.2 BacktestDatasetSpec / BacktestGateResult

`BacktestDatasetSpec` 作为回测数据选择契约，第一版字段为：

- `symbols`
- `start_ts_ms`
- `end_ts_ms`
- `feature_version`
- `venue`
- `initial_capital`
- `fee_bps`
- `slippage_bps`
- `benchmark`
- `data_mode`: `real_feature_store` 或 `dev_smoke`

`dev_smoke` 只能用于开发烟测，不能作为 Promote/部署准入依据。`BacktestGateResult` 必须明确给出 `passed`、`failed_rules`、`metrics`、`evidence_refs`。

### 8.3 Allocation / Autopilot

策略仓位配置标准字段：

- `strategy_id`
- `deployment_id`
- `max_notional`
- `max_symbol_exposure`
- `max_portfolio_weight`
- `min_confidence`
- `allow_short`
- `priority`
- `enabled`

每次分配链路必须记录 `AllocationTrace`，包含 `raw_requested_size`、`risk_sized_qty`、`allocated_qty`、`final_order_qty`、`allocation_decision`、`reject_or_clip_reason`。

组合自动控制器输出 `PortfolioAutopilotDecision`，动作值域为 `START`、`PAUSE`、`RESUME`、`STOP`、`REDUCE_ALLOCATION`、`DISABLE_ALLOCATION`。所有自动动作必须写入 `portfolio_autopilot.decision` 事件。

### 8.4 Market Risk Contract

市场无关风控契约是 Core / Policy / Persistence 之间的标准风险语言；Crypto、A 股、期货等市场规则必须作为 specialization 或 plugin 挂载在该契约之下，不能把单一市场语义扩散为平台默认命名。

新增 Core 内部通用 DTO 必须使用以下标准字段：

| 字段 | 含义 |
|------|------|
| `asset_class` | 资产类别，如 `crypto`、`cn_stock`、`futures` |
| `venue` | 交易场所或账户通道，如 `binance`、`sse`、`szse` |
| `risk_price` | 风控估值价格；crypto futures 可由 `mark_price` 映射而来，A 股可用参考价/最新可交易价 |
| `notional` | `abs(qty) * risk_price` 或订单风险名义价值 |
| `group` | 组合风险聚合分组；crypto 可映射 cluster，A 股可映射行业、主题、指数或风格因子 |
| `metadata` | 市场特有字段载体；如强平价、杠杆、T+1 可卖数量、涨跌停价，不得反向污染通用字段 |

新增跨模块类型：

| 类型 | 所属层 | 责任 |
|------|--------|------|
| `AssetClass` | Core domain model | 标准资产类别枚举，禁止用 `Crypto*` 名称表达通用资产类别 |
| `MarketInstrumentSpec` | Core domain model | 交易规则的市场无关形态，如 tick、step、min_qty、min_notional、可选 lot/涨跌停 metadata |
| `MarketAccountRisk` | Core domain model | 账户风险通用形态，如 equity、available_cash、currency、venue |
| `MarketPositionRisk` | Core domain model | signed 持仓、入场价、风控价格与市场特有 metadata |
| `MarketOpenOrderRisk` | Core domain model | 在途订单的标准风险形态，使用 `cl_ord_id` / `qty` / `filled_qty` |
| `MarketRiskBudget` | Core domain model | symbol、group、total 等市场无关预算 |
| `MarketRiskSnapshot` | Core domain model | pre-trade gate 的市场无关风险输入快照 |
| `MarketRiskAuditEvent` | Core/Persistence DTO | PG-first 风险审计事件标准字段 |
| `MarketRiskAuditRepository` | Persistence adapter | PG-first 持久化 `risk:market` 审计事件；PG 不可用时只允许按配置回退控制面事件流 |

规则：

- `CryptoRiskSnapshot`、`CryptoInstrumentSpec` 等类型继续保留，但必须视为 crypto specialization；跨市场新能力不得直接依赖 `CryptoRisk*` 作为通用契约。
- `CryptoInstrumentSpec` 必须能转换为 `MarketInstrumentSpec`；合约专属字段如 leverage bracket、liquidation price 只能进入 `metadata` 或 crypto plugin。
- `ExchangeRuleGuard` 的核心校验只依赖通用 instrument spec 字段，不得依赖 Binance 或 Crypto 命名。
- `risk:market` 审计事件标准字段固定为 `stream_key`、`event_type`、`schema_version`、`trace_id`、`ts_ms`、`asset_class`、`venue`、`account_id`、`payload`。
- `decision_trace_id` 是业务决策链路 ID；进入 `MarketRiskAuditEvent.trace_id` 时必须同时写入 payload 的 `decision_trace_id`，便于 API/前端在只看 payload 时仍能关联同一决策链。
- PG-first 风险审计表默认命名为 `risk_audit_events`；crypto 页面可继续以 `stream_key=risk:crypto` 过滤展示，但不得新建只服务 crypto 的平台级审计契约。
- `MarketRiskAuditRepository.append(event)` 必须返回兼容 `EventEnvelope` 的 dict；`list_events(stream_key, event_type, trace_id, since_ts_ms, limit)` 必须优先查询 PG，PG 不可用或写入失败时回退控制面内存事件流。
- 回测引擎适配器必须通过 `DataProviderPort` 获取历史数据；具体数据源如 Binance、A 股行情源只能在 Adapter/Service 装配层注入，不能在 engine 内部直接实例化。

### 8.5 Crypto Risk Gate

数字货币独立风控使用标准 DTO 将策略意图、账户风险快照、交易所规则与在途订单隔离开。策略只能提交 `Signal` / trade intent，不能直接决定最终下单或突破风控预算。

新增 Core 内部 DTO 必须使用以下标准字段：

| 字段 | 含义 |
|------|------|
| `cl_ord_id` | 在途订单的客户端订单 ID；Adapter 边界负责从 `clientOrderId` 转换 |
| `qty` | 标的数量，持仓为带方向 signed qty，订单为正数量 |
| `filled_qty` | 在途订单已成交数量 |
| `mark_price` | 合约保证金与强平风险使用的标记价格 |
| `notional` | `abs(qty) * mark_price` 或订单风险名义价值 |
| `initial_margin` | 初始保证金占用估算 |
| `maintenance_margin` | 维持保证金估算 |
| `margin_ratio` | 维持保证金 / 保证金余额 |
| `liquidation_buffer_ratio` | 与强平价的距离比例；缺失时必须 fail-closed 或由配置显式允许 |
| `reduce_only` | 只减仓订单；风控不得把未成交 reduce-only 订单提前计入风险释放 |

新增跨模块类型：

| 类型 | 所属层 | 责任 |
|------|--------|------|
| `CryptoInstrumentSpec` | Core domain model | 交易所规则的内部标准形态，如 tick、step、min_notional |
| `LeverageBracket` | Core domain model | 合约名义价值分层、最大杠杆和维持保证金率 |
| `CryptoPositionRisk` | Core domain model | signed 持仓、入场价、标记价、杠杆与可选强平价 |
| `OpenOrderRisk` | Core domain model | 在途订单的标准风险形态，使用 `cl_ord_id` / `qty` |
| `CryptoRiskSnapshot` | Core domain model | pre-trade gate 的唯一风险输入快照 |
| `CryptoRiskBudget` | Core domain model | symbol、cluster、total、margin、强平缓冲等账户级预算 |
| `CryptoRiskDataSource` | Service Protocol | 从 Adapter/账户源读取已标准化的账户、规则、持仓、在途订单、mark price |
| `DataSourceCryptoRiskSnapshotProvider` | Service | 聚合 `CryptoRiskDataSource` 输出为 `CryptoRiskSnapshot`，缺关键数据必须 fail-closed |
| `BinanceFuturesRiskDataSource` | Adapter | 调用 Binance USD-M REST，使用 mapper 将原始字段转换为内部 DTO |
| `CryptoRiskRuntimeConfig` | Control Plane config | 从环境变量解析数字货币风控启用状态、Binance USD-M base URL、基础 symbols 与预算 |
| `CryptoRiskRuntimeComponents` | Control Plane runtime wiring | 持有 concrete source、snapshot provider 与注入 OMS 的 `pre_trade_risk_check` |
| `CryptoRiskRuntimeStatus` | API DTO | 暴露当前是否 enabled/wired/fail_closed、`execution_env`、base URL、基础 symbols、预算和最近错误 |
| `CryptoRiskBudgetUpdateRequest` | API DTO | 运行时热更新风险预算；未填写字段沿用当前值，symbol cap 使用内部标准 symbol |
| `CryptoRiskProbeRequest` | API DTO | 只读联通性检查请求，字段为 `symbols` 与 `requested_by` |
| `CryptoRiskProbeResponse` | API DTO | 只读联通性检查结果，包含 `read_only`、`mode`、`execution_env`、symbols、耗时和逐项检查结果 |
| `PortfolioExposureAggregator` | Core domain service | 基于 `symbol_clusters` 聚合已成交持仓、active open orders 与本次拟下单的 cluster 风险 |

规则：

- Binance 原始字段如 `clientOrderId`、`maintMarginRatio`、`notionalCap` 只能在 Adapter 边界出现，进入 Core 前必须转换为以上 DTO。
- `CryptoRiskSnapshot` 缺失必要价格、规则、账户或 bracket 数据时，pre-trade 风控必须 fail-closed。
- 在途 `reduce_only` 订单不得提前释放风险预算；只有成交事件进入账本后才减少真实风险。
- pre-trade 插件只能返回通过、拒绝或附带裁剪建议，不能直接下单或修改 OMS 状态。
- `OMSCallbackHandler` 可接收 `pre_trade_risk_check: Callable[[Signal], Awaitable[RiskCheckResult]]`；该回调必须在真实下单前执行，返回拒绝或异常时 OMS 必须 fail-closed 且不得调用 broker `place_order`。
- `clientOrderId`、`origQty`、`executedQty`、`positionAmt`、`markPrice` 等 Binance 字段只能出现在 `trader/adapters/binance/*mapper*` 或具体 REST source 中；`trader/services/` 和 `trader/core/` 只能看到 `cl_ord_id`、`qty`、`filled_qty`、`mark_price`。
- `total_notional_cap` 检查需要覆盖账户当前持仓和全部 active open orders；若任一参与计算的 symbol 缺少 mark price，快照提供者必须拒绝构建快照，不能按 0 或跳过处理。
- `cluster_notional_caps` 检查需要通过 `symbol_clusters` 将 symbol 映射到 cluster，并覆盖当前持仓、全部 active open orders 与 proposed order；cluster cap 启用但目标 symbol 未配置 cluster 时必须 fail-closed，不能让未映射 symbol 绕过组合级预算。
- `CRYPTO_CLUSTER_EXPOSURE` 表示组合级 cluster 预算拒绝，KillSwitch 推荐级别为 L1 `NO_NEW_POSITIONS`。
- Control Plane 默认不启用 Binance USD-M 风控 source；只有 `CRYPTO_RISK_ENABLED` 为 `1`、`true`、`yes` 或 `on` 时才实例化 source 并注入 OMS。非法显式布尔值必须启动失败，而不是静默降级为关闭。
- `execution_env` 来自 `BINANCE_ENV`，当前执行适配器默认连接 Binance demo；USD-M 风控 source 的 `mode` 仅描述 `CRYPTO_RISK_FUTURES_BASE_URL` 的只读数据源模式，不代表已启用 Futures 下单。
- 运行时配置环境变量：`CRYPTO_RISK_FUTURES_BASE_URL`、`CRYPTO_RISK_BASE_SYMBOLS`、`CRYPTO_RISK_TOTAL_NOTIONAL_CAP`、`CRYPTO_RISK_SYMBOL_NOTIONAL_CAPS`、`CRYPTO_RISK_SYMBOL_CLUSTERS`、`CRYPTO_RISK_CLUSTER_NOTIONAL_CAPS`、`CRYPTO_RISK_MAX_MARGIN_RATIO`、`CRYPTO_RISK_MIN_LIQUIDATION_BUFFER_RATIO`、`CRYPTO_RISK_TIMEOUT_SECONDS`、`CRYPTO_RISK_PROXY_URL`、`CRYPTO_RISK_MAX_RETRIES`。
- `CRYPTO_RISK_SYMBOL_NOTIONAL_CAPS` 和 `CRYPTO_RISK_CLUSTER_NOTIONAL_CAPS` 格式为 `KEY=DECIMAL` 的逗号分隔列表；`CRYPTO_RISK_SYMBOL_CLUSTERS` 格式为 `SYMBOL=CLUSTER`。symbol 在 Control/Adapter 边界统一标准化为大写无分隔符，cluster 统一大写。
- `CRYPTO_RISK_ENABLED=true` 时缺少 Binance API key/secret 或预算十进制解析失败必须 fail-closed；不得生成无效 `pre_trade_risk_check`。
- `set_pre_trade_risk_check()` 必须支持 OMS handler 已经创建后的 late binding，确保 lifespan 先创建 fill handler 再初始化风险 source 时，后续订单仍会经过独立风控。
- `GET /v1/risk/crypto/runtime` 返回当前 runtime 状态；不得暴露 API key、secret 或签名参数。返回字段包含 `execution_env`，用于区分当前执行环境（本仓库默认 `demo`）和 USD-M 风控 source URL。
- `PATCH /v1/risk/crypto/budget` 只允许更新 `CryptoRiskBudget`，不改变交易所 base URL、凭证或 source 连接；runtime 未启用或未成功 wired 时必须返回冲突/失败，不得假装热更新成功。
- 热更新预算必须重新构建 snapshot provider / pre-trade risk check 并通过 `set_pre_trade_risk_check()` late-bind 到已存在 OMS handler；更新失败必须保持旧 check 或切换为 fail-closed，不能产生空风控。
- 预算热更新成功后必须写入控制面事件流：`stream_key=risk:crypto`、`event_type=crypto_risk.budget_updated`，payload 至少包含 `updated_by`、`previous_budget`、`new_budget`、`runtime_before`、`runtime_after`。
- `GET /v1/risk/crypto/budget/audit` 返回上述预算变更审计事件；该查询与通用 `/v1/events?stream_key=risk:crypto` 保持同一事件来源。
- `POST /v1/risk/crypto/probe` 是只读 readiness probe，只允许调用账户风险、mark price、instrument spec、leverage bracket、持仓、在途订单和 venue health 的读取方法；不得下单、撤单、调整杠杆或修改 runtime 配置。
- probe 成功或失败均返回逐项 `checks`；runtime 未 wired 时返回 409，不得尝试临时创建 source 或绕过显式启用配置。
- probe 成功返回后必须写入控制面事件流：`stream_key=risk:crypto`、`event_type=crypto_risk.probe_run`，payload 至少包含 `ok`、`read_only`、`mode`、`execution_env`、`symbols`、`requested_by`、`duration_ms` 和 `checks`。
- `risk:crypto` 是 `risk:market` 审计流的 crypto 过滤视图；payload 继续承载预算更新、probe 结果和后续 pre-trade decision evidence。
- crypto 风控审计查询必须 PG-first：PostgreSQL 可用时从 `risk_audit_events` 按 `stream_key=risk:crypto` 读取，PG 不可用时按配置回退控制面内存事件流；不得因为 PG 短暂不可用而让预算更新/probe 请求 fail-open。
- crypto pre-trade 拒绝必须写入市场无关风险审计：`stream_key=risk:crypto`、`event_type=crypto_risk.pre_trade_rejected`，payload 至少包含 `signal_id`、`strategy_id` 或 `strategy_name`、`symbol`、`signal_type`、`qty`、`price`、`rejection_reason`、`risk_level`、`message`、`details`、`recommended_killswitch_level`。
- pre-trade rejection evidence 必须在 Control/Service wrapper 层写入 `MarketRiskAuditRepository`；`CryptoPreTradeRiskPlugin` 和 Core service 仍保持无 IO。审计写入失败不得改写原始风控结果：已拒绝的信号仍然拒绝，风控回调异常仍然 fail-closed 抛出。
- `GET /v1/risk/crypto/audit` 是 crypto 风控审计的 PG-first 查询入口，支持 `event_type`、`trace_id`、`signal_id`、`since_ts_ms`、`limit`；其中 `signal_id` 基于 payload 过滤，用于定位单个策略信号的 pre-trade evidence。
- `GET /v1/risk/crypto/audit/summary` 是 crypto 风控审计的聚合统计入口，支持 `group_by`（值域：`reason`、`symbol`、`strategy`、`risk_level`）、`since_ts_ms`（时间过滤）、`limit`（分组上限，默认50）和 `event_type`（默认 `crypto_risk.pre_trade_rejected`）。返回格式为 `{ items: [{ key, count, latest_ts_ms, sample_event_id }], total, since_ts_ms }`，其中 `items` 按 count 降序排列。空结果时 `items=[]` 且 `total=0`。分组维度：`reason` 按 `payload.rejection_reason`、`symbol` 按 `payload.symbol`、`strategy` 按 `payload.strategy_id`（见下方 fallback）、`risk_level` 按 `payload.risk_level`。
  - **strategy 分组 fallback**：优先取 `payload.strategy_id`；若 `strategy_id` 为 `None` 或空字符串则 fallback 到 `payload.strategy_name`；两者均为 `None` 或空时归一化为 `"unknown"` 键。
  - **通用归一化**：所有分组维度（reason、symbol、strategy、risk_level）的值若为 `None` 或空字符串，统一归一化为 `"unknown"` 键后参与聚合。

### 8.5.1 Funding/OI 历史窗口风控

数字货币独立风控支持 Funding Rate Z-Score 和 Open Interest 变化率作为历史窗口派生指标。这些指标需要从 FeatureStore 读取历史数据并计算派生值。

新增 Core 内部 DTO：

| 字段 | 含义 |
|------|------|
| `symbol` | 交易对 |
| `current_funding_rate` | 当前资金费率（可选，缺失时为 `null`） |
| `funding_rate_z_score` | 资金费率 Z-Score（相对历史窗口均值）；启用时由 Core 纯计算 |
| `funding_rate_mean` | 历史窗口资金费率均值 |
| `funding_rate_std` | 历史窗口资金费率标准差 |
| `funding_history_count` | 计算 Z-Score 使用的历史样本数 |
| `current_open_interest` | 当前未平仓合约量（可选，缺失时为 `null`） |
| `open_interest_change_rate` | OI 百分比变化率 `(current_oi - mean) / mean * 100`；启用时由 Core 纯计算 |
| `oi_mean` | 历史窗口 OI 均值 |
| `oi_history_count` | 计算变化率使用的历史样本数 |
| `funding_data_stale` | funding 数据过期标志（超过配置阈值未更新） |
| `oi_data_stale` | OI 数据过期标志（超过配置阈值未更新） |
| `data_stale` | **兼容聚合属性**：`funding_data_stale or oi_data_stale` |
| `data_age_ms` | 最新数据相对当前时间的年龄（毫秒） |
| `funding_window_insufficient` | funding 历史窗口不足（样本数 < funding_min_periods） |
| `oi_window_insufficient` | OI 历史窗口不足（样本数 < oi_min_periods） |
| `window_insufficient` | **兼容聚合属性**：`funding_window_insufficient or oi_window_insufficient` |
| `funding_current_missing` | funding 当前值缺失（`current_funding_rate = null`） |
| `oi_current_missing` | OI 当前值缺失（`current_open_interest = null`） |
| `any_funding_missing` | **兼容聚合属性**：任一 funding 条件不满足 |
| `any_oi_missing` | **兼容聚合属性**：任一 OI 条件不满足 |
| `oi_std` | **保留字段**：历史窗口 OI 标准差（当前 change_rate 计算不使用） |
| `latest_funding_ts_ms` | 最新 funding 数据时间戳 |
| `latest_oi_ts_ms` | 最新 OI 数据时间戳 |

新增跨模块类型：

| 类型 | 所属层 | 责任 |
|------|--------|------|
| `CryptoFundingOIRiskMetrics` | Core domain model | Funding/OI 历史窗口派生指标 |
| `FundingOIMetricsProvider` | Service Protocol | 从 FeatureStore 读取历史数据并提供当前 funding/OI 值 |
| `FundingOIWindowCalculator` | Core domain service | 纯计算 funding z-score 和 OI change rate |

规则：

- Funding/OI 历史窗口指标计算必须由 Core domain service 纯函数完成，不得有 IO。
- `FundingOIMetricsProvider` 位于 Service 层，负责从 FeatureStore 读取历史数据并组装当前值。
- `FundingRateZScore` 和 `OIChangeRate` 的 Core 纯计算必须可独立测试，不依赖外部数据源。
- 窗口不足时 `funding_rate_z_score` / `open_interest_change_rate` 返回 `None`，不得 fail-open。
- 数据过期（超过配置 `max_data_age_seconds`）时 `data_stale=True`，风控启用该指标时必须 fail-closed。
- `CryptoRiskSnapshot` 包含可选 `funding_oi_metrics: dict[str, CryptoFundingOIRiskMetrics]`。
- 缺 funding 或 OI 数据时只影响对应启用阈值的检查，其他风控继续生效。
- `CryptoRiskBudget` 支持以下新字段：
  - `max_abs_funding_rate_z_score: Decimal`：资金费率 Z-Score 最大绝对值
  - `max_abs_open_interest_change_rate: Decimal`：OI 变化率最大绝对值
  - `funding_history_window: int`：计算 Z-Score 的历史窗口大小（默认 20）
  - `oi_history_window: int`：计算 OI 变化率的窗口大小（默认 20）
  - `funding_min_periods: int`：计算 Z-Score 最小样本数（默认 10）
  - `oi_min_periods: int`：计算 OI 变化率最小样本数（默认 10）
  - `max_data_age_seconds: int`：数据最大有效期（默认 24 * 3600）

**后续计划接入**（运行时环境变量，待 P4.8 实现）：
- `CRYPTO_RISK_MAX_ABS_FUNDING_RATE_Z_SCORE`
- `CRYPTO_RISK_MAX_ABS_OPEN_INTEREST_CHANGE_RATE`
- `CRYPTO_RISK_FUNDING_HISTORY_WINDOW`
- `CRYPTO_RISK_OI_HISTORY_WINDOW`
- `CRYPTO_RISK_FUNDING_MIN_PERIODS`
- `CRYPTO_RISK_OI_MIN_PERIODS`
- `CRYPTO_RISK_MAX_DATA_AGE_SECONDS`

### 8.6 StrategyCodeView

`GET /v1/strategies/{strategy_id}/code/view` 是 Strategy Management 详情页使用的只读源码视图。

字段：

| 字段 | 含义 |
|------|------|
| `strategy_id` | 策略模板 ID |
| `source_type` | `saved_code` 或 `module_entrypoint` |
| `code` | 可展示源码内容 |
| `code_version` | saved code 版本；模块源码为 `null` |
| `checksum` | 源码 SHA256 |
| `module_path` | 模块 entrypoint 的 Python module；saved code 为 `null` |
| `entrypoint` | 注册表中的原始 entrypoint；saved code 为 `null` |
| `created_at` / `created_by` / `notes` | saved code 元数据；模块源码为 `null` |

解析规则：

- 优先返回 `Strategy Lab` 保存的最新 `StrategyCodeVersion`，`source_type=saved_code`。
- 若无 saved code，则读取注册策略的 `trader.*` module entrypoint 源码，`source_type=module_entrypoint`。
- 只允许读取本仓库 `trader` 包内 `.py` 文件；`dynamic:*` 且无 saved code 时返回 404。
- `/code/view` 必须固定在 `/code/{code_version}` 路由之前，避免 `view` 被当成版本号解析。
