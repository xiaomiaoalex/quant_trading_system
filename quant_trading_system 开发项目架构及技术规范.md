# quant_trading_system v2 主文档（唯一执行规范）

> 版本：v2.0.0  
> 状态：Active  
> 生效日期：2026-02-28  
> 适用范围：Binance 单账户首发，Fail-Closed 默认开启  
> 文档定位：本文件同时承载“规范态（必须遵守）+ 落地态（当前实现）”

## 文档使用规则（双态结构）

1. 每章均包含：
- 规范态：目标架构和硬约束。
- 落地态：当前仓库实现位置与完成度。
- 代码映射表：`现状/缺口/改造点`，用于直接排期。

2. 变更治理：
- 任何架构级改动必须同时更新本文件与 `Spec Changelog`。
- 任何跨边界改动（事件契约、状态机、接口语义）必须附 ADR 编号。

3. 默认假设（已锁定）：
- 功能并行推进，但 P0 阻塞缺陷优先清零。
- 存储路线为 PostgreSQL（不走 SQLite 过渡）。
- 部署路线为单机 Docker Compose 首发。

---

## 1. 目标与SLO

### 1.1 规范态
- 正确性优先：订单状态单调、成交幂等、回放可重建。
- 可用性目标：控制面故障不影响本地 Fail-Closed 执行。
- 可靠性目标：私有流假死可在超时后进入 STALE 并触发恢复流程。
- 可观测性目标：关键路径具备可追踪事件和风险审计记录。

### 1.2 落地态
- 已有健康探针、核心回归测试、硬属性测试骨架。
- 当前缺少明确 SLI/SLO 数值、告警阈值与错误预算文档化。

### 1.3 代码映射表
| 代码位置 | 现状 | 缺口 | 改造点 |
| --- | --- | --- | --- |
| `trader/api/routes/health.py` | 提供 `/health` 存活检查 | 无分级健康（依赖、交易链路、控制链路） | 扩展为 liveness/readiness/dependency 三级 |
| `trader/tests/test_hard_properties.py` | 已覆盖关键可靠性属性 | 未绑定 SLO 指标阈值 | 将关键断言映射为 SLI 并纳入 CI 报告 |
| `docs/architecture_review.md` | 有架构审阅文档 | 非执行规范、无目标值 | 与本主文档统一，迁移关键指标 |

---

## 2. 三平面边界

### 2.1 规范态
- Core Plane：领域模型、状态机、风控、确定性处理，不直接做外部 I/O。
- Adapter Plane：交易所/券商协议接入、重连、限流、对齐、脏数据整形。
- Control Plane：治理 API、风险事件入口、KillSwitch、审计查询。
- 跨平面通信必须通过 Ports + Canonical Event，禁止隐式耦合。

### 2.2 落地态
- 目录分层已基本形成。
- 服务层仍集中在单文件，边界可维护性不足。

### 2.3 代码映射表
| 代码位置 | 现状 | 缺口 | 改造点 |
| --- | --- | --- | --- |
| `trader/core/application/ports/__init__.py` | 核心端口定义已存在 | 部分端口语义未文档冻结 | 增补端口契约注释与兼容策略 |
| `trader/adapters/binance/*.py` | Adapter 分层完整 | 与 Control 交互路径未统一抽象 | 抽象 `ControlPlaneClient` 并复用 |
| `trader/api/routes/*.py` | Control API 路由齐全 | Service 聚合过重 | 拆分 `trader/services/__init__.py` 为领域模块 |

---

## 3. Canonical Event

### 3.1 规范态
- 事件作为跨平面唯一业务载体。
- Envelope 冻结字段：`trace_id`、`stream_key`、`schema_version`、`exchange_event_ts_ms`、`local_receive_ts_ms`、`source`、flags。
- 事件必须携带可用于重放、追踪、去重的元信息。

### 3.2 落地态
- 领域事件类型已集中定义，确定性层输入输出类型已成型。
- API 层 `EventEnvelope` 仍偏简化，尚未完全对齐冻结字段集合。

### 3.3 代码映射表
| 代码位置 | 现状 | 缺口 | 改造点 |
| --- | --- | --- | --- |
| `trader/core/domain/models/events.py` | 事件枚举与构造函数已存在 | 与 API 模型字段集不完全一致 | 明确领域事件到 API 事件映射 |
| `trader/core/application/deterministic_layer.py` | `RawOrderUpdate/RawFillUpdate/OrderEvent/ExecutionEvent` 元数据较完整 | flags 语义仍分散 | 统一 flags 字段与含义表 |
| `trader/api/models/schemas.py` | `EventEnvelope` 已提供 `trace_id/stream_key/schema_version` | 缺 `exchange_event_ts_ms/local_receive_ts_ms/source/flags` | 升级模型并保留向后兼容 |
| `trader/adapters/binance/private_stream.py` | 输入已携带 `local_receive_ts_ms` 与 `source` | 与 API 事件投递链路未强制对齐 | 在入库/投递层补齐字段检查 |

### 3.4 接口冻结表（v1 Frozen Interface）
| 对象 | 冻结字段/语义 | 向后兼容策略 | 弃用策略 |
| --- | --- | --- | --- |
| Event Envelope | `trace_id`、`stream_key`、`schema_version`、`exchange_event_ts_ms`、`local_receive_ts_ms`、`source`、flags | 仅允许新增可选字段，不破坏旧字段 | `deprecated -> remove` 两阶段，至少跨 1 个小版本 |
| Deterministic 输入 | `RawOrderUpdate`、`RawFillUpdate` 字段名与类型 | 新字段只能追加并给默认值 | 移除字段前先打 `deprecated` 并出迁移文档 |
| Deterministic 输出 | `OrderEvent`、`ExecutionEvent` 关键业务语义不变 | 允许新增观测字段，不改幂等关键键 | 破坏性变更必须 ADR + 主版本升级 |
| 风险上报接口 | `POST /v1/risk/events` 入参含 `dedup_key` | 保持幂等语义和状态码约定 | 变更状态码需双版本并行窗口 |

---

## 4. 状态机与风控

### 4.1 规范态
- OMS 状态严格单调，不可逆回滚。
- 风控按 Pre/In/Post-Trade 分层，Fail-Closed 为默认策略。
- KillSwitch 级别语义固定：L0/L1/L2/L3。

### 4.2 落地态
- 确定性层已实现去重、顺序容忍、重置路径。
- 风控引擎可用，但插件化分层与 KillSwitch 一体化仍需补强。

### 4.3 代码映射表
| 代码位置 | 现状 | 缺口 | 改造点 |
| --- | --- | --- | --- |
| `trader/core/application/oms.py` | 有订单生命周期管理实现 | 规范化状态迁移表未外显 | 输出状态迁移矩阵与非法迁移测试 |
| `trader/core/application/deterministic_layer.py` | 幂等键、缓冲与 reset 机制存在 | 复杂边界条件缺少文档化约束 | 将 Hard Property 与实现点一一绑定 |
| `trader/core/application/risk_engine.py` | 提供交易前风控检查 | `datetime.utcnow()` 非时区安全，且分层插件接口不足 | 改为 timezone-aware UTC，抽象 Pre/In/Post 插件接口 |
| `trader/api/routes/killswitch.py` | 提供 KillSwitch 查询/设置 | 与风控执行路径联动约束未明确 | 定义 kill level 到执行动作映射并测试 |

---

## 5. Adapter可靠性

### 5.1 规范态
- Public/Private stream 必须隔离。
- 重连后必须经过 Alignment Gate（未对齐不外发）。
- 429/网络抖动必须触发受控退避，不允许 API 风暴。
- 静默断流必须依赖 recv/pong timeout，不依赖 TCP keepalive。

### 5.2 落地态
- Binance 适配层具备 backoff、rate limit、对齐与降级级联模块。
- 仍需继续加强“对齐前不外发”与异常路径的一致性验证。

### 5.3 代码映射表
| 代码位置 | 现状 | 缺口 | 改造点 |
| --- | --- | --- | --- |
| `trader/adapters/binance/connector.py` | 已按 testnet/mainnet 分配 `base_url/rest_url` | 配置冻结与校验规则未集中 | 增加配置 schema 校验与启动自检 |
| `trader/adapters/binance/private_stream.py` | `trade_id` 容错解析已实现 | 异常输入观测指标不足 | 增加 parse failure 计数与采样日志 |
| `trader/adapters/binance/rest_alignment.py` | 具备对齐逻辑 | 对齐阶段缓存与释放语义需进一步固化 | 对齐 Gate 增加状态机断言测试 |
| `trader/adapters/binance/rate_limit.py` | 有限流退避实现 | Retry-After 全链路下限保障需持续验证 | 在回归集中加入强制断言 |
| `trader/adapters/binance/degraded_cascade.py` | 已有 dedup/cooldown/local killswitch 机制 | 与控制面错误码协商契约未显式冻结 | 补齐错误码契约表及回归测试 |

---

## 6. Control闭环

### 6.1 规范态
- 最小闭环必须可达：`/v1/risk/events` -> `/v1/killswitch`。
- `dedup_key` 幂等必须稳定，返回码语义固定（201/409）。
- 控制面不可达时，本地必须触发 Fail-Closed 行为。

### 6.2 落地态
- 风险事件上报接口和 KillSwitch 接口已落地。
- 当前控制面服务层耦合较高，缺少持久化队列和事件回放联动。

### 6.3 代码映射表
| 代码位置 | 现状 | 缺口 | 改造点 |
| --- | --- | --- | --- |
| `trader/api/routes/risk.py` | 已支持 `POST /v1/risk/events` 幂等返回 201/409 | 缺统一错误码与重试策略文档 | 增补 API 错误码与重试契约 |
| `trader/api/routes/killswitch.py` | `GET/POST /v1/killswitch` 已有 | 与核心执行层联动闭环验证不足 | 增加端到端级联测试 |
| `trader/services/__init__.py` | 业务服务可用 | 单文件过大，职责混杂 | 按域拆分 service 模块 |
| `trader/storage/in_memory.py` | 风险事件 dedup 入库已实现 | 无持久化与重启恢复 | 对接 Postgres 事件表 |

---

## 7. 存储与回放

### 7.1 规范态
- 事件日志（append-only）为第一性数据。
- 快照用于恢复加速，恢复流程必须可验证。
- 读模型由投影构建，要求幂等投影。

### 7.2 落地态
- 内存事件存储与查询路径可用，适合开发测试。
- 生产级 PostgreSQL 事件溯源尚未落地。

### 7.3 代码映射表
| 代码位置 | 现状 | 缺口 | 改造点 |
| --- | --- | --- | --- |
| `trader/adapters/persistence/memory/event_store.py` | 提供内存 append/query | 重启丢失，不满足生产审计 | 增加 Postgres EventStore 实现 |
| `trader/storage/in_memory.py` | 控制面数据可读写 | 与核心事件存储抽象重复 | 统一为单一存储抽象层 |
| `trader/api/routes/events.py` | 已提供事件查询接口 | 缺 snapshot+replay 严格一致性验证 | 增加恢复一致性测试 |

---

## 8. 测试门禁

### 8.1 规范态
- 全离线、可重复、确定性执行。
- Hard Properties 必须作用于真实模块，不允许用 mock 替代核心行为。
- 关键链路（断流、重连、限流、对齐、级联）必须有 CI 阻断用例。

### 8.2 落地态
- 回归集覆盖了连接器、私有流、确定性层、级联、接口等关键路径。
- 缺少“已知 warning 豁免清单”和“时区改造完成前的严格门禁声明”。

### 8.3 Non-Negotiable Gate（CI 阻断清单）
- [ ] Hard Properties 全绿：`trader/tests/test_hard_properties.py`
- [ ] Fail-Closed 验证通过：控制面不可达时触发本地锁死
- [ ] 本地锁死状态可观测：`local_killswitch_active` 状态可查询/可断言
- [ ] 幂等保证通过：`dedup_key` 与 execution dedup 无重复副作用
- [ ] 确定性层无锁死：核心测试不得出现超时
- [ ] 重连后 Alignment Gate 生效：对齐完成前不外发

### 8.4 代码映射表
| 代码位置 | 现状 | 缺口 | 改造点 |
| --- | --- | --- | --- |
| `trader/tests/test_hard_properties.py` | 核心硬属性已覆盖 | 个别行为与实现细节耦合高 | 抽取通用断言夹具 |
| `trader/tests/test_deterministic_layer.py` | 覆盖重置与幂等关键逻辑 | 复杂时序场景仍可扩展 | 增补更强乱序/重复场景 |
| `trader/tests/test_binance_*` | 连接器、流、限流、级联均有测试 | 缺端到端恢复一致性套件 | 增加 snapshot+replay 与闭环联测 |

---

## 9. 部署运维

### 9.1 规范态
- 首发部署为单机 Docker Compose：`trader-core`、`control-api`、`postgres`、`redis(可选)`。
- 运行时必须输出结构化日志和关键指标（延迟、重连次数、级联次数、对齐耗时）。
- 发布要求具备最小 runbook（故障处置步骤 + 回滚策略）。

### 9.2 落地态
- 当前仓库暂无 Compose 拓扑文件与完整运维脚本。
- API 进程可运行，但生产级监控告警方案未落地。

### 9.3 代码映射表
| 代码位置 | 现状 | 缺口 | 改造点 |
| --- | --- | --- | --- |
| `trader/api/main.py` | API 入口可运行 | 部署编排缺失 | 新增 `docker-compose.yml` 与启动说明 |
| `trader/requirements.txt` | 依赖清单存在 | 缺分环境依赖分层 | 拆分 dev/prod 依赖管理 |
| `pyproject.toml` | 项目元数据存在 | 无运维流程约束 | 增加任务脚本（lint/test/run）和发布检查 |

---

## 10. 路线图

### 10.1 规范态
- P0：先清理阻塞缺陷，确保上线闭环和可靠性。
- P1：完成存储收敛与可回放能力。
- P2：并行推进策略研发能力与运维可观测。

### 10.2 落地态
- P0 中大部分缺陷已修复并进入回归阶段。
- P1/P2 仍以架构收敛、持久化、策略平台化为主线。

### 10.3 代码映射表
| 路线阶段 | 现状 | 缺口 | 改造点 |
| --- | --- | --- | --- |
| P0 | 连接器/私有流/确定性层/风险事件入口已具备 | 需以 CI Gate 形式固化验收 | 在 CI 中锁定 P0 回归集 |
| P1 | 内存存储可用 | Postgres 事件溯源未落地 | 新建 `trader/adapters/persistence/postgres/` |
| P2 | 策略与回测 API 结构已在 | 注册中心、Runner、实验追踪未收敛 | 分阶段补齐策略研发平台 |

### 10.4 Spec Changelog（强制维护）

| 日期 | 版本 | 变更摘要 | 关联 ADR | 变更人 |
| --- | --- | --- | --- | --- |
| 2026-02-28 | v2.0.0 | 主文档重写为“规范态+落地态”双态结构；固定 10 章；新增 Gate/接口冻结表 | ADR-0001（待补） | Codex |

Spec Changelog 维护规则：
1. 任何跨平面契约变更（事件字段、状态机、API 语义）必须新增 ADR（建议路径：`docs/adr/ADR-XXXX-*.md`）。
2. 本文件版本号与 Changelog 必须同步更新，禁止只改代码不改规范。
3. 未附 ADR 的架构变更不得合并到主分支。

