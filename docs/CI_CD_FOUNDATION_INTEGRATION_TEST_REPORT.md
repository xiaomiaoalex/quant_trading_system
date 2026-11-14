# CI/CD × 基础重构整合阶段：独立测试报告（测试员）

- 角色：全新独立测试员（verified-phase-workflow v3，父 Agent `e36d6105-e0bb-4f27-bbbd-225b5a55da2e` 派生，工程师 `c4f927bf-7b6b-4747-af1a-9f2d83fd98f1` 协调）。
- 工作目录：`D:/Code of Python/quant_trading_system/.worktrees/cicd-foundation-integration`（分支 `integration/cicd-foundation`，未提交 merge working state）。
- 依据（先于实现结论阅读）：父工作单 `artifacts/cicd/integration-20261111/parent-integration-work-order.md`、`AGENTS.md`、`docs/FOUNDATION_AGENT_WORKFLOW.md`、合并版 `docs/UNIFIED_TRADING_ACCEPTANCE.md`、合并后源码（`scripts/observe_trading_runtime.py`、`scripts/summarize_observation.py`、`scripts/release_binding.py`、`trader/trading/{service,models,core}.py`、`ci/test_manifest.json`、`ci/coverage_thresholds.json`、`compose.yml`、`deploy/compose.demo.yml`、`Dockerfile`、`scripts/verify_deploy_gate_live.py`）。

## 1. 环境事件（如实披露）

- 测试员命令通道在启动前即失败：`pwsh` 报 `SetNamedSecurityInfoW failed (Win32 5): grantWrite(...)`（与 workflow §7.5 同类）。测试员未修改 ACL、未提权、未改用不受限终端；全部工作仅使用文件工具完成（依据 workflow §2.1）。因此 `black --check --diff`/`isort`/pytest 均无法由测试员亲自运行，按 §2.1 冻结后发 `kind=execution_request` 由工程师代跑。

## 2. 设计（authored_by=测试员，先于执行）

### 2.1 既有 CI/CD 测试的最小适配（不改断言语义、不 skip/xfail、不删断言）

| 文件 | 适配点 |
|---|---|
| `trader/tests/test_cicd_independent_offline.py` | 3 个 `create_oms_callback` 用例改为统一入口 `TradingService.record_execution(Execution)`：同 exec_id 6 路并发只入账一次（1 True/5 False、executions==1、ledger 恰 3 条腿、USDT −40.1/BTC +0.4 守恒、无 `EXECUTION_KEY_CONFLICT`）；24 个不同 exec_id 并发全部入账（booked_qty 0.24、FILLED、仅成交腿净额 USDT −24.6/BTC +0.24，不重复不丢失）；完成后重放同 exec_id 返回 False 且 ledger 无新增。断言数从磁盘推导：`total_test_files == sum(groups.values())`（原 `>= 244` 硬编码下限移除）。 |
| `trader/tests/test_cicd_parentfix_offline.py` | L1408 `>= 256` 改为磁盘推导（`len(on_disk)==len(classified)>0`）；唯一分类/无重复/无失效断言原样保留。 |
| `trader/tests/test_cicd_parentfix2_offline.py` | observer argv `--password` → `--password-file`（tmp 密码文件）；observer 输出解析只取 `kind=="sample"`（marker/transition 不再计入窗口样本），并断言 marker 存在；`==263` 改为磁盘推导（`set(classified)==on_disk`，无失效文件断言保留并加强）。窗口手工推导的样本数/跨度断言（1441/1373/5 样本等）原样保留——phase 6 observer 的 deadline 锚定与请求计数与旧实现一致。 |
| `trader/tests/test_cicd_parentfix2_postgres.py` | `TestVerifyDeployGateLiveReport` 不再依赖未跟踪 `artifacts/cicd/raw/deploy-gate-live.json`：测试内 `subprocess` 调 `python scripts/verify_deploy_gate_live.py --output <tmp>/deploy-gate-live.json --project qts-cicd-livepg-<uuid12> --port <bind-only 探测的空闲回环端口>`，生成后断言（facts/pre_gate_errors/psql t-f 渲染/teardown==0 原样保留）。任务自有 Compose/端口、脚本自 teardown；Docker 不可用 → 断言失败（不 skip）。 |

### 2.2 本阶段新测试 `trader/tests/test_integration_cicd_foundation_offline.py`（分组建议：offline，工程师已加入 manifest，总数 236）

1. `test_merged_observer_output_passes_the_strict_release_gate`：真实 `observe_trading_runtime.main`（phase-6 `--password-file`、fake clock/fake httpx，24h@60s，1 个 503 降级轮）产出真实 JSONL（1441 个 `kind:"sample"` + `observer_started/observer_finished` marker + 2 条 ready transition，均带 runtime 身份/monotonic/attempts）；按运维惯例在窗口内补 drill marker（runtime_restart、runtime_restart_end、active_reconnect）；`build_binding` 构造与真实 collector 同构的 release binding；跑真实 `summarize_observation.main --strict-release`，断言 `ok=true、blockers=[]、parse_errors=0、sample_identity_checked=true、duration_hours>=24、ready_ratio<1`（证明 marker/transition/降级样本未被计入样本集）。
2. `test_strict_gate_rejects_a_sample_served_by_a_different_runtime`：篡改一个样本的 `runtime.commit` 后 strict 门禁必须以 `SAMPLE_RUNTIME_IDENTITY_MISMATCH:commit` fail-closed（exit 1）。
3. `test_manifest_uniquely_classifies_every_current_test_file`：manifest 唯一分类 == 磁盘全集（无 unclassified/stale/重复），数量从磁盘推导（当前 236），并自锚 `test_integration_cicd_foundation_offline.py == offline`。
4. `test_coverage_thresholds_dropped_the_deleted_modules_not_the_merged_bar`：只解析 `thresholds[*].path`（notes 里的历史性文字不算条目），两个已删除模块不在 floor 列表、文件确已不存在、每个非 overall floor 指向现存路径、overall floor ≥75%、`trader/trading/service.py` 与 `trader/trading/` 仍在守护。
5. `test_demo_overlay_and_dockerfile_keep_the_single_foundation_stack`：静态解析 compose.yml + deploy/compose.demo.yml（自定义 Loader 折叠 `!reset`/`!override` 标签）：overlay 服务集 == foundation 基础服务集（无第二套栈、唯一 postgres）；全部 app 服务同一 Dockerfile/context；overlay 每个 app 服务 `build` 为 null（禁重建）、镜像锚定 `QTS_RELEASE_IMAGE`、demo 标签、不覆盖 command；compose/overlay/Dockerfile 均不引用 `oms_callback`/`core.application.oms`。

不重复既有测试：`test_cicd_oms_ws_rest_fill_concurrency.py`（P0）已覆盖 2 路并发同 fill；本文件新测试针对合并后的 observer→summarize strict 链、manifest 磁盘推导分类、coverage floor 去留与单栈一致性，与其余 CI/CD 既有断言互补不重叠。

### 2.3 预授权写入范围

`trader/tests/test_cicd_independent_offline.py`、`trader/tests/test_cicd_parentfix_offline.py`、`trader/tests/test_cicd_parentfix2_offline.py`、`trader/tests/test_cicd_parentfix2_postgres.py`、`trader/tests/test_integration_cicd_foundation_offline.py`、本报告、`artifacts/cicd/integration-20261111/tester-integration/`。未改生产代码/契约/其他测试/manifest（manifest 由工程师加入新文件，236）。

## 3. 执行（executed_by=工程师，代跑；测试员逐项核对原始证据）

执行时间 2026-11-11 21:00–21:05（本地），产物：worktree `artifacts/cicd/integration-20261111/tester-integration/`（未跟踪）。测试员独立读取了每个 exit 文件、4 份 JUnit 的 `<testsuite>` 头、black 日志与 offline 全量进度日志，核对结果：

| 步骤 | 命令（工程师报告的精确 argv） | exit | JUnit 计数（测试员核对） |
|---|---|---|---|
| fmt | `black --check --diff` + `isort --check-only`（5 个测试员自有文件） | black=0、isort=0（`fmt.exit.txt`；black 日志 "5 files would be left unchanged"） | — |
| offline 全组 | `QTS_TEST_GROUP=offline QTS_TEST_NETWORK_POLICY=offline`（CI 实际命令，非 `-m`） | 0 | `junit-offline-full.xml`：tests=3325，failures=0，errors=0，skipped=0；进度日志 46×72+13=3325 全为点号，无 F/E/s |
| P0 全组 | 同 CI 命令 | 0 | `junit-p0.xml`：tests=309，0F/0E/0S |
| research_ai | `QTS_TEST_SUBSET=research_ai` | 0 | `junit-research-ai.xml`：tests=217，0F/0E/0S |
| PG 单文件 | DSN `127.0.0.1:55436`，`trader/tests/test_cicd_parentfix2_postgres.py` | 0 | `junit-postgres-single.xml`：tests=2，0F/0E/0S；含 `test_live_report_generated_through_the_real_psql_path`（10.29s，真实 docker compose exec/psql 路径）与 layer-2 SQL 用例 |

- 测试员另以 grep 在 `junit-offline-full.xml` 中确认新文件 5 个用例与 `test_cicd_independent_offline.py` 适配用例（record_execution 并发/去重/重放）均在 offline 全组中运行且无失败/跳过标记。
- 执行时文件指纹（`fingerprints.txt`，executed_by 记录）：新文件 `29cce15f…`、independent `df1874f0…`、parentfix `e32fd38f…`、parentfix2 offline `cb234380…`、parentfix2 PG `c19f5df2…`、manifest `3cc3c564…`（manifest 含新文件，总数 236）。测试员在冻结后未再改动任何源文件。
- `git status`（工程师报告）：merge working state 未提交，无越界源文件；测试产物均在未跟踪 artifacts 下。

## 4. 已知限制与未执行项

- 测试员未能亲自运行任何命令（§1 环境事件）；所有执行均由工程师代跑，本报告区分 authored/executed/analyzed，不将代跑写成测试员亲自运行。
- offline/p0/research_ai 的日志文件只含 pytest 进度/stdout，精确 argv 以工程师执行报告为准；JUnit 的 testsuite 头与 exit 文件为测试员亲自读取核对的第一手证据。
- 测试员 sandbox 无法写 root `artifacts/`，执行_request/产物清单保存在 worktree 未跟踪 `artifacts/cicd/integration-20261111/tester-integration/`（root 侧归档由工程师完成）。
- 远端 GitHub Actions、GHCR、真实 Demo 部署与当前合并版本的 24h 观察窗均未执行；`docs/UNIFIED_TRADING_ACCEPTANCE.md` 第五轮的 Demo/窗口证据属 2026-11-01/02 历史代码与环境，不得冒充本合并版本的新证据（父工作单验收口径）。

## 5. 测试员结论（kind=test_final_report 的正文依据）

- 独立测试通过：fmt（black/isort）+ offline 全组 3325 + P0 全组 309 + research_ai 217 + PG 单文件 2，全部 0 failed / 0 error / 0 skipped / 0 xfail，exit 0，JUnit 与原始日志保存在 `artifacts/cicd/integration-20261111/tester-integration/`。
- 独立测试设计覆盖了合并后的 observer→summarize strict 兼容链、runtime 身份 fail-closed、manifest 磁盘推导唯一分类（236）、coverage floor 去留与 demo overlay 单栈一致性；未删除、未弱化、未 skip 任何既有断言。
- 本报告仅为测试员阶段的独立测试证据；阶段最终交付仍需全新只读审核与父 Agent 验收，不构成提交/合并/部署授权。
