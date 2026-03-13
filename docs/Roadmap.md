## 开发蓝图（Gate 驱动里程碑）

借鉴 v4.3.0 的"Gate + 最小闭环"哲学，不铺大饼，先做最关键的事情。

**版本说明**：本文档为 v3.1.1 版本，采用 **Current / Next / Target** 能力分层：
- **Current**：代码存在 + 有非跳过测试 + 运行前提明确
- **Next**：下一阶段待实现，需要前置条件满足
- **Target**：未来目标，需要更多前置条件

---

## Sprint 1–2：数据底盘与感知层

### 目标
把 Binance 数据接入做成"可信输入"，而不是"能收到消息"。

### 任务
- Binance Adapter 封装
- WS 静默断流检测
- REST 快照对齐
- Alignment Gate（按流域生效）
- PostgreSQL 事件溯源 Schema

### 验收
- 断流与重连可被检测
- 未对齐时 Private/Execution 事件阻断，Public 行情流打 DEGRADED 标签
- 数据流可审计

### 能力状态
| 任务 | 状态 | 代码证据 | 测试证据 |
|---|---|---|---|
| Binance Adapter 封装 | Current | `trader/adapters/binance/` | `trader/tests/test_binance_*.py` |
| WS 静默断流检测 | Current | `trader/adapters/binance/connector.py` | `trader/tests/test_binance_connector.py` |
| REST 快照对齐 | Current | `trader/adapters/binance/rest_alignment.py` | `trader/tests/test_binance_rest_alignment.py` |
| Alignment Gate | Current | `trader/adapters/binance/private_stream.py` (line 394) | `trader/tests/test_binance_private_stream.py` |
| PG 事件溯源 | Current | `trader/adapters/persistence/risk_repository.py` | `trader/tests/test_risk_idempotency_persistence.py` |

### Entry/Exit Gate
- **Entry**：Adapter 核心模块与接口定义存在
- **Exit**：核心测试非 skip + 故障演练记录完成

---

## Sprint 3–4：Core 状态机与风险闭环

### 目标
把"不会乱掉"放在"先能跑"之前。

### 任务
- 订单状态单调递增
- 终态不可逆
- 风险事件收口
- KillSwitch API
- 账户 / 持仓状态管理

### 验收
- 无重复记账
- 无逆序回滚
- 风控可本地锁死

### 能力状态
| 任务 | 状态 | 代码证据 | 测试证据 |
|---|---|---|---|
| 订单状态单调递增 | Current | `trader/core/application/deterministic_layer.py` | `trader/tests/test_deterministic_layer.py` |
| 终态不可逆 | Current | `trader/core/application/deterministic_layer.py` | `trader/tests/test_deterministic_layer.py` |
| 风险事件收口 | Current | `trader/services/risk.py` | `trader/tests/test_risk_engine_layers.py` |
| KillSwitch API | Current | `trader/services/killswitch.py` | `trader/tests/test_api_endpoints.py` |
| 持仓状态管理 | Current | `trader/core/domain/models/position.py` | `trader/tests/test_domain_events.py` |

### KillSwitch Canonical Level Map
| 级别 | 名称 | 行为 |
|---|---|---|
| L1 | PAUSED | 禁止新订单，允许现有持仓平仓 |
| L2 | HALTED | 禁止所有交易，仅响应查询 |
| L3 | LOCKDOWN | 禁止所有操作，需人工介入 |

### Entry/Exit Gate
- **Entry**：CAS 与风险规则路径可定位
- **Exit**：状态机与风险回归测试非 skip + 失败场景演练完成

---

## Sprint 5–6：Crypto 特征沙盒与基础研究主线

### 目标
建立第一批真正贴近 crypto 的研究资产。

### 任务
- 趋势基础特征
- 量价基础特征
- Funding / OI 接口预留
- Feature Sandbox
- 流动性差标的过滤
- 事件驱动样例回放

### 验收
- 有第一批可信规则
- 坏规则能被拒绝
- 基础研究链路成立

### 能力状态
| 任务 | 状态 | 前置条件 |
|---|---|---|
| 趋势基础特征 | Next | Public Stream + Storage 完备 |
| 量价基础特征 | Next | Public Stream + Storage 完备 |
| Feature Sandbox | Next | 事件模型 + 特征存储 |
| Funding / OI 接口 | Next | 永续合约适配器 |

### Entry/Exit Gate
- **Entry**：特征与沙盒模块落地
- **Exit**：研究链路测试/回放通过 + 噪声规则拦截证据完整

---

## Sprint 7–8：对账器与 AI 观测哨

### 目标
形成最小研究—执行—复盘闭环。

### 任务
- **Reconciler**（Next，需要前置条件）
- 账户状态与交易所状态对账
- 确认窗口（reconcile grace period）
- **AI Regime 观测报告**（Target，仅读洞察）
- 统一复盘报告

### 验收
- 本地和交易所状态可校验
- AI 输出仅作为 Insight，不直接执行
- 闭环可复盘

### 能力状态
| 任务 | 状态 | 前置条件 |
|---|---|---|
| Reconciler 持续对账服务 | Next | Core 状态机 + Event Log 完善 |
| 账户状态对账 | Next | Reconciler 实现后 |
| 统一复盘报告 | Next | Event Log + 状态机 |
| AI Regime 观测 | Target | 审计数据 + 事件模型 + 报告管道 |

### Entry/Exit Gate
- **Entry**：Reconciler/AI 相关模块和接口已落地
- **Exit**：非 skip 测试 + 端到端演练记录 + 审计追踪字段完整

---

## Sprint 9–10：执行闭环与自动化

### 目标
完成从信号到订单的完整自动化链路。

### 任务
- Position & Risk Constructor
- Target-to-Orders 转换
- 自动化执行 Runner
- 统一报告系统

### 能力状态
| 任务 | 状态 | 前置条件 |
|---|---|---|
| Position & Risk Constructor | Next | 研究信号层 + 风险引擎 |
| Target-to-Orders | Next | Position Constructor |
| 自动化执行 Runner | Target | Position Constructor + Risk Policy 完备 |
| 统一报告系统 | Next | Event Log + 状态机 |

### Entry/Exit Gate
- **Entry**：Position Constructor 模块落地
- **Exit**：端到端下单测试通过 + 风险控制生效

---

## Sprint 11–12：复盘与 AI 增强

### 目标
建立完整的复盘和 AI 增强能力。

### 任务
- Replay Runner
- AI Insight Copilot
- 完整回测框架

### 能力状态
| 任务 | 状态 | 前置条件 |
|---|---|---|
| Replay Runner | Target | Event Log + 状态机 + PG 持久化完备 |
| AI Insight Copilot | Target | 统一报告 + 事件模型完整 |
| 完整回测框架 | Target | 研究信号 + 事件模型完备 |

### Entry/Exit Gate
- **Entry**：Event Log + 状态机完整
- **Exit**：回放测试通过 + AI 输出仅作 Insight

---

## Gate 驱动验收原则

每个 Sprint 的 **Entry/Exit Gate** 必须同时满足以下三项才算完成：

1. **代码存在性**：相关模块和接口已落地，可定位到仓库路径
2. **非跳过测试**：核心行为有测试覆盖，且测试未被 skip
3. **故障演练**：已记录故障场景演练结果（断流、错位、极端行情）

---

## 关键约束

### 禁止事项
- 先写复杂策略，后补状态机
- 先写 AI 自动化，后补风控
- 先做多市场扩展，后补 Binance 主链路
- 把 Binance SDK 调用直接写进 Core

### 优先级原则
1. **系统不瞎** → 数据可信、状态确定
2. **系统不动** → 风险可控、KillSwitch 有效
3. **系统能动** → 最小闭环、执行链路
4. **系统聪明** → 研究信号、仓位优化
5. **系统可审计** → Replay、报告、AI 洞察
