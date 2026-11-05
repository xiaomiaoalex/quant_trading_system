# CI 测试分类与门禁

`test_manifest.json` 是 `trader/tests/test_*.py` 的唯一分类来源。每个文件必须且只能
属于一个组：

| 组 | CI Job | 网络策略 | skip 政策 |
| --- | --- | --- | --- |
| `p0` | `P0 Regression Tests` | `QTS_TEST_NETWORK_POLICY=offline` | JUnit 强制 `skipped=0`、`xfailed=0` |
| `offline` | `Offline Unit and Integration Tests` | `offline` | 同上；该 Job 同时选择 `p0,offline` 以让 coverage 与已测基线一致，并执行 per-module coverage gate；快速反馈仍由独立 P0 Job 提供 |
| `postgres` | `Postgres Integration Tests` | `loopback` + `REQUIRE_POSTGRES=1` | 跳过即失败；JUnit 强制 `skipped=0` |
| `ops` | `optional-gates.yml` 的 `optional-ops-drills` | `open` | 非必需；skip 可见但不计为通过 |
| `external` | `optional-gates.yml` 的 `optional-external-research` | `open` | 非必需；可选依赖缺失可 skip，但不计为通过 |

`ci_subsets.research_ai` 是一个审计过的子集（文件仍属于上面的组），由
`Research and AI Gate` 以 `QTS_TEST_SUBSET=research_ai` 运行：回放一致性、影子/
Paper 晋级、Paper 不启用 Live、HITL 审批禁止直接执行、研究准入安全。它不是新的
分类组，不改变文件的唯一归属。

`trader/tests/conftest.py` 在收集阶段读取 manifest：

- 未分类文件直接使收集失败（exit 4），新增测试不能静默逃逸所有门禁；
- `QTS_TEST_GROUP=p0,offline` 只保留所选组，未选组不会被记为 skip；
- `QTS_TEST_NETWORK_POLICY` 拦截 socket：
  - `offline`：禁止所有 connect/DNS，仅放行 CPython asyncio 自管道所需的
    `socket.socketpair()` 内部 loopback；
  - `loopback`：只放行 `127.0.0.1`/`::1`/`localhost`；
  - `open`：仅显式 external/ops 运行使用。
- `REQUIRE_POSTGRES=1` 时，Postgres 组若缺 asyncpg/DSN 直接 usage error；运行期任何
  Postgres 测试被跳过会改写为失败。

`test_api_endpoints.py` 归类为 `postgres`：其中 8 个 durable KillSwitch/风险状态用例
在缺少 PostgreSQL 时会 skip，放入 P0/offline 会制造假绿；整个文件在 Postgres Job 中
以完整 API 风险状态用例执行，而不是只用 `-k` 选择参数校验。

工作流选择由 `scripts/verify_test_manifest.py` 审计：缺少必需 Job、Job 未引用其组、
raw `trader/tests/test_*.py` 列表、`-k` 或 `--ignore` 都会失败。`CI Required` 的
`needs` 必须恰好覆盖 `ci_jobs.required` 与 `ci_required_non_test_jobs`，不能遗漏必需
Job，也不能把 optional Job 纳入聚合。新增/删除测试文件时必须同步 `test_manifest.json`。

## 发布与部署门禁（父级核验 F-P1..F-P8）

| 门禁 | 脚本 | 失败后果 |
| --- | --- | --- |
| 发布 provenance | `scripts/verify_release_provenance.py` | 受保护 Demo 工作流在 checkout `inputs.commit` 或使用 secrets 前即失败 |
| 运行时绑定 | `scripts/collect_runtime_release_binding.py` | strict 观察要求 `runtime_inspected=true` 的容器/镜像/label/health 绑定 |
| 部署前后 | `scripts/deploy_gate.py --mode pre/post` | 风险未关闭、命令未收敛、owner/fencing 不一致、容器不 healthy、digest 不符即失败 |
| 发布证据 | `scripts/release_manifest.py`（默认强制） | JUnit/coverage/frontend/SBOM/scan/启动 smoke 缺失或不通过即失败 |
| 预检 | `scripts/deploy_preflight.py` | 非 Demo、默认/占位凭据、缺失 allowlist/warmup/外部告警/DR、digest 或证据不匹配即失败 |

观察门禁（`scripts/summarize_observation.py --strict-release`）额外要求：窗口 ≥24h、每个
样本显式包含 `reconciliation_age_ms`/`outbox_backlog`/`unbooked_fills`/`unknown_commands`/
`balance_anomalies`、release evidence 为目标主机实际采集的运行时绑定。

strict 不信任 binding 的声明位（`scripts/release_binding.py`）：binding 必须无采集错误、
带真实时间戳、control/execution 容器 present/running/healthy 且 digest/label 与 release
一致、健康端点就绪，并带 `runtime` 身份块；**每个样本**必须携带同一份运行时身份且
`observed_at_ms` 与样本时间一致；binding 的采集时间必须落在样本窗口内。观察器在
deadline 时刻补一次样本，所以请求 24h 得到 ≥24h 的窗口。

部署门禁的事实来自真实 PostgreSQL：`psql -tAc` 的布尔渲染为 `t`/`f`，缺失或不可解析的
事实按未知失败处理；命令收敛包含 UNKNOWN/QUEUED/SENDING；post 门禁在 `--wait-timeout`
内轮询。`scripts/verify_deploy_gate_live.py` 用任务自有 Compose project 复现真实
`docker compose exec` 路径。

受保护工作流只上传 `reports/` 一个 artifact 根，证据下载到 checkout 目录之外（第二次
checkout 默认 clean 会删除工作树内的 artifacts）；操作员输入只经 step `env` 传入并按格式
校验后再引用。
