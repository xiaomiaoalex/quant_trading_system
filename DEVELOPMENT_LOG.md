# 开发记录

> 本文件记录每次开发/修复/验证的过程性信息，用于补足 `PLAN.md` 与
> `PROJECT_STATUS.md` 之间的空白。

## 维护规则

- 每次代码或架构性文档变更后，都追加一条记录。
- 记录重点是“为什么改、改了什么、怎么验证、还剩什么风险”。
- 只追加，不重写历史；如果后续推翻前一条判断，在新记录中说明。
- 简短任务可以记录 3-5 行；复杂任务应包含完整模板。

## 记录模板

```markdown
### YYYY-MM-DD HH:mm - 任务标题

- 背景:
- 决策:
- 改动:
- 验证:
- 风险/遗留:
- 关联文档:
```

## 最近记录

### 2026-05-19 03:00 - 阶段4 保证金与强平模型升级验收修正

- 背景: 阶段4交付将 `MarginRiskCalculator` 扩展到强平价、费用缓冲和 bracket 语义，但验收发现强平价测试在测试文件内复制了本地 `calculate_liquidation_price()`，没有真正覆盖生产实现；生产公式还存在把 `maint_amount` 直接加到价格上的维度问题。
- 决策: 测试必须调用生产 `MarginRiskCalculator.calculate_liquidation_price()`；强平价按保证金等式求解，费用缓冲计入有效维持保证金，保持 Core 无 IO、确定性、可回放。
- 改动:
  - `trader/core/domain/services/margin_risk_calculator.py`: 修正 long/short 强平价公式，使用 `entry_notional`、`effective_initial_margin`、`maint_margin_ratio`、`maint_amount` 和 fee buffer 解算；`calculate_risk_adjusted_margin()` 将费用计入 adjusted maintenance。
  - `trader/tests/test_margin_risk_calculator.py`: 移除影子 `LiquidationPriceResult` 和影子 `calculate_liquidation_price()`；费用缓冲测试改为断言生产函数输出。
  - `trader/core/domain/models/crypto_risk.py`: 补充 `Optional` 导入，修复阶段4相关 DTO 类型追踪。
  - `docs/INTERFACE_CONTRACTS.md`: 修正阶段4强平价公式与测试约束。
- 验证:
  - `python -m pytest -q trader/tests/test_margin_risk_calculator.py trader/tests/test_risk_sizing_engine.py trader/tests/test_oms_pretrade_risk_gate.py --tb=short` → 50 passed
  - `python -m mypy trader/core/domain/services/margin_risk_calculator.py --ignore-missing-imports --follow-imports=skip` → Success
  - 未加 `--follow-imports=skip` 的 mypy 会追入历史类型债，当前仍有非阶段4遗留错误。
- 风险/遗留: MarginRiskCalculator 仍未接入 RiskSizingEngine constraint；费用配置仍需 runtime/交易所费率接线。
- 关联文档: `PROJECT_STATUS.md`、`docs/INTERFACE_CONTRACTS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-19 02:00 - 阶段3.6 Funding/OI 静默异常修复

- 背景: 阶段3最终验收发现 `BinanceFundingOIMetricsSource` 的历史窗口读取异常分支仍存在静默吞异常风险；虽然返回空窗口会触发 fail-closed/window_insufficient，但定位真实数据源故障时缺少日志证据。
- 决策: Funding/OI live 数据链路允许在 adapter/source 异常时返回缺失值进入 fail-closed，但异常必须记录 warning，禁止 `except Exception: pass`。
- 改动:
  - `trader/services/crypto_risk_snapshot.py`: `_get_current_funding()`、`_get_current_oi()`、`_get_latest_funding_ts()`、`_get_latest_oi_ts()` 捕获异常时记录 warning 后返回缺失值。
  - `trader/services/crypto_risk_snapshot.py`: `_get_funding_history()`、`_get_oi_history()` 捕获异常时记录 warning 后返回空历史窗口。
  - `PROJECT_STATUS.md`: 更新阶段3状态、测试口径和历史窗口说明，移除“实时值模拟”的旧描述。
- 验证:
  - `python -m pytest -q trader/tests/test_funding_oi_live_wiring.py trader/tests/test_funding_oi_window_calculator.py trader/tests/test_funding_oi_metrics_provider.py trader/tests/test_crypto_risk_p0.py --tb=short`
  - `python -m pytest -q trader/tests/test_risk_mode_oms_integration.py trader/tests/test_strategy_runner_risk_mode_gate.py trader/tests/test_risk_mode_controller.py --tb=short`
  - scoped mypy: `crypto_risk_snapshot.py`、`crypto_risk_runtime.py`、`funding_oi_metrics_provider.py`、`funding_oi_window_calculator.py`、`crypto_pre_trade_risk_plugin.py`、`funding_oi_stream.py`
- 风险/遗留: 当前异常日志进入服务日志；后续若需要运维面板展示，可把具体 source_error 汇入 audit details。
- 关联文档: `PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-19 01:00 - 阶段3.3 Funding/OI Live Wiring 返工修复

- 背景: 阶段3.2实现后仍有4个问题：
  1. `BinanceFundingOIMetricsSource._fetch_current_funding()` 使用 `hasattr(broker, "get_funding_rate")`，但 `BinanceFuturesRiskDataSource` 没有此方法，导致 live funding永远为 None
  2. `_fetch_current_oi()` 直接 `return None`，OI永远缺失
  3. `update_budget()` 重建 provider 时丢失 `funding_oi_metrics` wiring
  4. 没有新增测试覆盖关键 wiring
- 决策: 实现真正的 Binance Current Funding/OI 数据源，复用已有的 `CurrentFundingOIPort` 接口
- 改动:
  - `trader/adapters/binance/funding_oi_stream.py`: 新增 `BinanceCurrentFundingOISource` 实现 `CurrentFundingOIPort`
  - `trader/services/crypto_risk_snapshot.py`: 重构 `BinanceFundingOIMetricsSource` 使用 `BinanceCurrentFundingOISource`
  - `trader/api/crypto_risk_runtime.py`: 更新 wiring + `update_budget()` 保留 `funding_oi_metrics`
  - `trader/tests/test_funding_oi_live_wiring.py`: 新增 11 个 wiring 测试
- 验证:
  - 60 passed (Funding/OI calculator + metrics provider + live wiring + crypto P0)
  - 50 passed (RiskMode)
  - mypy → Success
- 风险/遗留:
  - OI 历史窗口暂使用实时值模拟，后续可增强使用 FeatureStore
  - `BinanceCurrentFundingOISource` 依赖 Binance REST API，需网络可达
- 关联文档: `PROJECT_STATUS.md`

### 2026-05-18 23:00 - 阶段3.1+3.2 Funding/OI 生产数据接线

- 背景: 阶段3.1审线发现核心断点：`DataSourceCryptoRiskSnapshotProvider` 没有构建 `funding_oi_metrics`，live runtime 永远返回空字典，导致 Funding/OI 风控实际上永远是"数据缺失拒绝"而非"基于实时市场状态拒绝"。
- 决策: 阶段3.2 最小闭环：注入 `FundingOIMetricsPort` → `BinanceFundingOIMetricsSource` → snapshot
- 改动:
  - `trader/services/crypto_risk_snapshot.py`: 新增 `FundingOIMetricsPort` Protocol、`BinanceFundingOIMetricsSource`
  - `trader/api/crypto_risk_runtime.py`: wiring `BinanceFundingOIMetricsSource` 到 snapshot provider
  - fail-closed: metrics 计算失败时抛出 `CryptoRiskSnapshotUnavailable`
- 验证:
  - 49 passed (Funding/OI + crypto P0)
  - 50 passed (RiskMode)
  - mypy → Success
- 风险/遗留:
  - `BinanceFundingOIMetricsSource` 只拉取 funding rate，OI 历史窗口使用实时值模拟
  - 后续可增强：使用 Binance funding history API 或 FeatureStore
- 关联文档: `PROJECT_STATUS.md`

### 2026-05-18 22:00 - 阶段2.1 第四轮返工（系统强平入口修正）

- 背景: 第三轮发现两个问题：1) LIQUIDATE_AND_DISCONNECT 的系统强平入口可以绕过开仓限制（只检查 is_system_liquidation=True，未检查信号类型）；2) 测试数量口径仍不准确。
- 决策: 将系统强平入口从布尔标签升级为真正的 reduce-only 约束。
- 改动:
  - `trader/services/oms_callback.py`: LIQUIDATE_AND_DISCONNECT 放行条件改为 `is_system_liquidation=True AND (is_close_signal() OR reduce_only=True)`
  - `trader/tests/test_risk_mode_oms_integration.py`: 新增 `test_oms_liquidate_and_disconnect_blocks_open_even_with_system_liquidation_flag`
  - `PROJECT_STATUS.md`: 清理重复 header，修正测试数量为 27+23=50
- 验证:
  - 50 passed ✅ (20 OMS集成 + 7 StrategyRunner + 23 Controller)
  - mypy → Success ✅
- 关联文档: `PROJECT_STATUS.md`

### 2026-05-18 21:00 - 阶段2.1 RiskMode/KillSwitch 统一控制 OMS（含返工）

- 背景: 阶段2原实现存在4个阻断点：1) PROJECT_STATUS.md 被截断；2) NO_NEW_POSITIONS 逻辑错误（只设置 blocked_reason 但不阻止开仓）；3) 测试主要 mock OMS callback 而非 StrategyRunner tick 路径；4) 测试数量口径不准确。
- 决策: 补阶段2.1返工：恢复历史文档、修复 RiskMode gate 逻辑、添加真实的 StrategyRunner tick 路径测试。
- 改动:
  - `PROJECT_STATUS.md`: 恢复历史内容（使用 git checkout），在顶部追加阶段2.1记录
  - `trader/services/strategy_runner.py`: 修复 RiskMode gate 逻辑；CLOSE_ONLY 只阻止开仓信号，允许减仓信号；CANCEL_ALL_AND_HALT/LIQUIDATE_AND_DISCONNECT 阻止所有策略信号
  - `trader/tests/test_strategy_runner_risk_mode_gate.py`: 新增7个测试，真实测试 StrategyRunner.tick() 路径中的 RiskMode gate 行为
- 验证:
  - 50 passed ✅ (20+7+23) - 注：第三轮修正后口径
  - mypy strategy_runner.py → Success ✅
- 风险/遗留:
  - 测试数量：OMS集成 20个 + StrategyRunner 7个 + Controller 23个 = 50个总计
  - 下一步：阶段3 Funding/OI 生产数据接线
- 关联文档: `PROJECT_STATUS.md`
  - 下一步：阶段3 Funding/OI 生产数据接线
- 关联文档: `PROJECT_STATUS.md`

### 2026-05-18 20:30 - 阶段2 RiskMode/KillSwitch 统一控制 OMS

- 背景: RiskMode/KillSwitch 需要成为实盘执行链路的一等控制源。StrategyRunner 做 early gate，OMS 做 final gate。
- 决策: 在 StrategyRunner.tick() 中增加 RiskMode 检查回调，CLOSE_ONLY 及以上模式阻止所有信号。
- 改动:
  - `trader/services/strategy_runner.py`: 新增 `set_risk_mode_callback()` 方法和 `_risk_mode_callback` 属性
  - RiskMode early gate 检查逻辑：CLOSE_ONLY 及以上阻止所有信号
  - `trader/tests/test_risk_mode_oms_integration.py`: 新增 9个测试
- 验证:
  - 36 passed ✅
  - mypy scoped 7 files → Success ✅
- 风险/遗留:
  - KillSwitch 和 RiskMode 语义必须保持一致
  - 下一步：阶段3 Funding/OI 生产数据接线
- 关联文档: `PROJECT_STATUS.md`、`docs/INTERFACE_CONTRACTS.md` 8.8节

### 2026-05-18 20:10 - P0 风控链路 mypy scoped 收敛

- 背景: 全仓 `mypy trader/` 存在大量历史类型债，不适合作为阶段1/2风控闭环的即时门禁；但 OMS、RiskEngine、RiskSizing、RiskMode 等 P0 风控链路需要先收敛，避免关键下单路径继续新增类型不确定性。
- 决策: 采用 scoped mypy 门禁，范围限定 `oms_callback.py`、`risk_engine.py`、`risk_decision.py`、`risk_sizing_engine.py`、`risk_mode.py`、`risk_mode_controller.py`，并使用 `--follow-imports=skip` 隔离非本阶段依赖噪声。
- 改动: `OMSCallbackHandler._reserved_balance()` 为 `sum()` 增加 `Decimal("0")` 初始值；新增 `FillCallback` 类型别名，允许同步或异步 fill callback；主下单和 WS fill 路径均只在回调返回 awaitable 时 await/调度任务。
- 验证: scoped mypy 6 个 P0 文件 Success；OMS+RiskSizing 回归 26 passed；Crypto Risk P0 + RiskMode 回归 35 passed。
- 风险/遗留: 全仓 mypy 仍有既存技术债，需要后续单独开全仓类型收敛专项；本次不清理 P10 未提交文件和历史 tests/fakes 类型问题。
- 关联文档: `PROJECT_STATUS.md`

### 2026-05-18 19:30 - 阶段1.1 实盘 RiskSizing 裁剪接入 OMS（含返工）

- 背景: 阶段1功能主线通过（broker 下单前应用 final_qty），但有3个阻断点：1) 审计测试断言不成立（只检查 order_submit_ok，没验证字段）；2) final_qty 解析无保护；3) 缺少文档闭环记录。
- 决策: 补阶段1.1返工：CLIP 订单写入审计字段、解析保护、storage 读取验证。
- 改动:
  - `trader/services/oms_callback.py`:
    - `_apply_risk_sizing_clip()`: `Decimal(str(final_qty_str))` 包 try-except，解析失败时 `_record_rejection()` + `RiskRejectedError`
    - `signal.metadata["risk_sizing_decision"] = sizing_dict` 保存裁剪上下文
    - `execute_signal()`: CLIP 成功订单写入 `risk_sizing_decision`、`risk_requested_qty`、`risk_normalized_qty`、`risk_final_qty`、`risk_limiting_factor`、`risk_trace_id`
  - `trader/tests/test_oms_pretrade_risk_gate.py`:
    - `test_oms_pretrade_risk_clip_audits_requested_and_final_qty`: 改为读取 storage 验证字段存在且值正确
- 验证:
  - 5个新测试全部通过 ✅
  - OMS + RiskSizing: 26 passed ✅
  - Crypto Risk P0: 12 passed ✅
- 风险/遗留:
  - mypy 全仓有既存类型债（不要求阶段1清完）
  - 下一步：阶段2 RiskMode/KillSwitch 控制 OMS
- 关联文档: `PROJECT_STATUS.md`、`docs/INTERFACE_CONTRACTS.md`

- 背景: 用户发现阶段0契约存在4个歧义：1) `RiskSizingDecision.calculate()` 写错应为 `RiskSizingEngine.calculate()`；2) 缺少三条链路架构图；3) RiskMode 动作矩阵用单一 blocks_all_orders 混淆三种命令；4) 缺少文档闭环记录。
- 决策: 先修正4个问题再进入阶段1。统一计算入口为 `RiskSizingEngine.calculate()`；新增三条链路图；区分 place_order/cancel_order/reduce_only liquidation 命令；追加文档记录。
- 改动:
  - `docs/INTERFACE_CONTRACTS.md`：
    - 8.7.5节：将 `RiskSizingDecision.calculate()` 修正为 `RiskSizingEngine.calculate(signal, snapshot, trace_id)`
    - 8.8.3节：RiskMode 动作矩阵改为区分三种命令（place_order/cancel_order/reduce_only liquidation）
    - 新增 8.5.2节：Funding/OI Runtime Contract（数据源、freshness、fail-closed 行为矩阵）
  - `docs/PROJECT_ARCHITECTURE.md`：
    - 新增 5.1节：三条风控闭环链路（RiskSizing裁剪、RiskMode/KillSwitch控制、Funding/OI数据）
  - `PROJECT_STATUS.md`：追加阶段0完成记录
- 验证:
  - `python -m pytest -q trader/tests/test_oms_pretrade_risk_gate.py trader/tests/test_risk_sizing_engine.py --tb=short` → 21 passed ✅
  - `python -m pytest -q trader/tests/test_binance_connector.py trader/tests/test_binance_private_stream.py trader/tests/test_binance_degraded_cascade.py trader/tests/test_deterministic_layer.py trader/tests/test_hard_properties.py --tb=short` → 108 passed ✅
  - `python -m mypy trader/core/domain/models/risk_decision.py trader/core/domain/models/risk_mode.py trader/core/domain/services/risk_sizing_engine.py --ignore-missing-imports` → Success ✅
- 风险/遗留:
  - 契约已锁定，后续阶段开发必须基于这三份契约
  - 阶段1目标：CLIP 决策下 broker 实际收到 final_qty，不是原始 requested_qty
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`PROJECT_STATUS.md`

### 2026-05-18 17:30 - P10 任务包 6 返工：VectorBT 真路径 + 格式修复

- 背景: 主审不通过。P1: `TestVectorBTReplayConsistency` 没有走 VectorBT 路径，仅用 `BacktestRiskIntegration` 间接验证。P1: black/isort 格式检查实际失败，5 个文件需要 reformat。
- 决策: 重写 `TestVectorBTReplayConsistency`，真正实例化 `VectorBTAdapterWithRisk`，调用 `_build_risk_adjusted_input_plan()`，比较 plan 的 approved_orders/clipped_orders/rejected_orders 与 replay 决策分类。运行 black/isort 修复格式。
- 改动:
  - 重写 `TestVectorBTReplayConsistency`（3 个测试）：
    - `test_order_classification_matches_vectorbt_plan`：真正调用 `VectorBTAdapterWithRisk._build_risk_adjusted_input_plan()`，比较 replay 的 approved/clipped/rejected 分类与 VectorBT risk plan 的 approved_orders/clipped_orders/rejected_orders
    - `test_effective_quantity_matches_vectorbt_plan`：验证 approved 和 clipped 订单的 effective_quantity 跨路径一致
    - `test_rejection_reason_counts_match_vectorbt_plan`：验证 rejection_reason 计数跨路径一致
  - 运行 `black --line-length 100` 修复 6 个文件格式
  - 运行 `isort --profile black` 修复 6 个文件 import 排序
- 验证:
  - 163 passed, 2 warnings
  - `python -m black --check --line-length 100 ...` → passed ✅
  - `python -m isort --check-only --profile black ...` → passed ✅
  - `py_compile` → passed ✅
  - `git diff --check` → passed ✅
- 风险/遗留:
  - 一致性测试使用 mock RiskEngine，未覆盖真实 CryptoPreTradeRiskPlugin 的完整链路
  - VectorBT 一致性测试现在真正走 `_build_risk_adjusted_input_plan()` 路径，审计闭环
- 关联文档: `docs/INTERFACE_CONTRACTS.md` P10 契约、`PROJECT_STATUS.md`

### 2026-05-18 16:00 - P10 任务包 6：一致性与回归

- 背景: P10 回测风险重放引擎需要证明 replay 路径与现有风控调用路径一致。任务包 5 修复了 duration 重叠和 after-risk 指标归零问题，任务包 6 需要验证一致性。
- 决策: 通过同一 signal + 同一 RiskCheckResult 序列，分别走 replay 和 BacktestRiskIntegration 两条路径，断言分类、effective_quantity、rejection_reason 完全一致。
- 改动:
  - 新增 `TestReplayRiskEngineConsistency`（7 个测试）：
    - `test_approved_signal_matches_direct_check`：approved 信号 replay 分类一致
    - `test_rejected_signal_matches_direct_check`：rejected 信号 replay 分类一致
    - `test_clipped_signal_matches_direct_check`：clipped 信号 replay 分类一致
    - `test_mixed_signals_match_direct_check`：混合信号 replay 分类一致
    - `test_replay_and_integration_classify_identically`：replay 与 BacktestRiskIntegration 分类完全一致
    - `test_effective_quantity_matches_integration`：effective_quantity 跨路径一致
    - `test_rejection_reason_matches_integration`：rejection_reason 跨路径一致
  - 新增 `TestVectorBTReplayConsistency`（1 个测试）：
    - `test_order_classification_matches_vectorbt`：VectorBT risk-adjusted 与 replay 订单分类一致
  - 新增 `_make_signal_with_dt` 辅助函数：BacktestRiskIntegration 需要 datetime 类型 timestamp，而 _make_signal 使用 int
- 验证:
  - 139 passed, 2 warnings
  - black/isort/py_compile/git diff check → passed（后经主审验证 black/isort 实际失败，返工修复）
- 风险/遗留:
  - 一致性测试使用 mock RiskEngine，未覆盖真实 CryptoPreTradeRiskPlugin 的完整链路
  - VectorBT 一致性测试通过 BacktestRiskIntegration 间接验证，未直接调用 VectorBTAdapterWithRisk（返工已修复）
- 关联文档: `docs/INTERFACE_CONTRACTS.md` P10 契约、`PROJECT_STATUS.md`

### 2026-05-15 14:00 - P9.5 回测市场端口准备 + 架构修正

- 背景: P9.5 实现回测用市场端口（TradingCalendarPort / MarketCostModelPort / MarketRuleSnapshotProviderPort），不接入真实行情/券商/交易接口。审计发现阻断问题：1) 文档闭环缺失；2) 重复定义 OrderSide/AssetClass；3) A 股字段污染通用 snapshot；4) limit_up/limit_down 语义错误。
- 决策: 复用 core 层已有枚举；A 股专属字段放入 metadata["china_stock"]；字段命名修正为 limit_up_rate/limit_down_rate；更新 INTERFACE_CONTRACTS.md 8.12 节。
- 改动:
  - 新增 `trader/services/backtesting/trading_calendar_port.py`：`TradingCalendarPort`、`FakeTradingCalendar`、`ChinaStockCalendar`
  - 新增 `trader/services/backtesting/market_cost_model_port.py`：`MarketCostModelPort`、`NoOpCostModel`、`ChinaStockCostModel`
  - 新增 `trader/services/backtesting/market_rule_snapshot_provider_port.py`：`MarketRuleSnapshotProviderPort`、`FakeMarketRuleSnapshotProvider`、`ChinaStockSnapshotProvider`、`MarketRuleSnapshot`、`ChinaStockMetadata`
  - 新增 `trader/tests/test_market_ports.py`：24 个测试
  - 更新 `docs/INTERFACE_CONTRACTS.md`：新增 8.12 节 P9.5 契约
  - 更新 `docs/PLAN.md`、`PROJECT_STATUS.md`
- 验证:
  - `python -m pytest trader/tests/test_market_ports.py -v --tb=short` → 24 passed
  - black/isort/mypy → passed
- 风险/遗留:
  - P9 全部子阶段完成，可以提交
  - A 股字段通过 metadata 隔离，避免跨市场抽象污染
- 关联文档: `docs/INTERFACE_CONTRACTS.md` 8.12 节、`docs/PLAN.md`、`PROJECT_STATUS.md`

### 2026-05-14 14:45 - QuantConnect Lean legacy 运行时代码清理

- 背景: 回测架构已收敛为 Qlib research / VectorBT fast backtest / future EventDrivenRiskReplay，`strategy_adapter.py` 与 `result_converter.py` 仍作为 Lean legacy runtime 文件留在 `trader/services/backtesting/`，容易误导后续开发继续接入旧路线。
- 决策: 删除 Lean 运行时代码，保留 ADR/比较文档中的历史选型背景；不删除历史文档记录，不改变 VectorBT active implementation。
- 改动:
  - 删除 `trader/services/backtesting/strategy_adapter.py`
  - 删除 `trader/services/backtesting/result_converter.py`
  - 清理 `trader/tests/test_backtesting_adapters.py` 中依赖上述模块的旧 Lean 测试，保留 execution simulator、slippage、SL/TP 与 next-bar 关键测试
  - 更新 `trader/services/backtesting/__init__.py`、`trader/services/backtesting/ports.py`、`docs/backtesting_architecture.md`、`docs/PROJECT_ARCHITECTURE.md` 和 `docs/adr/ADR-002-backtesting-research-architecture-convergence.md`
- 验证:
  - `rg "result_converter|strategy_adapter" trader` → 无 active 代码引用
  - `python -m pytest trader/tests/test_backtesting_adapters.py trader/tests/test_backtesting_vectorbt_adapter.py trader/tests/test_vectorbt_risk_adapter.py trader/tests/test_backtest_risk_integration.py -q --tb=short` → passed
  - black/isort/py_compile/git diff check → passed
- 风险/遗留:
  - 历史文档中仍保留 QuantConnect Lean 选型记录，均作为 superseded/historical reference
  - 后续新增回测能力应走 VectorBT 或 EventDrivenRiskReplay，不得重新引入 Lean runtime 适配层

### 2026-05-14 14:30 - P9.0+P9.1 市场无关规则框架

- 背景: P9 需要构建"市场无关规则接口 + 市场专用规则插件"架构，使 A 股规则和 Crypto 规则可以被插件化而不互相污染。
- 决策: Core 层只定义 `MarketRuleIntent`/`MarketRuleCheckResult`/`MarketRulePlugin` 契约和 `MarketRuleEngine` 调度引擎；A 股规则放入 `ChinaStockMarketRulePlugin`，Crypto 规则放入 `CryptoMarketRulePlugin`；`OrderSide`/`OrderType` 直接复用 `trader.core.domain.models.order` 中的枚举。
- 改动:
  - 新增 `trader/core/domain/models/market_rules.py`：`MarketRuleIntent`、`MarketRuleViolation`、`MarketRuleCheckResult`、`MarketRulePlugin`、导出 `OrderSide`/`OrderType`
  - 新增 `trader/core/domain/services/market_rule_engine.py`：`MarketRuleEngine`、`MarketRuleEngineConfig`；插件调度、结果聚合、fail-closed 包装
  - 新增 `trader/tests/test_market_rule_engine.py`：11 个测试覆盖无插件 fail-closed、supports() 异常 fail-closed、check() 异常 fail-closed、一个 reject 阻止整体、多插件 normalized_qty 取最小值、OrderSide 兼容性
  - 更新 `trader/core/domain/models/__init__.py` 和 `trader/core/domain/services/__init__.py`：导出新类型
- 审计修复（P9.1 阻断问题）:
  - [P1] supports() 异常被吞掉 → 改为直接返回 `MarketRuleCheckResult.fail_closed()`，不再 skip
  - [P1] 新增 `OrderSide` 与既有 `order.OrderSide` 冲突 → 直接引用 `trader.core.domain.models.order` 中的枚举
  - [P1] `fail_closed_on_error` 对 supports() 不生效 → 重命名为 `fail_closed_on_check_error`，文档说明 supports 异常永远 fail-closed
  - [P2] reject 聚合丢失插件 details → 保留 `plugin_details` 到 reject details
  - [P2] docstring 写 Raises 但实际返回 → 修正为"返回 fail_closed 结果，不 raise"
  - [P1] black/isort 格式门禁失败 → 运行 black/isort 格式化
- 验证:
  - `python -m pytest trader/tests/test_market_rule_engine.py -v --tb=short` → 11 passed
  - `python -m pytest trader/tests/test_architecture.py trader/tests/test_backtesting_vectorbt_adapter.py -v --tb=short` → 24 passed
  - black/isort/py_compile/git diff check → passed
- 风险/遗留:
  - P9.1 框架已建立，尚未连接实际 plugin（P9.2/P9.3 实现）
  - PLAN.md 已更新 P9.0/P9.1 状态标记为"已完成（含审计修复）"
- 关联文档: `docs/INTERFACE_CONTRACTS.md` 8.11 节、`docs/PROJECT_ARCHITECTURE.md` P9 市场规则插件架构、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-14 17:00 - P9.3 Crypto 市场规则插件

- 背景: P9.3 需要实现 Crypto 专属规则插件，包装现有 ExchangeRuleGuard 的 tick/step/minNotional/maxQty 语义。
- 决策: `CryptoMarketRulePlugin` 通过 metadata 读取交易所规则字段；不读取 A 股字段；缺失必填字段时 fail-closed。
- 改动:
  - 新增 `trader/core/domain/services/crypto_market_rule_plugin.py`：`CryptoMarketRulePlugin`、`CryptoMarketRulePluginConfig`；实现 price_tick/qty_step 归一化、min_qty/max_qty/min_notional/max_notional 检查
  - 新增 `trader/tests/test_crypto_market_rule_plugin.py`：33 个测试覆盖所有 Crypto 规则、不读取 A 股字段、缺失市场状态 fail-closed
  - 更新 `trader/core/domain/services/__init__.py`：导出新类型
  - 更新 `docs/INTERFACE_CONTRACTS.md` 8.11.5：补录 violation code 表格
- 验证:
  - `python -m pytest trader/tests/test_market_rule_engine.py trader/tests/test_china_stock_market_rule_plugin.py trader/tests/test_crypto_market_rule_plugin.py -v --tb=short` → 88 passed（33 crypto + 44 china + 11 engine）
  - black/isort/py_compile → passed
- 风险/遗留:
  - P9.3 完成，等待审计
- 关联文档: `docs/INTERFACE_CONTRACTS.md` 8.11.5 节、`docs/PLAN.md`、`PROJECT_STATUS.md`

### 2026-05-14 17:30 - P9.4 EventDrivenRiskReplay v1

- 背景: P9.4 需要实现 service 层 signal/bar 回放编排，调用 RiskEngine.check_pre_trade() 进行风控检查。
- 决策: `EventDrivenRiskReplay` 是 service 层编排，不属于 Core；按时间顺序回放 signals，调用风控检查，根据结果决定 APPROVED/CLIPPED/REJECTED。
- 改动:
  - 新增 `trader/services/backtesting/event_driven_risk_replay.py`：`EventDrivenRiskReplay`、`EventDrivenRiskReplayConfig`、相关 DTOs；实现信号回放、风控决策、权益曲线计算、最大回撤计算
  - 新增 `trader/tests/test_event_driven_risk_replay.py`：11 个测试覆盖 APPROVED/CLIPPED/REJECTED、异常处理、权益曲线、最大回撤
- 验证:
  - `python -m pytest trader/tests/test_market_rule_engine.py trader/tests/test_china_stock_market_rule_plugin.py trader/tests/test_crypto_market_rule_plugin.py trader/tests/test_event_driven_risk_replay.py -q --tb=short` → 99 passed（11 event + 33 crypto + 44 china + 11 engine）
  - black/isort/py_compile → passed
- 风险/遗留:
  - P9.4 完成，等待审计
- 关联文档: `docs/INTERFACE_CONTRACTS.md` 8.11.6 节、`docs/PLAN.md`、`PROJECT_STATUS.md`

### 2026-05-14 16:30 - P9.2 二次审计修复（is_suspended / 非法 bool / INTERFACE_CONTRACTS）

- 背景: P9.2 一次审计后复核，发现 `is_suspended` 缺失仍默认放行、非法 bool 值按 default 处理、INTERFACE_CONTRACTS.md 未同步新语义。
- 决策: 新增 `_parse_required_bool()` 专用于必填布尔字段，缺失/非法均返回 violation；`INTERFACE_CONTRACTS.md` 8.11.4 补录 violation code 表格和布尔解析规则。
- 改动:
  - `china_stock_market_rule_plugin.py`：新增 `_parse_required_bool()`、`_check_suspension()` 改用必填字段语义；approve details 改为真实 lot_size；lot size 违规时返回 normalized_qty
  - `INTERFACE_CONTRACTS.md` 8.11.4：新增"缺失行为"列、配置项、布尔解析规则、violation code 表格
- 二次审计修复:
  - [P1] `is_suspended` 缺失默认 `False` → `_parse_required_bool(require=True)` 返回 `MARKET_STATE_MISSING`
  - [P1] 非法 bool 值按 default 处理 → `_parse_required_bool()` 返回 `INVALID_BOOL` violation
  - [P1] `INTERFACE_CONTRACTS.md` 未同步新语义 → 补录 violation code 表格和布尔解析规则
  - [P2] approve details `lot_size` 是 normalized_qty → 改为真实 lot_size
  - [P2] lot size 违规未返回 normalized_qty → 返回 normalized_qty 供后续复用
- 验证:
  - `python -m pytest trader/tests/test_market_rule_engine.py trader/tests/test_china_stock_market_rule_plugin.py -v --tb=short` → 51 passed（+5 新测试）
  - black/isort/py_compile → passed
  - 新增 `TestRequiredBoolFields`（4 测试）、`TestNormalizedQtyInViolation`（1 测试）
- 风险/遗留:
  - P9.2 全部阻断问题已修复
- 关联文档: `docs/INTERFACE_CONTRACTS.md` 8.11.4 节、`docs/EXPERIENCE_SUMMARY.md` 34.5

### 2026-05-14 16:00 - P9.2 A 股市场规则插件（含一次审计修复）

- 背景: P9.2 需要实现 A 股专属规则插件，实现 T+1、100 股、涨跌停、停牌、不可做空、交易阶段检查。
- 决策: `ChinaStockMarketRulePlugin` 通过 metadata 读取 A 股市场状态；`require_market_state=True` 默认要求完整市场数据，缺失返回 `MARKET_STATE_MISSING`；`_parse_bool()` 显式解析布尔值避免字符串 "False" 被当作 True；`_validate_side()` 对未知 side 返回 violation 而不是默认 BUY。
- 改动:
  - 新增 `trader/core/domain/services/china_stock_market_rule_plugin.py`：`ChinaStockTradingPhase`（str,Enum）、`ChinaStockMarketRulePlugin`、`ChinaStockMarketRulePluginConfig`；实现 lot_size、T+1、涨跌停、停牌、不可做空、交易阶段检查
  - 新增 `trader/tests/test_china_stock_market_rule_plugin.py`：35 个测试覆盖所有 A 股规则和 fail-closed 边界
  - 更新 `trader/core/domain/services/__init__.py`：导出新类型
- 审计修复（P9.2 阻断问题）:
  - [P1] `allow_short="False"` 字符串被当作 True → `_parse_bool()` 显式解析 "true"/"false"/"1"/"0"/"yes"/"no"/"on"/"off"
  - [P1] 未知 side 默认 BUY → `_validate_side()` 返回 `INVALID_SIDE` violation
  - [P1] 市场状态缺失默认放行 → `require_market_state=True` 返回 `MARKET_STATE_MISSING` violation
  - [P1] 格式门禁失败 → 运行 black/isort
  - [P2] `ChinaStockTradingPhase` 不是 Enum → 改为 `class ChinaStockTradingPhase(str, Enum)`
- 验证:
  - `python -m pytest trader/tests/test_market_rule_engine.py trader/tests/test_china_stock_market_rule_plugin.py -v --tb=short` → 46 passed
  - black/isort/py_compile/git diff check → passed
- 风险/遗留:
  - P9.2 完成，等待审计
- 关联文档: `docs/INTERFACE_CONTRACTS.md` 8.11.4 节、`PROJECT_STATUS.md`、`docs/PLAN.md`

### 2026-05-14 14:30 - P9.0+P9.1 市场无关规则框架

- 背景: 仓库同时存在 QuantConnect Lean 历史选型、VectorBT 当前实现、Qlib 研究主线和 P7 风控回测路径，后续 AI 容易把研究框架、快速回测框架和生产级回放框架混成一个“主引擎”。
- 决策: 采用三层叙事：Qlib Research Layer、VectorBT Fast Backtest Layer、Future EventDrivenRiskReplay；ADR-001 标记为 superseded，新增 ADR-002 作为当前决策。
- 改动: 更新 `docs/PROJECT_ARCHITECTURE.md`、`docs/backtesting_architecture.md`、`docs/backtesting_framework_comparison.md`、`docs/PLAN.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`，并修正 `trader/services/backtesting` docstring；未改运行时逻辑。
- 验证: 通过搜索定位并消除当前入口中的 `Lean primary`、`VectorBT alternative`、`LeanBacktestEngine()` 等误导性表述；运行 `git diff --check` 与 P7 回测风险相关轻量回归。
- 风险/遗留: QuantConnect Lean legacy 文件当时仍保留；后续已由 2026-05-14 14:45 cleanup 任务删除 `result_converter.py` / `strategy_adapter.py` 并审计引用关系。
- 关联文档: `docs/adr/ADR-002-backtesting-research-architecture-convergence.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/backtesting_architecture.md`

### 2026-05-13 - P8 Demo 生产化联调与 Fail-Closed 演练

- 背景: P8 要求验证真实运行坏情况下不会放行订单，并且每个失败场景都有可审计证据；现有 demo fail-closed 脚本只覆盖 HTTP 负向 probe，缺少 runtime pre-trade 层的确定性演练。
- 决策: 新增本地确定性脚本 `scripts/rehearse_crypto_risk_runtime.py`，用真实 `CryptoPreTradeRiskPlugin` / `RiskEngine.check_pre_trade()` 和审计 wrapper 演练失败路径；脚本不访问网络、不连接交易所、不下单。
- 改动:
  - 新增 `scripts/rehearse_crypto_risk_runtime.py`：覆盖 mark price 缺失、leverage bracket 缺失、open orders 激增、Funding/OI 数据过期、Binance source 超时、连续重复信号、close-only 开仓信号、PG audit 不可用
  - 扩展 `CryptoPreTradeRiskPlugin`：消费 Funding/OI budget 阈值，启用阈值时缺失/过期/窗口不足/超阈值返回 `CRYPTO_FUNDING_OI_RISK`
  - 扩展 `RejectionReason`：新增 `RISK_MODE_CLOSE_ONLY`，KillSwitch 推荐级别为 L1
  - 新增 `trader/tests/test_crypto_risk_runtime_rehearsal.py`；扩展 `trader/tests/test_crypto_risk_p0.py` 覆盖 Funding/OI 拒绝
  - 更新 `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/CRYPTO_RISK_DEMO_RUNBOOK.md`
- 验证:
  - `python scripts/rehearse_crypto_risk_runtime.py --json` → `ok=true`
  - `python -m pytest trader/tests/test_crypto_risk_runtime_rehearsal.py trader/tests/test_crypto_risk_p0.py -q --tb=short` → 17 passed
  - `python -m pytest trader/tests/test_crypto_risk_runtime_rehearsal.py trader/tests/test_crypto_risk_fail_closed_rehearsal.py trader/tests/test_crypto_risk_p0.py trader/tests/test_risk_mode_controller.py trader/tests/test_crypto_risk_runtime_api.py -q --tb=short` → 58 passed
  - black/isort/py_compile/git diff check → passed
- 风险/遗留:
  - 本段是确定性本地演练，不替代启动 demo 后端后的 HTTP probe 和人工联调
  - PG audit 不可用场景刻意允许 audit append 失败，但要求风控结果仍拒绝
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/CRYPTO_RISK_DEMO_RUNBOOK.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-14 - P8 审计修复：订单方向与 PG audit 证据

- 背景: P8 审计指出两个问题：回测入队层把所有非 BUY/LONG 信号静默映射为 SELL；PG audit 不可用场景硬编码 `audit_append_failed=true`，不能证明真的尝试过 append。
- 决策: 回测入队层使用显式 signal type -> order side 映射，未知/无效信号 fail-closed 跳过；PG audit fake repository 记录 append 尝试次数和失败次数。
- 改动:
  - `RiskAwareOrderProcessor` 对 `SignalType.NONE` 等无效类型记录 `INVALID_SIGNAL_TYPE`，不入队、不计入 approved/clipped
  - `scripts/rehearse_crypto_risk_runtime.py` 增加 `audit_append_attempts` / `audit_append_failures` 证据
  - P8 演练 RiskEngine 使用本地全天允许 `TimeWindowConfig`，避免当前时间窗口影响目标故障场景
  - 测试补充无效信号不入队、`CLOSE_SHORT` 映射 BUY、PG audit append 尝试/失败计数
- 验证:
  - `python -m pytest trader/tests/test_risk_aware_order_processor.py trader/tests/test_crypto_risk_runtime_rehearsal.py trader/tests/test_crypto_risk_fail_closed_rehearsal.py trader/tests/test_crypto_risk_p0.py trader/tests/test_risk_mode_controller.py trader/tests/test_crypto_risk_runtime_api.py -q --tb=short` → 73 passed
  - `python scripts/rehearse_crypto_risk_runtime.py --json` → `ok=true`
  - black/isort/py_compile → passed
- 风险/遗留:
  - 本段仍是本地确定性演练，不替代 demo 后端启动后的 HTTP probe
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/CRYPTO_RISK_DEMO_RUNBOOK.md`、`PROJECT_STATUS.md`

### 2026-05-13 - P7 回测接入真实风控模块

- 背景: P7 要求回测订单经过与实盘一致的 `RiskEngine.check_pre_trade(signal)`，并生成风控前/后表现；前几轮实现存在绕过 RiskEngine、只记录报告不改变成交路径、VectorBT 输入硬编码等问题。
- 决策: 保留 `BacktestRiskIntegration` 作为唯一回测风控入口；新增 `RiskAwareOrderProcessor` 覆盖事件式执行器入队路径；新增 `VectorBTAdapterWithRisk` 覆盖向量化回测路径，风控结果必须落到 entries/exits/size 序列。
- 改动:
  - 新增 `trader/services/backtesting/backtest_risk_integration.py`：定义 `BacktestRiskEnginePort`、`BacktestRiskIntegration`、`BacktestRiskReport`、`BacktestSignalResult`
  - 新增 `trader/services/backtesting/risk_aware_order_processor.py`：APPROVED/CLIPPED 入 `NextBarOpenExecutor` 队列，REJECTED 跳过；CLIPPED 缺失正数 `max_allowed_qty` 时 fail-closed
  - 新增 `trader/services/backtesting/vectorbt_risk_adapter.py`：生成 raw plan 与 risk-adjusted plan，CLIPPED 写入裁剪后 size，REJECTED 写入 size=0
  - 扩展 `trader/services/backtesting/ports.py`：`BacktestResult` 增加风控订单明细、拒绝原因统计、风控前/后最大回撤与风控后权益曲线字段
  - 更新 `trader/services/backtesting/__init__.py`：导出 P7 新类型
  - 新增/更新 `trader/tests/test_backtest_risk_integration.py`、`trader/tests/test_risk_aware_order_processor.py`、`trader/tests/test_vectorbt_risk_adapter.py`
- 验证:
  - `python -m pytest trader/tests/test_vectorbt_risk_adapter.py trader/tests/test_risk_aware_order_processor.py trader/tests/test_backtest_risk_integration.py trader/tests/test_risk_mode_controller.py trader/tests/test_risk_sizing_engine.py trader/tests/test_crypto_risk_p0.py -q --tb=short` → 86 passed
  - black/isort/py_compile/git diff check → passed
  - `python -m mypy ...` 仍失败于仓库既有全局类型债；P7 新增 `vectorbt` 缺桩已局部 ignore
- 风险/遗留:
  - P7 完成本段验收；尚未做 P8 Demo 环境 fail-closed 演练
  - 全仓 mypy 当前不是干净基线，后续若要把类型检查作为 CI 门禁，需要先单独收敛既有类型债
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-12 - P6 Risk Mode 状态机

- 背景: P5 只能控制单笔订单，无法控制账户运行模式。需要一个状态机来管理整体风险模式，支持单调升级和人工干预。
- 决策: 新增独立的 `RiskMode` 枚举和 `RiskModeController` 服务，保持 Core 层无 IO 特性，支持审计回调。
- 改动:
  - 新增 `trader/core/domain/models/risk_mode.py`：包含 `RiskMode` 枚举（NORMAL/NO_NEW_POSITIONS/CLOSE_ONLY/CANCEL_ALL_AND_HALT/LIQUIDATE_AND_DISCONNECT）、`RiskModeState`、`RiskModeTransition`、`RiskModeAuditEvent`、`create_risk_mode_event()`
  - 新增 `trader/core/domain/services/risk_mode_controller.py`：包含 `RiskModeController`、`RiskModeControllerConfig`
  - 新增 `trader/tests/test_risk_mode_controller.py`：23 个测试用例覆盖状态枚举、单调升级、人工干预、审计回调
  - 更新 `trader/core/domain/models/__init__.py` 和 `trader/core/domain/services/__init__.py`：导出新类型
- 验证:
  - `python -m pytest trader/tests/test_risk_mode_controller.py` → 23 passed
  - `python -m pytest trader/tests/test_risk_sizing_engine.py trader/tests/test_crypto_risk_p0.py` → 27 passed
  - `python -m pytest trader/tests/test_crypto_risk_runtime_api.py trader/tests/test_crypto_risk_runtime_manager.py` → 22 passed
  - black/isort/py_compile/mypy → passed
- 风险/遗留:
  - 本段是 P6 状态机基础，尚未集成到 `CryptoPreTradeRiskPlugin`
  - 下一步可继续 P7 回测接入真实风控模块，或 P8 Demo 生产化联调
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`PROJECT_STATUS.md`

### 2026-05-12 - P5 Risk Sizing Decision，支持裁剪而不只是拒绝

- 背景: P4.4-P4.7 的风控只能返回"通过"或"拒绝"，无法告诉 OMS"最多能下多少"；后续OMS需要知道最大安全下单量来自动裁剪。
- 决策: 新增 `RiskSizingDecision` DTO 和 `RiskSizingEngine` Core domain service；第一阶段只计算不自动裁剪，plugin 仍返回 reject/pass，但 details 中附带 `max_allowed_qty`。
- 改动:
  - 新增 `trader/core/domain/models/risk_decision.py`：包含 `RiskSizingDecisionType`（approve/clip/reject/close_only）、`ConstraintResult`、`RiskSizingDecision`
  - 新增 `trader/core/domain/services/risk_sizing_engine.py`：纯计算无 IO，计算每个约束的最大允许数量（symbol_cap、total_cap、cluster_cap、margin_limit、exchange_rule），取最小值
  - 扩展 `trader/core/application/plugins/crypto_pre_trade_risk_plugin.py`：集成 `RiskSizingEngine`，所有 `_reject()` 调用附带 `risk_sizing_decision` 到 details
  - 更新 `docs/INTERFACE_CONTRACTS.md`：新增 8.7 节 Risk Sizing Decision 契约
  - 更新 `trader/core/domain/models/__init__.py` 和 `trader/core/domain/services/__init__.py`：导出新类型
- 验证:
  - `python -m pytest trader/tests/test_risk_sizing_engine.py` → 16 passed
  - `python -m pytest trader/tests/test_crypto_risk_p0.py` → 9 passed
  - `python -m pytest trader/tests/test_crypto_risk_runtime_api.py trader/tests/test_crypto_risk_runtime_manager.py trader/tests/test_oms_pretrade_risk_gate.py trader/tests/test_market_risk_audit_repository.py` → 29 passed
- 风险/遗留:
  - 本段是 P5 第一阶段，尚未实现 OMS 自动裁剪
  - 下一步可继续 P6 Risk Mode 状态机，或 P7 回测接入真实风控模块
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`PROJECT_STATUS.md`

### 2026-05-15 15:00 - AI编程能力体系升级：Skills + Session-Learning + 规则分层

- 背景: 学习TRAE团队AI编程方法论后，诊断项目现有AI规则体系存在差距：缺少Skills业务封装、Session-Learning积累机制、规则分层。
- 决策: 采用TRAE四层架构改进AI编程能力：1) Skills渐进式披露架构；2) Session-Learning自动经验沉淀；3) 规则分层（L0/L1/L2/L3）；4) Spec RFC流程。
- 改动:
  - 新增 `skills/_meta/index.yaml`、`skills/backtesting/SPEC.md`、`skills/risk_management/SPEC.md`、`skills/binance_adapter/SPEC.md`、`skills/oms_core/SPEC.md`、`skills/spec_rfc/SPEC.md`
  - 新增 `scripts/session_learn.py`：Session-Learning脚本
  - 新增 `rules/L0_COLLABORATION.md`、`rules/L1_TECH_STACK.md`、`rules/L2_BUSINESS.md`、`rules/L3_WORKFLOW.md`、`rules/README.md`
  - 更新 `AGENTS.md`、`CLAUDE.md`、`.traerules`：添加Skills加载规则
- 修复: `scripts/session_learn.py extract --auto --skill ...` 显式传递 skill，避免自动提取路径引用未定义参数。
- 验证: `python -m py_compile scripts/session_learn.py`、`python scripts/session_learn.py list`、`python -m pytest trader/tests/test_session_learn.py -q --tb=short` 通过；目录结构正确，规则分层清晰。
- 风险/遗留: Skills内容需随项目发展持续补充；Session-Learning需人工触发或CI hook；当前 YAML 校验依赖未安装 `PyYAML`，本次只做结构与回归验证。
- 关联文档: `skills/_meta/index.yaml`、`skills/spec_rfc/SPEC.md`、`rules/README.md`

### 2026-05-11 - P4.7 Funding/OI 运维页面配置暴露

- 背景: P4.6 已完成 Funding/OI 历史窗口派生基础（Core 计算 + Service Provider），但运维侧无法配置和查看这些阈值。
- 决策: 扩展后端 API Schema 和前端 CryptoRiskOps 页面，支持 Funding/OI 预算字段的 PATCH 热更新和 GET 查看。
- 改动: 后端扩展 `CryptoRiskBudgetSchema`/`CryptoRiskBudgetUpdateRequest` 添加 7 个 Funding/OI 字段；扩展 `crypto_risk_budget_to_dict()` 输出新字段；扩展 `merge_crypto_risk_budget()` 接收并解析新字段；扩展 `patch_crypto_risk_budget()` 传入新字段；新增 `_parse_positive_int()`/`_validate_min_periods_against_final_window()` 校验函数；前端扩展 `CryptoRiskBudget` 类型和 `CryptoRiskBudgetSchema` Zod 契约；前端 `CryptoRiskOps` 页面新增 Funding/OI 预算编辑区域；新增后端测试。
- 第一轮审计修复: 修复 `crypto_risk_budget_to_dict()` 输出 7 个新字段；修复 `merge_crypto_risk_budget()` 接收 7 个参数并校验；修复 `patch_crypto_risk_budget()` 传入新字段；前端 discard `tsconfig.tsbuildinfo`。
- 第二轮审计修复: 修复 window/min_periods 校验逻辑（先解析最终 window 再校验 min_periods）；运行 `black --line-length 100` 格式化；环境变量从"运行时配置"移回"待 P4.8 接入"；新增测试 `test_patch_window_without_min_periods_rejects_if_exceeds_current`。
- 第三轮审计修复: `_validate_min_periods_against_final_window()` 增加 `> 0` 校验；新增 `test_patch_funding_min_periods_zero_rejected` 和 `test_patch_oi_min_periods_negative_rejected`。
- 验证: 新增后端测试 5 passed；相关回归测试 23 passed；`npm run typecheck` passed；`npx vite build` passed（227 modules, 486KB）；`black --check` passed。
- 风险/遗留: 后端风控逻辑（`CryptoPreTradeRiskPlugin` 中的 Funding/OI 阈值检查）待 P4.8 接入；Funding Window/Min 和 OI Window/Min 仅展示不可编辑。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-08 - P4.6 Funding/OI 历史窗口派生

- 背景: P4.4/P4.5 的 Funding/OI 风险系数只能外部注入，无法基于历史窗口计算 Z-Score 和变化率。
- 决策: 在 Core 层新增纯计算服务 `FundingOIWindowCalculator` 计算 Z-Score 和 OI 变化率；在 Service 层新增 `FundingOIMetricsProvider` 从 FeatureStore 读取历史数据；扩展 `CryptoRiskBudget` 和 `CryptoRiskSnapshot` 添加 Funding/OI 指标支持。
- 改动: 新增 `trader/core/domain/services/funding_oi_window_calculator.py`（Core 纯计算）；新增 `trader/services/funding_oi_metrics_provider.py`（Service 层 Provider）；扩展 `trader/core/domain/models/crypto_risk.py` 添加 `CryptoFundingOIRiskMetrics` DTO 和 `CryptoRiskBudget`/`CryptoRiskSnapshot` 新字段；更新 `docs/INTERFACE_CONTRACTS.md`（8.5.1 节独立标志）；更新 `docs/PROJECT_ARCHITECTURE.md`（Funding/OI 数据流）。
- 审计修复: 独立标志（`funding_data_stale`/`oi_data_stale`/`funding_window_insufficient`/`oi_window_insufficient`/`funding_current_missing`/`oi_current_missing`）；百分比变化率公式 `(current - mean) / mean * 100`；当前值缺失返回 `None` 不转 `0.0`；环境变量标为"待 P4.8 接入"。
- 验证: Core 计算测试 25 passed；Service Provider 测试 11 passed；相关回归测试 21 passed；所有测试遵循 fail-closed 语义，窗口不足和数据过期正确处理。
- 风险/遗留: 本段是 Funding/OI 历史窗口派生基础；下一步应将 Funding/OI 指标检查接入 `CryptoPreTradeRiskPlugin`。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/PLAN.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-07 09:15 - P4.5 拒绝原因聚合统计 API

- 背景: P4.3 已支持按 event/trace/signal 过滤查询单个审计事件，但运维侧无法快速概览“哪种拒绝原因最频繁、哪个 symbol 被拒绝最多”。
- 决策: 新增 `GET /v1/risk/crypto/audit/summary` 端点，API 层内存聚合，不改 repository；聚合在内存做，PG SQL 聚合留作高基数优化项。
- 改动: `schemas.py` 新增 `CryptoRiskAuditSummaryItem` 和 `CryptoRiskAuditSummaryResponse`；`risk.py` 新增端点（含 Literal 导入），支持 `group_by`（reason/symbol/strategy/risk_level）、`since_ts_ms`、`limit`（默认50）、`event_type`（默认 `crypto_risk.pre_trade_rejected`）；strategy 分组含 `strategy_id → strategy_name → unknown` fallback；所有字段含 None/空字符串 → `unknown` 归一化；新增 12 个测试用例（含 strategy fallback 和 None→unknown 行为覆盖）；接口契约已更新。
- 验证: 12 个红测先失败于 404（端点不存在），实现后全部通过；P4.5 专项 12 passed；相关回归 30 passed；格式门禁 `black --check` ✅、`isort --check-only` ✅。
- 风险/遗留: 聚合在 API 层 O(n) 扫描，>5000 条事件时考虑 PG JSONB expression index + SQL `GROUP BY`；前端 CryptoRiskOps Audit Summary 面板尚未接入该 API，下一步可在 P4.6 或 P4.7 接入。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-07 03:30 - P4.3 DecisionTrace 审计查询闭环

- 背景: P4.2 已把 pre-trade rejection 写入 `risk_audit_events`，但 trace 语义还停留在事件字段，前端也只能通过通用 `/v1/events` 粗看 `risk:crypto`。
- 决策: 将 `decision_trace_id` 作为业务决策链路 ID，并同步到 `MarketRiskAuditEvent.trace_id`；新增 crypto 风控专用审计查询 API，前端运维页走 PG-first 审计源。
- 改动: `crypto_pre_trade_risk_audit.py` 优先使用 `Signal.metadata.decision_trace_id`，payload 增加 `decision_trace_id`；`risk.py` 新增 `GET /v1/risk/crypto/audit` 并让 budget/probe 审计 payload 自动携带 `decision_trace_id`；Frontend `CryptoRiskOps` 增加 event/trace/signal 过滤并调用新 API。
- 验证: P4.3 两条红测先失败于 trace 未标准化和接口 404；实现后风控/API/OMS/PG 单测 33 passed，市场抽象/快照/回测回归 16 passed，真实 PG 集成 40 passed，P0 回归 99 passed，Frontend `npm run typecheck` passed；scoped `py_compile`、`isort --check-only`、`black --check`、`git diff --check` passed。
- 风险/遗留: `signal_id` 当前在 API 层过滤 payload；后续如果审计量变大，应考虑 JSONB expression index 或将 signal_id 升为表字段。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/PLAN.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-06 17:40 - P4.2 Pre-trade 拒绝证据写入市场风险审计

- 背景: P4.1 已让 budget/probe 审计进入 `risk_audit_events`，但真实 pre-trade 风控拒绝仍缺少 PG-first 长期证据，无法按 signal/trace 回放“为什么没下单”。
- 决策: 不把 IO 塞入 `CryptoPreTradeRiskPlugin`；在 Control/Service 装配层新增 audited pre-trade wrapper，观察 `RiskCheckResult` 并写 `MarketRiskAuditEvent`。
- 改动: 新增 `trader/services/crypto_pre_trade_risk_audit.py`；`CryptoRiskRuntimeManager` 在 runtime wiring、预算热更新和 setup fail-closed check 中使用审计 wrapper；接口契约和架构图补充 `crypto_risk.pre_trade_rejected` 事件语义；真实 PG 审计测试兼容分散 Postgres 环境变量。
- 验证: P4.2 红测先失败于审计事件为空；完成实现后相关风控/API/OMS/PG 单测 32 passed，市场抽象/快照/回测回归 16 passed，真实 PG 集成 40 passed，P0 回归 99 passed；scoped `py_compile`、`isort --check-only`、`black --check` passed。
- 风险/遗留: 当前事件已经记录 signal 与 rejection evidence，但尚未串入统一 DecisionTraceId；前端运维页也还没有专门的 pre-trade rejection 查询视图。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/PLAN.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-06 17:28 - P4.1 真实 PG 集成补充验证

- 背景: P4.1 初始验证覆盖了 fake pool、API 和核心回归，但尚未用 Docker Postgres 验证 `risk_audit_events` 的真实 DDL/JSONB/查询路径。
- 决策: 保留 fake pool 单测用于 fallback 与分支覆盖，新增真实 asyncpg 集成用例专门验证市场无关风险审计表。
- 改动: `test_market_risk_audit_repository.py` 增加真实 PG 用例，使用唯一 `trace_id` 写入并清理 `risk_audit_events`，验证 payload、asset/venue 和时间过滤。
- 验证: `POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:5432/trading python -m pytest -q trader/tests/test_postgres_storage.py trader/tests/test_risk_idempotency_persistence.py trader/tests/test_market_risk_audit_repository.py --tb=short` → 40 passed。
- 风险/遗留: 真实 PG 测试共享同一数据库，仍应避免与会执行全库 clear 的 PG 用例并行运行；pre-trade rejection evidence 尚未接入 `risk_audit_events`。
- 关联文档: `PROJECT_STATUS.md`、`docs/PLAN.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-06 17:19 - P4.1 市场无关 PG 风险审计仓储

- 背景: P4.0 已把风险契约抽到 `MarketRisk*`，但 crypto budget/probe 审计仍只落控制面内存事件流；继续做运维和 DecisionTrace 前，需要先把平台级风险审计落到市场无关 PG 表。
- 决策: 新增 `risk_audit_events` 作为平台表，`risk:crypto` 仅作为 `stream_key` 过滤视图；repository 采用 PG-first，同时写内存投影保留旧 `/v1/events` 兼容。
- 改动: 新增 `PostgresMarketRiskAuditStorage`、`MarketRiskAuditRepository` 和迁移 `008_risk_audit_events.sql`；`PATCH /v1/risk/crypto/budget` 与 `POST /v1/risk/crypto/probe` 改为写 `MarketRiskAuditEvent`；`GET /v1/risk/crypto/budget/audit` 改为通过 repository 查询；测试隔离补充 market risk audit repository reset。
- 验证: `test_market_risk_audit_repository.py`、`test_crypto_risk_runtime_api.py`、`test_market_risk_contract.py`、`test_crypto_risk_p0.py` 合计 24 passed；相关模块 `py_compile` passed。
- 风险/遗留: 本轮尚未用 Docker Postgres 跑真实集成；pre-trade rejection evidence 还未进入 `risk_audit_events`，DecisionTrace 也尚未串联。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/PLAN.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-06 16:45 - P4.0 市场无关风险契约抽象

- 背景: 审计发现核心骨架较通用，但风险 DTO、审计契约和回测实现仍被 Crypto/Binance 命名牵引；继续做 PG 审计前需要先补市场无关风险契约。
- 决策: 采用薄抽象，不删除现有 crypto 风控；新增 `MarketRisk*` DTO，让 crypto specialization 可投影到通用契约，并先解开最明显的 Binance 回测数据源硬编码。
- 改动: 新增 `trader/core/domain/models/market_risk.py`；`CryptoInstrumentSpec`、`CryptoAccountRisk`、`CryptoPositionRisk`、`OpenOrderRisk`、`CryptoRiskBudget`、`CryptoRiskSnapshot` 增加 `to_market_*` 投影；`ExchangeRuleGuard`、`OpenOrderExposureCalculator`、`PortfolioExposureAggregator` 改为结构化输入；`VectorBTAdapter` 支持注入 `DataProviderPort`；接口契约、架构图、计划、状态和经验文档同步更新。
- 验证: `python -m pytest -q trader/tests/test_market_risk_contract.py trader/tests/test_crypto_risk_p0.py trader/tests/test_backtesting_vectorbt_adapter.py --tb=short` → 18 passed。
- 风险/遗留: `MarginRiskCalculator` 仍是 crypto/futures 专用；PG 风控审计尚未落库，下一步应以 `MarketRiskAuditRepository` / `risk_audit_events` 为平台契约。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/PLAN.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-06 13:15 - Crypto Risk P3.3c Fail-Closed 负向演练自动化

- 背景: 正常 Binance demo 只读 probe 已通过，但坏 symbol / 缺关键市场数据时仍缺少可重复的负向演练脚本，无法自动证明失败 probe 有审计且没有订单副作用。
- 决策: 新增只读演练脚本，不触发策略、不下单、不撤单；通过 runtime、probe、events、orders 四个只读入口验证 fail-closed 证据链。
- 改动: 新增 `scripts/rehearse_crypto_risk_demo_fail_closed.py` 与 `trader/tests/test_crypto_risk_fail_closed_rehearsal.py`；runbook 增加脚本化 Fail-Closed 演练步骤；同步项目状态与经验总结。
- 验证: 单测 10 passed；真实本地后端演练 PASS，`QTSFAILCLOSEDUSDT` 触发 `ok=false/read_only=true`，failed checks 为 `instrument_specs, leverage_brackets, mark_prices`；匹配 `risk:crypto / crypto_risk.probe_run` 审计事件；`/v1/orders` 前后一致；P0 回归 99 passed；isort/black/py_compile/git diff check passed。
- 风险/遗留: 负向演练仍基于控制面内存事件流；下一步需要 PG 级风控审计持久化，并把 pre-trade rejection 也纳入长期可追溯证据。
- 关联文档: `docs/CRYPTO_RISK_DEMO_RUNBOOK.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`、`docs/PLAN.md`

### 2026-05-06 11:41 - Crypto Risk P3.3b Binance demo 真实只读 Probe 验证

- 背景: P3.3a 已提供 demo preflight 与 runbook；用户确认继续后，需要用本地真实 demo 凭证启动后端并触发 `/v1/risk/crypto/probe`。
- 决策: 先运行 preflight，再启动后端；probe 只走 read-only API，不调用下单、撤单或杠杆调整。发现 `https://demo-api.binance.com/fapi` 返回 404 后，将 USD-M demo source 修正为 `https://demo-fapi.binance.com`，并把该错误组合写入 preflight 阻断。
- 改动: 修复 `scripts/check_crypto_risk_demo_env.py` 直接 CLI 运行时的 repo root import；新增 CLI 回归与 Spot Demo `/fapi` URL 拒绝测试；更新 `.env.example` 与 `docs/CRYPTO_RISK_DEMO_RUNBOOK.md` 的 USD-M demo source。
- 验证: preflight passed；runtime status 显示 `enabled=true/wired=true/fail_closed=false/execution_env=demo`；`POST /v1/risk/crypto/probe` 返回 `ok=true/read_only=true`，account、mark_prices、instrument_specs、leverage_brackets、positions、open_orders、venue_health 均 passed；`risk:crypto / crypto_risk.probe_run` 审计事件已写入；demo 自检测试 6 passed。
- 风险/遗留: 本次仍是只读联通性验证；生产级 PG 风控审计持久化、Funding/OI 风险系数和 fail-closed 负向演练仍需后续推进。
- 关联文档: `docs/CRYPTO_RISK_DEMO_RUNBOOK.md`、`.env.example`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`、`docs/PLAN.md`

### 2026-05-05 22:06 - 安装并固定 isort，补跑 Crypto Risk 运维回归

- 背景: 之前多次 Crypto Risk 任务记录 `isort` 因当前 Python 环境未安装而未执行成功；用户要求安装 `isort` 并跑之前漏跑的检查。
- 决策: 固定 `isort==5.13.2` 到 `pyproject.toml` 和 `trader/requirements-ci.txt`；不做全仓自动格式化，避免把历史导入排序问题混入当前任务。
- 改动: 安装 `isort==5.13.2`；对本次 Crypto Risk 相关 6 个 Python 文件运行 scoped `isort --profile black` 修复导入排序。
- 验证: scoped `isort --check-only --profile black` passed；scoped `black --check` passed；Crypto Risk runtime/API 回归 23 passed；Frontend typecheck passed；Frontend lint passed with 4 pre-existing warnings；Frontend tests 65 passed。
- 风险/遗留: 全仓 `python -m isort --check-only --profile black trader/` 已可运行，但暴露大量历史导入排序遗留；需要单独安排全仓 import sort 收敛任务。
- 关联文档: `PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-04 21:36 - 数字货币独立风控 P3.2c Binance demo 联调入口与前端运维

- 背景: P3.2b 已完成 cluster 预算和审计，但运维仍无法确认已 wired USD-M 风控 source 的只读读取链路，也没有前端入口把 demo 执行环境、source URL、预算和审计放在同一视图。
- 决策: 不新增 Futures 下单能力；只在已 wired runtime 上做 read-only readiness probe，并把 `execution_env` 与 source `mode` 分开，避免把当前 Binance demo 连接误称为 testnet。
- 改动: 后端新增 `execution_env` 状态字段、`POST /v1/risk/crypto/probe` 和 `crypto_risk.probe_run` 审计事件；前端新增 `/crypto-risk` 页面、`cryptoRisk` 类型/API/hooks/Zod 契约、预算输入解析工具与测试；补充前端导航和契约文档。
- 验证: crypto runtime config/manager/API 回归 23 passed；Frontend typecheck passed；Frontend lint passed with 4 pre-existing warnings；Frontend tests 65 passed。
- 风险/遗留: 本次没有自动使用真实 demo 凭证向 Binance 发起外部调用；需要后端以 `CRYPTO_RISK_ENABLED=true` 完成 wiring 后，由运维在 `/crypto-risk` 页或 API 明确触发只读 probe。生产级 PG 风控审计持久化仍待后续。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/PLAN.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`、`Frontend/frontend_docs/FRONTEND_PROJECT_TRACKER.md`

### 2026-05-04 20:55 - 数字货币独立风控 P3.2b 组合级 Cluster 风险预算

- 背景: P0/P1/P2/P3.1 已把单币种 cap、账户总 cap、保证金和强平缓冲接入 pre-trade，但多个 alt 仓位共享 BTC/ETH beta 时仍缺少组合级预算约束。
- 决策: 先实现可配置静态 cluster，而不是直接上动态相关性优化器；cluster 预算进入 `CryptoRiskBudget`，由 Core 纯计算聚合，Policy 层在下单前拒绝超限。
- 改动: 新增 `PortfolioExposureAggregator`；`CryptoRiskBudget` 新增 `symbol_clusters` / `cluster_notional_caps`；`CryptoPreTradeRiskPlugin` 将本次拟下单一起纳入 cluster exposure 并返回 `CRYPTO_CLUSTER_EXPOSURE`；运行时环境变量、状态视图和预算热更新 API 同步支持 cluster 字段。
- 验证: P3.2b/受影响 crypto/runtime/API/risk/OMS 回归 67 passed；P0 回归集 99 passed；`py_compile` passed；scoped `black --check` passed；`git diff --check` passed；`isort` 因当前环境未安装未执行成功。
- 风险/遗留: 当前 cluster 映射依赖人工配置，尚未做动态相关性矩阵、BTC beta adjusted exposure、Funding 系数和 testnet/live 真实联调。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/PLAN.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-04 19:41 - 数字货币独立风控 P3.2a 预算热更新审计

- 背景: P3.1 已提供 runtime status 与预算热更新 API，但预算变更没有历史审计，事故复盘无法知道阈值从多少被谁改到多少。
- 决策: 复用控制面 event log，不混入 AI audit log；成功热更新写入 `risk:crypto` / `crypto_risk.budget_updated`，并提供专用查询入口。
- 改动: `PATCH /v1/risk/crypto/budget` 成功后写入包含 `previous_budget`、`new_budget`、runtime 前后状态和 `updated_by` 的审计事件；新增 `GET /v1/risk/crypto/budget/audit`；失败更新不写成功审计。
- 验证: P3.2a/受影响 crypto/OMS/risk/API 回归 65 passed；P0 回归集 99 passed；`py_compile` passed；scoped `black --check` passed；`git diff --check` passed；`isort` 因当前环境未安装未执行成功。
- 风险/遗留: 审计事件当前写入控制面 in-memory event log；生产级 PG event log 持久化、前端运维入口和 Binance USD-M testnet/live 联调仍待后续。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/PLAN.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-04 19:05 - 数字货币独立风控 P3.1 Runtime API 与预算热更新

- 背景: P2 已把 crypto risk source 接入 lifespan，但 runtime 状态不可查询，风险预算只能通过环境变量静态配置，运维侧无法热更新。
- 决策: 新增 `CryptoRiskRuntimeManager` 作为 lifespan 与 Risk API 共用的单一状态源；热更新只替换 `CryptoRiskBudget`、snapshot provider 和 pre-trade check，不重建 Binance source 或暴露凭证。
- 改动: 新增 runtime manager/status helper；`GET /v1/risk/crypto/runtime` 暴露 enabled/wired/fail_closed/预算/错误；`PATCH /v1/risk/crypto/budget` 支持 symbol/total/margin/强平缓冲预算热更新；`main.py` 改为通过 manager 启用、fail-closed 和关闭。
- 验证: P3.1/受影响 crypto/OMS/risk/API 回归 passed；P0 回归集 99 passed；`py_compile` passed；scoped `black --check` passed；`git diff --check` passed；`isort` 因当前环境未安装未执行成功。
- 风险/遗留: 尚未做 Binance USD-M testnet/live 真实联调；预算热更新仍是进程内配置，后续需要持久化审计和前端运维入口。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/PLAN.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-04 15:21 - 数字货币独立风控 P2 运行时接线

- 背景: P1 已完成 Binance USD-M risk source、snapshot provider 与 OMS pre-trade 注入点，但应用启动链路尚未根据配置创建真实 risk check，也不支持 handler 先创建后的后注入。
- 决策: 把具体 source 装配放在 Control Plane runtime 模块，默认关闭；`CRYPTO_RISK_ENABLED=true` 时由 lifespan 创建 Binance USD-M source/provider/check 并 late-bind 到 OMS，配置或接线失败时注入 fail-closed check。
- 改动: 新增 `trader/api/crypto_risk_runtime.py`；`main.py` 接入 runtime 配置、source 生命周期与 shutdown close；`OMSCallbackHandler` 和策略路由支持 `set_pre_trade_risk_check()` late binding；同步接口契约、架构图、计划和状态文档。
- 验证: P2/受影响 crypto/OMS/risk 回归 53 passed；P0 回归集 99 passed；`py_compile` passed；scoped `black --check` passed；`isort` 因当前环境未安装未执行成功。
- 风险/遗留: 尚未做 Binance USD-M testnet/live 真实联调；风险预算仍是环境变量静态配置，后续进入 P3 做热更新和运维入口。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/PLAN.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-04 10:16 - 数字货币独立风控 P1 快照采集与 OMS 接线

- 背景: P0 已完成 crypto 风控纯计算与 `CryptoPreTradeRiskPlugin`，但真实账户/交易所规则/在途订单快照还没有 Adapter/Service 实现，OMS 下单链路也没有独立风控硬闸。
- 决策: 保持 Core 无 IO，把 Binance 原始字段清洗放在 Adapter mapper/source，把快照聚合放在 Service provider，把最终阻断点接到 OMS 的 `pre_trade_risk_check` 注入回调。
- 改动: 新增 `crypto_risk_mapper.py`、`crypto_risk_source.py`、`crypto_risk_snapshot.py`；`OMSCallbackHandler` 支持 pre-trade 风控拒绝/异常 fail-closed；策略路由增加 `set_pre_trade_risk_check()`；同步接口契约和架构图。
- 验证: P1 新增测试 13 passed；受影响 OMS/crypto/risk 回归 28 passed；P0 回归集 99 passed；`py_compile` passed；scoped `black --check` passed。
- 风险/遗留: 生产环境还需要在 lifespan/配置层实例化 Binance USD-M source、风险预算与 risk check，并用真实 testnet/live key 做联调；当前单测不访问网络。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/PLAN.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-04 09:40 - 安装并固定 Black 格式化工具

- 背景: 上次 crypto 风控 P0 落地后需要按项目约定运行 `black --line-length 100`，但当前 Python 环境未安装 Black。
- 决策: 将 Black 写入 `pyproject.toml` 和 `trader/requirements-ci.txt`，并选择与仓库固定 Python 3.12.5 实际兼容的版本。
- 改动: 安装 `black==24.4.2`；依赖文件新增同版本 pin；对 crypto 风控相关 Python 文件与 `risk_engine.py` 运行 scoped black，避免格式化无关工作区改动。
- 验证: `python -m black --version` 显示 24.4.2；`black --check` 8 files unchanged；`py_compile` passed；`test_crypto_risk_p0.py` 7 passed；风控核心回归 157 passed。
- 风险/遗留: Black 25.1.0/26.3.1 在 Python 3.12.5 上会因 AST safety check 风险硬阻断；若项目未来升级到 Python 3.12.6+，可再评估升级 Black。
- 关联文档: `PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-03 00:00 - 数字货币独立风控 P0 模块落地

- 背景: 用户要求按“风控系统独立于策略”的原则执行完善计划；现有通用风控框架已经存在，但数字货币合约特有的交易所规则、在途订单风险、mark price、leverage bracket 和保证金风险还没有统一门禁。
- 决策: 先落 P0 生存级纯计算能力，不直接接 Binance IO；Adapter/Service 后续负责构建 `CryptoRiskSnapshot`，Core/Policy 只做确定性审批，策略不能绕过。
- 改动: 更新接口契约与架构图；新增 `crypto_risk.py` DTO；新增 `ExchangeRuleGuard`、`OpenOrderExposureCalculator`、`MarginRiskCalculator`；新增 `CryptoPreTradeRiskPlugin` 接入 `RiskEngine` pre-trade 插件体系；扩展 crypto 风控拒绝原因与 KillSwitch 推荐。
- 验证: `test_crypto_risk_p0.py` 7 passed；风险相关回归 157 passed；新增/修改 Python 文件 `py_compile` passed；P0 回归集 99 passed；`black` 未安装，格式化命令未执行，已做 100 字符长行扫描。
- 风险/遗留: 当前尚未接入真实 Binance futures/account/exchangeInfo/leverage bracket 快照，下一步应实现 `CryptoRiskSnapshotProvider` 并把规则/mark price/open orders/account margin 从 Adapter 边界转换为 Core DTO。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/PLAN.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-30 16:48 - 修复 Strategy Management 空白页

- 背景: 用户反馈 `http://localhost:5173/strategies` 打开为空白。
- 决策: 修复运行时空值渲染，而不是改动代码展示 API；详情弹窗只有在选中策略时才需要挂载。
- 改动: `Strategies.tsx` 将 `StrategyDetailModal` 包裹在 `detailStrategy && (...)` 中，移除运行时 `null` 被非空断言掩盖的问题。
- 验证: Frontend `npm run typecheck` passed；`/strategies` dev server 路由返回 200。
- 风险/遗留: 诊断时发现 `npm run build` 仍受既有 `tsconfig.node.json` 配置影响失败，后续可单独处理。
- 关联文档: `PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-30 16:45 - Strategy Details Info 展示最新策略代码

- 背景: 用户希望在 Strategy Management 页面的 Strategy Details 卡片 Info 项下直接看到策略代码，方便核对运行/管理对象对应的源码。
- 决策: 复用已有 `/v1/strategies/{strategy_id}/code/latest` API，不新增后端接口；在前端 hook 层增加 `useLatestStrategyCode`，由详情弹窗 Info 页签按需加载。
- 改动: `StrategyDetailModal` 新增 Strategy Code 区块，显示最新 code version、checksum、创建时间和代码内容；未保存代码的 entrypoint 策略显示无保存代码提示。
- 验证: Frontend `npm run typecheck` passed。
- 风险/遗留: 当前只展示最新版本；如果需要审查历史版本和变更差异，后续应增加 code version selector 和 diff 视图。
- 关联文档: `PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-30 16:37 - Research 页面支持删除 StrategyCandidate

- 背景: Research 页面只有 Create Candidate 和列表，没有删除误建/废弃候选的操作；用户明确指出这一前端缺口。
- 决策: 增加安全删除而不是无条件删除；只删除研究候选实体，不删除策略模板、代码版本、回测报告或 deployment，并保护已进入运行关系的状态。
- 改动: 新增 `DELETE /v1/strategy-candidates/{candidate_id}`、storage 删除方法、service 删除保护和 `strategy_candidate.deleted` 事件；Research 页面增加 Delete 按钮，受保护状态禁用；接口契约补充删除语义。
- 验证: `test_strategy_candidate_workflow.py` 6 passed；相关后端模块 `py_compile` passed；Frontend `npm run typecheck` passed。
- 风险/遗留: 未来切到 PG 持久化时，需要把 hard delete/soft delete 策略定清楚；当前第一版按控制面列表删除并保留审计事件。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-30 17:29 - Strategy Details 支持模块 entrypoint 源码视图

- 背景: Strategy Management 的 Strategy Details 已能展示最新 saved code，但内置 `trader.*` module entrypoint 策略没有 saved code version，用户打开后仍只能看到无代码提示。
- 决策: 不改变 `/code/latest` 的版本语义，新增只读 `StrategyCodeView`；优先返回 saved code，缺省时安全读取本仓库 `trader.*` 模块源码。
- 改动: 新增 `StrategyCodeView` DTO 与 `GET /v1/strategies/{strategy_id}/code/view`；限制源码读取在 `trader` 包内 `.py` 文件；Frontend Strategy Details 改用 code view 展示 `saved code` 或 `module entrypoint`。
- 验证: `test_strategy_code_view.py` 2 passed；后端 `py_compile` passed；Frontend `npm run typecheck` passed；`git diff --check` passed。
- 风险/遗留: 当前只显示最新 saved code 或完整模块源码；后续若需要历史审查，应新增版本选择、diff 和权限控制。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-05-05 22:51 - Crypto Risk P3.3 Binance demo 联调自检与运行手册

- 背景: Crypto Risk 已有 runtime/probe/API/前端运维入口，但真实 demo 联调前缺少本地 preflight，`.env.example` 仍偏 testnet 口径，容易把执行环境和只读 USD-M 风控 source 混淆。
- 决策: 先补“无网络、无凭证打印”的 demo 自检脚本和 runbook，不在本次任务中触发真实 Binance probe 或下单 smoke。
- 改动: 新增 `scripts/check_crypto_risk_demo_env.py` 与 `trader/tests/test_crypto_risk_demo_env_check.py`；新增 `docs/CRYPTO_RISK_DEMO_RUNBOOK.md`；更新 `.env.example`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`、`docs/PLAN.md`。
- 验证: 新增测试先失败于脚本不存在，完成实现后 demo 自检测试 4 passed；Crypto Risk runtime/API 回归 23 passed；P0 回归 99 passed；isort/black check passed；脚本 `py_compile` passed；`git diff --check` passed。
- 风险/遗留: 尚未用真实 demo 凭证运行后端并触发 `/v1/risk/crypto/probe`；PG 级风控审计持久化和 Funding/OI 自动风险系数仍在后续 P3.3b/P4。
- 关联文档: `docs/CRYPTO_RISK_DEMO_RUNBOOK.md`、`.env.example`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`、`docs/PLAN.md`

### 2026-05-05 22:32 - 全仓 Python 格式化收敛与 CI 门禁

- 背景: `isort` 安装固定后，全仓检查暴露历史导入排序和 Black 格式债；用户要求先提交当前代码，再做一次纯格式化提交，并将 `black`/`isort` 加入 CI 门禁。
- 决策: 将依赖/scoped 修复、纯格式化、CI 门禁拆成连续独立提交；纯格式化提交记录到 `.git-blame-ignore-revs`，降低后续 blame 噪音。
- 改动: 新增 `Python Formatting Gate`，在 CI 中执行 `python -m isort --check-only --profile black trader/` 和 `python -m black --check --line-length 100 trader/`；新增 `.git-blame-ignore-revs` 指向格式化提交 `0df5107`；同步项目状态与经验总结。
- 验证: `python -m isort --check-only --profile black trader/` passed；`python -m black --check --line-length 100 trader/` passed；核心域/应用层回归 passed；PG/快照持久化集成测试 passed；Binance/Crypto Risk 回归 74 passed。
- 风险/遗留: CI 会阻断新的格式漂移；仍有既有 Pydantic V2 deprecated config 与 unknown integration mark warnings 待后续清理。
- 关联文档: `.github/workflows/ci-gate.yml`、`.git-blame-ignore-revs`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-30 16:17 - 端到端研究与自动组合运行第一版纵向切片

- 背景: 用户目标升级为从 Crypto 多源数据、策略开发、回测、门禁、部署、仓位管理到组合自动启停的流畅工作台；现有仓库已有很多后端能力，但 Strategy Lab 前端契约断裂，生命周期、仓位和自动组合控制未形成统一入口。
- 决策: 先做可验证纵向切片，而不是一次性重写全系统；新增 `StrategyCandidate` 作为研究到运行主实体，`dev_smoke` 回测默认不能作为部署准入，仓位分配和 Portfolio Autopilot 先落控制面 API 与审计事件。
- 改动: 更新接口契约和架构图；新增 strategy candidates、allocations、portfolio autopilot、data catalog 后端路由与服务；扩展 backtest DTO/metrics；修复 Frontend Strategy Lab 的 `deployment_id` 链路；新增 Data、Research、Portfolio Allocation、Portfolio Autopilot 页面入口。
- 验证: `test_strategy_candidate_workflow.py` 4 passed；`test_api_strategy_runner_endpoints.py` 3 passed；新增后端模块 `py_compile` passed；Frontend `npm run typecheck` passed。
- 风险/遗留: 生产级 PG 持久化、真实 FeatureStore 回测读取、自动组合控制器对 live runtime 的安全执行、Allocation/RiskSizer 在 OMS 主下单路径的强制接入仍是后续任务。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-29 23:18 - 新增项目架构图文档与维护约束

- 背景: 仓库已有长篇架构说明，但缺少快速查看当前层级边界、主数据流和关键闭环的架构图入口；架构变更时也没有明确要求同步图文。
- 决策: 新增 `docs/PROJECT_ARCHITECTURE.md` 作为当前架构图文真相源，并将架构图同步要求写入 `AGENTS.md`、`CLAUDE.md`、`.traerules`。
- 改动: 增加五层平面架构图、主数据流图、策略下单闭环、对账恢复闭环、文档契约关系和架构变更更新规则；同步三份规则入口和项目状态。
- 验证: 文档规范变更，无代码测试。
- 风险/遗留: 架构图需要在后续架构性改动中持续维护，否则会退化为静态说明。
- 关联文档: `docs/PROJECT_ARCHITECTURE.md`、`AGENTS.md`、`CLAUDE.md`、`.traerules`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-29 23:13 - 新增 AI TDD 防幻觉流程约束

- 背景: AI 修改代码时容易先臆造函数、DTO 或接口，再围绕虚构实现补测试；仅要求“有测试”不足以防止这种幻觉。
- 决策: 将 TDD 写入 `AGENTS.md`、`CLAUDE.md`、`.traerules` 的公共工程约束，要求行为变更先写基于真实接口的失败测试，再做最小实现和重构。
- 改动: 新增 Red / Green / Refactor 流程、No Hallucinated API 规则和 Verification 要求；明确不存在的新接口必须先更新 `docs/INTERFACE_CONTRACTS.md`。
- 验证: 文档规范变更，无代码测试。
- 风险/遗留: TDD 约束需要在后续实际编码任务中执行；文档本身不能替代测试执行。
- 关联文档: `AGENTS.md`、`CLAUDE.md`、`.traerules`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-29 23:04 - 同步 `.traerules` 与 AI 规则入口

- 背景: `.traerules` 也是 Trae/Kilo 等工具入口的工程规则，但它与 `AGENTS.md`、`CLAUDE.md` 在架构边界、测试规范、文档闭环和技术栈约束上存在口径漂移。
- 决策: 保留 `.traerules` 原有分支/PR/AI 集成规则，同时把公共工程约束收敛到与 `AGENTS.md`、`CLAUDE.md` 一致，并在三份规则入口都加入同步检查要求。
- 改动: 扩展 `.traerules` 的任务处理原则、项目扫描、常用命令、五层架构、测试规范、工程纪律、文档闭环和红线操作；更新 `AGENTS.md`、`CLAUDE.md` 的规则入口同步要求。
- 验证: 文档规范变更，无代码测试。
- 风险/遗留: 三份规则文档仍存在面向不同工具的专属章节；后续只要求公共工程约束一致，不要求逐字相同。
- 关联文档: `.traerules`、`AGENTS.md`、`CLAUDE.md`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-29 22:50 - 新增接口契约与命名规范约束

- 背景: AI 与多人协作修改代码时，函数签名、DTO、事件字段和跨层命名容易漂移，导致同一概念出现多个名称或同名不同义。
- 决策: 新增 `docs/INTERFACE_CONTRACTS.md` 作为接口契约与命名规范单一真相源，并把“先契约、后实现”纳入 `AGENTS.md`、`CLAUDE.md` 与 `.traerules`。
- 改动: 新增标准领域词汇、外部字段映射、跨层接口规则、事件 Schema 规则、接口变更流程和 AI 修改前检查清单；同步更新协作约束和项目状态。
- 验证: 文档规范变更，无代码测试。
- 风险/遗留: 契约文档需要在后续接口改名时持续维护，否则会再次退化为过期说明。
- 关联文档: `docs/INTERFACE_CONTRACTS.md`、`AGENTS.md`、`CLAUDE.md`、`.traerules`、`PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-28 14:44 - 同步 Claude 与 Agents 文档闭环要求

- 背景: `AGENTS.md` 已纳入 `DEVELOPMENT_LOG.md` 和计划文档新鲜度要求，但 `CLAUDE.md` 仍使用旧版 Documentation Updates 规则。
- 决策: 以 `AGENTS.md` 的 Mandatory Workflow 为准，同步更新 `CLAUDE.md`，让不同大模型入口遵守一致的项目文档闭环。
- 改动: 替换 `CLAUDE.md` 的 Documentation Updates 段落，明确 `PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`、`DEVELOPMENT_LOG.md` 与计划文档更新条件。
- 验证: 文档规范变更，无代码测试。
- 风险/遗留: 后续若再次修改协作规范，需要同时检查 `AGENTS.md` 与 `CLAUDE.md`。
- 关联文档: `CLAUDE.md`、`PROJECT_STATUS.md`

### 2026-04-28 14:41 - 测试全局状态污染隔离与全量回归恢复

- 背景: P0 回归和多个目标测试单独运行通过，但全量测试受全局状态、环境变量、logger 与 collection-time mock 污染影响，出现顺序相关失败。
- 决策: 把测试隔离收敛到 pytest autouse fixture，显式重置控制面/服务层单例、敏感环境变量和 `asyncpg` module；同时修复会触发真实 broker 或旧 API 兼容失败的控制面入口。
- 改动: 新增 `trader/tests/conftest.py` 隔离夹具；扩展 strategy routes reset hook 与 live=false broker 短路；补充 strategy event reset；兼容 int/string version；恢复 legacy deployment start/stop；将控制面 backtest 改为确定性 synthetic 路径；调整少量旧测试断言到当前 API 语义。
- 验证: `python -m pytest -q trader/tests/ --tb=short` passed；P0 回归集 passed。
- 风险/遗留: 仍有既有 warnings 待后续清理，包括 Pydantic V2 deprecated config、unknown integration mark 和 onchain AsyncMock await warning。
- 关联文档: `PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-28 07:49 - OMS Pre-trade 余额 Gate 完善

- 背景: 策略运行时持续出现交易所 `insufficient balance` 拒单，需要把交易所拒单从常态控制流降级为最后防线。
- 决策: 在 OMS 下单前执行本地余额 gate，账户余额不可获取时 fail-closed，并用短 TTL reservation 降低连续信号超额提交风险。
- 改动: 更新 `trader/services/oms_callback.py`，新增 `trader/tests/test_oms_pretrade_balance.py`，并补齐 E2E fake broker 的账户/行情接口。
- 验证: `test_oms_pretrade_balance.py`、`test_runtime_observability.py`、`test_oms_idempotency.py`、`test_automated_trading_e2e.py` 合计 56 passed。
- 风险/遗留: 当前 reservation 仍是进程内短 TTL 防抖，不替代独立 AccountState/ExecutionBudget 服务。
- 关联文档: `PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`

### 2026-04-28 07:55 - 增加开发记录文档规范

- 背景: `PLAN.md` 适合记录计划，`PROJECT_STATUS.md` 适合记录阶段状态，但缺少按时间追加的开发过程记录。
- 决策: 新增 `DEVELOPMENT_LOG.md` 作为开发流水账，后续开发除更新计划/状态/经验文档外，也追加开发记录。
- 改动: 新增开发记录模板与维护规则。
- 验证: 文档变更，无代码测试。
- 风险/遗留: 需要在协作规范中同步要求，避免后续遗漏。
- 关联文档: `AGENTS.md`、`PROJECT_STATUS.md`

### 2026-05-07 08:37 - 后续 Crypto Risk 计划加入审计停顿要求

- 背景: 用户要求后续每完成一段开发计划必须停下来输出必要信息，便于主审对照代码库变动审计代码，避免 AI 连续推进多个阶段导致漂移。
- 决策: 将停顿要求写入当前计划入口 `docs/PLAN.md`，自 P4.5 起作为硬性执行规则。
- 改动: `docs/PLAN.md` 新增“后续 Crypto Risk 开发停顿与审计交接要求”；`PROJECT_STATUS.md` 增加计划治理记录。
- 验证: 文档计划治理变更，无代码测试。
- 风险/遗留: 后续执行 P4.5-P9 时，必须在每段结束后等待审计通过，不能自行进入下一段。
- 关联文档: `docs/PLAN.md`、`PROJECT_STATUS.md`
