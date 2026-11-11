# GitHub Ruleset 迁移说明（未执行）

状态：**已准备迁移文件，未修改远端规则**。当前远端事实（2026-11-04 只读查询）：
ruleset <https://github.com/xiaomiaoalex/quant_trading_system/rules/13369928> 只要求
`Import Gate`、`P0 Regression Tests`、`Control Gate` 三个状态检查，要求 0 个审批，
未启用 strict up-to-date checks。该事实不得写成已修复。

## 目标

`deploy/github-ruleset.ci-required.json` 定义 `main` 的目标规则：

- 唯一必需状态检查为 `CI Required`（该 Job 汇总所有 PR 必需 Job；失败、跳过、取消均
  不能汇总为成功）；
- `strict_required_status_checks_policy=true`：分支必须与 `main` 同步后才可合并；
- `required_approving_review_count=1`，并要求最后推送批准、过期审查 dismissed、
  评论解决；GitHub 自身禁止作者批准自己的 PR，因此满足“至少一名非作者审核”；
- 禁止删除分支和 force-push。

`ops`/`external` 分组不在必需检查内，也不得通过把可选 Job 加入 `CI Required` 来伪装
覆盖；它们的结果单独记录。

## 操作步骤（需仓库管理员手动执行）

1. 确认 `CI Required` 已在至少一次 PR 运行中出现并成功，且当前 ruleset 中的三个旧
   Job 仍可作为过渡检查。
2. 用只读命令核对当前规则：

   ```bash
   gh api repos/xiaomiaoalex/quant_trading_system/rulesets
   gh api repos/xiaomiaoalex/quant_trading_system/rulesets/13369928
   ```

3. 先创建新 ruleset（不要直接删除旧规则）：

   ```bash
   gh api --method POST \
     repos/xiaomiaoalex/quant_trading_system/rulesets \
     --input deploy/github-ruleset.ci-required.json
   ```

4. 在至少一个测试 PR 上确认 `CI Required` 出现且必需检查阻断生效，再禁用旧的
   `13369928` 规则。禁用前保留导出：

   ```bash
   gh api repos/xiaomiaoalex/quant_trading_system/rulesets/13369928 \
     > /tmp/ruleset-13369928.before.json
   ```

5. 若 `CI Required` 因 GitHub Actions 权限、fork PR 或托管 runner 不可用而无法稳定
   出现，**停止迁移**，保留旧规则并记录未完成项；不得用“稍后补上”或放宽审批绕过。

## 回滚

- 重新启用旧 ruleset `13369928`；
- 删除新 ruleset（需管理员权限）；
- 不改动任何代码或工作流文件。

本仓库的 CI/CD 专项没有执行以上远端命令，也没有 `packages: write`、环境审批或
ruleset 管理权限。
