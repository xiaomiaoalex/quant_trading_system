# 阶段 5 独立测试报告（测试员）

| 项 | 值 |
|---|---|
| report_id | `P5-IND-TEST-R1-ff7eef49` |
| workflow_version | 3 |
| phase | 5 |
| role | tester |
| agent_id | `ff7eef49-9651-49cc-8215-d05d3c2f14ea` |
| round | 2（offhost 复验；§1–§12 的 round1 记录原样保留） |
| engineer | `5a4dfb1a-57a7-402d-9bd1-0e9e6ffe568f` |
| parent（不发送日常报告） | `18e99d90-33f5-41f9-8a2f-672d109886bb` |
| branch | `feat/foundation-refactor` |
| base_commit | `7995c02d4a704aa3c201edb12da3da0e6c2f16e6` |
| source_digest（round1，42 文件，历史） | `5c14dd9d1e6acfd96d5d3461150b0243034851cc9d1bba7f098b81eba730b6e2` |
| snapshot_path | `artifacts/foundation/phase5/final-source-snapshot.json`（现为 round2 的 44 文件清单） |
| snapshot_path（初版，历史） | `artifacts/foundation/phase5/tester-input-snapshot.json`（`3738b9b6…`，40 文件） |
| round2（offhost 复验）源（44 文件，round2 最终；已被 round3 取代） | `7392e8b0d8425bbb4d3005aed6fbe180614860b3b72534fe77f2d854f8fa7ab1` |
| round2 源（44 文件，上一版，历史） | `f3cd5e2fa3d2e3b76719b99db83089e381afc8d0fc0ea0ab8f73056b980f5af9`（因格式修正被取代） |
| round2 源（43 文件，历史） | `826c51ad5e662433511823722438060956f58e6005d92f0413e7f6addf92999b` |
| round2 状态 | **pass（已重新冻结）**：C14 final 26/26、C15 exit 0、全量 3847/0/0、异机证据复算一致 → 5.6/5.7 改判 pass（§13） |
| round3 源（45 文件，final frozen） | `2d963b2ca5f338e68ae0e167ce555110e7ec7c67e565640e3c0b8d537c92b447` |
| round3 源（45 文件，上一版，历史） | `af7f8f981b4ce0a586c1cc2b30d25f1c2345c41f713213b03a66b429e2643c6a`（BF-R3 修复前） |
| round3 源（44 文件，历史） | `10c41a5fa5913165da9996b50a75d8cf33df6558e35d701a653da363593014e0`（BF 修复后、我补反例前） |
| round3 状态 | **pass（final frozen）**：C18 final2 21/21、C19 final2 12/12、C20 0/0、C22 3871/0/0/0；BF-R3-1/R3-2 修复后 fresh 异机证据复核通过（§14） |
| overall（round3） | **pass**（5.6/5.7 维持 pass；round1 blocked 与 round2 记录均保留为历史） |
| overall（round2） | **pass**（5.6/5.7 由真实异机证据 + 独立反例闭合；round1 的 blocked 记录保留在 §12，不重写） |
| 报告哈希 | 本文件在 §14（BF-R3 更新）写入后已变；最终 sha256 由工程师冻结后重算并交第 4 轮审核员 |
| overall（round1，历史） | **pass**（带保留项：5.6 / 5.7 维持 blocked，不计 pass，理由见 §3/§9） |

## 0. 执行通道状态（优先披露）

本会话 `pwsh` 在命令启动前即失败，原始错误：

```text
Error: SetNamedSecurityInfoW failed (Win32 5): grantWrite(D:\Code of Python\quant_trading_system)
```

- 已重复两次，含更换 `workdir=C:\Windows\Temp` 的一次；错误对象恒为工作区根目录，与目标命令无关。
- 按工作单「命令通道受限时不要修 ACL/提权，先冻结并发 kind=execution_request」与 v3 §2.1，**未**修复 ACL、**未**改用不受原限制的终端、**未**申请提权。
- 因此本报告全部检查处于 `authored_by=tester / executed_by=pending`；**执行前不出具任何 pass**。凡下文标注 `pass 未授权` 的项，其状态一律视为「待执行确认」，不是通过。
- 文件工具（读/写/编辑）可用，独立测试已按 v3 §2.1 用文件工具完成编写并冻结。
- `source_digest` 的重算（对 40 个快照文件做 sha256 后再对 `[path,sha256]` 排序对做 sha256）属于命令操作，**同样待执行**；本报告沿用工程师冻结值并显式标注未验证。
- **digest 口径**（C12 第 1 轮结果回填后）：实现冻结 digest `3738b9b6…` 覆盖实现与其文档；本测试员新增的两个测试文件、本报告与 CI 追加属于授权作者变更，**另行计算、不并入实现 digest 口径**。

## 0.1 第 1 轮执行结果回填（executed_by=engineer，analyzed_by=tester）

| id | 结果 | 分析 |
|---|---|---|
| C1 | exit 0；JUnit `tests=58, failures=0, errors=0, skipped=0, time=17.077s`；`artifacts/foundation/phase5/tester-ff7eef49/C1.stdout.txt` / `C1.stderr.txt` | **离线独立用例全绿**。注意：报告初稿把收集数误写成 59（并称 5.9 为 20 例），实际为 40 个测试函数、5.9 为 19 例、收集 58 项，已按 JUnit 修正；断言未改动。此为报告计数笔误，非测试缺陷 |
| C11 | isort exit 1；black exit 1；diff 见 `C11.isort.diff` / `C11.black.diff` | 两处导入折行、一处 `assert` 消息折行、一处构造调用折行，共 4 处纯格式差异。已由测试员用文件工具本人修正（只动格式/导入，未动断言与测试意图）；**C11 需重跑确认** |
| C12 | 40 文件中 39 个与 `3738b9b6…` 一致；唯一差异 `.github/workflows/ci-gate.yml`（授权追加两行） | 属预期作者变更，非实现漂移；全量 digest 待格式修正后由工程师重算 |
| C2–C10 | 工程师继续受控执行中 | 结果稍后回填分析；在此之前不出具任何 pass |
| C1 rerun | exit 0；JUnit `tests=58, failures=0, errors=0, skipped=0, time=23.078s` | 格式修正后离线用例仍全绿（`offline.rerun.junit.xml`、`C1.rerun.*`） |
| C11 rerun | isort exit 0；black exit 0 | 格式门禁通过。两文件 sha256（`C13.tester-tests.sha256`）：offline `7f7638b2a8dd9c88691140450576f7ff7a9d1f527d9d09e294a27e56f55e300c`，postgres `2ed3717db94e7350071f1cc8e444108b6cf9a803a43c665ed07ce0ff44f779c7`（**格式修正后又被 fixture 修正再次改变，见下行 C2 行**） |
| C2 第 1 轮 | exit 1；`tests=11, failures=1, errors=0, skipped=0, time=26.886s`；失败 `test_5_11_a_conflicting_instance_cannot_reconcile_or_send` 于 384 行 `assert await runtime_b.start() is True` → False | **测试员 fixture 缺陷，非生产围栏缺陷**。我的 `_runtime` 让每次 reconcile 复用同一 `checked_at_ms`，而 A、B 共享同一 store；`TradingService.recover` 要求 `checked_at_ms` 严格晚于已存值（`trader/trading/service.py:770`），B 第二次启动被 `STALE_SNAPSHOT` 拒绝。工程师只读定位与我的复算一致。修正：fixture 引入模块级单调 `_VENUE_CLOCK`，provider 每次调用前进 1ms，`clock` 读取同一值；**断言与生产代码零改动**，5.11 的语义断言原样保留 |
| C3 | exit 0 | 六个既有 phase5 测试文件回归通过，G1 满足 |
| C4 / C5 | 各 exit 0 | `compose config` 通过；凭据隔离检查通过 |
| C6 | 重跑 exit 0 | 五服务健康、`health_semantics_ok=true`、`alert_delivery_ok=true`（`DR_EVIDENCE_INSUFFICIENT`/`EXECUTION_LEASE_LOST`）、凭据拒绝 exit 2、`existing_resources_untouched=true`、cleanup 0。注意：第 1 次失败源于工程师传入相对 evidence 目录，工程师随后修复 `scripts/compose_smoke.py` 的路径 resolve——这是**工程师的生产脚本修改**，需计入其冻结 digest 管理，与本测试员文件无关 |
| C7 / C8 | 各 exit 0 | 真实 crash drill 与 lease drill 通过（`C7.crash-durability.json`、`C8.lease-fencing.json`） |
| C9 | 两次 exit 0 且输出字节一致 | 9 个场景可重复（`C9.summary.json`），5.10 的可重复性得到独立确认 |
| C10 | exit 0 | 备份/恢复演练（模拟器）通过，且 gate 仍拒绝本地 target（与 T7 一致） |
| C12 | 39/40 一致 | 唯一差异 `.github/workflows/ci-gate.yml`（授权追加）；`C12.summary.json` |
| C13 | 已出两测试文件哈希；报告与 CI 哈希待补 | fixture 修正后 postgres 文件哈希已变化，需以修正后文件重算 |

## 0.2 最终批（源变更后全新冻结复验：`5c14dd9d…`，42 文件）

工程师依据本报告 §6 观察做限定修复（均在其写域，未改本测试员断言），并在新冻结源上全量重跑。本测试员对与其观察直接相关的三处修复做了**源码抽查核实**（非仅采信描述）：

- `trader/runtime/credentials.py` `_present`：任意非空串（含纯空白）即判凭据在场 → 观察b 已修，Control 对空白值 fail-closed。
- `trader/ops/dr_evidence.py`：`completed_at_ms > current_ms + CLOCK_SKEW_TOLERANCE_MS` → `DR_BACKUP_TIMESTAMP_INVALID`（restore 侧同理 `DR_RESTORE_TIMESTAMP_INVALID`）→ 观察a 已修；`restore.checks.facts_match/invariants_ok` 为 False → `DR_RESTORE_FACTS_UNVERIFIED` → 观察c 已修（gate 不再单信 `verified` 布尔）。
- `trader/trading/runtime.py` `readiness()`：主动 `renew_lease`，失租即置 `ready/started=False`、清 `lease_token` 并给出 `EXECUTION_INSTANCE_CONFLICT` → 租约丢失的可见性不再依赖下一次 IO。

| id | 最终结果 | 分析 |
|---|---|---|
| C1 final | exit 0；`tests=58, failures=0, errors=0, skipped=0, time=16.613s` | 离线独立用例在新源上仍全绿；观察b 的修复未破坏任何独立断言 |
| C2 final | exit 0；`tests=11, failures=0, errors=0, skipped=0, time=25.537s` | fixture 修正后真实 PG 侧全绿；5.8/5.11/5.9 运行态/5.6 回执/5.4 不变量全部通过 |
| C3 final | exit 0；`tests=47` | G1：工程师六文件在新源上零回归 |
| C4 / C5 | exit 0 | compose config 与凭据隔离通过 |
| C6 final | exit 0；五服务 healthy、`health_semantics_ok=true`、`alert_delivery_ok=true`、凭据拒绝 exit 2、`existing_resources_untouched=true`、cleanup 0 | compose 变更（告警签名密钥传递等）后复验通过 |
| C7 / C8 / C10 final | 各 exit 0 | crash drill、lease drill、备份/恢复演练（模拟器）通过；gate 仍拒绝本地 target |
| C9 final | 两次 exit 0、9 场景、输出字节一致 | 5.10 可重复性成立 |
| C11 final | isort exit 0；black exit 0 | 格式门禁通过。sha256：offline `7f7638b2a8dd9c88691140450576f7ff7a9d1f527d9d09e294a27e56f55e300c`；postgres `498387f0…`（fixture 修正后） |
| C12 final | 42 文件快照 `5c14dd9d1e6acfd96d5d3461150b0243034851cc9d1bba7f098b81eba730b6e2`；报告按 v3 §5 排除 | 实现修复、CI 追加与测试文件一并纳入最终冻结指纹 |
| C13 final | `C13.final.tester-files.sha256`：报告 `417a5bd1…`、ci-gate `418ed80d…` | 注意：本报告随后因写入最终结论再次变更，其哈希以审核时重算为准 |
| 全量套件 final | exit 0；`tests=3818, failures=0, errors=0, skipped=0, time=247.680s` | 仓库级零回归 |
| 未解决缺口 | 无经核实的异机 S3 兼容目标 | 5.6 / 5.7 维持 blocked（见 §3、§9），不得以模拟器结论计 pass |

## 1. 独立期望来源（G2）

期望全部来自以下三处，**不来自工程师的绿数**：

1. `artifacts/foundation/phase5/parent-phase5-work-order.md`（范围与禁止项）。
2. `docs/FOUNDATION_REFACTOR_PHASE0.md` 5.1–5.11 验收表（冻结版）。
3. `docs/INTERFACE_CONTRACTS.md` §13.16（健康三级语义、凭据边界键名、拒绝码、告警映射、DR 证据形状与理由码）；§8.4 `docs/PROJECT_ARCHITECTURE.md`（Compose 拓扑与参数化）。

设计流程：先从契约推导反例与断言值，再读实现核对接口形状（`trader/ops/dr_evidence.py`、`trader/ops/backup.py`、`trader/services/alerting.py`、`trader/trading/runtime.py`、`trader/runtime/credentials.py` 等）。实现中的具体返回值未被用作期望值来源；仅用于确认测试所调用的公共接口签名与字段名存在，避免测试因接口拼写而非行为失败。

## 2. 新增独立测试资产

| 文件 | 覆盖 | 说明 |
|---|---|---|
| `trader/tests/test_phase5_independent_offline.py` | 5.1、5.2、5.3、5.4、5.5、5.6（节律/清单/回执）、5.9 | 纯离线：只读 `compose.yml`，仅自建 127.0.0.1 接收端，不触 PG/网络/容器 |
| `trader/tests/test_phase5_independent_postgres.py` | 5.8、5.11、5.9（运行态）、5.4（不变量）、5.6（回执注入） | 真实 PG：自建唯一 schema `qts_p5ind_<hex>` / 一次性数据库 `qts_p5ind_bk_<hex>`，退出即删 |
| `.github/workflows/ci-gate.yml`（仅追加两行） | CI | `test_phase5_independent_postgres.py` 追加到既有 Postgres job；`test_phase5_independent_offline.py` 追加到既有 Trading Core job；未改动其他门禁 |

离线 40 个测试函数（其中 5.9 为 19 例参数化反例，收集计 58 项，与 C1 的 JUnit `tests=58` 一致）；PG 用例 11 个（含 3 个真实进程级故障注入）。所有密钥、DSN 均不出现在测试、报告或证据中；`POSTGRES_CONNECTION_STRING` 仅经环境变量读取。

## 3. 分项结论（5.1–5.11，round1 最终批）

下表状态列为最终批（§0.2，最终冻结源 `5c14dd9d…`）后的结论；`executed_by=engineer`、`analyzed_by=tester`。

| 项 | 独立覆盖 | 结论 |
|---|---|---|
| 5.1 Compose 五服务 | 独立解析 `compose.yml`：五服务齐全、无固定 `container_name`、`name` 参数化、四入口指向新栈 `trader.runtime.*_main` | **pass**（C1–C10 final） |
| 5.2 Control 无写凭据 | 独立复算 Control env 与四个禁键交集=空；Control 无 secrets/无共享 `.env`；`control_main.lifespan` 对每个禁键逐个注入后启动即抛 `CONTROL_EXCHANGE_CREDENTIALS_PRESENT`；跨检 `scripts/check_credential_isolation.py`（T7 双向） | **pass**（C1–C10 final） |
| 5.3 Execution 拒绝启动 | 理由码矩阵（缺失/不可读/非沙箱）；真实子进程 `python -m trader.runtime.trading_worker_main` 退出码 2 + `EXECUTION_STARTUP_REFUSED reason=...` + 值不泄露；变异对照（改环境→换理由码）证明非恒真 | **pass**（C1–C10 final） |
| 5.4 三级健康 | `live` 无 reasons；`ready` 降级 200+`STORAGE_UNAVAILABLE`；`trading` 503+reasons；探针异常→503 非 500；ready 永不吞 reasons；真实 `TradingRuntime.readiness()` 上断言 `ready == (not reasons)` | **pass**（C1–C10 final） |
| 5.5 真实接收端 | 自建真实 loopback `ThreadingHTTPServer` + 仓库 `alert_receiver`：三类阻断各收到一次且去重；`{"ack":true}` 才算 delivered；200 无 ack→`ALERT_ACK_MISSING` 且保持 pending；500→重试（≥8 次请求）；心跳可达可观测；无心跳 URL→`ALERT_HEARTBEAT_URL_NOT_CONFIGURED`；HMAC 错→401 拒收 | **pass**（C1–C10 final） |
| 5.6 RPO≤5m + 异地回执 | 节律>300s 配置期拒绝；≥2 次成功完成才算满足 RPO；失败清单不可作为成功清单；无目标→`S3_TARGET_NOT_CONFIGURED` 且落盘 failed；本地模拟器 target 恒为 `off_host=false/identity_verified=false`；真实 PG 上回执回读篡改/尺寸不符/元数据不符→记录 failed（T7 注入） | **blocked**（缺经核实异机目标；节律/清单/回执语义与真实 PG 回执注入用例已全部通过，异地收据本身无法验证） |
| 5.7 RTO≤60m + 恢复事实核验 | DR 证据中 `rto_seconds>3600`/缺失→`DR_RTO_EXCEEDED`；`verified` 由 `facts_match ∧ invariants_ok` 派生（实现核对）；无异地目标与恢复库，RTO 无法被真实测量 | **blocked**（缺经核实异机目标与独立恢复库；RTO 无法被真实测量，`rto_seconds>3600` 的 gate 反例已通过） |
| 5.8 零丢失 | 真实杀掉任务自有 writer 子进程（`SIGKILL`/TerminateProcess），verifier 子进程对 6 张事实表比对 counts+digests+commands+execution_keys+ledger+balances+invariants 全真、`resend_count==0`；T7：删 1 行 ledger → passed False、digests/counts False、计数 -1 | **pass**（C1–C10 final） |
| 5.9 非 READY | 19 个证据变异（schema/时间/节律/摘要/off_host/模拟器/未验证/RTO/摘要不一致/过期/同库/空白标识）全部保持非 READY 且给出对应理由码；真实 `TradingRuntime` 注入失败 gate → 拒新风险且 `exchange.sent==[]`；T7：gate 通过→同 runtime 可下单 | **pass**（C1–C10 final） |
| 5.10 演练可重复 | 依赖 `scripts/rehearse_trading_runtime.py` 连续两次执行比对（工程师代执行）；测试侧已覆盖其依赖的接收端与告警语义 | **pass**（C1–C10 final） |
| 5.11 租约丢失 | 真实 PG：A 取 token 1；B 冲突→`EXECUTION_INSTANCE_CONFLICT`、零 reconcile（无 `reconcile` 阶段）、零发送；A 租约到期→B 接管 token 2（严格递增）；A 在 IO 边界被拒、ready/started 置 False、readiness 含冲突码；B 可下单 | **pass**（C1–C10 final） |

## 4. 故障注入证明（T7，防止检查恒真）

| id | 注入 | 期望翻转 |
|---|---|---|
| T1 | `compose.yml` 变异副本注入 `BINANCE_API_KEY` | 独立复算报违规；`check_compose` 报 `CONTROL_HAS_EXCHANGE_CREDENTIALS`；对照：真实文件违规为空 |
| T2 | 真实 Execution 子进程输入从「缺凭据」改为「非沙箱环境」 | 退出码仍 2，但理由码从 `BINANCE_CREDENTIALS_MISSING` 变为 `BINANCE_ENV_NOT_SANDBOX`，且缺失码不再出现 |
| T3 | 5.8 删除一行 `trading_ledger` | `verify_after_crash` 从 passed True → False，digests/counts 翻转，计数 −1 |
| T4 | DR 证据 19 种变异 | 每种均保持非 READY 且给出对应理由码；正向对照：完整证据 ready=True 且 reasons=() |
| T5 | 5.9 真实 runtime 注入失败 DR gate | 拒绝新风险、`exchange.sent==[]`；T7 对照：gate 通过→同一 runtime 下单成功 |
| T6 | 5.6 回执回读篡改 / head 尺寸 −1 / 元数据摘要改写 | 记录 `S3_READBACK_DIGEST_MISMATCH` / `S3_RECEIPT_SIZE_MISMATCH` / `S3_RECEIPT_DIGEST_MISMATCH`，且 `latest-backup.json` 不能被 `load_manifest` 当作成功 |
| T7 | 本地模拟器 target 进入 DR 证据 | `DR_TARGET_NOT_VERIFIED_OFF_HOST`，`details.target_kind=="local_emulator"`；正向对照：`s3_compatible`+异地+已验证→通过 |

## 5. G1–G6

| 项 | 结论 |
|---|---|
| G1 既有断言不回归 | 未改任何既有测试、断言、全局 fixture；仅追加两份新文件与 CI 两行 |
| G2 期望独立 | 见 §1；实现返回值未用作期望源 |
| G3 真实执行不跳过 | PG 用例在无 DSN 时显式 `pytest.skip`（环境不可用，非检查缺失），并要求以显式 `POSTGRES_CONNECTION_STRING` 运行；不做静默降级 |
| G4 同一 source_digest | 最终批绑定 `5c14dd9d…`（42 文件，含实现修复、CI 追加与本测试员两份测试文件；报告按 v3 §5 排除）。历史初版 `3738b9b6…`（40 文件）仅作修正过程追溯 |
| G5 角色与写域 | 仅新增 2 个测试文件 + 本报告 + CI 追加；未改生产代码/契约/工程师测试/全局 fixture；未 commit/push/deploy |
| G6 文档/CI/接口一致 | 理由码与键名逐条对照 §13.16 与实现常量；CI 追加行与仓库既有格式一致 |

## 6. 观察与处置（round1 最终批）

1. **未来时间戳的备份会被视为新鲜** → **已修复并经源码核实**：`dr_evidence.py` 增加 `completed_at_ms > current_ms + CLOCK_SKEW_TOLERANCE_MS → DR_BACKUP_TIMESTAMP_INVALID`（restore 侧 `DR_RESTORE_TIMESTAMP_INVALID`）。
2. **纯空白禁键值不计为凭据在场** → **已修复并经源码核实**：`credentials.py` `_present` 对任意非空串（含纯空白）返回 True，Control fail-closed。
3. **DR gate 只信任 `restore.verified` 布尔** → **已修复并经源码核实**：`checks.facts_match` 或 `checks.invariants_ok` 为 False → `DR_RESTORE_FACTS_UNVERIFIED`。
4. **`UNKNOWN_COMMAND` / `BALANCE_MISMATCH` 未在 compose smoke 中对真实接收端投递** → 由本测试员 5.5 真实接收端用例补齐并在 C1 final 全部通过；C6 smoke 本身仍只投递两类阻断码，属演练脚本覆盖范围限制，非实现缺口。

## 7. 检查清单（`checks`：argv 由测试员编写 `authored_by=tester`；执行结果由工程师受控代跑 `executed_by=engineer`，分析 `analyzed_by=tester`）

| id | argv（`authored_by=tester`） | required | exit / passed / failed / skipped / xfailed | output_path |
|---|---|---|---|---|
| C1 | `python -m pytest -q trader/tests/test_phase5_independent_offline.py --tb=short -p no:randomly --junitxml=artifacts/foundation/phase5/tester-ff7eef49/offline.junit.xml` | 是 | **已执行（两轮）**：两轮均 exit 0，58 通过 / 0 失败 / 0 跳过 / 0 xfail（第 1 轮 time=17.077s；格式修正后复跑 time=23.078s） | `offline.junit.xml` / `offline.rerun.junit.xml` / `C1.rerun.*` |
| C2 | `POSTGRES_CONNECTION_STRING=<env> python -m pytest -q trader/tests/test_phase5_independent_postgres.py --tb=short -p no:randomly --junitxml=artifacts/foundation/phase5/tester-ff7eef49/postgres.junit.xml` | 是 | **第 1 轮失败**（exit 1，1/11：5.11 因测试员 fixture 时钟缺陷触发 `STALE_SNAPSHOT`）；fixture 已修正（§0.1、limitations 5），**待复跑** | `C2.stdout.txt` / `postgres.junit.xml` |
| C5 | `python scripts/check_credential_isolation.py --compose compose.yml` | 是 | **已执行**：exit 0 | `C5.*` |
| C6 | `python scripts/compose_smoke.py --project qts_ind_ff7eef49 --evidence-dir artifacts/foundation/phase5/tester-ff7eef49`（唯一 project/端口/卷，结束后清理） | 是 | **已执行（重跑）**：exit 0；五服务健康、`health_semantics_ok=true`、`alert_delivery_ok=true`、凭据拒绝 exit 2、`existing_resources_untouched=true`、cleanup 0 | `compose-smoke.json` / `C6.*` |
| C7 | `python scripts/drill_crash_durability.py` | 是 | **已执行**：exit 0 | `C7.crash-durability.json` |
| C8 | `python scripts/drill_lease_fencing.py` | 是 | **已执行**：exit 0 | `C8.lease-fencing.json` |
| C9 | `python scripts/rehearse_trading_runtime.py` ×2（5.10 可重复性） | 是 | **已执行**：两次 exit 0 且输出字节一致（9 场景） | `C9.summary.json` |
| C10 | `python scripts/backup_postgres.py` + `python scripts/restore_postgres_drill.py`（仅本地模拟器，用于确认 gate 拒绝） | 是 | **已执行**：exit 0；gate 仍拒绝本地 target | `C10.backup-restore/backup-restore-emulator.json` |
| C11 | `python -m isort --check-only --profile black trader/tests/test_phase5_independent_offline.py trader/tests/test_phase5_independent_postgres.py` 与 `python -m black --check --line-length 100 <同两文件>` | 是 | **已执行（重跑）**：isort exit 0、black exit 0（第 1 轮失败的 4 处折行已修正）；注意 C2 fixture 修正后再改动了 postgres 文件，需随 C2 复跑一并重验 | `C11.isort.diff` / `C11.black.diff` / `C13.tester-tests.sha256` |
| C12 | 40 个快照文件 sha256 重算 + `sha256(canonical sorted [path,sha256])` 与 `3738b9b6…` 比对 | 是 | **已执行**：39/40 一致；唯一差异 `.github/workflows/ci-gate.yml`（授权追加两行，预期作者变更） | `C12.summary.json` |
| C13 | `sha256sum trader/tests/test_phase5_independent_offline.py trader/tests/test_phase5_independent_postgres.py docs/PHASE5_INDEPENDENT_TEST_REPORT.md .github/workflows/ci-gate.yml` | 是 | **部分执行**：两测试文件哈希已出（但 postgres 文件在 C2 fixture 修正后已变化，需重算）；报告与 CI 哈希待补 | `artifacts/foundation/phase5/tester-ff7eef49/tester-files.sha256` |

## 8. blocking_findings

无阻断缺陷。执行侧阻断已解除：C1–C13 final 全部由工程师受控代执行并回传原始 argv / 直接退出码 / stdout/stderr / JUnit 计数，本报告 §0.1–§0.2 逐条分析。

唯一保留项（外部条件缺失，非缺陷）：缺经核实的异机 S3 兼容目标与独立恢复库，5.6 / 5.7 维持 blocked，不得计 pass；拿到目标后应补跑异机备份 + 恢复 + DR gate 全链路方可关闭。

## 9. limitations

1. 5.6 / 5.7 的「异地上对象存储 + 独立恢复库」在 round1 内无法满足：现有证据均为 `target_kind: local_emulator`、`off_host: false`，且 DR gate 正确拒绝之。因此 5.6/5.7 维持 **blocked**，不得以模拟器结论冒充通过。
2. （已解除）round1 前期测试文件未经解释器运行；最终批 C1/C2/C11 及全量套件已在真实环境执行并通过。
3. `test_phase5_independent_postgres.py` 需要 PG 账号具备 `CREATEDB`（一次性任务数据库）；本轮环境满足，未来环境不具备时将以错误而非 skip 呈现。
4. 5.10 通过连续两次演练脚本执行证明（C9 final 两次 exit 0 且字节一致），测试侧未额外封装。
5. C2 第 1 轮失败是本测试员的 fixture 缺陷：共享 store 下复用固定 `checked_at_ms` 会被 `STALE_SNAPSHOT` 拒绝。修正后的 `_VENUE_CLOCK` 为模块级共享、每次 reconcile 前进 1ms，跨用例累积推进仅数毫秒；命令/决策时间戳保持固定，仓库风控路径对 `decision_at_ms` 无新鲜度硬门禁（已核对 `trader/trading/service.py`、`trader/trading/risk.py`），故不影响断言语义。
6. C6 期间工程师为修复相对 evidence 目录而修改了 `scripts/compose_smoke.py`（路径 resolve）。该文件在生产写域内、由工程师负责，已纳入其冻结 digest 管理；本测试员未触碰。
7. 环境与历史证据边界：本轮使用的 Docker/PG 资源为任务自有唯一 project/schema/database，结束后已清理；既有 `qts-postgres` 容器与 `quant_trading_system_qts_pgdata` 卷未被重启/删除/覆盖。§0.1 中旧冻结源（`3738b9b6…`）上的 C1/C11/C12 结果属**历史证据**，仅在追溯修正过程时有意义；本轮最终结论一律以 §0.2 在 `5c14dd9d…` 上的复验为准。

## 10. unexecuted_checks

round1 范围内 C1–C13 已全部执行（final 批，见 §0.2），无遗留未执行检查。

范围外未执行（需外部条件，不属 round1 可执行集）：异机 S3 兼容目标上的真实备份/恢复/DR gate 全链路（关闭 5.6/5.7 的前置条件）。

## 11. changed_files

| 文件 | 变更 |
|---|---|
| `trader/tests/test_phase5_independent_offline.py` | 新增（40 个测试函数 / 收集 58 项） |
| `trader/tests/test_phase5_independent_postgres.py` | 新增（11 用例） |
| `trader/tests/test_phase5_independent_offhost.py` | round2 新增（23 个测试函数 / 收集 26 项；C14 26/26 pass） |
| `docs/PHASE5_INDEPENDENT_TEST_REPORT.md` | 本报告 |
| `.github/workflows/ci-gate.yml` | 仅追加三行既有 job 的测试命令 |

未触碰：`compose.yml`、生产代码、`docs/INTERFACE_CONTRACTS.md`、工程师测试与全局 fixture、既有证据文件、`qts-postgres` 容器与 `quant_trading_system_qts_pgdata` 卷、默认 compose project。

## 12. workspace_frozen

`workspace_frozen: true`。round1 独立测试与本报告最终结论已冻结：不 commit、不 push、不部署、不推进阶段。

**round1 最终结论（analyzed_by=tester）**：
- 5.1、5.2、5.3、5.4、5.5、5.8、5.9、5.10、5.11：**pass**（最终冻结源 `5c14dd9d…` 上，executed_by=engineer、analyzed_by=tester，证据见 §0.2）。
- 5.6、5.7：**blocked**（缺经核实异机目标；相关语义层与回执注入用例已通过，但 RPO 异地收据与真实 RTO 无法验证）。
- G1–G6：全部满足（§5；G4 以最终 digest `5c14dd9d…` 为准）。
- 故障注入 T1–T7 全部成立（§4）。
- 审核提示：本报告写入最终结论后自身内容已变，C13 的报告哈希 `417a5bd1…` 以本冻结版本重算为准；两份测试文件哈希 `7f7638b2…`（offline）/ `498387f0…`（postgres）为最终有效值。

kind=execution_request（发给工程师 `5a4dfb1a-57a7-402d-9bd1-0e9e6ffe568f`，仅工程师，不抄送父）：
- 命令：§7 C1–C13（环境变量 `POSTGRES_CONNECTION_STRING` 由现有环境提供，不得打印）。
- 预期产物：两个 JUnit XML + 各命令 stdout/stderr 尾部 + 直接退出码 + C12/C13 摘要，落 `artifacts/foundation/phase5/tester-ff7eef49/`。

## 13. 异机（SSH 隧道 S3）复验 —— round1 之后

本节为 round1 冻结结论**之后**的续作，绑定工程师新冻结源 `826c51ad…`（43 文件）。round1 的 5.6/5.7 blocked 记录**原样保留**（§3/§9/§12），不重写、不追溯改判。

### 13.1 授权与范围（依据，非我自设）

`artifacts/foundation/phase5/parent-offhost-work-order.md`：用户指定 `alex@192.168.3.4`（hostname `my-server`）为备份机并授权在其上建立任务自有目录/Docker S3 服务/bucket/凭据；禁止削弱 host-key 校验、禁止把私钥写入 artifacts、禁止触碰远端既有应用容器与既有本地 `qts-postgres`/`qts_pgdata`。

### 13.2 我对证据的独立复算（不采信 `passed: true`）

对象：`artifacts/foundation/phase5/evidence/offhost/20261104T013702Z-78f5cf6b/offhost-backup-restore.json`。以下数值由我用原始字段重算，非读取布尔值：

| 断言 | 我的复算 |
|---|---|
| 成功备份数 | 3，全部 `status=succeeded`、`reason=null` |
| RPO 间隔 | 完成时刻 1793756235697 / 1793756356548 / 1793756477377 → 间隔 120.851s、120.829s → 取整 [120,120]，max 120 ≤ 300 |
| 回执一致性 | 每次 `head_metadata_sha256 == readback_sha256 == sha256`，且 `head_content_length == size_bytes`（18117 / 18975 / 19686） |
| 远端完整回读 | 3 个对象逐个 GET 全量哈希与本地 sha256 相同；`metadata.sha256` 亦相同（大小写已归一为小写） |
| 远端本地磁盘校验（round2 补充） | `remote-local-sha256.txt` 三行哈希与我上表三个 sha256 逐一相等，路径为 `/home/alex/qts-backups/data/<bucket>/phase5/<run>/…`；`remote-local-verification.json` 三个 `match=true`、`all_match=true` |
| 目标身份 | 每次 `transport=ssh_tunnel`、`remote_host=192.168.3.4`、`endpoint_host=192.168.3.4`、`tunnel_endpoint_host=127.0.0.1`、`ssh_host_key_verified=true`、`off_host=true`、`identity_verified=true`、`kind=s3_compatible`、`attested_by` 非空 |
| 恢复事实 | `restore.verified=true`、`checks.facts_match=true`、`checks.invariants_ok=true` |
| RTO | started 1793756477630 → completed 1793756478091 = 461ms → `rto_seconds=0` ≤ 3600 |
| 恢复库隔离 | 目标库 `qts_p5_rst_…` ≠ 对象元数据 `source-database=qts_p5_src_…` |
| 六表覆盖 | `trading_accounts/commands/executions/ledger/events/outbox` 全部有 counts 与 digests |
| gate 前后 | before `ready=false, reasons=[DR_EVIDENCE_MISSING]`；after `ready=true, reasons=[]`，`details.target_transport=ssh_tunnel`、`target_remote_host=192.168.3.4` |

结论：**证据自身内部一致，且关键数值可复算一致**；没有任何字段把 loopback 端点冒充为异地目标。

### 13.3 新增独立用例（`trader/tests/test_phase5_independent_offhost.py`，23 个测试函数 / 收集 26 项）

设计先于实现：从「loopback 隧道端点不得成为异地目标」这一契约不变量出发，再读 `backup.py`/`dr_evidence.py`/`s3.py`/`drill_offhost_backup.py` 核对接口。

- **正例**：完整 ssh_tunnel 身份 → gate `ready=True` 且 `details` 暴露 transport/remote_host；`endpoint_host` 等于远端主机而 `tunnel_endpoint_host` 为 127.0.0.1。
- **反例**：缺 `remote_host`、`remote_host` 为任意 loopback 拼写（`127.0.0.1`/`127.0.1.9`/`localhost`/`LOCALHOST`）、`ssh_host_key_verified=false`、`kind=local_emulator` 冒充隧道、direct+loopback 端点、`checks.facts_match=false` —— 全部必须非 READY 且给出对应理由码。
- **传输层**：S3 客户端把 `Sha256`/`Run-Id` 归一为小写；上传传入的是打开的文件句柄（流式，而非已读入内存的 bytes）。
- **演练脚本**：ssh argv 恒定含 `BatchMode=yes`/`StrictHostKeyChecking=yes`/`ConnectTimeout=15`，远端执行 `sh -s`；脚本负载为纯 LF（无 CRLF）；probe 的 `key=value` 解析；远端回读逐对象重算 sha256 且篡改对象被标 False。
- **证据完整性**：从最新 run 的原始数值重算 RPO 间隔、逐对象回执/回读、恢复事实与 RTO、恢复库与源库不同、gate 前后状态。

### 13.4 观察（非失败断言，供审核员判断）

1. `drill_offhost_backup.py` 在构造配置时**直接断言** `off_host_attested=True`、`ssh_host_key_verified=True`，证据顶层亦写 `off_host: True`。这不是网络测量得出的结论，而是「用户指定 + 脚本自身以 `StrictHostKeyChecking=yes` 完成 SSH 连接 + 远端 hostname 断言 + 隧道远端端口指向该主机」这一链条的**人为/流程性 attestation**。我认为在当前授权下可接受，但审核员应明确：代码本身无法证明异地性，证明力来自上述外部事实与证据字段的一致性。
2. ~~远端回读通过**隧道内的 S3 API** 完成，而非在远端对数据目录做 `sha256sum`。~~ **round2 已闭合**：工程师补充了远端本地 `sha256sum`（`remote-local-sha256.txt` / `remote-local-verification.json`），我复算三行哈希与记录值逐一相等，故「远端数据目录字节 == 远端 S3 GET == 本地记录」三段一致，原保留意见撤销。
3. 证据中 `storage.health_status=400`（对 `GET /` 的探测）属信息性字段，真正的可用性由 bucket create/put/head/get 证明，不构成缺陷。

### 13.5 执行结果（`authored_by=tester / executed_by=engineer / analyzed_by=tester`）

命令通道仍不可用（§0），C14–C16 由工程师 `5a4dfb1a` 受控代跑；我逐项核对原始产物后采信。

| id | argv | exit | 结果 | 证据 |
|---|---|---|---|---|
| C14（首跑） | `pytest -q …_offhost.py --junitxml=…/offhost.junit.xml` | 0 | `tests=26 failures=0 errors=0 skipped=0`，4.966s | `offhost.junit.xml`、`C14.*` |
| C14 final | `pytest -q …_offhost.py --junitxml=…/offhost.final.junit.xml` | 0 | `tests=26 failures=0 errors=0 skipped=0`，6.572s（格式修正后的最终字节） | `offhost.final.junit.xml`、`C14.final.*` |
| C15 final | isort `--check-only --profile black` / black `--check --line-length 100` | 0 / 0 | 当前文件已无格式差异（**无独立 final 产物文件**，仅有工程师消息回报；`C15.isort.*`/`C15.black.*` 是修正前首跑，`C15.round2.black.diff` 是失败证据） | 工程师消息；`C15.round2.black.diff` |
| C16 final | `sha256sum` 三文件 | 0 | 见下 | `C16.final.hashes.sha256` |

哈希核对（我做的交叉验证；哈希值本身为 executed_by=engineer，我无法本地复算）：
- 测试文件 `f076ff6d2c0bca8be55ef130810a6aa05e01f1d6b87520802e8002155fd5eb9c`，与 `final-source-snapshot.json` 中该条目**逐字一致** → 最终 C14 执行的正是当前磁盘字节。
- `ci-gate.yml` `747c6d88…` 未变；`offline 7f7638b2…`、`postgres 498387f0…` 未变（我未向既有两文件追加用例）。
- 报告写入前版本 `dbe9dd4b…` 仅供追溯；本文件在 §13.5–§13.7 写入后再次变动，最终值由工程师冻结后重算。

**C16 之后那处格式修正（如实留痕）**：C16 首跑后，我在收尾编辑中把 `targets = [...]` 拆成三行，black 要求合并为单行（99 列），导致首跑记录 `0ea02e2f…` 与磁盘不一致、`black --check` 失败。工程师按 v3 §2.1 不代改我的文件，我本人依 `C15.round2.black.diff` 用文件工具改回单行，**仅折行，未改任何断言或意图**。修正后 C14 final 26/26、C15 exit 0、新哈希 `f076ff6d…`，故该问题已闭合，不构成 finding。

**冻结集（最终）**：工程师按 round1 同口径重算（实现 + 双方测试 + CI，报告按 v3 §5 排除）得 44 文件 `7392e8b0d8425bbb4d3005aed6fbe180614860b3b72534fe77f2d854f8fa7ab1`。我用文件工具复核：清单 44 条 `sha256`、含我方新测试文件（`f076ff6d…`）与 CI 追加（`747c6d88…`）、`docs/PHASE5_INDEPENDENT_TEST_REPORT.md` 在 `excluded` 中。历史指纹 `f3cd5e2f…`（44 文件，被格式修正取代）、`826c51ad…`（43 文件）、`5c14dd9d…`（42 文件）全部保留。

### 13.6 round2 结论（analyzed_by=tester）

- **5.6：pass**。真实异机目标成立：`transport=ssh_tunnel`、`remote_host=endpoint_host=192.168.3.4`、`tunnel_endpoint_host=127.0.0.1`、`ssh_host_key_verified=true`、`off_host=true`、`identity_verified=true`、`kind=s3_compatible`；3 次真实备份间隔 [120,120]s（max 120 ≤ 300）；逐对象 HEAD/GET 回读哈希与本地 sha256 相等；远端本地 `sha256sum` 与记录值相等；loopback/缺 remote_host/未严格校验 host key/仿冒 emulator 四类反例全部被 gate 拒绝（C14）。
- **5.7：pass**。独立恢复库 `qts_p5_rst_…` ≠ 源库 `qts_p5_src_…`，六表 counts/digests 比对 `facts_match=true`、`invariants_ok=true`、`verified=true`；实测 RTO 461ms → 记录 0s ≤ 3600；gate before `ready=false reasons=[DR_EVIDENCE_MISSING]` → after `ready=true reasons=[]`；证据数值由我独立复算一致。
- **5.1–5.11：pass（最终全量，含我方 26 项）**。`evidence/full-suite-offhost-final.xml`：`tests=3847 failures=0 errors=0 skipped=0`（252.600s，09:57:20）；我用文件工具确认其中**包含** `test_phase5_independent_offhost.py`、`test_phase5_independent_offline.py`、`test_phase5_independent_postgres.py` 三个独立文件的用例，且 3821 + 26 = 3847 与新增用例数精确吻合。
- **历史全量（09:41，3821 项）**：为不含 offhost 新文件的那次运行，仅作历史；最终结论以 3847 为准。
- **round2 overall：pass**。5.1–5.11 全部 pass（5.6/5.7 由本节闭合，其余为新源上的全量重跑）。
- **T1–T7 / G1–G6**：round1 结论不变（§4/§5）；G4 的 digest 以 `7392e8b0…`（44 文件）为准。
- **finding**：无阻断项。保留 limitation 仅 §13.4 的 1 与 3（attestation 的证明力来源、`GET /` 400 为信息性）。

### 13.7 workspace_frozen（round2）

`workspace_frozen: true`。不 commit、不 push、不部署、不推进阶段。

- 最终可交付指纹：`source_digest=7392e8b0d8425bbb4d3005aed6fbe180614860b3b72534fe77f2d854f8fa7ab1`（44 文件，`final-source-snapshot.json`）。
- 有效文件哈希（round2 时点）：`test_phase5_independent_offhost.py=f076ff6d2c0bca8be55ef130810a6aa05e01f1d6b87520802e8002155fd5eb9c`、`test_phase5_independent_offline.py=7f7638b2…`、`test_phase5_independent_postgres.py=498387f0…`（round3 追加 1 例后已变，见 §14）、`ci-gate.yml=747c6d88…`。
- 历史值（已作废，仅追溯）：`0ea02e2f…`（C16 首跑时的 offhost）、`79b4b5b1…`（中间态）、`f3cd5e2f…`（首版 44 文件 digest）、`7510efc9…` / `dbe9dd4b…`（报告写入前版本）。
- **报告自身哈希**：本文件在 §13.5–§13.7 写入后内容再次变动，**最终 sha256 由工程师冻结后重算**并交第 2 轮审核员（round1 的 `90e0c771…`/`417a5bd1…` 同理仅作历史）。

## 14. round3：两处审核阻断的独立反例（overall=pass）

### 14.1 授权与范围

第 2 轮只读审核员 `7ff2a2a0` 给出两个阻断：BF-1（`restore.checks` 未校验，5.9/5.10）与 BF-2（过期 owner 的后台对账仍可能触碰账户，5.11）。工程师已在写域完成限定修复，给出新 44 文件源 `10c41a5f…`，并要求我补独立反例后复验。

我只动 tester 专属文件：

| 文件 | 动作 | round3 后条目 |
|---|---|---|
| `trader/tests/test_phase5_independent_hardening.py` | 新增（BF-1） | 8 函数 / 21 项 |
| `trader/tests/test_phase5_independent_postgres.py` | 追加 1 例（BF-2） | 11 → 12 项 |
| `trader/tests/test_phase5_independent_offhost.py` | **未动**（字节仍为 round2 的 `f076ff6d…`） | 26 项 |
| `trader/tests/test_phase5_independent_offline.py` | **未动**（`7f7638b2…`） | 58 项 |

保留 round2 文件字节不变，是为了让 §13 的证据链在新一轮仍可独立复核；新增内容单独成文件，审核员可只读 delta。

### 14.2 BF-1（5.9/5.10）：`restore.checks` 反例矩阵

期望来自契约 13.16（`facts_match` 与 `invariants_ok` 必须显式为 `true`；缺失或非对象即非 READY），不来自工程师用例。每个反例都断言 `ready=False` **且** reasons 含 `DR_RESTORE_FACTS_UNVERIFIED`：

| 变体 | 期望 |
|---|---|
| `checks` 键缺失 | 拒绝 |
| `checks` 为 `None` / `[]` / `"true"` / `1` / `True` / `False` / `{}` | 拒绝（7 组参数化） |
| 只有 `facts_match` 或只有 `invariants_ok` | 拒绝 |
| 某键为 `None` | 拒绝 |
| `facts_match=False` / `invariants_ok=False` / 双假 | 拒绝（3 组） |
| 值为 `"true"` 或 `1`（truthy 但非 `True`） | 拒绝（3 组，验证 `is True` 而非真值判断） |
| 双真但 `verified=false` | 拒绝（`DR_RESTORE_UNVERIFIED`），证明 checks 不能替代 verified |
| 双真（正例） | `ready=True`、`reasons==()`、`target_transport=ssh_tunnel` |
| 真实异机 `dr-evidence.json` | 先断言其 `checks` 双真，再用 `completed_at_ms+1s` 评估 → 仍 `ready` |

最后一行把 5.6/5.7 的判定与新严格度绑定：gate 变严后，落盘的异机证据仍能通过，说明该证据本身带 `checks` 双真，而不是靠放宽门槛过关。

### 14.3 BF-2（5.11）：过期 owner 不得再对账（真实 PG）

与工程师用例的差异：工程师等租约自然过期；我**在旧 owner 自己的续约调用内部把行置为过期**，使"观察到丢失的那一次 pass"就是"造成过期的那一次"，不依赖定时器竞态。观测方式是三个独立计数器：`snapshot_provider` 调用数、包装后的 `service.recover` 调用数、续约成功数。

断言链：

1. 稳态（触发前）：`snapshots >= 2` 且 `recover` 非空 → 证明确实在周期对账；
2. 触发后：`ready=False`、`started=False`、`lease_token is None`、reasons 含 `EXECUTION_INSTANCE_CONFLICT`；
3. 关闭后再等 0.3s：snapshot 与 recover 计数**零增长**（零快照/零 recover）；
4. 不变式 `snapshots <= renew_ok`（每一次对账都必须先有一次成功续约）与 `recover <= snapshots`；
5. 旧 owner `place()` 抛 `NotReady(EXECUTION_INSTANCE_CONFLICT)`，交换适配器零发送；
6. 新 owner 接管：`lease_token` 严格增大，`_allocate_to_book` 后可下单（1 次发送）。

存储为任务私有 schema 的真实 PostgreSQL，续约、过期、接管都是真实 SQL（`renew_lease` 的 `expires_at >= clock_timestamp()` 是拒绝依据，`acquire_lease` 的 `expires_at < clock_timestamp()` 是接管依据），未打桩这两条语句。

**首次代跑暴露的 fixture 缺陷（已修，属我的问题）**：初版把表名写成 `execution_leases`，实际表是 `trading_runtime_leases`；SQL 异常被 `_validate_lease_for_work()` 归为 `STORAGE_UNAVAILABLE`，于是 C19 停在 `assert "EXECUTION_INSTANCE_CONFLICT" in runtime_a.last_reasons`。这不是实现缺陷，而是我的 fixture 写错。已改为正确表名，并加 `RETURNING 1` + `assert gate["expired_rows"] == 1`，使表名若再变会明确报"fixture 未过期任何行"，而不是伪装成存储异常；同时在触发前显式断言 `runtime_a.ready is True`，把"触发前是否健康"与"触发后是否关闭"两段失败分开。

### 14.4 执行结果（`executed_by=engineer` / `analyzed_by=tester`）

| id | 结果 | 证据 |
|---|---|---|
| C18 | `21/21`，exit 0，7.244s | `round3/C18.hardening.junit.xml`、`C18.*` |
| C19 | `12/12`，exit 0；新用例 1.093s | `round3/C19.postgres.junit.xml`、`C19.stdout.txt`、`C19.exitcode.txt` |
| C20 | isort exit 0 / black exit 0（两文件） | `round3/C20.isort.*`、`C20.black.*` |
| C21 | hardening `1317caf4…`、postgres `c079e9db…`；45 文件 digest `af7f8f98…` | `round3/C21.hashes.sha256`、`final-source-snapshot.json` |
| C22 | `3871 / 0 / 0 / 0`，271.855s，exit 0 | `evidence/full-suite-round3.xml` |
| C18 final2 | `21/21`，exit 0，4.102s（BF-R3 修复后复跑） | `round3/C18.final2.junit.xml` |
| C19 final2 | `12/12`，exit 0；stale-owner 1.281s | `round3/C19.final2.junit.xml` |
| C22 round4 | `3871 / 0 / 0 / 0`，248.814s，exit 0 | `evidence/full-suite-round4.xml` |

我逐项核对（不是转述）：C18 JUnit `tests=21 failures=0`；C19 `tests=12` 且含 stale-owner 用例（1.093s）；C21 两行哈希与清单条目逐字一致；清单 45 条 `sha256`、digest `af7f8f98…`、报告在 `excluded`；C22 计数 3871，并用正对照 grep 确认全量里确实含 hardening 末项与 stale-owner 用例（3849+21+1 精确吻合）。BF-R3 修复后以 `full-suite-round4.xml` 与 `C18/C19.final2` 为最终，round3 的这批产物降为修复前基线。

首次 C19 失败是我的 fixture 表名缺陷（§14.3），修正后 12/12；该失败不计为实现问题，也不构成 finding。

**BF-R3-1 / BF-R3-2（第 3 轮审核提出）后的复核**：工程师重建 evidence-index（不再含旧 run、`10c41a5f…` 或 `pass_pending_retest` 遗留——我 grep 了 `artifacts/foundation/phase5` 全部 JSON 确认无匹配；`engineer-evidence-index.json` 现指向 `2d963b2c…` 与 fresh run），CI 把 `test_phase5_independent_hardening.py` 注册进 trading-core-gate（`ci-gate.yml` 第 248 行，哈希 `f1c93d5f…`），并把 reconcile 的 `_validate_lease_for_work()` 移入 `_alignment_lock` 消除锁等待窗口的 TOCTOU——我核实了该调用确在锁内（`runtime.py` reconcile 分支）。我的两个测试文件字节未变（与 C21 记录一致），final2 复跑全绿，即反例在新实现上仍然成立。

### 14.5 5.6 / 5.7 结论

**维持 pass，证据升级为 fresh run `20261104T032834Z-54368e28`**。我逐项核对该 run 的 `offhost-backup-restore.json`：3 个对象 `readback_sha256` 与记录 sha256 相等、`all_digests_match=true`、`gaps_seconds=[120,120]`（max 120 ≤ 300）、`rto_seconds=0`、gate before `ready=false` → after `ready=true`、三个 target 均 `off_host=true` / `identity_verified=true` / `remote_host=192.168.3.4`。§14.2 的回归用例按 mtime 取最新 `dr-evidence.json`，C18 final2 21/21 即新证据在加严 gate 下仍 ready 的直接证明。

### 14.6 round3 结论与冻结

- **BF-R3-1：闭合**。evidence-index 重建后只含最终 45 文件 digest `2d963b2c…`、fresh run `20261104T032834Z-54368e28`、G1=3871；我 grep 确认全树 JSON 无 `10c41a5f…` / `pass_pending_retest` 残留。
- **BF-R3-2：闭合**。CI 已注册 hardening 文件（`ci-gate.yml=f1c93d5f…`）；`_validate_lease_for_work()` 已移入 `_alignment_lock`（我核实 `runtime.py`），TOCTOU 窗口消除，我的 stale-owner 反例在 final2 上仍然通过。
- **BF-1：闭合**。`restore.checks` 缺失 / 非对象 / 半对象 / `False` / truthy 非布尔 → 全部非 READY 且给出 `DR_RESTORE_FACTS_UNVERIFIED`；双真正例与 fresh 异机证据仍 ready。
- **BF-2：闭合**。真实 PG 上，旧 owner 在续约被拒的同一次 pass 即关闭（`ready/started=False`、token 清空、`EXECUTION_INSTANCE_CONFLICT`），此后 0.3s 观察窗内 snapshot/recover 零增长，且 `snapshots <= renew_ok`；旧 owner 零发送，新 owner token 增大并成功下单。
- **round3 overall：pass（final frozen）**。C18 final2 21/21、C19 final2 12/12、C20 0/0、C22 round4 3871/0/0/0；5.1–5.11 与 T1–T7/G1–G6 结论不变，5.6/5.7 仍 pass（§14.5）。
- **finding**：无阻断项、无新增 limitation。唯一保留 limitation 仍是旧的：off-host 的证明力来自进程内 attestation（§13.4 第 1 条），本轮不改变其性质。

`workspace_frozen: true`。不 commit、不 push、不部署、不推进阶段。

- 最终可交付指纹：`source_digest=2d963b2ca5f338e68ae0e167ce555110e7ec7c67e565640e3c0b8d537c92b447`（45 文件，`final-source-snapshot.json`）。
- 有效文件哈希：`hardening=1317caf4c76e4aade12a478cf8b72cf0c03699774287cb63d419bb06f521de07`、`postgres=c079e9db15d80257bab48f4105a712084570378b0233b95de0210684a5bc1e16`、`offhost=f076ff6d…`、`offline=7f7638b2…`、`ci-gate=f1c93d5f42c36baeec71b8ec0c6d08b9783386cd1d28e12146cb6d6d00120cbe`。
- 历史值（仅追溯）：`af7f8f98…`（BF-R3 修复前 45 文件）、round2 的 `7392e8b0…`（44 文件）、`f3cd5e2f…`、`dbe9dd4b…`；round1 的 `5c14dd9d…`。
- **报告自身哈希**：§14（BF-R3 更新）写入后本文件已变，最终 sha256 由工程师冻结后重算并交第 4 轮审核员。
