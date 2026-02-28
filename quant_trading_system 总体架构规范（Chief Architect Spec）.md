# quant_trading_system 总体架构规范（Chief Architect Spec）

> 目标：从零构建**生产级自动化交易系统**。在“国内商业 VPN + 海外交易所/券商”不稳定网络环境下，系统必须满足：  
> **Correctness First（交易正确性）**、**Fail-Closed（绝境逢生）**、**可回放可审计**、**策略研发效率优先**、**可扩展接入国内合规券商（XTP/CTP/QMT）**。

---

## 1. 架构原则（Non-Negotiable）

### 1.1 Correctness First（交易正确性优先）
- 订单状态机必须**单调**，终态不可回滚。
- 成交/回报必须**幂等**，重复事件不得重复记账或重复触发。
- 所有外部回报都以 **回报流（ExecutionReports）为准**，不信任 submit/REST 返回即代表成功。

### 1.2 Event Sourcing + Snapshot（可回放与可审计）
- event_log（append-only）为第一性数据。
- 快照用于加速重启恢复：按聚合根（OMS/Portfolio）+ stream_key 分片。
- 回放必须能重建 OMS/Portfolio 的精确状态。

### 1.3 Fail-Closed（不确定即降级/熔断）
- 无法确认一致性时：宁可停新单/熔断，也不“猜测继续”。
- 控制面不可达时：本地必须进入自保（local killswitch）。

### 1.4 强边界解耦（Plane Separation）
- **Core Plane**：纯业务，无 I/O。
- **Adapter Plane**：所有外部 I/O + 容错整形。
- **Control Plane**：治理、审计、策略管理、熔断控制。

### 1.5 可观测性（Observability Built-in）
- 每条外部消息必须携带 meta：`local_receive_ts_ms`、`exchange_event_ts_ms`、`seq/update_id`、`source`、`out_of_order`、`gap_detected`。
- 关键状态迁移必须可追踪（trace_id）。

---

## 2. 三平面职责规范（Core / Adapter / Control）

### 2.1 Core Plane（业务核心，无 I/O）
**职责**
- 领域模型：Instrument、OrderIntent、Order、Execution、Portfolio、Position、PnL
- OMS：唯一订单状态入口（Rank 单调、幂等、乱序容忍）
- Risk Engine：Fail-Closed + KillSwitch 规则执行
- Event Sourcing：写 canonical events；支持 Snapshot + Replay
- 投影/读模型规则（可在 Control/Infra 实现执行）

**禁止**
- 禁止网络/数据库/文件 I/O
- 禁止直接调用券商 SDK
- 禁止在多处更新订单状态（必须经 OMS 单入口）

### 2.2 Adapter Plane（外部 I/O + 容错整形）
**职责**
- Public/Private 连接隔离（物理连接 + 状态机独立）
- Dual-Driver Sync：WS 主驱动 + REST 对齐/补偿
- 静默断流检测：recv timeout / pong waiter timeout（禁止依赖 TCP keepalive）
- 限流与退避：Token Bucket + p0-only + Retry-After 下限 + full jitter backoff
- 强制对齐 gate：重连后对齐成功前不得对外 emit
- 输出 canonical reports：OrderEvent/ExecutionEvent/Balance/AdapterHealth
- 产生 DEGRADED 信号：reconnect storm / stale storm / rate-limit storm

**禁止**
- 禁止实现 OMS/Portfolio 的业务规则（只整形事实）
- 禁止在 429/断网时无限重试造成 API 风暴

### 2.3 Control Plane（治理与运维控制）
**职责**
- 策略治理：注册/版本/参数 schema、deployment 启停、参数热更新
- 风控治理：KillSwitch（L0-L3）、风险事件 ingest（dedup）
- 审计查询：events/snapshots/orders/executions/positions
- 实时推送：orders/pnl/health 的 WebSocket streams
- 对账（后续）：以券商为真相的 Reconciler

**禁止**
- 禁止让控制面请求阻塞交易执行循环（必须异步/可降级）
- 禁止自动把 KillSwitch 从 L1/L2/L3 降回 L0（需人工确认）

---

## 3. 关键系统组件规范（必备）

### 3.1 OMS 状态机（Core）
- 状态：`PENDING -> NEW -> SUBMITTED -> PARTIALLY_FILLED -> FILLED/CANCELLED/REJECTED/EXPIRED`
- 约束：
  - Rank 单调，不回滚
  - exec 去重（exec_id/exec_key）
  - Pending 超时：Fail-Closed（拒绝/过期并写事件）

### 3.2 Portfolio/PnL（Core）
- 持仓仅由 fill 驱动更新（不由 order update 直接推断）
- PnL：
  - Realized：由平仓 fill 计算
  - Unrealized：由 mark price 计算（mark 来自行情/估值源）
- 对账以 broker 为真相，差异触发熔断或修复

### 3.3 Adapter 双驱动同步（Adapter）
- WS：低延迟推进
- REST：纠偏恢复
- 重连后必须 REST Alignment（P0：openOrders + account）
- 重连期间必须 gate：对齐前不对外 emit

### 3.4 Rate Limit & Backoff（Adapter）
- Token Bucket：支持 priority P0/P1/P2
- 429：
  - 解析 Retry-After
  - degrade_to_p0_only(cooldown)
  - backoff full jitter，且 sleep >= Retry-After
- max retry / cooldown / storm control 必须存在

### 3.5 DEGRADED 级联（Adapter → Control）
- 判定：
  - reconnect storm / stale storm / rate-limit storm
- 行为：
  - 上报 EnvironmentalRiskEvent（dedup_key 确定性窗口）
  - 自动触发 GLOBAL KillSwitch L1（幂等）
  - 控制面不可达累计超阈值 → 本地 lock（Fail-Closed）

---

## 4. 数据与存储规范（Event Log / Snapshot / Read Models）

### 4.1 Event Log（append-only）
字段最低要求：
- stream_key（account:deployment:venue）
- event_type、schema_version
- trace_id
- ts_ms（exchange/local）
- payload（JSONB/结构化）

### 4.2 Snapshot
- 频率：每 1000 events 或每 5 分钟（先到为准）
- 粒度：按 stream_key 分片
- 必须包含 last_event_id/last_ts 以加速回放
- 写入不得阻塞交易流水线（COW / 异步写）

### 4.3 Read Models（投影）
- orders/executions/positions/pnl 由投影器从 event_log 生成
- 幂等投影：重复事件不膨胀

---

## 5. 接口契约规范（Ports + API）

### 5.1 Adapter → Core（Ports）
- ExecutionPort：submit/cancel/query
- ExecutionReportsPort：async stream 输出 canonical events
- Snapshot APIs：get_open_orders/account/positions（恢复/对账用）

### 5.2 Control Plane API（最小闭环）
必须具备：
- `POST /v1/risk/events`（dedup_key 幂等）
- `POST /v1/killswitch`（GLOBAL L1 幂等）
建议具备：
- `/v1/events`、`/v1/snapshots`、`/v1/orders`、`/v1/portfolio/*`
- WS streams：`/v1/stream/orders`、`/v1/stream/pnl`

---

## 6. KillSwitch 行为规范（严格定义）

- L0 NORMAL：允许交易
- L1 NO_NEW_POSITIONS：禁止新开仓；允许平仓/减仓/撤单
- L2 CANCEL_ALL_AND_HALT：撤销所有挂单；禁止交易（只允许查询与对账）
- L3 LIQUIDATE_AND_DISCONNECT：强平所有持仓；断开 broker 连接；进入人工解锁流程

---

## 7. 测试与验收规范（SRE Gate）

### 7.1 必须离线、可重复
- 禁止真实网络、禁止真实 sleep、禁止依赖调度抖动
- 使用 FakeClock/FakeHTTP/FakeWebSocket

### 7.2 Hard Properties（必须锁死）
- recv 假死 → timeout → STALE → reconnect（单飞）
- pong 超时 N 次 → STALE → reconnect
- reconnect 后 alignment gate：对齐成功前不 emit
- 429：p0-only + Retry-After 下限 + backoff sleep
- 控制面不可达累计超阈值 → local_killswitch_active=True
- flapping：cooldown 防抖，上报次数受限

### 7.3 离线端到端集成测试
- PrivateStream 假死 → 重连 → 429 → 退避 → 对齐成功 → 继续推进
- 断言 OMS 最终一致、不回滚、不重复成交、无 API 风暴、无 task 泄漏

---

## 8. 扩展性规范（国内券商接口保留）
- 通过 ports 契约保持 XTP/CTP/QMT 可插拔：
  - ExecutionPort / ExecutionReportsPort / AccountPort
- InstrumentMapper：canonical instrument ↔ 券商编码
- cl_ord_id ↔ broker_order_id 映射表为长期资产

---

## 9. 当前工程阶段的强制路线
1) Adapter Plane Hardening（已完成修复）
2) Hard Properties Tests 对真实模块生效（CI Gate）
3) 离线端到端集成测试（Adapter→Determinism→OMS）
4) 最小 Control Plane 风险+熔断闭环
5) Strategy SDK/Registry/Runner 主线推进