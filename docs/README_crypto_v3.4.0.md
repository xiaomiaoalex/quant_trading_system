# 项目定位

**quant_trading_system Crypto v3.4.0** 是一个以 **中低频交易为首要场景**、以 **趋势 / 量价 / 资金结构 / 链上行为 / 事件驱动** 为研究主线、以 **数据深度驱动** 与 **工程确定性** 为生存底座，并为未来 A 股券商接入预留统一适配层的系统化数字资产交易平台。

`quant_trading_system` 当前版本 **不追求高频、极致低延迟**，也不模仿大型机构的高频做市体系。它首先要解决的不是“更快地下单”，而是：

- 如何在 **Crypto 市场高波动、低信噪比、24/7 连续交易** 的现实条件下活下来
- 如何用 **统一、可审计、可回放、可治理** 的基础设施管理不确定性
- 如何在 **个人开发者资源有限** 的前提下，把时间投入到真正有差异化价值的层，而不是在商品化底层反复造轮子

系统主线为：

`venue_data（行情 / 衍生品 / 链上 / 事件） -> alignment -> feature engineering -> research signal -> policy gate（时间 / 流动性感知） -> execution -> audit -> replay`

---

## v3.4.0 的版本意图

v3.4.0 不是一次“功能大跃进”，而是一次 **架构收束与工程升级**。

本次升级的目标是三件事：

1. **保留差异化骨干**：继续强化 Core / Policy / Alignment / Feature Store / Audit 这些真正构成系统护城河的层。
2. **承认商品化部件**：明确交易所基础接入、常规指标、基础监控、部分研究底座应优先复用成熟开源能力。
3. **冻结过早扩张**：对多市场、全自动 AI、复杂组合优化等“未来很酷但现在不关键”的方向，保持接口占位，不提前兑现。

一句话说：

> **v3.4.0 的目标不是让系统“看起来更大”，而是让系统更接近“最小可生存闭环 + 可持续演进”。**

### v3.4.0 新增主线：Qlib + Hermes 的“研究编排闭环”
在保留既有执行链路（StrategyRunner + RiskEngine + OMS）的前提下，v3.4.0 新增两条研究能力主线：

1. **Qlib 研究引擎接入（离线）**：用于因子挖掘、模型训练、预测导出，不直接触发下单。
2. **Hermes 研发编排接入（离线）**：用于数据准备、训练、回测、报告自动化，不进入 Core/Policy 运行时。

详细任务拆解见：
- `docs/V3.4.0_HERMES_QLIB_INTEGRATION_PLAN.md`

---

## 为什么不是直接照搬 A 股版

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
- **资金费率 / OI / 清算 / 链上行为 / 叙事事件**
- 24/7 市场状态切换

两者共享：
- 五平面基础设施
- Core 的确定性与幂等
- 审计 / 回放 / Policy / AI 边界

但不共享：
- 研究主语
- 因子体系
- 数据源主线
- 调仓节奏
- 风险模型逻辑

---

## 项目解决的真实问题

### 1. 数据可信与多样性问题
- 行情流是否可信？
- **资金费率、OI、清算数据是否与行情对齐？**
- **链上数据是否能及时接入并与市场状态融合？**
- 事件公告（上币、监管、维护）能否被结构化并进入事件流？

### 2. 工程生存问题
- 极端行情下系统会不会乱？
- 会不会重复记账、逆序回滚、误触发风险？
- **断流、限流、交易所异常时能否自动 Fail-Closed？**
- **长时间持仓如何应对流动性枯竭？**

### 3. 研究有效性问题
- 趋势和量价信号在资金结构过滤后是否仍有预测力？
- 资金费率极端时是否真的预示反转？
- 链上行为能否提供早期预警，而不是赛博玄学？
- 事件驱动是否只是事后解释？

### 4. 小资金实盘问题
- 能否在小资金条件下做出真实可执行的仓位管理？
- **能否控制非交易时间（凌晨、周末）的风险？**
- 能否管理滑点、杠杆和极端波动风险？

### 5. 系统演进问题
- 如何在 Binance 首发前提下，不把系统写死成单交易所脚本？
- 如何在未来接入 A 股或其他 venue 时复用 Core / Persistence / Policy？
- 如何让 Research / Portfolio / Execution / AI 共用统一治理框架？

---

## 核心原则

### 1. Alignment 先于决策
未对齐，不决策。

WebSocket 重连后，在未完成 REST 快照重建与对齐前：
- Private / Execution 事件严禁外发
- Public 行情可继续外发，但必须标记 `DEGRADED/ALIGNING`
- 不允许信号驱动执行
- 必要时触发本地防御动作

### 2. 状态机必须单调递增
订单、成交、仓位等核心状态必须满足：

`R(S_{t+1}) >= R(S_t)`

终态不可逆，不允许逆序回滚与重复记账。

### 3. Core 必须 AI-clean
AI 可以做摘要、解释、事件抽取、候选规则草案，但不能直接进入 Core 决策与执行路径。

### 4. 数据深度先于策略多样性
没有可信的多维数据，就没有鲁棒的信号。Crypto 版首先治理的是：
- 行情流完整性
- 衍生品 / 链上 / 事件数据接入
- 特征计算可复现性
- 研究与实盘的一致性

### 5. 机制先于回测
任何候选策略必须附带市场微观结构假设，解释“为什么可能有效”和“何时会失效”。回测只是验证，不是许愿池。

### 6. 时间与流动性风险前置
中低频持仓必须考虑：
- 时间段（凌晨 / 周末）的流动性折扣
- 当前订单簿深度对滑点的影响
- 极端行情下的逃生时间

### 7. Fail-Closed
任何不确定状态，默认：
- 不下单
- 不外发 Private / Execution 事件
- 不假设系统是对的
- 必要时本地锁死

KillSwitch 级别（Canonical，数字为准）：
- Level 0 = NORMAL（L0_NORMAL）
- Level 1 = NO_NEW_POSITIONS（L1_NO_NEW_POS / L1_NO_NEW_POSITIONS）
- Level 2 = CANCEL_ALL_AND_HALT（L2_CLOSE_ONLY，仅历史兼容）
- Level 3 = LIQUIDATE_AND_DISCONNECT（L3_FULL_STOP，仅历史兼容）

### 8. 复用商品化部件，自研差异化骨干
系统开发必须区分两类东西：

**应优先复用的商品化部件**：
- 交易所基础 REST / WS 接入
- 常规指标与数值库
- 基础监控、日志、调度
- 通用研究 / 回测底座

**应持续自研的差异化骨干**：
- Core 状态机与幂等层
- Alignment Gate
- Policy Plane
- Feature Store 与研究—实盘一致性约束
- 审计 / 回放 / Risk 收口
- AI 治理边界

原则是：

> **Buy the boring parts, build the edge.**

---

## 五平面架构

### Core Plane
负责：
- 订单状态机
- 持仓状态机
- 幂等
- 审计事件生成
- 回放所需领域事件

要求：
- 不调用外部 I/O
- 不运行 AI 推理
- 不依赖交易所 SDK 细节

### Adapter Plane
负责：
- Binance 行情接入
- Binance 订单接口
- Binance 账户接口
- 衍生品 / 链上 / 事件数据适配
- 元数据、限流、重连、对齐

要求：
- 屏蔽交易所脏数据
- 实现 Alignment Gate
- 只在 Adapter 边界使用第三方交易库
- 进入系统内部前全部转为标准领域对象

### Persistence Plane
负责：
- event_log
- orders / fills / positions / balances
- 行情快照与原始参考
- funding_rates / open_interest / liquidations / onchain_metrics
- **feature store**
- risk_events
- 研究工件与 AI 输出

要求：
- Risk 事务链路当前为 PG-First
- 支持事件溯源与回放
- Feature Store 支持版本管理

### Policy Plane
负责：
- 风险预算
- 杠杆限制
- 时间窗口暴露限制
- 流动性深度检查
- KillSwitch
- 环境异常处理
- dedup_key 幂等契约

### Insight Plane
拆为三域：

**Crypto Research Domain**
- 趋势
- 量价结构
- 资金结构
- 链上行为
- 事件驱动
- 候选规则实验

**AI / Agent Domain**
- 公告 / 新闻摘要
- Regime 识别
- 候选规则草案
- 报告解释与复盘

**Future Equity Domain**
- 未来 A 股研究域接口契约占位，不在当前版本深做

---

## 数据源策略（Crypto 版 v3.4.0）

### Layer A：交易所直连底盘（P0）
当前主源：
- Binance REST + WebSocket
- 可基于 **CCXT / CCXT Pro** 做统一封装，但业务层不得直接依赖其对象模型

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
- 所有执行与研究的基准源

### Layer B：衍生品与链上辅助层（P1）
候选来源：
- Glassnode / CryptoQuant / Coinglass
- 交易所自身可获得的 Funding / OI / Liquidation 历史

核心字段：
- Funding Rate 历史与实时
- Open Interest
- Liquidations
- Exchange Netflows
- Stablecoin Supply Ratio
- Long / Short Ratio

角色：
- 用于信号过滤、Regime 判断、风险预警
- 属于中低频主线，不是锦上添花

### Layer C：非结构化叙事与事件层（P1）
候选来源：
- Binance 官方公告
- 项目方官方公告
- 监管消息
- Crypto News RSS

角色：
- 事件抽取输入
- AI 摘要输入
- 候选事件特征来源

原则：
1. 先官方原文
2. 再监管和法定公告
3. 再媒体聚合
4. 社交情绪只作辅助，不作正式真相源

---

## 研究主线（v3.4.0）

### 1. Trend
- 趋势延续
- 突破 / 回踩
- 中期相对强弱
- 波动压缩后的趋势释放

### 2. Price-Volume Structure
- 爆量突破
- 缩量回撤
- 价量背离
- 波动扩张 / 收缩
- 深度变化与流动性真空

### 3. Capital Structure
- Funding Rate 极端值及其变化率
- OI 与价格的背离
- 清算集中度
- 多空比与基差

### 4. On-Chain Behavior
- 交易所大额流入 / 流出
- 巨鲸地址移动
- 链上活跃度变化
- 稳定币风险偏好代理变量

### 5. Event / Regime
- 上币 / 下架
- 监管消息
- 项目方重大更新
- 维护公告与规则变更
- 市场状态切换

---

## Portfolio / Positioning 逻辑

采用 **Position & Risk Constructor**，核心目标：
- 单币种风险上限
- 总暴露控制
- 时间窗口系数（凌晨 / 周末降低仓位）
- 流动性深度检查（下单前估算滑点）
- 同方向集中度控制
- 事件前后仓位调整
- 极端行情自动降级

当前建议：
- 先从单标的 / 少量标的开始
- 规则型仓位优先
- 先证明最小闭环可以生存，再讨论复杂组合优化

---

## 当前开发边界

### 当前范围内（v3.4.0）
- Binance 首发
- Spot / USDT 永续优先
- 统一适配器抽象
- 趋势 / 量价 / 资金结构 / 链上 / 事件主线
- Feature Store 与版本管理
- PostgreSQL 事件溯源
- Core 状态机
- Risk 事务链路 PG-First
- 时间窗口与深度感知风控
- Reconciler 最小版本
- 监控与告警最小闭环

### 明确复用，不重复造轮子的部分
- 交易所基础 REST / WS 接入能力
- 常规技术指标库
- 基础监控、日志、调度
- 部分研究 / 回测底座

### Next / Target
- Qlib 数据转换与离线训练流水线
- Qlib 预测到 Strategy Signal 的桥接层
- Hermes 研究编排（数据→训练→评估→报告）SOP
- AI 策略五层门控与影子模式联动
- 逃生时间模拟
- PG 投影读模型替代当前内存读模型
- AI proposal / approve 治理接口
- Runner 自动执行主链路
- 多市场适配扩展

### 当前明确不做或延期
- 高频做市
- 毫秒级盘口策略
- 多交易所套利
- 复杂跨市场对冲
- 多账户分布式执行
- 全自动 AI 自主交易
- 完整 A 股实盘链路

---

## v3.4.0 的一句话总结

**这不是一个“什么都自己写”的交易系统，也不是一个“把开源项目拼起来就算完成”的机器人。它的目标是：复用商品化底座，自研确定性骨干，先做出一个能在真实 Crypto 市场里活下来的最小可生存闭环。**
