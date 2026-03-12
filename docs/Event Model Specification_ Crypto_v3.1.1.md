# Event Model Specification — Crypto v3.1.1

## 1. 文档目的

本文档定义 `quant_trading_system Crypto v3.1.1` 的事件分类、统一字段、路由边界与能力分层。  
目标：事件模型必须可记录、可回放、可审计；未落地能力不得写成 Current。

---

## 2. Capability Matrix（Current / Next / Target）

| 能力 | 状态 | 代码证据 | 测试证据 |
|---|---|---|---|
| `/v1/events` 事件查询（当前控制面读模型） | Current | `trader/api/routes/events.py`、`trader/services/event.py`、`trader/storage/in_memory.py` | `trader/tests/test_api_endpoints.py` |
| `/v1/snapshots/latest` 快照查询（当前内存读模型） | Current | `trader/api/routes/events.py`、`trader/services/event.py` | `trader/tests/test_api_endpoints.py` |
| 风险事件进入统一风险事务链路 | Current | `trader/api/routes/risk.py`、`trader/services/risk.py`、`trader/adapters/persistence/risk_repository.py` | `trader/tests/test_risk_idempotency_persistence.py` |
| ReconcileDivergenceEvent 运行时持续产出 | Next | 当前无 reconciler service/route | N/A |
| AIInsight proposal/approve 事件治理接口 | Target | 当前无 AI proposal/approve route | N/A |

---

## 3. 事件分类（v3.1.1 口径）

### 3.1 Current
- Market Data Event（行情）
- Risk Event（风险与降级）
- Control Plane Event（配置/治理）

### 3.2 Next
- ReconcileDivergenceEvent（持续对账分歧）
- 交易所状态漂移自动归因事件

### 3.3 Target
- AIInsightEvent（proposal/approve 治理闭环）
- AI 驱动事件分级协助（不越权）

---

## 4. 统一字段（最低要求）

每个事件至少包含：
- `event_id`
- `event_type`
- `source`
- `event_time`
- `ingest_time`
- `payload`
- `dedup_key`
- `raw_reference`（可追溯）

说明：字段命名可随实现模型调整，但语义必须保持一致。

---

## 5. 事件优先级与用途边界

优先级建议：
1. 交易所官方状态与系统风险事件
2. 项目方官方公告与可信辅助源
3. 新闻聚合
4. 社交媒体

用途边界：
- 低可信事件默认仅可进入 Insight/研究，不得直接进入执行链路。

---

## 6. API 真理源声明（修订）

- `/v1/events`：Current，当前真理源是控制面内存读模型；目标迁移至 PostgreSQL 投影读模型。
- `/v1/snapshots/latest`：Current，当前为内存快照读模型；目标迁移至 PostgreSQL 投影读模型。
- AI proposal/approve 事件接口：Target，未落地前不得宣称为 Current。

---

## 7. 生命周期与门禁

事件流程：
1. 进入系统（Adapter / Risk / Control Plane）
2. 标准化与去重
3. 路由（持久化、查询、回放、报告）
4. 影响执行（仅高优先级且制度化事件）

Current 门禁：
- 文档中每个 Current 条目必须绑定代码路径 + 非跳过测试名。

---

## 8. 一句话总结

Crypto v3.1.1 事件模型强调“可证据化的当前能力”；Reconciler 与 AI 事件治理保留为 Next/Target，不再作为当前交付承诺。
