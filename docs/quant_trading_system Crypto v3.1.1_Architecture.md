  
## 1. 文档目的  
  
本文档定义 `quant_trading_system Crypto v3.1.1` 的总体技术架构、模块边界、运行时职责划分、市场适配原则与关键非功能性约束。  
  
本架构面向以下现实：  
  
- 首发场景为 Binance 数字货币交易  
- 市场为 7x24、强波动、强事件驱动、数据流高噪声环境  
- 个人开发者资源有限，必须优先保证确定性、可回放与生存能力  
- 后续需要预留中国 A 股券商接口，不允许将 Binance 逻辑写死在系统核心  
  
一句话定义：  
  
> 本系统采用五平面隔离架构，以 Core 的确定性、Adapter 的抗脏数据能力、Persistence 的事件溯源、Policy 的 Fail-Closed 治理，以及 Insight 的研究与 AI 增强为核心，支撑 Crypto-first、Multi-market-ready 的交易平台。  
  
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
  
### 2.2 Core 必须 AI-clean  
Core 不允许：  
- 调用外部 I/O  
- 调用 LLM / Agent  
- 依赖交易所 SDK  
- 依赖具体数据库查询结果做隐式状态修复  
  
### 2.3 Adapter 负责吸收脏数据  
交易所接口、网络异常、限流、断流、序列错位、符号规则变化都必须在 Adapter Plane 被吸收和标准化，不能污染 Core。  
  
### 2.4 Persistence 以事件溯源为优先  
所有关键交易对象必须可以通过 event log 重建。  
  
### 2.5 Policy 是法律，不是建议  
风险规则不是日志提示，而是强约束。违反即阻断、降级、锁死或人工介入。  
  
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
  
---  
  
## 3. 五平面总览  
  
```text  
Core Plane  
Adapter Plane  
Persistence Plane  
Policy Plane  
Insight Plane
```

## 3.1 能力分层（Current / Next / Target）

Current 判定标准（必须同时满足）：
- 代码存在（可定位到仓库路径）
- 有非跳过测试覆盖核心行为
- 运行前提被明示（例如 PostgreSQL 未配置时的降级/skip 条件）

| 能力 | 状态 | 代码证据 | 测试证据 |
|---|---|---|---|
| 订单/成交单调状态与幂等 CAS | Current | `trader/core/application/deterministic_layer.py` | `trader/tests/test_deterministic_layer.py` |
| Private 流 ALIGNING 期间阻断执行相关事件 | Current | `trader/adapters/binance/private_stream.py` | `trader/tests/test_binance_private_stream.py` |
| 风险事件链路 PG-First（失败回退内存） | Current | `trader/adapters/persistence/risk_repository.py` | `trader/tests/test_risk_idempotency_persistence.py` |
| `/v1/events` 与 `/v1/snapshots/latest` 读模型 | Current | `trader/services/event.py`（当前内存读模型） | `trader/tests/test_api_endpoints.py` |
| Reconciler 持续对账服务 | Next | 当前无服务/路由实现 | N/A |
| AI proposal/approve 治理 API | Target | 当前无 `/v1/ai/proposals*` 路由 | N/A |
| Runner 主执行链路（自动执行） | Target | 当前无 runtime 主链路 | N/A |

---

## 4. Core Plane

## 4.1 职责

Core Plane 负责所有必须保持确定性、可验证、可回放的核心领域逻辑，包括：

- 订单状态机
    
- 持仓状态机
    
- 成交归并
    
- 风险状态推进
    
- 指令幂等处理
    
- 领域事件生成
    

## 4.2 设计要求

- 状态转换必须单调递增
    
- 终态不可逆
    
- 同一 fill 不可重复认领
    
- 不允许读外部网络
    
- 不允许直接访问交易所 SDK
    
- 不允许调用 AI
    

## 4.3 核心模块建议

- `core/order_state_machine.py`
    
- `core/position_state_machine.py`
    
- `core/fill_allocator.py`
    
- `core/domain_events.py`
    
- `core/idempotency_guard.py`
    

---

## 5. Adapter Plane

## 5.1 职责

Adapter Plane 负责与外部世界交互，并把异构、脏、非确定性输入转换成系统内部标准事件。

当前主要包括：

- Binance 行情接入
    
- Binance 账户接入
    
- Binance 下单与撤单
    
- 符号元数据同步
    
- 限流与重连
    
- REST / WS 对齐
    
- 错误分类与退避
    

## 5.2 当前 Adapter 划分

- `BinanceMarketDataAdapter`
    
- `BinanceOrderAdapter`
    
- `BinanceAccountAdapter`
    
- `BinanceMetadataAdapter`
    

## 5.3 未来预留 Adapter（接口契约占位）

- `VenueAdapter`（抽象契约）
    
- `BrokerAdapter`（抽象契约）
    
- `MarketDataPort`（抽象契约，跨市场）
    
- 当前要求是接口稳定，不要求落地 `ChinaBrokerAdapter` / `EquityMarketDataAdapter` 类实体
    

## 5.4 关键约束

- Alignment Gate 按流域生效：Private/Execution 事件阻断；Public 行情流可继续，但必须带降级状态标签
    
- 网络异常必须可观测
    
- 限流与重试策略可配置
    
- 所有外部响应都必须标准化，不可直接传入 Core
    

---

## 6. Persistence Plane

## 6.1 职责

Persistence Plane 负责存储所有可审计、可重建、可追溯的数据资产。

## 6.2 三类核心存储

### A. Transaction Store

存：

- orders
    
- fills
    
- positions
    
- balances
    
- risk_events
    
- execution_decisions
    

### B. Research Store

存：

- market snapshots
    
- signals
    
- feature values
    
- regime labels
    
- backtest results
    
- experiment outputs
    

### C. Artifact / Audit Store

存：

- ai outputs
    
- run configs
    
- reports
    
- replay artifacts
    
- raw event payload references
    

## 6.3 当前正式底座

- Risk 事务链路：PostgreSQL First（不可达时回退内存）
    
- 事件/快照控制面读模型：当前仍以内存投影为主，目标迁移到 PostgreSQL 投影读模型
    

## 6.4 设计要求

- 关键事件优先事件溯源
    
- 支持按时间与 symbol 检索
    
- 支持 replay
    
- 不依赖“最终状态表”作为唯一真相源
    

---

## 7. Policy Plane

## 7.1 职责

Policy Plane 负责系统级法律与治理，包括：

- 风险预算
    
- 交易权限
    
- 杠杆限制
    
- 单币种暴露限制
    
- 环境异常熔断
    
- KillSwitch
    
- 运行模式控制
    
- AI 准入边界
    

## 7.2 当前策略重点

- Alignment Gate 期间禁止交易驱动
    
- 单日最大亏损限制
    
- 单币种最大暴露限制
    
- 总杠杆上限
    
- API 异常时本地锁死

- KillSwitch 单调升级，不自动降级（Fail-Closed）
    

## 7.3 关键模块建议

- `policy/risk_gate.py`
    
- `policy/killswitch.py`
    
- `policy/runtime_mode.py`
    
- `policy/exposure_limits.py`
    
- `policy/ai_policy_guard.py`

## 7.4 Canonical KillSwitch Level Map（数字为准，别名兼容）

| Level | Canonical Name | 兼容别名（历史文档） |
|---|---|---|
| 0 | NORMAL | L0_NORMAL |
| 1 | NO_NEW_POSITIONS | L1_NO_NEW_POS / L1_NO_NEW_POSITIONS |
| 2 | CANCEL_ALL_AND_HALT | L2_CLOSE_ONLY（废弃别名，仅兼容） |
| 3 | LIQUIDATE_AND_DISCONNECT | L3_FULL_STOP（废弃别名，仅兼容） |
    

---

## 8. Insight Plane

Insight Plane 是研究、信号、实验、洞察与 AI 增强层。

拆分为三域：

### A. Crypto Research Domain

负责：

- 趋势信号
    
- 量价结构
    
- 事件模型
    
- regime 识别
    
- funding / oi / liquidation 增强研究
    
- 策略实验
    

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
    

---

## 9. 关键运行流

## 9.1 市场数据流

Binance WS/REST  
-> Adapter normalization  
-> Alignment Gate  
-> Standard Market Events  
-> Persistence  
-> Insight / Signal Engine

## 9.2 交易决策流

Research Signal / Position Intent  
-> Policy Gate  
-> Target Position  
-> Order Intent  
-> Adapter Execution  
-> Exchange Ack/Fills  
-> Core State Update  
-> Persistence  
-> Reconcile

## 9.3 AI 洞察流

Raw Text / Events / COP Snapshot  
-> AI Task  
-> Structured AIInsightEvent  
-> Persistence / Reporting / Human Review

AIInsightEvent 默认不直接驱动订单。

---

## 10. Alignment Gate

这是 Crypto v3.1.1 的关键架构组件之一。

## 10.1 作用

当出现以下任一情况时：

- WS 重连
    
- 序列错位
    
- 快照重建
    
- 订阅恢复
    
- 明显数据漂移
    

系统进入 Alignment Gate。

## 10.2 Gate 期间行为

- Private/Execution 事件停止外发，避免未对齐状态进入执行链
    
- Public 行情流允许继续外发，但必须打 `DEGRADED/ALIGNING` 元标签
    
- 允许内部对齐与状态重建
    
- 视风险策略决定是否降低仓位或停机
    

## 10.3 Gate 退出条件

- 快照已完成
    
- 增量事件已连续
    
- 序列号一致
    
- grace period 内无继续异常
    

---

## 11. Reconciler（Next）

## 11.1 作用

Reconciler 为下一阶段能力，用于持续核对：

- 本地订单状态 vs 交易所订单状态
    
- 本地持仓 vs 交易所持仓
    
- 本地余额 vs 交易所余额
    

## 11.2 目的

- 识别合理漂移
    
- 识别异常漂移
    
- 识别致命分歧
    
- 为 Policy Plane 提供证据
    

## 11.3 结果分级

- `EXPECTED_DRIFT`
    
- `UNEXPECTED_DRIFT`
    
- `FATAL_DIVERGENCE`
    

---

## 12. 市场适配策略

## 12.1 当前首发市场

- Binance
    

## 12.2 未来扩展市场

- 中国 A 股券商接口
    
- 其他 Crypto Venue
    

## 12.3 抽象原则

市场差异应该收敛在：

- Adapter Plane
    
- Research Domain
    
- 部分 Policy Rules
    

不应侵入：

- Core Plane
    
- Persistence 基础模型
    
- Audit / Replay 体系
    

---

## 13. 模块边界清单

### Core 不能依赖

- Binance SDK
    
- HTTP client
    
- WebSocket client
    
- LLM / Agent
    
- 非确定性外部状态
    

### Adapter 不能做

- 最终交易决策
    
- 状态机真相裁决
    
- 风险规则越权
    

### Insight 不能做

- 直接下单
    
- 直接修改 Core 状态
    
- 直接跳过 Policy Gate
    

### Policy 不能做

- 替代 Core 修复业务状态
    
- 在无证据情况下“猜测式修复”
    

---

## 14. 一句话总结

Crypto v3.1.1 的架构核心，不是把 Binance 接进来，而是建立一个在脏数据、强波动、断流和极端事件环境下，仍然能保持输入可信、状态确定、风险可锁死、研究可扩展的统一交易基础设施；并且对能力边界采用 Current/Next/Target 分层治理。
