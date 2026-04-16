## 1. 文档目的

本文档用于定义 `quant_trading_system Crypto v3.4.0` 的工程实现优先级，回答一个最关键的问题：

> 现在开始继续写代码时，先写什么，后写什么，哪些必须先落地，哪些可以明确延后，哪些应直接复用开源能力而不是自己重写。

这份文档不替代 Sprint 路线图，而是把路线图进一步压缩成 **工程开工优先级 + 复用策略**。

> v3.4.0 新增：Qlib + Hermes 接入按独立落地顺序推进，详见  
> `docs/V3.4.0_HERMES_QLIB_INTEGRATION_PLAN.md`。  
> 本文档继续作为“基础设施与工程纪律优先级”的总纲，不替代新增的集成计划。

---

## 2. 总体原则

### 2.1 先保证“系统不瞎”，再保证“系统能动”
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
- 对账与监控

后做：
- 复杂信号
- AI 洞察
- 多市场增强

### 2.3 先做“最小闭环”，后做“漂亮扩展”
任何模块都必须问自己：
- 它是否直接支撑最小可生存闭环？
- 如果没有它，系统是否还能形成可运行最小版本？

若答案是“能”，则它优先级下降。

### 2.4 先做“可审计”，再做“可聪明”
在 Crypto 市场里，错误的自动化比笨一点的自动化危险得多。
因此：
- Replay
- Event Log
- Reconciler
- Risk Event Router
- Strategy Monitor

都优先于“更聪明的 Alpha”。

### 2.5 中低频策略要求“数据深度”先于“信号多样性”
对于中低频，单一维度的信号很容易失效。必须优先接入多维度数据（Funding、OI、链上、事件），并建立可靠的 Feature Store 管道。

### 2.6 复用商品化部件，不在底盘螺丝上消耗生命
以下能力原则上应优先复用成熟开源实现：
- 交易所基础 REST / WS 接入
- 限流 / 重试 / 签名 / 网络客户端
- 常规指标与数值库
- 基础监控与告警设施
- 通用研究 / 回测底座

以下能力原则上应优先自研：
- Core 状态机与幂等
- Alignment Gate
- Policy Plane
- Feature Store 版本纪律
- 审计 / 回放 / Reconciler / Risk 收口
- AI 治理边界

---

## 3. 当前实现分层

本项目的工程优先级按五层推进：
- **P0：必须先做（主闭环地基）**
- **P1：第一阶段必须完成（主闭环成立）**
- **P2：第二阶段必须完成（研究闭环成立）**
- **P3：第三阶段增强（闭环完成后再上）**
- **P4：当前明确延后（冻结扩张）**

同时，每项能力都要标记：
- **Build**：应自研
- **Wrap**：应封装开源能力
- **Borrow**：应直接借鉴现成实现思路 / 测试样本

---

## 4. P0：必须先做（主闭环地基）

## 4.1 项目骨架与目录结构
**类型**：Build

### 原因
没有清晰目录和模块边界，后续 Binance 逻辑极易污染 Core。

### 当前必须完成
- 基础仓库结构
- 五平面目录分层
- 配置加载
- 基础日志与错误类型
- 环境变量与运行模式区分
- 第三方依赖准入规则文档

### 目标
保证后续任何模块都知道自己属于：
- Core
- Adapter
- Persistence
- Policy
- Insight

---

## 4.2 Binance Metadata Adapter
**类型**：Wrap

### 原因
没有 symbol 规则就无法安全下单，也无法构建 tradable universe。

### 当前必须完成
- symbol metadata 拉取
- tick size
- step size
- min notional
- symbol status
- metadata 缓存与刷新

### 输出价值
- 所有后续模块都可复用
- 立刻减少非法下单风险
- 为订单合法性检查、最小交易金额过滤和研究样本过滤提供底座

---

## 4.3 Binance Market Data Adapter
**类型**：Wrap

### 原因
没有可信市场数据，后续所有研究和执行都没有意义。

### 当前必须完成
- Kline
- Trades / AggTrades
- Depth
- 统一标准化结构
- 基础落盘能力

### 最低标准
- 能统一转换成系统内部事件
- 能记录 `event_time` 与 `local_receive_time`
- 能附带 `raw_reference`

### 复用建议
- 底层 REST / WS 接入优先基于成熟开源库封装
- 只在 Adapter 中出现第三方对象，业务层不感知

---

## 4.4 WS Health Monitor + Alignment Gate
**类型**：Build

### 原因
这是 Crypto 版最关键的生存组件之一。

### 当前必须完成
- WS 假死检测
- 重连逻辑
- REST 快照恢复
- 未对齐不外发正式事件
- `reconcile_grace_period_ms` 配置支持
- 降级状态标签

### 为什么优先级极高
如果这部分没有先做，你后面写的所有信号与风控都建立在脏流上。

---

## 4.5 Event Log Schema（PostgreSQL）
**类型**：Build

### 原因
没有事件溯源，就没有审计、回放和对账基础。

### 当前必须完成
- `event_log`
- `orders`
- `fills`
- `positions`
- `balances`
- `risk_events`

### 最低要求
- 每个关键事件都能被唯一标识
- 能支持 replay
- 能按 symbol / time 检索

### 注意
不要先用“最终状态表”替代事件日志。

---

## 4.6 Binance Funding / OI Adapter
**类型**：Wrap

### 原因
资金费率和 OI 是中低频策略的核心输入，必须从一开始就接入，并确保数据连续性。

### 必须完成
- 实时 Funding Rate 订阅（或定期拉取）
- 历史 Funding Rate 补录
- Open Interest 快照与增量
- 大额清算事件流（可选，但建议）

---

## 4.7 Feature Store 最小版本
**类型**：Build

### 原因
没有统一、版本化的特征层，研究与实盘会各写各的，最后变成“代码都叫同一个名字，结果不是同一个宇宙”。

### 必须完成
- 特征表结构
- `feature_name + version + symbol + ts` 主键纪律
- 来源引用字段
- 读写 API

---

## 5. P1：第一阶段必须完成（主闭环成立）

## 5.1 Binance Account / Order Adapter
**类型**：Wrap

### 原因
只有市场数据不够，必须有账户与订单的标准化接入，才能形成最小执行闭环。

### 必须完成
- account snapshot
- balances
- open orders
- place order
- cancel order
- query order
- response normalization

---

## 5.2 Core Order State Machine
**类型**：Build

### 原因
这是系统正确性的核心。

### 必须完成
- 状态 Rank 定义
- 单调递增约束
- 终态不可逆
- 重复事件幂等处理

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
**类型**：Build

### 原因
研究输出和执行结果必须通过统一仓位对象连接。

### 必须完成
- 持仓状态对象
- 持仓更新规则
- 与 fills / balances 的关系建模
- 方向、成本价、数量、可用性等基础字段

---

## 5.4 Risk Event Router + KillSwitch
**类型**：Build

### 原因
没有正式风险收口，系统一旦异常只能靠人工和日志。

### 必须完成
- RiskEvent 标准结构
- 风险事件收口
- KillSwitch L1-L2
- 禁开仓 / 本地锁死能力

### 当前关键触发条件
- Alignment Gate 长时间未恢复
- WS 断流 / REST 异常持续
- 对账出现 `fatal divergence`
- 单日亏损超限
- 重复下单 / 状态机异常

---

## 5.5 Reconciler（最小版本）
**类型**：Build

### 原因
本地状态一旦和交易所漂移，系统会越来越危险。

### 必须完成
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

### 备注
v3.4.0 中，Reconciler 不再只是“以后再说”的配件，而是主闭环的必备守门员。

---

## 5.6 链上 / 宏观数据接入（基础版）
**类型**：Wrap

### 原因
链上数据可作为 Regime 判断和风险预警。

### 必须完成
- 从 Coinglass 或类似聚合源拉取主要指标
- 存储为时间序列
- 可被 Insight Plane 读取

---

## 5.7 事件公告抓取基础
**类型**：Wrap

### 原因
事件驱动是主线之一，必须结构化存储。

### 必须完成
- 定时抓取 Binance 公告 RSS 或页面
- 解析关键字段（标题、时间、内容摘要）
- 存入事件表

---

## 5.8 时间窗口风控模块
**类型**：Build

### 原因
中低频持仓必须考虑非活跃时段的流动性风险。

### 必须完成
- 定义本地时间段（如 0:00-6:00 为低流动性时段）
- 根据当前时间计算仓位系数
- 与 KillSwitch 联动

---

## 5.9 下单前深度检查模块
**类型**：Build

### 原因
避免在薄订单簿中产生巨大滑点。

### 必须完成
- 下单前查询当前订单簿 depth
- 估算达到目标数量所需的平均滑点
- 若滑点 > 阈值，则拒绝下单或拆单

---

## 5.10 策略实时监控与告警（最小版本）
**类型**：Wrap + Build

### 原因
没有实时监控，系统会在出问题时装死，像一只假装自己是石头的赛博蜥蜴。

### 必须完成
- 监控预期持仓 vs 实际持仓
- 监控日内 PnL 与预期波动率
- 监控信号触发但未成交
- 配置告警规则
- 支持 Telegram / 企业微信 / 飞书其中一种最小通知链路

### 复用建议
- 告警发送、日志聚合、基础 metrics 可优先接成熟组件
- 监控规则与风险语义必须留在系统内部定义

---

## 6. P2：第二阶段必须完成（研究闭环成立）

## 6.1 Trend Base Signals
**类型**：Build

### 必须完成
- moving average state
- breakout
- momentum state
- relative strength

### 原则
先用最少参数和最透明规则建立第一批趋势信号，不追求复杂度。

---

## 6.2 Price-Volume Base Signals
**类型**：Build

### 必须完成
- volume expansion
- volatility compression / expansion
- price-volume divergence
- abnormal range detection

---

## 6.3 Capital Structure Signals
**类型**：Build

### 必须完成
- Funding Rate z-score
- OI 变化率与价格变化率背离指标
- 多空比极端指标
- 清算冲击代理变量

---

## 6.4 On-Chain Base Signals
**类型**：Build

### 必须完成
- 交易所净流入 / 流出的短期变化
- 稳定币供应比率
- 简化版巨鲸活跃代理变量

---

## 6.5 Event Model
**类型**：Build

### 必须完成
- `ListingEvent`
- `DelistingEvent`
- `MaintenanceEvent`
- `SymbolRuleChangeEvent`
- `AlignmentRiskEvent`
- `ReconcileDivergenceEvent`

### 后续扩展
- `FundingShockEvent`
- `LiquidationSpikeEvent`
- `RegulatoryShockEvent`

---

## 6.6 Signal / Strategy Sandbox（最小版本）
**类型**：Build + Borrow

### 原因
没有沙盒，研究层很快会被伪规则污染。

### 必须完成
- 规则可运行性检查
- 未来函数检查
- 成本后基本有效性检查
- regime 表现检查
- 候选规则状态机
- 机制验证步骤

### 借鉴建议
可借鉴成熟回测框架与研究框架的测试思路、指标输出结构，但不要把系统内部特征和风险语义外包给它们。

---

## 7. P3：第三阶段增强（闭环完成后再上）

## 7.1 Position & Risk Constructor
**类型**：Build

### 作用
把研究信号转成仓位规则。

### 必须完成
- 单币种最大暴露
- 总暴露控制
- 冷却期
- 最小交易阈值
- regime 不同时的风险折扣

---

## 7.2 PG 投影读模型
**类型**：Build

### 作用
把当前以内存为主的控制面读模型迁移到正式 PG 投影，提升持久性与可恢复性。

---

## 7.3 Escape Time Simulator
**类型**：Build

### 作用
模拟极端流动性条件下的退出成本和退出时间，用于中低频持仓风险管理。

---

## 7.4 Replay Runner
**类型**：Build

### 作用
形成真正可复盘闭环。

### 最低目标
- 能基于 event log 重建关键运行时
- 能重放特定 symbol / 时间窗口 / 风险事件场景

---

## 7.5 AI 洞察增强
**类型**：Build

### 作用
在不越权的前提下增强：
- 事件摘要
- regime 解释
- 候选规则草案
- 研究日志整理

### 注意
先做“解释与提案”，不做“直接控制执行”。

---

## 8. P4：当前明确延后（冻结扩张）

以下内容当前明确延后：
- 多交易所套利
- 高频做市
- 毫秒级盘口策略
- 复杂期权策略
- 多账户分布式执行
- 全自动 AI 自治交易
- 完整 A 股实盘链路
- 复杂跨市场风险预算优化

原则：
> **先让主闭环成立，再谈星际殖民。**

---

## 9. 当前最小可生存闭环定义

当前版本最小闭环定义为：

`Binance data`
+ `Funding / OI data`
+ `基础链上 / 宏观数据`
+ `Event data`
-> `health check`
-> `alignment gate`
-> `standardized events`
-> `feature store`
-> `core state machine`
-> `time-aware & depth-aware risk gate`
-> `minimal signal`
-> `target position`
-> `guarded execution`
-> `reconcile`
-> `real-time monitoring alert`
-> `report / replay`

只要这个链路成立，系统就具备在真实市场中以中低频策略生存和迭代的资格。

---

## 10. 开发时的禁止事项

当前阶段明确禁止：
- 先写复杂策略，后补数据接入
- 先写自动执行，后补 Reconciler 与监控
- 先做 AI 自动化，后补风控
- 先做多市场扩展，后补 Binance 主链路
- 先做华丽面板，后补事件溯源
- 把第三方 SDK 调用直接写进 Core
- 在 Feature Store 未就绪前，直接硬编码特征计算到策略中
- 忽视时间窗口和流动性检查，直接下单
- 为未来市场提前实现大量复杂抽象，拖慢当前闭环

---

## 11. 一句话总结

Crypto v3.4.0 的实现优先级核心，不是先让系统“做更多事”，而是先让系统 **拥有足够深的数据视野、足够硬的生存纪律、足够清楚的复用边界**；只有先把这些做对，后面的策略和 AI 才值得接进去。
