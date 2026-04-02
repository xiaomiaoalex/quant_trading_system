# Phase 6 — Risk Convergence & Allocation

> 更新日期：2026-04-02
> 目标：把现有分散的风险控制、仓位限制和文档状态收敛成单一真相源与统一决策面。

---

## 1. 阶段定位

Phase 5 已完成回测框架升级，Phase 6 不再以“增加更多表层功能”为主，而是优先解决四个结构性问题：

1. 文档真相源漂移：`PROJECT_STATUS.md`、`PLAN.md`、历史计划文档存在阶段状态不一致。
2. 仓位规则分散：时间窗口、策略级限额、暴露上限、KillSwitch 语义已存在，但尚未收敛成统一 sizing 决策。
3. 多策略缺少最小分配器：单策略资源限制已具备，但多策略并发时缺少预算竞争裁决。
4. 替代数据脆弱：Funding / OI / 清算 / 链上 / 事件等数据质量未被系统化纳入风险折扣。

---

## 2. Phase 6 里程碑

### M1. Truth Source Reset

**目标**：所有核心文档对当前阶段、已完成项、下一步计划保持一致。

**交付物**：
- 更新 `PROJECT_STATUS.md`
- 更新 `PLAN.md`
- 维护本文件作为 Phase 6 执行入口

**验收标准**：
- 不再出现同一任务在不同文档中同时“已完成”和“待开始”
- 当前阶段目标清晰定义为 Risk Convergence & Allocation

### M2. Survival Risk Sizer

**目标**：实现统一 `risk_sizer`，把前置风控从 pass/reject 升级为“可缩放的仓位决策”。

**建议实现位置**：
- `trader/core/domain/services/risk_sizer.py`

**输入建议**：
- `size_by_stop`
- `strategy_cap`
- `symbol_exposure_cap`
- `total_exposure_cap`
- `liquidity_cap`
- `time_coef`
- `drawdown_coef`
- `venue_health_coef`
- `regime_coef`

**目标公式**：
```text
final_size
= min(size_by_stop, strategy_cap, symbol_exposure_cap, total_exposure_cap, liquidity_cap)
  * time_coef
  * drawdown_coef
  * venue_health_coef
  * regime_coef
```

**验收标准**：
- 纯计算、无 IO、可重复
- 任一关键输入缺失时 Fail-Closed
- 单元测试覆盖边界值、零值、冲突限制和异常输入

### M3. Drawdown / Venue 联动去杠杆

**目标**：把“硬拒绝”前移为“先缩再停”的个人版生存风控。

**交付物**：
- 回撤缩放规则
- venue 健康度系数映射
- close-only / no-new-position / hard-halt 阶梯式动作定义

**建议状态映射**：
- 轻度回撤：半仓
- 中度回撤：close-only
- 严重回撤或风险系统异常：升级 KillSwitch

**验收标准**：
- 回撤达到软阈值时触发缩仓而非直接熔断
- DEGRADED / alignment 异常状态能够影响最大允许仓位

### M4. Minimal Capital Allocator

**目标**：在不引入重型组合优化器的前提下，实现多策略最小资本分配。

**建议实现位置**：
- `trader/services/capital_allocator.py`

**职责范围**：
- 净暴露与总暴露预算
- 同向信号预算竞争
- 反向信号净额化或互斥
- 输出 `approved / clipped / rejected` 及原因

**明确不做**：
- 复杂均值方差优化
- 机构级 VaR 归因平台
- 多 venue 路由优化

### M5. Alternative Data Reliability Gate

**目标**：将替代数据质量变为显式风险输入，而不是隐含假设。

**交付物**：
- 数据健康度模型：freshness / coverage / delay / source_quality
- 接入 signal gating 或 `risk_sizer`
- 缺失与延迟的降级策略

**验收标准**：
- 数据源断流或延迟时，系统能自动缩仓或禁止新开仓
- 替代数据质量可观测、可审计

---

## 3. 推荐执行顺序

1. M1 文档收敛
2. M2 `risk_sizer`
3. M3 回撤与 venue 联动
4. M4 `capital_allocator`
5. M5 数据健康度治理

---

## 4. 当前已知依赖

- `trader/core/application/risk_engine.py`
- `trader/core/domain/services/position_risk_constructor.py`
- `trader/core/application/strategy_protocol.py`
- `trader/services/strategy_runner.py`
- `trader/adapters/binance/rest_alignment.py`
- `trader/adapters/binance/degraded_cascade.py`

---

## 5. 设计约束

1. Core Plane 保持无 IO。
2. 所有风险决策必须 Fail-Closed。
3. 不重复建设机构级组合平台。
4. 优先个人版生存风控，再考虑策略扩张。
5. 所有新增功能必须补测试，并同步更新 `PROJECT_STATUS.md` 与 `docs/EXPERIENCE_SUMMARY.md`。
