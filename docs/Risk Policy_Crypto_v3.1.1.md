# Risk Policy — Crypto v3.1.1

## 1. 文档目的

本文档定义 `quant_trading_system Crypto v3.1.1` 的风险控制边界、KillSwitch 语义、环境异常处置与验收口径。  
v3.1.1 重点：统一级别语义，避免文档与实现命名冲突。

---

## 2. Capability Matrix（Current / Next / Target）

| 能力 | 状态 | 代码证据 | 测试证据 |
|---|---|---|---|
| 风险事件接入与幂等升级链路 | Current | `trader/api/routes/risk.py`、`trader/services/risk.py` | `trader/tests/test_api_endpoints.py` |
| 风险事务持久化 PG-First（不可用回退内存） | Current | `trader/adapters/persistence/risk_repository.py` | `trader/tests/test_risk_idempotency_persistence.py` |
| RiskEngine 级别建议（0/1/2/3） | Current | `trader/core/application/risk_engine.py` | `trader/tests/test_risk_engine_layers.py` |
| Adapter 环境风险事件建议级别输出 | Current | `trader/adapters/binance/environmental_risk.py` | `trader/tests/test_binance_environmental_risk.py` |
| 自动执行 L3 强平并断链 | Next | 当前无完整执行主链路闭环 | N/A |
| AI 自动调参并直接放行高风险动作 | Target | 当前无越权能力（按治理禁止） | N/A |

---

## 3. Canonical KillSwitch Level Map（数字为准）

| Level | Canonical Name | 兼容别名（历史） | 说明 |
|---|---|---|---|
| 0 | NORMAL | `L0_NORMAL` | 正常 |
| 1 | NO_NEW_POSITIONS | `L1_NO_NEW_POS`、`L1_NO_NEW_POSITIONS` | 禁新开仓 |
| 2 | CANCEL_ALL_AND_HALT | `L2_CLOSE_ONLY`（仅兼容） | 以数字 2 为准，语义统一为撤单并停机 |
| 3 | LIQUIDATE_AND_DISCONNECT | `L3_FULL_STOP`（仅兼容） | 以数字 3 为准，语义统一为强平并断链 |

说明：
- 代码中历史别名仍可能出现（如 `environmental_risk.py`、`api/routes/risk.py` 注释），文档口径统一以上表为准。
- 对外契约以数字 level 为权威标识，别名仅做兼容映射。

---

## 4. 风险哲学与动作优先级

- Fail-Closed：无法确认一致性时优先降级/阻断，不做猜测继续
- 风险优先于信号：任何策略不得绕过风险层
- 环境风险与市场风险同级：断流、限流、状态漂移必须进入同一治理链路

---

## 5. Alignment 风险规则（修订）

Gate 期间按流域生效：
- Private/Execution：禁止正式执行驱动
- Public 行情：允许继续外发，但必须标记 `DEGRADED/ALIGNING`

Alignment 超时：
- 必须产生风险事件
- 允许升级 KillSwitch（单调升级，不自动降级）

---

## 6. 当前正式风险对象

- 市场风险：极端波动、流动性真空
- 仓位风险：单币种暴露、方向集中、杠杆约束
- 执行风险：重复下单、状态不明、异常滑点
- 环境风险：WS 假死、REST 不可用、限流风暴、状态漂移

---

## 7. 参数与前置检查

下单前最低检查项：
- symbol 可交易
- health 状态允许
- Alignment Gate 已通过（对应执行流）
- 杠杆与暴露在阈值内
- KillSwitch 级别允许当前动作

---

## 8. 文档验收门禁

本文件中每个 `Current` 条目必须附：
- 1 个代码路径
- 1 个非跳过测试名

若无法提供，降级为 `Next/Target`。

---

## 9. 一句话总结

Crypto v3.1.1 风险策略以“数字级别统一、单调升级、按流域阻断”为核心，先保证系统可生存，再讨论收益优化。
