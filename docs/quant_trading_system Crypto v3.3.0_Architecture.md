## 1. 文档目的

本文档定义 `quant_trading_system Crypto v3.3.0` 的总体技术架构、模块边界、运行时职责划分、外部依赖准入规则、市场适配原则与关键非功能性约束。

本版本相较于 v3.2.1 的核心变化不是“多加几个模块”，而是：

- 明确 **哪些层属于系统护城河，必须自研**
- 明确 **哪些层属于商品化基础设施，应优先复用**
- 明确 **最小可生存闭环** 的边界
- 明确 **Current / Next / Target / Deferred** 的能力收口

一句话定义：

> 本系统采用五平面隔离架构，以 Core 的确定性、Adapter 的脏数据吸收与外部复用、Persistence 的事件溯源与 Feature Store、Policy 的时间 / 流动性感知治理，以及 Insight 的机制驱动研究，支撑 **Crypto-first, medium/low-frequency, data-depth-driven** 的交易平台。

---

## 2. 架构总原则

### 2.1 统一基础设施，分离市场逻辑
必须复用：
- Core Plane
- Persistence Plane
- Policy Plane
- 审计 / 回放
- AI 接入边界

必须按市场拆分：
- Crypto Research Domain
- Equity Research Domain（未来）

### 2.2 Core 必须 AI-clean、IO-clean
Core 不允许：
- 调用外部 I/O
- 调用 LLM / Agent
- 依赖交易所 SDK
- 依赖数据库查询结果做隐式状态修复

### 2.3 Adapter 负责吸收脏数据并隔离第三方依赖
交易所接口、网络异常、限流、断流、序列错位、符号规则变化都必须在 Adapter Plane 被吸收和标准化。

第三方库只能出现在 Adapter / Infrastructure 边界，不能污染 Core / Policy / Insight 的领域模型。

### 2.4 Persistence 以事件溯源和特征存储为双核心
所有关键交易对象必须可以通过 event log 重建。所有用于研究和信号的特征必须存储在 Feature Store 中，并支持版本管理，确保回测与实盘一致。

### 2.5 Policy 是法律，不是建议
风险规则不是“提醒一下”，而是强约束。违反即阻断、降级、锁死或人工介入。

### 2.6 Insight 是研究与洞察层，不是越权控制层
Insight 可以提出：
- signal
- candidate rule
- ai insight
- regime judgment

Insight 不可以直接：
- 下单
- 修改 Core 状态
- 绕过风险闸门

### 2.7 复用商品化部件，自研差异化骨干
系统必须明确区分：

**商品化部件**：
- REST / WS 基础接入
- 指标库
- 调度、日志、监控
- 基础研究 / 回测底座

**差异化骨干**：
- Core 状态机
- Alignment Gate
- Policy Plane
- Feature Store 版本纪律
- 审计 / 回放 / Reconciler / Risk 收口
- AI 治理边界

---

## 3. 五平面总览

Core Plane  
Adapter Plane  
Persistence Plane  
Policy Plane  
Insight Plane

### 3.1 能力分层（Current / Next / Target / Deferred）

定义：
- **Current**：代码存在 + 核心测试覆盖 + 运行前提明示
- **Next**：已经被纳入当前主闭环路线图，且缺失它会明显限制系统生存能力
- **Target**：未来重要能力，但当前最小闭环不依赖
- **Deferred**：明确延期，不在当前版本兑现

| 能力 | 状态 | 说明 |
|---|---|---|
| 订单/成交单调状态与幂等 CAS | Current | 已有核心确定性骨架 |
| Private 流 ALIGNING 期间阻断执行相关事件 | Current | Alignment Gate 已进入系统主纪律 |
| 风险事件链路 PG-First | Current | 已形成关键持久化收口 |
| Funding / OI 数据接入 | Current | 已进入中低频核心数据面 |
| Feature Store 基础（版本化） | Current | 已形成研究—实盘一致性的基础约束 |
| 时间窗口风控 | Current | 已进入 Policy 平面 |
| 下单前深度检查 | Current | 已进入 Policy 平面 |
| `/v1/events` 与 `/v1/snapshots/latest` 读模型 | Current | 当前以内存读模型运行 |
| Reconciler 最小版本 | Current | 已升级为主闭环必备能力，定时对账已接入 |
| 策略实时监控与告警 | Current | 主闭环生存能力组成部分 |
| PG 投影读模型 | Current | 已完成 Position/Order/Risk Projector |
| 逃生时间模拟 | Current | EscapeTimeSimulator 已实现 |
| HITL AI治理接口 | Current | HITLGovernance 已实现 |
| **StrategyRunner 策略执行器** | **Current** | **策略生命周期控制、异常隔离、资源限制** |
| **StrategyEvaluator 策略评估器** | **Current** | **回测引擎、实时评估、指标计算** |
| **StrategyHotSwapper 热插拔** | **Current** | **策略热切换、状态机、回滚保护** |
| **AIStrategyGenerator AI策略生成** | **Current** | **多LLM后端、代码安全验证、审计日志** |
| **StrategyChatInterface AI聊天界面** | **Current** | **自然语言策略开发、HITL集成** |
| **StrategyLifecycleManager 生命周期管理** | **Current** | **完整策略生命周期闭环** |
| AI proposal / approve 治理 API | Current | 已通过HITL Governance实现 |
| Runner 主执行链路（自动执行） | Current | StrategyRunner已实现 |
| 中国 A 股实盘适配器 | Deferred | 当前只保留接口契约 |
| 多交易所套利 / 高频做市 | Deferred | 明确不纳入当前版本 |

---

## 4. Core Plane

### 4.1 职责

Core Plane 负责所有必须保持确定性、可验证、可回放的核心领域逻辑，包括：
- 订单状态机
- 持仓状态机
- 成交归并
- 风险状态推进
- 指令幂等处理
- 领域事件生成

### 4.2 设计要求
- 状态转换必须单调递增
- 终态不可逆
- 同一 fill 不可重复认领
- 不允许读外部网络
- 不允许直接访问交易所 SDK
- 不允许调用 AI

### 4.3 核心模块建议
- `core/order_state_machine.py`
- `core/position_state_machine.py`
- `core/fill_allocator.py`
- `core/domain_events.py`
- `core/idempotency_guard.py`

### 4.4 不可让渡的边界
以下能力即便外部框架有类似实现，也不应直接让外部框架支配：
- 订单真相模型
- 持仓真相模型
- 风险推进逻辑
- 幂等与重放约束

这是系统的内核，不是可随便替换的插件。

---

## 5. Adapter Plane

### 5.1 职责
Adapter Plane 负责与外部世界交互，并把异构、脏、非确定性输入转换成系统内部标准事件。

**当前必须接入的数据源**：
- Binance 行情（Kline / Trades / Depth）
- Binance 账户与订单
- Binance 元数据
- Binance 资金费率 / OI / Liquidations
- 链上 / 宏观聚合数据
- 事件公告与新闻流

### 5.2 当前 Adapter 划分
- `BinanceMarketDataAdapter`
- `BinanceOrderAdapter`
- `BinanceAccountAdapter`
- `BinanceMetadataAdapter`
- `BinanceFundingOIAdapter`
- `MacroDataAdapter`（Coinglass / CryptoQuant / Glassnode 等）
- `EventAnnouncementAdapter`

### 5.3 未来预留 Adapter（接口契约占位）
- `VenueAdapter`
- `BrokerAdapter`
- `MarketDataPort`
- `ExecutionPort`

当前要求是接口稳定，不要求落地 `ChinaBrokerAdapter`。

### 5.4 关键约束
- Alignment Gate 按流域生效：Private / Execution 事件阻断；Public 行情流可继续，但必须带降级状态标签
- 网络异常必须可观测
- 限流与重试策略可配置
- 所有外部响应都必须标准化，不可直接传入 Core
- 非行情数据也需经过质量检查，但可接受一定延迟

### 5.5 开源复用策略
本层允许且鼓励复用外部成熟能力，但方式必须是 **外包裹，而不是内嵌式继承**。

建议：
- 可基于 `CCXT / CCXT Pro` 封装交易所能力
- WebSocket、HTTP、重试、限流使用成熟网络库
- 第三方对象进入系统前必须转成内部 DTO / 领域事件

禁止：
- 在业务层直接传播外部 SDK 类型
- 让第三方框架定义系统内部订单 / 持仓 / 风险语义

---

## 6. Persistence Plane

### 6.1 职责
Persistence Plane 负责存储所有可审计、可重建、可追溯的数据资产。

### 6.2 四类核心存储

#### A. Transaction Store
存：
- orders
- fills
- positions
- balances
- risk_events
- execution_decisions

#### B. Feature Store
存：
- 特征名称
- 版本
- 计算时间
- symbol
- 值
- 上游来源引用

#### C. Raw Data Store
存：
- market snapshots
- funding_rates
- open_interest
- liquidations
- onchain_metrics
- event_announcements

#### D. Artifact / Audit Store
存：
- ai outputs
- run configs
- reports
- replay artifacts
- raw payload references

### 6.3 当前正式底座
- Risk 事务链路：PostgreSQL First（不可达时回退内存）
- 事件 / 快照控制面读模型：当前以内存投影为主，目标迁移到 PG 投影读模型
- Feature Store：初期可用 PostgreSQL + 版本字段

### 6.4 设计要求
- 关键事件优先事件溯源
- 支持按时间与 symbol 检索
- 支持 replay
- 特征存储必须版本化，任何特征逻辑修改必须创建新版本
- 特征值应能回溯到源事件或源快照

---

## 7. Policy Plane

### 7.1 职责
Policy Plane 负责系统级法律与治理，包括：
- 风险预算
- 交易权限
- 杠杆限制
- 单币种暴露限制
- 时间窗口暴露限制
- 流动性深度检查
- 逃生时间模拟（Next）
- 环境异常熔断
- KillSwitch
- 运行模式控制
- AI 准入边界

### 7.2 当前策略重点
- Alignment Gate 期间禁止交易驱动
- 单日最大亏损限制
- 单币种最大暴露限制
- 凌晨 / 周末时段仓位系数下调
- 下单前检查订单簿深度，估算滑点，超阈值则拒绝
- 总杠杆上限
- API 异常时本地锁死
- KillSwitch 单调升级，不自动降级

### 7.3 关键模块建议
- `policy/risk_gate.py`
- `policy/killswitch.py`
- `policy/runtime_mode.py`
- `policy/exposure_limits.py`
- `policy/time_window_limits.py`
- `policy/liquidity_depth_check.py`
- `policy/escape_time_simulator.py`（Next）
- `policy/ai_policy_guard.py`

### 7.4 Canonical KillSwitch Level Map（数字为准）
| Level | Canonical Name | 兼容别名 |
|---|---|---|
| 0 | NORMAL | L0_NORMAL |
| 1 | NO_NEW_POSITIONS | L1_NO_NEW_POS / L1_NO_NEW_POSITIONS |
| 2 | CANCEL_ALL_AND_HALT | L2_CLOSE_ONLY（废弃别名，仅兼容） |
| 3 | LIQUIDATE_AND_DISCONNECT | L3_FULL_STOP（废弃别名，仅兼容） |

---

## 8. Insight Plane

Insight Plane 是研究、信号、实验、洞察与 AI 增强层。

### A. Crypto Research Domain
负责：
- 趋势信号
- 量价结构
- 资金结构因子
- 链上行为因子
- 事件模型
- regime 识别
- 策略实验

**强制要求**：所有信号必须从 Feature Store 读取特征，避免研究与实盘各写各的，变成平行宇宙。

### B. AI / Agent Domain
负责：
- 事件摘要
- regime 解读
- 候选规则草案
- 研究日志整理
- 报告总结

### C. Future Equity Domain
未来用于：
- A 股研究主线
- 券商接入后的研究与信号

### 8.1 AI 边界纪律
AI 可以：
- 总结
- 提案
- 解释
- 排序候选研究方向

AI 不可以：
- 直接下单
- 直接修改 Core 状态
- 绕过 Policy Gate

---

## 9. Strategy Management Plane（策略管理层）

策略管理层负责策略的全生命周期管理、热插拔、评估与AI共创。

### 9.1 职责
- 策略注册与版本管理
- 策略执行器（StrategyRunner）生命周期控制
- 策略评估与回测
- 策略热插拔与版本切换
- AI辅助策略生成与审批

### 9.2 核心组件

#### A. StrategyPlugin 协议
所有可执行策略必须实现统一协议：
```python
class StrategyPlugin(Protocol):
    @property
    def plugin_id(self) -> str: ...
    @property
    def version(self) -> str: ...
    @property
    def risk_level(self) -> Literal["LOW", "MEDIUM", "HIGH"]: ...
    
    async def initialize(self, config: Dict[str, Any]) -> None: ...
    async def on_tick(self, market_data: MarketData) -> Optional[Signal]: ...
    async def on_fill(self, fill_data: Dict[str, Any]) -> None: ...
    async def on_cancel(self, cancel_data: Dict[str, Any]) -> None: ...
    async def shutdown(self) -> None: ...
```

#### B. StrategyRunner（策略执行器）
- 动态加载策略代码
- 生命周期控制：start/stop/pause/resume
- 每个策略运行在独立 asyncio.Task 中
- 异常隔离：单策略崩溃不影响其他策略
- 资源限制：内存、并发订单数、订单频率、超时控制

#### C. StrategyEvaluator（策略评估器）
- BacktestEngine：历史数据回测
- LiveEvaluator：实时指标计算
- 指标：PnL、夏普率、最大回撤、胜率、盈亏比
- 数据质量验证

#### D. StrategyHotSwapper（热插拔管理器）
- 状态机：IDLE → LOADING → VALIDATING → PREPARING → SWITCHING → ACTIVE
- 切换模式：IMMEDIATE / GRADUAL / WAIT_ORDERS
- 挂单处理：切换前自动取消未结订单
- 持仓迁移：映射持仓到新策略
- 异常回滚：切换失败自动回滚到旧策略

#### E. AIStrategyGenerator（AI策略生成器）
- 多LLM后端支持（OpenAI/Anthropic/本地模型）
- 代码安全验证（AST分析、危险模式检测）
- 审计日志：输入需求、生成代码、验证结果、审批状态

#### F. StrategyChatInterface（AI聊天界面）
- 自然语言策略需求描述
- 意图识别：GENERATE_STRATEGY / MODIFY_PARAMS / CHECK_STATUS
- 与HITL Governance集成：AI生成策略 → 提交审批 → Trader确认 → 注册部署

### 9.3 策略生命周期状态机
```
DRAFT → VALIDATED → BACKTESTED → APPROVED → RUNNING → STOPPED
   │         │           │           │          │
   └─────────┴───────────┴───────────┴──────────┴──→ FAILED
                                                      │
                                                      └──→ ARCHIVED
```

### 9.4 与现有系统对接
```
StrategyRunner
     │
     ├──▶ OMS.submit_order()  ──▶ Broker Adapter
     │
     ├──▶ Reconciler.monitor()  ──▶ 定期对账
     │
     ├──▶ KillSwitch.check()  ──▶ 风险熔断
     │
     └──▶ FeatureStore.read()  ──▶ 特征数据
```

### 9.5 关键约束
- AI生成的策略必须经过HITL审批才能部署
- 策略崩溃不影响系统稳定性（异常隔离）
- 热插拔切换时挂单必须正确处理
- 代码安全验证必须拦截危险操作（os/subprocess/eval/exec/网络调用）

---

## 10. 关键运行流

### 10.1 市场数据流
`Binance WS/REST + Derivatives + On-chain + Event Crawler`
-> Adapter Normalization
-> Alignment Gate
-> Standardized Events
-> Feature Store（特征计算 / 特征快照）
-> Persistence
-> Insight / Signal Engine

### 10.2 交易决策流
`Research Signal / Position Intent`
-> Policy Gate（时间窗口、深度检查、风险预算）
-> Target Position
-> Order Intent