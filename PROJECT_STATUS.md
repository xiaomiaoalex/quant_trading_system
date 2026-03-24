# 项目开发状态追踪

> 本文件记录项目各模块的当前状态和测试验证结果
> 更新方法：`run_tests.bat` 后手动更新本文件，或运行 `scripts/update_project_status.py`

## 最后更新时间
2026-03-24 18:11:00 (北京时间)

## 分支状态
- **当前分支**：`main`
- **基于**：`main`
- **工作树**：干净
- **最新提交**：`bda5ef2` - feat(trader): add OnChainMarketDataAdapter

## 本次开发记录 (2026-03-23 下午)

### 开发前状态
- OnChain 适配器模块不存在
- Reconciler 集成测试缺失 (Issue #2)
- API 测试覆盖不足，/v1/reconciler/* 端点无 HTTP 测试 (Issue #3)

### 已完成开发动作
1. 新增 `trader/adapters/onchain/` 模块
   - `OnChainMarketDataAdapter`: 链上/宏观市场数据适配器
   - 支持 Binance ticker、CoinGecko 稳定币供应数据采集
   - 写入 Feature Store，包含降级保护
2. 新增 `trader/tests/test_onchain_market_data_stream.py`: 565 行单元测试
3. 新增 `trader/tests/test_reconciler_service_integration.py`: 493 行集成测试
4. 新增 `trader/tests/test_api_reconciler.py`: 396 行 API 端点测试
5. **全局订单状态清理** - 统一 `OrderStatus` 定义
   - `trader/storage/in_memory.py`: 默认值从 `"NEW"` 改为 `OrderStatus.SUBMITTED.value`
   - `trader/tests/test_deterministic_layer.py`: 3 处测试断言更新

### MVP 验证结果 (2026-03-23 20:18)
| 验证项 | 结果 |
|--------|------|
| 单元测试 | ✅ 667 passed, 0 skipped |
| PostgreSQL 集成 | ✅ 正常工作 |
| API 服务 | ✅ http://localhost:8080 |
| Binance Demo 连接 | ✅ 连通 demo.binance.com |
| 真实交易 Smoke Test | ✅ 下单→查单→撤单 完整生命周期 |
| Reconciler 对账 | ✅ trigger + report 正常工作 |
| Monitor 监控 | ✅ snapshot API 正常返回 |

### 下一步计划 (依据 PLAN.md)
- Phase 3: 信号层增强
- Broker 适配层接入真实策略

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
| 2.2 | OnChain适配器 | ✅ 已完成 | OnChainMarketDataAdapter 已实现，3个测试文件共1454行测试代码 |
| 2.3 | Reconciler集成测试 | ✅ 已完成 | test_reconciler_service_integration.py，493行，17个测试 |
| 2.4 | Reconciler API测试 | ✅ 已完成 | test_api_reconciler.py，396行，16个测试 |

## Phase 3: 信号层增强

| Task | 模块 | 状态 | 备注 |
|------|------|------|------|
| 7.1 | Position & Risk Constructor | ✅ 完成 | **完成时间**: 2026-03-24
**实现内容**: Position & Risk Constructor 完整体系
**测试结果**: 50/50 测试通过
**新增文件**: position_risk_constructor.py, test_position_risk_constructor.py |

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
