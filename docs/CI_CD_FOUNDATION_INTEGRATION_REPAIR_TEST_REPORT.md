# CI/CD × Foundation 整合：父级修复独立复验报告

- report_id: `RT-CICDINT-20261111-REPAIR-R1-FINAL`
- kind: `test_final_report`
- workflow_version: 3
- phase: cicd-foundation-integration（父级修复轮，CIINT-P1 / CIINT-P2）
- role: tester-repair（全新独立测试员，复验批）
- tester_agent_id: `e5974812-01c8-4d0e-847a-6bd3e099d600`
- engineer: `c4f927bf-7b6b-4747-af1a-9f2d83fd98f1`
- parent: `e36d6105-e0bb-4f27-bbbd-225b5a55da2e`
- worktree: `D:/Code of Python/quant_trading_system/.worktrees/cicd-foundation-integration`
- branch: `integration/cicd-foundation`（merge `--no-commit --no-ff` 未提交；foundation HEAD `833cc0207066ae83689a5bc42053a4ab7d02f6a2`，MERGE_HEAD `56577c78cccec7a91fca1678df7b0a27593f0f28`）
- 输入依据：`parent-verification/parent-verification-r1.md`（finding 只读引用，未修改该目录）、`engineer-final-report.md`、本复验 execution_request `repair-tester/execution-request-r1.md`
- **overall: pass**（CIINT-P1 与 CIINT-P2 两项 finding 均独立复验通过；无 blocking findings）

## 1. 角色与执行方式（authored / executed / analyzed 分离）

本测试员的 pwsh 命令通道在启动前被 Windows 文件授权问题阻断
（`SetNamedSecurityInfoW failed (Win32 5): grantWrite`）。按 workflow v3 §2.1：
ACL 修复请求已被拒绝（未重试、未换终端、未提权），随后按 execution_request 协作完成复验：

| 事项 | by |
| --- | --- |
| execution_request 与探针副本 `repair-tester/probe_guard_missing_docker.py`（父探针逐字节等义副本，仅存放目录不同）的编写 | **authored_by** = 测试员 `e5974812…` |
| 全部命令实际执行（第 1–6 组，输出保留在 `repair-tester/`） | **executed_by** = 工程师 `c4f927bf…`（受控代跑，非测试员亲跑） |
| 全部原始产物（JUnit XML、exit 文件、日志、JSON、diff、venv 列表）逐份独立核对与判读 | **analyzed_by** = 测试员 `e5974812…` |

工程师未修改测试员文件；测试员未修改生产/契约/父证据/其他测试。本报告与
`repair-tester/*` 是测试员本轮唯一写入。

## 2. CIINT-P1：required guard job 不得因缺 Docker 变绿 —— 复验通过

### 2.1 真实 Windows guard 子集（executed_by=工程师，analyzed_by=测试员）

- 命令：`QTS_TEST_SUBSET=phase6_guard QTS_TEST_NETWORK_POLICY=open python -m pytest -q trader/tests --tb=short -p no:cacheprovider --junitxml=…/repair-tester/guard-real.xml`
- 结果：pytest exit 0（`guard-real.exit.txt` = `guard_real=0`）；JUnit `guard-real.xml`
  独立解析：`tests=18 failures=0 errors=0 skipped=0`，18 个点全部为
  `tests.test_phase6_startup_guard`（含 1 个 `test_real_supervisor_entry_probe_acquires_without_side_effects`
  与 5 个参数化 `test_real_supervisor_entry_concurrent_exactly_one_owner`，各自实际耗时
  3.6–5.1s，为真实 supervisor 探针执行而非 skip）。
- 零 skip 门禁：`python scripts/check_junit.py guard-real.xml --require-zero-skipped --require-zero-xfailed`
  exit 0（`guard-real-gate.exit.txt` = `guard_real_gate=0`；`guard-real-gate.json`
  `ok=true, errors=[], skipped=0, xfailed=0, tests=18`）。

### 2.2 Docker 缺失反例（探针副本，authored_by=测试员 / executed_by=工程师 / analyzed_by=测试员）

- 探针副本从 worktree 根运行：pytest exit 0（`probe.exit.txt` = `probe=0`），
  stdout `probe.stdout.txt`：`6 skipped in 1.11s` +
  `{"pytest_exit": 0, "docker_available_forced": false}`。
- 独立解析 `guard-missing-docker.xml`：`tests=6 failures=0 errors=0 skipped=6`，
  6 个 `<skipped type="pytest.skip" message="docker daemon unavailable for the real-entry guard probe">`
  均指向 `test_phase6_startup_guard.py:73` —— 证实 pytest 绿 + 缺 Docker 时 required 组
  会静默丢 6 个测试，与父级 finding 的反例计数一致。
- 同一份 JUnit 过零 skip 门禁：`probe-gate.exit.txt` = `probe_gate=1`；
  `probe-gate.log`：`ok=false, errors=["JUNIT_SKIPPED_NOT_ALLOWED: 6 skipped"]`。
  **即：workflow 中新增的 `check_junit --require-zero-skipped --require-zero-xfailed`
  门禁会把"缺 Docker 变绿"拦截为失败（fail-closed 成立）。**

### 2.3 workflow 静态断言（analyzed_by=测试员，反例测试 executed 于 §3）

独立通读 `.github/workflows/ci-gate.yml` 的 `phase6-guard-gate`（windows-latest）：

- `docker version` 预检步骤，失败时 `GUARD_DOCKER_UNAVAILABLE` + `exit 1`；
- 安装步骤使用 `trader/requirements-ci.windows.constraints.txt`；
- `check_junit.py reports/junit-phase6-guard.xml --require-zero-skipped --require-zero-xfailed`；
- `phase6-guard-evidence` artifact（`if: always()`），含 JUnit XML/JSON，`if-no-files-found: error`。

上述要点同时被 `trader/tests/test_cicd_ci_tools.py::test_phase6_guard_job_fails_closed_when_the_probe_would_skip`
静态断言 + 行为反例（6-skip JUnit 被 check_junit 拒绝，返回 1）覆盖，该测试在本轮
offline/P0 全量中实际通过（见 §3，测试名已在其 JUnit 中独立检索确认）。

## 3. 受影响回归：offline/P0 全量复验（executed_by=工程师，analyzed_by=测试员）

- 命令：`QTS_TEST_GROUP=p0,offline QTS_TEST_NETWORK_POLICY=offline python -m pytest -q trader/tests --tb=short -p no:cacheprovider --junitxml=…/offline-p0.xml`
- 结果：pytest exit 0（`offline-p0.exit.txt` = `offline_p0=0`）；JUnit 独立解析
  `tests=3636 failures=0 errors=0 skipped=0`；`check_junit --require-zero-skipped
  --require-zero-xfailed` exit 0（`offline-p0-gate.exit.txt` = `gate=0`，JSON
  `ok=true, tests=3636, skipped=0, xfailed=0`）。
- 计数核对：此前合并证据 offline 唯一集合为 3634；本轮 +2 = 本轮新增的
  `test_phase6_guard_job_fails_closed_when_the_probe_would_skip` 与
  `test_windows_ci_lock_is_complete_and_used_only_by_the_windows_job`，两个测试名均已
  在 `offline-p0.xml` 中独立检索确认存在且通过。
- manifest：`python scripts/verify_test_manifest.py` exit 0（`manifest.exit.txt` =
  `manifest=0`；`manifest.log`：236 files、`ok=true`、`warnings=[]`、分组
  p0 17 / offline 189 / postgres 24 / ops 4 / external 2）。
- scoped format：isort/black 对 `trader/tests/test_cicd_ci_tools.py`（及探针副本）均
  exit 0（`fmt.exit.txt`）。

## 4. CIINT-P2：Windows required job 使用完整 Windows 锁 —— 复验通过

### 4.1 干净环境带哈希解析（executed_by=工程师，analyzed_by=测试员）

- 空环境证明：`win-lock-venv-list-before.txt` = 仅 `pip 24.2`（Python 3.12.5，
  无任何第三方依赖）——满足父级"不能依赖当前环境预装的 colorama"要求。
- 命令：`python -m uv pip install --python artifacts/cicd/integration-20261111/win-lock-venv/Scripts/python.exe --require-hashes --dry-run --default-index https://pypi.org/simple -r trader/requirements-ci.windows.constraints.txt`
- 结果：exit 0（`windows-lock-resolve.exit.txt` = `resolve=0`）；
  `windows-lock-resolve.log`：`Resolved 76 packages in 2.60s`，解析闭包中明确含
  `colorama==0.4.6` 与 `pytest==9.0.2`。`--require-hashes` 在干净 Windows 依赖闭包下
  完整可解析，父级 finding 的根因（Linux 锁缺 colorama）已在 Windows job 修复。

### 4.2 锁与 workflow 一致性（analyzed_by=测试员 + 工程师代跑指纹）

- Windows 锁头第 2 行：`uv pip compile … --python-version 3.12 --python-platform x86_64-pc-windows-msvc`；
  含 `pytest==9.0.2`（L2299）与 `colorama==0.4.6` 带 2 个 sha256 hash（L515-518，`# via pytest`）。
- Linux 锁未动：`linux-lock-vs-cicd.diff` 为 0 行（与 CI/CD 源侧完全一致），
  头仍为 `x86_64-unknown-linux-gnu`，`pytest==9.0.2`，不含任何 `colorama` 项 ——
  Linux/runtime 锁原有可复现性保持。
- workflow 锁引用唯一性：`requirements-ci.windows.constraints.txt` 仅出现在
  `phase6-guard-gate`（L400 cache path、L413 install）；其余 8 处 install 均用
  `trader/requirements-ci.constraints.txt`（Linux 锁）。上述断言也被新测试
  `test_windows_ci_lock_is_complete_and_used_only_by_the_windows_job` 固化并在 §3 实跑通过。
- 指纹（executed_by=工程师）：windows lock SHA256
  `34e7f3e24adcce384e4086825adf46a65472c06093a994094a3d6a4137610861`（218,933 B）；
  linux lock SHA256 `a230aac6349de1650567fae53b91d04079bbad6681d92721a4d8b5806f21badd`
  （218,770 B）（`lock-sha256.txt`）。
- `git status --porcelain`（77 行，`git-status.txt`）：为 merge 未提交工作态 +
  `?? trader/requirements-ci.windows.constraints.txt`（新增未跟踪）+ 测试员/工程师
  artifacts（被忽略项不在其中）；无额外未说明 tracked 改动。

## 5. checks（原始证据均在本轮 artifacts）

| id | 检查 | exit | 计数 | required | 证据 |
| --- | --- | --- | --- | --- | --- |
| P1-1 | 真实 guard 子集 pytest | 0 | 18P/0F/0E/0S | 是 | `repair-tester/guard-real.xml`、`guard-real.exit.txt`、`guard-real.log` |
| P1-2 | guard JUnit 零 skip 门禁 | 0 | tests=18, skipped=0 | 是 | `guard-real-gate.json`、`guard-real-gate.exit.txt` |
| P1-3 | Docker 缺失探针（反例） | 0 | 6 skipped | 反例（预期 pytest 绿） | `probe.stdout.txt`、`probe.exit.txt`、`guard-missing-docker.xml` |
| P1-4 | 反例 JUnit 过零 skip 门禁 | **1** | skipped=6, errors=[JUNIT_SKIPPED_NOT_ALLOWED] | 是（预期失败） | `probe-gate.log`、`probe-gate.exit.txt` |
| R-1 | offline+p0 全量 pytest | 0 | 3636P/0F/0E/0S | 是 | `offline-p0.xml`、`offline-p0.exit.txt` |
| R-2 | offline+p0 零 skip 门禁 | 0 | tests=3636, skipped=0 | 是 | `offline-p0-gate.log`、`offline-p0-gate.exit.txt` |
| R-3 | verify_test_manifest | 0 | 236 files, warnings=[] | 是 | `manifest.log`、`manifest.exit.txt` |
| R-4 | isort/black（受影响测试） | 0 | unchanged | 是 | `fmt-isort.log`、`fmt-black.log`、`fmt.exit.txt` |
| P2-1 | 空 venv 证明 | — | 仅 pip 24.2 | 是 | `win-lock-venv-list-before.txt` |
| P2-2 | uv --require-hashes --dry-run | 0 | Resolved 76 packages（含 colorama==0.4.6、pytest==9.0.2） | 是 | `windows-lock-resolve.log`、`.exit.txt` |
| P2-3 | 锁指纹 + Linux 锁 diff | — | diff 0 行 | 是 | `lock-sha256.txt`、`linux-lock-vs-cicd.diff` |

## 6. items（复验结论）

| id | verdict | evidence |
| --- | --- | --- |
| CIINT-P1 | pass | §2 全部：18/0/0/0 + gate 0；探针 6 skipped + gate 1；workflow 静态要点与新测试反例 |
| CIINT-P2 | pass | §4 全部：空 venv + `--require-hashes` dry-run exit 0（colorama/pytest 在闭包）；锁头平台/哈希；workflow 唯一引用；Linux 锁 0 行 diff |
| G1/G3 | pass | 3636 offline/P0 全绿、零 skip/xfail；manifest 236/ok；无删断言/扩 skip |
| G5 | pass | 测试员仅写 `repair-tester/*` 与本报告；parent-verification 未触碰；ACL 修复请求被拒后未重试 |
| G6 | pass | workflow 锁/门禁/artifact 与 `verify_test_manifest`、新测试断言一致 |

## 7. 限制与未执行项（不得作为已跑项引用）

- **远端 GitHub Actions 未运行**：`phase6-guard-gate` 及 `CI Required` 聚合的真实远端
  实跑不在本轮范围；本轮仅证明本地等价执行与静态门禁构造正确，不冒充远端已跑。
- `docker version` 预检步骤本身未作为独立 CI 步骤在本地重演（本地 Docker 可用，
  反例经由探针强制 `_docker_available=False` 完成等价验证）。
- 本测试员未亲自执行任何命令（executed_by=工程师）；判读基于工程师回传的原始产物
  逐份独立核对，无测试员本机复现执行。
- SHA256 指纹由工程师代跑生成，测试员逐值转录核对（`lock-sha256.txt`），未在本机重算。
- 未执行：research/PG 组本轮未重跑（本轮改动不触及其行为，既有合并证据保留，见
  engineer-final-report §3）；coverage 复算、frontend、compose render 等未在本轮重复。
- ACL 授权问题保留为环境历史（`grantWrite` 被拒），不计入代码失败轮次；未修改任何 ACL。

## 8. 结论与移交

CIINT-P1 与 CIINT-P2 的限定修复经独立复验全部通过：required guard job 现具备
基建预检（`GUARD_DOCKER_UNAVAILABLE`）、零 skip/xfail fail-closed 门禁与可追溯
`phase6-guard-evidence` artifact，且行为反例证明缺 Docker 无法变绿；Windows required
job 使用完整带哈希 Windows 锁（空 venv `--require-hashes` 解析成功），Linux 锁未动。
overall=pass，无 blocking findings。工作区保持冻结（merge 未提交）。
可由工程师启动全新只读审核员（本轮次审核），最终提交仍由父 Agent 处理。

- workspace_frozen: true（测试员本轮写入后不再改动源码；报告即冻结凭据）
- blocking_findings: []
- next: 工程师 → 新一轮只读审核员（每轮新会话）
