# CI/CD 发布与回滚手册（Binance Spot Demo）

版本：2026-11-04。适用于 `feat/foundation-refactor`/`main` 验收后的 CI/CD 交付。
本文是操作手册，不是已执行记录；当前分支没有执行任何远端发布、真实 Demo 交易、
24h 当前 digest 观察或生产部署。**Demo 是本项目最高外部验收环境，不要求 testnet。**

## 1. 不可混淆的结论

| 事件 | 能证明 | 不能证明 |
| --- | --- | --- |
| PR 的 `CI Required` 成功 | 该 commit 通过声明的本地/托管检查 | 该 commit 的镜像已发布或 Demo 已验收 |
| `Release Build` 推送 digest | 同一 commit 构建出唯一应用镜像 | 该 digest 已部署或运行健康 |
| `Deploy Demo` dry-run 通过 | 配置、manifest 和预检结构正确 | 已连接 Demo 主机或启动容器 |
| `Deploy Demo` 受保护部署完成 | 固定 digest 已启动 | 24h 观察/对账/恢复已通过 |
| `summarize_observation.py --strict-release` 通过 | 指定窗口满足观察门禁 | 历史窗口自动适用于新 digest |

## 2. 发布前输入

1. `workflow_run` 触发的 `Release Build` 产物 `release-evidence`：
   `release-manifest.json`、`sbom.cdx.json`、`trivy.sarif`。
2. manifest 中的 `commit`、`image_digest`（`repo@sha256:<64 hex>`）、
   `workflow_run`、依赖约束文件 SHA-256、测试/前端 artifact 摘要。
3. 运维填写并审批的变更单：变更内容、风险、schema 兼容结论、回滚目标 digest、
   批准人、观察窗口。不得在 workflow 中自动生成批准。
4. 受保护 GitHub Environment `demo` 的 secrets/vars（见 `deploy/demo.env.example`
   的键名）。本仓库不保存实际值。

## 3. 发布预检（fail-closed）

在任何 checkout `inputs.commit` 或执行带 secrets 的仓库脚本之前，必须先通过
**发布 provenance 门禁**：Release Build run 必须是 `workflow_run` 触发、位于 `main`、
`conclusion=success`、`head_sha` 等于请求 commit，且 release manifest 的
release_id/commit/image_digest/environment 与请求一致：

```bash
python scripts/verify_release_provenance.py \
  --repository "$GITHUB_REPOSITORY" \
  --run-id "$RELEASE_BUILD_RUN_ID" \
  --manifest artifacts/release/release-manifest.json \
  --commit "$COMMIT" --digest "$IMAGE_DIGEST" --release-id "$RELEASE_ID" \
  --run-json-out artifacts/release/release-build-run.json \
  --json-out artifacts/release/provenance.json
```

远端 dry-run（不接触主机）使用 `--no-host-evidence`，把 DR/schema/rollback 主机文件
检查标记为 `HOST_EVIDENCE_DEFERRED`；它不是部署批准。受保护 Demo runner 在启动任何
容器之前必须再执行一次完整预检（不带 `--no-host-evidence`）：

受保护工作流的证据与输入规则（父级第 2 轮核验后）：

- Release Build 只上传 `reports/` 一个根，下载后 `release-manifest.json` 位于 artifact
  顶层，启动 smoke 位于 `compose-smoke/compose-smoke.json`；混用多个根会让 upload-artifact
  取共同祖先，把 manifest 挪到别处。
- 消费工作流把证据下载到 `${{ runner.temp }}/qts-release`。第二次 checkout 默认
  `clean=true`，会删除工作树里未跟踪的 `artifacts/`，所以证据不能放在 checkout 目录内。
- 操作员输入（run id、commit、digest、release id、观察时长）只经 step `env` 传入，先用
  `[[ ... =~ ... ]]` 校验格式，再以 `"$VAR"` 引用；`run:` 脚本里不展开 `${{ inputs.* }}`。
- Demo secrets 只注入真正需要它们的步骤；provenance 步骤不需要 Demo 密钥。
- `--release-manifest` 始终由工作流传入；本地不传时预检只做配置检查，并给出警告
  `RELEASE_MANIFEST_NOT_PROVIDED`，不冒充已复核证据。

```bash
# 远端 dry-run（不连接主机）
python scripts/deploy_preflight.py \
  --release-manifest artifacts/release/release-manifest.json \
  --no-host-evidence \
  --json-out artifacts/release/preflight.json

# 目标 Demo 主机上、启动容器前（完整检查）
python scripts/deploy_preflight.py \
  --release-manifest artifacts/release/release-manifest.json \
  --json-out artifacts/release/preflight-host.json
```

预检只输出 reason code 和配置键名，不输出秘密值。任一错误即停止发布：

| 类别 | 典型 reason code |
| --- | --- |
| 环境 | `BINANCE_ENV_NOT_DEMO` |
| 凭据 | `POSTGRES_PASSWORD_DEFAULT_OR_MISSING`、`ALERT_SIGNING_SECRET_MISSING`、`AUTH_REQUIRED_NOT_ENABLED`、`CONTROL_PASSWORD_HASH_MISSING` |
| Demo 账户/范围 | `ACCOUNT_ID_MISSING`、`SYMBOLS_MISSING`、`INSTRUMENTS_MISSING`、`INSTRUMENT_SYMBOL_MISMATCH`、`STRATEGY_MODULE_MISSING`、`STRATEGY_WARMUP_MISSING` |
| 告警/DR | `EXTERNAL_ALERT_MISSING`、`EXTERNAL_ALERT_HEARTBEAT_MISSING`、`DR_REQUIRED_NOT_ENABLED`、`DR_EVIDENCE_PATH_MISSING`、`BACKUP_RPO_EXCEEDED`、`OFF_HOST_BACKUP_NOT_ATTESTED`，以及 `trader.ops.dr_evidence` 的 `DR_*` |
| Release 绑定 | `RELEASE_ID_MISSING`、`RELEASE_COMMIT_INVALID`、`RELEASE_IMAGE_DIGEST_MISSING`、`RELEASE_IMAGE_DIGEST_MISMATCH`、`RELEASE_MANIFEST_*_MISMATCH` |
| Release 证据 | `RELEASE_MANIFEST_EVIDENCE_MISSING:<key>`、`RELEASE_MANIFEST_STARTUP_SMOKE_FAILED` |
| schema/回滚 | `SCHEMA_COMPAT_EVIDENCE_MISSING`、`SCHEMA_COMPAT_EVIDENCE_INVALID`、`ROLLBACK_COMPAT_EVIDENCE_MISSING`、`ROLLBACK_DIGEST_MISMATCH`、`ROLLBACK_RELEASE_DIGEST_MISMATCH` |

预检通过只表示“允许进入受控发布”，不是健康或验收证明。禁止用
`docker compose config --quiet` 代替真实启动；启动验证见第 6 节。

## 4. 停止新增风险与目标身份

1. 通过既有 Control 入口 `BLOCK_NEW_RISK` 停止新增风险；查询、撤单和策略允许的
   风险降低动作保持可用。**不得直接写 KillSwitch 投影、删除事件或改余额。**
2. 等待 `QUEUED`/`SENDING`/`UNKNOWN` 命令收敛；未知结果不得假定安全，不得自动重发。
3. 确认目标 Compose project、端口、卷与现有 `qts-postgres`/`qts_pgdata` 不同：
   `deploy/compose.demo.yml` 强制 `QTS_COMPOSE_PROJECT`，端口默认
   65432/18090/18085，卷名带 project 前缀。
4. 确认当前执行 owner/fencing token 唯一；旧 owner 不得继续写入。

## 5. 数据副本上的迁移与兼容验证

仅对任务自有的临时 schema/容器执行；**禁止对已有 PG 数据或卷做迁移演练**。

1. expand-only：迁移脚本只允许 `CREATE TABLE/INDEX`、`ALTER TABLE ... ADD COLUMN`；
   `test_cicd_migration_safety.py` 静态拒绝 `DROP TABLE/COLUMN/SCHEMA/TYPE/INDEX`、
   `TRUNCATE`、`ALTER COLUMN`、`DELETE FROM`、`UPDATE ... SET`、`INSERT INTO`、
   `RENAME COLUMN/TO`。该检测器是破坏性语句黑名单，不是完整 SQL 白名单解析；
   其余语句仍需人工审查，不能把“测试通过”当成任意 DDL 都被允许。
2. 旧版读取：`test_cicd_migration_compat.py` 在 v2 数据副本上应用 v3，六个经济表
   （accounts/commands/executions/ledger/events/outbox）逐表行数与 SHA-256 不变；
   新 projection 表为空，可由 `rebuild_query_projections()` 重建。
3. 旧 runtime 不得打开新 schema：`initialize_trading_schema` 在
   `max(version) > 当前 runtime 最新版本` 时抛错，自动回滚被拒绝，转人工。
4. 事件回放/投影重建：只重建可丢弃的查询投影；账务事实、事件日志、余额和历史不得
   被“回滚”删除或覆盖。回放/守恒证据必须绑定当前 commit 与 schema 版本。
5. 若迁移不可逆、旧版不兼容或守恒校验失败：停止发布，保持新增风险关闭，按第 9 节
   转人工向前修复或专项恢复。

## 6. 固定 digest 启动与对账

```bash
docker compose -f compose.yml -f deploy/compose.demo.yml \
  --env-file <受控 env> pull postgres control execution research history backup alert-sink
docker compose -f compose.yml -f deploy/compose.demo.yml \
  --env-file <受控 env> up -d --no-build
```

启动后必须同时满足：

- 每个被启动容器 `healthy`（`scripts/compose_smoke.py` 会把 `starting`、`none`、
  `unhealthy` 全部判失败）；
- `/health/live` 与 `/health/ready` 为 200；`/health/trading` 只有在恢复/对账/租约/
  DR 全部通过后才为 200，否则 503 + reasons；
- 单一有效 owner/fencing token；无 `UNKNOWN` 命令、无未入账成交、无未解释余额差异；
- REST 对账已重新执行，WS/REST 并发同一成交只入账一次；
- 告警心跳/ack、外部告警路径、备份与异机证明正常；
- Control 无交易凭据，Execution 只引用挂载的 secret 文件，非 Demo 环境拒绝启动。

`scripts/compose_smoke.py --image <repo@sha256:...> --require-digest` 是隔离启动的最低
证据；它使用任务自有 project，验证前后既有资源身份完全一致（包括原本不存在），并
确认清理后无残留容器/卷。

正式 Demo 部署由 `deploy-demo.yml` 执行，并在容器启动前后各跑一次可执行门禁：

```bash
# 启动前：新增风险已关闭、命令收敛、单一 owner/有效 fencing
python scripts/deploy_gate.py --mode pre \
  --project "$QTS_COMPOSE_PROJECT" \
  --compose-file compose.yml --compose-file deploy/compose.demo.yml \
  --control-url "http://127.0.0.1:${QTS_DEMO_CONTROL_PORT:-18090}" \
  --account-id "$QTS_ACCOUNT_ID" \
  --expected-release-id "$QTS_RELEASE_ID" \
  --expected-commit "$QTS_RELEASE_COMMIT" \
  --expected-digest "${QTS_RELEASE_IMAGE#*@}" \
  --output artifacts/deploy/pre-gate.json

# 启动后：全部容器 healthy、固定 digest/label、/health/trading ready、无未入账成交
python scripts/deploy_gate.py --mode post ... --output artifacts/deploy/post-gate.json
```

发布前已通过 Control `BLOCK_NEW_RISK` 阻断新增风险，因此 post 门禁允许
`/health/trading` 返回 503 且 reasons 仅有 `NEW_RISK_BLOCKED`；出现任何其他原因即失败。
post 门禁通过后，才由操作员通过既有 Control 入口恢复新增风险，再进入 Demo 证据窗口；
运行时绑定在证据工作流中、恢复后采集。

`deploy-demo.yml` 与 `demo-evidence.yml` 共用同一个 concurrency 组
（`qts-demo-<QTS_COMPOSE_PROJECT>`），同一 Demo 目标的部署与证据采集不会并发互相替换。

post 门禁在 `--wait-timeout` 与 `--poll-interval` 的边界内轮询：`deploy-demo.yml` 通过
env 传入 600s / 15s；`deploy_gate.py` 的 argparse 默认是 0（单次判定），轮询节奏由工作流
显式启用。`up -d` 返回时容器仍处于 `starting`，单次判定会把启动竞态当成失败；pre 门禁
不等待。

事实来自真实 PostgreSQL：`psql -tAc` 把布尔渲染为 `t`/`f`，实现据此解析；空值或不可解析
的值是“未知”，门禁按未知失败处理（`FACTS_INVALID:<key>`），不按零处理。可复现命令：

```bash
python scripts/verify_deploy_gate_live.py --output artifacts/cicd/raw/deploy-gate-live.json
```

该脚本在任务自有 Compose project（独立端口/卷）中通过真实 `docker compose exec` 采集并
验证，随后销毁该项目；它不接触既有 `qts-postgres`/`qts_pgdata`。

## 7. Demo E2E 与 24h 观察

仅通过 `demo-evidence.yml`（手动、受保护环境、self-hosted Demo runner）或本地受控入口
执行；不得自动触发真实外部账户操作，不索取 testnet 凭据。

```bash
python scripts/observe_trading_runtime.py --duration 86400 --interval 60 \
  --out artifacts/demo/observation.jsonl
# 运维按演练脚本写入 runtime_restart_start/end 与 active_reconnect 标记
python scripts/summarize_observation.py \
  --input artifacts/demo/observation.jsonl \
  --min-hours 24 \
  --strict-release \
  --release-evidence artifacts/demo/release-binding.json \
  --expected-release-id <release-id> \
  --expected-commit <40hex> --expected-digest sha256:<64hex> \
  --json-out artifacts/demo/observation-summary.json
```

显式阈值（均可在命令行审计）：

| 指标 | 默认 | 说明 |
| --- | --- | --- |
| `--min-ready-ratio` | 0.60 | 已验收历史 Demo 基线为 0.6252；0.60 是保守下限，不追溯否定历史证据 |
| `--max-reconciliation-age-ms` | 60000 | 末样本必须存在，超出即阻断 |
| `--max-outbox-backlog` | 0 | 看末样本；窗口内瞬时积压单独记录 |
| `--max-unbooked-fills` | 0 | 看末样本；瞬时未入账后补齐可接受 |
| 重复/重发/覆盖证据 | 0 | 样本含 `duplicate_*`/`resend_*`/`overwrite_*` 数值键且 >0 时阻断；当前运行时主要经 `unresolved` 的 `DUPLICATE`/`RESEND`/`OVERWRITE`/`REPLAY` 代码触发同一 blocker |

当前 `TradingService.metrics()` 不产出上述数值键；数值键是向前兼容的显式入口，不能把“运行时未产出”当作零重复证据。

`--strict-release` 还要求：窗口 **不少于 24 小时**（`--strict-min-hours` 不得低于 24，
`--min-hours` 更小时按 24 计算并给出 `STRICT_WINDOW_SHORTER_THAN_24H`）；每个样本都必须
带 `reconciliation_age_ms`、`outbox_backlog`、`unbooked_fills`、`unknown_commands`、
`balance_anomalies` 显式计数器（缺失或类型非法即阻断，缺席不等于零）；release evidence
必须是目标主机实际 inspect 得到的运行时绑定（`runtime_inspected=true`、容器 label/digest
与 release 一致、`/health/trading` ready），commit/digest/environment 与期望一致。
缺失、过期或不匹配一律阻断。历史兼容读取模式不使用 strict，但不会把旧证据自动套到
新 commit/digest。

strict 进一步核对**实际事实**而不是声明位（`scripts/release_binding.py`）：

- binding 必须无采集错误（`errors` 为空）、`generated_at` 是真实带时区时间戳、
  control/execution 容器 present/running/healthy、镜像 digest 与 release label 与期望一致、
  健康端点 live/ready/trading 全部就绪；
- binding 必须带 `runtime` 身份块（release_id/commit/image_digest/environment/owner）；
- **每个样本**必须带同一份运行时身份（`runtime` 字段）且 `observed_at_ms` 与样本时间一致，
  否则 `SAMPLE_RUNTIME_IDENTITY_MISSING` / `SAMPLE_RUNTIME_IDENTITY_MISMATCH` 阻断；
- binding 的采集时间必须落在样本窗口内（否则 `RELEASE_BINDING_WINDOW_MISMATCH`）——
  这就是为什么证据工作流在观察窗口结束**之后**才采集绑定。

观察器的窗口从首个实际记录样本起算（HTTP client 初始化之后），末样本取到“首样本时间 +
请求时长”或之后，所以“请求 24h”得到的是 ≥24h 的窗口，不因初始化/请求耗时或
“时长减一个采样间隔”而缩短。

运行时绑定由目标主机采集，不能由操作者手写：

```bash
python scripts/collect_runtime_release_binding.py \
  --project "$QTS_COMPOSE_PROJECT" \
  --control-url "http://127.0.0.1:${QTS_DEMO_CONTROL_PORT:-18090}" \
  --expected-release-id "$QTS_RELEASE_ID" \
  --expected-commit "$QTS_RELEASE_COMMIT" \
  --expected-digest "${QTS_RELEASE_IMAGE#*@}" \
  --output artifacts/demo/release-binding.json
```

## 8. 观察通过后的发布提交

只有以下全部成立才可记录发布完成：

1. 预检、固定 digest 启动、健康、对账、告警、备份/DR 通过；
2. 观察窗口满足阈值且证据绑定当前 commit + digest + Demo 环境；
3. 无未解决差异、无重复发送、无账务异常；
4. 人工批准记录和变更单齐备。

发布完成仍不等于生产/真实资金获批；生产是独立项目。

## 9. 回滚与恢复边界

| 情形 | 动作 |
| --- | --- |
| 发布前失败 | 保持原版本，不打开新增风险；修复后重新预检 |
| 启动失败且 schema 兼容 | 固定旧 digest 重启，重新 REST 对账，确认不重复发送；旧 digest 必须在 manifest 中 |
| 事件/账务/风控语义异常 | 立即禁止新增风险，人工核对交易所与 append-only 事实；不得删除事件、改余额或撤销历史 |
| 不可逆 schema/旧 runtime 不兼容 | 禁止自动回滚；停止发布，人工专项向前修复或恢复 |
| 数据灾难 | 按已验证 DR 恢复到独立环境，校验 RPO/RTO、事实摘要和守恒后再人工切换 |

`scripts/model_rollback_manager.py` 不是运行系统回滚器。模型/策略回滚是独立业务变更：
固定已验证策略 artifact 和配置，走候选/影子/人工审批/暂停接口，不能以改 registry
文件替代停止信号、预算重估、在途订单处理和审计。

## 10. 证据清单

| 证据 | 路径/产物 |
| --- | --- |
| 发布 provenance | `provenance.json`、`release-build-run.json`（成功 main Release Build + manifest 绑定） |
| CI 必需检查 | `CI Required` job；各 job JUnit 与 coverage artifact |
| 测试分类 | `reports/test-manifest.json` |
| 镜像 | `repo@sha256:<64 hex>`、`release-manifest.json` |
| 固定 digest 启动 | `compose-smoke.json`（发布路径必须 `passed=true` 且 digest 已核验） |
| SBOM/扫描 | `sbom.cdx.json`、`trivy.sarif` |
| 预检 | `preflight.json` |
| 隔离启动 | `compose-smoke.json`（含 health、清理、既有资源身份） |
| 部署前后门禁 | `pre-gate.json`、`post-gate.json`（风险关闭/收敛/单 owner/健康/守恒） |
| 运行时绑定 | `release-binding.json`（`runtime_inspected=true`、`errors=[]`、容器 label/digest/health、`runtime` 身份块） |
| 样本身份 | 每个观察样本的 `runtime` 字段（release_id/commit/image_digest/environment/owner/observed_at_ms） |
| 真实采集验证 | `scripts/verify_deploy_gate_live.py` 的 JSON 输出（真实 `docker compose exec` + `psql`） |
| 迁移/守恒 | `test_cicd_migration_safety.py`、`test_cicd_migration_compat.py` 的 JUnit |
| Demo E2E/观察 | `e2e.json`、`observation.jsonl`、`observation-summary.json`、release binding |
| 回滚目标 | manifest 中的 previous digest、rollback compat evidence |

所有证据必须绑定 commit + digest + 环境；任何未执行项在交付报告中明确列出，不得以
历史 artifacts 或旧 digest 结果替代。
