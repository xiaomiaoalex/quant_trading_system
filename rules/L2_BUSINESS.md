# L2 业务/领域规则（智能生效）
# 版本: v1.0.0
# 根据当前任务上下文自动判断是否加载

---

## 五层架构边界（禁止越界）

本系统采用**五层平面架构**，严禁跨层直接调用或状态污染。

| 平面 | 路径 | 允许 | 禁止 |
|------|------|------|------|
| **Core Plane** | `trader/core/` | 领域模型、OMS、RiskEngine、DeterministicLayer、纯计算 | 任何 IO、网络、DB、环境变量、sleep、外部原始字段名 |
| **Adapter Plane** | `trader/adapters/` | WS/REST、限流退避、数据清洗、Public/Private Stream 物理隔离、REST Alignment | 直接修改 Core 状态、共享 Public/Private Stream 状态 |
| **Persistence Plane** | `trader/adapters/persistence/` | append-only event log、PG risk repository、投影读模型 | 绕过事件溯源语义直接覆盖真相源 |
| **Policy Plane** | `trader/core/application/`、`trader/services/risk.py` | KillSwitch、风险规则、TimeWindow、决策策略 | Fail-open、绕过 KillSwitch 升级语义 |
| **Control Plane** | `trader/api/`、`trader/services/` | FastAPI、生命周期管理、策略注册、全局风险触发 | 绕过 Risk/Policy/Core 直接下单 |

数据流主路径：Binance WS/REST → Adapter 清洗 → OMS/Core → Persistence/Event Log → API/Frontend。

## 硬性正确性约束（不可违反）

### 1. 单调状态机
- 订单状态只能前进（如：NEW → FILLED）
- 禁止从终端状态（CANCELLED/REJECTED/FILLED）回退
- 所有状态更新必须走统一 CAS 入口

### 2. 全链路幂等
- 基于 `cl_ord_id` + `exec_id` 进行去重
- WS 和 REST 可能并发送达同一事件，不得重复记账

### 3. Fail-Closed
- 无法确认一致性时，进入 `DEGRADED_MODE` 并触发 KillSwitch
- **严禁使用裸 `except: pass`**
- 禁止猜测继续

### 4. 确定性并发
- 同一 `cl_ord_id` 的并发处理必须加锁（hashed lock / actor 分区）
- 不依赖时序假设

### 5. Broker 是真相源
- WS 为低延迟驱动，REST 为纠偏
- 重连后必须先做 REST Alignment 再恢复业务

### 6. 接口契约一致
- 函数签名、DTO、事件 Schema、API 字段、跨层调用和命名重构必须遵守 `docs/INTERFACE_CONTRACTS.md`
- 外部字段（如 Binance `clientOrderId`）只能在 Adapter/API 边界转换，禁止泄漏到 Core Plane

## KillSwitch 级别

| 级别 | 名称 | 行为 |
|------|------|------|
| L0 | NORMAL | 正常交易 |
| L1 | NO_NEW_POSITIONS | 禁止新开仓，允许平仓/撤单 |
| L2 | CANCEL_ALL_AND_HALT | 撤所有挂单，禁止交易 |
| L3 | LIQUIDATE_AND_DISCONNECT | 强平所有持仓，断开 Broker |

**所有 KillSwitch 升级在当前 session 内不可逆（Fail-Closed）**。

## Portfolio 和 PnL 规则

Position、Exposure、Daily PnL、Unrealized PnL 必须明确：
- **输入数据**：成交回报中的 qty/price
- **计算公式**：qty × price
- **更新时间点**：on_fill 回调时
- **展示口径**：`total_positions`（数量）、`total_exposure`（数量×当前价）

存在估算值和真实值时，必须明确区分。

PnL 相关逻辑改动后，必须验证：
- 空仓、单仓、多仓
- 部分成交、多次成交
- 费用影响、行情波动

---

**适用场景**：涉及 Core/Adapter/Persistence/Policy 层时
**生效方式**：智能生效（根据任务上下文自动加载）
**维护者**：架构师
**版本**：v1.0.0