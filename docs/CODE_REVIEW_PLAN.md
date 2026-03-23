# Code Review 计划 — quant_trading_system v3.3.0

> 基于经验：代码从完成到可达控程度需要 5-6 轮 review。本计划定义系统性 review 策略，确保每轮 review 有明确目标、覆盖范围和验收标准。

---

## 一、Review 层次与职责划分

### 1.1 三层 Review 结构

```
┌─────────────────────────────────────────────────────────┐
│  L1: 架构合规 Review（Architecture Compliance）          │
│  目标：确保代码符合五平面架构约束                         │
│  频率：每 PR 必做，单次 30-60min                       │
│  重点：Core Plane 禁止 IO、Adapter 边界、平面依赖方向    │
├─────────────────────────────────────────────────────────┤
│  L2: 核心模块 Review（Core Correctness）                │
│  目标：状态机单调性、幂等性、Fail-Closed 保障            │
│  频率：每周一次深度 review，单次 2-4h                   │
│  重点：OMS、Risk Engine、确定性层、Reconciler          │
├─────────────────────────────────────────────────────────┤
│  L3: 系统集成 Review（Integration & Edge Cases）        │
│  目标：跨模块协作、边界条件、错误路径、极端场景           │
│  频率：每两周一次，单次 3-4h                            │
│  重点：Adapter 与 Core 交互、PG 持久化、KillSwitch     │
└─────────────────────────────────────────────────────────┘
```

### 1.2 Review 优先级矩阵

| 优先级 | 模块 | 风险等级 | Review 深度 | 轮次预估 |
|--------|------|----------|-------------|----------|
| P0 | OMS 状态机、幂等 CAS | 致命 | 最深 | 6轮 |
| P0 | Risk Engine 决策链路 | 致命 | 最深 | 6轮 |
| P0 | 确定性层 (deterministic_layer) | 致命 | 最深 | 5轮 |
| P1 | Reconciler 漂移检测 | 高 | 深 | 5轮 |
| P1 | Binance Adapter (Alignment Gate) | 高 | 深 | 5轮 |
| P1 | KillSwitch 升级/降级逻辑 | 高 | 深 | 4轮 |
| P2 | Feature Store 版本管理 | 中 | 中 | 4轮 |
| P2 | Event Store 幂等 append | 中 | 中 | 4轮 |
| P2 | Depth Checker 滑点估算 | 中 | 中 | 3轮 |
| P3 | Monitor Service 指标采集 | 低 | 基础 | 3轮 |
| P3 | API Routes 参数校验 | 低 | 基础 | 3轮 |

---

## 二、每轮 Review 的目标定义

### Round 1: 架构合规检查（Architecture Compliance）

**目标**：快速发现明显违反五平面架构的代码

**检查点**：
- [ ] `core/` 下是否有 IO、网络、DB、环境变量读取
- [ ] `core/` 下是否有 `time.sleep`、`asyncio.sleep`、线程阻塞
- [ ] `adapters/` 是否直接修改 Core 状态
- [ ] 依赖方向是否正确（Core → Adapter 禁止）
- [ ] 类型注解是否完整

**通过标准**：
- 零架构违规
- 所有函数签名有类型注解

**预计问题数**：5-15 个（首轮）

---

### Round 2: 核心逻辑正确性（Core Logic Correctness）

**目标**：验证状态机、幂等性、幂等边界条件

**检查点**：
- [ ] 订单状态转换是否单调（无回退）
- [ ] `cl_ord_id` + `exec_id` 去重逻辑
- [ ] CAS 操作原子性
- [ ] 并发锁粒度（256 分片锁）
- [ ] TTL 去重是否正确清理

**通过标准**：
- 所有状态转换路径可追溯
- 幂等边界条件有单测覆盖

**预计问题数**：3-10 个

---

### Round 3: 错误处理与降级（Error Handling & Degradation）

**目标**：确保 Fail-Closed 和优雅降级

**检查点**：
- [ ] 无裸 `except: pass`
- [ ] 所有异常有明确处理路径
- [ ] PG 不可用时 fallback 到内存
- [ ] Adapter DEGRADED_MODE 正确触发 KillSwitch
- [ ] 重连后 REST Alignment 正确执行

**通过标准**：
- 每个异常路径可解释
- 降级行为有日志可追溯

**预计问题数**：3-8 个

---

### Round 4: 边界条件与极端场景（Edge Cases & Extremes）

**目标**：发现隐藏的边界问题

**检查点**：
- [ ] 时间窗口边界（PRIME/OFF_PEAK/RESTRICTED 切换）
- [ ] 宽限窗口（60s 新订单不触发漂移）
- [ ] 空 orderbook 档位处理
- [ ] 滑点估算超阈值拒绝
- [ ] Funding rate 采集周期（8h 间隔）
- [ ] 并发下单同一 `cl_ord_id`
- [ ] 重连后乱序消息处理

**通过标准**：
- 所有边界有单测覆盖
- 边界行为有文档说明

**预计问题数**：5-12 个

---

### Round 5: 性能与资源泄漏（Performance & Resource Leaks）

**目标**：确保无资源泄漏和性能问题

**检查点**：
- [ ] 锁竞争是否合理（无全局锁）
- [ ] 内存是否有泄漏（事件累积未清理）
- [ ] 连接池是否正确释放
- [ ] 定时任务是否正确取消
- [ ] 256 分片锁的锁粒度是否合理

**通过标准**：
- 长时间运行无内存增长
- 锁等待时间 < 10ms P99

**预计问题数**：2-6 个

---

### Round 6: 集成与系统测试（Integration & System Testing）

**目标**：端到端验证和回归防护

**检查点**：
- [ ] CI 门禁全部通过
- [ ] P0 回归测试不回归
- [ ] postgres-integration 测试通过
- [ ] e2e 场景覆盖核心闭环
- [ ] 失败回退路径验证

**通过标准**：
- 全量测试通过
- 性能基线不下降

**预计问题数**：0-3 个

---

## 三、模块化 Review 清单

### 3.1 Core Plane (`core/`)

**禁止模式**：
```
❌ import requests, httpx, aiohttp
❌ import psycopg2, asyncpg
❌ import redis, memcache
❌ os.getenv, os.environ
❌ time.sleep, asyncio.sleep
❌ open(), Path().read_text()
```

**必须存在**：
```
✅ 类型注解（函数签名 + 类属性）
✅ 结构化日志（含 trace_id）
✅ 明确的异常类型（非裸 Exception）
✅ 状态转换前置条件检查
```

### 3.2 Adapter Plane (`adapters/`)

**检查点**：
- [ ] WS 消息是否标准化为 canonical events
- [ ] REST 纠偏是否在 WS Alignment 之前执行
- [ ] 限流退避策略是否可配置
- [ ] 网络异常是否带降级标签
- [ ] 消息是否携带 `local_receive_ts_ms`、`exchange_event_ts_ms`、`seq`

### 3.3 Persistence Plane (`adapters/persistence/`)

**检查点**：
- [ ] 幂等 append（相同 stream_key+seq 不重复插入）
- [ ] PG fallback to 内存
- [ ] 连接池管理
- [ ] 事务边界清晰
- [ ] 快照恢复路径

### 3.4 API Layer (`api/`)

**检查点**：
- [ ] 参数校验（pydantic schema）
- [ ] 错误响应格式统一
- [ ] 无业务逻辑（仅路由）
- [ ] 审计日志完整

---

## 四、Review 执行计划

### Phase 0: 代码冻结与自检（1天）

**执行人**：代码作者

1. 运行 `python scripts/check_core_no_io.py` 确认 Core 无 IO
2. 运行 `python -m pytest trader/tests -v` 确认全量测试通过
3. 运行 `black --check --diff . && isort --check --diff .` 确认格式
4. 自检 Review 清单 Round 1-2

**交付物**：自检报告 + PR

---

### Phase 1: L1 架构合规 Review（1-2天）

**执行人**：Reviewer

1. 逐文件扫描 `core/` 目录
2. 检查 `adapters/` 到 `core/` 的依赖方向
3. 验证类型注解覆盖率

**输出**：架构合规报告（Round 1 问题列表）

---

### Phase 2: L2 核心模块 Review（2-3天）

**执行人**：Reviewer

1. OMS 状态机深度检查（Round 2-3）
2. Risk Engine 决策链路（Round 2-3）
3. 确定性层验证（Round 2-3）

**输出**：核心逻辑报告（Round 2-3 问题列表）

---

### Phase 3: L3 集成与边界 Review（2-3天）

**执行人**：Reviewer

1. Adapter 与 Core 交互边界（Round 4）
2. PG 持久化集成（Round 4-5）
3. KillSwitch 全链路（Round 4-5）

**输出**：集成测试计划 + 问题列表

---

### Phase 4: 最终验收（1天）

**执行人**：Reviewer + Author

1. 确认所有问题已修复
2. 运行全量 CI 门禁
3. 生成最终 Review 报告

---

## 五、Review 问题追踪模板

```markdown
## Review Round {N} — {模块名}

### 问题列表

| # | 严重性 | 文件:行号 | 问题描述 | 建议修复 | 状态 |
|---|--------|-----------|----------|----------|------|
| 1 | P0 | `core/oms.py:123` | 状态转换缺少 CAS | 添加 `_apply_with_cas()` | OPEN |
| 2 | P1 | `adapters/...` | ... | ... | ... |

### 通过检查点

- [ ] 检查点 1
- [ ] 检查点 2

### 下轮 Review 重点

- ...
```

---

## 六、预计 Review 周期

| 模块 | 预计轮次 | 预计问题数 | 预计时间 |
|------|----------|------------|----------|
| OMS | 5-6 轮 | 15-25 个 | 2 周 |
| Risk Engine | 5-6 轮 | 12-20 个 | 2 周 |
| 确定性层 | 4-5 轮 | 8-15 个 | 1.5 周 |
| Reconciler | 4-5 轮 | 10-18 个 | 1.5 周 |
| Binance Adapter | 4-5 轮 | 12-18 个 | 1.5 周 |
| Feature Store | 3-4 轮 | 6-12 个 | 1 周 |
| Event Store | 3-4 轮 | 6-12 个 | 1 周 |
| API Routes | 2-3 轮 | 5-10 个 | 0.5 周 |

**总预计时间**：8-10 周（全部模块）

---

## 七、立即行动项

### 当前最紧急（已完成模块的 Review）

根据 PLAN.md，以下模块已完成开发：

1. **确定性层** (`core/application/deterministic_layer.py`) — Review Round 1 预备
2. **OMS** (`core/application/oms.py`) — Review Round 1 预备
3. **风险引擎** (`core/application/risk_engine.py`) — Review Round 1 预备
4. **Binance 适配器栈** (`adapters/binance/`) — Review Round 1 预备
5. **KillSwitch** (`services/killswitch.py`) — Review Round 1 预备
6. **PG 风险持久化** (`adapters/persistence/risk_repository.py`) — Review Round 1 预备

### Review 启动顺序

```
deterministic_layer.py → oms.py → risk_engine.py 
     → killswitch.py → risk_repository.py → binance/
```

---

## 八、成功标准

Review 完成的标志：

1. **L1 问题数**：0 个
2. **L2 问题数**：每模块 ≤ 3 个
3. **L3 问题数**：每模块 ≤ 5 个
4. **CI 门禁**：4 阶段全部通过
5. **测试覆盖率**：核心模块 ≥ 90%

---

*本文档应随项目进展更新，每完成一个模块的 Review 后记录结果。*
