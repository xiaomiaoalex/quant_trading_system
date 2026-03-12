

# 项目定位

quant_trading_system** Crypto v3.1.1 是一个以 Binance 为首发场景、以趋势 / 量价 / 事件驱动为研究主线、以状态机确定性和 Fail-Closed 为生存底座、并为未来 A 股券商接入预留统一适配层的系统化数字资产交易平台。

`quant_trading_system` 当前版本不是一个“币安下单脚本”，也不是一个模仿大型机构外形的高频系统。

它的目标是：

- 在 **Crypto 市场高波动、低信噪比、24/7 连续交易** 的现实条件下
- 构建一个 **数据可信、状态可审计、风控可锁死、研究可扩展** 的系统
- 先服务 **Binance 数字货币交易**
- 再在架构上平滑扩展到 **A 股券商接入**

系统主线为：

venue_data -> alignment -> state machine -> research signal -> risk gate -> execution -> audit -> replay

  

  

  

为什么不是直接照搬 A 股版

  

  

A 股版的核心问题是：

  

- PIT
- 财报发布时间
- 基本面可得性
- 财务质量与文本慢变量

  

  

Crypto 版的核心问题是：

  

- WS 静默断流
- REST 与 WS 状态不一致
- 插针与脏数据
- 交易所异常
- 资金费率 / OI / 清算 / 事件流
- 24/7 市场状态切换

  

  

所以两者共享：

  

- 五平面基础设施
- Core 的确定性与幂等
- 审计 / 回放 / Policy / AI 边界

  

  

但不共享：

  

- 研究主语
- 因子体系
- 数据源主线
- 调仓节奏
- 风险模型逻辑

  

  

  

  

  

项目解决的真实问题

  

  

  

1. 数据与状态可信问题

  

  

- Binance 的 WS 会不会静默断流？
- REST 快照和 WS 增量是否真的一致？
- 本地订单 / 仓位 / 账户状态是否和交易所一致？

  

  

  

2. 策略有效性问题

  

  

- 趋势和量价信号是否只是噪声？
- Funding / OI / Liquidations 是否真正有增量信息？
- 事件驱动是不是只是事后解释？

  

  

  

3. 工程生存问题

  

  

- 极端行情下系统会不会乱？
- 会不会重复记账、逆序回滚、误触发风险？
- 断流、限流、交易所异常时能不能自动 Fail-Closed？

  

  

  

4. 小资金实盘问题

  

  

- 能否在小资金条件下做出真实可执行的仓位管理？
- 能否控制手续费、滑点、杠杆和极端波动风险？

  

  

  

  

  

核心原则

  

  

  

1. Alignment 先于决策

  

  

未对齐，不决策。

WebSocket 重连后，在未完成 REST 快照重建与对齐前，Private/Execution 事件严禁外发；Public 行情可继续外发但必须标记 `DEGRADED/ALIGNING` 状态。

  

  

2. 状态机必须单调递增

  

  

订单、成交、仓位等核心状态必须满足：

  

[

R(S_{t+1}) \ge R(S_t)

]

  

终态不可逆，不允许逆序回滚与重复记账。

  

  

3. Core 必须 AI-clean

  

  

AI 可以做摘要、解释、事件抽取、候选规则草案，但不能直接进入 Core 决策与执行路径。

  

  

4. 数据源先于策略

  

  

没有可信的数据流，就没有正式研究。

Crypto 版首先治理的是：

  

- 行情流完整性
- 账户状态一致性
- 事件源可信度

  

  

  

5. 小资金优先“活下来”

  

  

不做机构式复杂优化器。

先做规则型仓位控制、暴露边界、杠杆约束、风险锁死和异常熔断。

  

  

6. Fail-Closed

  

  

任何不确定状态，默认：

  

- 不下单
- 不外发 Private/Execution 事件
- 不假设系统是对的
- 必要时本地锁死

KillSwitch 级别（Canonical，数字为准）：
- Level 0 = NORMAL（兼容别名：L0_NORMAL）
- Level 1 = NO_NEW_POSITIONS（兼容别名：L1_NO_NEW_POS / L1_NO_NEW_POSITIONS）
- Level 2 = CANCEL_ALL_AND_HALT（兼容别名：L2_CLOSE_ONLY，仅历史兼容）
- Level 3 = LIQUIDATE_AND_DISCONNECT（兼容别名：L3_FULL_STOP，仅历史兼容）

  

  

  

  

  

五平面架构

  

  

  

Core Plane

  

  

负责：

  

- 订单状态机
- 持仓状态机
- 幂等
- 审计
- 回放

  

  

要求：

  

- 不调用外部 I/O
- 不运行 AI 推理
- 不依赖交易所 SDK 细节

  

  

  

Adapter Plane

  

  

负责：

  

- Binance 行情接入
- Binance 订单接口
- Binance 账户接口
- 元数据、限流、重连、对齐

  

  

要求：

  

- 屏蔽交易所脏数据
- 实现 Alignment Gate
- 预留 A 股券商适配器抽象

  

  

  

Persistence Plane

  

  

负责：

  

- event_log
- orders / fills / positions
- 行情快照
- 风险事件
- 研究工件
- AI 输出

  

  

要求：

  

- Risk 事务链路当前为 PG-First；控制面事件查询当前仍以内存读模型为主（目标迁移到 PG 投影）
- 支持事件溯源与回放

  

  

  

Policy Plane

  

  

负责：

  

- 风险预算
- 杠杆限制
- KillSwitch
- 环境异常处理
- dedup_key 幂等契约

  

  

  

Insight Plane

  

  

拆为三域：

  

  

Crypto Research Domain

  

  

- 趋势
- 量价结构
- 事件驱动
- Funding / OI / Liquidations 增强研究

  

  

  

AI / Agent Domain

  

  

- 公告/新闻摘要
- Regime 识别
- 候选规则草案
- 报告解释与复盘

  

  

  

Future Equity Domain

  

  

- 未来 A 股研究域接口契约占位，不在当前版本深做

  

  

  

  

  

数据源策略（Crypto 版）

  

  

  

Layer A：交易所直连底盘

  

  

当前主源：

  

- Binance REST + WebSocket

  

  

核心字段：

  

- OHLCV
- Trades / AggTrades
- Depth / Orderbook
- Mark Price
- Funding Rate
- Symbol Metadata
- Account / Position / Orders

  

  

角色：

  

- 正式真相源
- 仅在 Runner/Execution 主链路上线并通过 Gate 后，才作为唯一执行驱动层

  

  

  

Layer B：链上与衍生品辅助层

  

  

候选来源：

  

- Glassnode
- CryptoQuant
- Coinglass

  

  

用途：

  

- 中低频 Regime 判断
- 市场水位与结构增强
- 趋势与事件解释

  

  

  

Layer C：非结构化叙事与事件层

  

  

候选来源：

  

- Binance 官方公告
- 项目方官方公告
- 监管消息
- Crypto News RSS

  

  

用途：

  

- 事件摘要
- 叙事标签
- AI Insight 输入

  

  

  

  

  

研究主线

  

  

  

1. Trend

  

  

- 趋势延续
- 突破 / 回踩
- 中期相对强弱
- 波动压缩后的趋势释放

  

  

  

2. Price-Volume Structure

  

  

- 爆量突破
- 缩量回撤
- 价量背离
- 波动扩张 / 收缩
- 深度变化与流动性真空

  

  

  

3. Event / Regime

  

  

- 上币 / 下架
- 监管消息
- 项目方重大更新
- Funding 极端
- OI / 清算异常
- 市场状态切换

  

  

  

4. 衍生品结构增强（逐步接入）

  

  

- Funding
- Open Interest
- Liquidations
- Basis

  

  

  

  

  

Portfolio / Positioning 逻辑

  

  

当前版本不做复杂组合优化，采用：

  

  

Position & Risk Constructor

  

  

其核心目标是：

  

- 单币种风险上限
- 总暴露控制
- 杠杆限制
- 同方向集中度控制
- 事件前后仓位调整
- 极端行情自动降级

  

  

当前建议：

  

- 先从单标的 / 少量标的开始
- 规则型仓位优先
- 再逐步扩展到多标的轮动

  

  

  

  

  

当前开发边界

  

  

  

当前范围内

  

  

- Binance 首发
- Spot 优先
- 统一适配器抽象
- 趋势 / 量价 / 事件主线
- PostgreSQL 事件溯源
- Core 状态机
- Risk 事务链路 PG-First（含回退）
- `/v1/events` 与 `/v1/snapshots/latest` 当前为内存读模型

Next / Target

- Reconciler（Next）
- AI 观测与提案治理接口（Target）
- Runner 自动执行主链路（Target）

Capability Matrix（简版）

| 能力 | 状态 | 证据 |
|---|---|---|
| Deterministic CAS | Current | `trader/core/application/deterministic_layer.py` + `trader/tests/test_deterministic_layer.py` |
| Private ALIGNING 阻断执行相关事件 | Current | `trader/adapters/binance/private_stream.py` + `trader/tests/test_binance_private_stream.py` |
| Risk PG-First 持久化 | Current | `trader/adapters/persistence/risk_repository.py` + `trader/tests/test_risk_idempotency_persistence.py` |
| Reconciler 服务 | Next | 当前无服务/路由实现 |
| AI proposal/approve API | Target | 当前无 `/v1/ai/proposals*` |

Public API 声明（当前口径）

- `/v1/events`：Current，内存读模型（目标切换 PG 投影读模型）
- `/v1/snapshots/latest`：Current，内存读模型（目标切换 PG 投影读模型）
- AI proposal / approve：Target，未落地前不得作为 Current 能力承诺

  

  

  

当前范围外

  

  

- 高频做市
- 多交易所套利
- 多账户分布式执行
- 复杂期权策略
- 完整 A 股交易实现
- 全链量化体系
