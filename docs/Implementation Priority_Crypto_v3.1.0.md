## 1. 文档目的

本文档用于定义 `quant_trading_system Crypto v3.1.1` 的工程实现优先级，回答一个最关键的问题：

> 现在开始写代码时，先写什么，后写什么，哪些必须先落地，哪些可以明确延后。

这份文档不替代 Sprint 路线图，而是把路线图进一步压缩成"工程开工优先级"。

**状态标注说明：**
- **Current**：代码存在 + 有非跳过测试 + 运行前提明确
- **Next**：下一阶段待实现，需要前置条件满足
- **Target**：未来目标，需要更多前置条件

---

## 2. 总体原则

### 2.1 先保证"系统不瞎"，再保证"系统能动"
在 Crypto 场景下，优先级不是：
- 先让它会下单

而是：
- 先让它知道什么时候不能信
- 再让它知道什么时候不能动
- 最后才让它去动

### 2.2 先处理运行时真相，再做研究增强
必须优先做：
- 市场数据与对齐
- 账户与订单状态
- 事件溯源
- 风险与锁死

后做：
- 复杂信号
- AI 洞察
- 多源增强

### 2.3 先做"最小闭环"，后做"漂亮扩展"
任何模块都必须问自己：
- 它是否直接支撑最小闭环
- 如果没有它，系统是否还能形成可运行最小版本

若答案是"能"，则它优先级下降。

### 2.4 先做"可审计"，再做"可聪明"
在 Crypto 市场里，错误的自动化比笨一点的自动化危险得多。
因此：
- Replay
- Event Log
- Reconciler
- Risk Event Router

都优先于"更聪明的 Alpha"。

---

## 3. 当前实现分层

本项目的工程优先级按五层推进：

- **P0：必须先做（立刻开工）**
- **P1：第一阶段必须完成（主闭环核心）**
- **P2：第二阶段必须完成（研究闭环核心）**
- **P3：第三阶段增强（闭环完成后再上）**
- **P4：当前明确可延后**

---

## 4. P0：必须先做（立刻开工）

## 4.1 项目骨架与目录结构
### 原因
没有清晰目录和模块边界，后续 Binance 逻辑极易污染 Core。

### 当前状态：Current
| 能力 | 状态 | 代码路径 | 测试路径 |
|---|---|---|---|
| 五平面目录分层 | Current | `trader/core/`, `trader/adapters/`, `trader/services/`, `trader/storage/`, `trader/api/` | `trader/tests/test_architecture.py` |
| 配置加载 | Current | `trader/adapters/binance/connector.py` | `trader/tests/test_binance_connector.py` |
| 基础日志与错误类型 | Current | 各模块 | `trader/tests/test_hard_properties.py` |

### 目标
保证后续任何模块都知道自己属于：
- Core
- Adapter
- Persistence
- Policy
- Insight

---

## 4.2 Binance Metadata Adapter
### 原因
没有 symbol 规则就无法安全下单，也无法构建 tradable universe。

### 当前状态：Current
| 能力 | 状态 | 代码路径 | 测试路径 |
|---|---|---|---|
| symbol metadata 拉取 | Current | `trader/adapters/binance/connector.py` | `trader/tests/test_binance_connector.py` |
| tick size / step size / min notional | Current | `trader/adapters/binance/connector.py` | `trader/tests/test_binance_connector.py` |
| metadata 缓存与刷新 | Current | `trader/adapters/binance/connector.py` | `trader/tests/test_binance_connector.py` |

### 输出价值
- 所有后续模块都可复用
- 立刻减少非法下单风险
- 为订单合法性检查、最小交易金额过滤和研究样本过滤提供底座

---

## 4.3 Binance Market Data Adapter
### 原因
没有可信市场数据，后续所有研究和执行都没有意义。

### 当前状态：Current
| 能力 | 状态 | 代码路径 | 测试路径 |
|---|---|---|---|
| Kline 数据流 | Current | `trader/adapters/binance/public_stream.py` | `trader/tests/test_binance_stream_base.py` |
| Trades / AggTrades | Current | `trader/adapters/binance/public_stream.py` | `trader/tests/test_binance_stream_base.py` |
| Depth 数据流 | Current | `trader/adapters/binance/public_stream.py` | `trader/tests/test_binance_stream_base.py` |
| 统一标准化结构 | Current | `trader/adapters/binance/stream_base.py` | `trader/tests/test_binance_stream_base.py` |
| 基础落盘能力 | Current | `trader/storage/in_memory.py` | `trader/tests/test_api_endpoints.py` |

### 最低标准
- 能统一转换成系统内部事件
- 能记录 `event_time` 与 `local_receive_time`
- 能附带 `raw_reference`

---

## 4.4 WS Health Monitor + Alignment Gate
### 原因
这是 Crypto 版最关键的生存组件之一。

### 当前状态：Current
| 能力 | 状态 | 代码路径 | 测试路径 |
|---|---|---|---|
| WS 假死检测 | Current | `trader/adapters/binance/connector.py` | `trader/tests/test_binance_connector.py` |
| 重连逻辑 | Current | `trader/adapters/binance/connector.py` | `trader/tests/test_binance_connector.py` |
| REST 快照恢复 | Current | `trader/adapters/binance/rest_alignment.py` | `trader/tests/test_binance_rest_alignment.py` |
| Private 流 ALIGNING 阻断 | Current | `trader/adapters/binance/private_stream.py` (line 394) | `trader/tests/test_binance_private_stream.py` |
| Public 流 DEGRADED 标签 | Current | `trader/adapters/binance/public_stream.py` (line 233) | `trader/tests/test_binance_stream_base.py` |
| reconcile_grace_period_ms 配置 | Current | `trader/adapters/binance/rest_alignment.py` | `trader/tests/test_binance_rest_alignment.py` |

### 说明
**Alignment Gate 按流域生效**：
- Private/Execution 事件在 Gate 期间阻断
- Public 行情流可继续但必须打 DEGRADED 标签
- 现状证据：Private 在 ALIGNING 丢弃消息，Public 仍继续外发

---

## 4.5 Event Log Schema（PostgreSQL）
### 原因
没有事件溯源，就没有审计、回放和对账基础。

### 当前状态：Current
| 能力 | 状态 | 代码路径 | 测试路径 |
|---|---|---|---|
| 风险事件 PG-First 持久化 | Current | `trader/adapters/persistence/risk_repository.py` | `trader/tests/test_risk_idempotency_persistence.py` |
| 事件幂等去重 | Current | `trader/adapters/persistence/risk_repository.py` | `trader/tests/test_risk_repository.py` |
| 内存事件存储 | Current | `trader/storage/in_memory.py` | `trader/tests/test_api_endpoints.py` |

### 现状说明
- **Risk 事务链路**：PostgreSQL First（不可达时回退内存）
- **事件/快照读模型**：当前仍以内存投影为主，目标迁移到 PostgreSQL 投影读模型

### 最低要求
- 每个关键事件都能被唯一标识
- 能支持 replay
- 能按 symbol / time 检索

---

## 5. P1：第一阶段必须完成（主闭环核心）

## 5.1 Binance Account / Order Adapter
### 原因
只有市场数据不够，必须有账户与订单的标准化接入，才能形成最小执行闭环。

### 当前状态：Current
| 能力 | 状态 | 代码路径 | 测试路径 |
|---|---|---|---|
| account snapshot | Current | `trader/adapters/binance/private_stream.py` | `trader/tests/test_binance_private_stream.py` |
| balances | Current | `trader/adapters/binance/private_stream.py` | `trader/tests/test_binance_private_stream.py` |
| open orders | Current | `trader/adapters/binance/private_stream.py` | `trader/tests/test_binance_private_stream.py` |
| place order | Current | `trader/api/routes/orders.py` | `trader/tests/test_api_services.py` |
| cancel order | Current | `trader/api/routes/orders.py` | `trader/tests/test_api_services.py` |
| query order | Current | `trader/api/routes/orders.py` | `trader/tests/test_api_services.py` |
| response normalization | Current | `trader/adapters/binance/private_stream.py` | `trader/tests/test_binance_private_stream.py` |

---

## 5.2 Core Order State Machine
### 原因
这是系统正确性的核心。

### 当前状态：Current
| 能力 | 状态 | 代码路径 | 测试路径 |
|---|---|---|---|
| 状态 Rank 定义 | Current | `trader/core/application/deterministic_layer.py` | `trader/tests/test_deterministic_layer.py` |
| 单调递增约束 | Current | `trader/core/application/deterministic_layer.py` | `trader/tests/test_deterministic_layer.py` |
| 终态不可逆 | Current | `trader/core/application/deterministic_layer.py` | `trader/tests/test_deterministic_layer.py` |
| 重复事件幂等处理 | Current | `trader/core/application/deterministic_layer.py` | `trader/tests/test_deterministic_layer.py` |

### 最低输出
- `Pending -> Accepted -> PartiallyFilled -> Filled`
- `Pending -> Rejected`
- `Accepted -> Canceled`

### 不允许
- 从终态回退
- 同一 fill 重复记账
- 因重连导致状态覆盖回滚

---

## 5.3 Position State
### 原因
研究输出和执行结果必须通过统一仓位对象连接。

### 当前状态：Current
| 能力 | 状态 | 代码路径 | 测试路径 |
|---|---|---|---|
| 持仓状态对象 | Current | `trader/core/domain/models/position.py` | `trader/tests/test_domain_events.py` |
| 持仓更新规则 | Current | `trader/core/domain/models/position.py` | `trader/tests/test_domain_events.py` |
| 方向、成本价、数量、可用性字段 | Current | `trader/core/domain/models/position.py` | `trader/tests/test_domain_events.py` |

---

## 5.4 Risk Event Router + KillSwitch
### 原因
没有正式风险收口，系统一旦异常只能靠人工和日志。

### 当前状态：Current
| 能力 | 状态 | 代码路径 | 测试路径 |
|---|---|---|---|
| RiskEvent 标准结构 | Current | `trader/core/domain/models/events.py` | `trader/tests/test_domain_events.py` |
| 风险事件收口 | Current | `trader/services/risk.py` | `trader/tests/test_risk_engine_layers.py` |
| KillSwitch L1 | Current | `trader/services/killswitch.py` | `trader/tests/test_api_endpoints.py` |
| 禁开仓 / 本地锁死能力 | Current | `trader/services/killswitch.py` | `trader/tests/test_api_endpoints.py` |

### 当前关键触发条件
- Alignment Gate 长时间未恢复
- WS 断流 / REST 异常持续
- 对账出现 fatal divergence（Reconciler 实现后）
- 单日亏损超限
- 重复下单 / 状态机异常

### KillSwitch Canonical Level Map
| 级别 | 名称 | 行为 |
|---|---|---|
| L1 | PAUSED | 禁止新订单，允许现有持仓平仓 |
| L2 | HALTED | 禁止所有交易，仅响应查询 |
| L3 | LOCKDOWN | 禁止所有操作，需人工介入 |

---

## 5.5 Reconciler（最小版本）
### 原因
本地状态一旦和交易所漂移，系统会越来越危险。

### 当前状态：Next
| 能力 | 状态 | 前置条件 | 目标 Sprint |
|---|---|---|---|
| Reconciler 持续对账服务 | Next | Core 状态机 + Event Log 完善 | Sprint 5-6 |

### 必须完成（实现后）
- open orders 对账
- balances 对账
- positions 对账
- grace window
- drift 分级

### 目标
不是自动修复一切，而是：
- 先发现
- 再分级
- 再交给 Policy 决定动作

---

## 6. P2：第二阶段必须完成（研究闭环核心）

## 6.1 Trend Base Signals
### 原因
这是最符合当前 Crypto 主线的第一批正式研究资产。

### 当前状态：Next
| 能力 | 状态 | 前置条件 |
|---|---|---|
| moving average state | Next | Public Stream + Storage |
| breakout | Next | Public Stream + Storage |
| momentum state | Next | Public Stream + Storage |
| relative strength | Next | Public Stream + Storage |

---

## 6.2 Price-Volume Base Signals
### 原因
Crypto 的信号密度更多体现在量价结构，不在财报。

### 当前状态：Next
| 能力 | 状态 | 前置条件 |
|---|---|---|
| volume expansion | Next | Public Stream + Storage |
| volatility compression / expansion | Next | Public Stream + Storage |
| price-volume divergence | Next | Public Stream + Storage |
| abnormal range detection | Next | Public Stream + Storage |

---

## 6.3 Event Model
### 原因
Crypto 事件不是"可选增强"，而是主线之一。

### 当前状态：Current
| 能力 | 状态 | 代码路径 | 测试路径 |
|---|---|---|---|
| 核心事件结构 | Current | `trader/core/domain/models/events.py` | `trader/tests/test_domain_events.py` |

### 必须完成（扩展）
- ListingEvent
- DelistingEvent
- MaintenanceEvent
- SymbolRuleChangeEvent
- AlignmentRiskEvent
- ReconcileDivergenceEvent

### 后续扩展
- FundingShockEvent
- LiquidationSpikeEvent
- RegulatoryShockEvent

---

## 6.4 Signal / Strategy Sandbox（最小版本）
### 原因
没有沙盒，研究层很快会被伪规则污染。

### 当前状态：Next
| 能力 | 状态 | 前置条件 |
|---|---|---|
| 规则可运行性检查 | Next | Event Model 完善 |
| 未来函数检查 | Next | Event Model 完善 |
| 候选规则状态机 | Next | Event Model 完善 |

---

## 7. P3：第三阶段增强（闭环完成后再上）

## 7.1 Position & Risk Constructor
### 作用
把研究信号转成仓位规则。

### 当前状态：Next
| 能力 | 状态 | 前置条件 |
|---|---|---|
| 单币种最大暴露 | Next | 研究信号层 + 风险引擎 |
| 总暴露控制 | Next | 研究信号层 + 风险引擎 |
| 冷却期 | Next | 研究信号层 + 风险引擎 |
| 最小交易阈值 | Next | 研究信号层 + 风险引擎 |
| regime 风险折扣 | Next | 研究信号层 + 风险引擎 |

---

## 7.2 Replay Runner
### 作用
形成真正可复盘闭环。

### 当前状态：Target
| 能力 | 状态 | 前置条件 |
|---|---|---|
| 基于 event log 重建状态 | Target | Event Log + 状态机 + PG |
| 订单/持仓/风险事件回放 | Target | Event Log + 状态机 + PG |

---

## 7.3 Unified Reporting
### 作用
把 Data / Risk / Research / Execution 串起来。

### 当前状态：Next
| 能力 | 状态 | 前置条件 |
|---|---|---|
| 统一报告生成 | Next | Event Log + 状态机 |

---

## 7.4 AI Insight Copilot
### 作用
提供：
- 公告摘要
- Regime 解读
- 复盘总结
- 候选规则草案

### 当前状态：Target
| 能力 | 状态 | 前置条件 |
|---|---|---|
| AI Insight API | Target | 审计数据 + 事件模型 + 报告管道 |

---

## 8. P4：当前明确可延后的内容

以下内容当前可以明确不做，避免稀释带宽。

## 8.1 Futures 深度化
- funding 全量主线
- OI / Liquidations 全量主线
- 合约杠杆复杂策略

## 8.2 多交易所
- 多 Venue Router
- 跨所套利
- 多所状态协调

## 8.3 高级 AI
- 多 Agent 协作
- 自动参数调整
- 自动策略启停

## 8.4 A 股交易实现
- 券商订单通道
- A 股日历
- 基本面主线
- PIT 财报治理

**当前只保留接口抽象（BrokerPort / MarketDataPort），不落地实现。**

---

## 9. 当前最推荐的工程开工顺序

### 第 1 组：基础骨架（Current）
1. 仓库骨架 / 五平面目录
2. metadata adapter
3. market data adapter
4. event log schema

### 第 2 组：输入可信（Current）
5. ws health monitor
6. alignment gate
7. account adapter
8. order adapter

### 第 3 组：状态正确（Current）
9. order state machine
10. position state
11. risk event router
12. killswitch

### 第 4 组：真相校验（Next）
13. reconciler
14. drift grading
15. grace window config

### 第 5 组：研究起步（Next）
16. trend signals
17. price-volume signals
18. event model
19. strategy sandbox

### 第 6 组：执行闭环（Next/Target）
20. position & risk constructor
21. target-to-orders
22. unified reporting
23. replay runner
24. ai insight copilot

---

## 10. 当前最小可运行闭环定义

当前版本最小闭环不应该定义为"能下单"，而应该定义为：


Binance data
-> health check
-> alignment gate (按流域生效)
-> standardized events
-> event log
-> core state machine
-> risk gate
-> minimal signal (Next)
-> target position (Next)
-> order intent
-> reconcile (Next)
-> report (Next)

只要这个链路成立，系统就具备继续扩张的资格。

---

## 11. 开发时的禁止事项

当前阶段明确禁止：

- 先写复杂策略，后补状态机
- 先写 AI 自动化，后补风控
- 先做多市场扩展，后补 Binance 主链路
- 先做华丽面板，后补事件溯源
- 把 Binance SDK 调用直接写进 Core

---

## 12. 一句话总结

Crypto v3.1.1 的实现优先级核心，不是先让系统"做更多事"，而是先让系统"在最危险的地方不出错"；只有先把数据、状态、对账和风控做对，后面的策略和 AI 才值得接进去。
