# PLAN.md — quant_trading_system Crypto v3.3.0 工程推进计划

> 总架构师视角。本文件描述当前项目状态、各阶段任务优先级与验收标准，供工程师执行参考。
> 执行优先级遵循 `.traerules`：As-Is > In-Progress > Target。

---

## 一、当前状态快照（As-Is）

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

### 部分完成（In-Progress，需推进）

| 模块 | 缺口 |
|------|------|
| 事件溯源（PG） | 通用event_log表结构不完整；仅风险事件有PG落地 |
| 时间窗口风控 | 概念存在于risk_engine，但时段系数体系未实现 |
| Funding/OI适配器 | 数据结构已定义，Binance API实际集成缺失 |

### 未开始（Target，按优先级推进）

- Feature Store（P0阻塞项）
- Reconciler（P1阻塞项）
- On-Chain/宏观数据适配器
- 事件公告爬虫
- 深度检查 + 滑点估算
- 策略监控 + 告警
- 研究信号层（趋势/价量/资金结构/链上）
- PG投影读模型
- Escape Time模拟器
- Replay Runner

---

## 二、阶段划分

```
Phase 0 (当前)  ──► Phase 1 ──► Phase 2 ──► Phase 3
基础设施稳固       闭环安全层    研究信号层    增强与自动化
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
- [ ] `/monitor/snapshot` 返回完整系统状态快照
- [ ] 日亏损超限时触发告警日志
- [ ] Adapter DEGRADED时触发告警日志

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
- [ ] OMS事件持久化到PG
- [ ] 重启后可从event_log恢复OMS状态
- [ ] 幂等append通过CI postgres-integration
- [ ] 内存fallback在PG不可用时自动启用

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

**交付物**：
- `adapters/onchain/coinglass_adapter.py`（或等价公开数据源）
  - 采集：交易所净流入/流出、稳定币供应量变化、大额清算事件
  - 写入Feature Store

**验收标准**：
- [ ] 至少2个链上指标稳定写入Feature Store
- [ ] 数据延迟可观测（local_receive_ts vs source_ts）

---

### Task 2.3 — 事件公告爬虫

**交付物**：
- `adapters/announcements/binance_crawler.py`
  - 解析Binance公告RSS/API
  - 分类：`ListingEvent`、`DelistingEvent`、`MaintenanceEvent`、`OtherEvent`
  - 写入event_log（stream_key=`announcements`）

**验收标准**：
- [ ] 新上币公告能被正确分类为ListingEvent
- [ ] 事件写入event_log，可被策略订阅

---

### Task 2.4 — 基础信号层（趋势 + 价量）

**交付物**：
- `core/domain/signals/trend_signals.py`
  - EMA交叉、价格动量、布林带位置
- `core/domain/signals/price_volume_signals.py`
  - 成交量扩张检测、波动率压缩检测
- Signal Sandbox：`tools/signal_sandbox.py`
  - 输入：历史Feature Store数据
  - 输出：信号时序、未来函数检测报告

**验收标准**：
- [ ] 所有信号计算无IO（Core Plane约束）
- [ ] Signal Sandbox能检测出未来函数泄漏
- [ ] 信号输出为标准化`Signal` dataclass

---

### Task 2.5 — 资金结构信号

**交付物**：
- `core/domain/signals/capital_structure_signals.py`
  - Funding rate z-score（滚动窗口）
  - OI变化率 + 价格背离检测
  - 多空比异常检测

**验收标准**：
- [ ] 依赖Feature Store中的funding_rate/OI数据
- [ ] z-score计算有单测（边界：窗口不足时返回None）

---

## 五、Phase 3 — 增强与自动化

**前置条件**：Phase 2完成，系统有稳定信号输出。

---

### Task 3.1 — PG投影读模型

- 将OMS/Position事件投影为PG读表（`positions_view`、`orders_view`）
- API层从PG读模型查询，替换内存查询

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

## 六、工程执行约束（所有Phase通用）

1. **Core Plane禁止IO**：`core/` 下所有新代码不得有网络/DB/文件IO，不得读环境变量。
2. **幂等优先**：所有写操作必须幂等，重复调用结果一致。
3. **测试门禁**：每个Task交付必须包含单测，P0回归不得回归。
4. **类型注解**：所有函数签名和类属性必须有类型注解。
5. **结构化日志**：关键状态迁移必须打结构化日志，含`trace_id`。
6. **Fail-Closed**：异常处理必须Fail-Closed，禁止裸`except: pass`。
7. **PR包格式**：功能描述 + 变更文件清单 + 测试结果 + 风险与回滚方案。

---

## 七、里程碑检查点

| 里程碑 | 完成条件 | 对应Tasks |
|--------|----------|-----------|
| M1: 安全闭环 | Phase 1全部Task通过CI | 1.1–1.6 |
| M2: 数据就绪 | Funding/OI/链上数据稳定写入Feature Store | 2.1–2.2 |
| M3: 信号就绪 | 至少3类信号通过Signal Sandbox验证 | 2.3–2.5 |
| M4: 可试运行 | M1+M3完成 + Reconciler无漂移告警 + 监控正常 | 全部Phase 1+2 |

---

## 八、当前推荐起点

**立即开始**：Task 1.1（Feature Store）和 Task 1.2（Reconciler）可并行开发，互不依赖。

**顺序建议**：
```
Task 1.1 (Feature Store)  ─┐
Task 1.2 (Reconciler)     ─┤─► Task 1.5 (监控) ─► Task 1.6 (事件溯源)
Task 1.3 (深度检查)        ─┤
Task 1.4 (时间窗口)        ─┘
```

Task 1.3 和 1.4 体量小，可穿插在 1.1/1.2 开发间隙完成。
