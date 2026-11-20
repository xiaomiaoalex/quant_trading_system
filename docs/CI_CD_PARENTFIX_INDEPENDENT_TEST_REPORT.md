# CI/CD 父级修复（F-P1..F-P8）独立复验测试报告

- 测试员：全新独立会话（agent_id 见最终报告），role=tester，workflow_version=3
- 分支：`feature/ci-cd-hardening`；base：`c6fe450260eeb59d46c9caee591fb1625510b443`
- 日期：2026-11-05
- 状态：**进行中**（本文档先记录测试设计与手算期望，验证结果与限制在执行后追加）

## 0. 命令通道状态（v3 §2.1）

会话内 `pwsh` 命令通道在**命令启动前**即失败：
`SetNamedSecurityInfoW failed (Win32 5): grantWrite(D:\...\cicd-hardening)`。
无论 workdir 指向会话工作树还是平台临时目录均同样失败，属宿主沙箱对工作树的
ACL 授予失败，与 `docs/FOUNDATION_AGENT_WORKFLOW.md` §7.5 记录的历史阻塞一致。
按 §2.1 与派工约束：不修复 ACL、不提权、不改用不受限终端。已冻结，全部验证命令
以 `kind=execution_request` 交工程师代跑；报告区分 `authored_by` / `executed_by` /
`analyzed_by`，未执行前不出具任何 pass 结论。

## 1. 测试设计与手算期望（先于读实现编写）

依据：`docs/INTERFACE_CONTRACTS.md` 13.17/13.18、`docs/CI_CD_RELEASE_RUNBOOK.md`、
`docs/CI_CD_IMPROVEMENT_RFC.md` §6.5、四个 workflow YAML。期望值均为手算/契约推导，
不来自被测实现或工程师/旧测试员结论。

### F-P1 strict 观察窗口（summarize_observation.py）

| # | 输入构造 | 手算期望 |
| --- | --- | --- |
| P1-1 | 样本窗口跨度 1h（如 10:00→11:00），`--strict-release --min-hours 1`，其余指标全部合规 | 退出码 1；blockers 含 `STRICT_WINDOW_SHORTER_THAN_24H`；`effective_min_hours=24`（按 24 计） |
| P1-2 | 同上但 `--strict-min-hours 12`（<24） | argparse usage error（退出码 2），报 `--strict-min-hours` 下限 24，不产出 summary JSON |
| P1-3 | 窗口跨度 ≥24h 且 `--strict-min-hours 24`，其余全合规 | 退出码 0，无 `STRICT_WINDOW_SHORTER_THAN_24H` |

### F-P2 strict 运行时绑定（summarize_observation.py strict 校验 + collect_runtime_release_binding.build_binding）

手算规则（契约 13.17/13.18）：strict 下 release evidence 必须是 runtime-inspected
绑定：`runtime_inspected=true`、`checks.{all_running,all_healthy,labels_match,
image_digest_match,trading_ready}` 全 true、`containers` 存在且 label/digest 与期望一致、
`environment=demo`、commit/digest 与期望一致。

| # | 输入构造 | 手算期望 |
| --- | --- | --- |
| P2-1 | 手写 binding：内容"看起来完整"但缺 `runtime_inspected=true` | 退出码 1；blocker `RELEASE_RUNTIME_BINDING_MISSING`（或等价拒绝），绝不通过 |
| P2-2 | binding 无 `checks` 键 | blocker `RELEASE_RUNTIME_CHECKS_MISSING` |
| P2-3 | binding 无 `containers` 键 | blocker `RELEASE_RUNTIME_CONTAINERS_MISSING` |
| P2-4 | binding `checks.all_healthy=false` | blocker `RELEASE_RUNTIME_CHECK_FAILED:all_healthy` |
| P2-5 | binding commit/digest/environment 与期望不一致 | 对应 `RELEASE_*` blocker（commit/digest/environment） |
| P2-6 | 合法 runtime-inspected binding（全字段正确、checks 全 true、health.trading ready） | strict 全链路退出码 0 |
| P2-7..P2-12 | `collect_runtime_release_binding.build_binding`（或等价入口）以假 inspect 数据注入：缺容器 / 容器未运行 / health=unhealthy / label release-id 不符 / 镜像 digest 不符 / `/health/trading` 未 ready | 逐一得到稳定 code：`CONTAINER_MISSING:<svc>`、`CONTAINER_NOT_RUNNING:<svc>`、`CONTAINER_NOT_HEALTHY:<svc>:<status>`、`LABEL_RELEASE_ID_MISMATCH:<svc>`、`IMAGE_DIGEST_MISMATCH:<svc>`、`HEALTH_TRADING_NOT_READY`；且输出含 `runtime_inspected=true` 与 `errors` |

### F-P3 deploy_gate（pre/post 反例 + 静态 concurrency）

以合成证据注入（control health JSON / 容器 inspect 假数据），逐项反例：

| # | 反例 | 手算期望 code |
| --- | --- | --- |
| P3-1 | 新增风险未关闭（`new_risk_blocked=false`） | `NEW_RISK_NOT_BLOCKED` |
| P3-2 | UNKNOWN 命令存在 | `UNKNOWN_COMMANDS_PRESENT` |
| P3-3 | outbox 积压 >0 | `OUTBOX_PENDING` |
| P3-4 | 租约无效 | `EXECUTION_LEASE_INVALID` |
| P3-5 | runtime owner 与租约不一致 | `EXECUTION_OWNER_NOT_ALIGNED` |
| P3-6 | fencing token 不一致 | `EXECUTION_FENCING_NOT_ALIGNED` |
| P3-7 | 存在未入账成交 | `UNBOOKED_FILLS_PRESENT` |
| P3-8 | 容器不健康 | `CONTAINER_NOT_HEALTHY:<svc>:<status>` |
| P3-9 | label 与发布不符 | `LABEL_RELEASE_*_MISMATCH:<svc>` |
| P3-10 | digest 不符 | `IMAGE_DIGEST_MISMATCH:<svc>` |
| P3-11 | `/health/trading` 未 ready | `HEALTH_TRADING_NOT_READY` |
| P3-12 | 全合规 pre / 全合规 post | 退出码 0，无 blockers |
| P3-13 | 静态：`deploy-demo.yml` 与 `demo-evidence.yml` 的 `concurrency.group` 必须同为 `qts-demo-${{ <project 输入/env> }}` 形式 | 两文件 group 表达式一致且含 `qts-demo-`；cancel-in-progress 语义一致 |

### F-P4 release-build 启动 smoke（静态工作流断言）

| # | 断言（解析 `.github/workflows/release-build.yml`） |
| --- | --- |
| P4-1 | main 发布路径存在使用 `${IMAGE}@${DIGEST}`（即 `repo@sha256:...` digest 引用，非 tag/local build）调用 `compose_smoke.py` 且带 `--require-digest` 的步骤 |
| P4-2 | release manifest 生成/上传绑定 startup smoke 证据（manifest 步骤在 smoke 之后或引用其产物） |
| P4-3 | PR（非 main 发布）路径不运行该启动 smoke，也不上传声称"发布启动通过"的 release 证据 |

### F-P5 verify_release_provenance（run-json 反例 + 工作流顺序静态断言）

| # | 反例 | 手算期望 code |
| --- | --- | --- |
| P5-1 | 事件非 `workflow_run` | `PROVENANCE_EVENT_MISMATCH` |
| P5-2 | workflow path 非 release-build.yml | `PROVENANCE_WORKFLOW_MISMATCH` |
| P5-3 | 分支非 main | `PROVENANCE_BRANCH_MISMATCH` |
| P5-4 | status≠completed | `PROVENANCE_RUN_NOT_COMPLETED` |
| P5-5 | conclusion≠success | `PROVENANCE_RUN_NOT_SUCCESSFUL` |
| P5-6 | head_sha ≠ 请求 commit | `PROVENANCE_HEAD_SHA_MISMATCH` |
| P5-7 | manifest commit/digest/release_id/environment 与请求不符 | `MANIFEST_COMMIT_MISMATCH` / `MANIFEST_DIGEST_MISMATCH` / `MANIFEST_RELEASE_ID_MISMATCH` / `MANIFEST_ENVIRONMENT_NOT_DEMO` |
| P5-8 | 期望 commit/digest 格式非法 | `EXPECTED_COMMIT_INVALID` / `EXPECTED_DIGEST_INVALID` |
| P5-9 | 全合规 run-json + manifest | 退出码 0 |
| P5-10 | 静态：`deploy-demo.yml`、`demo-evidence.yml` 中 provenance 门禁步骤位于 checkout `inputs.commit` 之前、位于任何使用 secrets 的步骤之前 | 文本/YAML 步骤顺序断言 |

### F-P6 Demo overlay 外部告警接线

| # | 断言 |
| --- | --- |
| P6-1 | `compose.yml + deploy/compose.demo.yml` 合并渲染后，control 与 execution 的 `QTS_ALERT_WEBHOOK_URL`/`QTS_ALERT_HEARTBEAT_URL` 均等于外部键 `${QTS_EXTERNAL_ALERT_WEBHOOK_URL}` / `${QTS_EXTERNAL_ALERT_HEARTBEAT_URL}`，不指向 alert-sink 服务 |
| P6-2 | `deploy_preflight.py`：全合规环境缺 `QTS_EXTERNAL_ALERT_HEARTBEAT_URL` → errors 含 `EXTERNAL_ALERT_HEARTBEAT_MISSING`，退出码非 0 |

### F-P7 strict 指标缺席≠零

| # | 输入 | 手算期望 |
| --- | --- | --- |
| P7-1 | strict，样本缺 `outbox_backlog` | blocker `STRICT_METRICS_MISSING:outbox_backlog`（退出码 1） |
| P7-2 | strict，样本缺 `unbooked_fills`/`unknown_commands`/`balance_anomalies` | `STRICT_METRICS_MISSING` 列出全部缺键 |
| P7-3 | strict，样本 `outbox_backlog="0"`（字符串，类型非法） | `STRICT_METRICS_INVALID:outbox_backlog` |
| P7-4 | 兼容模式（无 `--strict-release`），样本缺上述键 | 保持历史行为：不因缺 metrics 失败（若其余合规则通过） |

### F-P8 release manifest 证据强制

| # | 输入 | 手算期望 |
| --- | --- | --- |
| P8-1 | `release_manifest.build_manifest` 缺 JUnit/coverage/frontend/SBOM/scan/startup smoke 任一 | 失败，code `RELEASE_EVIDENCE_MISSING:<key>` |
| P8-2 | startup smoke summary `passed=false` | `RELEASE_STARTUP_SMOKE_NOT_PASSED` |
| P8-3 | smoke digest 与镜像不一致 | `RELEASE_STARTUP_SMOKE_DIGEST_MISMATCH` |
| P8-4 | 证据齐全且 smoke 通过、digest 一致 | 成功 |
| P8-5 | `deploy_preflight.py` manifest 缺证据 | `RELEASE_MANIFEST_EVIDENCE_MISSING:<key>` |
| P8-6 | manifest startup smoke 未通过 | `RELEASE_MANIFEST_STARTUP_SMOKE_FAILED` |
| P8-7 | 静态：`release-build.yml` 关键 artifact（manifest/SBOM/scan 等）`if-no-files-found: error` | YAML 断言 |

### 回归（R 组）

| # | 检查 | 期望 |
| --- | --- | --- |
| R-1 | `python scripts/verify_test_manifest.py` | exit 0；256+ 文件全部唯一分类，无未分类/重复 |
| R-2 | P0 组（offline 网络策略） | 全部 passed，0 skipped / 0 xfailed |
| R-3 | offline 组 | 全部 passed，0 skipped / 0 xfailed |
| R-4 | research_ai 子集 | 全部 passed |
| R-5 | postgres 组（任务自有容器 127.0.0.1:55436，`REQUIRE_POSTGRES=1`） | 全部 passed，0 skipped；不触碰 qts-postgres/qts_pgdata |
| R-6 | 写入范围：`git status` 仅列出两个 parentfix 测试文件与本报告/artifacts | 无越界写入 |

## 2. 实现记录（执行后追加）

- 测试文件（authored_by=本测试员，冻结于发 execution_request 前）：
  - `trader/tests/test_cicd_parentfix_offline.py`（59 项：F-P1×3、F-P2 strict×6 + build_binding×7、
    F-P3 evaluate×13 + concurrency 静态×1、F-P4×3、F-P5 evaluate×9 + 顺序静态×1、
    F-P6 渲染×1 + preflight×2、F-P7×4、F-P8 manifest×4 + preflight×3、回归×1）
  - `trader/tests/test_cicd_parentfix_postgres.py`（5 项：`collect_db_facts` 真实 SQL 维度×4 +
    account_id fail-closed×1）
- 工程师只读预检（executed_by=工程师，2026-11-05，`artifacts/cicd/tester/parentfix-*.diff`）：
  - black（line-length 100）/isort 初版不合规 → 已按工程师生成的 diff 修正；
  - offline 预检 59 collected / 57 passed / 2 failed；失败均为本测试员 fixture 缺陷
    （`_manifest_args` 无条件重写默认 smoke 文件，覆盖了失败 smoke 用例）→ 已修复：
    仅在未显式传入 `startup_smoke` 时创建默认 smoke；
  - 工程师在预检期间改动了实现：`build_binding` 缺容器时 `all_running/all_healthy=false`、
    strict 指标 blocker 聚合行+逐键行、契约 13.17 同步；这些是实现改动，最终复验以
    正式代跑结果为准。

### 2.1 第一轮代跑（executed_by=工程师；analyzed_by=测试员，已独立核对原始文件）

原始输出：`artifacts/cicd/parentfix-tester-cr*.stdout.txt` / `.exit.txt`；
JUnit：`artifacts/cicd/parentfix-tester-{offline,p0,research-ai,postgres}.junit.xml`。
测试员逐文件核对：各 `.exit.txt` 分别为 `cr<N>_exit=0`（CR-1 为 1）；
JUnit `testsuite` 属性独立核对为 errors=0 / failures=0 / skipped=0，
tests=offline 3413、p0 367、research_ai 252、postgres 259，与工程师转录一致
（CR-3 stdout 进度行全为 `.`，无 F/E；stdout 尾部仅含 warnings 段，最终
summary 行缺失，以 JUnit 属性为准）。manifest 261 文件唯一分类、warnings=[]、ok=true
已从 `cr2.stdout.txt` 原文核对。

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| CR-1 black（line-length 100） | 1 | offline 文件通过；PG 文件需重排（4 处）→ 已按 diff 修正 |
| CR-1 isort | 1 | offline 通过；PG 文件 `trader` 导入前缺空行 → 已按 diff 修正 |
| CR-2 `verify_test_manifest.py` | 0 | 261 个测试文件唯一分类，warnings=[]（原文核对） |
| CR-3 offline 全量 | 0 | 3413 passed / 0 failed / 0 skipped / 0 xfailed（含 parentfix offline 59 项） |
| CR-4 P0 | 0 | 367 passed / 0 skipped |
| CR-5 research_ai 子集 | 0 | 252 passed / 0 skipped |
| CR-6 postgres 全量 | 0 | 259 passed / 0 skipped（基线 254 → parentfix PG 贡献 5 项，与实际测试定义数一致） |
| CR-7 git status/diff + 指纹 | 0 | 全仓库工作区含工程师既有交付改动（61 行 status / 30 行 diff）；测试员可归属改动仅两份测试文件 + 本报告。格式修正前指纹：offline `9a02bd83…`、postgres `9be37dd7…`、report `c5409d48…`（修正后指纹由第二轮 CR-7 重发） |

- 功能用例在第一轮已全部通过；本轮对 PG 文件仅按 black/isort diff 做格式修正，
  字符串隐式拼接结果不变、断言未改动。

### 2.2 第二轮复核（executed_by=工程师；测试员已独立核对 .exit.txt 与 JUnit 属性）

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| CR-1b black（两文件） | 0 | `2 files unchanged`；`parentfix-tester-cr1b-black.exit.txt` 原文核对 |
| CR-1b isort（两文件） | 0 | 通过；`parentfix-tester-cr1b-isort.exit.txt` 原文核对 |
| CR-6b PG 单文件（格式修正后） | 0 | JUnit `parentfix-tester-postgres-single3.junit.xml` 核对：tests=5，errors/failures/skipped=0，5 个 parentfix 用例全通过 |
| PG 全量复跑（冻结后） | 0 | JUnit `parentfix-tester-postgres-frozen.junit.xml` 核对：tests=259，errors/failures/skipped=0 |
| CR-7b 指纹 | — | `parentfix-tester-cr7b-sha.stdout.txt` 原文核对：offline `9a02bd834b84e2d86b9ac83f49af82969d02824e84149693300b4349e4b86ee1`、postgres `613b08d307beebf21c7e0c025277aed8aaf5f35f167f7b2bee73227024982e95`；报告指纹 `27966199…` 为定稿前时点，冻结后以重算为准 |

### 2.3 第二轮契约加严适配（父级第 2 次核验拒绝后，v2 契约 13.18/13.19）

实现按加严契约变更（`scripts/release_binding.py` 校验器、`build_binding(runtime=)`、
gate 计数器必填、release-build 单一 `reports/` bundle）；本测试员仅适配测试文件：

- `_valid_binding()` 升级为契约完整形态：`errors=[]`、带时区 `generated_at`
  （窗口末样本 +1 分钟，常量 `BINDING_GENERATED_AT_MS` 手算锚定于
  `OBSERVATION_START_MS=1_793_750_400_000`）、`containers.control/execution`
  实际事实（present/running/health/image/labels）、`health.{live,ready,trading}`、
  `runtime` 身份块（含 instance_owner/lease_token）；
- 每个观察样本携带 `runtime` 身份且 `observed_at_ms == ts_ms`；
- `_clean_facts()` 补 `queued_commands: 0, sending_commands: 0`；
- upload 断言改为单一 `path: reports/` + manifest 步骤
  `--startup-smoke reports/compose-smoke/compose-smoke.json` + `if-no-files-found: error`；
- 新增 6 项反例覆盖加严面：`RUNTIME_IDENTITY_MISSING(:instance_owner)`、
  `RUNTIME_IDENTITY_MISMATCH:release_id`、`SAMPLE_RUNTIME_IDENTITY_MISMATCH:release_id`、
  `SAMPLE_RUNTIME_IDENTITY_MISSING`、`RELEASE_BINDING_WINDOW_MISMATCH`、
  `FACTS_INVALID:<key>`（4 个计数器逐一缺失 + queued>0 → `COMMANDS_NOT_CONVERGED:QUEUED`），
  并把 unbooked 反例扩展为 pre/post 双模式（pre 也拒绝未入账成交）；
- 修复的 5 个失败用例：`test_strict_full_24h_window_with_runtime_binding_passes`、
  `test_build_binding_positive_produces_inspected_binding`、
  `test_post_gate_accepts_intentional_new_risk_block_503`、
  `test_pre_and_post_gates_pass_on_clean_inputs`、`test_release_build_upload_requires_evidence_files`。

第二轮适配后 offline 文件共 65 项（原 59 + 新增 6）。未改实现/契约/其他测试员文件。

- CR-3c 首跑（executed_by=工程师）：65 collected / 42 passed / 23 failed；失败全部为
  本测试员 fixture 缺陷——`_app_container_facts()` 只定义了 control/execution，未覆盖
  全部 `APP_SERVICES`（缺 research/history/backup，`KeyError: 'research'`）→ 已修复为
  按 `APP_SERVICES` 生成全部 5 个应用服务事实；断言语义未改动。

### 2.5 第二轮最终代跑（executed_by=工程师；测试员已独立核对 .exit/stdout 与 JUnit 属性）

| 命令 | 退出码 | 结果（JUnit 属性独立核对） |
| --- | --- | --- |
| CR-1d black/isort（两文件） | 0 / 0 | black 原文 `2 files would be left unchanged`；isort 无 diff 输出 |
| CR-3d offline 单文件 | 0 | `parentfix-tester-offline-r3.junit.xml`：tests=65，errors/failures/skipped=0（65 项全通过，含新增 6 项反例） |
| CR-4c offline 全组 | 0 | `parentfix-tester-offline-full-r2.junit.xml`：tests=3443，0/0/0 |
| CR-5c P0 | 0 | tests=367，0/0/0 |
| CR-5c research_ai | 0 | tests=252，0/0/0 |
| CR-5c postgres 全量 | 0 | tests=261（基线 254 + 本测试员 5 项 + 工程师新增/计数变化 2 项），0/0/0；本测试员 PG 文件指纹未变（`613b08d3…`） |
| CR-7d 指纹 | — | offline `f3d57f83c420023046b3fad089ef82b3031e11b15b565946fc8abb2f54ff97bd`；postgres `613b08d307beebf21c7e0c025277aed8aaf5f35f167f7b2bee73227024982e95`；报告 `5b46637e…`（本节追加前时点） |

- 最终 source digest 待第 2 轮全新测试员的 `test_cicd_parentfix2_*` 冻结后由工程师重算
  回填（§4 保留占位行）。

### 2.4 逐项结论（第二轮加严复跑后 overall=pass）

| 项 | 结论 | 依据（本测试员反例，全部实跑通过） |
| --- | --- | --- |
| F-P1 | pass | 1h 窗口 `--strict-release --min-hours 1` → exit 1 且 `STRICT_WINDOW_SHORTER_THAN_24H`；`--strict-min-hours 12` → usage error（exit 2）；24h 窗口通过 |
| F-P2 | pass | strict 拒绝缺 `runtime_inspected`/`checks`/`containers`、失败 check、release 身份不符与无 evidence 的手写 binding；`build_binding` 对缺容器/未运行/不健康/label/digest/trading 未 ready/`RUNTIME_IDENTITY_MISSING(:instance_owner)`/`RUNTIME_IDENTITY_MISMATCH:*` 各给稳定错误码；契约完整形态的 binding 与合法 runtime 身份通过 |
| F-P3 | pass | evaluate pre/post 全部反例（风险未关闭、UNKNOWN、outbox、租约无效/owner/fencing、不健康、label/digest、trading 未 ready、未入账 pre/post 双拒、计数器缺失 `FACTS_INVALID:*`、queued/sending>0 未收敛）+ 双正例 + 503 NEW_RISK_BLOCKED 负向语义 + deploy/evidence 共用 `qts-demo-` concurrency；PG 真实 schema 手算计数与门禁集成 4 项通过 |
| F-P4 | pass | release-build 发布路径存在 `${IMAGE_NAME}@${{ steps.build.outputs.digest }}` + `--require-digest` 启动 smoke；manifest 绑定 startup smoke 且在 smoke 之后；PR/dispatch（PUBLISH=false）不运行 smoke/manifest/登录/下载，也不上传 release evidence |
| F-P5 | pass | provenance 对事件/工作流路径/分支/未完成/未成功/head SHA/run id/repository/manifest commit-digest-release_id-environment/期望格式非法逐一拒绝；全合规通过；两受保护工作流 provenance 先于 `ref: ${{ inputs.commit }}` checkout 且此前无仓库脚本执行 |
| F-P6 | pass | `docker compose config` 合并渲染后 control/execution 的 `QTS_ALERT_WEBHOOK_URL`/`QTS_ALERT_HEARTBEAT_URL` 等于外部 URL 且不含 alert-sink；preflight 缺 heartbeat → `EXTERNAL_ALERT_HEARTBEAT_MISSING`，集群内/非 https heartbeat 同样拒绝 |
| F-P7 | pass | strict 下缺 `outbox_backlog` 与全缺 metrics 均 `STRICT_METRICS_MISSING:*` 阻断（缺席≠0）；字符串/布尔类型 → `STRICT_METRICS_INVALID:*`；兼容模式无 metrics 仍 exit 0（历史行为） |
| F-P8 | pass | manifest 缺 junit/coverage/sbom → `RELEASE_EVIDENCE_MISSING:*`；constraints 缺失失败；smoke `passed=false`/`image_digest_ok=false`/digest 不符分别拒绝；正例绑定 smoke summary；preflight 复核 `RELEASE_MANIFEST_EVIDENCE_MISSING:coverage`/`RELEASE_MANIFEST_STARTUP_SMOKE_FAILED`；release-build 上传步骤 publish-only 且 `if-no-files-found: error` |
| 回归 | pass | manifest 261 文件唯一分类（0 warning）；第二轮最终计数：offline 全组 3443 / P0 367 / research_ai 252 / postgres 261 全部 passed、0 skipped / 0 xfailed；写入范围以可归属文件 + 指纹限定（见 §3） |

- 周期内由本测试员反例驱动、工程师完成的两处实现修正（`build_binding` 缺容器时
  `all_running/all_healthy=false`；strict 指标 blocker 聚合行+逐键行）已在最终复跑中
  覆盖，无遗留失败。blocking_findings：无。

## 3. 限制与未执行项

- **命令通道**：本测试员会话 `pwsh` 在命令启动前即 `grantWrite` 失败（§0），全部命令由
  工程师代跑（executed_by=工程师），测试员负责设计与分析原始输出（authored_by/
  analyzed_by=测试员），符合 workflow v3 §2.1 的受控协作。
- **未执行（范围外/未授权，不构成通过结论）**：
  - 远端 GitHub Actions 实际运行（release-build/deploy-demo/demo-evidence 的远端行为）
    仅以本地静态 YAML 断言覆盖（F-P4/F-P5 静态项）；
  - 真实 Demo 主机预检、24h 当前 digest 观察、真实容器启动 smoke、真实发布/回滚
    ——本专项明确不执行；
  - `deploy_gate.collect_db_facts` 的 `docker compose exec`/psql 采集分支未实测
    （PG 测试以受控 monkeypatch 替换 `_psql`，运行 gate 自身 SQL 与 `evaluate` 集成；
    docker 采集器属主机演练范围）；
  - `deploy_preflight.evaluate` 的 DR/schema/rollback 主机文件证据路径未构造
    （用例统一 `host_evidence=False`，预期为 `HOST_EVIDENCE_DEFERRED` warning，已断言）。
- **写入范围证据**：CR-7 的 `git status` 反映全仓库工作区（含工程师交付改动），不能
  单独证明测试员写入范围；本报告以"测试员可归属改动 = 两份测试文件 + 本报告 +
  `artifacts/cicd/parentfix-tester-*`"及逐文件 SHA-256 指纹为限。

## 4. 最终元数据

- branch：`feature/ci-cd-hardening`；base_commit：`c6fe450260eeb59d46c9caee591fb1625510b443`
- 最终测试文件指纹（CR-7d，冻结）：
  - `trader/tests/test_cicd_parentfix_offline.py`（65 项）
    `f3d57f83c420023046b3fad089ef82b3031e11b15b565946fc8abb2f54ff97bd`
  - `trader/tests/test_cicd_parentfix_postgres.py`（5 项）
    `613b08d307beebf21c7e0c025277aed8aaf5f35f167f7b2bee73227024982e95`
- source digest（工程师冻结后提供，69 files canonical，
  `artifacts/cicd/source-snapshot.json`；不含本报告与父级元数据）：
  `6590c1e78ed1f8413af9879b8bba5ec10eb227a33f9e751d3c83634a581b3c91`
- workspace_frozen：true（本报告发布后，上述 digest 之外的测试员侧改动视为失效；
  未提交、未推送、未合并、未部署，无 ACL/权限修改，未操作真实账户）
