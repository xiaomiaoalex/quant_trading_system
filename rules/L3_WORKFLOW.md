# L3 工作流/SOP（手动触发）
# 版本: v1.0.0
# 复杂任务或明确要求时手动触发

---

## TDD 开发流程（防止 AI 幻觉）

### 流程步骤

1. **Red**：先检索现有接口、类型和调用点，再编写失败测试
   - 测试必须失败在目标行为上
   - 不是 import error、拼写错误或虚构接口

2. **Green**：只写让测试通过的最小实现
   - 优先复用既有模块和契约
   - 不新增无证据抽象

3. **Refactor**：测试通过后再做小步重构
   - 保持核心不变性和接口契约不变

4. **No Hallucinated API**：
   - 如果测试需要的新函数、字段或 DTO 尚不存在
   - 必须先更新 `docs/INTERFACE_CONTRACTS.md` 并说明兼容策略
   - 再修改测试和实现

5. **Verification**：
   - 至少运行新增/修改测试
   - 涉及共享核心逻辑时，继续运行相关回归或 P0 回归集

### 适用条件
- 涉及代码行为变更时
- 新增核心功能时
- 修改状态机逻辑时

## Spec RFC 流程（复杂需求）

当任务涉及以下场景时，必须执行 Spec RFC 流程：

| 场景 | 是否使用 |
|------|---------|
| 跨模块重构 | ✅ |
| 新增外部依赖 | ✅ |
| 多服务交互变更 | ✅ |
| 核心领域模型变更 | ✅ |
| 简单 Bug 修复 | ❌ |
| 单文件功能添加 | ❌ |

### 五阶段流程

1. **需求启发**：主动反问澄清，深度挖掘涉众诉求
2. **需求分析**：整理最终需求，按类型分类
3. **需求定义**：撰写需求说明书（10维度）
4. **技术设计**：现状分析、目标状态、设计选项、详细设计
5. **验证**：8个质量标准检查

**用户确认RFC后，才进入开发阶段。**

## 文档更新流程（开发后必须执行）

完成任务后，必须按以下顺序更新文档：

### 1. 更新 `docs/PROJECT_ARCHITECTURE.md`
- 影响层级边界、模块职责、跨层调用、主数据流、持久化路径、风控闭环、部署/运行拓扑时

### 2. 更新 `docs/INTERFACE_CONTRACTS.md`
- 涉及函数签名、DTO、事件 Schema、API 字段、跨层调用或命名重构时

### 3. 更新 `PROJECT_STATUS.md`
- 记录开发前后状态对比
- 将 Issue 从"待确认"移至"已验证"
- 更新最后操作时间戳

### 4. 更新 `docs/EXPERIENCE_SUMMARY.md`
- 踩坑记录、设计模式、可复用经验

### 5. 更新 `DEVELOPMENT_LOG.md`
- 追加本次开发记录，说明背景、决策、改动、验证、风险/遗留
- 只追加，不重写历史

### 6. 保持计划文档新鲜
- 涉及排期、阶段切换、优先级重排时，同步更新 `PLAN.md`

## 测试执行

### 单元测试（必须覆盖）
- 状态机：每个状态迁移路径
- 边界输入：空值、零值、最大值、负数、重复 ID、乱序序列号
- 错误路径：异常抛出、Fail-Closed 行为、降级触发条件
- 幂等性：同一操作两次结果一致
- 并发安全：hashed lock 分区、CAS 竞争

### 集成测试（必须覆盖）
- 持久化层：PG event_store 幂等 append、乱序读取、快照恢复
- 适配器：REST Alignment 重连后恢复 OMS 状态
- 风险仓储：PG 写入、读取、并发幂等

### E2E 测试（必须覆盖核心闭环）
- 正常闭环：下单 → 成交回报 → OMS FILLED → Position 更新 → 事件写入
- 失败回退：DEGRADED_MODE → KillSwitch L1 → 新开仓拒绝
- 重连恢复：WS 断线 → 重连 → REST Alignment → OMS 与交易所一致

## CI Gate Order

顺序执行：`import-gate` → `p0-gate` → `control-gate` → `postgres-integration`。全部通过方可合并。

## Git 工作流

### 分支命名规范
```
feature/task-{id}/{short-description}
bugfix/task-{id}/{short-description}
hotfix/task-{id}/{short-description}
docs/task-{id}/{short-description}
```

### Commit 规范（Conventional Commits）
```
{type}(task-{id}): {description}

# type: feat | fix | refactor | test | docs | chore | perf | ci
```

### PR 必填项
- Task ID（关联 PLAN.md 中的任务）
- 变更说明
- 测试结果
- 风险评估
- Rollback 方案

---

**适用场景**：复杂任务、手动指定、执行 TDD/Spec RFC 时
**生效方式**：手动触发
**维护者**：Tech Lead
**版本**：v1.0.0