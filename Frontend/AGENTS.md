# Frontend AGENTS.md

AI 助手在前端开发中的核心准则和工作流规范。

---

## 🤖 核心原则

1. **契约优先**: 修改代码前先阅读 `frontend_docs/FRONTEND_CONTRACT.md`
2. **状态追踪**: 任务开始前更新 `frontend_docs/FRONTEND_PROJECT_TRACKER.md`
3. **文档闭环**: 任务完成后立即刷新项目追踪器
4. **Truth Gap 感知**: 阻塞问题标注 `// TODO: BLOCKED BY BACKEND API`
5. **禁止发明 API**: 严禁脑补后端接口，缺失能力必须标注 `// TODO: BLOCKED BY BACKEND API`

---

## 🔢 任务命名规范（与后端保持一致）

前端任务编号沿用后端 `Task X.Y` 风格，不再使用 `P0-1` 这类编号作为主标识。

- 前端主线统一使用 `Task 9.x`
- 优先级仍保留 `P0/P1/P2`，但作为优先级标签而不是任务编号
- 任务文档、追踪器、会议纪要、行动项必须使用同一编号

当前映射（必须一致）：
- `Task 9.1`：统一 API 前缀与文档路径
- `Task 9.2`：Monitor Snapshot 真聚合化
- `Task 9.3`：Reconciler Trigger 无参触发
- `Task 9.4`：Backtests 列表与进度接口
- `Task 9.5`：Reports 详情接口
- `Task 9.6`：Audit 专用查询接口
- `Task 9.7`：Replay 任务状态接口
- `Task 9.8`：`strategies/running` 语义澄清
- `Task 9.9`：Chat / Research 参数风格统一
- `Task 9.10`：Stale/Degraded 统一枚举
- `Task 9.11`：快照历史查询接口

---

## 🛠️ 常用命令

### 工程检查
```bash
# 检查工程是否已初始化
test -f package.json && echo "已初始化" || echo "未初始化"

# 未初始化时先执行
npm init -y
npm install
```

### 环境配置
```bash
# 安装依赖
npm install

# 类型检查
npm run typecheck

# 格式化
npm run format

# Lint 检查
npm run lint
```

### 开发服务器
```bash
# 启动开发服务器
npm run dev

# 构建生产版本
npm run build

# 预览构建
npm run preview
```

### 测试执行
```bash
# 运行单元测试
npm run test

# 运行 E2E 测试
npm run test:e2e

# 测试覆盖报告
npm run test:coverage
```

---

## 📝 开发工作流

### 1. 任务启动
```bash
# 1. 阅读 FRONTEND_PROJECT_TRACKER.md 确认当前任务
# 2. 检查相关 API 契约文档
# 3. 创建 TODO 列表追踪多步骤任务
# 4. 更新项目追踪器状态为 in_progress
```

### 2. 代码开发
```bash
# 组件开发顺序:
# 1. TypeScript 类型定义 (types/*.ts)
# 2. API Client (api/*.ts)
# 3. React Hooks (hooks/*.ts)
# 4. UI 组件 (components/*.tsx)
# 5. 页面 (pages/*.tsx)
```

### 3. 测试覆盖
```bash
# 单元测试必覆盖:
# - 组件渲染 (loading/empty/error/stale)
# - 状态迁移 (blocked/degraded/killed)
# - 危险操作确认 (二次确认弹窗)
# - API 错误处理 (400/404/409/422/500)
```

### 4. 文档更新
```bash
# 任务完成后更新:
# 1. FRONTEND_PROJECT_TRACKER.md - 任务状态、完成时间
# 2. 会议纪要 - 关键决策
# 3. 进度指标 - 燃尽图数据
```

---

## ⚠️ 不变性约束

### 状态表达
- ✅ 必须表达：`loading/empty/error/stale/degraded/blocked`
- ❌ 禁止弱化：风险态、熔断态、漂移态必须明显表达

### 危险操作
- ✅ 必须二次确认：所有 `POST/PUT/DELETE` 操作
- ✅ 失败回滚：optimistic UI 失败后必须回滚

### API 调用
- ✅ 使用 TanStack Query 管理缓存和轮询
- ✅ 使用 Zod 运行时验证契约
- ❌ 禁止直接访问网络，必须通过 API Client

---

## 🧪 测试规范与 DoD

### Definition of Done (DoD)
每个任务必须完成以下检查才能标记为完成：

```bash
# 1. 类型检查
npm run typecheck  # 必须通过

# 2. Lint 检查
npm run lint  # 必须通过

# 3. 单元测试
npm run test  # 覆盖率 >= 80%

# 4. 文档回写
# - 更新 FRONTEND_PROJECT_TRACKER.md
# - 标注 Truth Gap 状态（如适用）
```

### 单元测试
```typescript
// 必须覆盖的场景
- loading/empty/error 状态
- stale/degraded/blocked 状态表达
- 危险操作二次确认
- API 错误处理 (400/404/409/422/500)
- 乐观更新失败回滚
```

### E2E 测试
```typescript
// 关键流程
- Monitor: 查看快照、清除告警
- Strategies: 加载/卸载策略、修改参数
- Reconcile: 触发对账、查看漂移
- Backtests: 创建回测、追踪进度
```

---

## 📋 文档维护清单

### 任务完成后必更新
- [ ] `FRONTEND_PROJECT_TRACKER.md` - 任务状态
- [ ] `FRONTEND_PROJECT_TRACKER.md` - 进度指标
- [ ] `FRONTEND_PROJECT_TRACKER.md` - 会议纪要（如适用）

### Truth Gap 修复后
- [ ] 移动到"已解决 Truth Gap"列表
- [ ] 更新阻塞清单
- [ ] 标注联调验收状态

---

## 🔗 关键文档

| 文档 | 用途 | 优先级 |
|------|------|--------|
| `frontend_docs/FRONTEND_CONTRACT.md` | 数据契约全集 | P0 |
| `frontend_docs/contract_models.md` | 核心模型与枚举 | P0 |
| `frontend_docs/contract_endpoints.md` | API 端点清单 | P0 |
| `frontend_docs/FRONTEND_PROJECT_TRACKER.md` | 项目追踪 | P0 |
| `frontend_docs/truth_gap_priority.md` | Truth Gap 优先级与任务序列（Task 9.x） | P1 |
| `frontend_docs/frontend_delivery_plan.md` | 交付计划 | P1 |

---

## 🎯 行动指南

### 任务执行
1. 阅读 `FRONTEND_PROJECT_TRACKER.md` 确认当前任务
2. 检查相关 API 契约文档
3. 创建 TODO 列表
4. 开始开发

### 本周重点
- 以 `FRONTEND_PROJECT_TRACKER.md` 当前状态为准
- 联调时间请关注项目追踪器更新

---

**最后更新**: 2026-04-10  
**维护人**: AI Agent（`contract-doc` 责任域）
