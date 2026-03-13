
## 1. 项目名称

**quant_trading_system — Crypto v3.1.1**

---

## 2. 项目使命

在个人开发者资源约束下，构建一个以 **Binance 为首发交易场所**、以 **趋势 / 量价 / 衍生品资金结构 / 事件驱动** 为核心研究对象、以 **工程确定性与 Fail-Closed 生存能力** 为首要原则的系统化数字资产交易平台。

该项目不是“自动下单脚本”的工程化包装，也不是模仿大型做市或高频机构的“Quant Cosplay”。  
它的使命是：

- 在加密市场高波动、低信噪比、24/7 连续交易的条件下
- 用统一、可审计、可回放、可治理的基础设施
- 先构建一个**能活下来的交易系统**
- 再逐步演进为一个**有研究纪律、有状态识别能力、有策略扩展能力**的平台

同时，系统在架构上保留未来接入 **中国 A 股券商接口** 的能力，但不在当前版本里提前实现所有 A 股逻辑。

---

## 3. 版本定位

`v3.1.1` 是面向数字货币市场的一次**Crypto-First 架构落地版本**，并补充 Current/Next/Target 能力分层。

它吸收了 `v4.3.0` 的几个关键思想：

1. **数据源与可信度前置**
2. **失败机制前置**
3. **五平面隔离**
4. **AI 必须受治理**
5. **小资金必须优先考虑生存，不追求机构外形**
6. **架构尽量统一，市场逻辑必须分离**

和 A 股版本最大的区别在于：

- A 股版首先要解决 **PIT / 财报 / 基本面可得性**
- Crypto 版首先要解决 **WS 静默断流 / REST 与 WS 不一致 / 插针脏数据 / 交易所状态与账户状态不一致**

也就是说，Crypto 版的底层核心不是“财报时间线”，而是：

> **交易所数据流、订单状态、账户状态、市场事件流，是否足够可信到能支撑自动化决策。**

---

## 4. 项目背景

加密货币交易市场与 A 股、传统期货或股票量化市场不同，具有以下结构性特征：

### 4.1 7x24 连续交易
- 无开盘、无收盘
- 无天然日内结算边界
- 周末和凌晨同样可能发生极端波动
- 风险暴露没有“休市缓冲带”

### 4.2 弱基本面、强叙事
- 大多数交易标的不具备成熟的财务报表体系
- 价值锚点弱
- 价格受叙事、流动性、情绪、资金结构和监管预期影响极大

### 4.3 强趋势、强量价、强衍生品结构影响
- 趋势延续、量价背离、波动压缩与扩张有更强可交易性
- 资金费率、未平仓合约、清算、Basis 等衍生品数据常常携带更强信息密度
- 市场状态切换快，趋势与回撤都更剧烈

### 4.4 数据极脏、执行极脆弱
- WebSocket 静默断流
- REST 与 WS 同步延迟
- 交易所接口限流与错误码风暴
- 插针与局部流动性真空
- 订单状态与本地状态不一致

### 4.5 小资金的优势与约束并存
- 优势：冲击成本低，反应灵活
- 约束：容错率低，无法依赖复杂团队和大规模基础设施兜底

因此，Crypto 版不能套用“季度基本面 + 月频多因子组合”的主线，而必须承认：

> **数字货币版系统的主语，应是“高脏数据环境下的状态识别、量价研究、事件驱动与确定性执行”。**

---

## 5. 项目解决的真实问题

本项目不是要解决“如何在 Binance 更快地下单”，而是要解决以下几类真实问题：

### 5.1 数据可信问题
- Binance WS 是否静默断流
- REST 快照与 WS 增量是否对齐
- K 线、成交、深度、资金费率等是否完整
- 上币 / 下架 / 风险公告是否被及时纳入事件流
- 极端脏数据是否会污染信号

### 5.2 工程确定性问题
- 本地订单状态是否可单调推进
- 是否会出现重复记账、逆序状态回滚、重复成交认领
- 是否能在交易所异常时 Fail-Closed
- 是否可回放、可审计、可恢复

### 5.3 研究有效性问题
- 信号是否只是伪相关的量价模式
- 资金费率 / OI / 清算等衍生变量是否真正有增量信息
- 事件驱动是否只是事后解释
- AI 生成的候选规则是否只是噪声

### 5.4 风险生存问题
- 在插针、暴涨暴跌、断流和交易所异常下是否还能活下来
- 是否具备断连、未对齐、账户异常时的本地锁死能力
- 是否能控制单币种、单方向、单策略的风险暴露

### 5.5 系统演进问题
- 如何在 Binance 首发的前提下，不把系统写死成单交易所脚本
- 如何复用基础设施到未来 A 股券商接入
- 如何让 Research / Portfolio / Execution / AI 共用统一治理框架

---

## 6. 项目范围

## 6.1 当前范围内

### 市场与交易场所
- 数字货币市场
- 首发接入 Binance

### 产品形态
建议分阶段推进：
- **Phase 1：现货 Spot**
- **Phase 2：USDT 永续合约**
- **Phase 3：更复杂的衍生场景（如有必要）**

### 策略主线
当前正式研究主线为：
- 趋势（Trend）
- 量价结构（Price-Volume Structure）
- 事件驱动（Event / Regime）
- 衍生品资金结构（Funding / OI / Liquidations）作为增强层

### 执行形态
- 单交易所
- 单账户
- 小资金
- 规则型仓位控制
- 最小审计与回放能力

### 架构预留
- 预留中国 A 股券商 Adapter 抽象
- 预留多市场统一基础设施接口

---

## 6.2 当前范围外

当前明确不在范围内的内容包括：

- 高频做市
- 毫秒级盘口策略
- 多交易所套利
- 复杂跨市场对冲
- 多账户分布式执行
- 复杂期权策略
- 全链数据量化体系
- 完整 A 股实盘链路

---

## 6.3 能力状态矩阵（Current / Next / Target）

Current 判定标准（必须同时满足）：
- 代码存在
- 至少 1 个非跳过测试可证明核心行为
- 运行前提明确（例如 PostgreSQL 未配置时的 skip 说明）

| 能力 | 状态 | 证据（代码） | 证据（测试） |
|---|---|---|---|
| Deterministic CAS（订单/成交单调与幂等） | Current | `trader/core/application/deterministic_layer.py` | `trader/tests/test_deterministic_layer.py` |
| Private 流 ALIGNING 期阻断执行相关事件 | Current | `trader/adapters/binance/private_stream.py` | `trader/tests/test_binance_private_stream.py` |
| 风险事件 PG-First 持久化链路 | Current | `trader/adapters/persistence/risk_repository.py` | `trader/tests/test_risk_idempotency_persistence.py` |
| `/v1/events` `/v1/snapshots/latest` | Current（内存读模型） | `trader/services/event.py` | `trader/tests/test_api_endpoints.py` |
| Reconciler 持续对账服务 | Next | 当前无服务/路由实现 | N/A |
| AI proposal/approve 治理接口 | Target | 当前无 `/v1/ai/proposals*` | N/A |
| Runner 自动执行主链路 | Target | 当前无 runtime 主链路 | N/A |

---

## 7. 核心设计原则

### 7.1 数据源与数据流可信性先于策略
在 Crypto 市场中，系统首要问题不是“有没有策略”，而是：

- 行情流是否可信
- 深度流是否连续
- 订单状态是否可信
- 账户状态是否与交易所一致

没有这些，所有策略回测和实盘信号都可能建立在脏输入上。

---

### 7.2 Alignment Gate 先于事件外发
这是 Crypto 版的关键制度之一。

当出现以下任一情况时：

- WS 重连
- REST 补快照
- 序列号跳变
- 订单薄状态不连续
- 账户/仓位状态可能失真

系统必须先进入 **Alignment Gate**，在未重新对齐快照前：

- 不外发 Private/Execution 事件
- Public 行情事件可继续外发，但必须附带 `DEGRADED/ALIGNING` 状态标签
- 不允许信号驱动执行
- 必要时触发本地防御动作

这意味着：

> **未对齐，不决策；未确认，不执行。**

---

### 7.3 状态机必须单调递增，终态不可逆
这是工程正确性的底线。

对于订单、成交、仓位等核心对象，必须保证状态 Rank 满足：

\[
R(S_{t+1}) \ge R(S_t)
\]

例如：
- Pending < Accepted < PartiallyFilled < Filled / Canceled / Rejected

要求：
- 终态不可逆
- 不允许状态回滚
- 不允许重复记账
- 不允许相同成交被重复认领

---

### 7.4 AI 必须 AI-Clean 地接入
沿用 `v4.3.0` 的纪律：

- **Core Plane 必须 AI-clean**
- AI 只能在 Insight / Research / Report 层活动
- AI 可以输出 `AIInsightEvent`
- AI 不能直接穿透到 Core 执行下单

---

### 7.5 小资金优先“活下来”
当前版本不追求复杂资产配置，而追求：

- 仓位边界
- 风险上限
- 成本透明
- 执行稳定
- 极端行情生存

因此，Portfolio / Position 层必须优先是：

- 规则型
- 硬边界
- 低复杂度
- 可解释

而不是：
- 复杂优化器
- 协方差求逆
- 花哨的最优解

---

### 7.6 多市场适配靠抽象，不靠提前实现
当前不实现 A 股交易逻辑，但必须在接口层保证：

- 不把 Binance 写死到业务层
- 订单、账户、行情、符号元数据都通过抽象接口暴露
- 未来中国券商接口可以复用 Core / Persistence / Policy / Audit 基础设施

---

## 8. 数据源策略（Crypto 版）

这部分吸收了 `v4.3.0` 的“数据源前置”思想，并按 Crypto 场景重写。

---

## 8.1 Layer A：高频确定性底盘（交易所直连层）

### 当前来源
- Binance 直连（REST + WebSocket）
- 可基于 CCXT / CCXT Pro 做统一封装，但业务层不得直接依赖具体 SDK 细节

### 核心字段
- Kline / OHLCV
- Trades / AggTrades
- Orderbook / Depth
- Ticker / Mark Price
- Funding Rate
- Symbol Metadata
- Exchange Status
- Account / Balance / Position / Open Orders

### 角色
- 系统正式主源
- 执行与研究共用的真相源
- 仅在 Runner/Execution 主链路上线并通过 Gate 后，才作为唯一执行驱动源；当前阶段不直接自动下单

### 关键风险
- WS 假死
- 增量深度丢包
- REST/WS 不一致
- 429 限流风暴
- 交易所状态异常

### 系统要求
- 必须建立 `Alignment Gate`
- 必须有重连单飞控制
- 必须有快照重建机制
- 必须有延迟与异常状态检测
- Reconciler 为 Next 能力，当前阶段由 REST 对齐与风控降级兜底

---

## 8.2 Layer B：链上与衍生品辅助校验层

### 候选来源
- Glassnode
- CryptoQuant
- Coinglass
- 其他可审计的链上 / 衍生品聚合源

### 核心字段
- 交易所净流入 / 流出
- 稳定币供应变化
- 全局 OI
- 多空比
- 资金费率结构
- 爆仓分布
- 基差与期限结构

### 角色
- 中低频市场“水位线”
- Regime 判断增强层
- 趋势与事件解释层

### 约束
这些数据不应在当前版本成为唯一执行驱动源，而应作为：
- 研究增强
- 状态判断
- 风险过滤
- 事件解释

---

## 8.3 Layer C：非结构化叙事与事件层

### 候选来源
- Binance 官方公告
- 交易所公告 / 上下架公告
- 项目官方 Blog / X / Discord 公告（后续可选）
- Crypto News RSS
- 监管机构公告
- 宏观事件源

### 角色
- 事件抽取输入
- AI 摘要输入
- 候选事件特征来源
- 市场 Regime 识别增强层

### 原则
优先级应为：
1. 交易所和项目官方原文
2. 监管和法定公告
3. 媒体聚合和 RSS
4. 社交情绪（仅作辅助，不作正式底层真相源）

---

## 8.4 Crypto 数据源哲学总结
A 股版的核心问题是：
- PIT / 财报 / 可得时间

Crypto 版的核心问题是：
- 对齐 / 连续性 / 实时真相 / 事件及时性

因此，Crypto 版的 Data Source Strategy 一句话总结是：

> **不是先问“字段多不多”，而是先问“这条实时数据流在极端行情下还能不能被信任”。**

---

## 9. 技术架构

项目继续采用五平面架构。

---

## 9.1 Core Plane（确定性大脑）

### 职责
- 订单状态机
- 持仓状态机
- 风险状态
- 决策输入的确定性处理
- 幂等
- 回放

### 约束
- 不允许直接访问外部 I/O
- 不允许直接调用 AI 推理
- 所有状态转换必须单调
- 终态不可逆

### 目标
确保在任意异常行情下，核心状态仍然可解释、可恢复、可审计。

---

## 9.2 Adapter Plane（感知与执行 I/O）

### 当前必须实现
- `BinanceMarketDataAdapter`
- `BinanceOrderAdapter`
- `BinanceAccountAdapter`
- `BinanceMetadataAdapter`

### 必须预留
- `VenueAdapter`
- `BrokerAdapter`
- `MarketDataPort` 占位接口（跨市场）
- 当前阶段不要求落地 `ChinaBrokerAdapter` 类实体

### 关键职责
- 吸收交易所脏数据
- 统一标准化输入
- WS / REST 对齐
- 限流控制
- 重连逻辑
- 错误分类与退避

---

## 9.3 Persistence Plane（持久化记忆）

### 存储对象
- event_log
- market snapshots
- symbol metadata
- orders
- fills
- positions
- risk events
- strategy signals
- experiment artifacts
- AI outputs

### 设计要求
- Risk 事务链路 PG-First；控制面读链路当前仍以内存投影为主，目标迁移到 PG 投影
- 不再以 SQLite 作为长期正式方案
- 事件溯源优先
- 可按时间或 symbol 分区
- 支持灾后快速重建

---

## 9.4 Policy Plane（法律与治理）

### 职责
- 风险预算
- 交易权限
- 策略启停
- 杠杆限制
- 单币种暴露限制
- 环境异常触发本地锁死
- KillSwitch

### 关键规则
- dedup_key 幂等契约
- Risk Event 收口
- 环境异常可触发 L1 KillSwitch
- 断流 / 未对齐 / 异常状态时自动降级或停机

### Canonical KillSwitch Level Map（数字为准）
- Level 0 = NORMAL（兼容别名：L0_NORMAL）
- Level 1 = NO_NEW_POSITIONS（兼容别名：L1_NO_NEW_POS / L1_NO_NEW_POSITIONS）
- Level 2 = CANCEL_ALL_AND_HALT（兼容别名：L2_CLOSE_ONLY，仅历史兼容）
- Level 3 = LIQUIDATE_AND_DISCONNECT（兼容别名：L3_FULL_STOP，仅历史兼容）

---

## 9.5 Insight Plane（洞察大脑）

拆成三域：

### A. Crypto Research Domain
负责：
- 趋势
- 量价结构
- 波动 regime
- 资金结构
- 事件驱动
- 候选规则实验

### B. AI / Agent Domain
负责：
- 事件摘要
- 规则候选生成
- 市场状态说明
- 报告总结
- 风险与异常解释

### C. Future Equity Domain（接口契约占位）
负责未来：
- A 股研究主线
- 券商接入后的研究对象适配

---

## 10. 研究主线（Crypto 专属）

相对于 A 股版的 Value / Quality / Momentum，Crypto 版主线必须重写。

---

## 10.1 Trend（趋势主线）
研究对象包括：
- 趋势延续
- 均线结构
- 突破 / 回踩
- 波动收缩后的趋势释放
- Cross-sectional momentum

适用原因：
- Crypto 市场趋势更明显
- 连续交易更容易形成 regime persistence

---

## 10.2 Price-Volume Structure（量价结构主线）
研究对象包括：
- 爆量突破
- 缩量回撤
- 价量背离
- 波动压缩与扩张
- 深度变化
- 插针与流动性真空识别

适用原因：
- 数字货币市场大量信号来自交易结构，而不是财报

---

## 10.3 Event / Regime（事件与状态切换主线）
研究对象包括：
- 上币 / 下架
- 监管新闻
- 项目方重大事件
- Binance 公告
- Funding / OI 极端
- 大额清算
- 市场风险偏好切换

适用原因：
- Crypto 市场叙事驱动极强
- regime 切换快，且影响可交易性

---

## 10.4 Funding / OI / Liquidation（增强层）
这层不是当前必须独立成主线，但强烈建议尽快接入。

用途：
- 强化趋势判断
- 辅助识别 squeeze
- 区分“真突破”和“强行挤仓”
- 提高市场状态识别能力

---

## 11. 候选策略与因子沙盒（Crypto 版）

Crypto 版仍需要统一的 Sandbox，只是评价维度不同于 A 股版。

### 必须检查
- 数据完整性
- 规则可执行性
- 未来函数
- 成本后有效性
- Turnover 合理性
- Regime 稳健性
- 风险暴露边界

### AI 候选规则也必须经过沙盒
AI 可以生成：
- 规则草案
- 参数候选
- 事件过滤条件
- 风险解释

但必须通过：
- 编译检查
- 时间检查
- 样本内/样本外观察
- 成本检查
- 风险检查

### 系统要求
Sandbox 的目标不是接纳更多候选，而是：
- 拒绝大多数噪声规则
- 防止事件后验解释污染研究主线
- 确保策略在极端行情下仍有最小生存性

---

## 12. 仓位与风险控制（Crypto 版）

A 股版强调 `Portfolio Constructor`，Crypto 版当前更适合定义为：

## Position & Risk Constructor

其核心任务不是“求最优组合”，而是：

- 控制单标的风险
- 控制总暴露
- 控制方向性集中
- 控制杠杆
- 控制极端行情损失
- 控制环境异常时的自动降级

### 当前建议
- 优先单标的 / 少量标的轮动
- 再逐步扩展到多标的
- 仓位规则简单透明
- 先做规则型仓位控制，不做复杂优化

### 当前必须具备的边界
- 单币种最大仓位
- 单方向最大总风险
- 最大杠杆
- 单日最大亏损
- 环境异常减仓 / 停机机制

---

## 13. 长期架构价值

这版 Crypto-First 架构的真正价值不只是“先能接 Binance”，而在于：

### 14.1 能复用到未来 A 股
未来接 A 股时，可以直接复用：
- Core Plane
- Adapter 抽象
- Persistence
- Policy
- Audit / Replay
- AI 边界治理

### 14.2 研究主线可独立扩展
- Crypto 用趋势 / 量价 / 事件
- A 股用基本面 / 财务质量 / 文本慢变量
- 两者共享 Sandbox / Experiment / Report 纪律

### 14.3 Fail-Closed 让系统有反脆弱性
在币圈最致命的不是“赚得不够快”，而是：
- 断流时继续交易
- 状态错乱
- 杠杆失控
- 极端行情中系统自相矛盾

所以这个版本的真正竞争优势是：

> **在脏数据、高波动、连续交易环境下，系统仍然知道什么时候不该动。**

---

## 14. 一句话总结

`quant_trading_system Crypto v3.1.1` 是一个以 Binance 为首发落地场景、以五平面隔离和事件溯源为基础设施、以趋势/量价/事件驱动为研究主线、以 Fail-Closed 和状态机确定性为生存底座，并通过 Current/Next/Target 分层治理能力边界的系统化数字资产交易平台。
