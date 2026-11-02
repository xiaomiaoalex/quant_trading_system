# 基础重构：工程师、独立测试员与只读审核员工作流

版本：3；生效日期：2026-11-02。用户已授权增加独立测试员、继续阶段式开发，
并要求减少高成本父 Agent 的日常调用。版本 2 的历史报告与计数原样保留，不伪称当时已采用版本 3。
业务范围和阶段验收沿用 [阶段计划](FOUNDATION_REFACTOR_PHASE0.md)，不另立一套业务规格。
父 Agent 维护 [状态文件](FOUNDATION_WORKFLOW_STATE.json) 的阶段边界、升级事件与最终交付；
工程师在该阶段 artifacts 目录维护日常交接和同一问题处理次数，不逐次调用父 Agent 更新状态。

## 1. 官方接口与配置边界

本次已读取 Paseo 官方文档索引及以下 Markdown 页面：

- [文档索引](https://paseo.sh/llms.txt)
- [Orchestration](https://paseo.sh/docs/orchestration.md)
- [Common orchestration workflows](https://paseo.sh/docs/orchestration-workflows.md)
- [Agent profiles](https://paseo.sh/docs/agent-profiles.md)
- [MCP reference](https://paseo.sh/docs/mcp.md)

采用现有 MCP 能力：`list_hosts`、`list_profiles`、provider discovery、`create_agent`、
`send_agent_prompt` 和完成/错误/权限通知。每次新建会话先读取当前 profiles 的 notes，
将 provider/model/mode/thinking/features 实际填入创建参数；不存在可用 profile 时先发现配置。
`workspaceId` 决定位置，父子关系决定通知归属，二者不可混淆。

父 Agent 每阶段只创建或唤醒工程师，并给出完整工作单、范围和父 Agent 返回地址；
创建/跟进工程师使用 `notifyOnFinish=false`，不把中间回合结束当作阶段完成通知。
工程师在同一 workspace 创建或唤醒阶段内测试员、每轮新审核员，使用 `notifyOnFinish=true`
使通知返回工程师；显式报告也发给工程师。不得让日常完成通知落到父 Agent。
父 Agent 名下遗留的测试员/审核员不用于新日常闭环；需要继续独立验证时，由工程师创建新会话。
不增加 cron/heartbeat、后台调度器或插件，不轮询、不 detach。工程师负责实际调用下一步工具；
本文和 JSON 本身不是后台执行服务。工程师停止、通知缺失或权限阻塞时不能假定任务仍在推进。

本主机已保存“架构师”“工程师”“审核员（DeepSeek）”和“测试员”四个 profiles。
测试员 profile ID 为 `agent_profile_tester_deepseek_harness`，provider 为
`deepseek-harness`（界面名称 DeepSeek Harness），model 为
`["magpie","workbuddy/auto"]`（WorkBuddy / Auto），采用 `thinkingOptionId=high`、
`features.auto_accept=false`。该 provider 无 mode，创建时不传 `modeId`；
模型名 Auto 不代表自动批准权限。
测试员渠道依据用户明确指定及现有审核员的实际设置选择，并经 `inspect_provider` 核验。
以后派工仍先读取当时的“测试员”profile 与 notes，不猜模型、不自动切换到官方付费渠道。
保存 profile 与启动会话是两件事；修改 profile 后，已有会话必须显式更新运行设置。
初次误选 `["deepseek-official","deepseek-v4-pro"]` 的经过及修正保留在第7.4节。
不同 provider 不构成正确性保证，独立性来自职责、测试依据和真实验证。

## 2. 角色与文件所有权

| 角色 | 责任 | 可写范围 | 不可做 |
|---|---|---|---|
| 父 Agent | 阶段工作单、三轮未解升级分析、最终验收核验、提交 | 工作流规范/状态/阶段交付记录；通过后 Git 暂存与本地提交 | 接收或转发日常测试/审核；替代独立验收；并发修改实现 |
| 工程师 | 实现与缺陷修复；阶段内串行派工、证据整合、问题计数 | 已授权阶段实现、基础测试、受影响文档/CI、阶段 artifacts | 自测放行；弱化独立验收；修改业务契约裁决；提交或越权升级权限 |
| 测试员 | 从契约独立设计反例、补测试、验证副作用/故障/真实存储 | 本阶段独立测试、专用 fixtures、测试设计/报告；CI 中只追加这些测试 | 修生产代码；改业务契约、既有测试断言或全局 fixtures；删除/跳过门禁；向父 Agent 发送日常结果 |
| 审核员 | 只读审查实现、双方测试、文档与原始证据 | 无仓库写入；允许隔离测试产生忽略缓存和临时 schema | 代写实现或测试；批准未运行检查；向父 Agent 发送日常结果；提交或进入下一阶段 |

单一写入权针对实现、测试和受审文档。工程师串行转移写入权，可写本阶段交接 artifacts，
不能写父 Agent 的阶段状态文件；最终审核必须披露这些 artifacts 和规范/状态文件。
这是协作约束，不是按角色强制隔离文件的操作系统安全边界；宿主 sandbox/approval 仍然生效。

测试员阶段 2 专属输出：

- `trader/tests/test_phase2_independent_acceptance.py`
- `trader/tests/test_phase2_independent_postgres.py`
- `docs/PHASE2_INDEPENDENT_TEST_REPORT.md`（先写设计，再追加验证与限制）
- `artifacts/foundation/phase2/` 下的原始输出、JUnit 与快照（不提交原始临时产物）
- `.github/workflows/ci-gate.yml`：只向既有适当 job 追加两份独立测试，不改其他门禁。

新增 fixtures 优先放在上述专属测试文件中。确需超出原工作单的必要文件时，工程师先暂停该路径；
若属于不改变验收的常规实现选择，可在阶段工作单预授权范围内处理并披露，否则升级父 Agent。
单元测试不联网；PG 只使用唯一临时 schema，禁止碰默认 schema、现有运行时或账户。
已有后台 Control/Execution 进程不是本次流程启动，不能擅自停止或借它们验证。

### 2.1 子代理命令执行受限时

子代理的命令工具不可用时，先冻结并报告原始错误、文件变更和自建任务状态。
不得自动修复 ACL、扩大访问权限或改用不受原限制的终端。
工程师可以在自身现有受控环境中执行已授权、范围明确的验证命令；
这不授权重做被拒绝的权限修改，也不取消该环境原有的审批。

测试员仍独立设计并编写测试，用已有文件工具编辑白名单；若文件工具也受限，
返回完整 patch 供工程师检查并原样应用，不自行扩大写入方式。
用例准备完成后先冻结，发送 `kind=execution_request` 的中间报告，
列明命令、环境需求、预期产物及源文件清单；尚未执行时不出具验收通过结论。
工程师检查命令及测试的副作用后执行，保留完整输出、退出码、JUnit和执行时文件指纹，
再交测试员分析。报告必须区分 `authored_by`、`executed_by`、`analyzed_by`，
不得把工程师代跑写成测试员亲自运行。最终只读审核同时检查此执行协作和原始证据，
可向工程师请求额外的受控复验；原阶段验收与三轮升级门禁保持不变。
只有工程师也没有可用执行通道时，才请求父 Agent 提供一次受控代跑，不给父 Agent 转发日常 verdict。

测试员自有文件的格式检查必须使用 `black --check --diff` 保留 Black 自身的 diff；
不能用两份相同文件的 `git diff` 代替。若检查失败，工程师只回传 diff 和退出码，
由测试员手改自己的文件后复跑；不采用工程师对测试员文件的格式化写入，
以保持作者与修改者边界。

### 2.2 Handoff 路由与触发条件

| 发起节点 | 日常接收节点 | 触发条件 | 父 Agent 是否参与 |
|---|---|---|---|
| 父 Agent | 工程师 | 阶段开始；给出验收、范围、写入权与返回地址 | 一次 |
| 工程师 | 测试员 | 实现/限定修复冻结；需首次独立测试或修复后复验 | 否 |
| 测试员 | 工程师 | 测试通过、发现可复现缺陷、fixture 修正或执行证据判读完成 | 否 |
| 工程师 | 新一轮审核员 | 必需独立测试全通过、指纹冻结；每轮审核使用新会话 | 否 |
| 审核员 | 工程师 | 审核 pass/fail/blocked 及逐项证据完成 | 否 |
| 工程师 | 测试员 | 审核 fail 后的限定修复冻结，先复验再派新审核员 | 否 |
| 工程师 | 父 Agent | 同一 finding 三次修复—独立复验仍未解；或最终审核通过且阶段完整交付 | 仅这两种情况 |

路由示例：

```text
阶段开始：父 Agent -> 工程师
日常循环：工程师 -> 测试员 -> 工程师
                 -> 新审核员 -> 工程师
                      | fail -> 工程师修复 -> 测试员复验 -> 新审核员
同一问题第 3 次修复复验仍失败：工程师 -> 父 Agent
最终测试与审核均通过且交付完整：工程师 -> 父 Agent（仅一次）
```

工程师用 `artifacts/foundation/phase<N>/engineer-issue-ledger.json` 记录稳定的
`finding_id`、根因、`attempt`、每次修复指纹和独立复验结果。一个“处理轮次”是工程师针对同一
finding 交付一次修复并完成独立复验；测试与审核共同构成该轮验证，不分别重置次数。
测试 fixture 修正、环境/权限失败、无代码变化的格式修复不消耗修复轮次。

同一根因的别名、复现症状或换测试文件不创建新计数；换 Agent、模型、会话、报告或阶段内
review round 也不清零。新根因可以另建 finding，但不得把旧根因改名规避升级。
第 1、2 次失败由工程师直接修复并复验；第 3 次修复后仍失败即停止该 finding 的继续尝试，
将三轮差异、原始反例、已排除假设和建议交父 Agent。父 Agent 分析并给出方案后才能继续。
旧的“整阶段三次正式审核即暂停”不再作为版本 3 的全局限制；历史轮次仍保留。

最终交付必须同时满足：独立测试通过、最新只读审核 `overall=pass`、完整变更/指纹/未执行项齐备、
没有 blocking findings、工作区冻结。只有测试通过或审核单独通过都不能调用父 Agent 宣布阶段完成。
父 Agent 最终核验后仍发现实现问题时，注明 finding 返回工程师，阶段尚未完成，不创建提交。

## 3. 通知驱动的状态转换

| 当前状态 | 事件/证据 | 工程师动作；父 Agent 仅在注明时介入 |
|---|---|---|
| `IMPLEMENTING` | 实现、基础测试并确认冻结 | 将写入权交测试员，进入 `TESTING` |
| `TESTING` | 可复现需求失败 | 保留反例；同一 finding 未满三轮则进入 `FIXING`，不通知父 Agent |
| `FIXING` | 限定修复冻结 | 交回测试员复验并刷新指纹，不直接进入审核 |
| `TESTING` | 必需独立项全通过 | 收回测试写入权，创建本轮新只读审核员，进入 `REVIEWING` |
| `REVIEWING` | 任一验收或通用约束失败 | 记录 finding/attempt；直接回到 `FIXING`，不通知父 Agent |
| `REVIEWING` | 同一 finding 三次修复复验后仍失败 | 冻结并一次性升级父 Agent；父给方案前不继续 |
| `REVIEWING` | 最新审核 `overall=pass` 且最终交付条件齐全 | 工程师一次性 handoff 父 Agent，进入 `READY_TO_COMMIT` |
| `READY_TO_COMMIT` | 父 Agent 核验差异、指纹和证据成立 | 父 Agent 创建已授权的本地提交；工程师不参与 |
| `COMMITTING` | 本地提交成功且父 Agent 核验 | 父 Agent 记录 `PHASE_COMPLETE`，再开始下一阶段 |
| 任意状态 | 未知文件变更、需求冲突、权限或证据无法对应 | 冻结；超出工程师授权才升级父 Agent，不伪造通过 |

高影响阶段 2–6 均必须经过独立测试；阶段 0/1 已完成，不追溯重写通过记录。
之后的小范围低影响任务可在派工前明确采用工程师+审核员，不能在测试失败后降级流程。

正式审核一轮必须有 `overall` 与逐项证据；环境故障不计代码失败轮次。
同一 finding 的处理计数不能靠换模型、创建新会话或修改 workflow_version 清零。
独立测试失败与审核失败都进入同一 finding 的处理历史，但同一轮修复的多个验证信号只计一次。

同一报告的显式 agent-message 与 finished 通知可能重复出现。工程师按
`phase + workflow_version + role + agent_id + report_id` 去重，并记录已消费 message ID；
旧版本通知只补历史，不能再次派工、提交或推进阶段。
`finished` 只表示回合结束，不等于测试或审核通过。报告缺字段时只补证据，不自行推断 pass。

权限请求由当前协调者对照用户授权与宿主规则处理，不能一律批准。
拒绝后不换工具规避。工具/网络/会话错误保留为 `BLOCKED`，不吞错和无限重试。

## 4. 必须独立判断的验收

阶段业务编号保持 2.1–2.12 等原编号。每份正式审核另外返回以下通用约束，
不能再出现“业务项全 pass”被误当成 overall pass：

| 编号 | 约束 |
|---|---|
| G1 | 既有有效断言与行为不回归；必需回归套件真实通过 |
| G2 | 独立测试依据来自契约/手算/独立模型，不从被测实现生成期望值 |
| G3 | 必需检查实际运行；PG 无 skip；无删断言、宽泛异常吞错、无故容差或 xfail 放行 |
| G4 | 报告对应同一分支、base、完整修改/新增文件清单与文件内容指纹 |
| G5 | 实现/测试/审核职责和写入权未越界；无未经授权的账户、部署、历史操作 |
| G6 | 接口、架构、迁移说明及 CI 测试入口与实现一致 |

测试员在看工程师的“通过结论”之前先根据契约写测试设计，再读代码定点验证。
允许复用 fake clock/传输接口，但不复用被测计算作为断言的答案。
必须观察返回值和副作用：交易所调用、账户与各 book 的余额/预约/持仓、命令和追加事实。
测试金额和预期变化使用独立的具体数字，守恒与归属隔离分别验证。

阶段 2 独立测试重点：

| 编号 | 必须留下的证据 |
|---|---|
| T1 | A book=100、B book=900、账户=1000；A 需要200时拒绝，B不被借款，无发送及残留预约 |
| T2 | 账户/book双层预约与成交写入的故障原子性；真实PG回滚、重启、并发分配不能超额 |
| T3 | 部分成交、base/quote/第三币种费用、取消/UNKNOWN后的剩余预约与逐资产守恒；不漏记真实成交 |
| T4 | 旧快照、有在途预约、未知归属事实的迁移与解除；不猜历史、不清库、原追加事实保持 |
| T5 | 规格显式性、价格来源、重复/乱序/终态，以及Control至Execution入口的真实行为 |
| T6 | 第1轮C2估值原因码回归已修且其他book/UNKNOWN门禁仍阻断；单跑目标用例及所在套件 |
| T7 | 至少两处关键约束的故障注入/变异证明：人为破坏时独立测试命中业务断言；恢复后正常通过 |
| T8 | 独立测试进入对应CI job；完整命令/计数/skip/环境/指纹，测试设计与实现分开记录 |

T7 在测试内通过限定的注入或隔离临时副本完成，不修改共享生产文件。
测试员发现实现缺陷时保留真实失败测试，不代写修复、不改为 xfail/skip；
报告工程师后冻结，由工程师修复。若期望与契约冲突，由工程师暂停并升级父 Agent 裁决，
不把测试本身当作绝对真理。

## 5. 版本指纹和通过报告

每次交接记录：branch、base_commit、HEAD、`git diff --name-status <base>`、
`git ls-files --others --exclude-standard`（仅枚举路径，剔除 `.workbuddy/`）。
必须显式包含未跟踪的新测试和新模块，不能只提交 `git diff` 输出。
为每个受审源文件计算 SHA-256；为按路径排序的清单计算总摘要，删除文件记为 `null`。
同时记录未修改 tracked 基线由 base_commit 标识；任何未说明的额外 tracked/index 变化阻断交接。

指纹输入是完整阶段修改集，含生产、测试、CI、契约和迁移文档。
防止自引用：`docs/FOUNDATION_WORKFLOW_STATE.json`、测试报告本身和被 Git 忽略的
`artifacts/foundation/` 不放进源文件总摘要，必须在报告中显式列出为运维元数据，
由工程师/审核员单独检查，最终交父 Agent 核验。工作流规范文件本身仍参与指纹。
`.workbuddy/` 不属于任务，既不读取内容、不计算哈希，也不纳入提交。

通过报告最小结构（占位值不能当作真实证据）：

```json
{
  "report_id": "<unique-report-id>",
  "workflow_version": 3,
  "phase": 2,
  "role": "tester-or-reviewer",
  "agent_id": "<actual-agent-id>",
  "round": 2,
  "base_commit": "<full-hash>",
  "branch": "feat/foundation-refactor",
  "source_digest": "<sha256>",
  "snapshot_path": "<artifact-path>",
  "overall": "pass-or-fail-or-blocked",
  "items": [{"id": "2.1-or-T1-or-G1", "verdict": "pass-or-fail-or-blocked", "evidence": "<test-or-file:line>"}],
  "checks": [{"command": "<exact-command>", "exit_code": 0, "passed": 0, "failed": 0, "skipped": 0, "xfailed": 0, "required": true, "output_path": "<raw-output>"}],
  "changed_files": [],
  "blocking_findings": [],
  "limitations": [],
  "unexecuted_checks": [],
  "workspace_frozen": true
}
```

每个 handoff 另含 `from_agent_id`、`to_agent_id`、`handoff_kind` 与 `finding_id`（若有）。
日常报告的 `to_agent_id` 必须是工程师；`phase_final_handoff` 和 `three_attempt_escalation`
的接收者才是父 Agent。不能用“仅抄送”名义把日常报告发给父 Agent。

测试员报告的 round 是测试批次，不消耗正式审核轮次；role 必须明确。
审核员对 12 个业务项、G1–G6 及独立测试报告逐项检查，不能只引用工程师的 counts。
必需测试未执行、失败、skip、xfail 或 source_digest 不符，不能给 overall=pass。
隔离变异实验的预期失败与正常验收结果分栏，不计入正常通过数量。

父 Agent 收到最终 handoff 后比较实际文件、指纹与暂存清单；实现/测试变化必须回到工程师局部闭环。
仅追加提交记录等元数据可以单独核对，但必须注明未再次独立审核。
提交成功后用 `git show --name-status`、`git status` 核验，记录 full hash。
提交失败时停留当前阶段；不得先派下一阶段。

## 6. 自包含交接包

父 Agent 给工程师、工程师给测试员/审核员创建会话时，提示词必须包含：

1. Task：角色、阶段、真实轮次、允许写入范围、明确的停止/返回条件。
2. Context：分支/full base、workspace/server、工程师 ID、父 Agent 最终返回地址、当前写入权与已知运行进程。
3. Relevant files：本规范、状态、阶段契约、实现/测试调用点，不能只说“见之前对话”。
4. Current state / What was tried：已完成证据、真实失败和未运行项、修复是否已复验。
5. Acceptance：完整本阶段业务编号、通用约束、角色专项检查，不能只给总通过数量。
6. Constraints：不得提交、越权或绕过既定 handoff；不得触碰用户文件/真实账户，环境阻塞如实返回。

工程师日常完成 -> 向测试员交接并冻结；测试员/审核员完成 -> 只向工程师报告并冻结。
最终阶段完成或同一 finding 三轮未解 -> 工程师向父 Agent 发送一次结构化报告并冻结；
该显式 handoff 使用 `notifyOnFinish=false`，消息本身就是父 Agent 的唤醒与交付凭据。
发送显式回复前核对 `list_hosts`，使用真实接收者 ID；子 Agent 的报告用
`notifyOnFinish=false` 避免重复通知，创建/跟进时的完成回调属于工程师而非父 Agent。
工程师不以后台自动派工插件的存在为假设，必须实际调用相应工具。

## 7. 阶段 2 切换记录

原base：`1c587f33c65fda0f95d8992d6195b1b209f84ed6`。
第1轮审核员 `eb4e5adb-a736-402f-bdfa-0fed4b5065d5` 报告
`5702e876-15ce-4639-8660-73415b1a5e7d`：2.1–2.12 pass，但 C2/G1 fail，
`test_stale_valuation_blocks_new_risk` 的精确原因码回归。overall=fail，不得提交。

工程师 `d29dfb7b-dfac-4c6d-857c-129a3ed578e5` 冻结报告
`d1264b19-b572-49a9-895d-ca4726816d6c`：已在冻结前删除 `_migrate_books()` 中的
`_refresh_status()` 调用，目标用例1 passed；修复后的无DSN全量3368 passed/57 skipped，
带DSN全量被中断，均不等于修复后的完整PG验收。独立测试员需复验。

双方确认无继续写入，父 Agent 接管派工。旧第1轮失败保留；
独立测试通过后启动全新第2轮审核员，不复用工程师自己创建的旧审核会话作为新轮次。
34个tracked修改+5个新增任务文件作为交接时枚举事实；报告中“4个新增”的笔误不沿用，
以真实路径清单为准。工作流改动随后由父 Agent 添加，属于本次用户授权的额外交付范围。

### 7.1 首次测试员启动的审批阻塞（2026-11-02）

已完成配置、真实路径枚举和输入快照 `artifacts/foundation/phase2/tester-input-snapshot.json`，
摘要为 `968408d3c2e80b7be56dc03c9ecc08dc7b7a6a9cef6a2e6351724696173c8814`。
首次 `create_agent` 启动独立测试员时，宿主返回“Automatic approval review failed /
usage limit / action was not executed”；没有创建测试员，也没有执行测试任务。
父 Agent 未换用工具或 provider 绕过审批，保留 `BLOCKED` 状态。

恢复时核对 provider 显示的额度窗口已结束，重新读取 profiles 与当前分支/HEAD/状态；
若快照仍匹配，按第2节用户指定的 DeepSeek Harness 配置及第7节上下文创建测试员。
交接包采用第6节结构，
附带阶段2完整2.1–2.12、T1–T8、冻结报告、允许写入路径、PG/回归命令和
`tester-input-snapshot.json`。不得将本阻塞记为独立测试失败或正式审核失败，
也不得在它未恢复时提交阶段2或进入阶段3。

2026-11-02 13:17 收到用户指定 provider 后，重新调用了 `list_profiles`、`list_hosts`、
`list_models` 和 `get_agent_status`，这些只读 Paseo 工具仍全部在宿主自动审批额度处失败，
没有实际调用 DeepSeek Harness。已把待启动 provider 更新为 `deepseek-harness`，
没有跳过核验直接创建测试员。必须在宿主审批恢复后完成模型/能力发现并启动。

### 7.2 阻塞根因定位与处置决策（2026-11-02）

**已确认事实**

- 生产配置 `~/.codex/config.toml` 中 `model = "group/official-first"`，
  无 `approvals_reviewer`、无 `approval_policy`，即两端均为默认值。
- 该组经 magpie 以 `order` 策略实现，第一顺位 codex 额度耗尽后切第二顺位 kimi；
  主对话确实切换成功，agent 仍能正常回复。
- 13:31 会话记录显示：`Shell git branch --show-current` 正常执行，
  紧随其后的 4 个 MCP 工具调用（`list_profiles`、`list_hosts`、`list_models`、
  `inspect_provider`）全部带审批告警失败。

**机制判断**

沙箱内允许的命令不走审批，MCP 工具调用必须过审批。审批审查是运行时发起的
独立模型调用，携带的是具体模型标识而非 `group/official-first` 这一虚拟组名，
因此不命中路由组，magpie 的 `order` 兜底无法生效，请求落到额度已空的 codex。
审批判定失败语义为 fail-closed，故直接拒绝执行而不是降级放行。
这正是"provider 有额度、路由组有兜底，却仍不能执行"的原因：
**路由组覆盖的是生成通道，不是审批通道。**

**评估过但未采用的方案**

| 方案 | 未采用原因 |
| --- | --- |
| `approvals_reviewer = "user"` | 审查数量上升而信息质量下降（人只看渲染文案，审查代理看结构化请求），易产生审批疲劳；同时破坏本流程 `polling:false` 的无人值守特性 |
| 追加 `approval_policy = "on-request"` | 若原为 `never`/full-access 会反向收紧，导致原本畅通的 shell 命令也开始弹审批 |
| 关闭审批 / full-access | 该仓库含 git 与文件写权限，风险收益不成立 |
| 在 `[auto_review]` 指定审查模型 | 官方文档提到该配置段存在，但本机版本是否支持指定模型未经证实，需先探测 |

另注：手动改 `config.toml` 存在 magpie Drift 检测覆盖风险，不作为首选。

**处置决策（13:44）**

用户选择重置额度，不修改任何审批配置。工作流继续保持 `BLOCKED`，
`state` 不推进，第 1 轮审核失败记录保留。本次阻塞不得计为独立测试失败。

**额度恢复后的恢复顺序**

1. 回原 Paseo 会话，先单独调用 `list_profiles` 探测（最省额度）。
2. 通过后读取 `list_profiles`、`list_models deepseek-harness`、`inspect_provider`，
   据返回的真实模型 ID 与支持项填写 `roles.tester.model`，不得猜测。
3. 校验 `artifacts/foundation/phase2/tester-input-snapshot.json` 摘要仍为
   `968408d3…8814`；不匹配则重建快照。
4. 用第 6 节结构创建独立测试员并交接阶段 2 工作单。
5. 独立测试通过后才启动第 2 轮只读审核；期间不得提交阶段 2 或进入阶段 3。

### 7.3 额度恢复后的接续（2026-11-02）

本次用户要求再次尝试后，`list_profiles`、`list_hosts`、`list_models`、
`inspect_provider` 已实际成功；DeepSeek Harness 的实际可用模型见第1节。
工程师和第1轮审核员均为 idle，没有其他实施写入者。
核对42个受审源文件后，只有本规范追加的审批诊断记录发生变化，生产实现与测试未变。
父 Agent 将保留原审批阻塞历史，更新本次启动配置与输入快照，再创建独立测试员。
测试写入权只交给新测试员，正式审核仍从第2轮继续；额度恢复不代表阶段2通过。

### 7.4 保存测试员配置并修正模型渠道（2026-11-02）

首次创建的测试员会话 `29822745-df5a-4e5e-b5f7-f66230c24ddd` 使用
`deepseek-harness`，但内部模型误选为 `["deepseek-official","deepseek-v4-pro"]`。
该会话在任何测试开始前返回 `Insufficient Balance`，未获测试写入权；
此错误属于该模型渠道，不能据此判断用户的 WorkBuddy 渠道无额度。
同时，父 Agent 只创建了会话，没有保存可复用的测试员 profile，故配置界面缺少该角色。

用户指出后，父 Agent 核对了同一 Desktop 宿主 `srv_3QDZImHo82uk`、
现有 profiles、DeepSeek Harness 能力和官方 Agent profiles / Configuration 文档。
通过官方 CLI 的 `daemon config set daemon.agentProfiles` 追加“测试员”，
保存到该宿主 `config.json` 并热加载；`list_profiles` 已返回第四个角色。
配置校验确认原三个 profiles 及其他配置值未改。测试员使用用户已有的
`["magpie","workbuddy/auto"]` 渠道，正常权限审查保持开启。

父 Agent 已显式更新原测试员会话的模型与名称，不新建并行写入者。
再次交接前核对原42个受审源文件与v2快照一致，再仅更新本规范并生成v3输入快照；
新快照显式记录总摘要的计算格式。运行状态与启动确认使用新的摘要。
先前错误保留为环境历史，不计独立测试失败或正式审核轮次。
测试员完成独立测试并冻结后才启动第2轮审核，阶段2仍未提交。

### 7.5 命令执行环境阻塞与受控协作（2026-11-02）

测试员报告 DSH 的 `pwsh` 在命令启动前遇到
`SetNamedSecurityInfoW failed (Win32 5): grantWrite(...)`。
父 Agent 拒绝修改 ACL 的修复申请，并要求冻结。测试员确认未修改 ACL，
未编写独立测试、未运行 pytest，且其自建诊断终端已关闭。
父 Agent 核对全部42个文件的磁盘哈希、完整修改清单及v3总摘要，均与冻结输入一致。
报告提到的五个测试文件均为基线中已提交且未修改的文件，并非快照漏项；
完整 base_commit 以 Git 输出和状态文件为准，不沿用报告中的抄写错误。

父 Agent 在自身现有受控环境运行 `test_trading_service.py`，结果36 passed，
原始输出、JUnit和退出码保存在 `artifacts/foundation/phase2/parent-service-baseline.*`。
这仅证明该执行通道可用并复验已有service套件，不代表独立测试或阶段2已通过。
后续依第2.1节恢复测试员用例编写，由父 Agent 代跑核准命令、测试员分析原始证据；
环境事件不增加测试失败计数或正式审核轮次。
