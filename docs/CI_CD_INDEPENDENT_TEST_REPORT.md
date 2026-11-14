# CI/CD 专项独立测试报告（workflow v3）

- `report_id`: `cicd-independent-test-report-001`
- `workflow_version`: 3（`phase=cicd-hardening`，`role=tester`）
- `authored_by`: tester（agent `e4b6e317-d4c1-4cd1-8687-8d3b91e6cbe0`）
- `executed_by`: engineer（agent `c4f927bf-7b6b-4747-af1a-9f2d83fd98f1`；本测试员无命令执行通道，全部命令按 `execution_request` 由工程师代跑）
- `analyzed_by`: tester（所有结论仅基于工程师回传的原始 stdout/stderr、退出码与 JUnit，不采信口头"已通过"）
- `base_commit`: `c6fe450260eeb59d46c9caee591fb1625510b443`（branch `feature/ci-cd-hardening`，worktree `.worktrees/cicd-hardening`）
- `source_digest`: `6590c1e78ed1f8413af9879b8bba5ec10eb227a33f9e751d3c83634a581b3c91`
  （69 files，最终冻结值；snapshot：`artifacts/cicd/source-snapshot.json`。R2-F-P3 修复后最终
  集成回归 offline 3554 / P0 367 / research_ai 252 / postgres 261，全部 0 failed / 0 skipped
  / 0 xfailed；R2-F-P3-001/002 已由第 2 位审核员确认关闭。测试员两文件 SHA256 见 §2，
  均为最终版）
- 工作单引用 SHA256: `e3c0fb841b33b4589142e11690088bce9d2ec117b4b675619e7f8ba79ec25e57`
- `overall`: **pass（限定于本报告实际执行的独立检查；未执行项见 §7，不据此外推）**

## 1. 方法与边界

- 先依据工作单 C1–C6、RFC、契约（`INTERFACE_CONTRACTS.md` 13.17）与手算期望编写测试设计
  `artifacts/cicd/tester/test-design.md`（10 项矩阵），随后才实现测试；全程未阅读生产实现之外
  的任何"通过声明"作为依据。
- 测试均为行为级反例：直接驱动真实入口（`verify_test_manifest.main`、`check_junit.main`、
  `deploy_preflight.evaluate/main`、`summarize_observation.main`、`release_manifest.build_manifest`、
  `compose_smoke.evaluate_evidence`、`create_oms_callback`、`initialize_trading_schema`、conftest
  hooks、CI 内嵌聚合脚本），而不是以 YAML 可解析、仅 config 存在或历史 artifacts 代替验证。
- 执行通道：Windows 沙箱下 pwsh/pytest/docker 均不可用（ACL provisioning 失败，且按 v3 禁止
  提权/不受限终端）。两份测试文件写好后冻结（SHA256 见 §3），以五条 `execution_request`
  （`tester-exec-req-002` … `-005`，含 F-P 轮）交工程师代跑；`authored_by`/`executed_by`/
  `analyzed_by` 三方分离。
- 只写入了允许路径：`trader/tests/test_cicd_independent_offline.py`、
  `trader/tests/test_cicd_independent_postgres.py`、`artifacts/cicd/tester/*`（工程师代跑产物）、
  本报告。未触碰生产代码、既有测试、`ci/test_manifest.json`、workflows、契约与文档。

## 2. 独立测试文件

- `trader/tests/test_cicd_independent_offline.py` — offline 组，85 个用例（R1 按父级 F-P 新契约
  重写并新增反例；R2 加严轮再适配 strict binding 实际事实与 workflow 输入仅经 step env，并新增
  `deploy_gate.evaluate` 独立反例，见 §4bis 与 §5#10）。
- `trader/tests/test_cicd_independent_postgres.py` — postgres 组，8 个用例（R1/R2 两轮零改动）。
- 两文件均为预分类（manifest offline / postgres 组），不引入 skip/xfail；PG 迁移用例中仅
  `pytest.importorskip("asyncpg")`，在 `REQUIRE_POSTGRES=1` 下会被 conftest 改写为 failed。

### 冻结指纹（executed_by=engineer 回传）

| 文件 | SHA256 | 备注 |
| --- | --- | --- |
| `trader/tests/test_cicd_independent_offline.py` | `e3d8ef785f21ad36e78079dd09aa2795baf3242204d2999cca6dc76d5b1fd937` | R2 加严轮最终版（此前：R1 `9eb5ec42…`、初版 `f4967200…`） |
| `trader/tests/test_cicd_independent_postgres.py` | `d44e0fc991730ecbd2f45466a4758bc446ebd76761097c29345a9aa4565dec86` | 全程零改动 |

## 3. 执行命令与退出码（executed_by=engineer）

| # | 命令（cwd = worktree 根） | 退出码 | 结果 | 产物 |
| --- | --- | --- | --- | --- |
| 1 | `python -m black --check --line-length 100 trader/tests/test_cicd_independent_offline.py trader/tests/test_cicd_independent_postgres.py` | 0 | 2 files unchanged | `artifacts/cicd/tester/fmt-black-final.log` |
| 2 | `python -m isort --check-only --profile black trader/tests/test_cicd_independent_offline.py trader/tests/test_cicd_independent_postgres.py` | 0 | 通过 | `artifacts/cicd/tester/fmt-isort-final.log` |
| 3 | `QTS_TEST_GROUP=offline QTS_TEST_NETWORK_POLICY=offline python -m pytest -q trader/tests/test_cicd_independent_offline.py --tb=short --junitxml=artifacts/cicd/tester/offline-independent.xml` | 0 | **73 passed / 0 skipped / 0 xfailed** | `artifacts/cicd/tester/offline-independent.xml`、`.log` |
| 4 | `REQUIRE_POSTGRES=1 POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:55435/trading QTS_TEST_GROUP=postgres QTS_TEST_NETWORK_POLICY=loopback python -m pytest -q trader/tests/test_cicd_independent_postgres.py --tb=short --junitxml=artifacts/cicd/tester/postgres-engineer-run.xml` | 0 | **8 passed / 0 skipped / 0 xfailed** | `artifacts/cicd/tester/postgres-engineer-run.xml`、`.log` |
| 5 | 完整 offline 组（含本测试员文件）：`QTS_TEST_GROUP=offline QTS_TEST_NETWORK_POLICY=offline python -m pytest -q trader/tests --junitxml=artifacts/cicd/raw/offline-final3.xml` | 0 | 3315 passed / 0 skipped / 0 xfailed | `artifacts/cicd/raw/offline-final3.xml` |
| 6 | 完整 postgres 组（含本测试员文件）：`REQUIRE_POSTGRES=1 … QTS_TEST_GROUP=postgres QTS_TEST_NETWORK_POLICY=loopback python -m pytest -q trader/tests --junitxml=artifacts/cicd/raw/postgres-final2.xml` | 0 | 254 passed / 0 skipped / 0 xfailed | `artifacts/cicd/raw/postgres-final2.xml` |
| 7 | 接线修复后复跑（digest `deb9f2f7…`）：`QTS_TEST_GROUP=offline QTS_TEST_NETWORK_POLICY=offline python -m pytest -q trader/tests/test_cicd_independent_offline.py --tb=short --junitxml=artifacts/cicd/tester/offline-independent-postfix.xml` | 0 | 73 passed / 0 skipped / 0 xfailed | `artifacts/cicd/tester/offline-independent-postfix.xml` |
| 8 | 接线修复后最终组合 P0+offline（coverage 79.5%） | 0 | 3682 passed / 0 skipped | `artifacts/cicd/raw/combined-final2.xml`、`artifacts/cicd/raw/coverage-final2.xml` |
| 9 | 第 1 轮审核修复后目标回归（含本测试员 offline 文件）：`QTS_TEST_GROUP=offline QTS_TEST_NETWORK_POLICY=offline python -m pytest -q <目标文件集> --junitxml=artifacts/cicd/raw/fix-round-targeted.xml` | 0 | 111 passed / 0 skipped / 0 xfailed（其中本测试员 offline 73 passed） | `artifacts/cicd/raw/fix-round-targeted.xml` |
| 10 | 修复后 PG 独立文件复跑（同表 3 #4 命令） | 0 | 8 passed / 0 skipped / 0 xfailed | `artifacts/cicd/tester/postgres-engineer-run2.xml` |
| 11 | 修复后 `python scripts/verify_test_manifest.py`；`black --check`/`isort --check-only`/`mypy`（工程师代跑） | 0 | manifest ok=true；三工具 exit 0 | 工程师回传（tester-exec 复验轮） |
| 12 | F-P 轮 offline 独立文件（execution_request 004，含 3 个 fixture 冲突失败）：`QTS_TEST_GROUP=offline QTS_TEST_NETWORK_POLICY=offline python -m pytest -q trader/tests/test_cicd_independent_offline.py --tb=short --junitxml=artifacts/cicd/tester/offline-independent-fp.xml` | 1 | 82 collected / 79 passed / 3 failed / 0 skipped（失败均为测试员 `_release_args` 的 `dist` 目录重复创建，已由测试员修正） | `artifacts/cicd/tester/offline-independent-fp.xml`、`.log`、`tester-black-fp.diff` |
| 13 | F-P 轮 PG 独立文件（DSN 端口实测 `127.0.0.1:55436`，其余同 #4） | 0 | 8 passed / 0 skipped / 0 xfailed（文件零改动，SHA256 `d44e0fc9…`） | `artifacts/cicd/tester/postgres-independent-fp.xml`、`.log` |
| 14 | F-P 轮最终格式门（execution_request 005） | 0 | black 2 files unchanged；isort 通过 | `artifacts/cicd/tester/fmt-black-fp2.log`、`fmt-isort-fp2.log` |
| 15 | **F-P 轮最终 offline 独立文件**：`QTS_TEST_GROUP=offline QTS_TEST_NETWORK_POLICY=offline python -m pytest -q trader/tests/test_cicd_independent_offline.py --tb=short --junitxml=artifacts/cicd/tester/offline-independent-fp2.xml` | 0 | **82 passed / 0 skipped / 0 xfailed** | `artifacts/cicd/tester/offline-independent-fp2.xml`、`.log` |
| 16 | R2 加严轮格式门（execution_request 006）：black/isort（两测试文件） | 0 | 2 files unchanged；通过 | `artifacts/cicd/tester/fmt-fp3.log` |
| 17 | **R2 加严轮最终 offline 独立文件**（同 #15 命令，`--junitxml=artifacts/cicd/tester/offline-independent-r2-final.xml`） | 0 | **85 passed / 0 skipped / 0 xfailed** | `artifacts/cicd/tester/offline-independent-r2-final.xml`、`.log` |
| 18 | R2 加严后全量回归（offline / P0 / research_ai / postgres 四组，含 parentfix 两代测试员文件） | 0 | **3443 / 367 / 252 / 261 passed，全部 0 failed / 0 skipped / 0 xfailed** | 工程师回传（tester-exec-req-006） |

## 4. 十项重点覆盖与结论

| # | 重点 | 独立验证方式（摘要） | 结论 |
| --- | --- | --- | --- |
| 1 | 假绿防线 | 真实树删条目/重复/失效路径/缺组/空组/缺 CI Job/subset 失效 → `verify_test_manifest.main()==1` 且错误码逐条断言；真树 `main([])==0` 且 244+ 文件唯一分类；新建未分类文件后子进程 pytest `--collect-only` 实测 **exit 4** 并在 finally 中删除；conftest hook 单测（未分类 UsageError、未知组 UsageError、逗号组 `p0,offline`）；对 ci-gate 内嵌聚合脚本以真实 `NEEDS_JSON` 执行子进程：failed/skipped/cancelled/null 全部非零且含 `CI_REQUIRED_FAILED`；needs 漏项/optional 混入/非测试 Job 缺失/声明非法四类反例逐条命中 checker 错误码；`check_junit` 对 skipped/xfail/0 tests/红 JUnit 拒绝、对绿 0-skip 接受 | **PASS** |
| 2 | 网络策略 | offline 进程内实测公网 connect、DNS、loopback connect 全部 `PermissionError(QTS_NETWORK_BLOCKED)`，`socketpair`（asyncio self-pipe）可用；loopback 进程内实测任务 DB `127.0.0.1:55435` 可达、公网/DNS 被拒 | **PASS** |
| 3 | PG fail-closed | 子进程实测：无 DSN → exit 4 且含 `no PostgreSQL DSN is configured`；不可达 DSN → exit 4 且含 `unreachable or the DSN is invalid`；hookwrapper 单测：`REQUIRE_POSTGRES=1` 下 postgres skip 改 failed 且消息含 nodeid，`=0` 或非 postgres 项保持 skipped | **PASS** |
| 4 | OMS 并发去重 | 走真实 `create_oms_callback` + `ControlPlaneInMemoryStorage`：同 `cl_ord_id+exec_id` 6 任务并发 → 1 execution、1 fill、`filled_qty=0.4`、PARTIALLY_FILLED、`exec_dedup_hits>=5`；24 笔不同 exec 并发（12×0.01@50000 + 12×0.01@51000）→ 24 executions/24 fills、`avg_price=50500`、FILLED；完成后重放同一 exec 再投递仍 1 次 | **PASS** |
| 5 | Compose 证据判定 | 对 `compose_smoke.evaluate_evidence` 全负因变异：容器 health 非健康/缺失、语义失败、残留容器/卷、cleanup 失败、identity 变更、digest 失效、凭据泄漏/引用缺失、拒绝证据缺 reason/错误 exit code、告警失败、早期 `reason` 不得掩盖其余失败 | **PASS** |
| 6 | 观察门禁 | `summarize_observation.main()`（argv 注入）覆盖 READY_RATIO_BELOW_THRESHOLD、RECONCILIATION_AGE_UNKNOWN/EXCEEDED、OUTBOX_BACKLOG_AT_END、UNBOOKED_FILLS_AT_END、DUPLICATE_OR_RESEND_EVIDENCE（含字符串 `"1"`/bool/`"0"` 规范化）、NON_MONOTONIC_SAMPLES、SAMPLE_GAP_EXCEEDED、WINDOW_SHORTER_THAN_MIN、NOT_READY_AT_END、MISSING_MARKER:runtime_restart、strict-release 的 evidence 缺失/commit/digest/env/release_id 不匹配；显式阈值可放宽且记录 `max_allowed_*`；历史无 age 证据不虚构 blocker | **PASS** |
| 7 | Preflight | 合法 Demo 环境手算构造 → `evaluate(...).ok is True`；逐项注入失败断言稳定 (code,key)：非 demo、默认/占位密码、AUTH 关闭、账户非专用、symbols/instruments 失配、warmup 0、内网告警、DR 关闭/RPO 超限/未 attested、digest 不匹配/可变 tag、schema/rollback 证据缺失·不 expand_only·rollback digest 错、release manifest 三重不匹配；13.17 契约区分 `DR_EVIDENCE_PATH_MISSING`（未配置）与 `DR_EVIDENCE_MISSING`（已配置缺失）；host_evidence 双模式：远端 deferred → ok 且仅 `HOST_EVIDENCE_DEFERRED` 警告（R2 起未传 release manifest 时另有 `RELEASE_MANIFEST_NOT_PROVIDED` 建议性警告，用例已容忍），同环境完整预检 fail，互不冒充；报告与 CLI stdout 均不泄漏 4 个注入秘密 | **PASS** |
| 8 | 迁移安全 | 静态：`MIGRATIONS` 有序唯一 [2,3]，两 SQL 去注释后无 `DROP/TRUNCATE/ALTER COLUMN/DELETE FROM`（检测器先用 6 类破坏性语句自证非空洞）；动态：任务自有容器 `127.0.0.1:55435` 内每用例新建 `qts_cicd_tester_<hex12>` schema（pool `search_path` 隔离），v2 种子六表各 1 行 → 升 v3 后六表 `to_jsonb` 行集 SHA256 逐一不变、`trading_order_projection` 出现、`trading_schema_migrations` checksum 与迁移文件字节 SHA256 一致；v3 后以 v2 runtime 初始化 → `RuntimeError("newer")`，行集不变、版本仍 3、两条迁移记录未回滚；finally DROP schema 并断言已消失 | **PASS** |
| 9 | Release 证据链 | `release_manifest.build_manifest`：依赖文件 SHA256 与 `hashlib` 重算一致、可变 tag/短 commit `SystemExit`、目录树摘要确定性且内容变化即变、`non_claims` 存在且无秘密串；两份 constraints 逐条 `==` 且带 `--hash=sha256:<64hex>`（runtime 39 项），无占位符；demo-evidence workflow 的 `release_build_run_id` 下载 manifest 并以 `EVIDENCE_MANIFEST_MISMATCH` 校验 release_id/commit/digest，secret 全部 `DEMO_*` 作用域 | **PASS** |
| 10 | 文档/契约一致性 | ruleset JSON：唯一 required check `CI Required`、strict 同步、评审≥1、stale 评审驱逐、禁 deletion/non_fast_forward；`RULESET_MIGRATION.md` 明示"未修改远端规则"与远端 ruleset `13369928` 事实；runbook 含 `--strict-release` 与"禁止自动回滚"；RFC §6.3 未执行清单存在且声明"未执行项不得按通过处理"；demo overlay `build: !reset null`、`${QTS_RELEASE_IMAGE:?}`、硬编码 `BINANCE_ENV: demo`、`QTS_COMPOSE_PROJECT:?`，`demo.env.example` 秘密全占位且无真实 bcrypt；deploy-demo 默认 dry_run=true/confirm_deploy=false，preflight `--no-host-evidence` 而 deploy job 完整预检；optional-gates 无 `continue-on-error`；ci-gate 去注释后无 `packages: write` 且只读 | **PASS** |

### 4bis. 父级 F-P 门禁补充矩阵（R2 加严后全绿证据：`artifacts/cicd/tester/offline-independent-r2-final.xml`，85 passed；R1 版 82 passed 见 `offline-independent-fp2.xml`）

| F-P | 门禁 | 独立反例（本测试员 offline 文件） | 结论 |
| --- | --- | --- | --- |
| F-P1 | strict 观察窗口 ≥24h（`--strict-min-hours` 默认/下限 24） | 24h 窗恰达下限 → 0 blocker 且 `strict_min_hours==24`、`effective_min_hours==24`；4h 窗 → `STRICT_WINDOW_SHORTER_THAN_24H`+`WINDOW_SHORTER_THAN_MIN`；`--strict-min-hours 12` → argparse exit 2；workflow 层 `OBSERVATION_HOURS_BELOW_24` 断言 | **PASS** |
| F-P2 | strict 逐样本显式指标 + binding 实际事实（R2 加严：`scripts/release_binding.py`） | 逐键断言：缺 `outbox_backlog` → `STRICT_METRICS_MISSING:outbox_backlog`；字符串 `"2"` → `STRICT_METRICS_INVALID:unbooked_fills`；`metrics=None` → 全键 MISSING；R2 后 binding 须携带实际事实：`errors` 非空 → `RELEASE_RUNTIME_ERROR_PRESENT:*`、`generated_at` 非法 → `RELEASE_RUNTIME_GENERATED_AT_INVALID`、容器 present/running/health/image/labels 逐项变异 → `RELEASE_RUNTIME_CONTAINER_*/LABEL_MISMATCH:*`、health 三探针 → `RELEASE_RUNTIME_HEALTH_*`、身份块缺失/漂移/无 owner → `RELEASE_RUNTIME_IDENTITY_*/OWNER_MISSING`；窗口锚定：binding 生成时间早于窗口或远超窗口+1h → `RELEASE_BINDING_WINDOW_MISMATCH`（样本锚定真实 epoch ms 2026-11-04T00:00Z 起） | **PASS** |
| F-P3 | release manifest 必需证据齐全 + gate facts 必需（R2 加严：`deploy_gate.evaluate`） | 缺 coverage → `RELEASE_EVIDENCE_MISSING: coverage`；全证据齐 → 各 evidence present 且无 `--allow-missing-evidence`；`--allow-missing-evidence` 放行且缺省段不虚构；R2 后 gate facts 缺失即失败：`queued_commands=2`/`sending_commands=None`/缺 `unbooked_fills` → `COMMANDS_NOT_CONVERGED:QUEUED`+`FACTS_INVALID:*`；lease/runtime owner 错位 → `EXECUTION_OWNER_NOT_ALIGNED`；`new_risk_blocked=False` → `NEW_RISK_NOT_BLOCKED`；post 标签/digest 漂移 → `LABEL_RELEASE_COMMIT_MISMATCH`/`IMAGE_DIGEST_MISMATCH`；trading 503+`DB_DOWN` 拒绝而 503+`NEW_RISK_BLOCKED` 放行 | **PASS** |
| F-P4 | startup smoke 必须通过且绑定 image digest | `passed=false` → `RELEASE_STARTUP_SMOKE_NOT_PASSED`；`image_digest_ok=false` → `..._DIGEST_NOT_VERIFIED`；smoke image 与 digest 不匹配 → `..._DIGEST_MISMATCH`；通过时 manifest 含 `startup_smoke_summary` | **PASS** |
| F-P5 | demo-evidence/deploy-demo 先过 `verify_release_provenance.py` 再 checkout `inputs.commit`；operator 输入仅经 step env（R2 加严，13.19） | workflow 静态顺序断言（provenance 在 `ref: ${{ inputs.commit }}` 之前、verifier 先 checkout default branch、manifest/run 产物路径、Control 端口、24h 楼层、secrets `DEMO_*` 步骤级注入）；R2 后：`Validate the operator inputs` 步骤与 5 类输入校验码、`${{ runner.temp }}/qts-release` 下载 + `RELEASE_EVIDENCE_DIR` 注入 + `--manifest "$RELEASE_EVIDENCE_DIR/release-manifest.json"`、`${{ inputs.* }}` 不得出现在裸 shell 命令行（双 workflow 静态反查）；`evaluate()` 直测：合法 main/workflow_run/success run → `[]`；run-id/workflow path/event/branch/status/conclusion/head_sha/manifest commit·digest·release_id·environment 共 11 类拒绝 + commit/digest 格式非法 | **PASS** |
| F-P6 | preflight 必需 `QTS_EXTERNAL_ALERT_HEARTBEAT_URL`（webhook+heartbeat 均 https 且非集群内） | 缺失 → `EXTERNAL_ALERT_HEARTBEAT_MISSING`；`http://` 与 `https://127.0.0.1` 同码拒绝；happy 环境含 heartbeat → ok | **PASS** |
| F-P7 | strict release evidence 必须为运行时绑定（旧声明式 checks 已不足，R2 起按 §4bis F-P2 行的实际事实校验） | `runtime_inspected=False` → `RELEASE_RUNTIME_BINDING_MISSING`；无 checks → `RELEASE_RUNTIME_CHECKS_MISSING`；check 不全/为 false → `RELEASE_RUNTIME_CHECK_FAILED:<key>`；无 containers或缺 control/execution → `RELEASE_RUNTIME_CONTAINERS_MISSING`；三码联合反例；happy 全事实绑定（errors=[]、真实时间戳、容器/health/身份块齐备）→ 0 blocker；旧码 `RELEASE_RUNTIME_BINDING_INVALID` 已随 R2 实现移除，断言同步更新 | **PASS** |
| F-P8 | 受控上传/服务固定（release-build 仅 `PUBLISH=='true'` 上传证据；ci-gate postgres service pinned digest） | ci-gate `image:\s*postgres:16@sha256:[0-9a-f]{64}` 断言（本测试员）；`PUBLISH` 门由工程师自有静态测试覆盖（工程师端，已记录于 §5#9） | **PASS** |

## 5. 过程中的发现与闭环

1. `host_evidence` 反例初版把"未配置 DR 路径"与"已配置但文件缺失"混为一个断言；对照契约
   13.17 后拆分为 `DR_EVIDENCE_PATH_MISSING` / `DR_EVIDENCE_MISSING` 两条独立断言（实现行为正确，
   是测试期望错误，已修正并复验转绿）。
2. `packages: write` 检查初版被 ci-gate 注释文字命中；改为剔除注释行后匹配并加 `^\s+packages:\s*write\s*$`
   正则防线（测试检查方式问题，已修正）。
3. 两文件首轮 black/isort 未过（222 行 diff，`artifacts/cicd/tester-black.diff`）；由测试员按 diff
   手工修正后，最终 black/isort 均 exit 0（表 3 #1/#2）。
4. 未发现生产实现的阻塞缺陷；上述均为测试侧修正。
5. 审核期工程师修复（`executed_by=engineer`，不属测试文件变更，不改变本报告测试结论）：
   `.github/workflows/demo-evidence.yml` 原先 `testnet_e2e.py` / `observe_trading_runtime.py` 使用
   默认 Control 端口 8090，而 Demo overlay 的 Control 发布端口是 18090；已显式改为
   `--base-url http://127.0.0.1:${QTS_DEMO_CONTROL_PORT:-18090}` 并新增 env
   `QTS_DEMO_CONTROL_PORT`。该修复后 source digest 更新为 `deb9f2f7…`；测试员两文件 SHA256
   未变，offline 独立文件复跑仍 73 passed / 0 skipped
   （`artifacts/cicd/tester/offline-independent-postfix.xml`）；最终组合 P0+offline
   3682 passed / 0 skipped、coverage 79.5%（`artifacts/cicd/raw/combined-final2.xml`、
   `coverage-final2.xml`）。
6. 工程师端 lint 配置修复（`executed_by=engineer`，不属测试文件变更，不改变本报告测试结论）：
   `Frontend/eslint.config.js` 的 ESLint ignores 增加 `coverage`，避免已生成 coverage 时
   `npm run lint` 对生成 JS 报假错误。修复后 full source digest 更新为
   `2622675e…`（53 文件，`eslint.config.js` 进入受审源文件）；验证：lint exit 0
   （0 errors / 7 warnings）、typecheck 0、`test:coverage` 156 tests 通过、build 0。
   测试员两文件 SHA256 未变，本报告其余结论不变。
7. 第 1 轮只读审核（`overall=fail`，仅 3 项文档-实现一致性缺口；核心机制与证据链全部 pass、
   无假绿）后工程师完成限定修复，测试员逐条复核关闭（行号为修复后文件实测）：
   - **R1-F1（关闭）**：`docs/INTERFACE_CONTRACTS.md` 13.17 Demo env 键表补录
     `QTS_AUTH_REQUIRED` / `QTS_USERNAME`（L3562，含"`AUTH_REQUIRED_NOT_ENABLED`"语义），
     错误码清单同步（L3582）。
   - **R1-F2（关闭）**：`docs/CI_CD_RELEASE_RUNBOOK.md` L140 将 `duplicate_*`/`resend_*`/
     `overwrite_*` 改述为"数值键若存在且 >0 则阻断；当前运行时主要经 `unresolved` 的
     DUPLICATE/RESEND/OVERWRITE/REPLAY 代码触发同一 blocker"；L142 明确"运行时未产出
     数值键不等于零重复证据"。与 `summarize_observation.py` 实际逻辑一致（本报告 §4#6
     的字符串/bool 计数器规范化用例已覆盖）。
   - **R1-F3（关闭）**：runbook L76-79 改为实际检测范围（`DROP TABLE/COLUMN/SCHEMA/TYPE/INDEX`、
     `TRUNCATE`、`ALTER COLUMN`、`DELETE FROM`、`UPDATE ... SET`、`INSERT INTO`、
     `RENAME COLUMN/TO`）并注明黑名单非完整白名单解析、其余 DDL 仍需人工审查；
     `trader/tests/test_cicd_migration_safety.py` 检测器正则已加强（L20-24）并新增 4 个
     参数化反例（L57-67：UPDATE...SET / INSERT INTO / RENAME COLUMN / DROP INDEX）。
   - 限定修复：`scripts/deploy_preflight.py` 新增对 `QTS_ACCOUNT_ID`（L145）、
     `QTS_STRATEGY_MODULE`（L175）、`QTS_BACKUP_ATTESTED_BY`（L205）占位符拒绝（均接
     `_is_placeholder`）；非阻塞项：deploy-demo checkout 固定 `inputs.commit`、ci-gate
     postgres service 改 pinned digest、demo-evidence 增加 env var vs mounted secret file
     注释、RFC 增加测试员批次数字说明。
   - 修复后回归（`executed_by=engineer`）：目标回归 111 passed / 0 skipped
     （`artifacts/cicd/raw/fix-round-targeted.xml`，含本测试员 offline 文件 73 passed）；
     PG 独立文件 8 passed / 0 skipped（`artifacts/cicd/tester/postgres-engineer-run2.xml`）；
     manifest ok=true；black/isort/mypy exit 0。测试文件未改动。
8. CI drift 防护修复（`executed_by=engineer`，由第 1 轮 reviewer non-blocking finding 转化，
   不属测试文件变更，不改变本报告测试结论）：`scripts/verify_test_manifest.py` 新增错误码
   `CI_JOB_UNREGISTERED`——`ci-gate.yml` 中未登记到 `ci_jobs`/`ci_required_non_test_jobs` 的
   Job 会导致 manifest gate 失败，防止新增 Job 绕过聚合校验；
   `trader/tests/test_cicd_ci_tools.py` 新增 unregistered-job 反例测试（已通过）。修复后：
   目标回归 `test_cicd_ci_tools.py` 通过；测试员 offline 73 / PG 8 文件未变；
   manifest/black/isort exit 0。full source digest 更新为 `4793213c…`（53 文件）。
9. 父级 F-P 门禁轮（8 项缺口由父级最终核验提出、工程师实现，测试员按新契约重写断言并补反例）：
   测试员 offline 文件重写 6 个既有用例（preflight heartbeat 绑定、strict 24h/runtime 绑定、
   manifest 全证据+smoke 绑定、demo-evidence provenance 门禁）并新增 12 个 F-P 反例
   （§4bis），PG 文件零改动。两轮迭代：004 轮 82 collected / 79 passed，3 个失败均为测试员
   fixture bug（`_release_args` 的 `dist` 目录重复创建触发 WinError 183）+ 3 行 black 引号
   规范，由测试员修正（单引号 JSON 字面量、`mkdir(exist_ok=True)`）；005 轮全绿
   （82 passed / 0 skipped / 0 xfailed，`offline-independent-fp2.xml`）。逐键
   `STRICT_METRICS_*`、24h 边界（>= 语义）、runtime binding 全部与实现实测一致。
   PG 实际端口为 `127.0.0.1:55436`（此前执行记录中 55435 为笔误；测试文件按环境变量注入
   DSN，无端口硬编码，不受影响）。工程师端另有 `release-build.yml` release-evidence 上传
   改为仅 `env.PUBLISH == 'true'` 并附自有静态测试（不属测试文件变更）。source digest 当前
   为中间值（含两个 `test_cicd_parentfix_*.py` 占位文件），最终值待全新测试员冻结后重算。
10. R2 加严轮（父级第 2 次核验要求 F-P1/F-P2/F-P3/F-P5/F-P8 加严；工程师已实现
    `scripts/release_binding.py`（binding 实际事实校验 + 样本身份/窗口锚定）、13.18/13.19
    契约、runbook 更新、workflow 输入只经 step env、`deploy_gate` facts 必需、preflight
    `RELEASE_MANIFEST_NOT_PROVIDED` 警告；测试员只改测试文件，未触碰实现/契约/workflow）：
    offline 文件适配 4 个失败用例并新增 `deploy_gate.evaluate` 独立反例与样本身份/窗口锚定
    反例（85 个收集用例）。两轮迭代：预检 85 passed 仅剩一处 black 列表推导折叠分歧（由
    测试员按 black 规范形态修正）；006 轮全绿——fmt exit 0、offline 85 passed / 0 skipped
    / 0 xfailed（`artifacts/cicd/tester/offline-independent-r2-final.xml`）、全量回归
    offline 3443 / P0 367 / research_ai 252 / postgres 261 全部 0 failed / 0 skipped。
    PG 独立文件仍零改动（`d44e0fc9…`）。旧码 `RELEASE_RUNTIME_BINDING_INVALID` 已被
    `RELEASE_RUNTIME_GENERATED_AT_INVALID` 等新码取代，断言同步更新；strict 窗口样本锚定
    到真实 epoch ms（2026-11-04T00:00:00Z 起），binding `generated_at` 落在窗口后 1h 内。
    前 digest `1f62fa71…` 因 `test_cicd_parentfix2_*` 引入失效，最终值待第 2 轮测试员冻结
    后重算回填。

## 6. changed_files（本测试员写入）

- `trader/tests/test_cicd_independent_offline.py`（新增，offline 组）
- `trader/tests/test_cicd_independent_postgres.py`（新增，postgres 组）
- `artifacts/cicd/tester/test-design.md`（测试设计，先于实现）
- `docs/CI_CD_INDEPENDENT_TEST_REPORT.md`（本报告）
- `artifacts/cicd/tester/*`、`artifacts/cicd/tester-black.diff`（工程师代跑产物）

## 7. 限制（limitations）

- 测试员无命令执行通道（沙箱 ACL 限制 + v3 禁止提权/不受限终端）：所有执行由工程师代跑，
  测试员只分析回传的原始输出/JUnit；黑盒命令的真实性依赖工作区 JUnit/日志文件可复核。
- `test_new_unclassified_file_makes_pytest_exit_4` 在测试进程内再起子进程 pytest（超时 600s），
  验证的是 conftest 门禁行为，不覆盖 GitHub 托管 runner 上的真实失败形态。
- PG 用例的 loopback 断言能确认 DSN 指向回环地址，但无法从进程内证明该端口是"任务自有容器"
  而非同机其他实例；容器侧事实（tmpfs、未触碰 qts-postgres/qts_pgdata、实际端口 55436）由
  工程师执行陈述与命令清单佐证（`tester-exec-req-002/003/004/005/006`）。
- offline/PG 用例计数以 pytest 收集为准（85 / 8），与 JUnit 一致；文件内 `def test_` 文本计数
  不作为报告数字。
- 本报告 source_digest 为 `6590c1e78ed1f8413af9879b8bba5ec10eb227a33f9e751d3c83634a581b3c91`
  （69 files，见元数据）；`test_cicd_parentfix2_*` 测试文件已冻结，R2-F-P3-001/002 已由第 2 位
  审核员确认关闭。该变化不影响本报告的测试结论与证据。
- 迁移守恒测试 monkeypatch `MIGRATIONS` 模拟 v2/v3 runtime 差异，未单独构造"更老 runtime 进程"。

## 8. 未执行项（unexecuted_checks，不得按通过处理）

- 远端 GitHub Actions 全量真实运行（PR 与 main 上的 ci-gate/release-build/deploy-demo/demo-evidence）。
- 远端 ruleset 迁移的实际应用与旧 ruleset `13369928` 停用（当前仅本地迁移文件，见
  `deploy/RULESET_MIGRATION.md`）。
- GHCR 镜像推送、trivy SBOM/漏洞扫描的远端产物。
- 真实 Demo 部署（self-hosted `qts-demo`）与 24h 观察窗（含 `--strict-release` 的真实窗口）、
  DR 演练远端证据。
- Binance testnet / 真实外部依赖联调（专项范围外，未请求凭据）。

## 9. 判定

在上述已执行证据范围内，10 项重点（§4）与父级 F-P 门禁补充矩阵（§4bis，含 R2 加严）全部通过
独立反例验证，无阻塞发现（`blocking_findings: []`）；最终组合证据为 `artifacts/cicd/tester/
offline-independent-r2-final.xml`（85 passed / 0 skipped / 0 xfailed）、PG 独立文件 8 passed
/ 0 skipped（`postgres-engineer-run2.xml`、`postgres-independent-fp.xml`）与 R2 全量回归
offline 3443 / P0 367 / research_ai 252 / postgres 261 全绿（tester-exec-req-006）。
`workspace_frozen: true`（两测试文件 SHA256 见 §2，此后未再改动）。
