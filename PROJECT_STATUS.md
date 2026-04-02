# 项目开发状态追踪

> 本文件记录项目各模块的当前状态和测试验证结果
> 更新方法：`run_tests.bat` 后手动更新本文件，或运行 `scripts/update_project_status.py`

## 最后更新时间
2026-04-02 (北京时间)

## 分支状态
- **当前分支**：`main`
- **基于**：`main`
- **工作树**：干净
- **最新提交**：Architecture文档更新 - 添加策略管理层

## 最近开发记录（滚动式）

### 本次任务：Phase 5 回测框架升级完成
- 完成时间: 2026-03-31
- 分支: main (直接提交)
- 状态: ✅ 全部完成
- 主要变更: 
  - Task 5.7: 自研回测模块归档 - deprecated 标记 + 迁移指南
  - Task 5.9: 性能基准测试 - PerformanceBenchmark, BenchmarkRunner
  - Phase 5 全部 9 个 Task 完成
- 测试结果: 267 tests passing (Phase 5)

### 上次任务：Phase 5 回测框架升级 Week 2
- 完成时间: 2026-03-31
- 状态: ✅ 主要任务完成
- 主要变更: 
  - Task 5.3: 回测结果标准化与可视化 - StandardizedBacktestReport, BacktestVisualizer
  - Task 5.4: 样本外验证框架 - WalkForwardAnalyzer, KFoldValidator, SensitivityAnalyzer
  - Task 5.5: StrategyLifecycleManager 集成 - AutoApprovalRules, BacktestJob
  - Task 5.6: 数据管道优化 - DataCache, DataValidator, ParallelBacktestRunner
  - Task 5.8: 回测框架测试套件 - 226 tests passing

### 上次任务：Phase 5 回测框架升级 Week 1
- 完成时间: 2026-03-31
- 分支: main (直接提交)
- 状态: ✅ 架构和适配层完成
- 主要变更:
  - Task 5.1: 框架选型完成（QuantConnect Lean - Apache 2.0）
  - Task 5.2: 核心适配层 - ports, quantconnect_adapter, strategy_adapter, execution_simulator, result_converter
  - 修复 Task 5.2.4 bug: insight.direction 大小写转换问题

### 上上次任务：Phase 4 核心文档更新
- 完成时间: 2026-03-31
- 分支: main (直接提交)
- 状态: ✅ 文档更新完成
- 主要变更: 
  - Architecture文档新增第9节 Strategy Management Plane
  - 能力分层表更新，Phase 4所有能力标记为Current
  - 策略生命周期状态机、核心组件、对接架构文档化

### 上次任务：Task 2.5 资金结构信号
- 完成时间: 2026-03-25
- 分支: task/2.5-capital-structure-signals
- 状态: ✅ 已合并到main
- 主要变更: FeatureStore范围查询, 多空比数据适配器, 三大信号计算

### 上次任务：Task 2.4 基础信号层（趋势+价量）
- 完成时间: 2026-03-25
- 分支: main (直接提交)
- 状态: ✅ 已合并
- 主要变更: trend_signals.py, price_volume_signals.py, signal_sandbox.py, 64个测试全部通过

### 下次计划：Phase 6 Risk Convergence & Allocation
- 目标: 从“继续扩功能”转为“统一真相源 + 生存风控收敛 + 轻量资本分配”
- 前置条件: Phase 5 全部完成（✅）
- Phase 5 总结: 已完成 QuantConnect Lean 集成、验证框架、性能基准与迁移归档，下一阶段不再以回测功能扩张为主线

**Phase 6 核心优先级**：
1. 文档单一真相源收敛：统一 `PROJECT_STATUS.md`、`PLAN.md`、`plans/phase6_risk_convergence.md`
2. 统一 `risk_sizer`：把时间窗口、流动性、回撤、策略级限额、venue 健康度收敛到一个 sizing 决策
3. 回撤去杠杆与 venue 健康度联动：先缩仓/close-only，再升级到 KillSwitch
4. 轻量 `capital_allocator`：处理多策略并发时的净暴露、预算竞争与冲突裁决
5. 替代数据健康度治理：把 freshness、coverage、delay 纳入信号放行与仓位缩放

**本次文档修订说明**：
1. 明确 Phase 6 主线为 Risk Convergence，而非继续扩 AI/回测表面功能
2. 将“个人版生存风控”定义为优先于“机构级组合风控”
3. 把文档一致性问题提升为 P0 工程任务
4. 后续执行清单下沉到 `plans/phase6_risk_convergence.md`

## Phase 1: M1 安全闭环

### 已验证任务

| Task | 模块 | 测试文件 | 测试数 | 通过数 | 状态 | 最后验证 |
|------|------|----------|--------|--------|------|----------|
| 1.1 | Feature Store | test_feature_store.py | 14 | 14 | ✅ | <!--2026-03-23--> |
| 1.2 | Reconciler | test_reconciler.py | 13 | 13 | ✅ | <!--2026-03-23--> |
| 1.3 | 深度检查 | test_depth_checker.py | 19 | 19 | ✅ | <!--2026-03-23--> |
| 1.4 | 时间窗口 | test_time_window_policy.py | 29 | 29 | ✅ | <!--2026-03-23--> |
| P0 | 核心回归 | (5个P0文件) | 93 | 93 | ✅ | <!--日期--> |
| PG | 集成测试 | test_postgres_storage.py | 32 | 32 | ✅ | <!--日期--> |

**Phase 1 核心验证总计：168/168 测试通过**

### 待确认任务

| Task | 模块 | 状态 | 备注 |
|------|------|------|------|
| 1.5 | 策略监控 | ✅ 已验收 | API端点完整，10个测试全部通过 |
| 1.6 | 事件溯源 | ✅ 已验收 | PG落地完整，17个测试全部通过 |

## Phase 2: M2 数据就绪

| Task | 模块 | 状态 | 备注 |
|------|------|------|------|
| 2.1 | Funding/OI适配器 | ✅ 已完成 | 已实现 funding_oi_stream.py，21个测试通过 |
| 2.2 | OnChain适配器 | ✅ 已完成 | **本次优化**: perf(task-2.2) - _flush_bucket_locked I/O锁优化，新增部分失败场景测试 |
| | | | | **原实现**: OnChainMarketDataAdapter + LiquidationAggregator + BinanceLiquidationWSConnector |
| | | | | **测试**: test_onchain_market_data_stream.py (66 tests 全部通过) |
| 2.3 | 公告爬虫 WS 迁移 | ✅ 已完成 | **分支**: feature/task-2.3/announcement-crawler-tests |
| | | | **实现内容**: WebSocket-first架构，RawAnnouncement统一模型，ws_source.py + html_source.py 双源 |
| | | | **测试**: 74个测试全部通过，test_announcements_crawler*.py |
| 2.4 | 基础信号层（趋势+价量） | ✅ 已完成 | **完成时间**: 2026-03-25 |
| | | | **提交**: feat(task-2.4) |
| | | | **实现内容**: trend_signals.py - EMA交叉、价格动量、布林带; price_volume_signals.py - 成交量扩张、波动率压缩; signal_sandbox.py - 信号沙箱工具 |
| | | | **测试**: test_trend_signals.py (32 tests), test_price_volume_signals.py (32 tests), 全部通过 |
| 2.5 | 资金结构信号 | ✅ 已完成 | **完成时间**: 2026-03-25 |
| | | | **分支**: task/2.5-capital-structure-signals |
| | | | **实现内容**: capital_structure_signals.py - Funding rate z-score、OI变化率+价格背离检测、多空比异常检测 |
| | | | **测试**: test_capital_structure_signals.py，1000+行测试代码 |

## Phase 3: 信号层增强

| Task | 模块 | 状态 | 备注 |
|------|------|------|------|
| 3.1 | PG投影读模型 | ✅ 完成 | **完成时间**: 2026-03-24/25 |
| | | | **分支**: task/7.2-pg-projection-read-model |
| | | | **实现内容**: PostgreSQL 投影读模型完整体系（PositionProjector, OrderProjector, RiskProjector） |
| | | | **代码优化**: |
| | | | - `order_projector.py`: `get_order_by_client_order_id` 索引查询优化 |
| | | | - `position_projector.py`: `_apply_position_increased` 重构 |
| | | | - `risk_projector.py`: `EventType` 枚举引入，统一事件类型 |
| | | | **测试结果**: 44 个单元测试 + 766 全量测试通过 |
| | | | **新增文件**: projectors/__init__.py, base.py, position_projector.py, order_projector.py, risk_projector.py |
| | | | migrations/003_projections.sql, tests/test_postgres_projectors.py | | 3.2 | Escape Time模拟器 | ✅ 已完成 | 包含 `EscapeTimeSimulator` 核心模拟器和 29 个单元测试（commit 17c03f7） |
| 3.3 | Replay Runner | ✅ 已完成 | `ReplayRunner` 核心重放器和 22 个单元测试 |
| 3.4 | AI治理接口（HITL） | ✅ 已完成 | `HITLGovernance` AI建议审核治理器和 37 个单元测试 |

## Phase 4: 策略管理与AI共创

### Task 4.7: 策略参数动态调整 ✅ 已完成

- **完成时间**: 2026-03-31
- **分支**: feature/task-4.7-strategy-param-adjustment
- **状态**: ✅ 已完成
- **主要目标**: 
  添加动态调整策略参数的能力，无需停止或重载策略即可更新配置。

- **主要变更**:

   1. **StrategyPlugin 协议增强** (`trader/core/application/strategy_protocol.py`):
      - 新增 `update_config(config: Dict[str, Any]) -> ValidationResult` 方法
      - 支持动态参数更新，无需重启策略
      - 返回验证结果，支持参数有效性校验

   2. **StrategyRunner 增强** (`trader/services/strategy_runner.py`):
      - 新增 `update_strategy_config(strategy_id, config)` 方法
      - 支持部分更新（增量更新）
      - 兼容不支持 `update_config()` 的插件（使用 `initialize()` 后备）

   3. **StrategyLifecycleManager 增强** (`trader/services/strategy_lifecycle_manager.py`):
      - 新增 `LifecycleEventType.PARAMS_UPDATED` 事件类型
      - 新增 `UpdateParamsOutcome` 结果类型
      - 新增 `update_strategy_params()` 方法
      - 参数变更记录到生命周期事件历史

   4. **API 路由增强** (`trader/api/routes/strategies.py`):
      - 新增 `PUT /v1/strategies/{strategy_id}/params` 端点
      - 新增 `UpdateStrategyParamsRequest` 请求模型
      - 新增 `UpdateStrategyParamsResponse` 响应模型
      - 支持 `validate_only` 模式（仅验证参数）

   5. **单元测试** (`trader/tests/test_strategy_runner.py`):
      - 新增 `MockStrategyPluginWithUpdateConfig` 测试插件
      - 新增 `TestStrategyConfigUpdate` 测试类
      - 新增 5 个测试用例：
        - `test_update_config_success`: 成功更新配置
        - `test_update_config_validation_failure`: 配置验证失败
        - `test_update_config_not_loaded`: 更新未加载策略
        - `test_update_config_without_update_method_uses_initialize`: 后备方案
        - `test_update_config_partial_update`: 部分更新

- **测试结果**:
   - `test_strategy_runner.py`: 41/41 通过 ✅ (新增5个测试)
   - P0 回归测试: 93/93 通过 ✅

- **验收标准检查**:
  - ✅ 策略运行中可调整参数（无需重启）
  - ✅ 参数变更记录可追溯（LifecycleEventType.PARAMS_UPDATED）
  - ✅ 无效参数被拒绝并返回错误
  - ✅ 部分更新支持（增量更新）
  - ✅ API 支持 validate_only 模式

- **代码审核修复**（2026-03-31）:
  - 修复 `strategy_runner.py` 中 `except TypeError` 块的逻辑错误
  - 修复 `strategies.py` API 中直接访问私有属性问题
  - 将 `update_config` 添加到 `validate_strategy_plugin()` 的必需方法列表
  - 更新 `MockStrategyPlugin` 测试类以符合协议要求
  - 修复 `test_strategy_hotswap.py` 中 `FakeStrategyPlugin` 和 `FakeValidationErrorStrategy` 缺少 `update_config` 方法

### Task 4.1: StrategyRunner 单测验证与增强 ✅ 已完成

- **完成时间**: 2026-03-30
- **分支**: main (直接提交)
- **主要变更**:
  1. **StrategyRunner 增强** (`trader/services/strategy_runner.py`):
     - 集成 `StrategyResourceLimits` 进行资源限制检查（订单频率、持仓大小、日亏损、超时控制）
     - 与 `KillSwitch` 对接：当 KillSwitch 升级到 L1+ 时自动阻止新订单，L2+ 时自动停止策略
     - 与 `OMS` 对接：策略信号通过 OMS 回调执行订单
     - 新增 `blocked_reason` 字段用于记录阻塞原因
     - 新增 `resource_limits` 字段用于配置资源限制

  2. **API Routes 增强** (`trader/api/routes/strategies.py`):
     - `LoadStrategyRequest` 新增资源限制参数（max_position_size, max_daily_loss, max_orders_per_minute, timeout_seconds）
     - `StrategyStatusResponse` 新增 `blocked_reason` 字段
     - API 端点支持策略加载时配置资源限制

  3. **Bug 修复**:
     - 修复 `trader/core/application/__init__.py` 中 `OrderRepository` 导入错误

  4. **单元测试** (`trader/tests/test_strategy_runner.py`):
     - 新增 `TestStrategyResourceLimits` 类：测试资源限制集成、订单频率限制、OMS回调
     - 新增 `TestKillSwitchIntegration` 类：测试KillSwitch L0/L1/L2/L3 各级别行为
     - 新增 `TestTimeoutControl` 类：测试策略执行超时控制
     - 新增 13 个测试用例，测试总数从 23 增至 36

- **测试结果**:
  - `test_strategy_runner.py`: 36/36 通过 ✅
  - `test_api_endpoints.py` (strategy相关): 4/4 通过 ✅
  - P0 回归测试: 93/93 通过 ✅

- **验收标准检查**:
  - ✅ API启动/停止策略
  - ✅ 崩溃隔离（策略崩溃不影响主系统）
  - ✅ 状态可查询
  - ✅ P0不回归
  - ✅ P99<500ms（异步测试均配置超时保护）
  - ✅ StrategyResourceLimits正确执行资源限制

### Task 4.2: StrategyEvaluator 策略评估器 ✅ 已完成

- **完成时间**: 2026-03-30
- **分支**: main (直接提交)
- **主要变更**:
  1. **新增 `trader/services/strategy_evaluator.py`**:
     - `StrategyMetrics`: 策略性能指标（total_pnl, sharpe_ratio, max_drawdown, win_rate, trade_count, profit_factor等）
     - `BacktestReport`: 完整回测报告（包含指标、交易记录、数据质量、执行时间）
     - `BacktestEngine`: 回测引擎，支持历史数据重放和性能计算
     - `LiveEvaluator`: 实时评估器，支持异常检测和回测对比
     - `FeatureStorePort`: 特征存储端口接口（Protocol）
     - `DataQualityResult`: 数据质量验证结果
     - `EvaluationResult`: 评估结果（包含告警和建议）
     - 辅助函数: `calculate_sharpe_ratio`, `calculate_max_drawdown`

  2. **新增单元测试 `trader/tests/test_strategy_evaluator.py`**:
     - `TestStrategyMetrics`: 测试指标创建、类型转换、摘要属性
     - `TestBacktestReport`: 测试报告创建、收益率计算、字典转换
     - `TestDataQualityResult`: 测试数据质量验证逻辑
     - `TestBacktestEngine`: 测试回测引擎（包含性能测试 <1分钟）
     - `TestLiveEvaluator`: 测试实时评估器（正常/告警状态）
     - `TestCalculateSharpeRatio`: 测试夏普率计算
     - `TestCalculateMaxDrawdown`: 测试最大回撤计算
     - `TestBacktestLiveEvaluatorIntegration`: 测试完整流程集成
     - 测试总数: 46个

  3. **设计特性**:
     - 严格幂等：评估结果可重复计算
     - 数据质量验证：覆盖间隙检测、OHLC有效性
     - 性能保证：1年数据（每小时K线）< 1分钟完成
     - 异常检测：连续亏损、权益曲线spike、胜率下降

- **测试结果**:
  - `test_strategy_evaluator.py`: 46/46 通过 ✅

- **验收标准检查**:
  - ✅ 回测报告含夏普率/最大回撤/胜率
  - ✅ 实时指标可查（LiveEvaluator.evaluate）
  - ✅ 数据质量验证（DataQualityResult）
  - ✅ 1年数据<1分钟（性能测试通过）
  - ✅ FeatureStorePort接口定义，支持FeatureStore适配

### Task 4.3: 策略热插拔机制 ✅ 已完成

- **完成时间**: 2026-03-30
- **分支**: main (直接提交)
- **主要变更**:

   1. **新增 `trader/services/strategy_hotswap.py`**:
      - `StrategyLoader`: 策略加载器，支持从模块路径或代码字符串加载
        - `load_from_module()`: 从模块路径加载策略
        - `load_from_code()`: 从代码字符串加载策略
        - `compute_checksum()`: 代码校验和计算
        - 支持签名验证、沙箱执行、资源限制检查
      
      - `VersionManager`: 版本管理器
        - `save_version()`: 保存策略版本
        - `load_version()`: 加载指定版本
        - `list_versions()`: 列出版本历史
        - `set_active_version()`: 设置活跃版本
        - `add_swap_history()`: 添加切换历史

      - `StrategyHotSwapper`: 热插拔管理器
        - 状态机: `IDLE -> LOADING -> VALIDATING -> PREPARING -> SWITCHING -> ACTIVE`
        - 回滚机制: `SWITCHING -> ROLLING_BACK -> IDLE`
        - 挂单处理: 切换前自动取消旧策略未结订单
        - 持仓迁移: 获取并映射持仓到新策略
        - 异常回滚: 切换失败自动回滚到旧策略

   2. **新增 `trader/tests/test_strategy_hotswap.py`**:
      - `TestStrategyLoader`: 策略加载器测试
      - `TestVersionManager`: 版本管理器测试
      - `TestStrategyHotSwapperStateMachine`: 状态机测试
      - `TestSwapResult`: 切换结果测试
      - `TestSwapPhase`: 切换阶段枚举测试
      - `TestVersionId`: 版本ID测试
      - `TestVersionInfo`: 版本信息测试
      - `TestStrategyHotSwapIntegration`: 集成测试
      - 测试总数: 30个

   3. **核心类型定义**:
      - `SwapState`: 热插拔状态枚举 (IDLE, LOADING, VALIDATING, PREPARING, SWITCHING, ROLLING_BACK, ACTIVE, ERROR)
      - `SwapPhase`: 热插拔阶段枚举 (LOADING_*, VALIDATING_*, PREPARING_*, SWITCHING_*, ROLLING_BACK_*)
      - `SwapResult`: 切换操作结果
      - `SwapError`: 切换错误信息
      - `VersionId`: 版本ID
      - `VersionInfo`: 版本信息
      - `PositionMapping`: 持仓映射

   4. **Port接口定义**:
      - `StrategyLoaderPort`: 策略加载器端口
      - `PositionProviderPort`: 持仓提供者端口
      - `OrderManagerPort`: 订单管理器端口
      - `StrategyRegistryPort`: 策略注册表端口

- **测试结果**:
   - `test_strategy_hotswap.py`: 30/30 通过 ✅
   - P0 回归测试: 93/93 通过 ✅
   - Strategy Runner/Evaluator 集成测试: 73/73 通过 ✅

- **验收标准检查**:
   - ✅ 无需重启更新策略（状态机支持在线切换）
   - ✅ 挂单正确处理（切换前自动取消未结订单）
   - ✅ 持仓迁移（PositionProviderPort 支持持仓映射）
   - ✅ 异常自动回滚（切换失败自动回滚到旧策略）
   - ✅ 代码安全验证（签名验证、沙箱执行、资源限制检查）
   - ✅ 状态机转换正确（LOADING -> VALIDATING -> PREPARING -> SWITCHING -> ACTIVE）
   - ✅ 回滚机制完整（ROLLING_BACK -> IDLE）

### Task 4.4: AI策略生成服务 ✅ 已完成

- **完成时间**: 2026-03-30
- **分支**: main (直接提交)
- **主要变更**:

   1. **新增 `insight/` 目录**:
      - `insight/__init__.py`: 包初始化，导出核心类型
      - `insight/code_sandbox.py`: 安全代码执行沙箱
      - `insight/ai_audit_log.py`: AI审计日志
      - `insight/ai_strategy_generator.py`: AI策略生成器

   2. **CodeSandbox (`insight/code_sandbox.py`)**:
      - **危险代码拦截机制**:
        - 静态AST分析：检测危险模式（exec/eval/open/socket等）
        - 网络调用拦截：禁止socket/http/urllib等网络操作
        - 文件系统拦截：禁止open/read/write等文件操作
        - 导入限制：白名单+黑名单双重检查
        - 正则表达式扫描：30+危险模式检测
      
      - **资源限制**:
        - 内存限制（Unix: RLIMIT_AS）
        - CPU时间限制（Unix: RLIMIT_CPU）
        - 执行超时控制
        - Windows平台兼容处理
      
      - **核心类型**:
        - `SandboxConfig`: 沙箱配置
        - `SandboxResult`: 执行结果
        - `SandboxStatus`: 执行状态枚举
        - `DangerousCodeError`: 危险代码异常

   3. **AIAuditLog (`insight/ai_audit_log.py`)**:
      - **审计功能**:
        - 记录所有AI生成的代码
        - 版本历史管理
        - 审批状态跟踪
        - 完整审计追溯
      
      - **核心类型**:
        - `AuditEntry`: 审计日志条目
        - `AuditStatus`: 审计状态（DRAFT/PENDING/APPROVED/REJECTED/ACTIVE等）
        - `AuditEventType`: 事件类型（GENERATED/VALIDATED/SUBMITTED/APPROVED等）
        - `AuditStatistics`: 统计数据
        - `AuditLogStorage`: 存储接口（Protocol）
        - `InMemoryAuditLogStorage`: 内存存储实现

   4. **AIStrategyGenerator (`insight/ai_strategy_generator.py`)**:
      - **多LLM后端支持**（Adapter模式）:
        - `OpenAIAdapter`: OpenAI GPT系列
        - `AnthropicAdapter`: Claude系列
        - `LocalAdapter`: 本地模型（vLLM等）
        - `MockAdapter`: 测试用Mock
      
      - **核心功能**:
        - `generate()`: 基于提示生成策略代码
        - `validate_code()`: 代码安全性验证
        - `register_strategy()`: 策略注册
        - `submit_for_approval()`: 提交审批
        - `approve_strategy()`: 批准策略
        - `deploy_strategy()`: 部署策略
      
      - **HITL对接**:
        - 与HITLGovernance集成
        - 审批流程支持
        - 审计日志集成
      
      - **核心类型**:
        - `GenerationConfig`: 生成配置
        - `GeneratedStrategy`: 生成的策略
        - `RegistrationResult`: 注册结果
        - `LLMBackend`: LLM后端枚举

   5. **新增 `trader/tests/test_ai_strategy_generator.py`**:
      - `TestCodeSandboxValidation`: 沙箱验证测试（exec/eval/open/socket等危险模式检测）
      - `TestCodeSandboxExecution`: 沙箱执行测试
      - `TestAIAuditLog`: 审计日志测试（生成/审批/部署/统计）
      - `TestAIStrategyGenerator`: AI生成器测试
      - `TestGeneratedStrategyPlugin`: 策略包装器测试
      - `TestIntegration`: 集成测试
      - 测试总数: 36个

- **测试结果**:
   - `test_ai_strategy_generator.py`: 36/36 通过 ✅

- **验收标准检查**:
   - ✅ 符合StrategyPlugin协议（GeneratedStrategyPlugin实现）
   - ✅ 危险代码拦截（30+危险模式检测）
   - ✅ 网络调用检测（socket/http/urllib/websocket等）
   - ✅ 审计追溯（AuditEntry完整记录）
   - ✅ 多LLM后端（OpenAI/Anthropic/Local/Mock）

### Task 4.5: AI策略聊天界面 ✅ 已完成

- **完成时间**: 2026-03-30
- **分支**: main (直接提交)
- **主要变更**:

   1. **新增 `insight/chat_interface.py`**: 策略聊天接口
      - **核心类型**:
        - `ChatSession`: 聊天会话（session_id, messages, context, status）
        - `ChatMessage`: 聊天消息（role, content, timestamp, attachments）
        - `ChatResponse`: 聊天响应（message, suggestions, status）
        - `StrategyContext`: 策略上下文（存储当前会话中的策略信息）
        - `Attachment`: 附件（生成的代码等）
        - `SessionStatus`: 会话状态枚举（ACTIVE/WAITING_APPROVAL/APPROVED/REJECTED等）
        - `MessageRole`: 消息角色枚举（USER/ASSISTANT/SYSTEM）
      
      - **StrategyChatInterface 核心方法**:
        - `create_session()`: 创建新会话
        - `send_message()`: 发送消息并获取AI响应
        - `get_history()`: 获取会话历史
        - `approve_and_register()`: 审批并注册策略
        - `reject_strategy()`: 拒绝策略
        - `list_sessions()`: 列出所有会话
      
      - **存储端口**:
        - `ChatSessionStorePort`: 会话存储接口（Protocol）
        - `InMemoryChatSessionStore`: 内存存储实现
      
      - **工厂函数**:
        - `create_chat_interface()`: 创建聊天接口实例

   2. **新增 `trader/api/routes/chat.py`**: 聊天API路由
      - **API端点**:
        - `POST /api/chat/sessions`: 创建会话
        - `POST /api/chat/sessions/{id}/messages`: 发送消息
        - `GET /api/chat/sessions/{id}/history`: 获取历史
        - `POST /api/chat/sessions/{id}/approve`: 审批并注册
        - `POST /api/chat/sessions/{id}/reject`: 拒绝策略
        - `DELETE /api/chat/sessions/{id}`: 删除会话
        - `GET /api/chat/sessions`: 列出所有会话
        - `GET /api/chat/sessions/{id}`: 获取会话详情
      
      - **请求/响应模型**:
        - `CreateSessionRequest`, `SendMessageRequest`, `ApproveRequest`, `RejectRequest`
        - `SessionResponse`, `ChatMessageResponse`, `SendMessageResponse`, `RegistrationResultResponse`

   3. **模块集成**:
      - 导出到 `insight/__init__.py`
      - 注册到 `trader/api/routes/__init__.py`

   4. **新增 `trader/tests/test_chat_interface.py`**: 聊天界面单元测试
      - `TestChatMessage`: 消息创建测试
      - `TestAttachment`: 附件测试
      - `TestStrategyContext`: 策略上下文测试
      - `TestChatSession`: 会话管理测试
      - `TestInMemoryChatSessionStore`: 内存存储测试
      - `TestStrategyChatInterface`: 聊天接口核心测试
      - `TestCreateChatInterface`: 工厂函数测试
      - `TestBoundaryConditions`: 边界条件测试
      - `TestErrorPaths`: 错误路径测试
      - 测试总数: 37个

- **测试结果**:
   - `test_chat_interface.py`: 37/37 通过 ✅

- **验收标准检查**:
   - ✅ 自然语言描述策略（send_message识别策略生成请求）
   - ✅ 自动HITL审批（approve_and_register自动提交审批）
   - ✅ 对话历史可查（get_history返回完整消息列表）
   - ✅ 审批通过自动注册（approve_and_register调用register_strategy）

### Task 4.6: 策略管理端到端集成 ✅ 已完成

- **完成时间**: 2026-03-30
- **分支**: main (直接提交)
- **主要变更**:

   1. **新增 `trader/services/strategy_lifecycle_manager.py`**: 策略生命周期管理器
      - **核心类型**:
        - `LifecycleStatus`: 生命周期状态枚举 (DRAFT/VALIDATED/BACKTESTED/APPROVED/RUNNING/STOPPED/FAILED/ARCHIVED)
        - `LifecycleEvent`: 生命周期事件记录
        - `LifecycleEventType`: 事件类型枚举
        - `StrategyLifecycle`: 单个策略的完整生命周期
        - `ValidationOutcome`: 验证结果
        - `BacktestOutcome`: 回测结果
        - `ApprovalOutcome`: 审批结果
        - `StartOutcome`: 启动结果
        - `StopOutcome`: 停止结果
        - `SwapOutcome`: 热插拔结果

      - **StrategyLifecycleManager 核心方法**:
        - `create_strategy()`: 从代码创建策略 (DRAFT)
        - `validate_strategy()`: 验证策略有效性 (VALIDATED)
        - `run_backtest()`: 运行策略回测 (BACKTESTED)
        - `approve_strategy()`: 审批策略 (APPROVED)
        - `start_strategy()`: 启动策略 (RUNNING)
        - `stop_strategy()`: 停止策略 (STOPPED)
        - `swap_strategy()`: 热插拔更新策略
        - `get_lifecycle()`: 获取策略生命周期
        - `list_lifecycles()`: 列出策略生命周期
        - `get_metrics_summary()`: 获取性能指标摘要

      - **生命周期状态转换**:
        ```
        DRAFT → VALIDATED → BACKTESTED → APPROVED → RUNNING → STOPPED
                                                    ↓
                                               (热插拔) ↓
        FAILED ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ← ←
        ```

      - **Port接口定义**:
        - `LifecyclePort`: 生命周期管理器端口
        - `RunnerPort`: 策略运行器端口

      - **存储实现**:
        - `InMemoryLifecycleStore`: 内存生命周期存储

      - **性能监控**:
        - `_record_duration()`: 记录操作耗时
        - `_get_p99()`: 获取P99延迟

   2. **新增 `trader/tests/test_strategy_lifecycle_e2e.py`**: 端到端集成测试
      - `test_scenario_1_ai_generated_deployment`: AI生成并部署策略
      - `test_scenario_2_strategy_hotswap`: 策略热插拔更新
      - `test_scenario_3_backtest_and_approval`: 策略回测与审批
      - `test_scenario_4_rollback_on_error`: 异常自动回滚
      - `test_crash_isolation`: 崩溃隔离测试
      - `test_p99_latency`: P99延迟验证
      - `test_status_transition_constraints`: 状态转换约束测试
      - `test_lifecycle_traceability`: 生命周期追溯测试
      - 测试总数: 8个

   3. **4个端到端场景说明**:

      **场景1: AI生成并部署策略**
      - 流程: chat → generator → lifecycle → runner
      - 状态: DRAFT → VALIDATED → BACKTESTED → APPROVED → RUNNING
      - 验证: 状态转换正确、事件历史完整、性能指标正常

      **场景2: 策略热插拔更新**
      - 流程: hotswap → runner → evaluator
      - 状态: RUNNING → STOPPED(旧) + RUNNING(新)
      - 验证: 热插拔状态转换、旧策略状态更新、新策略状态正确

      **场景3: 策略回测与审批**
      - 流程: evaluator → backtest → approval → running
      - 验证: 回测报告指标完整性、审批流程正确性、状态转换顺序

      **场景4: 异常自动回滚**
      - 流程: hotswap rollback → previous version
      - 验证: 热插拔失败时回滚、旧策略状态恢复、错误信息记录

- **测试结果**:
   - `test_strategy_lifecycle_e2e.py`: 8/8 通过 ✅

- **验收标准检查**:
   - ✅ 完整流程走通 (DRAFT → VALIDATED → BACKTESTED → APPROVED → RUNNING → STOPPED)
   - ✅ 4场景覆盖 (AI生成部署/热插拔更新/回测审批/异常回滚)
   - ✅ 文档完整 (策略生命周期管理器、核心类型、状态转换图)
   - ✅ 崩溃隔离 (单策略崩溃不影响其他策略)
   - ✅ P99<500ms (性能监控验证)

## Phase 5: 回测框架升级

### 背景与目标

自研回测模块存在方法论问题，经过专业评估后决定引入成熟开源框架。

### 问题分析

| # | 问题 | 影响 |
|---|------|------|
| 1 | **前瞻偏差**：使用当前 bar 收盘价执行而非下一 bar 开盘价 | 性能被高估 |
| 2 | **滑点方向错误**：始终加滑点，而非根据买卖方向调整 | 成本计算不准确 |
| 3 | **不支持止盈/止损**：无法正确测试带风控的策略 | 策略评估不完整 |
| 4 | **无样本外验证**：缺乏交叉验证和前向分析支持 | 过拟合风险 |

### 推荐方案

**首选框架**：QuantConnect Lean (Apache 2.0许可)
- 功能完整，支持多标的、多策略
- 主动开发中，机构级质量
- 支持止盈/止损、滑点模型

**备选框架**：Backtrader
- GPLv3许可（考虑许可证风险）
- 功能完整，文档好，社区活跃

### 实施计划

| Task | 内容 | 预计工作量 | 优先级 |
|------|------|-----------|--------|
| 5.1 | 框架选型与集成架构设计 | 1 人天 | P0 |
| 5.2 | Backtrader 适配层开发 | 5 人天 | P0 |
| 5.3 | 回测结果标准化与可视化 | 2-3 人天 | P1 |
| 5.4 | 样本外验证与交叉验证框架 | 4 人天 | P0 |
| 5.5 | 与 StrategyLifecycleManager 集成 | 2.5 人天 | P0 |
| 5.6 | 回测数据管道优化 | 2 人天 | P1 |
| 5.7 | 自研回测模块归档 | 1 人天 | P2 |
| **5.8** | **回测框架测试套件** | **2 人天** | **P0** |
| **5.9** | **性能基准测试** | **1 人天** | **P1** |
| **总计** | | **约 20.5 人天** | |

### 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Backtrader 学习曲线 | 延期 | 先完成 POC 验证核心功能 |
| 数据格式不兼容 | 阻塞 | 优先实现数据适配器 |
| 性能不达标 | 需优化 | 预留性能测试时间 |

### 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│              StrategyLifecycleManager                        │
│                  (自研 - 核心业务逻辑)                        │
└─────────────────────┬───────────────────────────────────────┘
                      │
          ┌─────────────┴─────────────┐
          ▼                           ▼
  ┌───────────────┐         ┌───────────────┐
  │   Backtrader  │         │   VectorBT    │
  │   (生产回测)   │         │   (快速原型)   │
  └───────┬───────┘         └───────┬───────┘
          │                         │
          └─────────────┬───────────┘
                        ▼
              ┌─────────────────┐
              │  统一绩效报告    │
              │  (自研聚合层)    │
               └─────────────────┘
```

### Phase 5 实施进度

**更新日期**: 2026-03-31

| Task | 内容 | 状态 | 测试数 | 完成日期 |
|------|------|------|--------|----------|
| 5.1 | 框架选型与集成架构设计 | ✅ 完成 | - | 2026-03-31 |
| 5.2 | QuantConnect Lean 适配层开发 | ✅ 完成 | 95 | 2026-03-31 |
| 5.3 | 回测结果标准化与可视化 | ✅ 完成 | 29 | 2026-03-31 |
| 5.4 | 样本外验证与交叉验证框架 | ✅ 完成 | 32 | 2026-03-31 |
| 5.5 | 与 StrategyLifecycleManager 集成 | ✅ 完成 | 43 | 2026-03-31 |
| 5.6 | 回测数据管道优化 | ✅ 完成 | 31 | 2026-03-31 |
| 5.7 | 自研回测模块归档 | ✅ 完成 | 迁移指南 | 2026-03-31 |
| **5.8** | **回测框架测试套件** | ✅ 完成 | 226 | 2026-03-31 |
| 5.9 | 性能基准测试 | ✅ 完成 | 21 | 2026-03-31 |

**Phase 5 测试总计**: 267 tests passing (247 backtesting + existing)

**已完成交付物**:
- `trader/services/backtesting/ports.py` - 协议定义
- `trader/services/backtesting/quantconnect_adapter.py` - QuantConnect Lean 数据适配器
- `trader/services/backtesting/strategy_adapter.py` - 策略适配器
- `trader/services/backtesting/execution_simulator.py` - 执行模拟器（修正滑点方向）
- `trader/services/backtesting/result_converter.py` - 结果转换器
- `trader/services/backtesting/report_formatter.py` - 标准化报告
- `trader/services/backtesting/visualizer.py` - 可视化
- `trader/services/backtesting/validation.py` - Walk-Forward, K-Fold, 敏感性分析
- `trader/services/backtesting/lifecycle_integration.py` - 生命周期集成
- `trader/services/backtesting/data_pipeline.py` - 数据管道优化
- `trader/services/backtesting/performance_benchmark.py` - 性能基准测试
- `docs/migration_guide.md` - 自研模块迁移指南

**Task 5.7 完成**:
- `strategy_evaluator.py` 已标记为 deprecated
- 迁移指南文档已创建

**Task 5.9 完成**:
- `PerformanceBenchmark` 实现
- `BenchmarkRunner` 实现
- 21 tests 通过

## 已知问题

| 优先级 | 问题 | 状态 | 备注 |
|--------|------|------|------|
| 高 | Funding/OI适配器文件缺失 | ✅ 已解决 | Task 2.1 已完成 |
| 中 | Reconciler集成测试缺失 | ✅ 已解决 | Task 2.3 已完成 |
| 中 | API测试覆盖不足 | ✅ 已解决 | Task 2.4 已完成 |
| 低 | OnChain爆仓数据为STUB | ⚠️ 已知 | Binance无公开爆仓API，需接入Coinglass等付费数据源 |
| 低 | 交易所流量为STUB | ⚠️ 已知 | Glassnode需API Key，待接入 |

## CI 门禁状态

| 阶段 | 状态 | 备注 |
|------|------|------|
| import-gate | ✅ | |
| architecture-gate | ✅ | |
| p0-gate | ✅ | |
| control-gate | ✅ | |
| postgres-integration | ✅ | |
