# Chief Architect Audit - 2026-03-09

## 1. 审计结论

当前仓库的真实状态，不再是 `实施计划-v3.0.5-蓝图到Sprint.md` 中的 “Sprint 0/1 待启动”。

更准确的判断是：

1. P0 主线已基本稳定，核心 Binance / Deterministic / Risk 闭环测试可通过。
2. Sprint 1-3 对应的 `risk_events` / `risk_upgrades` / `risk_upgrade_effects` 已经落了代码。
3. Sprint 4 还没有真正收口，因为“重启后幂等契约 Gate”没有按计划独立成型。
4. Sprint 5-10 里承诺的 Observability / Reconciler / Runner / AI Governance，目前仍然是文档目标，不是代码能力。

结论：项目当前应重基线为“`Sprint 3 已部分完成，Sprint 4 为当前主 Sprint`”。

## 2. 代码真相

### 2.1 已落地能力

1. 风险事件持久化仓库已存在，支持 PostgreSQL 优先、内存回退：
   - `trader/adapters/persistence/risk_repository.py`
   - `trader/adapters/persistence/postgres/__init__.py`
2. `risk_events` / `risk_upgrades` / `risk_upgrade_effects` 表结构与事务接口已实现。
3. `/v1/risk/events` 已接入“事件写入 + 升级记录 + 副作用意图 + 恢复接口”闭环：
   - `trader/api/routes/risk.py`
4. P0 关键路径测试当前可跑通：
   - `trader/tests/test_binance_connector.py`
   - `trader/tests/test_binance_private_stream.py`
   - `trader/tests/test_binance_degraded_cascade.py`
   - `trader/tests/test_deterministic_layer.py`
   - `trader/tests/test_hard_properties.py`
   - `trader/tests/test_api_endpoints.py`
   - `trader/tests/test_api_services.py`

### 2.2 未完成或未对齐能力

1. `trader/tests/test_risk_idempotency_persistence.py` 不存在。
2. CI 的 `postgres-integration` 仅运行 `trader/tests/test_postgres_storage.py`，还不是计划中要求的独立契约 Gate。
3. `EventService / OrderService / PortfolioService / StrategyService / DeploymentService` 仍全部绑定 `ControlPlaneInMemoryStorage`。
4. `/v1/events` 和 `/v1/snapshots/latest` 目前查询的是内存存储，不是 PostgreSQL 事件真理源。
5. 仓库中没有 Runner、Reconciler、AI Proposal、OTel、Prometheus、Alembic、Testcontainers 的落地代码或依赖接线。

## 3. 主要发现

### F1. Sprint 4 的契约门禁没有真正落地

计划要求：

1. 独立的“重启后幂等”契约测试文件。
2. 接入 `postgres-integration`。
3. 失败阻断 PR。

当前现状：

1. 仓库没有 `test_risk_idempotency_persistence.py`。
2. `ci-gate.yml` 只执行 `test_postgres_storage.py`。
3. 事务/恢复能力虽然已有代码，但 Sprint 4 的验收工件没有按计划成型。

判定：`Sprint 4 In-Progress`，不能宣称完成。

### F2. Persistence Plane 只完成了一半

PostgreSQL 存储已经存在，但控制面查询路径仍主要走内存：

1. `trader/storage/__init__.py` 只暴露 InMemoryStorage。
2. `trader/services/event.py` 查询的是内存 `list_events/get_latest_snapshot`。
3. `trader/services/order.py`、`trader/services/portfolio.py` 仍是纯内存视图。

这意味着规范里“PostgreSQL-First / Event Source of Truth”目前只在风险持久化和独立存储类层面成立，还没有贯穿到控制面读路径。

### F3. Sprint 5-8 还是文档目标，不是代码能力

当前代码中没有看到以下主线能力：

1. Observability MVP：无 OTel、无 Prometheus、无 `/metrics`。
2. Reconciler MVP：无实体、无服务、无 API。
3. Runner MVP：无 runner runtime，仅有 strategy registry 和 deployment 状态切换骨架。
4. AI Governance MVP：无 `AIInsightEvent`、无 `AIDecisionProposal`、无 `/v1/ai/proposals`、无审批链路。

判定：Sprint 5-8 尚未开工，不允许在汇报中表述为“部分完成”。

### F4. 时间纪律仍有漏洞

领域模型仍在使用 naive UTC 时间：

1. `trader/core/domain/models/order.py`
2. `trader/core/domain/models/position.py`

这与规范中的 UTC aware / `ts_ms` 纪律不一致，并且本地测试已经出现 `datetime.utcnow()` 的废弃告警。

判定：这是小缺陷，但必须在 Sprint 4 一并收口，避免继续扩散。

## 4. 对 minimax 的执行指令

### 4.1 当前主 Sprint

从今天起，主 Sprint 定义为：

`Sprint 4 - Contract Gate`

禁止跳到 Reconciler / Runner / AI Governance 主开发，直到以下事项全部完成。

### 4.2 本周必须交付

1. 新增 `trader/tests/test_risk_idempotency_persistence.py`
2. 覆盖 4 个场景：
   - 重启后重复 `dedup_key` 返回 `409`
   - 重启后重复 `upgrade_key` 不产生重复副作用
   - Step2/Step6/Step8 失败后恢复到一致状态
   - KillSwitch 级别只单调升级，不回滚
3. 调整 `ci-gate.yml`
   - `postgres-integration` 必须显式跑该测试文件
   - 不能仅靠 `test_postgres_storage.py` 代表契约 Gate
4. 清理 `datetime.utcnow()` 的 naive 时间用法
5. 更新规范变更记录，把 Sprint 状态从“文档完成”改成“代码 + Gate 完成”

### 4.3 交付格式

每次提交给我审查时，minimax 必须同时提供：

1. 变更文件清单
2. 新增测试场景清单
3. 本地执行命令与结果
4. 是否触及契约语义变更（201/409/500、字段、幂等）
5. 对 `实施计划-v3.0.5-蓝图到Sprint.md` 和技术规范的同步更新

缺一项，视为未完成评审输入。

## 5. 下一阶段排期重排

### Sprint 4

目标：完成契约 Gate，而不是继续加功能。

DoD：

1. PostgreSQL 真实跑通
2. API 级重启幂等契约测试通过
3. CI 强制门禁生效
4. 文档状态与代码一致

### Sprint 5

只有在 Sprint 4 完成后，才允许进入：

1. API -> service -> repository 的 tracing
2. 风险事件/升级/恢复计数指标
3. `/metrics` 暴露
4. 最小 runbook/指标字典

### Sprint 6-7

按顺序推进：

1. Sprint 6 做 Reconciler Candidate/Confirmed 骨架
2. Sprint 7 做 Strategy Runner 最小 dry-run 闭环

其中 Runner 不得只做“部署状态改 RUNNING”，必须有真实运行时边界。

## 6. 本次本地验证

已验证通过：

1. `trader/tests/test_api_endpoints.py`
2. `trader/tests/test_api_services.py`
3. `trader/tests/test_binance_connector.py`
4. `trader/tests/test_binance_private_stream.py`
5. `trader/tests/test_binance_degraded_cascade.py`
6. `trader/tests/test_deterministic_layer.py`
7. `trader/tests/test_hard_properties.py`
8. `trader/tests/test_architecture.py`

注意：

1. `trader/tests/test_postgres_storage.py` 在当前本地环境大部分被跳过，因为未配置 PostgreSQL。
2. 因此，当前我只能确认“代码已写到 Sprint 3/4 边界”，不能确认“Postgres 契约 Gate 已完成”。
