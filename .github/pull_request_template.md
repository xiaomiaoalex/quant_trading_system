## Task Reference
- [ ] Task ID: <!-- e.g., 1.3 -->
- [ ] Phase: <!-- e.g., Phase 1 闭环安全层 -->
- [ ] Branch: `feature/task-{id}/{short-description}`

## Summary
<!-- 简短描述本次变更 -->

## Changes
<!-- 详细说明变更内容 -->

## Type
- [ ] Feature (新功能)
- [ ] Bugfix (错误修复)
- [ ] Refactor (重构)
- [ ] Test (测试)
- [ ] Docs (文档)
- [ ] Chore (杂项)

## CI Evidence
<!-- 链接到本次 PR 的 CI Required run；不要只写“本地通过”。 -->

- CI Required run:
- Test manifest report (`reports/test-manifest.json`):
- P0 JUnit / offline JUnit / Postgres JUnit:
- Postgres skipped count: <!-- 必须为 0 -->
- Coverage gate (`reports/coverage-gate.json`):
- Frontend lint / typecheck / `test:coverage` / build:

```
# 实际运行的命令与关键输出（含退出码）
```

## Release Binding
<!-- 仅发布/部署相关变更填写；普通 PR 填“不适用”。 -->

- Commit:
- Image (`repo@sha256:...`):
- Release manifest:
- SBOM / scan artifacts:
- 已执行的检查：<!-- 本地构建 / 隔离 Compose / dry-run -->
- **未执行**的检查：<!-- 远端发布 / 真实 Demo / 24h 观察；不得省略 -->

> CI 绿灯 ≠ 镜像已发布 ≠ Demo 已验收。历史 Demo/24h 证据不自动适用于本 commit/digest。

## Risks
- [ ] 无新增风险
- [ ] 已知风险：<!-- 描述，包含对 Demo/账务/风控的影响 -->

## Rollback Plan
- Previous image digest:
- Schema compatibility evidence:
- 是否 expand-only / 旧 runtime 是否兼容：
- 回滚步骤与验证（REST 对账、不重复发送、账务守恒）：
- 明确不做的操作：<!-- 不删事件、不改余额、不撤销历史、不对既有 PG/卷做迁移演练 -->

## Checklist
- [ ] 代码遵循项目编码规范
- [ ] 新增测试已在 `ci/test_manifest.json` 唯一分类
- [ ] 单测覆盖新增代码；关键负向路径有失败复现
- [ ] P0 回归测试通过；无 skip/xfail/删断言放行
- [ ] Postgres 必需测试真实运行且 skipped=0
- [ ] 普通单测/P0 未访问公网
- [ ] 文档/契约/架构已同步（如需要）
- [ ] 未提交 `.env`、真实凭据、本地 artifacts 或秘密值
- [ ] 未修改基础阶段状态或历史验收事实
