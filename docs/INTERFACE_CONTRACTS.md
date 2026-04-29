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
- `signal_type` 是策略信号字段；不得新增 `direction` 表示同一含义。
- `qty` 是内部数量字段；API legacy 的 `quantity` 必须在边界转换。
- Core Plane 不允许引入外部字段名或原始交易所 payload。
