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
- **策略执行器（StrategyRunner）** — Phase 4核心
- **策略评估器（StrategyEvaluator）** — Phase 4核心
- **策略热插拔机制** — Phase 4核心
- **AI策略生成服务** — Phase 4核心
- **AI策略聊天界面** — Phase 4核心

---

## 二、阶段划分

```
Phase 0 (当前)  ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4
基础设施稳固       闭环安全层    研究信号层    增强与自动化  策略管理与AI共创
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
- [ ] 与OMS对接：信号回调 → OMS.submit_order()
- [ ] 与KillSwitch对接：策略级风险检查
- [ ] 集成 `StrategyResourceLimits` 资源限制

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

**技术选型建议**：
- LLM后端：优先支持 OpenAI API，预留 Anthropic 本地模型接口
- 代码加载：使用 `importlib` 动态加载，避免 `exec()` 安全风险
- 会话存储：短期使用内存，长期迁移到 Redis/PG
