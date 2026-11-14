# CI 门禁独立测试报告（第 4 轮，最终判读）

- 报告 ID：`ci-gate-tester-r4-20261114`
- 规范：`docs/FOUNDATION_AGENT_WORKFLOW.md` v3 第 5 节
- 分支：`feat/foundation-refactor`
- base：`c6fe450260eeb59d46c9caee591fb1625510b443`
- HEAD：`8f1d76ab7963c7a053014102686d51fd653b9b39`
- 测试员（`agent_id` / `analyzed_by`）：`ec489b02-16ea-4a85-ba34-a9fb9aca0802`
- 结果只发工程师 `89f8ab3c-73ed-41b2-86bc-4b141b4c7c5b`，不发父 Agent
- 结构化报告：[`report.json`](../artifacts/branch-preview-20261112/ci-gate-tester-r4/report.json)；本轮直接检查记录：[`checks-r4.json`](../artifacts/branch-preview-20261112/ci-gate-tester-r4/checks-r4.json)

## 1. 结论

**总体：pass（范围限定于测试员可执行的 T1.1–T5.2 与 G1–G6，绑定最终工作区字节）。**

- 36 文件最终指纹由本轮**独立重算**，四种口径与逐文件哈希全部一致：
  - `source_digest = 924e9319caf4c58221193a450b7dad7cc5f1e1971325ce57f0e1fea839c5c968`
  - canonical `65192e2832d2d815e9526934cb355446de59dc6b6b882dda662347a49bf8e58f`
  - 未归一化 newline `c6511376…`、canonical `3bd09835…`
- 最终字节上的四个必过 Linux job 与独立套件全部通过且零 skip/xfail。
- **不覆盖**：Linux CI runner 的 Postgres 组、远端 GitHub Actions、按 commit 重建的服务器预览、PR #122（见 §7）。
- `workspace_frozen = false`：本轮未写任何源文件且指纹前后一致，但按要求仍需**新一轮只读审核员**绑定同一字节后才算冻结完成。

## 2. 方法与边界

按父级要求**未重跑整套测试**：已收齐的 raw/JUnit 直接判读；仅在能低成本复核真实门禁时重跑。本轮亲自执行的项目：

| 检查 | 直接退出码 | 结果 |
| --- | --- | --- |
| `python -I .../recompute_digest.py` | 0 | 36 文件四口径全部匹配 |
| `python scripts/verify_test_manifest.py` | 0 | 240 个测试文件，各组计数见 §5 |
| isort（CI 真实 scope） | 0 | 空输出 = 通过 |
| black（CI 真实 scope） | 0 | `502 files would be left unchanged` |
| `gh api .../tags/v0.28.0` | 0 | `915b19bbe73b92a6cf82a1bc12b087c9a19a5fe2` |
| PyPI `psutil/7.1.3/json` | 0 | 三个摘要全部匹配 |
| JUnit 重新解析 | 0 | §4 计数全部复核 |

未写入生产代码、工程师测试断言、`ci/test_manifest.json` 其他条目、`scripts/server_egress/`、`deploy/server-egress/`；未提交/推送/部署/改 ACL。

### 2.1 指纹口径合理性的确认

`ci/test_manifest.json` 采用“**去掉测试员自己那一行**后哈希”的约定（`run_extended_linux.py` 的比较约定）。该约定**合理但需两项配套事实**：

1. 原始磁盘摘要单独记录为 `36b971b4559a57c067764200e2c0f8d8cf6dbcfdd08f32baff4596e2b8895d63`；
2. 测试员自有测试文件的正文仍在指纹内（`99c9c4cf…`），因此被归一化的只是那一行分类，测试本身仍被绑定。

## 3. T1 Unix 沙箱资源限制（隔离 Linux 子进程）

全部证据来自 `trader/tests/test_cicd_sandbox_limits_independent.py`（32 个展开用例）与 `trader/tests/test_code_sandbox_unix_limits.py`（7 个），并在 `clean3` 的 offline-only（3327）与 p0+offline（3636）两次运行中均通过。

| 项 | 结论 | 关键证据 |
| --- | --- | --- |
| T1.1 | pass | 实现读 `/proc/self/statm` 第 0 列（VmSize）；探针断言 `fields[0] > fields[1] + 128 MiB/pages`、`_current_address_space_bytes() == vm()`、执行期间 AS soft ≥ 当时 VmSize |
| T1.2 | pass | 只降 soft；hard 前后不变；原有二元组恢复。探针断言 `limits() == before` 且 `during hard == original hard` |
| T1.3 | pass | statm 不可读/空字段均 fail-closed 为 `PROCESS_ADDRESS_SPACE_UNKNOWN`，不改限制、不执行 |
| T1.4 | pass | 更严格调用方 soft 不被抬高；调用方 soft < 当前 VmSize 时以 `PROCESS_MEMORY_SOFT_LIMIT_BELOW_ADDRESS_SPACE` 拒绝 |
| T1.5 | pass | `getitimer` 快照先于 `setrlimit`；快照失败 `SANDBOX_TIMER_SNAPSHOT_UNAVAILABLE` 且进程不变；handler/timer 部分安装失败 `SANDBOX_TIMEOUT_GUARD_UNAVAILABLE`，限制、SIGALRM/SIGXCPU handler 与调用方周期定时器 `(30,7)` 均恢复 |
| T1.6 | pass | 调用方 alarm 保留；`0.15s` 忙循环真实 TIMEOUT，实测耗时 0.05–1.5s，结束无残留 timer |
| T1.7 | pass | 工作线程 `SANDBOX_REQUIRES_MAIN_THREAD`，无任何 `setrlimit` 调用，内核限额前后一致 |
| T1.8 | pass | 每个场景 `subprocess` 子解释器 + `getrlimit`/`getitimer`/statm 内核状态断言，不复用内部计数 |

## 4. T2–T5 与 G1–G6

| 项 | 结论 | 关键证据 |
| --- | --- | --- |
| T2.1 | pass | `conftest._required_groups` 同时保护 required group 与 required subset 成员所在组；`test_required_group_or_subset_cannot_be_excluded[p0/offline/loopback/postgres/ops]`、`test_required_subset_members_cannot_be_excluded` |
| T2.2 | pass | 未知 group/subset、select∩exclude 均被拒；`test_invalid_group_requests_are_rejected` 等 |
| T2.3 | pass | `collect_ignore` 在导入前限定范围；导入 tripwire 证明 external 模块未被导入；必过 job `--require-zero-skipped --require-zero-xfailed` 退出码 0 |
| T2.4 | pass | 本轮实跑 manifest 退出码 0，240 文件：p0=17、offline=191、loopback=2、postgres=24、ops=4、external=2 |
| T3.1 | pass | 本轮独立核对 PyPI 三摘要一致；工程师 raw 输出显示 Linux/Windows `--require-hashes` 退出码 0、psutil 7.1.3（安装为工程师代跑） |
| T3.2 | pass | 本轮独立 `gh api` 确认 `v0.28.0 → 915b19bb…`；`release-build.yml:104,111` 固定该 SHA，`:117` 保留 `exit-code: "1"` |
| T4.1 | pass | Compose 实测 39/168/168；校验器拒绝未加引号、接受单引号与 `$$` |
| T4.2 | pass | 占位符/弱口令/非 loopback/非 40hex commit/非 `sha256:<64hex>` 全部拒绝（9 参数独立用例 + 8 个契约用例） |
| T5.1 | pass | 逐条比对 HEAD：3 处路由断言改读 OpenAPI（路径断言原样保留）；quantstats 仅把自相矛盾的 `/tmp` 断言收窄到 Windows，主断言保留；reconciler 用固定时钟且期望值不变；group 列表**新增** loopback；`**kwargs` 对应真实的 `_start_tunnel(..., *, host_key_alias="")`。无删断言、无放宽、无新增 skip/xfail |
| T5.2 | pass | `loopback-gate` 加入 `ci-required.needs`，manifest 中 required=true；offline 仍为 `QTS_TEST_NETWORK_POLICY=offline` |
| G1 | pass | clean3：p0 309、offline 3636、loopback 66、research-ai 217、offline-only 3327，全 0 fail/0 skip/0 xfail；coverage 10/10 门槛（overall 84.58%）；Windows PG 180/0/0/0、phase6-guard 18/0/0/0 |
| G2 | pass | 期望来自内核状态、pytest 退出码与契约，未从被测实现生成答案 |
| G3 | pass | 必需检查真实运行；零 skip/xfail 门禁退出码 0；无删断言/宽泛吞错 |
| G4 | pass | 分支/base/HEAD 一致；36 文件指纹本轮独立重算一致；完整改动清单见 §6 |
| G5 | pass | 本轮仅写报告与 `ci-gate-tester-r4/`；无越权、无提交/部署/凭据操作；指纹前后一致 |
| G6 | pass | `PROJECT_ARCHITECTURE.md` 已列六个 group、loopback 必过、收集前限定与 subset 保护；预览/运维手册与实现一致；格式门禁已纳入 `validate_preview_env.py` |

## 5. 最终字节证据盘点

- 四个必过 job（`clean3/`）：p0 309/0/0/0；offline 3636/0/0/0 + coverage 10/10；loopback 66/0/0/0；research-ai 217/0/0/0；另有 offline-only 3327/0/0/0。
- 格式与 manifest：`black-all.txt` 488（trader-only）与本轮 CI 真实 scope 502 均 exit 0；`isort` 空；`manifest-gate.txt` ok。
- 独立用例：32（T1）+ 7（Unix 限额）+ 9（分组排除）+ 8（预览校验）在 clean3 两个运行中全部 pass。
- Windows：Postgres 180/0/0/0、phase6-guard 18/0/0/0、sandbox+gates 63（**1 个诚实 POSIX-only skip**、0 fail）。
- **过期产物**：root 级 `junit-offline.xml` 为 3664 pass / 3 fail 的 pre-fix 产物，不得引用；权威离线证据为 `clean/`(3601)、`continued/`(3636)、`clean3/`(3636)。

## 6. 改动集与指纹说明

- **36 文件指纹集**：见 `report.json.changed_files` 前 36 项；含生产、测试、CI、契约文档与新模块。
- **指纹外但已披露**：`docs/PROJECT_ARCHITECTURE.md`（纯文档，G6 契约文档，已重读，不参与 `source_digest`）。
- **运维元数据**：`docs/CI_GATE_INDEPENDENT_TEST_REPORT.md`、`docs/FOUNDATION_WORKFLOW_STATE.json`、各阶段独立测试报告，按要求不进源摘要。
- **并行工作流**：`deploy/server-egress/`、`scripts/server_egress/`、`docs/SERVER_EGRESS_RUNBOOK.md` 属网络工程师，未纳入指纹、未审阅。

## 7. 未执行项与限制（不得写成已通过）

1. **Linux CI runner 的 Postgres 组未验证**：本地 Linux 为 179/1/0/0，唯一失败 `test_cicd_parentfix2_postgres.py::TestVerifyDeployGateLiveReport::test_live_report_generated_through_the_real_psql_path` 因本地无 Docker daemon 报 `Cannot connect to the Docker daemon at unix:///var/run/docker.sock`；需远端 `postgres-integration` 真实 service container 运行。
2. **远端 GitHub Actions** 未在推送 commit 上重跑（父级职责）。
3. **服务器预览**：按 commit 冻结重建镜像、镜像 ID 绑定、登录/SPA/API/版本验证未执行（父级职责）。
4. **PR #122** 必过 CI 全绿与合并未执行（父级职责）。
5. Windows 恰有 1 个诚实 POSIX-only skip（`test_execution_restores_real_process_resource_limits`）；**不要把 Linux 零 skip 说成“全部平台零 skip”**。
6. 历史 Windows 3643/0/0/0 与 coverage 80.38% 是父级历史证据，不是当前字节的 Unix 证据。
7. 本地预览 smoke（登录 200、SPA 深链 200、`/health/trading` 503 真实阻断、6 容器 0 凭据、无 execution/backup）为**本地一次性栈**，不等于服务器验证。
8. T3.1 的 pip 安装为工程师代跑；本轮核对 raw 输出与 PyPI 摘要，未重跑 pip。

## 8. 对上一轮审核员 `f63524b8`（overall=fail）的独立处置

| 上轮 blocking finding | 本轮处置 |
| --- | --- |
| digest 未绑定测试员批准字节 | **已关闭**：本轮独立重算四口径与逐文件哈希，均匹配当前字节 |
| `analyzed_by=null` | **已关闭**：本报告 `analyzed_by` 为本轮测试员，且逐项直接判读 raw 证据 |
| Linux runner Postgres 未验证 | **保留**：列入未执行项（环境阻塞，非代码失败） |
| 远端 Actions/preview/PR 未执行 | **保留**：父级职责，列入未执行项 |
| black 门禁 | 本轮已用 CI 真实 scope 复跑：exit 0、502 files |
| T3/T4 raw evidence | 上轮已关闭；本轮再次独立核对 PyPI 与 Trivy |

## 9. 后续

- 工程师可据此创建**新一轮只读审核员**绑定最终字节；审核员通过后方可由工程师一次性 handoff 父 Agent。
- 本报告不构成阶段完成声明：远端门禁与服务器预览仍由父级在提交后执行。
