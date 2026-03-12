
# Binance Adapter Specification — Crypto v3.1.1

## 1. 文档目的

本文档定义 `quant_trading_system Crypto v3.1.1` 中 Binance 适配层的职责、边界、状态机行为与验收口径。  
原则：不删除愿景，但对能力做 `Current / Next / Target` 分层，避免把未落地能力写成当前能力。

---

## 2. Capability Matrix（Current / Next / Target）

Current 判定标准（必须同时满足）：
- 代码路径可定位
- 有非跳过测试覆盖关键行为
- 运行前提明确（如网络/PG 前置条件）

| 能力 | 状态 | 代码证据 | 测试证据 |
|---|---|---|---|
| Public/Private 物理流与 FSM 隔离 | Current | `trader/adapters/binance/public_stream.py`、`trader/adapters/binance/private_stream.py`、`trader/adapters/binance/connector.py` | `trader/tests/test_binance_connector.py` |
| Private 流在 ALIGNING 期间阻断执行相关消息 | Current | `trader/adapters/binance/private_stream.py`（`_is_aligning` 分支） | `trader/tests/test_hard_properties.py`（`test_alignment_gate_buffers_messages`） |
| WS 重连触发 REST P0 对齐 | Current | `trader/adapters/binance/connector.py`（`_on_force_resync`） | `trader/tests/test_resilience_circuit_breaker.py`（`test_ws_reconnect_triggers_p0_alignment`） |
| 429 风暴降级（P0 优先 + 退避） | Current | `trader/adapters/binance/rate_limit.py`、`trader/adapters/binance/backoff.py` | `trader/tests/test_binance_rate_limit.py` |
| Reconciler 持续对账闭环接入 Adapter | Next | 当前无 reconciler service/route | N/A |
| AI 参与适配层判决/放行 | Target | 当前无 AI adapter gate 实现 | N/A |

---

## 3. 适配层职责边界

Binance Adapter 当前负责：
- 市场流接入（Public）
- 账户/订单流接入（Private）
- REST 对齐与重连恢复
- 限流、退避、健康状态输出
- 外部数据标准化后交给上游

不负责：
- 最终交易决策
- 风险规则裁决
- Core 真相修复裁定
- AI 推理与自动放行

---

## 4. Alignment Gate（按流域生效）

### 4.1 触发条件
- WS 重连
- 快照失效或序列异常
- 静默断流检测触发

### 4.2 Gate 期间行为（v3.1.1 口径）
- Private/Execution 事件阻断外发（防止未对齐状态进入执行链）
- Public 行情流允许继续外发，但必须标记 `DEGRADED/ALIGNING`
- 允许内部快照修复、缓存重建与审计记录

### 4.3 退出条件
- 快照恢复成功
- 增量连续且无 gap
- grace window 内无复发异常

---

## 5. 标准化输出要求（Current）

所有 Adapter 输出必须映射为 canonical 事件，并保留：
- `local_receive_ts_ms`
- `exchange_event_ts_ms`（或等价字段）
- `source`
- `raw_reference`（或可追溯引用）

说明：字段命名可在实现层按模型细化，但语义必须可审计、可回放。

---

## 6. Health 状态与动作

建议统一语义：
- `HEALTHY`：正常
- `DEGRADED`：可运行但受限
- `DISCONNECTED`：连接不可用，触发恢复与降级流程
- `ALIGNING/REBUILDING`：对齐恢复中

动作原则（Fail-Closed）：
- 无法确认一致性时，优先阻断执行相关事件，不做“猜测继续”。

---

## 7. 文档验收门禁（新增）

本规范中标记为 `Current` 的条目必须同时附带：
- 1 个代码路径
- 1 个非跳过测试名

若缺任一证据，则自动降级为 `Next/Target`。

---

## 8. 一句话总结

Binance Adapter 的当前目标不是“吞吐最大化”，而是“在不稳定交易所输入下维持可验证的流域隔离与对齐恢复，并只对上游输出可审计的标准事件”。
