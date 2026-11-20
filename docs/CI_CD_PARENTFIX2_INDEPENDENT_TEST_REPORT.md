# CI/CD 父级第 2 轮修复：独立测试报告（全新独立测试员）

- 日期：2026-11-05；工作流：workflow v3。
- **authored_by（设计并编写测试）**：CI/CD 父级第 2 轮独立测试员（全新，agent `ce7472b9-3310-41e6-9680-5cbc585ac938`），设计文档先于读实现完成：`artifacts/cicd/parentfix2-tester-r2/test-design.md`。
- **executed_by（代跑命令）**：工程师 `c4f927bf-7b6b-4747-af1a-9f2d83fd98f1`（6 次 execution_request；测试员命令通道启动前即被沙箱拒绝 `grantWrite` Win32 5，未修 ACL、未提权）。
- **analyzed_by（独立核对原始产物）**：独立测试员本人，仅依据 `.exit.txt`、JUnit XML、stdout 原文判定，不采信转述。
- 分支：`feature/ci-cd-hardening`；被测对象：第 2 轮修复后的 `scripts/release_binding.py`、`scripts/observe_trading_runtime.py`、`scripts/collect_runtime_release_binding.py`、`scripts/deploy_gate.py`、`scripts/verify_deploy_gate_live.py`、三个受保护 workflow、`deploy/compose.demo.yml`、契约 §13.18/§13.19。
- 测试文件（冻结，工程师未改动其内容；仅按其只读 diff 应用 black/isort 格式）：
  - `trader/tests/test_cicd_parentfix2_offline.py`
    R3 终态 fingerprint `787b1131572943c47f4c281363decdb10089c453ccca0fd1fcf39ebfb4ecfa8a`（112 项；R2 时为 `ac75606b…` / 109 项）
  - `trader/tests/test_cicd_parentfix2_postgres.py`
    fingerprint `3285a04251e08cc044b9506947298af0821a92bc15c902d86125b3eec710c98f`（2 项，未改动）
  - 指纹由工程师计算；测试员因命令通道不可用未能独立重算哈希（见"限制"），但逐项核对了 JUnit 计数与 exit 文件。

## 实际计数（以 JUnit 原文核对）

| 组 | 命令 | exit | tests | passed | failed | error | skipped |
|---|---|---|---|---|---|---|---|
| offline | `pytest trader/tests/test_cicd_parentfix2_offline.py` | 0 | 109 | 109 | 0 | 0 | 0 |
| postgres | `pytest trader/tests/test_cicd_parentfix2_postgres.py` | 0 | 2 | 2 | 0 | 0 | 0 |
| livepg（真实 psql） | `python scripts/verify_deploy_gate_live.py --output artifacts/cicd/raw/deploy-gate-live.json` | 0 | — | — | — | — | — |
| 格式 | `black --check` / `isort --check-only`（line-length 100） | 0 / 0 | — | — | — | — | — |

无 skipped、无 xfail。产物目录：`artifacts/cicd/parentfix2-tester-r2/`（`offline4.exit.txt`、`offline4.stdout.txt`、`junit-offline.xml`、`postgres2.exit.txt`、`postgres2.stdout.txt`、`junit-postgres.xml`、`livepg.exit.txt`、`livepg.stdout.txt`、`fmtcheck3.exit.txt`）。

> 上表为首轮计数（offline 109）。R2 复验维持 109，第 3 轮 F-P1 复验新增 3 个边界用例后
> offline 为 **112**（见下文两节）；postgres 始终 2 项，未改动。

### 审核 R2 后限定修复的复验（re-verification，同一冻结测试文件）

审核员第 2 轮判 fail（R2-F-P3-001/002，lease 布尔解析与证据一致性）后，工程师做了限定修复；测试员在同一冻结测试文件（未改动内容，指纹不变）上复验：

| 项 | 命令/产物 | exit | tests | passed | failed | error | skipped |
|---|---|---|---|---|---|---|---|
| offline 复验 | `junit-offline-r2fix.xml` / `offline5.stdout.txt` | 0 | 109 | 109 | 0 | 0 | 0 |
| postgres 全量复验 | `artifacts/cicd/raw/reviewer2-exec/B3b-postgres.xml` | 0 | 261 | 261 | 0 | 0 | 0 |
| 格式复验 | `fmtcheck4.exit.txt`（black=0 / isort=0） | 0 | — | — | — | — | — |

- PG 全量 261 项中经 grep 在 XML 原文确认含本文件 2 项（`TestVerifyDeployGateLiveReport`、`TestGateSqlAgainstRealSchema`）。
- **R2-F-P3 关闭证据**：
  - 源码核对：`collect_db_facts` 的 `lease.valid` 与 `runtime.ready` 现统一
    `parse_boolean(...) is True`（同时接受 `t/f` 与 `true/false`）；`evaluate` 仍以
    `is not True` fail-closed。
  - 真实 psql 原始回显（`artifacts/cicd/raw/deploy-gate-live.json`）：
    `raw_psql_bare_boolean="t"`、`raw_psql_concat_boolean="x|true"`、
    `raw_lease_query="exec-live|7|true"`、`psql_boolean_is_t=true`、
    `psql_concat_boolean_is_true=true`、`ok=true`、`teardown_returncode=0`；
    facts 与五项门禁错误维持不变。测试员的 `parse_boolean("t"/"f")` 单测与 PG 真实
    schema 测试共同覆盖该解析路径。
  - `summarize_observation --expected-release-id` 默认空串，未传时回退 binding 的
    `release_id`（`expected_release_id or release_id`），既有调用行为兼容；本文件
    109 项在未传该参数下全部通过。
- 父级三反例复跑复验（`artifacts/cicd/raw/reviewer2-exec/C1-C3.stdout.txt`）：
  矛盾 binding exit 1 / ok=false / 19 项 blockers；observer 1441 样本 / 24.0h / 达标；
  pre 三码（QUEUED/SENDING/UNBOOKED）。
- 说明：复验未改动测试文件内容，以保持候选 digest 与全量回归的一致性；R2-F-P3 的关闭
  由源码核对 + 真实 psql 回显 + 同文件全量复跑三重证据支撑。

### 父级第 3 轮 F-P1 复验：observer 初始化耗时（pass）

父级第 3 轮指出：deadline 原在 `httpx.AsyncClient` 初始化*之前*锚定，注入 2s 的
`__aenter__` 后，86400s/60s 请求只记录到 86398s 跨度。修复后 deadline 改为**首个实际
写入样本的 `observed_at` + duration**（同一 `time.time` 时钟、与 `ts_ms` 同基准），
并保留 deadline 补样与 `min(interval, deadline-observed_at)` 截断（源码逐行核对）。

- **父级只读探针复跑**（未修改父级文件；探针内部 patch 掉文件写入与网络）：
  `artifacts/cicd/parentfix2-tester-r3/probe.stdout.txt` →
  `samples=1441`、`actual_span_seconds=86400.0`、`passed=true`、`filesystem_writes=false`、
  `network=false`，exit 0（`probe.exit.txt`）。
- **新增 3 个边界用例**（原零初始化用例保留，手算期望先于断言）：
  1. 初始化 2s → 首样本 T0+2、**1441 样本、跨度恰 86,400,000 ms**；
  2. 每请求 1s（首轮 4 请求、后续 3 请求）→ **1373 样本、跨度 86,403,000 ms**，
     ≥24h 且过冲被一个请求批次界定（断言 `[86,400,000, 86,405,000]`）；
  3. `duration=100 / interval=30`（不整除）→ 样本 0/30/60/90/100 五个、
     **跨度恰 100,000 ms**（末次 sleep 截断为 10s，无 130s 过冲）。
- **终跑**（`junit-offline-r3-final.xml` / `offline7.exit.txt` / `r3fmt2.exit.txt`）：
  112 tests / 112 passed / 0 failed / 0 error / **0 skipped**，exit 0；black/isort exit 0。
- 新指纹：offline `787b1131572943c47f4c281363decdb10089c453ccca0fd1fcf39ebfb4ecfa8a`（112 项）；
  postgres 未改 `3285a042…`（2 项）。R3 全量回归 offline 3560（含本文件 112，以
  `artifacts/cicd/raw/reviewer3-exec/counts-by-file.stdout.txt` 核对；增测前的 3557 为
  历史值）/ P0 367 /
  research 252 / PG 261，全部 0 failed/0 skipped。

## 独立测试捕获的真实缺口（重要）

**binding 缺 `errors` 键 fail-open（F-P2 残余缺口，已修复）**。设计阶段依据契约
§13.19"binding 新增 `errors`（采集错误必须为空）"的 fail-closed 语义构造反例
C1（删除 `errors` 键）。初版实现 `validate_binding` 对缺失键不产生任何 blocker
（`errors=None` 落入放行分支）。首次代跑该反例如实失败
（blockers 为空），工程师随后补 `RELEASE_RUNTIME_ERRORS_MISSING`（缺键）/`
RELEASE_RUNTIME_ERRORS_INVALID`（非列表）并同步契约 §13.19；第 3 次代跑起反例按
`RELEASE_RUNTIME_ERRORS_MISSING` 阻断。这不是测试作者问题，是本轮独立测试
实际捕获并推动修复的实现缺口。

## 逐项结论

### F-P1：观察窗口边界 — 通过
- 真实 `observe_trading_runtime.main()`，假时钟（T0=1_800_000_000.0）+ 假 httpx、零网络，
  `--duration 86400 --interval 60`：**1441 个样本**，首末跨度 **86,400,000 ms = 恰 24h ≥ 下限**，
  末样本 ts ≥ deadline（父级旧实测 23.9833h 的"少采一个间隔"已反转）。
- 每个样本携带 `runtime` 身份（release_id/commit/image_digest/environment/instance_owner/
  lease_token/observed_at_ms），且 `observed_at_ms == ts_ms`。
- strict 24h 下限未放宽：23.983h 窗口（1440 样本）+ 合法 binding 仍被
  `STRICT_WINDOW_SHORTER_THAN_24H` 拒绝；`--strict-min-hours < 24` 被 argparse 拒绝（exit 2）；
  完整 24h 窗口 + 合法 binding → exit 0、blockers 空。
- 用例：`test_observer_covers_full_requested_window`、`test_strict_24h_floor_is_not_relaxed`、
  `test_strict_min_hours_cannot_be_lowered_below_24`、`test_full_window_with_valid_binding_passes_strict`。

### F-P2：绑定校验实际事实 — 通过
- **父级矛盾反例复现**（stopped 容器、`wrong:latest`、非空 errors、
  `generated_at="invalid-timestamp"`、五项布尔全 true、1970 年无身份样本）经真实
  `summarize_observation.main() --strict-release`：exit 1，blockers 含
  `RELEASE_RUNTIME_ERROR_PRESENT:IMAGE_DIGEST_MISMATCH:execution`、
  `RELEASE_RUNTIME_GENERATED_AT_INVALID`、两个服务的 `..._NOT_RUNNING` / `..._IMAGE_MISMATCH`、
  `RELEASE_RUNTIME_HEALTH_MISSING`、`SAMPLE_RUNTIME_IDENTITY_MISSING` —— 布尔位不再能兑换通过。
- 单一变量反例矩阵（27 例，全部在合法基线上一次只注入一个变量）全部按契约 code 阻断：
  缺/非空 `errors`、缺/假 `checks`、无效时间戳、容器缺服务/未 present/未 running/不健康/
  镜像错、三个 release label 逐字段错、health live/ready/trading 三端点、缺 `runtime` 块、
  身份字段缺失/不匹配、`instance_owner` 空、`runtime_inspected=false`；
  样本维度 9 例：缺 runtime/字段缺失/身份不符/时间戳缺失（含类型错误）/超出 120s 偏移；
  窗口锚点 5 例：窗口前（超出 60s 偏移）与窗口后（超出 1h lag）→ `RELEASE_BINDING_WINDOW_MISMATCH`，
  窗口内与边界值放行。
- 合法完整 binding（由真实 `build_binding` 产出）+ 逐样本身份 → exit 0、blockers 空。
- 集成层面 `RELEASE_BINDING_WINDOW_MISMATCH` 也经 strict 摘要入口复现。

### F-P3：真实解析与收敛门禁 — 通过（真实 psql 路径已实测）
- `parse_boolean`：`t→True`、`f→False`、`true/false/1/0/yes/no/on/off`、大小写与空白容错、
  `""/None/"maybe"→未知(None)`；`parse_count` 同理。psql `t/f` 渲染不再被误读为仅 `true`。
- pre 注入 `QUEUED=1 / SENDING=1 / unbooked_fills=1`（父级反例）→
  `COMMANDS_NOT_CONVERGED:QUEUED`、`COMMANDS_NOT_CONVERGED:SENDING`、`UNBOOKED_FILLS_PRESENT`，
  收敛事实下 errors 为空；13 例事实缺陷反例（缺失/None 计数、缺 lease/runtime、
  风险状态未知/未阻断、租约无效、owner/fencing 不对齐）逐码阻断。
- post `run_gate` 假时钟有界轮询：starting→starting→healthy → 3 attempts、sleep=[15,15]；
  始终 starting → 5 attempts / 4 sleeps / 60s 后有界失败（手算一致）；pre 模式 1 attempt、零 sleep。
- **真实路径实测**（`verify_deploy_gate_live.py`，一次性 Compose 项目 `qts-cicd-livepg`，
  真实 `docker compose exec -T postgres psql -tAc`，exit 0）：`ok=true`、**`psql_boolean_is_t=true`**、
  facts 与手算种子完全一致（QUEUED/SENDING/UNKNOWN/outbox/unbooked 均 1、
  new_risk_blocked=true、lease 与 runtime 对齐）、`pre_gate_errors` 恰为手算五项、
  `unexpected_errors=[]`、teardown=0。旧测试"asyncpg 布尔人工转 true/false"的掩盖路径不再需要：
  真实 t/f 输出由本报告直接证明。
- 另在任务自有 127.0.0.1:55436 一次性 schema（`qts_parentfix2_tester_*`，用后即删）上执行
  `collect_db_facts` 的逐字 SQL 文本，返回手算 facts，`evaluate(pre)` 恰为五项错误。

### F-P5：输入注入 — 通过
- deploy-demo.yml 与 demo-evidence.yml 全部 `run:` 块中无 `${{ inputs.`（逐 step 断言）。
- 三个消费 job 均有"Validate the operator inputs"步骤：`[[ ... =~ ... ]]` 校验并输出
  `RELEASE_BUILD_RUN_ID_INVALID` / `RELEASE_COMMIT_INVALID` / `RELEASE_IMAGE_DIGEST_INVALID` /
  `RELEASE_ID_INVALID`（evidence 侧另有 `OBSERVATION_HOURS_INVALID`）；
  输入经 step env（`${{ inputs.* }}` 仅出现在 env 值中）进入 shell。
- provenance 步骤位于校验步骤之后、`ref: ${{ inputs.commit }}` checkout 之前
  （三 job 逐一断言顺序）；以 `"$RELEASE_*"`/`"$RELEASE_EVIDENCE_DIR/release-manifest.json"`
  引用数据；`OBSERVATION_HOURS_BELOW_24` 下限步骤同样走 env。
- provenance 步骤 env 恰为 `GITHUB_TOKEN + 4×RELEASE_* + RELEASE_EVIDENCE_DIR`，
  无任何 Demo secrets。

### F-P8：artifact 传递链 — 通过
- release-build 仅一个 upload-artifact 步骤，单一字符串根 `reports/`、
  `if-no-files-found: error`；manifest 在 `reports/release-manifest.json`、smoke 在
  `reports/compose-smoke/`。
- 单根下载映射按手算验证：`release-manifest.json` 落 artifact 顶层、
  `compose-smoke/compose-smoke.json` 落子目录。
- 三个消费 job（deploy-demo preflight/deploy、demo-evidence collect）均下载
  `release-evidence` 到 `${{ runner.temp }}/qts-release`（checkout 工作树之外，
  默认 `clean=true` 的二次 checkout 不会删除；各 checkout 步骤无 `path` 覆盖），
  manifest 路径统一为 `${{ runner.temp }}/qts-release/release-manifest.json`（三处一致），
  preflight 与 provenance 使用同一路径。
- deploy-demo.yml 全文无 `release-binding`/`runtime-binding` 上传；demo-evidence 中
  binding 采集严格位于观察窗口之后、strict 门禁之前（步骤顺序断言）。

### 回归
- `ci/test_manifest.json`：263 个测试文件跨 5 组唯一分类、无重复、文件均存在；
  两个新文件分别在 offline / postgres 组；`p0-gate`/`offline-gate`/`postgres-integration`
  均 required 且指向正确分组。

## 父级三反例复跑证据（本轮全部反转）

| 父级 probe | 父级实测（修复前） | 本轮实测 |
|---|---|---|
| `contradictory-runtime-and-historical-unbound-samples` | exit 0 / ok=true / blockers 空 | exit 1 / ok=false / blockers 含 8 项事实码（见 F-P2） |
| `real-observer-with-fake-clock-and-fake-http` | 1440 样本 / 23.9833h / 不达 24h 下限 | 1441 样本 / 24.0000h / 达标；strict 下限未被放宽 |
| `pre-deploy-unconverged-commands-and-fills` | errors 空 / passed | errors 含 QUEUED/SENDING/UNBOOKED 三码 |

## 候选交付（审核前）

- **当前候选**（第 3 轮 F-P1 限定修复 + 本文件 112 项）：source digest
  `cd9f27706386c29441e6b9e5b01a33b119af1b6a0d1630c946e93d114aa16a6c`（69 files）。
  R3 全量回归（R3b-\* 最终全量）：offline 3560、P0 367、research_ai 252、postgres 261，
  均 0 failed/0 skipped；
  livepg `ok=true`、raw 回显不变；black/isort/mypy/core-no-io 全 exit 0。
- 历史候选：`6590c1e7…`（R2 限定修复后）、`465ef35a…`（R2 审核前），均已被取代，仅作记录。
- 均为**只读审核前候选**；若审核导致源变化，以工程师重算并通知的新 digest 为准。

## 限制与未执行项（如实）

- 测试员命令通道自始被沙箱拒绝（`grantWrite` Win32 5），全部命令由工程师代跑；
  测试员仅以只读方式核对原始产物，未独立重算 SHA256 指纹与 source digest。
- black/isort 格式为测试员按工程师只读 diff 手工应用（R2 三轮 + R3 一轮，断言零改动），
  终态经 `black --check` / `isort --check-only` exit 0 确认。
- 未执行：远端 Actions、真实 GHCR digest smoke、Demo 真实部署、24h 真实外部观察窗口、
  攻击性注入 payload（F-P5 为静态结构断言 + 校验逻辑验证）；F-P4/F-P6/F-P7 不在本轮范围。
- PG 使用仅限任务自有 127.0.0.1:55436 与一次性 Compose 项目 `qts-cicd-livepg`（端口 55439）；
  未触碰既有 `qts-postgres` 容器与 `qts_pgdata` 卷。
- 未提交、未推送、未部署。

## 结论

独立测试**通过**：F-P1/F-P2/F-P3/F-P5/F-P8 的修复在 114 项独立测试（112 offline + 2 postgres）
与一次真实 `docker compose exec/psql` 实测下全部满足契约 §13.18/§13.19；父级三个反例全部反转；
过程中捕获并推动修复了 1 个真实 fail-open 缺口（binding 缺 `errors` 键）。

**审核 R2 后复验结论：pass**。R2-F-P3-001/002（lease 布尔解析与证据一致性）已在限定修复后
关闭：`lease.valid`/`runtime.ready` 统一走 `parse_boolean`；真实 psql 回显
`t` / `x|true` / `exec-live|7|true` 与 `ok=true`、`teardown=0` 一致；同一冻结测试文件
offline 109/0S、postgres 全量 261/0S 均 exit 0。

**父级第 3 轮 F-P1 复验结论：pass**。deadline 已锚定首个记录样本的 `observed_at`；
父级只读探针复跑 1441 样本 / 86,400.0s / passed=true / exit 0；新增 3 个边界用例
（2s 初始化、每请求 1s、100s/30s 不整除）与原有零初始化用例共同覆盖，offline
112/112、0 skipped，exit 0。当前候选 digest `cd9f2770…`（69 files）。可交全新只读审核员。
