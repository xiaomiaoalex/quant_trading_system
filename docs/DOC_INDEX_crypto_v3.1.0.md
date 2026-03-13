## 1. 文档目的

本文档用于汇总 `quant_trading_system Crypto v3.1.1` 当前的核心文档体系，说明每份文档的职责、适用阶段、推荐阅读顺序与相互依赖关系。

本索引的目标不是简单列出文件名，而是帮助开发时始终保持一个明确的问题意识：

- 先看哪份文档
- 哪些文档定义方向
- 哪些文档定义边界
- 哪些文档直接指导编码
- 哪些文档决定运行时行为
- 哪些文档决定以后能否安全扩展到 A 股

一句话定义：

> 这份文档是当前 Crypto v3.1.1 文档体系的导航页，确保你在写任何一行代码前，都知道它应该受哪份规范约束。

---

## 2. 当前文档总览

### 顶层说明
- `README_crypto_v3.1.1.md`

### 项目级文档
- `docs/PROJECT_CHARTER_crypto_v3.1.0.md`
- `docs/ROADMAP_12_SPRINTS_crypto_v3.1.0.md`

### 架构与核心规范
- `docs/ARCHITECTURE_crypto_v3.1.1.md`
- `docs/ADAPTER_SPEC_binance_v3.1.1.md`
- `docs/RISK_POLICY_crypto_v3.1.1.md`

### 数据与事件规范
- `docs/DATA_SOURCE_STRATEGY_crypto_v3.1.1.md`
- `docs/RECONCILER_SPEC_crypto_v3.1.1.md`
- `docs/EVENT_MODEL_SPEC_crypto_v3.1.1.md`

### 工程与实施文档
- `docs/IMPLEMENTATION_PRIORITY_crypto_v3.1.1.md`
- `docs/DOC_INDEX_crypto_v3.1.1.md`

---

## 3. 各文档职责说明

## 3.1 `README_crypto_v3.1.1.md`

### 作用
项目总入口，用最短路径说明这个系统是什么、为什么存在、当前边界在哪里。

### 回答的问题
- 这个项目是什么
- 为什么先做 Binance
- 为什么不是直接照搬 A 股版
- 当前的主研究对象是什么
- 当前系统最重要的原则是什么

### 适合谁看
- 未来的你自己
- 新加入项目的人
- 需要快速理解项目方向的人

### 不负责
- 模块细节
- 接口规范
- 风控阈值
- 数据标准化细节

---

## 3.2 `docs/PROJECT_CHARTER_crypto_v3.1.0.md`

### 作用
项目宪章，定义项目使命、边界、目标、问题定义与成功标准。

### 回答的问题
- 这个项目真正要解决什么问题
- 当前范围是什么，不做什么
- 长中短期目标是什么
- 为什么要预留 A 股接口
- 当前最重要的成功标准是什么

### 适合谁看
- 做方向选择时
- 判断需求是否应纳入当前版本时
- 防止 scope creep 时

### 不负责
- 接口设计细节
- 订单状态机细节
- 对账器实现细节
- Binance 适配层字段定义

---

## 3.3 `docs/ROADMAP_12_SPRINTS_crypto_v3.1.0.md`

### 作用
把项目分解成 12 个一周 Sprint，定义开发顺序、阶段目标、每周产物与验收标准。

### 回答的问题
- 先做什么，后做什么
- 哪些 Sprint 是 Gate
- 哪些能力当前必须做
- 哪些能力可以延期

### 适合谁看
- 开发排期
- Sprint 计划
- 周度复盘
- 控制项目节奏

### 不负责
- 模块内部技术设计
- 风控政策细节
- 事件模型字段细节

---

## 3.4 `docs/ARCHITECTURE_crypto_v3.1.1.md`

### 作用
定义系统总体架构、五平面职责、模块边界、运行流与市场适配原则。

### 回答的问题
- 为什么必须五平面隔离
- Core / Adapter / Persistence / Policy / Insight 各自做什么
- Binance 首发如何不把系统写死
- A 股未来如何接入而不推倒重来
- Alignment Gate、Reconciler、AI 分别处于哪一层

### 适合谁看
- 设计项目目录时
- 写模块边界时
- 判断一段代码放哪一层时
- 做重构时

### 不负责
- 具体 Binance 字段
- 具体风险阈值
- 具体事件分类细节

---

## 3.5 `docs/ADAPTER_SPEC_binance_v3.1.1.md`

### 作用
定义 Binance 适配层的职责、标准化输出、健康检查、重连、对齐与限流策略。

### 回答的问题
- Binance 接入层到底负责什么
- WS 假死与 REST/WS 对齐怎么处理
- 哪些脏数据在 Adapter 吸收
- 适配层输出给系统内部的标准对象是什么

### 适合谁看
- 写 Binance Market Data Adapter
- 写 Binance Order / Account Adapter
- 写 WS 重连和对齐逻辑
- 写 metadata 同步逻辑

### 不负责
- Core 状态机真相
- 风控裁决逻辑
- 策略研究主线

---

## 3.6 `docs/RISK_POLICY_crypto_v3.1.1.md`

### 作用
定义风险哲学、风险层级、运行时风控边界、KillSwitch 与环境异常处理原则。

### 回答的问题
- 系统在什么情况下必须停
- 断流、错位、极端行情如何处理
- 单币种、总暴露、杠杆和单日亏损如何约束
- Reconciler 发现严重分歧后怎么办

### 适合谁看
- 写 Policy Plane
- 写 KillSwitch
- 写运行时风控
- 写 Position & Risk Constructor 时

### 不负责
- 行情接入
- 事件标准化
- 研究信号生成

---

## 3.7 `docs/DATA_SOURCE_STRATEGY_crypto_v3.1.1.md`

### 作用
定义 Crypto 版数据源分层、可信度分级、用途边界与升级路径。

### 回答的问题
- 什么是正式真相源
- 什么是辅助研究源
- 什么只能用于 AI / Insight
- Binance 直连、衍生品辅助源、公告与新闻源各自扮演什么角色
- 哪些数据可以驱动执行，哪些不可以

### 适合谁看
- 接入任何新数据源前
- 判断某个外部数据能否进入研究主链路时
- 规划数据升级时

### 不负责
- 具体 Adapter 接口
- 具体风控阈值
- 事件对象字段细节

---

## 3.8 `docs/RECONCILER_SPEC_crypto_v3.1.1.md`

### 作用
定义对账器的核对对象、运行方式、分歧分级、grace window 与联动动作。

### 回答的问题
- 本地状态和交易所状态如何核对
- 漂移何时是合理的、何时是危险的
- `EXPECTED_DRIFT / UNEXPECTED_DRIFT / FATAL_DIVERGENCE` 分别意味着什么
- Reconciler 的结果如何进入 Policy Plane

### 适合谁看
- 写 Reconciler 时
- 写账户 / 订单 / 仓位对账时
- 写运行时一致性检查时

### 不负责
- 策略逻辑
- 行情接入
- AI 摘要任务

---

## 3.9 `docs/EVENT_MODEL_SPEC_crypto_v3.1.1.md`

### 作用
定义系统事件模型的分类、统一字段、来源优先级、生命周期与事件用途边界。

### 回答的问题
- 什么叫事件
- 事件如何标准化
- 哪些事件能影响风险和执行
- AIInsightEvent 处于什么层级
- 交易所公告、规则变更、风险异常如何进入系统

### 适合谁看
- 设计事件总线时
- 写事件落盘与路由时
- 写回放与报告系统时

### 不负责
- 订单状态机细节
- 风控阈值
- Binance 连接健康判断

---

## 3.10 `docs/IMPLEMENTATION_PRIORITY_crypto_v3.1.1.md`

### 作用
定义工程开工优先级，把 12 Sprint 路线图压缩成"现在立刻写代码该先做什么"。

### 回答的问题
- 哪些模块是 P0
- 哪些模块是 P1 / P2 / P3
- 为什么某些能力必须先做
- 当前最小可运行闭环到底是什么

### 适合谁看
- 开始真正搭工程时
- 每天排编码顺序时
- 遇到范围膨胀时做取舍时

### 不负责
- 总体方向定义
- 完整项目边界说明
- 长期路线图叙述

---

## 3.11 `docs/DOC_INDEX_crypto_v3.1.1.md`

### 作用
作为当前文档体系的导航页，帮助你快速定位"遇到某个问题该看哪份文档"。

### 回答的问题
- 当前有哪些文档
- 每份文档负责什么
- 推荐阅读顺序是什么
- 文档之间依赖关系是什么

### 适合谁看
- 刚回到项目时
- 准备开始新模块时
- 文档体系变多之后快速找入口时

---

## 4. 推荐阅读顺序

### 第一步：理解项目方向
1. `README_crypto_v3.1.1.md`
2. `docs/PROJECT_CHARTER_crypto_v3.1.0.md`

### 第二步：理解开发顺序
3. `docs/ROADMAP_12_SPRINTS_crypto_v3.1.0.md`
4. `docs/IMPLEMENTATION_PRIORITY_crypto_v3.1.1.md`

### 第三步：理解系统骨架
5. `docs/ARCHITECTURE_crypto_v3.1.1.md`

### 第四步：理解接入与风控
6. `docs/ADAPTER_SPEC_binance_v3.1.1.md`
7. `docs/RISK_POLICY_crypto_v3.1.1.md`

### 第五步：理解数据与运行时治理
8. `docs/DATA_SOURCE_STRATEGY_crypto_v3.1.1.md`
9. `docs/RECONCILER_SPEC_crypto_v3.1.1.md`
10. `docs/EVENT_MODEL_SPEC_crypto_v3.1.1.md`

---

## 5. 文档依赖关系

```text
README
  -> PROJECT_CHARTER
  -> ROADMAP
  -> IMPLEMENTATION_PRIORITY

PROJECT_CHARTER
  -> ARCHITECTURE
  -> DATA_SOURCE_STRATEGY
  -> RISK_POLICY

ROADMAP
  -> IMPLEMENTATION_PRIORITY
  -> ARCHITECTURE
  -> all implementation-facing docs

ARCHITECTURE
  -> ADAPTER_SPEC
  -> RECONCILER_SPEC
  -> EVENT_MODEL_SPEC
  -> RISK_POLICY

ADAPTER_SPEC
  -> DATA_SOURCE_STRATEGY
  -> RECONCILER_SPEC

RISK_POLICY
  -> RECONCILER_SPEC
  -> EVENT_MODEL_SPEC

IMPLEMENTATION_PRIORITY
  -> ARCHITECTURE
  -> ADAPTER_SPEC
  -> RISK_POLICY
  -> RECONCILER_SPEC
```

---

## 6. Capability Matrix (Current / Next / Target)

本节定义系统各能力的当前实现状态。Current 判定标准：代码存在 + 有非跳过测试 + 运行前提明确。

### 6.1 Core Plane

| 能力 | 状态 | 代码路径 | 测试路径 |
|---|---|---|---|
| 订单/成交单调状态与幂等 CAS | Current | `trader/core/application/deterministic_layer.py` | `trader/tests/test_deterministic_layer.py` |
| 订单状态机 (Pending/Accepted/Filled/Canceled) | Current | `trader/core/application/oms.py` | `trader/tests/test_api_services.py` |
| 持仓状态管理 | Current | `trader/core/domain/models/position.py` | `trader/tests/test_domain_events.py` |
| 风险引擎多层防护 | Current | `trader/core/application/risk_engine.py` | `trader/tests/test_risk_engine_layers.py` |

### 6.2 Adapter Plane

| 能力 | 状态 | 代码路径 | 测试路径 |
|---|---|---|---|
| Binance Public Stream (Kline/Depth/Trade) | Current | `trader/adapters/binance/public_stream.py` | `trader/tests/test_binance_private_stream.py` |
| Binance Private Stream (Order/Account) | Current | `trader/adapters/binance/private_stream.py` | `trader/tests/test_binance_private_stream.py` |
| Private 流 ALIGNING 期间阻断执行事件 | Current | `trader/adapters/binance/private_stream.py` (line 394) | `trader/tests/test_binance_private_stream.py` |
| Public 流 DEGRADED 标签 | Current | `trader/adapters/binance/public_stream.py` (line 233) | `trader/tests/test_binance_stream_base.py` |
| WS Health Monitor | Current | `trader/adapters/binance/connector.py` | `trader/tests/test_binance_connector.py` |
| 限流与退避策略 | Current | `trader/adapters/binance/rate_limit.py` | `trader/tests/test_binance_rate_limit.py` |
| 环境风险检测 (WS断流/REST异常) | Current | `trader/adapters/binance/environmental_risk.py` | `trader/tests/test_binance_environmental_risk.py` |
| REST 对齐快照恢复 | Current | `trader/adapters/binance/rest_alignment.py` | `trader/tests/test_binance_rest_alignment.py` |

### 6.3 Persistence Plane

| 能力 | 状态 | 代码路径 | 测试路径 |
|---|---|---|---|
| 风险事件 PG-First 持久化 | Current | `trader/adapters/persistence/risk_repository.py` | `trader/tests/test_risk_idempotency_persistence.py` |
| 事件幂等去重 | Current | `trader/adapters/persistence/risk_repository.py` | `trader/tests/test_risk_repository.py` |
| 内存事件存储 | Current | `trader/storage/in_memory.py` | `trader/tests/test_api_endpoints.py` |
| 事件查询服务 (内存读模型) | Current | `trader/services/event.py` | `trader/tests/test_api_endpoints.py` |
| PostgreSQL 存储 | Current | `trader/adapters/persistence/postgres/` | `trader/tests/test_postgres_storage.py` |

### 6.4 Policy Plane

| 能力 | 状态 | 代码路径 | 测试路径 |
|---|---|---|---|
| KillSwitch 服务 | Current | `trader/services/killswitch.py` | `trader/tests/test_api_endpoints.py` |
| 风险事件路由 | Current | `trader/services/risk.py` | `trader/tests/test_risk_engine_layers.py` |
| 环境风险 L1/L2/L3 分级 | Current | `trader/adapters/binance/environmental_risk.py` | `trader/tests/test_binance_environmental_risk.py` |

### 6.5 API / Service Layer

| 能力 | 状态 | 代码路径 | 测试路径 |
|---|---|---|---|
| /v1/events 事件查询 | Current | `trader/api/routes/events.py` | `trader/tests/test_api_endpoints.py` |
| /v1/snapshots/latest 快照查询 | Current | `trader/api/routes/events.py` | `trader/tests/test_api_endpoints.py` |
| /v1/killswitch 开关管理 | Current | `trader/api/routes/killswitch.py` | `trader/tests/test_api_endpoints.py` |
| /v1/orders 订单管理 | Current | `trader/api/routes/orders.py` | `trader/tests/test_api_services.py` |
| /v1/risk 风险查询 | Current | `trader/api/routes/risk.py` | `trader/tests/test_api_endpoints.py` |

### 6.6 Next (下一阶段)

| 能力 | 状态 | 前置条件 | 目标 Sprint |
|---|---|---|---|
| Reconciler 持续对账服务 | Next | Core 状态机 + Event Log 完善 | Sprint 5-6 |
| Position Constructor 仓位构造器 | Next | 研究信号层 + 风险引擎 | Sprint 7-8 |
| 统一报告系统 | Next | Event Log + 状态机 | Sprint 9-10 |

### 6.7 Target (未来目标)

| 能力 | 状态 | 前置条件 |
|---|---|---|---|
| AI Proposal/Approve 治理 API | Target | Reconciler + 审计数据完善 |
| Runner 主执行链路 (自动下单) | Target | Position Constructor + Risk Policy 完备 |
| AI Insight Copilot | Target | 统一报告 + 事件模型完整 |
| Replay Runner 回放系统 | Target | Event Log + 状态机 + PG 持久化完备 |

---

## 7. 如果现在立刻开始写代码，最重要的 5 份文档

当前最重要的 5 份文档是：

1. `docs/ARCHITECTURE_crypto_v3.1.1.md`
2. `docs/ADAPTER_SPEC_binance_v3.1.1.md`
3. `docs/RISK_POLICY_crypto_v3.1.1.md`
4. `docs/RECONCILER_SPEC_crypto_v3.1.1.md`
5. `docs/IMPLEMENTATION_PRIORITY_crypto_v3.1.1.md`

原因很简单：

- 它们直接决定目录结构
- 决定模块边界
- 决定运行时行为
- 决定最小闭环
- 决定系统什么时候该停、什么时候不能信

---

## 8. 当前文档体系的空缺

当前文档体系已经能支撑开始写核心代码，但仍建议后续补充：

- `docs/REPO_STRUCTURE_crypto_v3.1.1.md`
- `docs/DB_SCHEMA_crypto_v3.1.1.md`
- `docs/STATE_MACHINE_SPEC_crypto_v3.1.1.md`
- `docs/POSITION_CONSTRUCTOR_SPEC_crypto_v3.1.1.md`
- `docs/REPLAY_SPEC_crypto_v3.1.1.md`
- `docs/AI_INSIGHT_POLICY_crypto_v3.1.1.md`

---

## 9. 一句话总结

这份文档索引的意义，不是为了"把文档列齐"，而是确保你在实现任何一块 Binance / Crypto 交易逻辑之前，都知道它属于哪一层、受哪份规范约束、是否会越权，以及在极端行情下系统优先保护的到底是什么。
