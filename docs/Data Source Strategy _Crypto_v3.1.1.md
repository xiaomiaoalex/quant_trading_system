# Data Source Strategy — Crypto v3.1.1

## 1. 文档目的

本文档定义 `quant_trading_system Crypto v3.1.1` 的数据源分层、可信度等级、用途边界与接入门禁。  
核心原则：保留长期愿景，但当前能力必须可被代码和测试证明。

---

## 2. Capability Matrix（Current / Next / Target）

| 能力 | 状态 | 代码证据 | 测试证据 |
|---|---|---|---|
| Binance 直连（Public+Private）作为首发真相输入源 | Current | `trader/adapters/binance/connector.py`、`trader/adapters/binance/public_stream.py`、`trader/adapters/binance/private_stream.py` | `trader/tests/test_binance_connector.py` |
| `/v1/events`、`/v1/snapshots/latest` 当前由内存读模型提供 | Current | `trader/services/event.py`、`trader/storage/in_memory.py`、`trader/api/routes/events.py` | `trader/tests/test_api_endpoints.py` |
| 风险事务链路 PG-First（不可用回退内存） | Current | `trader/adapters/persistence/risk_repository.py` | `trader/tests/test_risk_idempotency_persistence.py`（PG 未配置时按约定 skip） |
| Reconciler 作为跨源一致性裁决层 | Next | 当前无 reconciler service/route | N/A |
| AI proposal/approve 治理接口 | Target | 当前无 `/v1/ai/proposals*` 路由 | N/A |
| Runner 自动执行主链路（唯一执行驱动） | Target | 当前无 runtime 主链路 | N/A |

---

## 3. 三层数据源架构

### 3.1 Layer A：交易所直连底盘

当前来源：
- Binance REST
- Binance WebSocket

当前角色：
- 正式真相输入源
- 对账基准输入源
- 数据治理与对齐主源

约束修订（v3.1.1）：
- 仅在 Runner/Execution 主链路上线并通过 Gate 后，Layer A 才成为唯一执行驱动源。
- 当前阶段不承诺“已具备唯一自动执行驱动”。

### 3.2 Layer B：链上与衍生品辅助层

当前定位：
- `Research Only` / `Risk Filter Candidate`
- 用于 Regime 与解释增强

禁止：
- 在未验证稳定前单独驱动开仓或仓位调整

### 3.3 Layer C：事件与文本叙事层

当前定位：
- 事件检索、摘要、解释
- Insight 输入

禁止：
- 未经策略准入直接转成 OrderIntent

---

## 4. 数据用途边界

### 4.1 可进入执行链路（当前严格口径）

必须同时满足：
- 交易所直连来源
- 通过健康检查与对齐门禁
- 可被幂等与审计链路追踪

### 4.2 仅研究链路

满足任一即降级：
- 非交易所直连
- 时效低于主数据流
- 语义不稳定或来源一致性不足

### 4.3 仅洞察链路

- 新闻
- 公告文本
- 社交媒体文本
- AI 摘要结果

---

## 5. Alignment Gate 口径修订

v3.1.1 统一为“按流域生效”：
- Private/Execution：Gate 期间阻断正式外发
- Public 行情：可继续外发，但必须带 `DEGRADED/ALIGNING` 标签

---

## 6. 里程碑口径（取消硬周数）

保留 Sprint 名称，但完成标准改为 Gate 驱动：
- Entry Gate：代码存在性
- Exit Gate：非跳过测试通过
- 演练 Gate：关键故障演练记录完备

三项同时满足才算 Sprint 完成。

---

## 7. 注册与审计要求

每个数据源至少登记：
- `source_name`
- `source_level`（A/B/C/D）
- `allowed_for_execution`
- `allowed_for_research`
- `allowed_for_ai_insight`
- `raw_reference`（可追溯来源）

---

## 8. 一句话总结

Crypto v3.1.1 的数据源策略先解决“输入可信”，再讨论“字段丰富”；无法证明可信的输入必须降级，不得越权进入执行链路。
