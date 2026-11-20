# CI/CD 完善实施工作单

## Task / authority

你是本专项的工程师兼日常协调者。按 `verified-phase-workflow` v3 和
`docs/FOUNDATION_AGENT_WORKFLOW.md` 的接口与证据规则，在独立分支完成
`docs/CI_CD_IMPROVEMENT_RFC.md` 的实现，而非只给建议。

- Server：`srv_3QDZImHo82uk`；workspace：`wks_ec1aad545fcca404`。
- Worktree：`D:\Code of Python\quant_trading_system\.worktrees\cicd-hardening`。
- Branch/base：`feature/ci-cd-hardening` / `c6fe450260eeb59d46c9caee591fb1625510b443`。
- Parent（本 CI/CD 专项协调者）：`e36d6105-e0bb-4f27-bbbd-225b5a55da2e`。
- 阶段 6 根工作树写锁：`4c592f2d-2019-4f36-877a-c3314ccc9978`；不得进入根工作树写入、
  修改其阶段状态/源码或抢锁。仅可只读当前根状态以协调最终整合。
- 使用当前 Paseo“工程师”profile 的职责。你是实现者，不自行独立验收；必须创建或唤醒你名下的
  测试员和每轮全新只读审核员，日常通知只返回你。当前 profile 需在派工前重新 `list_profiles`。
- 你无提交、push、merge、部署或改远端 ruleset 权限。父 Agent 在最终独立测试/审核通过后，
  依用户“另开分支，完成后合并”授权处理已验证分支提交；阶段 6 提交前不得合回开发线。

用户已明确：**Binance Spot Demo 为最高验收环境，不要求真实 testnet。**不能把旧
Testnet 缺口继续列为阻塞，也不能因此降低 Demo 中的覆盖证明、账务/恢复/幂等要求。
不读取或报告凭据值，不启动真实账户，不连接生产；本专项不要求实际跑 24h 才能交付 CI 改造，
但不得声称后续 release 所需的当前 digest Demo 验收已执行。

## Required reading / inputs

先读 `AGENTS.md`、本工作单、`docs/CI_CD_WORKFLOW_STATE.json`、RFC、`rules/L1_TECH_STACK.md`、
`rules/L2_BUSINESS.md`、`docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、
`docs/FOUNDATION_AGENT_WORKFLOW.md`、已安装 `verified-phase-workflow` 及 protocol。
涉及 OMS/风险/适配器测试时按需读 `.agents/skills/quant-oms-core`、risk、binance 及 SPEC；
前端变更读 `Frontend/AGENTS.md`。根 Demo 文档已复制到本工作树，不要再修改根文件。

## Acceptance and implementation scope

### C1 测试选择与 P0

- 为 `trader/tests/test_*.py` 建立可审计的完整分类/发现校验。保留快速 P0，同时让全部
  244 个当前测试文件被唯一分类到 P0/其他离线/PG/显式外部或运维组；新增未分类、失效路径、
  重复冲突或工作流选择缩小必须失败。不要以“未列出即 indirect covered”规避直接执行。
- P0 直接加入 OMS CAS/终态、`cl_ord_id+exec_id` 去重、并发下单、pre-trade fail-closed、
  风险模式升级/恢复、回放与 WS/REST 幂等的关键已有测试。新增一条通过真实 OMS 接口的
  WS/REST 相同成交并发验证，不能只调用存储代替。Control 执行完整 API 风险状态用例，
  不再用只覆盖参数校验的 `-k`；Research/AI 独立 gate 覆盖回放一致性、离线/Shadow 晋级、
  Paper 不启用 Live、HITL 禁止直接执行。可复用已有可靠测试，不无意义重写业务。
- 普通 unit/P0 禁止公网 socket；PG/本地 HTTP 只在明确分组放行 loopback。保留现有测试
  隔离，不因 `.env` 加载而假设禁网。`REQUIRE_POSTGRES=1` 在必跑 PG job 使缺驱动/连接/
  schema 失败成为 fail 而非 skip；JUnit 强制 PG skipped=0。外部/可选研究依赖分组明确，
  不允许必跑项静默 skip；`testnet_e2e.py` 不作为普通 CI 或 Testnet 必跑项。
- 所有关键修复先失败复现，再实现并定向复验；运行相关回归和实际 CI 命令。

### C2 CI 门禁、证据和依赖

- 更新 `.github/workflows/ci-gate.yml`：最小默认权限、pip 缓存、合理 job 超时、JUnit/覆盖
  报告 artifact、完整测试 gate；新增 `CI Required` 聚合所有 PR 必需 Job，某必需 Job 失败/
  跳过/取消不能汇总为成功。类型检查按当前 Python 配置收口，不制造大规模无关类型清理；
  可先对 CI/ops 新模块严格检查并如实限定范围。前端实际运行 lint 和 `test:coverage`，
  缺 provider 时按 lockfile/现有工具补齐，不能把配置阈值当成已执行。
- 后端覆盖率先形成行/分支基线，关键核心/交易模块设可审查门槛，不虚构未测得的数字。
  Python minor 保留现有 3.12 策略；修复 `rules/L1_TECH_STACK.md` 的旧 3.12.5 事实。
- 生成可复核的 runtime/CI 依赖锁或 constraints（含哈希，注意现有 `*.lock` ignore）。
  不以手工编造 hash 冒充锁定；网络/工具不可用时保留明确未完成项，不伪称可复现构建。
- 准备 ruleset 迁移文件/操作说明：`CI Required` 为必需检查、strict 分支同步、至少一名
  非作者审核；不实际改远端权限。现远端仅 3 个必需 Job 的事实不得写成已修复。

### C3 不可变构建与隔离启动

- 后端镜像按 commit 构建一次，main 成功后推送 GHCR 并用 digest 部署；PR 可构建验证但不得
  获取发布 secrets。镜像含 revision/release 元数据，runtime dependencies 与 CI 依赖区分。
  Dockerfile 保持非 root，避免复制本地 secrets/env；基础镜像可核验 digest 后再 pin。
- `scripts/compose_smoke.py` 支持已构建的固定 digest，不要求目标主机重新构建。实际启动
  隔离 Compose，要求所有被启动容器健康、现有资源前后身份完全相同（包括原不存在的情况）、
  Control 无交易凭据、Execution 空凭据和非 Demo 均拒绝、`/health/trading` 负向语义、
  告警 ack 与任务资源清理。原有 `passed` 漏看 health/清理的结果先写失败测试。
- 生成 release manifest（commit/tree clean/workflow、digest、依赖 hash、schema/checksum、
  测试/前端 artifact 引用），并提供 SBOM 与镜像/依赖扫描工作流或工具入口。Action 使用
  当前可验证的官方发布引用；不把 tag 引用写成 SHA pin。产物不含密钥。
- 前端已有 Vite build 可保存为版本化 artifact；尚无运行时前端托管路径时明确保留该边界，
  不在本专项任意增加公开 Web 服务。

### C4 Demo 配置、发布预检和 dry-run

- 添加 `deploy/compose.demo.yml` 与 `deploy/demo.env.example`（只含非秘密配置/安全占位），
  强制 `BINANCE_ENV=demo`、固定 digest、独立 project/端口/卷，不使用 `latest` 或本地源码
  build。验证与基础 Compose 合并后的真实配置，不能用 `config --quiet` 代替启动。
- 实现启动前 fail-closed 配置/发布预检：拒绝默认或缺失 DB/告警/Control 凭据配置、
  非 Demo、缺失专用账户/标的 allowlist/warmup/外部告警/DR、错误或不匹配 release digest、
  缺失 schema 或回滚兼容证据。输出 reason codes 和键名，绝不输出秘密值。
- 新 release ID 只用于产物/日志/证据关联，不改变 `QTS_DEPLOYMENT_ID` 的策略实例语义，
  不绕过现有租约/fencing/恢复/Control→Execution 接口。公开健康字段变化须同步接口契约。
- 发布 workflow 默认 dry-run，不自动 SSH/连接主机/执行 Demo 交易。真正 Demo 发布只允许
  后续受保护环境的人工触发；不能将 `workflow_dispatch` 的批准偷换成自动生产部署。

### C5 回滚、迁移、Demo 证据与观察门禁

- 新建 `docs/CI_CD_RELEASE_RUNBOOK.md`：选择已验证 digest、批准/变更风险、目标资源身份、
  先关闭新增风险但保留查询/撤单/允许的风险降低、REST 对账、单 owner、健康/备份/告警、
  旧 digest 回退和恢复边界。绝不通过改投影、删事件、改历史或旧模型 registry 回滚账务。
- 提供数据副本上的 expand-only 迁移、旧版兼容、事件回放/投影重建、六表守恒/校验和测试；
  不可逆 schema 或旧版不兼容时拒绝自动回滚并转人工。不能对已有 PG 数据/卷执行迁移演练。
- `scripts/summarize_observation.py` 将 ready_ratio、对账年龄、outbox、最终未入账成交、
  重复/重发/覆盖证据等纳入显式门禁。参数和默认阈值必须可审计；基于已记录 Demo
  `ready_ratio=0.6252` 说明初始阈值，不通过降低安全断言制造绿灯。发布证据需绑定
  当前 commit+digest+Demo 环境；历史兼容读取与 release strict 模式分开。
- Demo E2E/24h、主动重连、runtime 重启、崩溃/租约/DR 由手动受保护 workflow 或本地
  受控入口执行；离线故障演练可定时，不能自动触发真实外部账户操作。工作流不索取
  testnet 凭据，不声称本专项已运行外部 Demo。
- `scripts/model_rollback_manager.py` 不作为运行系统回滚器；文档明确策略回滚的独立审批边界。

### C6 文档与交付

- 同步 RFC 的实施状态（区分实现、实测、未执行）、`PROJECT_ARCHITECTURE.md` 的部署拓扑、
  `INTERFACE_CONTRACTS.md` 的配置/发布接口（若有）、Demo 验收/runbook。不修改基础阶段状态，
  不改历史验收事实。更新 `.github/pull_request_template.md` 让测试结果/风险/回滚绑定 digest。
- 仅纳入本专项文件，不读取 `.workbuddy/`、不修改 `workflow/` 或 `artifacts/foundation/`。
  专用原始证据放 `artifacts/cicd/`（不提交）；专项运维状态为 `docs/CI_CD_WORKFLOW_STATE.json`。
- 通过本地 YAML 解析/语法/静态检查、pytest、前端检查和实际隔离 Compose（Docker 可用且
  任务资源范围内）验证。无法运行的 GitHub 远端/真实 Demo 明示未执行。禁止把 0 tests、
  skip、xfail、仅 YAML 可解析、仅 config、历史 artifacts 当作行为通过。

## Workflow / ownership / handoff

你取得本工作树实现写权，维护 `artifacts/cicd/engineer-issue-ledger.json`，同一 finding
三次修复+独立复验仍失败才向 parent 请求根因分析。实现/基础测试冻结后，创建你名下的独立
测试员；其写权限仅限 `trader/tests/test_cicd_independent*.py`、其专用 fixtures/报告、
`docs/CI_CD_INDEPENDENT_TEST_REPORT.md` 和 `artifacts/cicd/tester-*`，不得修改生产、既有
断言、业务契约或 workflow。完成通知返回你，routine result 不抄送 parent。

测试员依据上述 C1–C6 独立设计负向/反例测试，尤其检查假绿、错误 digest/环境、旧版回滚、
PG skip、socket 禁网、清理与 source fingerprint。若其命令通道不可用，按现有 controlled
execution 协议给你精确 argv 和副作用，你在现有权限内审查代跑，作者/执行者/分析者分开记录。

必需测试通过后收回写权，创建每轮全新只读审核员，核查完整 diff（含 untracked）、真实命令、
退出码、skip/xfail、角色/写权、文档一致性、manifest 和 SHA-256。审核失败→限定修复→测试员
复验→新审核员；不得直接自审放行。保留所有尝试和真实失败，不因换 Agent 清零。

最终交付前冻结文件，生成完整文件清单、逐文件 hash 和 canonical source digest（状态/报告/
artifacts 作为运维元数据单列），提供精确命令、输出路径、未执行检查。仅当独立测试全过且
最新审核 overall=pass 时，用 `send_agent_prompt` 给 parent `e36d...` 一次
`handoff_kind=phase_final_handoff`、`notifyOnFinish=false` 的结构化报告，并冻结。
只有 parent 可做本地 commit。不要等待 root 阶段 6 才完成本分支实现/审核；阶段 6 完成后再协调
开发线整合。`main` 只能通过不夹带未验收基础重构的 PR，不能直接 merge。
