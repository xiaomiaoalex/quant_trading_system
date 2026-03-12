# Reconciler Specification — Crypto v3.1.1

## 1. 文档目的

本文档定义 `quant_trading_system Crypto v3.1.1` 的 Reconciler 目标形态、契约与验收门禁。  
本文件在 v3.1.1 中明确归类为 `Next`，不是当前已交付能力。

---

## 2. 当前状态声明

Capability 判定：
- Reconciler 运行时服务：`Next`
- 当前代码库无 reconciler service/route 落地证据（`trader/services`、`trader/api/routes` 未提供对应模块）

结论：
- 本文档是下一阶段实现规范，不应作为“当前已具备持续对账闭环”的承诺依据。

---

## 3. 角色与边界（Next 设计）

Reconciler 负责：
- 校验本地订单/成交/仓位/余额与交易所真相的一致性
- 产出结构化分歧级别与证据
- 向 Policy Plane 输出可执行风险建议

Reconciler 不负责：
- 直接修改 Core 领域状态
- 跳过 Policy 直接触发交易动作
- 替代 Adapter 健康检查

---

## 4. 输入输出契约（Next）

### 4.1 输入
- 本地订单/成交/仓位/余额快照
- 交易所 REST 查询结果
- 交易所 WS 缓存状态
- Adapter health 与 Alignment 状态
- `reconcile_grace_period_ms`

### 4.2 输出
- `reconcile_run_id`
- `scope`
- `drift_level`（EXPECTED/UNEXPECTED/FATAL）
- `affected_entities`
- `evidence_refs`
- `recommended_action`

---

## 5. 分歧分级与建议动作（Next）

- `EXPECTED_DRIFT`：记录并持续观察
- `UNEXPECTED_DRIFT`：告警并限制新开仓（可配置）
- `FATAL_DIVERGENCE`：触发更高等级 KillSwitch 或人工接管

说明：分级为建议，不绕过 Policy Plane 的最终治理。

---

## 6. 里程碑门禁（Gate 驱动）

### 6.1 Entry Gate
- Reconciler service 模块存在
- `/v1/reconcile/*` 或等价 API 契约存在

### 6.2 Exit Gate
- 非跳过测试覆盖：orders/fills/positions/balances 四类核对
- 覆盖 Grace Window、乱序、延迟、重复上报场景
- 与 KillSwitch 联动行为可验证

### 6.3 演练 Gate
- 故障注入演练记录：交易所状态漂移、网络抖动、对齐失败

---

## 7. 与当前架构的衔接约束

在 Next 落地前：
- 事件与风控链路继续使用当前机制（Adapter + RiskService）
- 文档、README、项目说明中不得将 Reconciler 标记为 Current

---

## 8. 一句话总结

Reconciler 在 v3.1.1 中是明确的 `Next` 能力：规范先行、实现后置；未满足代码与测试门禁前，不得作为当前能力承诺。
