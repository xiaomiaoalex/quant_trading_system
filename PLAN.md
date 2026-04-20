# PLAN.md — quant_trading_system Crypto v3.4.0 工程推进计划

> 总架构师视角。本文件描述当前项目状态、各阶段任务优先级与验收标准，供工程师执行参考。
> 执行优先级遵循 `.traerules`：As-Is > In-Progress > Target。

---

## 一、当前状态快照（As-Is）

> 注：本文件历史阶段计划较长。自 2026-04-16 起，当前执行主线切换为  
> “Phase 8 — v3.4.0 Qlib + Hermes 研究编排集成”。  
> Phase 1-7 视为历史完成记录与并行维护项；任务排序以本节“当前执行主线”优先。

### 已完成（可信赖，不得回归）

| 模块 | 文件 | 说明 |
|------|------|------|
| 确定性层 | `core/application/deterministic_layer.py` | CAS + 状态单调 + 256分片锁 + 900s TTL去重，生产级 |
| OMS | `core/application/oms.py` | 订单生命周期、幂等提交、事件发布 |
| 风险引擎 | `core/application/risk_engine.py` | 前/中/后置风控、KillSwitch推荐、插件架构 |
| Binance适配器栈 | `adapters/binance/` | connector / public_stream / private_stream / rest_alignment / degraded_cascade / rate_limit / backoff / environmental_risk |
| KillSwitch（L0-L3） | `services/killswitch.py` | 四级定义完整，行为语义正确 |
| PostgreSQL风险持久化 | `adapters/persistence/risk_repository.py` | 风险事件 + PG fallback to in-memory |
| API骨架 | `api/main.py` + `api/routes/` | FastAPI路由已注册，端点为stub |
| 测试基础设施 | `tests/fakes/` | fake_clock / fake_http / fake_websocket |
| CI门禁 | `.github/workflows/ci-gate.yml` | 4阶段门禁，P0回归全覆盖 |
| Feature Store / Reconciler / 深度检查 / 时间窗口 | `adapters/persistence/feature_store.py` 等 | Phase 1 已完成并验证 |
| 研究信号层 | `core/domain/signals/` | 趋势、价量、资金结构信号已完成 |
| PG投影 / Replay / HITL | `adapters/persistence/postgres/projectors/` 等 | Phase 3 核心能力已落地 |
| 策略管理与AI共创 | `services/strategy_runner.py` 等 | Phase 4 已完成 |
| 回测框架升级 | `services/backtesting/` | Phase 5 已完成 Lean 适配、验证与性能基准 |
| **策略自动交易闭环** | `services/oms_callback.py` 等 | **Task 11-15 完成（2026-04-20）：实时行情 → tick调度 → OMS回调 → 真实下单 → 成交幂等 → 安全闸门** |

### 部分完成（Phase 6 已完成，待收尾文档）

| 模块 | 状态 |
|------|------|
| 文档真相源（Phase 6 M1） | ✅ 已完成 |
| RiskSizer 统一仓位决策（Phase 6 M2） | ✅ 已完成 |
| 回撤与 venue 联动（Phase 6 M3） | ✅ 已完成 |
| Capital Allocator（Phase 6 M4） | ✅ 已完成 |
| 替代数据健康度（Phase 6 M5） | ✅ 已完成 |

### 未开始（Target，按优先级推进）

- **Phase 9**: 下一阶段规划（待定义）
- 策略元数据治理（edge / failure mode / capacity / conflicts）

## 当前执行主线：Phase 8 — v3.4.0 Qlib + Hermes 研究编排集成

### 目标

在不破坏既有五平面与确定性约束的前提下，完成“Qlib 离线研究 + Hermes 研发编排 + 现有执行链路”的闭环集成。

### Phase 8 P0 任务

| Task | 目标 | 交付物 | 状态 |
|------|------|--------|------|
| 8.1 | 研究数据契约冻结 | 数据字段/时区/对齐/缺失值规则文档化 | ✅ 已完成 |
| 8.2 | Qlib 数据转换与训练流水线 | `qlib_data_converter` + `qlib_train_workflow` | ✅ 已完成 |
| 8.3 | 模型版本治理 | `model_version/feature_version` 注册规范 | ✅ 已完成 |

### Phase 8 P1 任务

| Task | 目标 | 交付物 | 状态 |
|------|------|--------|------|
| 8.4 | Qlib 预测信号桥接 | `qlib_to_strategy_bridge` 标准化 Signal 输出 | ✅ 已完成 |
| 8.5 | Hermes 研究编排 SOP | 数据→训练→评估→报告自动化 | ✅ 已完成 |
| 8.6 | 五层门控联调 | 与 `strategy_validation_gate` 集成 | 进行中 |

### Phase 8 P2 任务

| Task | 目标 | 交付物 | 状态 |
|------|------|--------|------|
| 8.7 | 影子验证与上线收敛 | 回测/影子/成交偏差报告 | ✅ 已完成 |
| 8.8 | 运行观测与回滚方案 | 模型漂移检测、模型级回滚策略 | ✅ 已完成 |

### 已完成交付物

- `scripts/qlib_data_converter.py` - 数据转换器 (Phase A)
- `docs/DATA_CONTRACT.md` - 数据契约文档 (Phase A)
- `scripts/qlib_train_workflow.py` - 训练工作流 (Phase B)
- `scripts/qlib_factor_miner.py` - 因子挖掘器 (Phase B)
- `scripts/qlib_to_strategy_bridge.py` - 信号桥接 (Phase C)
- `docs/HERMES_ORCHESTRATION_TEMPLATES.md` - Hermes 编排模板 (Phase D)
- `scripts/qlib_model_validator.py` - 模型验证器 (Phase E)
- `scripts/model_drift_detector.py` - 模型漂移检测 (Phase F)
- `scripts/model_rollback_manager.py` - 回滚管理器 (Phase F)

### 验收原则

1. Qlib 与 Hermes 只在研究/编排域运行，不直连执行下单。
2. AI 信号必须带版本与 trace 信息（model/feature/signal）。
3. 未通过五层门控的策略不得进入 RUNNING。
4. 文档、计划、状态三者同步更新。

### 计划文档入口

- 详细拆解见：`docs/V3.4.0_HERMES_QLIB_INTEGRATION_PLAN.md`

### 补充进展（2026-04-18）

- 控制面策略链路已打通：`策略代码新建/调试 -> 回测 -> 代码注册 -> 加载 -> 运行`
- 后端回测执行已从占位改为真实异步任务，包含 `progress` 推进与报告落盘
- Task 9.4 / 9.5 在后端与前端联调维度进入“已验证”状态
- Task 9.6（Audit 查询接口 + 前端 Audit 页）已完成并回归通过
- Task 9.7（Replay 查询接口 + 前端 Replay 页）已完成并回归通过
- 下一优先级建议：Task 9.9 / Task 9.10（Chat 参数风格统一 + Stale/Degraded 枚举）继续闭环

## 并行维护主线：Phase 7 — 风控穿透验证与策略正期望证明

### 目标

把系统从"功能齐全但无法证明真的生效"收敛到"风控改变下单结果可量化、策略扣成本后样本外仍正期望"的状态。

### Phase 6 状态（M1-M5 全部完成 ✅）
- M1: 文档单一真相源 ✅
- M2: Survival Risk Sizer ✅
- M3: Drawdown/Venue 联动去杠杆 ✅
- M4: Minimal Capital Allocator ✅
- M5: Alternative Data Health Gate ✅

### Phase 7 P0 任务

| Task | 目标 | 交付物 |
|------|------|--------|
| 7.1 | 风控穿透测试矩阵 | 8+ 场景端到端测试，验证风控真的改变订单命运 |
| 7.2 | RiskInterventionTracker | Risk Intervention Rate 量化指标 |
| 7.3 | 策略上线前 5 层验证门控 | L1 机制假设/L2 回测合规/L3 样本外/L4 成本压测/L5 影子模式 |

### Phase 7 P1 任务

| Task | 目标 | 交付物 |
|------|------|--------|
| 7.4 | 成本压测标准化入口 | 1x/1.5x/2x 成本压测一键执行 |
| 7.5 | 影子模式验证框架 | 回测/影子/成交三者偏差比较 |
| 7.6 | AIAuditLog 持久化 | 从内存迁移到 PostgreSQL |
| 7.7 | 控制面快照持久化 | /v1/snapshots 从 PG 读取 |

### Phase 7 P2 任务

| Task | 目标 | 交付物 |
|------|------|--------|
| 7.8 | 统一 DecisionTraceId | 全链路 evidence chain 贯穿每次决策 |

### 验收原则

1. 不先写更多分析报告，从可证伪开始。
2. 风控验证看"订单命运是否改变"，不看"代码里有没有 if"。
3. 策略验证看"成本后期望"，不看"回测 Sharpe 多高"。
4. Core Plane 新增能力必须保持无 IO、确定性、可测试。

---

## 二、阶段划分

```
Phase 0 (当前)  ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5 ──► Phase 6 ──► Phase 7 ──► Phase 8
基础设施稳固       闭环安全层    研究信号层    增强与自动化  策略管理与AI共创  回测框架升级   风险收敛      风控穿透验证    Qlib/Hermes 集成
```

---

## 三、Phase 1 — 闭环安全层（最高优先级）

**目标**：系统能够安全地感知状态漂移、执行深度检查、监控运行，达到"可以开始小仓位试运行"的最低门槛。

---

### Task 1.1 — Feature Store（P0，解锁研究全链路）

**背景**：研究信号依赖特征版本一致性。无Feature Store则回测与实盘特征不一致，所有信号工作无意义。

**交付物**：
- `adapters/persistence/feature_store.py`
  - 表结构：`feature_values(symbol, feature_name, version, ts_ms, value, meta)`
  - 接口：`write_feature()`, `read_feature()`, `list_versions()`
  - 版本锁定：同一 `(symbol, feature_name, version)` 不可覆盖写（幂等）
- `adapters/persistence/postgres/migrations/001_feature_store.sql`
- 单测：版本冲突拒绝、幂等写入、跨版本读取隔离

**验收标准**：
- [x] 同一特征不同版本可共存
- [x] 写入幂等（重复写相同数据不报错，不重复插入）
- [x] 版本冲突（相同key不同value）抛出明确异常
- [x] CI postgres-integration阶段通过

---

### Task 1.2 — Reconciler（P1，生产安全底线）

**背景**：OMS本地状态与交易所真实状态可能因网络抖动、重连、丢包产生漂移。无Reconciler则无法发现也无法恢复。

**交付物**：
- `core/application/reconciler.py`
  - 输入：OMS本地订单快照 + REST拉取的交易所订单快照
  - 逻辑：对比 `cl_ord_id` 维度的状态差异，分类为 `GHOST`（本地有/交易所无）、`PHANTOM`（交易所有/本地无）、`DIVERGED`（状态不一致）
  - 宽限窗口：新订单60s内不触发漂移告警
  - 输出：`ReconcileReport` dataclass + 事件发布到event bus
- `services/reconciler_service.py`：定时触发（默认30s间隔），接入Control Plane
- `api/routes/reconciler.py`：`GET /reconciler/report`、`POST /reconciler/trigger`
- 单测：GHOST/PHANTOM/DIVERGED三种场景、宽限窗口边界、幂等触发

**验收标准**：
- [x] 能检测出本地OPEN但交易所已FILLED的订单
- [x] 能检测出本地无记录但交易所存在的挂单
- [x] 宽限窗口内新订单不误报
- [x] 漂移事件发布到event bus，KillSwitch可订阅
- [x] P0回归测试不回归

---

### Task 1.3 — 深度检查 + 滑点估算（P1，执行安全）

**背景**：薄流动性市场下单会产生大幅滑点。下单前必须验证深度充足。

**交付物**：
- `core/domain/services/depth_checker.py`
  - 输入：`OrderBook`快照 + 目标下单量 + 最大可接受滑点比例
  - 输出：`DepthCheckResult(ok: bool, estimated_slippage_bps: float, available_qty: float)`
  - 逻辑：遍历orderbook档位，累计可成交量，计算加权均价偏离
- 集成到 `risk_engine.py` 的前置检查（pre-trade hook）
- 单测：正常深度通过、深度不足拒绝、滑点超限拒绝

**验收标准**：
- [x] 深度不足时pre-trade check返回REJECT
- [x] 滑点估算误差在合理范围（单测用mock orderbook验证）
- [x] 不引入任何IO（Core Plane约束）

---

### Task 1.4 — 时间窗口风控完整实现（P1）

**背景**：`risk_engine.py`中时间窗口概念存在但系数体系未落地。

**交付物**：
- `core/domain/rules/time_window_policy.py`
  - 时段定义：`PRIME`（主力时段）/ `OFF_PEAK`（低流动性）/ `RESTRICTED`（禁止新开仓）
  - 可配置：UTC时段范围 + 对应的仓位系数（0.0–1.0）
  - 输出：`TimeWindowContext(period: str, position_coefficient: float, allow_new_position: bool)`
- 集成到 `risk_engine.py` pre-trade检查
- 单测：各时段边界、系数应用、RESTRICTED时段拒绝新开仓

**验收标准**：
- [x] RESTRICTED时段pre-trade返回REJECT
- [x] OFF_PEAK时段仓位上限按系数缩减
- [x] 时段配置可热更新（通过Control Plane API）

---

### Task 1.5 — 策略监控 + 基础告警（P1，运营可见性）

**背景**：无监控则无法感知执行异常、持仓漂移、PnL异常。

**交付物**：
- `services/monitor_service.py`
  - 指标采集：持仓数量、未成交订单数、当日PnL、KillSwitch级别、Adapter健康状态
  - 告警规则：PnL超日亏损阈值、未成交订单堆积超阈值、Adapter进入DEGRADED
  - 告警输出：结构化日志（第一阶段）；预留Telegram/webhook接口
- `api/routes/monitor.py`：`GET /monitor/snapshot`
- 单测：各告警规则触发条件

**验收标准**：
- [x] `/monitor/snapshot` 返回完整系统状态快照
- [x] 日亏损超限时触发告警日志
- [x] Adapter DEGRADED时触发告警日志

---

### Task 1.6 — 通用事件溯源完整落地（P1）

**背景**：当前仅风险事件有PG落地，OMS/Position事件仍为内存。事件溯源是系统恢复的基础。

**交付物**：
- `adapters/persistence/postgres/migrations/002_event_log.sql`
  - 表：`event_log(id, stream_key, seq, event_type, payload jsonb, ts_ms, schema_version)`
  - 索引：`(stream_key, seq)` 唯一约束
- `adapters/persistence/postgres/event_store.py`
  - 实现 `append(event)`, `read_stream(stream_key, from_seq)`, `snapshot_at(stream_key, seq)`
  - 幂等append（相同stream_key+seq不重复插入）
- OMS和Position事件接入PG event store
- 单测：幂等append、乱序读取、快照恢复

**验收标准**：
- [x] OMS事件持久化到PG
- [x] 重启后可从event_log恢复OMS状态
- [x] 幂等append通过CI postgres-integration
- [x] 内存fallback在PG不可用时自动启用

---

## 四、Phase 2 — 研究信号层

**前置条件**：Phase 1全部完成（Feature Store + Reconciler + 深度检查）。

---

### Task 2.1 — Funding/OI数据适配器完整实现

**交付物**：
- `adapters/binance/funding_oi_stream.py`
  - REST拉取：`GET /fapi/v1/fundingRate`、`GET /fapi/v1/openInterest`
  - 定时采集（默认8h funding周期前30min触发）
  - 写入Feature Store：`feature_name=funding_rate|open_interest`

**验收标准**：
- [ ] Funding rate数据写入Feature Store
- [ ] OI数据写入Feature Store
- [ ] 采集失败不影响主交易流程（降级日志）

---

### Task 2.2 — On-Chain/宏观数据适配器

**状态**：✅ 已完成（2026-03-25）

**交付物**：
- `adapters/onchain/onchain_market_data_stream.py`
  - `RawLiquidationEvent`：Binance Futures `!forceOrder@arr` WebSocket原始事件模型
  - `LiquidationBucket`：1分钟聚合桶（count, notional, long/short breakdown, net imbalance）
  - `LiquidationAggregator`：桶对齐、事件聚合、Feature Store flush
  - `BinanceLiquidationWSConnector`：WebSocket连接、自动重连、消息解析
  - `stablecoin_supply`：Binance API稳定币供应量采集
- `trader/tests/test_onchain_market_data_stream.py`
  - 34个测试全部通过（单元+集成）

**验收标准**：
- [x] `stablecoin_supply` 稳定写入Feature Store
- [x] `liquidation_stream` 通过Binance Futures `!forceOrder@arr` WebSocket实时采集
- [x] `LiquidationAggregator` 1分钟桶聚合正确（bucket边界对齐验证）
- [x] 数据延迟可观测（local_receive_ts vs source_ts）
- [ ] `exchange_flow`（交易所净流入/流出）- 未来增强项，待接入CoinGecko或等价数据源

---

### Task 2.3 — 事件公告爬虫

**状态**：✅ 已完成（2026-03-25）

**交付物**：
- `adapters/announcements/binance_crawler.py`
  - 解析Binance公告RSS/API
  - 分类：`ListingEvent`、`DelistingEvent`、`MaintenanceEvent`、`OtherEvent`
  - 写入event_log（stream_key=`announcements`）
- `adapters/announcements/models.py`
  - `RawAnnouncement` 统一数据模型（所有字段 Optional）
  - `dedup_key` 属性（URL尾部或 content hash）
  - `classify_announcement()` 共享分类函数
- `adapters/announcements/ws_source.py`
  - WebSocket 主数据源（`wss://api.binance.com/sapi/wss?topic=com_announcement_en`）
  - 核心方法：`connect()`, `subscribe()`, `recv_one()`, `recv_async_iterator()`
- `adapters/announcements/html_source.py`
  - HTML 回退数据源（Binance CMS API）
  - `fetch_initial()` 返回 `list[RawAnnouncement]`
- `trader/tests/test_announcements_crawler*.py`
  - 74个测试全部通过（单元+集成+e2e）

**验收标准**：
- [x] 新上币公告能被正确分类为ListingEvent
- [x] 事件写入event_log，可被策略订阅
- [x] WebSocket-first 架构，失败自动降级到 HTML
- [x] RawAnnouncement 统一模型，WS/HTML 双源兼容

---

### Task 2.4 — 基础信号层（趋势 + 价量）

**状态**：✅ 已完成（2026-03-25）

**交付物**：
- `core/domain/signals/trend_signals.py`
  - EMA交叉、价格动量、布林带位置
- `core/domain/signals/price_volume_signals.py`
  - 成交量扩张检测、波动率压缩检测
- Signal Sandbox：`scripts/tools/signal_sandbox.py`
  - 输入：历史Feature Store数据
  - 输出：信号时序、未来函数检测报告

**验收标准**：
- [x] 所有信号计算无IO（Core Plane约束）
- [x] Signal Sandbox能检测出未来函数泄漏
- [x] 信号输出为标准化`Signal` dataclass

---

### Task 2.5 — 资金结构信号

**状态**：✅ 已完成（2026-03-25）

**交付物**：
- `core/domain/signals/capital_structure_signals.py`
  - Funding rate z-score（滚动窗口）
  - OI变化率 + 价格背离检测
  - 多空比异常检测

**验收标准**：
- [x] 依赖Feature Store中的funding_rate/OI数据
- [x] z-score计算有单测（边界：窗口不足时返回None）

---

## 五、Phase 3 — 增强与自动化

**前置条件**：Phase 2完成，系统有稳定信号输出。

**目标**：完善系统可观测性和自动化能力，为Phase 4策略管理奠定基础。

---

### Task 3.1 — PG投影读模型

**状态**：✅ 已完成（2026-03-25）

**交付物**：
- `adapters/persistence/postgres/projectors/order_projector.py`
  - `get_order_by_client_order_id()` 索引查询优化
- `adapters/persistence/postgres/projectors/position_projector.py`
  - `_apply_position_increased()` 重构
- `adapters/persistence/postgres/projectors/risk_projector.py`
  - `EventType` 枚举引入，统一事件类型定义
- `adapters/persistence/postgres/migrations/003_projections.sql`
  - 投影表结构定义

**验收标准**：
- [x] OrderProjector 索引查询优化，O(1) 查找
- [x] PositionProjector 重构，状态更新逻辑清晰
- [x] RiskProjector EventType 枚举避免字符串硬编码
- [x] 44个单元测试全部通过
- [x] 766 全量测试通过

### Task 3.2 — Escape Time模拟器

- 输入：当前持仓 + 实时深度
- 输出：在不同滑点约束下的最快平仓时间估算
- 用于风控决策（是否允许建仓）

### Task 3.3 — Replay Runner

- 从event_log重放历史事件序列
- 用于回归测试、场景复现、策略回测

### Task 3.4 — AI治理接口（HITL）

- AI建议接口：接收结构化提案（参数变更、风控阈值调整）
- Human-in-the-Loop确认流程
- 审计日志：trace_id + 输入上下文 + 输出结果 + 执行结果

---

## 六、Phase 4 — 策略管理与AI共创

**前置条件**：Phase 3完成，系统有完整信号输出和回放能力。

**目标**：实现策略全生命周期管理、热插拔、AI辅助策略开发。

### Phase 4 任务依赖图

```
Task 0 (StrategyPlugin协议) ──────────────────────────────┐
       │                                                   │
       ▼                                                   │
Task 4.1 (StrategyRunner) ──────────────┐                  │
       │                                 │                  │
       ▼                                 │                  │
Task 4.2 (StrategyEvaluator) ──────────▶ Task 4.6 (端到端集成)
       │                                 ▲                  │
       ▼                                 │                  │
Task 4.3 (热插拔) ──────────────────────┘                  │
       │                                                    │
       ▼                                                    │
Task 4.4 (AI策略生成) ─────────────────▶ Task 4.6 ──────────┘
       │
       ▼
Task 4.5 (AI聊天界面) ─────────────────▶ Task 4.6
```

### 与现有系统对接架构

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

---

### Task 0 — StrategyPlugin 协议定义（基础设施）

**背景**：所有策略必须实现统一协议，是AI生成代码、StrategyRunner加载、热插拔切换的基础。

**交付物**：
- `core/application/strategy_protocol.py`
  - `MarketData` dataclass：市场数据协议
  - `Signal` dataclass扩展：增加 `confidence: float` (0.0-1.0) 字段
  - `StrategyPlugin` 协议完整定义：
    ```python
    from typing import Protocol, Dict, Any, Optional, Literal

    class StrategyPlugin(Protocol):
        """策略插件协议 - 所有可执行策略必须实现此协议"""

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
  - `StrategyResourceLimits` dataclass：
    ```python
    @dataclass(slots=True)
    class StrategyResourceLimits:
        max_memory_mb: int = 512
        max_concurrent_orders: int = 10
        max_order_rate_per_minute: int = 60
        timeout_seconds: int = 30
    ```
- 协议兼容性单测：验证现有 `Signal` 模型可兼容

**验收标准**：
- [ ] 协议定义独立于执行器，可被AI生成代码引用
- [ ] `risk_level` 属性用于风险引擎集成
- [ ] 资源限制可配置，支持策略级隔离

---

### Task 4.1 — StrategyRunner 策略执行器

**状态**：✅ 核心代码已实现 (2026-03-30)，待测试验证

**背景**：当前仅有策略注册/版本管理，无真正执行策略的运行时组件。

**已交付**：
- `services/strategy_runner.py` ✅
  - `StrategyPlugin` 协议（`on_tick()`, `on_fill()`, `on_cancel()` 钩子）✅
  - `StrategyRunner` 类：
    - `load_strategy(strategy_id, version, module_path)` — 动态加载策略代码 ✅
    - `start()` / `stop()` / `pause()` / `resume()` — 生命周期控制 ✅
    - `tick(strategy_id, market_data)` — 驱动策略Tick循环 ✅
  - 每个策略运行在独立 `asyncio.Task` 中 ✅
  - 异常隔离：单策略崩溃不影响其他策略（10次错误自动标记ERROR）✅
- `api/routes/strategies.py` 增强：
  - `POST /strategies/{id}/load` — 加载策略代码 ✅
  - `POST /strategies/{id}/unload` — 卸载策略 ✅
  - `POST /strategies/{id}/start` — 启动策略执行 ✅
  - `POST /strategies/{id}/stop` — 停止策略执行 ✅
  - `POST /strategies/{id}/pause` — 暂停策略 ✅
  - `POST /strategies/{id}/resume` — 恢复策略 ✅
  - `GET /strategies/{id}/status` — 获取运行状态 ✅
  - `GET /strategies/running` — 列出所有已加载策略 ✅
- `tests/test_strategy_runner.py` ✅
  - 加载/卸载测试（5个）
  - 生命周期控制测试（7个）
  - Tick驱动测试（5个）
  - 异常隔离测试（2个）
  - 回调通知测试（3个）
  - 查询功能测试（3个）
  - 关闭测试（1个）

**待完成**：
- [ ] 运行单测并验证通过
- [x] 与OMS对接：信号回调 → OMS.submit_order() ✅ (Task 11-15, 2026-04-20)
- [x] 与KillSwitch对接：策略级风险检查 ✅ (Task 11-15, 2026-04-20)
- [x] 集成 `StrategyResourceLimits` 资源限制 ✅ (Task 11-15, 2026-04-20)

**验收标准**：
- [ ] 策略可通过API启动和停止
- [ ] 策略崩溃不影响系统稳定性（单策略崩溃后其他策略正常运行率 = 100%）
- [ ] 策略状态可查询（RUNNING/STOPPED/ERROR）
- [ ] P0回归测试不回归
- [ ] API响应时间 P99 < 500ms

---

### Task 4.2 — StrategyEvaluator 策略评估器

**背景**：策略部署前需要回测验证，运行中需要实时评估。

**交付物**：
- `services/strategy_evaluator.py`
  - `BacktestEngine` 类：
    - 输入：策略代码 + 历史Feature Store数据 + 时间范围
    - 数据质量验证：缺失值检查、异常值检测、时间戳连续性
    - 输出：`BacktestReport`（PnL曲线、夏普率、最大回撤、胜率）
    - 性能要求：1年数据回测 < 1分钟
  - `LiveEvaluator` 类：
    - 实时计算：当日PnL、持仓盈亏、滑点统计
  - `StrategyMetrics` dataclass：
    ```python
    @dataclass(slots=True)
    class StrategyMetrics:
        total_pnl: Decimal
        sharpe_ratio: float
        max_drawdown: float
        win_rate: float
        avg_win_loss_ratio: float
        total_trades: int
        avg_slippage_bps: float
    ```
  - `FeatureStorePort` 协议（Feature Store集成）：
    ```python
    class FeatureStorePort(Protocol):
        async def get_historical_features(
            self,
            feature_names: List[str],
            start_time: datetime,
            end_time: datetime,
            symbol: str
        ) -> pd.DataFrame: ...
    ```
- `api/routes/strategies.py` 增强：
  - `POST /strategies/{id}/backtest` — 触发回测
  - `GET /strategies/{id}/metrics` — 获取实时指标
- 单测：回测引擎准确性、指标计算边界条件、数据质量验证

**验收标准**：
- [ ] 回测报告包含夏普率、最大回撤、胜率
- [ ] 实时指标可通过API查询
- [ ] 数据质量验证能检测出缺失值和异常值
- [ ] 指标计算时间 < 1分钟（1年数据）

---

### Task 4.3 — 策略热插拔机制

**背景**：策略更新需要停机部署，无法满足快速迭代需求。

**交付物**：
- `services/strategy_hotswap.py`
  - `StrategyLoader` 类：
    - `load(code_ref)` — 从文件/git/AI生成代码动态加载
    - `unload(strategy_id)` — 卸载并清理资源
    - 代码安全验证（AST分析，禁止危险模块导入）
  - `StrategyHotSwapper` 类：
    - `swap(old_id, new_id, mode)` — 策略切换
    - 模式：`IMMEDIATE`（立即切换）/ `GRADUAL`（灰度）/ `WAIT_ORDERS`（等待挂单成交）
    - **热切换状态机**：
      ```python
      class HotswapStateMachine:
          """热切换状态机"""
          states = {"ACTIVE", "PAUSING", "ORDERS_PENDING", "MIGRATING", "INACTIVE"}
          # ACTIVE + hotswap_request → PAUSING
          # PAUSING + all_orders_filled → ORDERS_PENDING
          # PAUSING + timeout → CANCELLING_ORDERS
          # CANCELLING_ORDERS + all_cancelled → ORDERS_PENDING
          # ORDERS_PENDING + positions_transferred → MIGRATING
          # MIGRATING + new_strategy_started → INACTIVE (old) / ACTIVE (new)
      ```
  - `VersionManager` 类：
    - `deploy_with_rollback()` — 部署后监控，异常自动回滚
    - 回滚触发条件：错误率>5%、PnL异常、超时无响应
- `api/routes/deployments.py` 增强：
  - `POST /deployments/{id}/hotswap` — 热切换策略版本
  - `POST /deployments/{id}/rollback` — 手动回滚
- 单测：切换流程、状态迁移、自动回滚
- **混沌测试场景**：
  - 场景4：策略运行中强制终止 → 验证自动重启 + 状态恢复

**验收标准**：
- [ ] 策略更新无需重启系统
- [ ] 切换时挂单正确处理（成交或取消）
- [ ] 持仓状态正确迁移到新策略
- [ ] 异常时自动回滚到旧版本
- [ ] 代码安全验证阻止危险导入
- [ ] 混沌测试：强制终止后自动恢复

---

### Task 4.4 — AI策略生成服务

**背景**：人工编写策略效率低，需要AI辅助生成策略代码。

**交付物**：
- `insight/ai_strategy_generator.py`
  - `AIStrategyGenerator` 类：
    - `generate(requirements, features)` — 调用LLM生成策略代码
    - `validate_syntax(code)` — 语法验证
    - `extract_metadata(code)` — 提取策略描述、参数Schema
  - `LLMClientPort` 接口抽象：支持OpenAI/Anthropic/本地模型
  - Prompt模板：包含项目架构约束、可用特征列表、输出格式要求
- `insight/code_sandbox.py`
  - `SafeCodeExecutor` 类：
    - AST分析检测危险操作（**扩展禁止列表**）：
      ```python
      FORBIDDEN_IMPORTS = {
          "os", "sys", "subprocess", "requests", "aiohttp",
          "urllib", "urllib3", "http", "socket", "ftplib",
          "eval", "exec", "compile", "open", "file",
          # 禁止动态代码生成和网络调用
      }
      ```
    - 无限循环检测
    - 内存泄漏模式检测
    - 执行超时保护
- `insight/ai_audit_log.py`
  - 记录：输入需求、生成代码、验证结果、审批状态
- 单测：语法验证、危险代码检测（含网络调用检测）、Prompt模板正确性

**验收标准**：
- [ ] AI生成的代码符合StrategyPlugin协议
- [ ] 危险代码（os/subprocess/requests/aiohttp）被拦截
- [ ] 网络调用（requests/aiohttp/urllib）被检测并拦截
- [ ] 生成记录可审计追溯（trace_id + 输入上下文 + 输出结果）
- [ ] 支持多LLM后端切换

---

### Task 4.5 — AI策略聊天界面

**背景**：需要自然语言接口让Trader与AI共同开发策略。

**交付物**：
- `insight/chat_interface.py`
  - `StrategyChatInterface` 类：
    - `chat(trader_id, message)` — 处理自然语言输入
    - 意图识别：`GENERATE_STRATEGY` / `MODIFY_PARAMS` / `CHECK_STATUS` / `REQUEST_BACKTEST`
    - 上下文管理：维护对话历史，支持多轮交互
  - `ChatSession` dataclass：
    ```python
    @dataclass(slots=True)
    class ChatSession:
        session_id: str
        trader_id: str
        context: Dict[str, Any]  # 当前讨论的策略、参数等
        history: List[ChatMessage]
        created_at: datetime
    ```
- `api/routes/chat.py`：
  - `POST /chat/message` — 发送消息
  - `GET /chat/history/{session_id}` — 获取对话历史
  - `GET /chat/sessions` — 获取活跃会话列表
- 与HITL Governance集成：
  - AI生成策略 → 提交审批 → Trader确认 → 注册部署
- 单测：意图识别准确性、上下文保持、审批流程

**验收标准**：
- [ ] 支持自然语言描述策略需求
- [ ] AI生成策略后自动提交HITL审批
- [ ] 对话历史可查询
- [ ] 审批通过后策略自动注册到管理模块

---

### Task 4.6 — 策略管理端到端集成

**背景**：将上述组件整合为完整的策略管理闭环。

**交付物**：
- `services/strategy_lifecycle_manager.py`
  - `StrategyLifecycleManager` 类：
    - `create_from_chat()` — 从聊天创建策略
    - `validate_and_deploy()` — 验证 → 回测 → 审批 → 部署
    - `monitor_and_evaluate()` — 运行时监控 → 指标计算
    - `hot_update()` — 热更新 → 回滚保护
  - 完整生命周期：`DRAFT → VALIDATED → BACKTESTED → APPROVED → RUNNING → STOPPED`
- 端到端测试：
  - 场景1：AI生成策略 → 审批 → 部署 → 运行 → 停止
  - 场景2：策略运行中热更新 → 异常 → 自动回滚
  - 场景3：策略评估指标计算 → 告警触发
  - 场景4（混沌测试）：策略运行中强制终止 → 验证自动重启 + 状态恢复
- 文档更新：
  - `docs/STRATEGY_MANAGEMENT_GUIDE.md` — 策略管理用户手册
  - `docs/AI_STRATEGY_WORKFLOW.md` — AI策略共创工作流

**验收标准**：
- [ ] 完整生命周期流程可走通
- [ ] 端到端测试覆盖主要场景（4个场景）
- [ ] 用户文档完整
- [ ] 策略崩溃不影响系统稳定性（单策略崩溃后其他策略正常运行率 = 100%）
- [ ] 系统API响应时间 P99 < 500ms

---

## 七、工程执行约束（所有Phase通用）

1. **Core Plane禁止IO**：`core/` 下所有新代码不得有网络/DB/文件IO，不得读环境变量。
2. **幂等优先**：所有写操作必须幂等，重复调用结果一致。
3. **测试门禁**：每个Task交付必须包含单测，P0回归不得回归。
4. **类型注解**：所有函数签名和类属性必须有类型注解。
5. **结构化日志**：关键状态迁移必须打结构化日志，含`trace_id`。
6. **Fail-Closed**：异常处理必须Fail-Closed，禁止裸`except: pass`。
7. **PR包格式**：功能描述 + 变更文件清单 + 测试结果 + 风险与回滚方案。

---

## 八、里程碑检查点

| 里程碑 | 完成条件 | 对应Tasks |
|--------|----------|-----------|
| M1: 安全闭环 | Phase 1全部Task通过CI | 1.1–1.6 |
| M2: 数据就绪 | Funding/OI/链上数据稳定写入Feature Store | 2.1–2.2 |
| M3: 信号就绪 | 至少3类信号通过Signal Sandbox验证 | 2.3–2.5 |
| M4: 可试运行 | M1+M3完成 + Reconciler无漂移告警 + 监控正常 | 全部Phase 1+2 |
| M5: 协议就绪 | StrategyPlugin协议定义完整，AI/Runner/热插拔可引用 | 0 |
| M6: 策略可执行 | StrategyRunner可启动/停止策略，评估器输出指标 | 4.1–4.2 |
| M7: 热插拔就绪 | 策略更新无需重启，异常自动回滚 | 4.3 |
| M8: AI共创可用 | AI可生成策略代码，聊天界面可用，HITL审批集成 | 4.4–4.6 |

---

## 九、当前推荐起点

**立即开始**：Task 1.1（Feature Store）和 Task 1.2（Reconciler）可并行开发，互不依赖。

**Phase 1-3 顺序建议**：
```
Task 1.1 (Feature Store)  ─┐
Task 1.2 (Reconciler)     ─┤─► Task 1.5 (监控) ─► Task 1.6 (事件溯源)
Task 1.3 (深度检查)        ─┤
Task 1.4 (时间窗口)        ─┘
```

Task 1.3 和 1.4 体量小，可穿插在 1.1/1.2 开发间隙完成。

**Phase 4 顺序建议**（Phase 3 完成后）：
```
Task 0  (StrategyPlugin协议) ──────────────────────────────┐
       │                                                   │
       ▼                                                   │
Task 4.1 (StrategyRunner) ──────────────┐                  │
       │                                 │                  │
       ▼                                 │                  │
Task 4.2 (StrategyEvaluator) ──────────▶ Task 4.6 (端到端集成)
       │                                 ▲                  │
       ▼                                 │                  │
Task 4.3 (热插拔) ──────────────────────┘                  │
       │                                                    │
       ▼                                                    │
Task 4.4 (AI策略生成) ─────────────────▶ Task 4.6 ──────────┘
       │
       ▼
Task 4.5 (AI聊天界面) ─────────────────▶ Task 4.6
```

依赖关系：
- Task 0（协议定义）是所有Phase 4任务的前置依赖
- Task 4.1（执行器）依赖 Task 0（协议）
- Task 4.2（评估器）依赖 Task 4.1（执行器）
- Task 4.3（热插拔）依赖 4.1（执行器）+ 4.2（评估器）
- Task 4.4（AI生成）依赖 Task 0（协议）+ Task 4.1（执行器）
- Task 4.5（聊天界面）依赖 4.4（AI生成服务）
- Task 4.6（端到端集成）依赖全部 0–4.5

---

## 十、Phase 5 — 回测框架升级（引入成熟开源框架）

**前置条件**：Phase 4 完成，系统具备策略执行和生命周期管理能力。

**背景**：经过专业评估，当前自研回测模块存在以下问题：
1. **前瞻偏差**：使用当前 bar 收盘价执行而非下一 bar 开盘价
2. **滑点方向错误**：始终加滑点，而非根据买卖方向调整
3. **不支持止盈/止损**：无法正确测试带风控的策略
4. **无样本外验证**：缺乏交叉验证和前向分析支持

这些问题在成熟开源框架（Backtrader/VectorBT）中已被修复和验证。

**原则**：
- 回测引擎是**基础设施**，应使用成熟方案，不重复造轮子
- 差异化竞争点在于：策略逻辑、风险管理、交易执行、与项目架构的深度集成
- 自研组件保持：StrategyLifecycleManager、StrategyRunner、策略协议

---

### Task 5.1 — 成熟回测框架选型与集成架构设计

**优先级**：P0 | **工作量**：1 人天

**交付物**：
- **框架对比报告**：
  | 框架 | 语言 | 成熟度 | GitHub Stars | 特点 |
  |------|------|--------|--------------|------|
  | Backtrader | Python | ⭐⭐⭐⭐⭐ | 10k+ | 功能完整，文档好，社区活跃 |
  | VectorBT | Python | ⭐⭐⭐⭐ | 3k+ | 向量化执行，速度快，适合快速原型 |

- **接口契约定义**：
  ```python
  class BacktestEnginePort(Protocol):
      """回测引擎端口 - 统一多框架接口"""
      async def run_backtest(
          self,
          strategy: StrategyPlugin,
          config: BacktestConfig,
          data_provider: DataProviderPort,
      ) -> BacktestReport: ...
  ```

- **数据模型映射**：现有 `BacktestReport` 与框架输出的映射关系

**验收标准**：
- [ ] 完成框架选型报告（Backtrader 首选）
- [ ] 定义 `BacktestEnginePort` 协议
- [ ] 确定集成架构设计

---

### Task 5.2 — Backtrader 适配层开发

**优先级**：P0 | **工作量**：5 人天

**子任务分解**：

| 子任务 | 工作量 | 说明 |
|--------|--------|------|
| 5.2.1 数据源适配器 | 1 人天 | FeatureStore → Backtrader Data Feed |
| 5.2.2 策略包装器 | 1.5 人天 | StrategyPlugin → Backtrader Strategy |
| 5.2.3 订单执行模拟 | 1 人天 | 正确实现滑点、手续费、止盈止损 |
| 5.2.4 结果转换器 | 0.5 人天 | Backtrader 结果 → BacktestReport |
| 5.2.5 单元测试 | 1 人天 | 适配层测试覆盖 |

**关键技术实现**：
```python
# 滑点模型 - 方向感知
class DirectionAwareSlippage(bt.SlippagePerc):
    def _apply_slippage(self, data, size, price):
        if size > 0:  # 买入
            return price * (1 + self.p.perc)
        else:  # 卖出
            return price * (1 - self.p.perc)

# 市价单执行 - 下一 bar 开盘价
class NextBarOpenExecutor:
    def next(self):
        for order in self.get_orders():
            if order.is_market():
                # 在下一 bar 开盘价执行
                self.execute(order, self.data.open[0])

# 数据源适配器
class BacktraderDataFeedConverter:
    """FeatureStore → Backtrader Data Feed"""
    
    def convert(self, symbol: str, df: pd.DataFrame) -> bt.feeds.PandasData:
        """转换 K 线数据为 Backtrader 格式"""
        return bt.feeds.PandasData(
            dataname=df,
            datetime='timestamp',
            open='open',
            high='high', 
            low='low',
            close='close',
            volume='volume',
            openinterest=-1
        )

# 策略包装器
class StrategyWrapper(bt.Strategy):
    """StrategyPlugin → Backtrader Strategy"""
    
    def __init__(self, plugin: StrategyPlugin, config: Dict):
        self.plugin = plugin
        self.config = config
        self.indicators = self._setup_indicators()
    
    def _setup_indicators(self):
        """设置 Backtrader 指标"""
        return {
            'sma20': bt.indicators.SMA(self.data.close, period=20),
            'sma50': bt.indicators.SMA(self.data.close, period=50),
        }
    
    def next(self):
        """在每个 bar 执行策略"""
        market_data = self._convert_to_market_data()
        signal = self.plugin.on_market_data(market_data)
        if signal:
            self._execute_signal(signal)
```

**交付物**：
- `services/backtesting/backtrader_adapter.py` - Backtrader 适配层
- `services/backtesting/performance_aggregator.py` - 结果聚合
- `services/backtesting/config.py` - 配置数据类

**验收标准**：
- [ ] 下一 bar 开盘价执行（消除前瞻偏差）
- [ ] 方向感知滑点
- [ ] 止盈/止损支持
- [ ] 单测覆盖适配层

---

### Task 5.3 — 回测结果标准化与可视化

**优先级**：P1 | **工作量**：2-3 人天

**交付物**：
- `services/backtesting/report_formatter.py`
  - `StandardizedBacktestReport` 数据类：
    ```python
    @dataclass(slots=True)
    class StandardizedBacktestReport:
        # 收益指标
        total_return: float
        annual_return: float
        monthly_returns: List[float]
        
        # 风险指标
        max_drawdown: float
        max_drawdown_duration: int  # days
        var_95: float  # 95% VaR
        
        # 风险调整收益
        sharpe_ratio: float
        sortino_ratio: float
        calmar_ratio: float
        
        # 交易统计
        total_trades: int
        win_rate: float
        profit_factor: float
        avg_trade_duration: float  # hours
        
        # 元信息
        framework: str  # "backtrader" | "vectorbt"
        data_range: Tuple[datetime, datetime]
    ```

- `services/backtesting/visualizer.py`
  - `plot_equity_curve()` - 资金曲线
  - `plot_drawdown()` - 回撤曲线
  - `plot_monthly_heatmap()` - 月度收益热力图
  - `plot_trade_markers()` - 交易标记图
  - `plot_returns_distribution()` - 收益分布图

- `api/routes/backtest.py` - 回测 API 端点

**验收标准**：
- [ ] 标准化报告包含所有风险收益指标
- [ ] 添加 Buy & Hold 基准对比
- [ ] 可视化图表直观清晰

---

### Task 5.4 — 样本外验证与交叉验证框架

**优先级**：P0 | **工作量**：4 人天

**子任务分解**：

| 子任务 | 工作量 | 说明 |
|--------|--------|------|
| 5.4.1 Walk-Forward Analysis | 1.5 人天 | 滚动窗口优化 + 样本外验证 |
| 5.4.2 K-Fold 交叉验证 | 1 人天 | 时间序列 K-Fold（非随机分割） |
| 5.4.3 参数敏感性分析 | 1 人天 | 参数网格扫描 + 稳定性评估 |
| 5.4.4 过拟合检测 | 0.5 人天 | 样本内 vs 样本外差异检测 |

**关键实现**：
```python
class WalkForwardAnalyzer:
    """Walk-Forward 分析器"""
    
    def analyze(
        self,
        strategy_class: Type[StrategyPlugin],
        param_grid: Dict[str, List[Any]],
        train_period: timedelta,
        test_period: timedelta,
        n_splits: int = 5,
    ) -> WalkForwardReport:
        """
        时间线: | train1 | test1 | train2 | test2 | ... |
        """
        results = []
        for i in range(n_splits):
            # 1. 训练期优化参数
            best_params = self._optimize(strategy_class, param_grid, ...)
            # 2. 测试期验证
            test_result = self._backtest(strategy_class, best_params, ...)
            results.append(test_result)
        
        return WalkForwardReport(
            in_sample_metrics=self._aggregate_train(),
            out_of_sample_metrics=self._aggregate_test(),
            overfitting_score=self._calc_overfitting(),
        )
```

**验收标准**：
- [ ] Walk-Forward Analysis 可用
- [ ] K-Fold 交叉验证可用
- [ ] 过拟合检测输出

---

### Task 5.5 — 与 StrategyLifecycleManager 集成

**优先级**：P0 | **工作量**：2.5 人天

**交付物**：
- 状态机扩展：
  ```
  DRAFT → VALIDATED → BACKTESTING → BACKTESTED → APPROVED → RUNNING
                            ↓
                         FAILED (可重试)
  ```

- 自动审批规则：
  ```python
  @dataclass
  class AutoApprovalRules:
      min_sharpe: float = 1.0
      max_drawdown_pct: float = 20.0
      min_trades: int = 30
      min_win_rate: float = 0.4
      max_overfitting_score: float = 0.3
  ```

- API 端点：
  - `POST /v1/strategies/{id}/backtest` - 触发回测
  - `GET /v1/strategies/{id}/backtest/{backtest_id}` - 获取回测结果
  - `POST /v1/backtests/parameter_sweep` - 参数扫描
  - `GET /v1/backtests/compare` - 策略对比

**验收标准**：
- [ ] 策略生命周期中可触发回测
- [ ] 回测结果自动关联策略版本
- [ ] 支持参数扫描和策略对比

---

### Task 5.6 — 回测数据管道优化

**优先级**：P1 | **工作量**：2 人天

**交付物**：
- `services/backtesting/data_pipeline.py`
  ```
  FeatureStore → DataLoader → DataValidator → Cache → BacktestEngine
                    ↓
              QualityReport
  ```

- 数据质量检查：
  - 数据对齐正确性验证
  - 缺口数据明确标记
  - 存活者偏差警告机制
  - 并行回测支持

**验收标准**：
- [ ] 数据缓存层避免重复加载
- [ ] 数据预检验
- [ ] 多策略并行回测

---

### Task 5.7 — 自研回测模块归档

**优先级**：P2 | **工作量**：1 人天

**交付物**：
- `services/strategy_evaluator_legacy.py` - 重命名自研模块
- 迁移指南文档
- 短期兼容层（避免现有代码 break）
- `@deprecated` 注释

**验收标准**：
- [ ] 自研模块标记为 deprecated
- [ ] 迁移指南完整

---

### Task 5.8 — 回测框架测试套件

**优先级**：P0 | **工作量**：2 人天

**目标**：确保回测结果正确性，验证框架集成质量

**交付物**：

| 测试类型 | 说明 | 验证方法 |
|----------|------|----------|
| 前瞻偏差测试 | 验证信号在下一 bar 开盘价执行 | Mock 策略 + 打印执行时间戳 |
| 滑点方向测试 | 验证买卖滑点方向正确 | 买单执行价>信号价，卖单执行价<信号价 |
| 止盈止损测试 | 验证 bar 内触发逻辑 | 设置 TP/SL，检查是否在正确 bar 触发 |
| 手续费计算测试 | 验证手续费计算准确 | 入场+出场手续费 = 配置费率 |
| 基准对比测试 | 与已知策略结果对比 | 对比简单均线策略的买入持有收益 |

**正确性验证量化标准**：
```python
CORRECTNESS_TESTS = {
    "前瞻偏差测试": "信号在下一bar开盘价执行",
    "滑点方向测试": "买入加滑点(>0), 卖出减滑点(<0)", 
    "止盈止损测试": "TP触及返回profit>0, SL触及返回profit<0",
    "手续费计算测试": "|计算手续费 - 预期手续费| < 0.01",
    "基准对比测试": "|回测收益 - 基准收益| < 1%"
}
```

**验收标准**：
- [ ] 前瞻偏差测试通过 - 信号在下一 bar 开盘价执行
- [ ] 滑点方向测试通过 - 买入加滑点，卖出减滑点
- [ ] 止盈止损逻辑测试通过 - bar 内正确触发
- [ ] 手续费计算误差 < 0.01

---

### Task 5.9 — 性能基准测试

**优先级**：P1 | **工作量**：1 人天

**目标**：确保回测性能满足生产要求

**性能指标目标**：

| 指标 | 目标 | 验证数据集 |
|------|------|-----------|
| 1年数据回测 | < 30秒 | BTC/USDT 1分钟 K 线 |
| 5年数据回测 | < 2分钟 | 多标的 1H K 线组合 |
| 参数优化 (100组) | < 10分钟 | EMA 交叉策略参数网格 |
| 内存占用 | < 2GB | 5年 + 多标的 |

**性能测试方法**：
```python
# 性能基准测试用例
class PerformanceBenchmark:
    """性能基准测试"""
    
    def test_1year_backtest(self):
        """1年数据回测 < 30秒"""
        start = time.time()
        result = backtester.run(strategy, config, "2024-01-01", "2025-01-01")
        duration = time.time() - start
        assert duration < 30, f"1年回测耗时 {duration}s，超过30s目标"
    
    def test_memory_usage(self):
        """内存占用 < 2GB"""
        import tracemalloc
        tracemalloc.start()
        backtester.run(strategy, config, "2020-01-01", "2025-01-01")
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert peak < 2 * 1024 * 1024 * 1024, f"内存峰值 {peak/GB:.2f}GB，超过2GB目标"
```

**验收标准**：
- [ ] 1年数据回测 < 30秒
- [ ] 5年数据回测 < 2分钟
- [ ] 参数优化 (100组) < 10分钟
- [ ] 内存占用 < 2GB
- [ ] 性能测试可重复执行

---

## Phase 5 依赖关系与优先级分组

### 优先级分组

```
Phase 5 — 回测框架升级 (约 20.5 人天)
├── P0 核心任务 (12.5 人天)
│   ├── 5.1 框架选型与架构设计 (1 人天)
│   ├── 5.2 Backtrader 适配层开发 (5 人天)
│   ├── 5.4 样本外验证框架 (4 人天) 
│   └── 5.8 回测框架测试套件 (2 人天)
├── P1 增强任务 (5-6 人天)
│   ├── 5.3 结果标准化与可视化 (2-3 人天)
│   ├── 5.5 生命周期管理集成 (2.5 人天)
│   └── 5.9 性能基准测试 (1 人天)
└── P2 收尾任务 (1 人天)
    └── 5.7 自研模块归档 (1 人天)
```

### 依赖关系图

```
Task 5.1 (选型与架构设计) ─┐
                           │
Task 5.2 (Backtrader适配) ─┼─► Task 5.3 (标准化与可视化) ─► Task 5.5 (集成)
                           │                                      │
Task 5.4 (交叉验证框架) ────┘                                      │
Task 5.8 (测试套件) ───────────────────────────────────────────────┘
Task 5.6 (数据管道优化) ───────────────────────────────────────────┘
Task 5.7 (归档自研模块) ───────────────────────────────────────────┘
```

---

## Phase 5 验收标准

| 里程碑 | 完成条件 | 关联任务 |
|--------|----------|----------|
| M9: 回测就绪 | Backtrader 适配层可用，消除前瞻偏差 | 5.1, 5.2, 5.8 |
| M10: 报告标准化 | 统一回测结果格式，可视化完整 | 5.3 |
| M11: 验证可信 | 支持样本外验证，过拟合检测 | 5.4, 5.8 |
| M12: 集成完成 | 与 StrategyLifecycleManager 集成，数据管道优化 | 5.5, 5.6 |

---

## Phase 5 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Backtrader 学习曲线 | 延期 | 先完成 POC 验证核心功能 |
| 数据格式不兼容 | 阻塞 | 优先实现数据适配器 (5.2.1) |
| 性能不达标 | 需优化 | 预留 Task 5.9 性能测试时间 |
| 样本外验证复杂 | 延期 | 参考 sklearn 时间序列分割 |

---

## 修订后工作量估算

| Task | 描述 | 工作量 | 优先级 |
|------|------|--------|--------|
| 5.1 | 框架选型与架构设计 | 1 人天 | P0 |
| 5.2 | Backtrader 适配层开发 | 5 人天 | P0 |
| 5.3 | 结果标准化与可视化 | 2-3 人天 | P1 |
| 5.4 | 样本外验证框架 | 4 人天 | P0 |
| 5.5 | 生命周期管理集成 | 2.5 人天 | P0 |
| 5.6 | 数据管道优化 | 2 人天 | P1 |
| 5.7 | 自研模块归档 | 1 人天 | P2 |
| **5.8** | **回测框架测试套件** | **2 人天** | **P0** |
| **5.9** | **性能基准测试** | **1 人天** | **P1** |

**总计：约 20.5 人天**

---

## 十一、Phase 7 — 风控穿透验证与策略正期望证明（新增）

> **定位**：从"功能齐全"到"可信赖系统"的关键一跳。
> 
> **核心原则**：不先写更多分析报告，从可证伪开始。
> 
> **两个验证目标**：
> 1. **风控不是摆设**：证明同一信号经过风控前后，输出真的发生可观察差异
> 2. **策略不是回测幻觉**：证明扣掉真实成本后，样本外仍有正期望

### 背景与问题定义

#### A. 风控验证现状与缺口

| 已完成 | 缺口 |
|--------|------|
| 深度检查单元测试（19 tests） | 缺少端到端"信号→风控→订单命运改变"的直接验证 |
| 时间窗口策略单元测试（29 tests） | 缺少对照组：深度充足时通过 vs 深度不足时拒绝 |
| RiskSizer 统一仓位计算（52 tests） | 缺少 Risk Intervention Rate 量化指标 |
| KillSwitch 级别定义（L0-L3） | 缺少 trace_id 全链路追踪 |
| 回撤/venue 联动去杠杆 | 缺少"缩单 vs 拒单 vs 停机"四类场景覆盖 |

**当前状态**：各模块单元测试通过，但没有人验证过"风控真的改变了多少下单结果"。

#### B. 策略正期望验证现状与缺口

| 已完成 | 缺口 |
|--------|------|
| WalkForwardAnalyzer / KFoldValidator / SensitivityAnalyzer | 工具存在，但未绑定上线流程 |
| QuantConnect Lean 适配层（修正滑点方向） | 没有要求每个策略必须回答"机制假设" |
| ExecutionSimulator（支持止盈止损） | 没有 1x/1.5x/2x 成本压测的标准化入口 |
| 标准化 BacktestReport | 没有影子模式验证（回测信号 vs 实盘信号 vs 成交偏差） |

**当前状态**：回测框架完整，但没有人验证过"这个策略扣成本后样本外还能活"。

#### C. 审计链路现状与缺口

| 已完成 | 缺口 |
|--------|------|
| Event Sourcing 到 PG，OMS 事件持久化 | AIAuditLog 当前是 InMemoryAuditLogStorage |
| 幂等订单，状态机可恢复 | /v1/events 和 /v1/snapshots/latest 仍是内存读模型 |
| Reconciler 漂移检测发布事件 | 缺少统一 decision_id 证据链 |

**当前状态**：有审计骨架，但控制面快照和 AI 审计链路未持久化。

---

### 目标与成功标准

#### 目标 1：风控真的改变下单结果

**验证方法**：风控穿透测试

**成功标准**（必须同时满足）：
1. **结果改变订单命运**：不是打印日志，而是 OMS 行为真的变了（没有下单/下单量变小/订单被取消/策略被阻塞）
2. **结果可回放**：每次拦截都留下 trace_id + signal_id + rule_name + action + original_size + approved_size
3. **有反例对照组**：深度充足时同一信号通过，深度不足时同一信号拒绝

**核心指标**：
```
Risk Intervention Rate = 被风控改变命运的信号数 / 总信号数
  = reject_rate + size_reduction_rate + killswitch_block_rate
```

#### 目标 2：策略扣成本后仍有正期望

**验证方法**：策略上线前 5 层验证门控

**成功标准**（必须同时满足）：
1. **成本后期望为正**：`Expectancy = avg_win * win_rate - avg_loss * loss_rate - avg_cost > 0`
2. **样本外仍活着**：Walk-Forward 测试 Sharpe 衰减 < 20%
3. **成本敏感性可控**：1.5x 成本后策略仍为正期望

**禁止先看这些**：
- 年化收益多高
- Sharpe 多漂亮
- 单段行情多惊艳

---

### 执行计划

#### Task 7.1 — 风控穿透测试矩阵（P0）

**目标**：创建端到端测试，验证每条风控规则真的会改变订单命运。

**交付物**：`trader/tests/test_risk_intervention_matrix.py`

**测试矩阵结构**：

| case_id | 信号输入 | 市场状态 | 账户状态 | 预期动作 | 实际动作 | trace_id |
|---------|----------|----------|----------|----------|----------|----------|
| TC-001 | BUY 1 BTC | 深度充足 | 正常 | PASS | PASS? | - |
| TC-002 | BUY 1 BTC | 深度薄、预估滑点超阈值 | 正常 | REJECT | REJECT? | - |
| TC-003 | BUY 1 BTC | OFF_PEAK 时段 | 正常 | size → 0.5x | size → 0.5x? | - |
| TC-004 | BUY 1 BTC | RESTRICTED 时段 | 正常 | REJECT | REJECT? | - |
| TC-005 | BUY 1 BTC | 日亏损超限 | 正常 | REJECT | REJECT? | - |
| TC-006 | BUY 1 BTC | KillSwitch L1 | 正常 | no new positions | blocked? | - |
| TC-007 | BUY 1 BTC | KillSwitch L2 | 正常 | strategy stopped | halted? | - |
| TC-008 | BUY 1 BTC | 对账漂移 | DIVERGED | block / escalate | blocked? | - |

**测试约束**：
- 使用 fake orderbook / fake signal / fake metrics，不依赖网络
- 每个 case 必须验证 `original_size != approved_size` 或 `passed == False`
- 每个 case 必须产生 `RiskInterventionRecord`

**验收标准**：
- [ ] 8+ 个确定性场景覆盖所有主要风控规则
- [ ] 每个 case 可回放（trace_id + signal_id + rule_name 记录）
- [ ] 反例对照组存在（深度充足 vs 深度不足）
- [ ] Risk Intervention Rate 指标可计算

---

#### Task 7.2 — RiskInterventionTracker 实现（P0）

**目标**：量化风控实际改变了多少下单结果。

**交付物**：`trader/core/domain/services/risk_intervention_tracker.py`

**核心类型**：
```python
@dataclass(slots=True)
class RiskInterventionRecord:
    signal_id: str
    strategy_id: str
    rule_name: str
    action: Literal["PASS", "REDUCE", "REJECT", "HALT"]
    original_size: float
    approved_size: float
    market_state_ref: str  # orderbook hash 或 timestamp
    trace_id: str
    timestamp: datetime


@dataclass
class RiskInterventionMetrics:
    total_signals: int
    passed_signals: int
    rejected_signals: int
    reduced_signals: int
    halted_signals: int
    reject_rate: float
    size_reduction_rate: float
    killswitch_block_rate: float
    intervention_rate: float  # 核心指标
```

**API**：
```python
class RiskInterventionTracker:
    def record(self, record: RiskInterventionRecord) -> None: ...
    def get_metrics(self, strategy_id: str | None = None) -> RiskInterventionMetrics: ...
    def get_records(self, strategy_id: str | None = None, limit: int = 100) -> list[RiskInterventionRecord]: ...
```

**验收标准**：
- [ ] 每次风控拦截自动记录（集成到 RiskEngine）
- [ ] `intervention_rate` 可按策略计算
- [ ] 记录可查询、可导出
- [ ] 单元测试覆盖

---

#### Task 7.3 — 策略上线前 5 层验证门控（P0）

**目标**：在 StrategyLifecycleManager 绑定验证门控，未通过的策略不允许进入 RUNNING。

**交付物**：`trader/services/strategy_validation_gate.py`

**5 层验证结构**：

```
Layer 1: 机制假设（必须回答 3 个问题）
  ├── Q1: 它为什么会赚钱？
  ├── Q2: 它靠什么市场机制赚钱？
  └── Q3: 什么情况下会失效？
  → 未回答的策略拒绝进入回测

Layer 2: 回测合规检查
  ├── 下一 bar 开盘价执行（消除前瞻偏差）
  ├── 方向感知滑点（买加/卖减）
  ├── 止盈/止损支持
  └── 手续费真实模型
  → 不满足的策略拒绝上线

Layer 3: 样本外验证
  ├── Walk-Forward Analysis（至少 5 split）
  ├── K-Fold 交叉验证（至少 5 fold）
  └── Sharpe 衰减 < 20%
  → 不满足的策略拒绝上线

Layer 4: 成本压测
  ├── 1x 成本：Expectancy > 0
  ├── 1.5x 成本：Expectancy > 0
  └── 2x 成本：记录边界
  → 1.5x 成本后为负的策略降级或拒绝

Layer 5: 影子模式验证
  ├── 信号触发率对比（回测 vs 影子）
  ├── sizing 变化对比
  └── 成交偏差监控
  → 偏差超阈值触发告警
```

**API**：
```python
@dataclass
class StrategyValidationReport:
    strategy_id: str
    layer1_mechanism: bool
    layer2_backtest合规: bool
    layer3_out_of_sample: bool
    layer4_cost_stress: bool
    layer5_shadow_mode: bool
    overall_passed: bool
    failed_layers: list[int]
    recommendations: list[str]


class StrategyValidationGate:
    async def validate(self, strategy_id: str) -> StrategyValidationReport: ...
    async def validate_layer1(self, strategy_id: str) -> tuple[bool, str | None]: ...
    async def validate_layer2(self, strategy_id: str) -> tuple[bool, str | None]: ...
    async def validate_layer3(self, strategy_id: str) -> tuple[bool, str | None]: ...
    async def validate_layer4(self, strategy_id: str) -> tuple[bool, str | None]: ...
    async def validate_layer5(self, strategy_id: str) -> tuple[bool, str | None]: ...
```

**与 StrategyLifecycleManager 集成**：
- 状态转换 `BACKTESTED → APPROVED` 前必须通过 `validate()`
- 未通过的策略停留在 `BACKTESTED` 状态，带 `failed_layers` 信息

**验收标准**：
- [ ] 每个策略上线前必须通过 Layer 1-4 验证
- [ ] Layer 5 影子模式可选但建议执行
- [ ] 未通过的策略明确标注 `failed_layers`
- [ ] 验证报告可查询、可导出

---

#### Task 7.4 — 成本压测标准化入口（P1）

**目标**：为每个策略提供 1x/1.5x/2x 成本压测的标准化入口。

**交付物**：`trader/services/backtesting/cost_stress_tester.py`

**API**：
```python
@dataclass
class CostStressResult:
    cost_multiplier: float
    expectancy: float
    sharpe_ratio: float
    max_drawdown: float
    passed: bool


class CostStressTester:
    def stress_test(
        self,
        backtest_result: BacktestResult,
        multipliers: list[float] = [1.0, 1.5, 2.0]
    ) -> list[CostStressResult]:
        """
        对回测结果执行成本压测
        
        Returns:
            每个成本倍数对应的性能指标
        """
```

**在 StrategyValidationGate Layer 4 调用**：
```python
# Layer 4: 成本压测
stress_results = cost_stress_tester.stress_test(backtest_result)
for result in stress_results:
    if result.cost_multiplier == 1.5 and result.expectancy <= 0:
        return False, "1.5x 成本后期望为负"
```

**验收标准**：
- [ ] 1x/1.5x/2x 成本压测一键执行
- [ ] 每个倍数的 Expectancy / Sharpe / MaxDrawdown 可查
- [ ] 1.5x 成本作为上线门槛
- [ ] 单元测试覆盖

---

#### Task 7.5 — 影子模式验证框架（P1）

**目标**：实现回测信号 vs 影子实盘信号 vs 实际成交的三者偏差比较。

**交付物**：`trader/services/shadow_mode_verifier.py`

**API**：
```python
@dataclass
class ShadowDeviationReport:
    signal_trigger_rate_diff: float      # 回测 vs 影子
    sizing_avg_diff: float               # sizing 平均偏差
    sizing_max_diff: float              # sizing 最大偏差
    execution_gap_avg: float            # 成交价偏差
    execution_gap_max: float           # 成交价最大偏差
    risk_block_rate_diff: float         # 风控拦截率差异
    overall_healthy: bool


class ShadowModeVerifier:
    async def verify(
        self,
        strategy_id: str,
        lookback_period: timedelta = timedelta(days=7)
    ) -> ShadowDeviationReport:
        """
        比较回测信号、影子实盘信号、实际成交
        """
```

**偏差阈值**：
- `signal_trigger_rate_diff > 20%` → 告警
- `sizing_avg_diff > 30%` → 告警
- `execution_gap_avg > 2x 回测滑点假设` → 告警
- `risk_block_rate_diff > 50%` → 告警

**验收标准**：
- [ ] 回测信号 vs 影子信号 vs 成交可对比
- [ ] 偏差超阈值时触发告警
- [ ] 报告可查询
- [ ] 单元测试覆盖

---

#### Task 7.6 — AIAuditLog 持久化（P1）

**目标**：将 AI 审计日志从内存存储迁移到 PostgreSQL。

**交付物**：
- `trader/adapters/persistence/postgres/ai_audit_storage.py`
- `trader/adapters/persistence/postgres/migrations/004_ai_audit.sql`

**表结构**：
```sql
CREATE TABLE ai_audit_log (
    id BIGSERIAL PRIMARY KEY,
    audit_id UUID NOT NULL UNIQUE,
    event_type VARCHAR(50) NOT NULL,
    strategy_id VARCHAR(100),
    input_context JSONB,
    generated_code TEXT,
    validation_result JSONB,
    approval_status VARCHAR(20),
    approval_reason TEXT,
    approver VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ,
    metadata JSONB
);

CREATE INDEX idx_ai_audit_strategy ON ai_audit_log(strategy_id);
CREATE INDEX idx_ai_audit_status ON ai_audit_log(approval_status);
CREATE INDEX idx_ai_audit_created ON ai_audit_log(created_at);
```

**迁移步骤**：
1. 创建 PG 表和迁移脚本
2. 实现 `PostgresAuditLogStorage` 实现 `AuditLogStorage` 协议
3. 将 `InMemoryAuditLogStorage` 替换为 `PostgresAuditLogStorage`
4. 保持接口兼容，现有代码无需修改

**验收标准**：
- [ ] AI 审计日志写入 PG
- [ ] 现有 `InMemoryAuditLogStorage` 接口兼容
- [ ] 查询接口正常工作
- [ ] 单元测试覆盖

---

#### Task 7.7 — 控制面快照持久化（P1）✅

**目标**：将 /v1/snapshots/latest 从内存读模型迁移到 PG 投影读模型。

**交付物**：
- ✅ `trader/adapters/persistence/postgres/snapshot_storage.py` - PostgresSnapshotStorage 实现
- ✅ `trader/services/event.py` - EventService 支持 dual storage backend
- ✅ `trader/tests/test_snapshot_storage.py` - 13 tests (1 skipped integration)

**迁移策略**：
1. ✅ 首先确保 PG 投影读模型完整（已有基础）
2. ✅ 将 /v1/events 和 /v1/snapshots/latest 的 fallback 链改为：
   - Primary: PG 投影读模型
   - Fallback: 内存（仅在 PG 不可用时）
3. ✅ 添加快照重建接口（通过 factory function）

**验收标准**：
- [x] /v1/snapshots/latest 优先从 PG 读取
- [x] 内存 fallback 仅在 PG 不可用时触发
- [x] 快照一致性可验证
- [x] 单元测试覆盖

**实现细节**：
- `PostgresSnapshotStorage` 提供 `save()`, `get_latest()`, `list_by_stream()`, `list_recent()`, `delete()`, `count()` 方法
- `EventService` 支持注入 `snapshot_storage` 参数，优先使用 PG 存储
- 迁移 SQL 已包含在 `snapshot_storage.py` 的 `MIGRATION_SQL` 常量中
- 同步方法 `get_latest_snapshot()` 使用 `run_coroutine_threadsafe` 在异步上下文中的兼容处理

---

#### Task 7.8 — 统一 DecisionTraceId 证据链（P2）

**目标**：强制每次策略决策产生统一 trace_id，串起完整证据链。

**交付物**：`trader/core/domain/models/decision_trace.py`

**核心类型**：
```python
@dataclass(frozen=True)
class DecisionTraceId:
    """统一决策追踪ID"""
    market_state_ref: str      # orderbook/data hash
    feature_version: str       # feature store version
    signal_id: str           # 信号ID
    decision_id: str          # 决策ID (UUID)
    risk_action: str          # PASS/REDUCE/REJECT/HALT
    order_intent_id: str      # 下单意图ID
    exchange_order_id: str | None  # 交易所订单ID（下单后填充）
    reconcile_result: str | None   # 对账结果（下单后填充）


@dataclass
class DecisionEvidence:
    """单次决策的完整证据链"""
    trace_id: DecisionTraceId
    market_state: dict
    signal: Signal
    risk_check: RiskCheckResult
    order_intent: OrderIntent | None
    exchange_order: Order | None
    reconcile_report: ReconcileReport | None
    timestamp: datetime
```

**在 event_log 中记录**：
```python
# 信号生成时
trace_id = DecisionTraceId(
    market_state_ref=hash(orderbook),
    feature_version=feature_store_version,
    signal_id=signal.signal_id,
    decision_id=uuid4(),
    ...
)
event_log.append("decision", trace_id=trace_id, ...)

# 风控检查后
event_log.append("risk_action", trace_id=trace_id, risk_action=action, ...)

# 下单后
event_log.append("order_intent", trace_id=trace_id, order_intent_id=..., ...)
```

**验收标准**：
- [ ] 每次信号决策产生唯一 trace_id
- [ ] trace_id 贯穿 signal → risk → order_intent → exchange_order → reconcile
- [ ] trace_id 可在 event_log 中查询
- [ ] 单元测试覆盖

---

### Phase 7 依赖关系

```
Task 7.1 (风控穿透测试矩阵)
         │
         ▼
Task 7.2 (RiskInterventionTracker) ──────────┐
         │                                     │
         │                                     ▼
         │                          Task 7.3 (5层验证门控)
         │                                     │
         │                                     ▼
         │                          Task 7.4 (成本压测入口)
         │                                     │
         │                                     ▼
         │                          Task 7.5 (影子模式验证)
         │
         ▼
Task 7.6 (AIAuditLog持久化) ────▶ Task 7.7 (控制面快照持久化)
         │
         ▼
Task 7.8 (统一DecisionTraceId)
```

---

### Phase 7 验收标准

| 里程碑 | 完成条件 | 关联任务 |
|--------|----------|----------|
| M13: 风控可证伪 | Risk Intervention Rate 可计算，8+ 场景通过 | 7.1, 7.2 |
| M14: 策略上线有门控 | 5 层验证门控集成到生命周期，未通过不可上线 | 7.3 |
| M15: 成本压测标准化 | 1x/1.5x/2x 成本压测一键执行 | 7.4 |
| M16: 影子模式可用 | 回测/影子/成交三者偏差可比较 | 7.5 |
| M17: AI审计持久化 | AIAuditLog 写入 PG | 7.6 |
| M18: 控制面快照持久化 | /v1/snapshots 从 PG 读取 | 7.7 |
| M19: 全链路 trace_id | 统一证据链贯穿每次决策 | 7.8 |

---

### Phase 7 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 风控穿透测试 mock 数据不真实 | 验证结果无效 | 使用真实历史 orderbook 数据片段 |
| 5 层验证门控太严格导致无策略可上线 | 阻塞业务 | 分阶段实施，先强制 L1-L4，L5 建议执行 |
| 影子模式需要实盘数据 | 无法在测试环境验证 | 提供回测模式下的影子验证功能 |
| AIAuditLog 迁移影响现有 AI 功能 | 功能回退 | 保持接口兼容，PG 失败时回退到内存 |

---

### Phase 7 工作量估算

| Task | 描述 | 工作量 | 优先级 |
|------|------|--------|--------|
| 7.1 | 风控穿透测试矩阵 | 2 人天 | P0 |
| 7.2 | RiskInterventionTracker | 1.5 人天 | P0 |
| 7.3 | 策略上线前 5 层验证门控 | 3 人天 | P0 |
| 7.4 | 成本压测标准化入口 | 1 人天 | P1 |
| 7.5 | 影子模式验证框架 | 2 人天 | P1 |
| 7.6 | AIAuditLog 持久化 | 1.5 人天 | P1 |
| 7.7 | 控制面快照持久化 | 2 人天 | P1 |
| 7.8 | 统一 DecisionTraceId | 1.5 人天 | P2 |

**Phase 7 总计：约 14.5 人天**

---

### Phase 7 与 Phase 6 的关系

**Phase 6 状态**：M1-M5 全部完成 ✅

**Phase 7 是 Phase 6 的自然延续**：
- Phase 6 解决了"风控规则分散"的问题
- Phase 7 要解决"风控是否真的生效"的问题
- 两个阶段都服务于同一个目标：从"功能齐全"到"可信赖系统"

**执行建议**：
1. Phase 7 的 Task 7.1 和 7.2 可以独立于 Phase 6 先执行
2. Task 7.3 依赖 Phase 6 的 `StrategyLifecycleManager`（已完成）
3. Task 7.4-7.5 依赖 Task 7.3 的验证门控
4. Task 7.6-7.8 可并行执行，不依赖其他 Task
