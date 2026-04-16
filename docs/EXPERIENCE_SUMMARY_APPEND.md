---

## 十七、Phase A-F 脚本实现踩坑记录 (2026-04-16)

### 17.1 dataclass 字段顺序与默认值约束

**问题描述**：
在 `model_drift_detector.py` 的 `DriftMetrics` 中，字段顺序导致 LSP 报错。

**根因**：
Python dataclass 要求：有默认值的字段必须在无默认值字段之后。

**解决方案**：
将无默认值字段放在前面，有默认值字段放在后面。

---

### 17.2 dataclass 中 Literal 类型赋值问题

**问题描述**：
在 `__post_init__` 中，直接赋值 `self.drift_severity = severity` 报错。

**解决方案**：
使用 `object.__setattr__()` 绕过 dataclass 的类型检查。

---

### 17.3 Qlib/Hermes 边界设计原则

**核心约束**：
1. Hermes 只调用研究脚本，不直接调用下单接口
2. Qlib 只进入 Insight/Research 路径，不直接触发下单
3. Core Plane 保持 IO-clean / AI-clean，执行依旧走 StrategyRunner + RiskEngine + OMS

**架构边界**：
Hermes -> Qlib -> qlib_to_strategy_bridge -> StrategyRunner + RiskEngine + OMS -> Broker Adapter