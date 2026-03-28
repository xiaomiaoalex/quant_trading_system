# 项目开发状态追踪

> 本文件记录项目各模块的当前状态和测试验证结果
> 更新方法：`run_tests.bat` 后手动更新本文件，或运行 `scripts/update_project_status.py`

## 最后更新时间
2026-03-27 10:06:00 (北京时间)

## 分支状态
- **当前分支**：`main`
- **基于**：`main`
- **工作树**：干净
- **最新提交**：`10548e6` - perf(task-2.2): optimize flush_bucket_locked I/O to avoid blocking add_event

## 最近开发记录（滚动式）

### 上次任务：Task 2.5 资金结构信号
- 完成时间: 2026-03-25
- 分支: task/2.5-capital-structure-signals
- 状态: ✅ 已合并到main
- 主要变更: FeatureStore范围查询, 多空比数据适配器, 三大信号计算

### 本次任务：Task 2.4 基础信号层（趋势+价量）
- 完成时间: 2026-03-25
- 分支: main (直接提交)
- 状态: ✅ 已合并
- 主要变更: trend_signals.py, price_volume_signals.py, signal_sandbox.py, 64个测试全部通过

### 下次计划：Task 2.1 Funding/OI适配器完整实现
- 目标: 实现Funding/OI数据到Feature Store的完整写入
- 前置条件: Task 2.4完成（✅）
- 预计工作量: 待评估

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
