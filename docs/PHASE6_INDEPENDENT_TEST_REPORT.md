# Phase 6 独立测试报告

> **round17 更新（2026-11-10）**：本文件顶部 JSON 与 "声明范围/测试项/changed_files/
> blocking_findings/limitations/unexecuted_checks/归属与冻结" 各节为 round16 时点的历史
> 记录，保留不改写。最新一轮独立复验见文末 **「round17 复验：分段累计、resume/segment、
> observation-only 与 guard」** 段（`report_id=P6-TESTER-ROUND17-CONTINUATION`，
> `overall=blocked`，`source_digest` 使用本轮独立复算值）。round17 的 blocking findings
> 不改变 round16 时点的历史结论，但阻断最终交付，详见文末。
>
> **round18 更新（2026-11-10）**：round17 的两个 blocking findings 已由工程师修复并经
> 本轮独立复验关闭；最新结论见文末 **「round18 复验：manifest/lineage 修复与 final16」**
> 段（`report_id=P6-TESTER-ROUND18-CONTINUATION`，
> `overall=pass_for_6.3-6.12; pending_6.1-6.2`）。6.1/6.2 仍 pending，未声明通过。
>
> **round19 更新（2026-11-10）**：文档口径与最终指纹的针对性复验通过；最新结论见文末
> **「round19 复验：文档口径与最终指纹」** 段（`report_id=P6-TESTER-ROUND19-CONTINUATION`，
> `overall=pass_for_6.3-6.12; pending_6.1-6.2`）。6.1/6.2 仍 pending，未声明通过。
>
> **round20 最终审计（2026-11-11）**：segment 5 的 24h 窗口 passed=true（20/20 criteria），
> 最终 aggregate acceptance_satisfied=true；6.1/6.2 通过。最新结论见文末
> **「round20 最终审计：6.1/6.2 通过」** 段（`report_id=P6-TESTER-ROUND20-FINAL`，
> `overall=pass_for_6.1-6.12`）。fresh reviewer 与 final handoff 仍 pending。
>
> **round21 修复复验（2026-11-11）**：reviewer round14 七项阻断中六项已验证修复；
> `P6-R14-AGGREGATE-IDENTITY-INPUTS` 的修复引入新的 canonical identity 回归，标准
> aggregate 命令无法复现 94,410,660ms/acceptance true。最新结论见文末
> **「round21 复验：reviewer round14 七项阻断修复」** 段（`report_id=P6-TESTER-ROUND21-FIXES`，
> `overall=blocked`）。修复前不得进入 fresh reviewer/final handoff。
>
> **round22 identity 修复复验（2026-11-11）**：canonical identity 修复通过；标准命令
> exit 0、94,410,660ms、acceptance=true；五类反例仍被拒；final20/独立全量均
> 3504/0/0/0。最新结论见文末 **「round22 复验：aggregate identity 修复与 canonical
> 复现」** 段（`report_id=P6-TESTER-ROUND22-IDENTITY-FIX`，
> `overall=pass_for_6.1-6.12`）。fresh reviewer/final handoff 仍 pending。
>
> **round23 最终复验（2026-11-11）**：aggregate 输出已字节可复现（两次运行与
> canonical 均为 sha `f146b9c3…`），runbook 已指向 final20；final21/独立全量均
> 3504/0/0/0。最新结论见文末 **「round23 最终复验：aggregate 输出确定性与 runbook
> 指针」** 段（`report_id=P6-TESTER-ROUND23-OUTPUT-DETERMINISM`，
> `overall=pass_for_6.1-6.12`）。fresh reviewer/final handoff 仍 pending。

```json
{
  "report_id": "P6-TESTER-ROUND16-GUARD",
  "role": "tester",
  "agent_id": "tester-session (independent tester round 16, child of engineer 231a51ed-bcba-4dd2-afe6-271e45f72164)",
  "round": 16,
  "base_commit": "c6fe450260eeb59d46c9caee591fb1625510b443",
  "branch": "feat/foundation-refactor",
  "source_digest": {
    "implementation_digest": "74c95aa77039a6b7cc2a82cd7622be7ae63997c3620bdd33c5696a533f94e879",
    "implementation_digest_files": 160,
    "implementation_manifest": "artifacts/foundation/phase6/engineer-source-manifest-impl.json",
    "complete_phase_digest": "0f932c430e65d8d83c1c1e0154778d2babda2d7ebdaac80c5e3ea652bebd25a0",
    "complete_phase_digest_files": 162,
    "complete_manifest": "artifacts/foundation/phase6/engineer-source-manifest-final.json",
    "round14_guard_verification": "fail_blocking:P6-R14-GUARD-CONTENDER-MISCLASSIFIED",
    "round14_guard_verification_status": "fixed_verified_round15",
    "round15_guard_verification": "pass",
    "round15_a2_preflight_lookback": "fixed_verified",
    "round16_reviewer_b1_cleanup_stdout": "fixed_verified",
    "round16_reviewer_b2_stress_raw_evidence": "fixed_verified",
    "round16_note": "round16 独立复算：impl 160 文件 / 74c95aa7…，final 162 文件 / 0f932c43…，digest_match=true，mismatch=[]，null_but_exists=[]；supervisor ea0ab9fd…、guard 50e680f2…、guard test a4d5c555… 与工程师声明一致；tester offline eb643f0a…/66 与 postgres 0f7e030f…/4 未改动。final12 XML tests=3474/errors=0/failures=0/skipped=0、exit=0，含 guard 18 例与 tester offline 66 例。B1 以真实 _stop_stack_best_effort + 真实 run() finally 复现：正常/中止路径均到达 docker stop，空列表不 stop，失败记录且不掩盖退出码；B2 逐对核对 40/40 [0,3]、0 失败、输入前后 sha256 与当前磁盘一致，并由测试者独立 40 对真实入口竞争复核。详见 body round16 段。",
    "round15_note": "round15 历史：impl 160 文件 / accf7614…，final 162 文件 / 50692d8b…，final11 3471/0/0/0；B1 竞争者误分类修复后 36 对 [0,3]；A2 修复。",
    "round14_note": "round14 历史：impl 160 文件 / f6498565…，final 162 文件 / 8b2a374d…，final10 3466/0/0/0、exit 0；round14 发现 B1 并发活性缺陷（详见 body round14 段），该 finding 已在 round15 修复并复验。",
    "round10_fix_chain": "P6-R5-METRICS-EVIDENCE-GAP：_summarize 增加 metrics_samples_present / metrics_gap_within_bound（含窗口末端尾部间隔）/ coverage_fresh_at_end，缺 metrics 的采样不再清空 unresolved，unresolved_cleared_at_end 要求末端有证据，输出 metrics_samples / metrics_missing / metrics_gap_max_ms；新增 trader/tests/test_phase6_window_gates.py 红/绿回归。P6-R5-OBSERVER-SESSION-RELOGIN：metrics/risk 的 401/403 响应清 token 并在同一样本内重新登录重试（attempts=[False, True]），新增 trader/tests/test_phase6_observer_relogin.py。P6-R6-COVERAGE-FRESH-MARGIN：coverage_fresh_at_end 原先固定 300s，低于 reconcile 300s + 采样 slack 的真实相位，健康窗口可能误杀；修复为要求 metrics.coverage.recorded_at_ms 存在，上界 max(300_000, 2*reconcile_seconds*1000 + sample_gap_limit_ms)=780_000ms，并输出 coverage_fresh_limit_ms。tester round10 独立补 24h 稠密窗口红/绿、metrics/risk 两分支同样本重登、以及 0/60/120/180/240/300s 相位与 780_000/780_001ms 边界/missing 的 margin 用例共 5 个；离线套件由 61 增至 66，run11 66/0/0/0、black 2 files unchanged。",
    "round6_fix_chain": "P6-R6-COVERAGE-FRESH-MARGIN 生产修复后，所有期望通过的 tester 窗口 fixture 都携带 metrics.coverage.recorded_at_ms（默认样本 ts，健康覆盖年龄 0–30s；显式 None 用于 fail-closed 反例）。tester margin 独立手算：2*300_000 + 180_000 = 780_000ms；相位 0/60/120/180/240/300s 全部通过，精确 780_000ms 通过、780_001ms 失败，recorded_at_ms 缺失 → coverage_fresh_at_end=false 且 passed=false。P6-R6-ROUND10 执行证据：run11 离线 66 passed/0 failed/0 skipped、exit 0、offline SHA-256 eb643f0a…；final12 全量 3454 passed/0 failed/0 skipped/0 error、exit 0（final11 曾有 1 个 setup-only 环境抖动 error，单测重跑通过，已由 final12 取代；final12 现为历史；final8（3454/0/0/0、exit 0）亦被 monotonic/对账 reason 类修复后的 final9（3456/0/0/0、exit 0）取代，final8 及更早仅作历史）。",
    "digest_revision_note": "round13 元数据更新（2026-11-08，tester）：24h 窗口 20261107T083226Z-50077d3c 官方 window.json passed=false，仅三项——wall marker 短 2s（NTP slew，observer monotonic 实际 86400s；旧 supervisor 按墙钟算出 86398s）、一次 DR 成功备份间隔 379.8s>300s RPO（备份机短暂掉线，非代码缺陷）、UNBOOKED_EXECUTIONS 作为健康 reason 持续 301s 落在 2 分钟类（实为对账事实，等待下一个 reconcile 周期）。对应修复：observer marker 增加 monotonic 起点/elapsed 字段；supervisor 时长改用 observer monotonic 计算（wall 仅旧 marker 回退，回退方向只会低估、fail-closed）；对账事实 reason（UNBOOKED_EXECUTIONS 等）归入 30 分钟类（与 unresolved 侧对称，dr_backups_fresh 权威判据未放宽）；13.17/runbook 校正 egress、preflight clean 1h 与对账 reason 类。tester 只读复核：monotonic 边界（两 marker 均有 monotonic 才启用、缺失回退 wall、`//1000` 截断只会低估）、reason 类与 unresolved 侧 11 码集合一致、旧 marker 回退行为不变（tester 66 用例 fixture 无 monotonic 字段仍走 wall 路径，接口未变、无需改动）。新指纹 implementation b7b0b4ca…（157 文件）、complete 350d815e…（159 文件）；final9 全量 3456/0/0/0、exit 0（final8 及更早仅历史）；镜像 aea741d2… 不变。历史修复链（本轮最终 source_digest 见上方字段与 round6_fix_chain；下列旧离线 68e1c3c1…/61 仅作历史）：修复链=审核 round1（P6-R1-SUPERVISOR-GAP：samples_cover_window/sample_gap_within_bound）→ 6 分钟演练触发的 duration-based reason bounds → Demo legacy listenKey 410 修复与 runbook/architecture/pairs 更新 → 演练复验两处修复（myTrades startTime lookback 过滤解决 UNBOOKED_EXECUTIONS；DR feeder 提前至执行就绪前）→ 窗口复验修正（账号/估值类 reason 归入 30 分钟网络恢复类；runbook §5.3 历史门禁）→ P6-R3-RECONCILE-LOOKBACK：恢复 reconcile 5 分钟 lookback 漏掉成交导致永久 UNBOOKED_EXECUTIONS，改为 preflight clean 5 分钟/runtime reconcile 1 小时/coverage 24 小时（getattr 默认值兼容 fixture）→ round-3 advisory A 与时间制 unresolved：unresolved 判据改按真实持续时间（reconciliation/balance 类 30 分钟、其他 2 分钟，UNBOOKED_EXECUTIONS 因 300s reconcile 周期会持续约 5 分钟故旧样本数判据必然误杀），UNKNOWN_COMMAND 归入 30 分钟对账类，tester 2 分钟类代表码改用 BOOK_ATTRIBUTION_UNKNOWN；window.json 新增 unresolved_max_duration_ms → 窗口 b4ee4b6d 中断后的直连与恢复类修正：沙箱容器与 preflight 出口由本机代理改为直连（--container-proxy 默认空、BINANCE_PROXY_URL 对齐；依据代理路径 70 分钟 ConnectionResetError，容器直连 REST/WS 握手已验证），RECOVERY_GENERATION_OR_OVERFLOW/RECOVERY_SNAPSHOT_UNSTABLE 归入 30 分钟恢复类（该轮曾把 DR_BACKUP_STALE 一并归入，父级升级后已移回严格 2 分钟界；DR 权威判据 dr_backups_fresh 未放宽）。tester 文件冻结于 offline 68e1c3c1…（61 用例）/ postgres 0f7e030f…。父级升级两个 finding 的修复链：summarize_rpo 改窗口级 RPO（supervisor 传入观测窗口 start/end：head ≤300s 覆盖窗口起点、内部 gap ≤300s、tail ≤300s 覆盖终点、毫秒精确、窗外备份不补历史空窗）→ S3 _create_bucket 幂等仅对验证过的任务目标（BucketAlreadyExists → _assert_task_target + head_bucket 可达性验证，失败 BUCKET_EXISTING_BUT_NOT_ACCESSIBLE 且诊断脱敏；_ensure_tunnel 复用前应用级 probe，失败丢弃重建并成功 readback）→ 分类修正：RECOVERY_GENERATION_OR_OVERFLOW 归入 30 分钟恢复类、DR_BACKUP_STALE 移回严格 2 分钟（dr_backups_fresh 权威判据未放宽）→ DR cadence 120s + 启动前 3 连续 verified off-host cycle 门禁 → 容器/preflight 直连出口与 DR 代理路径隔离 → 窗口 d2b0356f 在首个 DR cycle 前因 host supervisor `NameError: tempfile`（漏 import）fail-closed abort（34s、dr.cycles=0，归档 not-acceptance，启动期 DR 门禁按设计拦截），修复为单行 `import tempfile`（语义零变化）后重算 digest。→ observer 会话过期 finding 与修复（最后一次源码变更）：control 会话生命周期 `lifetime_seconds=43200`（12h，`trader/api/auth.py`）到期后，`/v1/trading/metrics` 与 risk 返回 401 响应而不是抛异常，`scripts/observe_trading_runtime.py` 原只在异常分支清 token，因此 12h 之后的每轮采样都带着过期 token，窗口 `20261105T135205Z-9f6dbb22` 自 01:54Z 起 metrics 证据持续 401 缺失、证据链断裂；工程师主动停止该窗口并按 not-acceptance 归档（前 12h 后验健康：coverage/DR/samples/无重复发送等判据成立，仅 duration、工程师主动停止与末端 reasons 未过）。修复为 metrics/risk 响应 401/403 时清空 csrf，使下一轮重新登录；新增工程师测试 `trader/tests/test_phase6_observer_relogin.py`（会话过期→401→重新登录→证据恢复）。随后重算最终指纹 `implementation=79038d82…`（157 文件）/ `complete=a32e7478…`（159 文件），final9 全量 3444/0/0/0、镜像 `aea741d2…` 不变，并重启 24h 窗口。 → reviewer round 7 N1–N5 非阻塞文档同步（最终窗口 cc19a9ea 运行期间的 docs-only 变更，运行镜像与宿主脚本不变）：13.17（INTERFACE_CONTRACTS.md §13.17）/UNIFIED_TRADING_RUNBOOK/engineer-window-runbook 的 coverage 末端新鲜度文本统一为派生上界 max(300s, 2*reconcile_interval + sample_gap_limit)=780s，并写明 coverage.recorded_at_ms 必填、缺失 fail-closed、window.json 输出 coverage_fresh_limit_ms/coverage_missing_recorded_at_ms（与 scripts/sandbox_supervisor.py 的 coverage_fresh_limit_ms/coverage_missing_recorded_at_ms 实现一致）；window-runbook 中止清单补 9f6dbb22（observer 会话过期 not-acceptance）；issue ledger 补 P6-R6-COVERAGE-FRESH-MARGIN 与 P6-R6-ROUND10-TESTER-EXECUTION-PENDING；evidence-index 的 tester_tests 统一为 run11 66/eb643f0a… 与报告 SHA 0b3b3b8a…；UNIFIED_TRADING_RUNBOOK 的 30 分钟未决类清单补 RECOVERY_GENERATION_OR_OVERFLOW、RECOVERY_SNAPSHOT_UNSTABLE（与 supervisor reconciliation_unresolved 一致）。docs-only 同步后最终 manifests 重算为 implementation 56a7cf79…（157）/complete 9380eb14…（159）；C5 行与 unexecuted_checks 中记录的 cba4983f…/f7f999e0… 为 final12 时点历史值。已披露差异：engineer-window-runbook 的 no_persistent_unresolved 30 分钟码清单与 §13.17 均未列出上述两个 RECOVERY 码（以 UNIFIED_TRADING_RUNBOOK 清单为权威文本）；evidence-index tester_report.overall 存在 6.13 笔误（应为 6.12）。；round12 元数据追加（egress 拆分与 f0a611de 归档）：窗口 20261107T002812Z-f0a611de 的私有流在代理路径上于 2026-11-07T07:34Z 实测完全不可达（代理 WS 连接 ClientConnectorError、容器直连 WS 连接并保持 300s），私有流中断约 48 分钟（PRIVATE_STREAM_UNHEALTHY 未解释时长 2_916_539ms，超 30 分钟界），由工程师停止并归档 not-acceptance（aborted_reason=ENGINEER_ABORTED_EXTERNAL_DEMO_WS_OUTAGE_VIA_PROXY_PATH、passed=false，后验汇总在 windows-aborted/20261107T002812Z-f0a611de/）；据此 supervisor 出口拆分为两条显式路径：新增 --preflight-proxy（默认空=继承 .env 的 BINANCE_PROXY_URL）供 host 侧 preflight 使用，容器 --container-proxy 默认空=直连（实测稳定），start_window.ps1 已移除容器代理参数；Demo 与 preflight 仍指向同一 endpoint/同一账户（同一 BINANCE_ENV=demo 与同一凭证，preflight evidence 记录 rest_base/private_ws_base），离机 DR 仍 LAN 直连 192.168.3.4 且与 Demo 出口隔离（_load_env 剥离宿主 HTTP(S)_PROXY/ALL_PROXY）；随后重算最终指纹 implementation 89deead0…（157 文件）/ complete b616b55b…（159 文件），egress 拆分后的最终全量为 final8 3454 passed/0 failed/0 skipped/0 error、exit 0（final12 及更早仅历史）；tester 66 用例无需 fixture 更新（_preflight 以 getattr 默认值读取 preflight_proxy/runtime_lookback_ms，SimpleNamespace fixture 兼容，离线文件无 proxy 引用）；round14 元数据（2026-11-09，独立测试员）：启动互斥 guard 修复后的 source_digest 更新为 implementation f6498565…（160 文件）/ complete 8b2a374d…（162 文件），此前 b7b0b4ca…/350d815e… 及更早值均转为历史；round14 的只读验证、final10 交叉核对与 blocking finding 见 body 的 round14 段；round15 元数据（2026-11-09，独立测试员）：B1 已修复并复验通过（guard 不再把非 owner supervisor 当孤儿；0/0.1/0.25/0.5/1.0s 共 36 对真实入口竞争全部 [0,3]、无 exit 4），A2 已修复（preflight lookback = max(lookback_ms, runtime_lookback_ms)），A3 由工程师 15 项 guard 套件覆盖；source_digest 更新为 implementation accf7614…（160 文件）/ complete 50692d8b…（162 文件）；final11 全量 3471/0/0/0、exit 0。详见 body round15 段；round16 元数据（2026-11-09，独立测试员）：fresh reviewer round11 的 B1（P6-R11-GUARD-CLEANUP-STDOUT）与 B2（P6-R11-GUARD-STRESS-RAW-EVIDENCE-MISSING）均已修复并独立复验通过；source_digest 更新为 implementation 74c95aa7…（160 文件）/ complete 0f932c43…（162 文件）；final12 全量 3474/0/0/0、exit 0；guard 套件 18/18×3。详见 body round16 段。"
  },
  "snapshot_path": "artifacts/foundation/phase6/phase6-input-snapshot.json",
  "overall": "pass_for_6.3-6.12; pending_6.1-6.2",
  "workspace_frozen": true,
  "authored_by": "tester",
  "executed_by": "tester (rounds 14–16, independent shell); engineer (historical rounds 1–13)",
  "analyzed_by": "tester"
}
```

## 声明范围

`overall` 定稿为 **pass_for_6.3-6.12；6.1/6.2 pending**。6.3–6.10 的既有验证继续成立，
round10/round6 新增用例已由工程师 run11/run12 代跑通过（run12 复跑 66/0/0/0，exit 0），
C4b final12（reviewer B1/B2 修复后的最终全量）为
`3474 passed / 0 failed / 0 skipped / 0 error`、exit 0。final9（3456）、final10（3466）、
final11（3471）及更早轮次（含历史同名 final11 的 1 个满载下 `test_fakes.py` 事件循环
创建抖动 setup error，该用例单独重跑 1 passed）仅作历史，final12 取代之。round14 的
guard 并发 blocking finding（P6-R14-GUARD-CONTENDER-MISCLASSIFIED）已在 round15 修复并
独立复验通过（36/36 对 `[0,3]`、guard 套件 15/15×3）；fresh reviewer round11 的
P6-R11-GUARD-CLEANUP-STDOUT 与 P6-R11-GUARD-STRESS-RAW-EVIDENCE-MISSING 已在 round16
修复并独立复验通过（真实清理函数正常/中止路径、40/40 对原始压力证据）。items 6.1（≥24h 连续沙箱窗口）与 6.2
（私有 WS 覆盖）为 **pending**——尚无走完 24h 且全部门禁通过的窗口（50077d3c 走满 24h
但官方 window.json passed=false，见下），本报告明确不声明其通过。

本报告覆盖测试者在 `trader/tests/test_phase6_independent_offline.py`（66 个
用例）与 `trader/tests/test_phase6_independent_postgres.py`（4 个用例）中独立验证的内容，
以及 black 格式检查。round15 独立复跑：`66 passed / 0 failed / 0 skipped / 0 xfailed`，
exit 0，JUnit `tests="66"`；guard 套件 3×15/15、exit 0；black 两文件
`would be left unchanged`。round14–15 的命令由测试者在具备 shell 的会话中独立执行；
round1–13 因 DSH sandbox ACL（`SetNamedSecurityInfoW failed (Win32 5): grantWrite`）
由工程师代跑，相关历史证据保留原归属。
24 小时持续沙箱窗口做了审计准备（runbook 与 `_summarize` 判据验证），但已启动窗口均未走完
24h（`9f6dbb22` 最长 12.15h，因 observer 会话过期归档 not-acceptance；`f0a611de`
7.45h，因代理路径 demo WS 中断约 48 分钟归档 not-acceptance，见
unexecuted_checks），不声明通过。

## 测试项（items，按阶段 0 权威编号）

| 项 | 权威内容 | 结论 | 支持证据（tester 用例） |
| --- | --- | --- | --- |
| 6.1 | ≥24h 连续沙箱窗口 | **pending（50077d3c 走满 24h 但官方 window.json passed=false，不声明通过）** | 判据已验证：supervisor `_summarize` 合成窗口全组判据（含 samples_cover_window ≥90% 密度、sample_gap_within_bound ≤180s、metrics 证据密度/最大间隔/末端覆盖新鲜度、duration-based reason bounds、时间制 unresolved 界（reconciliation 类 30 分钟/其他 2 分钟）、结束必须清空）、窗口级 DR RPO≤300s（首/尾/内部/毫秒精确）、RECOVERY_GENERATION_OR_OVERFLOW 30 分钟类与 DR_BACKUP_STALE 2 分钟分类、S3 幂等/tunnel probe、runbook 就绪；run11 66 用例全绿（含 24h 证据断层红/绿、coverage margin 780s 边界）；最长真实窗口 9f6dbb22 为 12.15h（observer 会话过期）、f0a611de 为 7.45h（代理路径 demo WS 中断约 48 分钟），均归档 not-acceptance；50077d3c 走满 24h（monotonic）但官方 window.json passed=false（wall marker 短 2s / 一次 DR 备份间隔 379.8s / UNBOOKED_EXECUTIONS reason 301s），见 6.1/6.2 审计节；下一窗口待 1h clean 后启动，tester 届时仅审计 |
| 6.2 | 私有 WS 覆盖 | **pending（随 6.1 窗口）** | coverage/连续性判据已在合成窗口验证；网络类 reason 恢复边界（29min 恢复通过 / 31min 失败）已验证；真实覆盖数据待最终窗口产生 |
| 6.3 | 旧 main / 只读迁移 | pass | `test_retired_modules_cannot_be_imported`（root services/retired 模块不可导入）、静态无残留引用/无遗留写入扫描、`trader.services.*` 旧栈除名；遗留表只读保留（PG 写触发器 + sentinel 可读） |
| 6.4 | OMS submit | pass | offline：place → venue 派发、`order.queued`/`order.accepted` 事件、统一 `OrderStatus` 映射（EXPIRED_IN_MATCH→CANCELLED）；PG：成交跨重启按 `exec_id` 恰好入账一次、余额 950 USDT / 0.75 BTC / book 350 USDT |
| 6.5 | ExecutionBudget / OrderService | pass | 静态：`trader.services.execution_budget`、`budget_reservation_repository` 退役且 `save_budget_reservation` 等遗留写入无调用方；运行：唯一 `TradingService` 统一 allocate/place/control/resume 路径（`defer_send` 拆分 + `CONTROL_PROCESS_CANNOT_EXECUTE`）；预算域细分行为见工程师全量回归（C4 背景） |
| 6.6 | 旧 strategy runner | pass | `test_only_unified_strategy_runner_produces_risk_commands`：全仓无第二个 risk command 产出方；`trader.services.strategy_runner` 退役不可导入；`/v1/strategies/loaded` 只读 |
| 6.7 | Control 无事实写入口 | pass | Control 进程（`defer_send=True`）只能排队、不能执行事实（`resume_queued` 拒绝）；`/v1/control/*` 只读；killswitch 为持久化控制命令而非本地标志；410（`LEGACY_*_RETIRED`）/503（`RECONCILIATION_IS_EXECUTION_OWNED`、`LEGACY_CANCEL_RETIRED`、`LEGACY_ALLOCATION_RETIRED`）拒绝码 |
| 6.8 | 无 eligibility=None | pass | `test_backtest_application_requires_an_eligibility_gate`：构造 `BacktestApplication` 必须显式传入 eligibility 门禁 |
| 6.9 | 旧账户/投影表停写 | pass | PG T4：16 张保留遗留表挂写触发器，recover/allocate/place/fill/control 全流程零写入、零触发器报错，且保留可读（sentinel 读回）；`save_lot/save_position_projection/save_reconciliation/save_strategy_runtime_state` 无生产调用方 |
| 6.10 | 观察 JSONL | pass（run11） | 字段、fsync、ready 状态迁移（含 UNKNOWN/ 重试路径）、401/403 清 csrf 后重登且不泄露密码、无密码拒启（exit 2）、send 结果/coverage/execution 计数进 metrics；run11 66 用例全绿。独立新增 metrics 401 与 risk 401 两个分支的同一样本重登、证据恢复用例，均通过（attempts=[False, True]、只登录一次、metrics/risk 恢复） |
| 6.11 | 无第二套下单/账务实现 | pass | 静态全仓扫描：唯一 HTTP 组装（`create_trading_app`）、唯一服务/账务栈；遗留写入函数定义仅存在于已退役文件，`trading_executions`/`trading_ledger` 写入仅在 `trader/trading/postgres.py` |
| 6.12 | P0 + 核心回归 | pass（final12） | tester 侧 C1（round16 独立复跑：66 passed/0 failed/0 skipped、exit 0；历史 run11/run12 同值）、C2（run4 补跑：4 passed）、C3（round16：black 两文件 unchanged）；工程师 C4b final12（reviewer B1/B2 修复后源码）`3474 passed / 0 failed / 0 skipped / 0 error`，exit 0（`engineer-full-suite-final12.*`），含 tester 66 + guard 18 + window-gates 23 + observer 1 + tester postgres 4。final9（3456）、final10（3466）、final11（3471）及更早仅作历史；历史同名的 3454 用例 final11 轮次曾为 1 个 setup-only 环境抖动 error（`TestFakeWebSocket::test_pong_triggers_waiter` 的 `new_event_loop()` `WinError 10055`，单独重跑 1 passed），仅作历史。C4 prod-final 3361/4 保留为 round1 背景，4 个失败均为 tester PG fixture 缺陷。round14 的 guard 并发 B1 已在 round15 修复（36/36 对 `[0,3]`，round16 再复 40/40）；reviewer round11 的 B1 清理路径与 B2 原始压力证据已在 round16 修复/补齐并独立复验 |

| 门 | 覆盖 |
| --- | --- |
| G1 | 单栈静态证明（items 6.3/6.6/6.11） |
| G2 | Control 只读 + killswitch/410/503 + 控制面不可执行事实（item 6.7） |
| G3 | 真实 PG 账户/预留/台账副作用 + 零遗留表写入（items 6.4/6.9） |
| G4 | 观察 JSONL 字段/fsync/迁移/密钥（item 6.10） |
| G5 | supervisor fail-closed 判据（6.1/6.2 的窗口判据预验证：samples/gap 覆盖 + duration-based reason bounds + 窗口级 DR RPO 判据） |
| G6 | 回归与格式（C1–C3 + 工程师全量回归 C4b final9，item 6.12） |

## 6.1 / 6.2 只读审计（round10）

**历史窗口 `20261105T135205Z-9f6dbb22`：not-acceptance，不能用于 6.1/6.2。** 原始
`observation.jsonl` 显示：窗口运行 43_747s（12.15h），最后一条有完整证据的样本是
2026-11-06T01:52:58Z（seq 1437，metrics/risk 均 200，coverage contiguous、recorded_at_ms
= 01:52:15Z，无 unresolved、无 repeated send、executions=71）；01:53:28Z 起首个 401 样本
（seq 1438）只保留 health，连续 19 个样本到 02:02:29Z 的 metrics/risk 都是 401。此后
coverage 新鲜度、unresolved 清空和重复发送都无法从 metrics 证明；旧后验汇总仍写
`unresolved_cleared_at_end=true`，是该 finding 的现场反例。DR journal 末次成功备份距窗口末端
32.9s，但这不能补足 metrics/coverage 证据。结论：**6.1/6.2 不成立**，归档正确。

**窗口 `20261107T083226Z-50077d3c`：走满 24h 但官方 `window.json` `passed=false`，不是验收证据。**
tester 只读复核原始产物（window.json / observation.jsonl / dr/）：observer marker 无
monotonic 字段（该窗口跑在修复前代码上），wall 起止差 86_398s < 86_400s（NTP slew 短 2s，
observer 循环 deadline 按 monotonic 计实际为 86400s）；`duration_at_least_24h=false`、
`no_persistent_reasons=false`（UNBOOKED_EXECUTIONS 作为健康 reason 持续 301_091ms > 2 分钟界，
实为对账事实：成交等待下一个 300s reconcile 周期；unresolved 侧同码 329_595ms 在 30 分钟类内
本来就不失败）、`dr_backups_fresh=false`（708 次成功备份中一次成功间隔 379.8s > 300s RPO，
备份机短暂掉线的外部事件；head/tail 覆盖均成立）。其余判据全部为 true：2872/2879 采样密度、
metrics_gap_max 32_202ms（≤180s 界）、coverage_fresh_at_end（780s margin）、unresolved_cleared_at_end、
无重复发送、execution 单调、无容器重启/abort/error、reasons_cleared_at_end。前两项已由
monotonic 时长判据与对账 reason 类修复覆盖（tester 已只读复核实现与工程师
window-gates 用例），DR 间隔为外部备份机掉线、判据本身按设计 fail-closed。新窗口将在
1h clean（2026-11-08T09:33:04Z）满足后启动；窗口完成后由 tester 做 6.1/6.2 只读审计
（24h 连续、monotonic 时长、metrics 证据密度/间隔、coverage 末端新鲜 ≤780s、DR RPO
≤300s、无未决/重复发送/重启），不声明通过。
此前窗口 `20261107T002812Z-f0a611de` 运行 26_808s 后因代理路径 demo WS 中断约 48 分钟
被工程师停止并归档 not-acceptance（windows-aborted/，
aborted_reason=ENGINEER_ABORTED_EXTERNAL_DEMO_WS_OUTAGE_VIA_PROXY_PATH）。
结论：**6.1/6.2 仍为 pending，不声明通过**。

## 检查（checks）

| id | 命令 | exit | passed | failed | skipped | xfailed | 产物 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C1（round15 独立复跑；历史 run11/run12 同值） | `python -m pytest -o addopts='' -q -p no:cacheprovider trader/tests/test_phase6_independent_offline.py --junitxml=artifacts/foundation/phase6/tester-round14/tester-offline-round15.xml --tb=short` | 0 | 66 | 0 | 0 | 0 | `artifacts/foundation/phase6/tester-round14/tester-offline-round15.*`（round15：stdout `66 passed, 3 warnings in 27.80s`、XML tests=66、exit=0）。历史 run11/run12（`tester-offline-engineer-run11/run12.*`）为同文件前次全绿运行 |
| C2 | `POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:5432/qts_p6_tester_d603ce3e python -m pytest -o addopts='' -q -p no:cacheprovider trader/tests/test_phase6_independent_postgres.py --junitxml=...tester-postgres-engineer-run4.xml --tb=short` | 0 | 4 | 0 | 0 | 0 | `artifacts/foundation/phase6/tester-postgres-engineer-run4.stdout.txt` / `.xml` / `.exitcode.txt`（run4 首次因 502 中断，已补跑完成） |
| C3（round15 独立执行） | `python -m black --check --diff --line-length 100 trader/tests/test_phase6_independent_offline.py trader/tests/test_phase6_independent_postgres.py` | 0 | 2 files unchanged | 0 | 0 | 0 | round15 测试者本地执行 `2 files would be left unchanged`、exit 0；历史 run11 工程师复核与 `tester-black-diff-run6.*` 仅作旧轮记录 |
| C4（round1 背景，已被取代） | 工程师全量 suite（prod-final） | 1 | 3361 | 4 | 0 | 0 | `artifacts/foundation/phase6/engineer-full-suite-prod-final.stdout.txt` / `.xml` |
| C4b（final12，工程师执行；最终） | 工程师全量 suite（final12，源码含 reviewer B1 清理修复（`_stop_stack_best_effort` 直接用 `_checked()` 字符串）+ B2 原始压力证据 + round15 B1 的 guard 孤儿语义 + A2 `_preflight_lookback_ms` + 前述直连出口、egress 拆分、恢复类归类、lookback/时间制 unresolved、窗口级 RPO、S3 幂等/分类、tempfile import、observer 401 重登、metrics 证据门禁、coverage 780s margin、observer marker monotonic、supervisor monotonic 时长、对账事实 reason 30 分钟类） | 0 | 3474 | 0 | 0 | 0 | `artifacts/foundation/phase6/engineer-full-suite-final12.*`（XML `tests=3474 errors=0 failures=0 skipped=0`、exit=0；stdout `3474 passed, 24 warnings in 301.56s`；含 tester offline 66 + guard 18 + window-gates 23 + observer 1 + tester postgres 4）。final9（3456）、final10（3466）、final11（3471）及更早仅作历史 |
| C4b-flake（历史记录；同名 final11 已被 round15 新轮次复用） | 历史 3454 用例的 final11 重跑 | 1 | 3453 passed；1 setup error；0 failed/skipped | 0 | 0 | 0 | 历史 XML `tests=3454 errors=1 failures=0 skipped=0`、exit=1（error 为 `TestFakeWebSocket::test_pong_triggers_waiter` setup 的 `new_event_loop()` `WinError 10055`，单独重跑 1 passed）；该 setup-only flake 仅作历史，其产物已被新的 final11（3471 全绿）覆盖 |
| C5（digest 重算，最终） | round16 独立复算 manifest | 0 | 160/162 文件 | — | — | — | `engineer-source-manifest-impl.json`（160 文件，`74c95aa77039a6b7cc2a82cd7622be7ae63997c3620bdd33c5696a533f94e879`）/ `engineer-source-manifest-final.json`（162 文件，`0f932c430e65d8d83c1c1e0154778d2babda2d7ebdaac80c5e3ea652bebd25a0`，已含 tester 66 用例 `eb643f0a…` 与 postgres 4 用例 `0f7e030f…`）——reviewer B1/B2 修复后的最终对。round16 独立复算 `digest_match=true`、`mismatch=[]`、`null_but_exists=[]`，文件集与 git 变更集一致（impl 明确排除两个 tester 测试文件）。历史值：accf7614…/50692d8b…（round15 B1/A2 修复时点）、f6498565…/8b2a374d…（round14 guard 初版）、b7b0b4ca…/350d815e…（monotonic/对账 reason 类修复时点）、89deead0…/b616b55b…（egress 拆分时点）、cba4983f…/f7f999e0…（历史 final12 时点）、56a7cf79…/9380eb14…（N1–N5 docs-only 后） |

C4 的 4 个失败全部是测试者自己的 PG fixture 缺陷（opening ledger 基线、缺 `reason`、jsonb
dict 绑定），不是生产缺陷；已在 round2–round4 修复，并以 C2 全绿复核。final9（monotonic
时长判据 + 对账 reason 类修复后源码）已跑完 `3456 passed / 0 failed / 0 skipped / 0 error`，
exit 0，包含 tester 66 用例、工程师 window-gates 23 用例与 observer 1 用例。final8（3454）
及更早仅作历史（含 final11 的 1 个
setup-only 环境抖动 error：`TestFakeWebSocket::test_pong_triggers_waiter` 的
`new_event_loop()` `WinError 10055`，单独重跑 `1 passed`）。final10 的 3446 及更早同样
仅作历史；C4 prod-final 3361/4 保留为 round1 背景。

## 本轮修复（仅测试者自有文件）

- `test_queued_allocation_survives_restart_until_a_snapshot_exists`：`recover()` 后取 opening
  ledger 基线并断言 `==2`；ALLOCATE 后断言不变。
- `test_fill_side_effects_are_booked_exactly_once_across_restarts`：基线 `==2`，成交后
  `ledger == opening_ledger + 2`；余额 950 USDT / 0.75 BTC / book 350 USDT。
- `test_fill...` / `test_killswitch...` / `test_no_flow_writes_a_legacy_table`：所有 `allocate()`
  提供非空 `reason`。
- `_seed_legacy_tables`：JSONB 参数改用 `json.dumps({})`。
- black：按 `tester-black-diff.txt` 逐 hunk 手工应用（测试者无法本地执行 black），C3 复核通过。
- round5（响应审核发现 P6-R1-SUPERVISOR-GAP）：supervisor 判据用例改为密集采样——`_dense_samples`
  helper（30s 间隔 2880 条）、`_clean_window` 密集化、clean window 断言
  samples/expected/min_samples/gap_limit/gap_max、新增稀疏采样与超距 gap 两个失败用例；
  transient/health/repeated/balance 窗口改密集。
- round6（响应 6 分钟演练暴露的按采样数判据过严）：`no_persistent_reasons` 用例改为
  duration-based——非网络 reason `OPERATOR_BLOCK_PENDING` 全窗持续（86_370_000ms）失败、
  网络类 29min 恢复通过/31min 失败边界用例、短/长 warmup 两段（unexplained 始终为 {}）。
- round7（响应 P6-R3 后的时间制 unresolved 判据）：两个样本数判据用例改为时间制——非
  reconciliation 类 `BOOK_ATTRIBUTION_UNKNOWN`（round-3 advisory A 后替换 UNKNOWN_COMMAND）
  1 分钟通过/3 分钟失败、reconciliation 类 BALANCE_MISMATCH 29 分钟通过/31 分钟失败，均以
  `unresolved_entries[].at_ms` 手算 `unresolved_max_duration_ms` 断言；`_sample` helper 增加
  `unresolved_entries` 透传；用例总数保持 48。
- round8（响应父级升级两个 finding）：`_clean_window` 改为全窗口备份——新 helper
  `_window_backups`（`[T0-120_000]` + 每 180_000ms 至 `T0+DAY_MS`，手算 head age 120_000ms、
  内部 gap 180_000ms、窗口尾 age 1_000ms），所有期望通过的窗口 build 一并切换；保留并显式化
  「备份日志只有开头一段」失败反例（head-only：head 覆盖/tail 未覆盖 → `dr_backups_fresh=False`、
  `passed=False`）；新增窗口级 RPO 5 用例（缺首覆盖含窗外备份不补空窗、缺尾覆盖、内部 gap、
  300_000ms 过/300_001ms 败、全覆盖正例）、分类 1 用例（3 分钟 RECOVERY_GENERATION_OR_OVERFLOW
  过 30 分钟恢复类、3 分钟 DR_BACKUP_STALE 败 2 分钟界且 `dr_backups_fresh` 仍 True）、S3 4 用例
  （verified 任务目标重连幂等、BUCKET_EXISTING_BUT_NOT_ACCESSIBLE + 诊断脱敏、foreign/rejected
  目标参数化×3 拒绝且不 probe、`_ensure_tunnel` 活 tunnel probe 复用/失败丢弃重建 + 成功
  readback）；用例总数 48→61。
- round10（响应 fresh reviewer round5 的两个 blocking finding）：
  - **P6-R5-METRICS-EVIDENCE-GAP**：新增 `test_metrics_evidence_stopping_at_half_the_window_cannot_pass`
    与 `test_a_fully_evidenced_24h_window_passes`。前者构造 24h、每 30s 一条的 2880 个样本，
    前 1440 条带完整 metrics 体、后 1440 条为 401 无体；手算 present=1440 < min=2592、
    missing=1440、最后证据在 T0+43_170_000、尾部 gap=43_230_000ms > 180_000ms，因此
    `metrics_samples_present` / `metrics_gap_within_bound` / `coverage_fresh_at_end` /
    `unresolved_cleared_at_end` 均为 false 且 `passed=false`。后者全部 2880 条带 metrics 与
    `coverage.recorded_at_ms`，手算 metrics gap=max(30_000, 30_000)=30_000ms，四项判据及
    `passed` 均为 true。`_sample` helper 增加 `metrics_present` / `metrics_status_code` /
    `coverage_recorded_at_ms` 三个可选参数；round6 后默认写入样本 `ts_ms`，显式 `None`
    保留缺失反例。
  - **P6-R5-OBSERVER-SESSION-RELOGIN**：新增 `_ReloginMetricsClient` 和
    `test_observer_metrics_401_relogins_and_keeps_the_same_sample_evidence`、
    `test_observer_risk_401_relogins_and_keeps_the_same_sample_evidence`。两个用例分别让
    metrics 与 risk 的首次 GET 以 401 **响应**返回，保留上一样本的 stale csrf；断言同一
    样本内 attempts=`[False, True]`、只登录一次、重试后 metrics evidence/coverage 与 risk
    READY 都恢复。两条响应分支均覆盖，run11 实测通过；不再存在“重登路径仅由工程师测试锁定”
    的缺口。
  - **P6-R6-COVERAGE-FRESH-MARGIN**：新增 `test_coverage_freshness_margin_allows_reconcile_phase_and_fails_closed`。
    24h 窗口的 2880 个样本在 `T0+86_400_000` 精确结束，手算 `2*300_000 + 180_000 =
    780_000ms`；末端覆盖年龄 0/60/120/180/240/300s 全部 `coverage_fresh_at_end=True` 且
    `passed=True`；精确 780_000ms 通过，780_001ms 失败；`recorded_at_ms` 缺失时
    `coverage_fresh_at_end=False`、`passed=False`（fail-closed）。同时断言
    `coverage_fresh_limit_ms == 780_000`。
  - **P6-R6-ROUND10 执行证据**：round-6 审核指出旧窗口 fixture 未带
    `metrics.coverage.recorded_at_ms`，8 个健康窗口用例会因新判据失败。tester 将 `_sample`
    默认值改为样本 `ts_ms`，并保留显式 `None` 的 fail-closed 反例；run11 离线 66/0/0/0、
    exit 0、JUnit tests=66，black 两文件 unchanged，offline SHA-256
    `eb643f0a…`。final12 全量 3454/0/0/0、exit 0（现为历史）；最终全量为
    final9 3456/0/0/0、exit 0（final8 及更早仅作历史）。final11 的 1 个 setup-only 环境抖动
    error 单独重跑通过，仅作历史。run10 的 65/0/0/0 只作历史。
  - 用例总数 61→66；run11 实测 C1=66/0/0/0、exit 0、JUnit tests=66；C3=2 files unchanged。
    所有 66 个用例已由工程师实际执行，不再是 authored-only。

## round14 独立验证：启动互斥 guard（P6-TESTER-ROUND14-GUARD）

本轮由独立测试员（全新会话，具备 shell）按父级决定
`artifacts/foundation/phase6/parent-successor-20261108/decision.md` 与 finding
`P6-R10-DUPLICATE-WINDOW-START-20261108` 对 `trader/ops/startup_guard.py` +
`scripts/sandbox_supervisor.py` 的启动互斥 guard 做只读验证。未启动/停止任何窗口，未访问
网络、Demo 或真实账户，未改 ACL/提权，未改生产源码、宿主脚本、CI 或工程师测试。源码冻结于
`f6498565…`/160 与 `8b2a374d…`/162。

### 逐项 verdict

| 项 | 要求 | verdict | 独立证据 |
| --- | --- | --- | --- |
| 1 | 固定主机级锁路径、非阻塞原子获取、任何副作用前获取、失败者零副作用、元数据无凭据、持有至安全清理 | **pass** | T01/T02/T03/T12/T13/T14/T17：路径稳定为 `%LOCALAPPDATA%\qts\phase6-sandbox-start.lock`；竞争者 2.37ms 内以 3 拒绝且 orphan probe 未被调用；元数据键仅 pid/run_id/entry/started_at_utc/host/cwd，哨兵密钥不出现；`main()` 1584 行 acquire 早于 1602 行构造 supervisor，release 在 finally（1605），`run()` 的 finally 调用 `_stop_stack_best_effort`（1519）；真实 `run()` 动态验证 inner 与 cleanup 期间 guard 均为 held；同进程第二个 handle 也以 0.61ms 拒绝 |
| 2 | 真实双进程竞争恰好一个 owner、另一个 STARTUP_GUARD_HELD(3)；正常释放/owner 崩溃/崩溃后孤儿 fail-closed；真实入口单进程与并发 | **fail（blocking，见 P6-R14-GUARD-CONTENDER-MISCLASSIFIED）** | 正常释放 T04/T05 pass；os._exit 崩溃 T05 pass；崩溃后存活任务进程 T06 pass（exit 4，清理后恢复）；容器分类/ Docker 不可用 T06/T07 pass（注入）；真实入口单进程 T09 pass；并发在 0 错开下重复执行出现 `[3,4]`，错开 0.1/0.25/0.5s 时稳定失败 |
| 3 | WorkBuddy 隧道、含脚本名 shell 包装、qts-postgres、self 不误报 | **pass** | T08：真实 WorkBuddy `ssh.exe` PID 32536（ConnectTimeout=10、`-L 19119:127.0.0.1:9119`）未被标记；真实 `docker ps` 仅 `qts-postgres`，未被标记；真实 `probe_orphans()` 返回 `[]`；合成 rows 仅命中 101/102/103 与 `qts_p6_sandbox_*` 容器；bash/cmd 包装与 self(107) 均排除 |
| 4 | open_orders/lookback 按实时 venue 事实与完整 runtime lookback fail-closed，不靠时间戳/本地账本重置 | **pass（附 advisory A2）** | `verify_preflight_lookback.py`：clean 通过；挂单 → `SANDBOX_ACCOUNT_HAS_OPEN_ORDERS`；30 分钟前 qts- 成交 + 1h lookback → `SANDBOX_LOOKBACK_NOT_CLEAN`；61 分钟前 → clean；foreign → `SANDBOX_ACCOUNT_NOT_DEDICATED`；读取 `get_open_orders/get_all_orders/get_my_trades` 并以 venue `server_time` 过滤，无本地账本/时间戳等待。实际启动脚本 `start_window.ps1` 传 `--lookback-ms 3600000` 与 `--runtime-lookback-ms 3600000` |
| 5 | 独立复算两个 manifest（文件数、逐文件 sha256、digest 算法） | **pass** | `verify_evidence.py`：impl 160 文件 / `f6498565…`，final 162 文件 / `8b2a374d…`，`digest_match=true`，`mismatch=[]`，`null_but_exists=[]`，重复路径 0、已排序；文件集与 git 变更集一致（impl 明确排除两个 tester 测试文件，final 包含）；supervisor `518d4ee6…`、guard `abaff736…`、tester offline `eb643f0a…`、postgres `0f7e030f…` 全部与声明一致 |
| 6 | 运行 tester 66 用例与 `trader/tests/test_phase6_startup_guard.py`，交叉核对 final10 | **fail（tester 66/66 pass；guard 文件 9/10，1 个并发用例失败；final10 交叉核对 pass）** | tester offline：`66 passed`、exit 0（`tester-offline-round14.*`）；guard 文件：`9 passed / 1 failed`，失败即 `test_real_supervisor_entry_concurrent_exactly_one_owner` 的 `[3,4]`（`tester-startup-guard-round14.*`）；final10 XML `tests=3466 errors=0 failures=0 skipped=0`、exit 0、stdout `3466 passed`，含 guard 10 / tester offline 66 / postgres 4 / window-gates 23 / observer 1。该 fail 是 guard 并发缺陷的现场，不是 tester 66 或 final10 交叉核对的失败 |
| 7 | 更新本报告；source_digest 换最终对；6.1/6.2 保持 pending；N5 判断 | **pass** | 本段与顶部 `source_digest` 已更新；6.1/6.2 未声明通过；N5 直接字段断言以独立 artifact 执行通过（见下），未改冻结套件以保持 final 对与 final10 计数有效 |

### Blocking finding：P6-R14-GUARD-CONTENDER-MISCLASSIFIED

> **round15 状态：fixed_verified（关闭）。** 工程师已按本 finding 修复：`probe_orphans`
> 不再扫描 supervisor 进程，OS 锁是 supervisor 归属的唯一权威；孤儿只按副作用幸存者
> （observer 进程、strict-key DR 隧道、运行中的 `qts_p6_sandbox_*` 容器）判断。round15
> 独立复验 0/0.1/0.25/0.5/1.0s 共 36 对真实入口竞争全部 `[0,3]`、无 exit 4；详见 round15 段。

**round14 原始结论：guard 的互斥安全性成立（未观察到第二个 stack/owner 并存），但“恰好一个 owner
通过”的活性属性不成立。** 孤儿探测在 owner 取得锁之后立即扫描全部进程，把仍在启动/退出
的并发竞争者（其 argv 含 `scripts/sandbox_supervisor.py`、但从未取得锁、也无任何副作用）
误判为 `supervisor_process:<pid>` 存活孤儿，导致 owner 自己以 `STARTUP_ORPHAN_DETECTED(4)`
退出。

可复现证据（均为真实入口 `python scripts/sandbox_supervisor.py --guard-probe`，两进程）：

| 两进程启动错开 | 结果分布 | 说明 |
| --- | --- | --- |
| 0.0s（同时） | 10/10 `[0,3]`（专项复现）；但工程师冻结用例重复执行 **累计 4/23 次失败为 `[3,4]`** | 时序相关；失败时 exit 4 的 findings 恰为另一竞争者的 PID |
| 0.1s | 8 对中 7 对 `[3,4]`（**零 owner**）、1 对 `[0,3]`；7/7 个 exit 4 的 findings 指向另一竞争者 | 先持锁者 exit 4，后到者 exit 3，两者都未启动 |
| 0.25s | 14 对中 12 对 `[0,4]`、2 对 `[3,4]`，**0 对 `[0,3]`**；14/14 个 exit 4 的 findings 指向另一竞争者 | 先持锁者 exit 4，后到者随后成为 owner（owner 反转）或两者都失败 |
| 0.5s | 4/4 `[0,4]`；4/4 findings 指向另一竞争者 | owner 反转 |
| 1.0s | 4/4 `[0,3]` | 已越过误判窗口 |
| 2.5s | 4/4 `[0,0]` | 顺序启动，非并发 |

失败样例（`engineer-concurrency-detail-run5.txt`）：
`assert [3, 4] == [0, 3]`，exit 4 输出
`{"error": "STARTUP_ORPHAN_DETECTED", "detail": "{\"findings\": [\"supervisor_process:4672\"], ...}"}`
——4672 是同一用例启动的另一个竞争者，不是崩溃 owner 的存活任务进程。

**影响**：父级要求的“真实双进程竞争恰好一个 owner、另一个以 3 退出”未被满足；同一秒内
手动 + watcher 启动时可能两个都失败（零 owner），或先启动者以 ORPHAN_DETECTED 失败、后
启动者成为 owner。没有观察到重复 stack/重复账户副作用——安全侧 fail-closed 成立，但
操作可用性与验收属性失败。工程师的并发用例只覆盖 0 错开，且本身约 18% 概率失败，final10
的一次全绿不足以证明该属性。

**建议修复方向（供工程师决定）**：在取得锁后对孤儿探测做有界重试/宽限（真实孤儿持续
存在，竞争者会在毫秒级退出）；或先探测后取锁并在取锁后复检；或让竞争者在尝试取锁前写入
可识别的 contender 标记并在探测中排除。修复后应把 0.1/0.25/0.5s 错开用例加入冻结套件。

### Advisory findings

- **A1（路径范围）**：默认路径 `%LOCALAPPDATA%\qts\phase6-sandbox-start.lock` 是**每用户**
  路径，不是跨 Windows 用户的真正主机级路径；同一用户/同一环境内稳定，两个不同 OS 用户
  可各自取得锁。当前部署是单用户，故不阻塞；若要求真正 host-wide，应改用共享位置并配 ACL。
- **A2（preflight 默认 lookback）**：`_preflight` 用 `self.args.lookback_ms`（CLI 默认
  300000=5 分钟）作为 clean 窗口，而不是 `runtime_lookback_ms`（默认 3600000=1 小时）。
  文档启动命令与 `start_window.ps1` 都显式传 `--lookback-ms 3600000`，验收路径满足“完整
  runtime lookback”；但直接 `python scripts/sandbox_supervisor.py`（guard 明确支持的入口之一）
  用默认值只检查 5 分钟。建议由 runtime lookback 派生或校验两者相等。
- **A3（测试覆盖）**：`test_real_supervisor_entry_concurrent_exactly_one_owner` 只覆盖
  同时启动；建议增加错开启动参数化用例，并让断言覆盖“exit 4 的 findings 不得是另一竞争者”。
- **A4（诊断路径）**：`--guard-probe` 取锁后固定 `time.sleep(2)`；在真实窗口启动的同一秒
  运行该诊断会把真实启动以 3 拒绝。仅影响诊断使用，建议文档提示。

### final10 与 manifests 交叉核对

- `engineer-full-suite-final10.xml`：`tests=3466 errors=0 failures=0 skipped=0`，
  `testcase` 3466 个，`failure/error/skipped` 节点均为 0；stdout 末行
  `3466 passed, 24 warnings in 281.46s (0:04:41)`；exitcode=0。分类计数：guard 10、
  tester offline 66、tester postgres 4、window-gates 23、observer relogin 1。
- `engineer-source-manifest-impl.json`：160 文件，`f64985650a471e58537a625426bb83c92a442e3963fd642061fbb43865789b6e`。
- `engineer-source-manifest-final.json`：162 文件，`8b2a374d2f4798cbe26c650906817d4fe1c961245c54fa688eb2638a49727af2`。
- 两者 `digest_match=true`、`mismatch=[]`、`null_but_exists=[]`；逐文件 sha256 全部匹配磁盘；
  impl 不含两个 tester 测试文件，final 含之；文件集与 `git diff/name-status base + untracked`
  的排除后集合完全一致。
- supervisor `518d4ee6b109a0bc1c0f50c4676de739a6be9ef9bd44bf48c90fa513d81344a5`、
  guard `abaff7367511df57ef3464c180c102357fc69eb2e3fff331d5e9e318a702d8e9` 与工程师声明一致。

### N5（observer marker 直接字段断言）

`verify_observer_marker.py` 直接运行真实 `scripts/observe_trading_runtime.py` 的 main 循环
（`_sample` 替换为本地 fake，无 HTTP/网络）：写入 start/finish 两个 marker 与 10 个样本；
finish 的 `elapsed_monotonic_ms=1015`，与 `finish.monotonic_ms - start.monotonic_ms=1015`
一致（整除截断容差 0–1ms），start 无 `samples` 字段、finish `samples=10`，两 marker 均有
整数 `monotonic_ms`/`ts_ms`，`verdict=pass`。为保持 `8b2a374d…`/final10 计数有效，本轮
**未**把该断言加入冻结的 `test_phase6_independent_offline.py`；建议工程师下次重算 manifest
时把它作为正式用例并入（会改变 complete digest）。

### 独立结论

1. guard 的静态属性、失败者零副作用、崩溃自动释放、崩溃后孤儿 fail-closed、Docker 不可用
   fail-closed、真实主机误报排除、单进程真实入口均成立。
2. **并发活性不成立（round14 blocking；round15 已修复并复验通过）**：真实入口在
   0.1–0.5s 错开启动时出现零 owner 或 owner 反转；工程师冻结用例在 0 错开下也约 18%
   失败。round15 修复后 36 对错开竞争全部 `[0,3]`、无 exit 4，B1 关闭；详见 round15 段。
3. 6.1/6.2 仍为 **pending**，本报告不声明通过；本轮未启动/停止任何窗口。
4. 证据目录：`artifacts/foundation/phase6/tester-round14/`
   （`guard-verification-round14.json`、`race-reproduction-round14-stagger*.json`、
   `preflight-lookback-round14.json`、`observer-marker-round14.json`、
   `evidence-verification-round14.json`、`tester-offline-round14.*`、
   `tester-startup-guard-round14.*`、`engineer-concurrency-repeat-round14.txt`、
   `engineer-concurrency-detail-run*.txt`）。

## round15 复验：B1 修复、A2 修复与 guard 最终 verdict（P6-TESTER-ROUND15-GUARD）

本轮由同一独立测试员按父级 round15 指令做只读复验。工程师未新开根因、未改历史：
1) `probe_orphans` 不再扫描 supervisor 进程，OS 锁是 supervisor 归属的唯一权威；孤儿只
检测副作用幸存者（窗口 observer、strict-key DR 隧道、运行中的 `qts_p6_sandbox_*` 容器）；
Docker 不可用仍 fail-closed。2) A2：新增 `_preflight_lookback_ms(args) =
max(lookback_ms, runtime_lookback_ms)`，`_preflight` 使用该值。3) A3：guard 套件扩展为 15 项，
含真实入口 stagger 0/0.1/0.25/0.5/1.0s 参数化与 preflight lookback 单测。

未启动/停止窗口，未访问网络、Demo 或真实账户，未改 ACL/提权，未改生产源码、宿主脚本、CI
或工程师测试。源码冻结于 `accf7614…`/160 与 `50692d8b…`/162。

### 逐项 verdict

| 项 | 要求 | verdict | 独立证据 |
| --- | --- | --- | --- |
| 1 | 0/0.1/0.25/0.5s（每档多对）与 1.0s 真实入口 stagger 矩阵；断言不再出现 `[3,4]`/`[0,4]`/`[3,3]`，exit 4 findings 不含竞争者 PID；“恰好一个 owner、另一个 exit 3”稳定 | **pass** | 独立 36 对：0/0.1/0.25/0.5s 各 8 对 + 1.0s 4 对，全部 `[0,3]`（`exactly_one_owner_pairs` 36/36），`pairs_where_orphan_finding_is_the_other_contender=[]`，无 exit 4；工程师 15 项 guard 套件独立连跑 3 次均 15/15（含 5 个 offset 参数化） |
| 2 | 真实入口并发/崩溃/孤儿子集；15 项 guard 测试与 tester harness | **pass** | harness 17/17：T04 竞争 `[0,3]`、T05 os._exit 崩溃后成功接管、T06 崩溃后存活 observer → 真实入口 exit 4 + `observer_process:<pid>`、清理后恢复、T07 Docker 不可用 fail-closed；T15/T16（0.25/0.1s）均 `[0,3]`；guard 套件 3 次 15/15 |
| 3 | 独立复算 manifest 与三个 host 文件 sha256；交叉核对 final11 XML/exitcode | **pass** | impl 160/`accf7614…`、final 162/`50692d8b…`，`digest_match=true`、`mismatch=[]`、`null_but_exists=[]`，文件集与 git 一致；supervisor `2df69d76…`、guard `50e680f2…`、guard test `8b7483b8…` 全部匹配；final11 XML `tests=3471 errors=0 failures=0 skipped=0`、exit 0、stdout `3471 passed`（guard 15、tester offline 66、postgres 4、window-gates 23、observer 1） |
| 4 | A2 修复审阅：preflight 使用完整 runtime lookback | **pass** | `_preflight_lookback_ms` 派生值 300k+3600k→3600k、300k only→300k、7200k+3600k→7200k；`--lookback-ms 300000` + `--runtime-lookback-ms 3600000` 下 30 分钟前 qts- 成交被 `SANDBOX_LOOKBACK_NOT_CLEAN` 拒绝；source 中旧 `str(self.args.lookback_ms)` 赋值已不存在 |
| 5 | A1/A4 现状 | **unchanged（advisory，不阻塞）** | A1 默认路径仍为每用户 `%LOCALAPPDATA%\qts\phase6-sandbox-start.lock`；A4 `--guard-probe` 仍在取锁后 `time.sleep(2)`；两者均为已知非阻塞项，当前单用户部署/诊断用途下可接受 |

### B1 修复验证

- **修复语义**：`probe_orphans` 只分类 `observer_process:<pid>`、`dr_tunnel_process:<pid>`、
  `container:qts_p6_sandbox_*`；supervisor 进程（无论启动中/退出中/已释放锁）不再参与孤儿
  判定。真实主机上以 argv 含 `scripts/sandbox_supervisor.py` 的存活 python 进程验证：
  `probe_orphans()` 返回 `[]`（round14 会返回 `supervisor_process:<pid>`）。
- **真实入口矩阵**：0/0.1/0.25/0.5s 各 8 对 + 1.0s 4 对，36/36 `[0,3]`；无 `[3,4]`、
  无 `[0,4]`、无 `[3,3]`、无 exit 4，失败者均为 `STARTUP_GUARD_HELD`。
- **工程师套件**：15 项独立连跑 3 次全部 `15 passed`、exit 0，包含 5 个 offset 参数化。
- **安全性未被削弱**：崩溃后存活 observer 仍使真实入口 exit 4（T06）；容器/DR 隧道分类
  不变（T06/T08）；Docker 不可用仍 exit 5（T07）。
- **A2**：默认 5 分钟 + runtime 1 小时组合现在按 1 小时执行 clean 检查，30 分钟前 qts-
  成交被拒绝（round14 advisory 关闭）。
- **A1/A4**：保持原状；A1 每用户路径在单用户部署下不阻塞，A4 诊断持有 2s 仅影响诊断使用。

### final11 与 manifests

- `engineer-full-suite-final11.xml`：`tests=3471 errors=0 failures=0 skipped=0`，testcase
  3471，failure/error/skipped 节点均 0；stdout `3471 passed, 24 warnings in 250.11s (0:04:10)`；
  exitcode `0`。分类计数：guard 15、tester offline 66、tester postgres 4、window-gates 23、
  observer relogin 1。
- impl `accf76141bf7829048f7359508ee1bded64ed3eb6ca7ae8de33afae05febbed9`（160）；
  final `50692d8bb1541d5a555d503cea8bf7962a74c93c5c96003b8cd50007429218bb`（162）。
- 逐文件 sha256 全部匹配磁盘；impl 不含两个 tester 测试文件，final 含之；文件集与
  `git diff --name-status base + untracked` 的排除后集合完全一致。

### 最终 guard verdict

**pass。** P6-R14-GUARD-CONTENDER-MISCLASSIFIED 已修复并有独立证据；guard 的互斥安全性
（失败者零副作用、崩溃自动释放、崩溃后副作用孤儿 fail-closed、Docker 不可用 fail-closed）
与活性属性（0–1.0s 错开下恰好一个 owner、另一个 exit 3）均成立。round14 的 blocking
finding 关闭；A2 关闭；A1/A4 保留为不影响本轮验收的 advisory。6.1/6.2 仍为 **pending**，
本报告不声明通过；本轮未启动/停止任何窗口。

### round15 证据文件

`artifacts/foundation/phase6/tester-round14/` 下新增/更新：
`guard-verification-round15.json`（17/17）、`race-reproduction-round15-stagger{0,0.1,0.25,0.5,1}.json`
（36 对全 `[0,3]`）、`tester-startup-guard-round15-run{1,2,3}.{xml,stdout.txt,exitcode.txt}`
（3×15/15）、`tester-offline-round15.*`（66/0/0/0）、`preflight-lookback-round15.json`、
`evidence-verification-round15.json`。

## round16 复验：reviewer round11 的 B1/B2 修复（P6-TESTER-ROUND16-GUARD）

fresh reviewer round11 对 guard 交付给出 fail/deny，发现两项：**B1
P6-R11-GUARD-CLEANUP-STDOUT（P1）** 与 **B2 P6-R11-GUARD-STRESS-RAW-EVIDENCE-MISSING**。
工程师已修复；本轮独立只读复验。未启动/停止窗口，未访问网络、Demo 或真实账户，未改
ACL/提权，未改生产源码、宿主脚本、CI 或工程师测试。源码冻结于 `74c95aa7…`/160 与
`0f932c43…`/162。

### 逐项 verdict

| 项 | 要求 | verdict | 独立证据 |
| --- | --- | --- | --- |
| 1 | 重点复现 B1：保留真实 `_stop_stack_best_effort` 与 `run()` finally，只把 `_checked` 桩为返回字符串；正常/中止路径均请求 `docker stop`，空列表不 stop，失败记录且不抛出、不掩盖退出码 | **pass** | `verify_cleanup_round16.py`（real `SandboxSupervisor` 构造 + real `_stop_stack_best_effort` + real `run()`）：7/7 checks。stop argv `["docker","stop","--time","15","cid1","cid2"]`；空列表无 stop；stop 失败记录 `failed` 不抛出；`run()` 正常路径返回 0 且 stop；中止路径返回 1 且 stop（abort+teardown）；清理失败时正常退出码仍为 0；静态确认 `listed.stdout` 不存在且使用 `listed or ""` |
| 2 | 复跑 18 项 guard 套件（多轮）、66 离线、harness；核对 final12 XML/exitcode 与 guard 分类计数 | **pass** | guard 套件独立连跑 3 次均 `18 passed`、exit 0；tester offline `66 passed`、exit 0；harness 17/17（T15/T16 并发 `[0,3]`、T06 observer 孤儿 exit 4、T07 Docker 不可用 fail-closed）；final12 XML `tests=3474 errors=0 failures=0 skipped=0`、exit 0、stdout `3474 passed`，guard 分类 18、tester offline 66、postgres 4、window-gates 23、observer 1 |
| 3 | 逐对核对 `engineer-concurrency-stress-round2.json`：40/40 `[0,3]`、0 失败、输入前后 sha256 与当前磁盘一致 | **pass** | `verify_stress_round16.py`：5 offsets（0/0.1/0.25/0.5/1.0）×8=40 对；40/40 `codes_sorted=[0,3]`（37 对 p1 胜、3 对 p2 胜）；`failed_pairs=0`；无 exit 4、无 `STARTUP_ORPHAN_DETECTED`；每对 winner stdout 含 `"guard": "acquired"`、loser 含 `STARTUP_GUARD_HELD`；40 个 p1/p2 PID 各自唯一；before/after/current sha256 三者一致（supervisor `ea0ab9fd…`、guard `50e680f2…`）；`problems=[]`。测试者另以独立脚本跑 8×5=40 对真实入口竞争，全部 `[0,3]`、无 exit 4 |
| 4 | 独立复算 manifest 与三个 host 文件 sha256 | **pass** | impl 160/`74c95aa7…`、final 162/`0f932c43…`，`digest_match=true`、`mismatch=[]`、`null_but_exists=[]`，文件集与 git 一致；supervisor `ea0ab9fd…`、guard `50e680f2…`、guard test `a4d5c555…` 全部匹配 |
| 5 | 更新报告、标记 B1/B2 fixed_verified、6.1/6.2 pending、报告 SHA | **pass** | 本段与顶部 `source_digest` 已更新；B1/B2 标记 fixed_verified；6.1/6.2 仍 pending；报告 SHA 见最终回复 |

### B1（P6-R11-GUARD-CLEANUP-STDOUT）修复验证

- **原缺陷**：`_stop_stack_best_effort` 把 `_checked()` 的 `str` 返回值赋给 `listed` 后访问
  `listed.stdout`，清理路径必抛 `AttributeError`，`docker stop` 从不执行；异常被宽泛捕获
  记为 failed，supervisor 仍以 0 退出。
- **修复**：直接使用 `_checked()` 返回的字符串（`listed or ""`），`docker stop` 可达。
- **独立复现（不是整体替身）**：用真实 `SandboxSupervisor` 构造函数创建对象；保留真实
  `_stop_stack_best_effort` 与真实 `run()`；仅桩 `_checked`（返回字符串/在 stop 时抛错）、
  `_run_inner`（正常/中止分支）以及中止分支的 `_summarize`（与清理无关）。
  - 正常结束：`run()` finally 调用真实清理，`docker stop --time 15 cid1 cid2` 被调用，
    journal `status=stopped, containers=2`，`run()` 返回 0。
  - 中止路径：`_run_inner` 抛异常，`run()` 返回 1（不掩盖 abort），finally 仍调用真实清理，
    journal 含 `abort` 与 `teardown stopped`。
  - 空列表：只调用 `docker ps`，不调用 `docker stop`，journal 无 teardown。
  - stop 失败：journal 记录 `failed` 与 `COMMAND_FAILED`，不抛出；正常路径退出码仍为 0。
- 工程师新增 3 项清理回归测试（保留真实清理函数、只桩 `_checked`）随 18 项套件 3×18/18
  通过；其测试仅直接调用清理函数，本轮额外覆盖了 `run()` 的正常/中止 finally 路径。

### B2（P6-R11-GUARD-STRESS-RAW-EVIDENCE-MISSING）修复验证

- `engineer-concurrency-stress-round2.json` 是逐对原始证据（非文字声明）：每对含
  `p1_pid/p2_pid`、`p1_exit/p2_exit`、`codes_sorted`、`failed`、双方 stdout/stderr 头部；
  顶层含 5 个 offset、每档 8 对、运行前后输入 sha256。
- 逐对核对结果：40/40 `[0,3]`、0 失败、无 exit 4、无 ORPHAN；stdout 与退出码一致；
  before/after/current 三个 sha256 完全一致；PID 各自唯一；`problems=[]`。
- 测试者独立复跑 40 对（0/0.1/0.25/0.5/1.0s 各 8 对）全部 `[0,3]`、无 exit 4、
  `pairs_where_orphan_finding_is_the_other_contender=[]`。B2 关闭。

### final12 与 manifests

- `engineer-full-suite-final12.xml`：3474 testcase、`errors=0 failures=0 skipped=0`，
  exit 0；stdout `3474 passed, 24 warnings in 301.56s (0:05:01)`；guard 18、tester offline
  66、postgres 4、window-gates 23、observer 1。
- impl `74c95aa77039a6b7cc2a82cd7622be7ae63997c3620bdd33c5696a533f94e879`（160）；
  final `0f932c430e65d8d83c1c1e0154778d2babda2d7ebdaac80c5e3ea652bebd25a0`（162）。
- 逐文件 sha256 全部匹配；`mismatch=[]`、`null_but_exists=[]`；文件集与 git 变更集一致。

### 最终 guard verdict

**pass。** reviewer round11 的 B1/B2 均已修复并有独立、保留真实清理函数的证据；guard 的
互斥安全性/活性、崩溃孤儿 fail-closed、Docker 不可用 fail-closed、清理路径与真实退出码
保持均成立。round14/round15 的 B1 与 A2 仍为 fixed_verified；A1/A4 保持非阻塞 advisory。
**无 open blocking findings。** 6.1/6.2 仍为 **pending**，本报告不声明通过；本轮未启动/
停止任何窗口。

### round16 证据文件

`artifacts/foundation/phase6/tester-round14/` 下新增/更新：
`cleanup-verification-round16.json`（B1 7 checks）、`stress-validation-round16.json`（B2 逐对）、
`guard-verification-round16.json`（harness 17/17）、`race-reproduction-round16-stagger{0,0.1,0.25,0.5,1}.json`
（独立 40 对全 `[0,3]`）、`tester-startup-guard-round16-run{1,2,3}.*`（3×18/18）、
`tester-offline-round16.*`（66/0/0/0）、`evidence-verification-round16.json`。

## changed_files（tester 全部改动）

| 文件 | SHA-256（冻结时实测，工程师计算） |
| --- | --- |
| `trader/tests/test_phase6_independent_offline.py`（新增，最终 66 用例；round14–15 未改动） | `eb643f0a284a1149329d2bb58a39ab3205d0c9be770fa37c2339247c5777eb51` |
| `trader/tests/test_phase6_independent_postgres.py`（新增；round14–15 未改动） | `0f7e030f5cb66f7ad8e0c658617c9194983522c14247b59a94004380e3995e1e` |
| `.github/workflows/ci-gate.yml`（仅追加两个测试文件条目） | `fe91e6db30a7f02bf3a19807ffb91a43e90bf63ec44a9bfa3216cf4b05db52b4` |
| `artifacts/foundation/phase6/tester-execution-request-round4.json` | `9400fbd42dba2bec498af3ebd0b75f02f75ac149b98b6f9da9a81ecf1cccc0c2` |
| `artifacts/foundation/phase6/tester-execution-request-round5.json` / `round6.json` / `round7.json` / `round8.json` / `round10.json` | `31d7e408…` / `29c4c12d…` / 其余见目录（round10 待冻结时计算） |
| `artifacts/foundation/phase6/tester-execution-request-round3.json`、`tester-*` 证据文件 | 请求/证据，见目录 |
| `artifacts/foundation/phase6/tester-round14/`（新增：round14–16 guard 验证脚本与证据，共 73 文件） | round16：`verify_cleanup_round16.py` `04416b7f…`、`cleanup-verification-round16.json` `32cc48dd…`、`verify_stress_round16.py` `cc77cb0f…`、`stress-validation-round16.json` `8b5a6695…`、`guard-verification-round16.json` `8521f577…`、`race_reproduction.py` `82567f7e…`、`run_guard_verification.py` `acfea5be…`、`verify_evidence.py` `aca5fbc0…`、`race-reproduction-round16-stagger{0,0.1,0.25,0.5,1}.json`、`tester-startup-guard-round16-run{1,2,3}.*`、`tester-offline-round16.*`、`evidence-verification-round16.json`。round15/14 历史：`guard-verification-round15/14.json`、`race-reproduction-round15/14*.json`、`verify_observer_marker.py`、`engineer-concurrency-*`；完整清单与逐文件哈希见目录 |
| 本报告 `docs/PHASE6_INDEPENDENT_TEST_REPORT.md` | 待工程师在本次定稿后计算并回填；按约定测试者不自算自身报告哈希（round16 测试者具备 shell，其余哈希均本地复算；当前内容哈希见最终回复） |

round14–16 未修改生产代码、宿主脚本、CI、工程师测试、既有测试断言、业务契约或全局 fixture；
两个 tester 测试文件保持冻结哈希 `eb643f0a…`/`0f7e030f…`。

## blocking_findings

fresh reviewer round11 的 B1/B2 已在 round16 修复并复验通过（**关闭**）：

- **P6-R11-GUARD-CLEANUP-STDOUT（P1）**：`_stop_stack_best_effort` 读取 `_checked()` 字符串
  返回值的 `.stdout`，清理路径必抛 AttributeError、`docker stop` 从不执行，supervisor 仍
  以 0 退出。round16 修复后，测试者以真实 `_stop_stack_best_effort` + 真实 `run()` finally
  （只桩 `_checked` 等）验证正常/中止路径均到达 `docker stop`、空列表不 stop、失败记录且
  不掩盖退出码；18 项 guard 套件 3×18/18。
- **P6-R11-GUARD-STRESS-RAW-EVIDENCE-MISSING**：工程师提供逐对原始压力证据
  `engineer-concurrency-stress-round2.json`；测试者逐对核对 40/40 `[0,3]`、0 失败、无
  exit 4、输入前后 sha256 与当前磁盘一致，并独立复跑 40 对全部 `[0,3]`。

round14 的 blocking finding 已在 round15 修复并复验通过（**关闭**）：

- **P6-R14-GUARD-CONTENDER-MISCLASSIFIED**：启动 guard 的孤儿探测把仍在启动/退出的并发
  竞争者误判为存活 `supervisor_process`，使先持锁的 owner 以 `STARTUP_ORPHAN_DETECTED(4)`
  退出。round14 真实入口两进程错开 0.1s 时 8 对中 7 对为 `[3,4]`（零 owner）、错开
  0.25s 时 14 对中 12 对 `[0,4]`、2 对 `[3,4]`（0 对 `[0,3]`，owner 反转或两者都失败，
  findings 均指向另一竞争者）；工程师冻结的 0 错开并发用例累计 4/23 次失败为 `[3,4]`。
  round15 修复为“OS 锁是 supervisor 归属唯一权威，不再扫描 supervisor 进程，孤儿只按
  observer/DR 隧道/容器等副作用幸存者判断”，独立复验 0/0.1/0.25/0.5/1.0s 共 36 对全部
  `[0,3]`、无 exit 4；详见 body 的 round14/round15 段。

round5 两个 finding 与 round6 一个 finding 均已由工程师修复，并由 tester 独立用例在 run11
实际通过，本轮关闭：

- **P6-R5-METRICS-EVIDENCE-GAP**：旧 `_summarize` 把无 metrics 体的采样视为「无未决、无重复
  发送」，coverage 证明具有粘性，导致后半个窗口无 metrics 证据仍可能 `passed=true`。修复后
  新增 `metrics_samples_present` / `metrics_gap_within_bound` / `coverage_fresh_at_end`，
  缺证据采样不清空 unresolved，末端必须有证据；tester 的 24h 红/绿用例在 run11 通过。
- **P6-R5-OBSERVER-SESSION-RELOGIN**：12h control 会话以 401 响应过期，旧 observer 只在
  异常时清 token，导致 `9f6dbb22` 在 01:53:28Z 后 19 个采样持续 metrics/risk 401。修复为
  响应路径清 token 并在同一样本重登；tester 的 metrics/risk 两分支独立用例在 run11 通过，
  attempts 断言 `[False, True]` 仍成立。
- **P6-R6-COVERAGE-FRESH-MARGIN**：旧固定 300s 上界低于 reconcile 300s + 采样 slack，
  健康窗口有 3–5% 误杀概率；修复为 `max(300_000, 2*300_000 + 180_000)=780_000ms` 并要求
  `metrics.coverage.recorded_at_ms` 存在。tester 的相位/边界/missing 用例在 run11 通过；
  `coverage_fresh_limit_ms=780_000`、780_001ms 失败、缺失 fail-closed 均已锁定。

## limitations

1. round1–round13 的测试者环境无 shell（sandbox ACL 拒绝），当时命令由工程师代跑；round14–16
   的测试者会话具备 shell，全部命令由测试者独立执行，digest/SHA-256 均由测试者本地复算
   （见 round14/round15 段）。历史轮次仍保留“工程师代跑”的归属描述。
2. PG 用例运行在工程师提供的专用库 `qts_p6_tester_d603ce3e` 上，由用例自建 `qts_p6ind_<hex>`
   schema 并在退出时 DROP，未触碰 `/trading` 业务库；隔离依赖 schema，而非独立实例。
3. supervisor `_summarize` 的窗口期间追加/调整判据均为工程师声明并实现的行为，与测试简报的
   扁平「连击 ≤3」表述不一致：unresolved 判据最终为时间制（reconciliation/balance 类 30 分钟、
   其他 2 分钟，`unresolved_max_duration_ms` 由 `unresolved_entries[].at_ms` 计算；
   `UNKNOWN_COMMAND` 归入 30 分钟对账类，tester 2 分钟类代表码用 `BOOK_ATTRIBUTION_UNKNOWN`），
   reason 判据为 duration-based（网络类 30min、其他 2min、结束必须清空），另有
   `samples_cover_window`（≥90% 密度）与 `sample_gap_within_bound`（≤180s）；DR RPO 判据在
   父级升级后为窗口级（head ≤300s 覆盖起点、内部 gap ≤300s、tail ≤300s 覆盖终点，毫秒精确，
   窗外备份不补历史空窗）；`coverage_fresh_at_end` 要求 `metrics.coverage.recorded_at_ms` 存在，
   上界为 `max(300_000, 2*reconcile_seconds*1000 + sample_gap_limit_ms)`（默认 780_000ms）；
   已按实现验证并在报告披露。
4. black：round11 由工程师复核为 `2 files unchanged`；round14–16 测试者本地执行
   `python -m black --check --diff --line-length 100 trader/tests/test_phase6_independent_offline.py trader/tests/test_phase6_independent_postgres.py`
   结果为 `2 files would be left unchanged`、exit 0（历史轮次无本地 black 产物）。
5. C4b final12 的 `3474 passed / 0 failed / 0 skipped / 0 error`、exit=0 取自 stdout 末行
   （`3474 passed, 24 warnings in 301.56s`），并以
   `engineer-full-suite-final12.xml` 的 `tests=3474 errors=0 failures=0 skipped=0` 与
   `.exitcode.txt` 的 `exit=0` 交叉核对（round16 测试者已独立读取三个产物）。final9
   （3456）、final10（3466）、final11（3471）及更早仅作历史；历史同名 3454 用例 final11
   的 1 个 setup-only 环境抖动 error（`TestFakeWebSocket::test_pong_triggers_waiter`，
   单独重跑 1 passed）仅作历史。round14 的并发用例曾累计 4/23 次以 `[3,4]` 失败（B1）；
   round15 修复后 guard 套件 15/15×3、36/36 对 `[0,3]`，round16 再以 18 项套件 3×18/18、
   40/40 对锁定；reviewer round11 的 B1 清理路径与 B2 压力证据亦在 round16 关闭。
6. 24 小时窗口的判据（时长、采样密度与间隔、duration-based reason bounds、时间制 unresolved
   界、coverage/execution 单调、fail-closed、窗口级 DR RPO≤300s）已用合成窗口验证；真实窗口均未
   走完（见 unexecuted_checks），本报告不据此声明 6.1/6.2 通过。
7. `_clean_window` 原先只写窗口开头 4 个备份（T0..T0+540_000），在窗口级 RPO 判据下不足以
   证明窗口尾部新鲜度；已改为 `_window_backups` 全窗口备份（head `[T0-120_000]` + 每 180s
   至 `T0+DAY_MS`，head age 120_000ms/gap 180_000ms/tail age 1_000ms 均手算）。「备份日志只有
   开头一段」的失败反例显式保留在 `test_stale_or_missing_dr_evidence_fails_the_window`
   （head-only：head 覆盖/tail 未覆盖 → `dr_backups_fresh=False`、`passed=False`），防止判据
   退化为前导覆盖时回归不被发现。
8. 早期独立 61 用例中的 `test_observation_retries_auth_failure_without_leaking_the_secret` 只覆盖
   **登录接口**返回 401（异常路径：清 token、重试、不泄露口令），未覆盖 12h 会话到期后
   `/v1/trading/metrics` 以 401 **响应**返回的路径，因此窗口 `9f6dbb22` 的 observer 证据缺失
   未被测试提前拦截；round10 已补 metrics/risk 两分支独立用例，run11 66 用例全部通过。
   该缺口不再存在，本条保留为历史说明。
9. 窗口 `9f6dbb22` 的 `window-summary-engineer-posthoc.json` 是工程师后验汇总，不是原始
   `window.json`；它显示旧门禁 `unresolved_cleared_at_end=true` 而最后 19 个样本
   metrics/risk 均为 401，这正是 P6-R5-METRICS-EVIDENCE-GAP 的现场证据。报告不把该汇总
   当作修复后的判据验证。
10. round14–16 的容器孤儿分类用注入的 container_lister 验证（`qts_p6_sandbox_*` 命中、
    `qts-postgres` 排除），未真的创建容器；Docker 不可用同样用注入异常验证，未停止 Docker
    守护进程。真实主机检查使用当前实际进程表（含真实 WorkBuddy 9119 隧道 PID 32536、以及
    round15 刻意启动的 argv 含 `scripts/sandbox_supervisor.py` 的存活非 owner 进程，均未被
    误报）和实际 `docker ps`（仅 `qts-postgres`），未产生容器或网络副作用。guard 竞态为
    时序相关：round14 用重复执行与 0/0.1/0.25/0.5/1.0/2.5s 错开量化并发现 B1；round15
    在 0/0.1/0.25/0.5s 各 8 对 + 1.0s 4 对（共 36 对）上全部 `[0,3]`；round16 再以
    0/0.1/0.25/0.5/1.0s 各 8 对（共 40 对）复核全部 `[0,3]`。round16 B1 复现保留真实
    `_stop_stack_best_effort` 与真实 `run()`，仅桩 `_checked`（返回字符串/抛错）、
    `_run_inner` 与中止分支的 `_summarize`；正常/中止路径均到达 `docker stop`。B2 为
    工程师逐对原始证据，测试者逐对核对并独立复跑 40 对。未启动真实窗口，因此“正常窗口
    结束后 observer/隧道终止与下一次启动的间隔”未在真实窗口上演练；该路径由 `run()` 的
    finally 终止 observer/tunnel、`_stop_stack_best_effort` 停容器后释放锁的顺序覆盖，
    残留进程仍会被下一次探测 fail-closed。详见
    `artifacts/foundation/phase6/tester-round14/`。

## unexecuted_checks

1. **24h 持续沙箱窗口**：已启动窗口均 aborted、不作验收——`e072a35d`（审核 round1 判据
   修复前）、`f2c526af`（09:41Z 真实 Demo 中断 + 旧判据误分类）、`a3204219`（P6-R3：
   恢复 reconcile 的 5 分钟 lookback 恰好漏掉 11:16:30Z 成交，导致永久
   `UNBOOKED_EXECUTIONS`/`BALANCE_MISMATCH`，账户 blocked 后终止归档）。11:59 的一次启动尝试被
   preflight 以 `SANDBOX_LOOKBACK_NOT_CLEAN` 正确拒绝（11:53:25Z 有一笔真实成交，距启动不足
   1 小时，该窗口归档）——门禁按设计拦截。窗口 `20261105T045604Z-b4ee4b6d`（12:56 启动）在
   6.95h 时因外部链路中断终止归档 aborted：10:15–11:22Z 本机代理节点不可达（67 分钟），DR
   SSH/S3 成功备份最大间隔 1496s > 300s RPO；后验汇总（`windows-aborted/20261105T045604Z-b4ee4b6d/window-summary-engineer-posthoc.json`）显示覆盖证明全程成立、无未决累积、无重复发送、
   成交单调，仅 `dr_backups_fresh` 与 `no_persistent_reasons`/`reasons_cleared_at_end` 因该外部
   中断失败。据此已将容器/preflight 出口改为直连并把 DR/恢复类 reason 归入 30 分钟类
   （`dr_backups_fresh` 判据未放宽）。父级升级后窗口级 RPO、S3 幂等/tunnel probe、
   RECOVERY_GENERATION_OR_OVERFLOW 30 分钟类与 DR_BACKUP_STALE 2 分钟分类均有独立用例锁定
   （offline run11 66 用例 + 工程师 window-gates 21 用例 + observer 1 用例；offline run11 全绿，
   final12 全量 3454/0/0/0 全绿）。fresh reviewer
   round 4 已通过（overall=pass_for_parent_findings_fix_review）；重启窗口 d2b0356f 在首个 DR
   cycle 前因 host supervisor `NameError: tempfile`（漏 import）fail-closed abort（34s、
   dr.cycles=0，归档 not-acceptance，启动期 DR 门禁按设计拦截），修复为单行 `import tempfile`
   （语义零变化）后重算 digest 并重启新窗口（1h clean 已满足）。随后窗口
   `20261105T135205Z-9f6dbb22`（duration 43_747s=12.15h，样本 1456、gap 上限 30.6s、
   DR 判据成立、旧 coverage 判据成立（新 metrics 证据门禁下会失败）、无重复发送、成交计数单调）在 control 会话 12h 生命周期到期处暴露
   observer 缺陷：过期会话以 401 响应而非异常返回，观察器未清 token。原始 JSONL 的首个 401
   样本为 2026-11-06T01:53:28Z（seq 1438），其后到 02:02:29Z 连续 19 个样本
   `metrics_status_code=401`/`risk_status_code=401`，metrics 证据链断裂。该窗口由工程师主动
   停止并归档 not-acceptance（`aborted_reason=
   ENGINEER_ABORTED_OBSERVER_SESSION_EXPIRY_MISSING_METRICS_EVIDENCE`，`passed=false`，仅
   `duration_at_least_24h`、`no_supervisor_abort`（工程师主动停止）与
   `reasons_cleared_at_end` 未过）。旧后验汇总仍给出 `unresolved_cleared_at_end=true`，恰好
   复现 P6-R5-METRICS-EVIDENCE-GAP：无 metrics 的采样被当成「无未决」；该汇总不是验收证据。
   修复为 metrics/risk 收到 401/403 时清 csrf 并在同一采样内重新登录重试，新增
   `trader/tests/test_phase6_observer_relogin.py` 与 tester round10 的两个独立分支用例锁定。
   随后重算最终指纹（`cba4983f…`/157、`f7f999e0…`/159）。final12 全量 `3454 passed /
   0 failed / 0 skipped / 0 error`、exit=0（final11 曾有 1 个 setup-only 环境抖动 error，
   `TestFakeWebSocket::test_pong_triggers_waiter` 单独重跑 1 passed；final12 现为历史）。round-7 复审通过后启动的窗口
   `20261107T002812Z-f0a611de` 运行 26_808s，私有流在代理路径上中断约 48 分钟
   （PRIVATE_STREAM_UNHEALTHY 未解释时长 2_916_539ms，超 30 分钟界；2026-11-07T07:34Z
   实测代理路径 demo WS 完全不可达、容器直连稳定 300s），由工程师停止并归档
   not-acceptance（aborted_reason=ENGINEER_ABORTED_EXTERNAL_DEMO_WS_OUTAGE_VIA_PROXY_PATH，
   passed=false，dr tail gap 494s/665s 超 RPO）。据此 supervisor 出口拆分为 preflight
   显式代理（--preflight-proxy 继承 .env）/ 容器直连（--container-proxy 默认空），
   start_window.ps1 移除容器代理参数，重算指纹 89deead0…/157、b616b55b…/159（现为历史），
   final8 全量 3454/0/0/0、exit 0（现为历史）。随后窗口 `20261107T083226Z-50077d3c`
   走满 24h（observer monotonic 86400s；旧 supervisor 按 wall marker 算 86398s，NTP slew
   短 2s），官方 `window.json` passed=false 仅三项：`duration_at_least_24h=false`（wall
   marker 短 2s）、`dr_backups_fresh=false`（708 次成功备份中一次成功间隔 379.8s > 300s
   RPO，备份机短暂掉线）、`no_persistent_reasons=false`（UNBOOKED_EXECUTIONS 作为健康
   reason 持续 301s 落在 2 分钟类，实为对账事实）；其余判据（采样密度/间隔、metrics
   证据密度/间隔/末端覆盖、unresolved_cleared_at_end、无重复发送、execution 单调、
   无容器重启/abort/error）全部为 true。该窗口不是验收证据（见 6.1/6.2 审计节）。
2. **最终窗口审计阻塞**：窗口 `20261107T083226Z-50077d3c` 已完成但官方 passed=false，
   不是验收证据；对应修复为 observer marker monotonic 起点/elapsed、supervisor monotonic
   时长计算（wall 仅旧 marker 回退）与对账事实 reason 归入 30 分钟类（与 unresolved 侧
   对称），重算最终指纹 b7b0b4ca…/157、350d815e…/159，final9 全量 3456/0/0/0、exit 0。
   下一窗口将在 guard finding 已修复/复验（round15）与 1h clean（2026-11-08T09:33:04Z）后启动；
   窗口完成前无法执行 step 5 要求的
   最终窗口 6.1/6.2 只读审计；不声明通过。已完成的只是历史窗口 `9f6dbb22` 的失败复核、
   50077d3c 的只读复核与判据合成验证。
3. 修复 PG fixture 后的整仓 full suite 复跑已由工程师完成（C4b final9：3456/0/0/0，exit 0；
   final8 及更早仅历史）。
   final11 曾有 1 个 setup-only 环境抖动 error（`TestFakeWebSocket::test_pong_triggers_waiter`
   setup 的 `WinError 10055`；单独重跑 1 passed），仅作历史。
4. `POSTGRES_CONNECTION_STRING` 未设置时 PG 用例按设计 `pytest.skip`（外部依赖门控）——本次
   实跑已连接真实 PG，未发生 skip，但该门控行为本身在 CI 无 DSN 环境下会表现为 4 skipped。
5. 真实 Binance sandbox 的 preflight 未用真实凭证执行（测试使用注入的 fake broker 模块）。

## 归属与冻结

- 测试代码 `authored_by: tester`；round1–13 命令执行 `executed_by: engineer`，round14–15 由
  独立测试员在具备 shell 的会话中执行；分析 `analyzed_by: tester`。
- 实现冻结：`implementation_digest=74c95aa77039a6b7cc2a82cd7622be7ae63997c3620bdd33c5696a533f94e879`
  （160 文件）与 `complete_phase_digest=0f932c430e65d8d83c1c1e0154778d2babda2d7ebdaac80c5e3ea652bebd25a0`
  （162 文件，已包含 tester offline `eb643f0a…`/66 与 postgres `0f7e030f…`）；这是审核
  round1 修复、supervisor samples/gap 与 duration-based reason bounds（含账号/估值类 reason 归入
  30 分钟网络恢复类）、myTrades lookback 过滤、DR feeder 提前启动、P6-R3-RECONCILE-LOOKBACK
  三段 lookback 修正、时间制 unresolved 判据（reconciliation 类 30 分钟/其他 2 分钟，
  `UNKNOWN_COMMAND` 归入 30 分钟类）、容器/preflight 直连出口与代理路径隔离、窗口级 DR RPO
  判据（首/尾/内部/毫秒精确）、S3 `BucketAlreadyExists` 幂等（验证过的任务目标 + 可达性
  readback）与 `_ensure_tunnel` 应用级 probe、`RECOVERY_GENERATION_OR_OVERFLOW` 归入 30 分钟
  恢复类与 `DR_BACKUP_STALE` 严格 2 分钟、DR cadence 120s + 启动前 3 连续 verified off-host
  cycle 门禁及 runbook/architecture/pairs 更新、host supervisor tempfile import 修复、observer
  会话过期（401）重登修复、supervisor metrics 证据门禁修复、coverage 780s margin 修复、
  egress 拆分（preflight 显式代理 --preflight-proxy 继承 .env / 容器直连
  --container-proxy 默认空）、observer marker monotonic 起点/elapsed 字段与 supervisor
  monotonic 时长计算（wall 仅旧 marker 回退）、对账事实 reason（UNBOOKED_EXECUTIONS 等）
  归入 30 分钟类（与 unresolved 侧对称，`dr_backups_fresh` 权威判据未放宽）及
  13.17/runbook 的 egress、preflight clean 1h、对账 reason 类校正、启动互斥 guard
  （`trader/ops/startup_guard.py` + supervisor main 获取顺序/`--guard-probe`/孤儿检测；
  round14 发现并发活性缺陷，round15 修复为“OS 锁是 supervisor 唯一权威、孤儿只按
  observer/DR 隧道/容器副作用幸存者判定”）与 A2 `_preflight_lookback_ms`（preflight
  clean = max(lookback, runtime lookback)）、reviewer round11 B1 清理路径修复
  （`_stop_stack_best_effort` 直接使用 `_checked()` 的字符串，`docker stop` 可达）与 B2
  逐对原始压力证据之后的最终清单。历史
  offline `68e1c3c1…`（61 用例）仅为 round8 历史值。
- 本报告的 source_digest 元数据（`74c95aa7…`/160、`0f932c43…`/162，round16 独立复算，
  为 reviewer B1/B2 修复后的最终对；`accf7614…`/160、`50692d8b…`/162 为 round15
  B1/A2 修复时点历史；`f6498565…`/160、`8b2a374d…`/162 为 round14 guard 初版历史；
  `b7b0b4ca…`/157、`350d815e…`/159 为 monotonic/对账 reason 类修复时点历史；
  egress 拆分时点值 89deead0…/b616b55b…、历史 final12 时点值 cba4983f…/f7f999e0…、
  N1–N5 docs-only 后的 56a7cf79…/9380eb14… 均为历史，见 C5 行与 unexecuted_checks
  历史）、round16 独立离线（66/0/0/0、exit 0）与 black（2 files unchanged）、guard
  套件 3×18/18、final12（3474/0/0/0、exit 0；final9/final10/final11 及更早仅作历史；
  历史同名 3454 用例 final11 的 1 个 setup-only 环境抖动 error 仅作历史）、未执行窗口记录
  （`20261105T135205Z-9f6dbb22`、`20261107T002812Z-f0a611de`、
  `20261107T083226Z-50077d3c` 均非验收证据；下一窗口待 1h clean 后启动，guard findings
  已修复，尚未生成审计文件）与本节归属描述为 round16 的最终元数据更新；除本报告与
  `artifacts/foundation/phase6/tester-round14/` 证据外，未改生产代码、宿主脚本、CI、
  工程师测试或两个 tester 测试文件。
- 报告 SHA-256 由工程师在本次文件定稿后计算并回填；round16 由独立测试员在具备 shell 的
  会话中执行全部复算与测试（未改 ACL、未提权、未访问网络/Demo/真实账户），但报告自身
  哈希仍由工程师回填，测试者不自算。
- `workspace_frozen: true`（round16 独立复跑 tester 66 用例、guard 18 例×3、40 对真实
  入口错开竞争、B1 真实清理函数复现、B2 逐对压力证据核对、final12 交叉核对与 manifest
  复算；源码冻结对 `74c95aa7…`/`0f932c43…`；本报告定稿，reviewer round11 B1/B2 已修复
  并复验关闭）。
  当前 `overall` 只对 6.3–6.12 声明 pass；6.1/6.2 仍 pending，不声明通过。

## round17 复验：分段累计、resume/segment、observation-only 与 guard（P6-TESTER-ROUND17-CONTINUATION）

```json
{
  "report_id": "P6-TESTER-ROUND17-CONTINUATION",
  "workflow_version": 3,
  "phase": 6,
  "role": "tester",
  "agent_id": "tester-round17 session (child of engineer 231a51ed-bcba-4dd2-afe6-271e45f72164)",
  "round": 17,
  "base_commit": "c6fe450260eeb59d46c9caee591fb1625510b443",
  "branch": "feat/foundation-refactor",
  "source_digest": {
    "recomputed_implementation_digest": "2d2334f2c10f38d16da7c4b27494a602abbdbcc6d4adcfdff7bfce6e9b5dc0c7",
    "recomputed_implementation_files": 166,
    "recomputed_complete_digest": "bdf448ae308af14de2b90989df442d3a83bc61694e952cecd7afb1324f49ec19",
    "recomputed_complete_files": 168,
    "declared_implementation_digest": "e3237b883eb9ff8b90841fd3677aa8364f8e7c9983e8b027e73b0ca80f0ec2a7",
    "declared_implementation_files": 164,
    "declared_complete_digest": "892788202a3e014216da32b7b4b7f57220bd2ec6888c536d672a2d1b46a18b00",
    "declared_complete_files": 166,
    "declared_manifest_per_file_hashes": "all match; mismatch=[]; null_but_exists=[]; missing=[]",
    "declared_manifest_file_set": "incomplete: missing trader/strategies/fire_test.py and trader/strategies/__init__.py",
    "recomputed_manifests": [
      "artifacts/foundation/phase6/tester-round17/manifest-regen-impl-current.json",
      "artifacts/foundation/phase6/tester-round17/manifest-regen-current.json"
    ]
  },
  "overall": "blocked",
  "overall_note": "functional 6.3-6.12 checks pass and the aggregate arithmetic is reproduced; blocking delivery findings are P6-R17-MANIFEST-INCOMPLETE and P6-R17-SEGMENT-LINEAGE-BASELINE. 6.1/6.2 remain pending and are not claimed.",
  "authored_by": "tester (round17 session)",
  "executed_by": "tester (all commands in this round; no engineer proxy run)",
  "analyzed_by": "tester",
  "workspace_frozen": true
}
```

### 结论摘要

- **算术与功能通过**：独立复跑全量 `3491 passed / 0 failed / 0 errors / 0 skipped`、exit 0；
  聚合脚本逐字段复现 canonical：原窗口 qualified `19,874,125ms`、active `39,598,016ms`、
  excluded `19,723,891ms`、evidence_digest `6498dad2…`；验证段 qualified `872,313ms`；
  累计 `20,746,438ms`、剩余 `65,653,562ms`、`all_runs_valid=true`、
  `dr_internal_breach=false`、`acceptance_satisfied=false`。guard 18 例×3、40/40 压力证据、
  当前代码 6/6 真实入口竞争、observation-only 镜像与行为检查均通过。
- **交付级 blocking**：两份声明 manifest 都漏掉两个实际改动文件
  （`trader/strategies/fire_test.py`、`trader/strategies/__init__.py`），声明的
  `source_digest` 不覆盖完整变更集；验证段/长段的 continuation baseline 被覆盖，
  `segment.json` 的 `db_baseline` 指针与实际 preflight identity 不一致，lineage 不能从
  留档证据复现。因此 `overall=blocked`，不声明本轮可最终交付。
- **6.1/6.2 仍 pending**：累计 20,746,438ms < 86,400,000ms，剩余 65,653,562ms（18h14m）；
  本轮未启动任何窗口/分段/沙箱 compose，不声明 6.1/6.2 通过。

### 本轮独立执行的命令与结果

| # | 命令（DSN 以 `<task-db>` 代替，不写入报告） | exit | tests / failures / errors / skipped | 证据 |
| --- | --- | --- | --- | --- |
| 1 | `POSTGRES_CONNECTION_STRING=<task-db> python -m pytest -o addopts='' -q -p no:cacheprovider trader/tests --junitxml=artifacts/foundation/phase6/tester-round17/full-suite.xml --tb=short`（task DB `qts_p6_suite_948e94bb`） | 0 | 3491 / 0 / 0 / 0（`3491 passed, 24 warnings in 260.98s`） | `tester-round17/full-suite.{xml,stdout.txt,exitcode.txt}`；JUnit timestamp `2026-11-10T14:49:21.450073+08:00` |
| 2 | `python -m pytest -o addopts='' -q -p no:cacheprovider trader/tests/test_phase6_independent_offline.py --junitxml=artifacts/foundation/phase6/tester-round17/tester-offline.xml --tb=short` | 0 | 66 / 0 / 0 / 0 | `tester-offline.xml`；文件 sha256 `eb643f0a…` 未变 |
| 3 | `POSTGRES_CONNECTION_STRING=<task-db> python -m pytest ... trader/tests/test_phase6_independent_postgres.py --junitxml=artifacts/foundation/phase6/tester-round17/tester-postgres.xml --tb=short` | 0 | 4 / 0 / 0 / 0 | `tester-postgres.xml`；文件 sha256 `0f7e030f…` 未变 |
| 4 | `python -m pytest ... trader/tests/test_phase6_startup_guard.py` ×3（每次独立 JUnit） | 0 / 0 / 0 | 18 / 0 / 0 / 0 ×3（28.22s / 25.26s / 27.57s） | `tester-round17/guard-run{1,2,3}.{xml,stdout.txt}` |
| 5 | `python -m pytest ... trader/tests/test_phase6_observation_only_strategy.py trader/tests/test_phase6_segment_aggregate.py` | 0 | 17 / 0 / 0 / 0（5 + 12） | `tester-round17/obs-aggregate.{xml,stdout.txt}` |
| 6 | `python scripts/phase6_segment_aggregate.py --original artifacts/foundation/phase6/windows-aborted/20261109T011441Z-efbba899 --segment artifacts/foundation/phase6/windows/20261110T030232Z-7a911829 --out artifacts/foundation/phase6/tester-round17/aggregate-continuation-recomputed.json` | 0 | n/a | `aggregate-continuation-recomputed.json`、`aggregate-recompute.stdout.txt` |
| 7 | `python -I artifacts/foundation/phase6/tester-round17/verify_manifests.py` + `python -I scripts/phase_manifest.py --base c6fe450…`（impl 与 complete 两次，输出仅到 tester-round17） | 0 | n/a | `manifest-verification.json`、`manifest-regen-impl-current.json`、`manifest-regen-current.json` |
| 8 | `python -I artifacts/foundation/phase6/tester-round17/verify_stress_round17.py`（核对工程师 40 对 + 当前代码 6 对真实入口竞争） | 0 | 40/40 对 + 6/6 对均 `[0,3]` | `stress-verification-round17.json` |
| 9 | `python -I artifacts/foundation/phase6/tester-round17/verify_handoff_round17.py` | 0 | 16 项检查：14 pass、2 fail（lineage） | `handoff-consistency-round17.json` |
| 10 | `docker image inspect qts-runtime:local …`；`docker run --rm -i --entrypoint python qts-runtime:local -`（导入 + 行为检查） | 0 | 见下 | 终端输出（本轮记录在正文） |
| 11 | 阶段 6 文档检查：`test_architecture.py` / P0 五套件 / trading_service+api+runtime_entrypoint+worker_assembly+split_control+phase0 / `observe_trading_runtime.py --help` | 0 / 0 / 0 / 0 | 18 / 99 / 72 / help ok，均 0 failed | `tester-round17/check-{architecture,p0,control}.xml`、`check-observer-help.stdout.txt` |

### 聚合复算（独立运行，未覆盖 canonical）

| 量 | 复算值 | 与 canonical `aggregate-continuation.json` |
| --- | --- | --- |
| original qualified_ms | 19,874,125 | 一致 |
| original active_ms | 39,598,016 | 一致 |
| original excluded_union_ms | 19,723,891 | 一致 |
| original primary buckets | not_ready 9,741,442 + balance_unproven 9,982,449 = 19,723,891 | 一致；求和等于 excluded_union |
| original multi-label overlaps | unresolved 571,249 / dr_stale 120,328 / freshness 1,232,687 / risk_snapshot_stale 9,530,927 | 一致 |
| original evidence_digest | `6498dad2cd7b28e10587a99e3a6c20c6dc16fee4d817e4660abb9ed05122f150` | 一致 |
| validation segment qualified_ms | 872,313 | 一致 |
| validation segment active_ms / excluded | 872,313 / 0 | 一致 |
| validation segment samples / wall_ms | 30 / 872,284（首 2026-11-10T03:03:27.750Z，末 03:18:00.034Z） | 一致 |
| validation segment evidence_digest | `2c3693b749c2a383815d08bb5578ac2ff5ebf4d58d3ff569d17eb80a5c4f4981` | 一致 |
| cumulative qualified_ms / remaining_ms | 20,746,438 / 65,653,562 | 一致 |
| all_runs_valid / dr_internal_breach | true / false | 一致 |
| acceptance_satisfied | false | 一致 |
| cumulative wall / elapsed_with_pauses / pause | 40,469,534 / 93,663,293 / 53,193,759 | 一致 |
| strong invariants（两段） | repeated_sends/execution_regression/ready_unresolved/coverage_proof_missing/supervisor_abort/container_restart 全 false | 一致 |
| segment primary buckets / overlaps | 全 0 / `{}` | 一致 |

补充：验证段 `window.json` 的 `passed=false` **仅**因为 `duration_at_least_24h=false`；
`ready_ratio=1.0`、30/30 ready、`metrics_samples=30`、`sample_gap_max_ms=30172`、
`metrics_gap_max_ms=30167`、`execution_counts` 恒为 65、DR 11 cycles/0 failures、
head 6,944ms、max gap 128,507ms、tail 31,715ms，其余窗口门禁均 true。aggregate 不把
`window.json` 的 passed 当输入；qualified 由 raw observation/DR 复算。

### 6.1 / 6.2

**pending，不声明通过。** 原窗口 canonical `19,874,125ms` + 验证段 `872,313ms` =
`20,746,438ms`，距 86,400,000ms 还差 `65,653,562ms`（18h14m）。原 24h 窗口仍是
`not_acceptance_continuous`（host WiFi 自动切换导致 DR RPO 违约，证据原样保留）。
本轮未启动新的窗口/分段/沙箱 compose，未访问 Demo/真实账户/网络。

### guard（启动互斥）

- 代码核对：`trader/ops/startup_guard.py` 使用 host-level OS 独占锁
  （Windows `msvcrt.locking`，POSIX `flock`），锁身份不含 run ID/入口；supervisor
  `main()` 在任何 Demo/Compose/DR 副作用前 `guard.acquire()`（`scripts/sandbox_supervisor.py:1957`
  附近），失败分别 `STARTUP_GUARD_HELD=3`、`STARTUP_ORPHAN_DETECTED=4`、
  `STARTUP_ORPHAN_CHECK_UNAVAILABLE=5`；孤儿只按 observer/DR strict-key 隧道/运行中的
  `qts_p6_sandbox_*` 容器判定，supervisor 归属由 OS 锁裁决；`--guard-probe` 只取锁/探测后
  退出，无副作用。
- `test_phase6_startup_guard.py`：18/18 ×3 通过，exit 0。
- `engineer-concurrency-stress-round2.json` 逐对核对：5 个 offset ×8 = **40 对**，
  **40/40 `[0,3]`**、0 failed、40 个 owner stdout 含 `"guard": "acquired"`、40 个 loser 含
  `STARTUP_GUARD_HELD`、无 exit 4/ORPHAN、80 个 PID 唯一、输入 before==after；
  guard 文件 sha256 `50e680f2…` 与当前磁盘一致。
  **注意**：该证据记录的 supervisor sha256 为 `ea0ab9fd…`（2026-11-08），当前磁盘为
  `b137c656…`（2026-11-10 11:05 的 resume-baseline/segment-metadata 编辑），故旧 40 对
  不覆盖当前 supervisor；guard 模块本身未变。
- 当前代码小规模复跑（不得启动沙箱/窗口）：使用与工程师脚本相同的真实入口
  `python scripts/sandbox_supervisor.py --guard-probe`，0/0.25/1.0s 三个 offset ×2 =
  **6/6 对 `[0,3]`**、0 failed、输入前后 sha256 不变。未直接运行
  `artifacts/foundation/phase6/run_concurrency_stress.py`，因为该脚本硬编码 40 对并写入
  工程师的 canonical 压力证据路径；为遵守“不得改写工程师证据”，以 tester 自有脚本
  `tester-round17/verify_stress_round17.py` 复用同一入口执行。

### observation-only

- 镜像 `qts-runtime:local` 的实际 Id 为
  `sha256:b4cd10c93e467e88d283a1bd8ed1441b9fc5338d6c953d67fe8dc2e18af6875f`，与 handoff
  的 `image` 字段一致；`docker run --rm -i --entrypoint python qts-runtime:local -` 一次性
  导入检查输出：`OBS_MODULE /app/trader/strategies/observation_only.py`、
  `FIRE_OBS_FLAG True`；行为：`ObservationOnlyStrategy.on_market_data → None`、
  `FireTestStrategy(observation_only=True).on_market_data → None`、默认
  `FireTestStrategy(observation_only=False) → Signal(SignalType.BUY)`。
- 执行容器环境（只读 `docker inspect` 两个键）：
  `QTS_STRATEGY_MODULE=trader.strategies.fire_test`、
  `QTS_STRATEGY_CONFIG={"observation_only": true}`。
- 验证段：preflight 与 post 的 commands/executions 均为 67/65；`window.json`
  `execution_counts` 30 个样本恒 65；`allocations=[]`；无新 PLACE/新订单。

### resume/segment 机制与证据

- 代码（只读）：`--resume-from-run`/`--segment-index`（`scripts/sandbox_supervisor.py:1929-1934`）；
  resume 时 project 由原 run id 派生（`:447-450`），原 host 端口由原容器 PortBindings 读取
  （`:711-723`），secrets 复用 `secrets/phase6_<原 run id>/`（`:763-779`），continuation
  baseline 优先（`:783-794`），DB counts/identity/lease/outbox/command/order/books/own-history/
  dual-snapshot 检查（`:790-916`），lease fencing token 严格递增（`:918-937`），
  `_prepare_resume` 设置 `observation_only=true`（`:725-734`），segment.json 写入
  （`:939-961`），readiness 用 `asyncio.to_thread`（`:1690`）避免阻塞 DR feeder；
  resume 跳过 book allocation（`:1673-1680`）并记录 `resume_allocation_skipped`。
  `_preflight` 在 resume 时仍收集 own_history 供匹配，但跳过
  `SANDBOX_LOOKBACK_NOT_CLEAN` 的新窗口拒绝（`:646-657`）。
- 验证段 `segment.json`：`segment_index=2`、`observation_only=true`、`dr_ready_cycles=3`、
  `lease_before=2`、`lease_after=["trading:binance_spot","f919629deb92:1","3","t"]`、
  `original_evidence_digest=6498dad2…`、`db_identity_hashes` 与 `resume-preflight.json`
  一致（executions `4cacdd5b…`、ledger `6f15c986…`、events `a3446acc…`）。
- `window.json`：project `qts_p6_sandbox_20261109t011441z_efbba899`、ports
  postgres 56430/control 56431/alert 56432、image `b4cd10c9…`、remote
  `alex@192.168.3.4` strict-key、allocations `[]`。docker 只读核对：同名 project、
  三个 volume 保留（`..._qts_pgdata`/`..._qts_state`/`..._alert_journal`）、postgres
  compose config hash `bbdc531c…` 与工作包一致；control/execution 的 config-hash
  因 resume 环境（observation_only 等）分别为 `6991731f…`/`60c7dbfe…`，与工作包记录的
  `e02b25da…`/`e213d578…` 不同（postgres/alert 一致）。
- supervisor journal：`resume_allocation_skipped`、`resume_lease before=2 after=3`、
  teardown stopped、无 abort/restart。
- DB（continuation baseline）：67 commands（2 ALLOCATE + 65 PLACE）、67 CONFIRMED、
  65 executions、199 ledger、319 outbox 全 delivered、65 FILLED、holds 空、
  schema migration version 3、account_version 1942。原始 64 executions/196 ledger/436 events
  全部是新集合的子集；新增仅 queued fill `qts-83facbd2aaa50265fea81071` /
  trade `340241327` 的 1 条 execution 与 3 条 ledger（base/quote/fee），**仅一次、无重复入账**；
  新增 14 条 events 为 3 条该订单事实 + 11 条 `account.reconciled`。
- 失败段未被改写：`595fbb5b` 的 posthoc 记录 observer_started=false、queued fill 明细与
  counts 66/436/64/196/316 → 67/441/65/199/319，无 observation.jsonl；`36448c34` 的
  posthoc 记录 43 samples、7 次 SSH_TUNNEL_NOT_READY、0 新订单。原始窗口 evidence_digest
  仍为 `6498dad2…`。

### handoff / evidence-index 一致性

`tester-round17/handoff-consistency-round17.json` 共 16 项检查：14 pass、2 fail（两项均为
continuation baseline lineage）。要点：

- handoff 的 `source_digest`/`complete_source_digest` 与两份 manifest 一致；
  final15 指针（3491/0/0/0、exit 0、timestamp）一致；cumulative/remaining 与 canonical
  aggregate 一致；report sha256 `c4a782…` 在 round17 编辑前与 handoff/index 一致。
- **fail 1**：验证段 `segment.json.db_baseline` 指向原 `db-baseline.json`
  （66/436/64/196/316），而该段 preflight 为 67/441/65/199/319；当前 continuation baseline
  为 67/450/65/199/319（events identity `895982…`），与验证段 preflight events
  `a3446acc…` 不同。
- **fail 2**：长段 `segment.json.db_baseline` 指向当前 continuation baseline
  （450 events），而长段 preflight 为 445 events（events identity `3b366cf…`）。
- 采集时间线：`db-baseline-continuation-20261110.json` 的 `captured_at_utc` 为
  `2026-11-10T04:19:39Z`，晚于验证段（03:02–03:18Z）与长段（03:57–04:18Z）；capture 脚本
  `capture_continuation_baseline.py` 复用同一路径/同一组 raw 文件，后一次采集覆盖了前一次。
  验证段实际匹配的 441-event 基线与长段匹配的 445-event 基线均未留档。此外，当前 baseline 的
  `delta_from_original` 仍写着 `events: 5`（436→441，对应 595fbb5b 的 queued fill），而
  450−436=**14**；该字段是 capture 脚本中的静态字面量，重采集后未随 counts 更新。
- 另：验证段启动于 11:02:32（本地）/03:02:32Z，host supervisor 文件 mtime 为 11:05（本地）；
  运行进程在编辑前已加载旧模块，故该段 `segment.json` 的 `db_baseline` 字段反映旧行为；
  current supervisor 的 continuation-baseline 优先逻辑只有单测/full suite 覆盖，未被该段
  端到端执行。

### Findings

**Blocking（阻断最终交付，需工程师修复/重生成后再复验）**

1. **P6-R17-MANIFEST-INCOMPLETE** — 两份声明 manifest（impl 164/`e3237b88…`、complete
   166/`89278820…`）漏掉 `trader/strategies/fire_test.py` 与
   `trader/strategies/__init__.py`（相对 base 的 observation-only 实际改动）。
   逐文件 sha256 在声明文件集内全部匹配，`null_but_exists=[]`、`missing=[]`；但用同一
   `scripts/phase_manifest.py` 重算得到 impl **166/`2d2334f2…`**、complete
   **168/`bdf448ae…`**。这违反 G4“完整变更集/指纹”，也使 handoff 的冻结 digest 不代表
   实际代码。要求：重生成两份 manifest、更新 handoff/evidence-index/report 的 digest，
   再交复验。
   证据：`tester-round17/manifest-verification.json`、`manifest-regen-*.json`。
2. **P6-R17-SEGMENT-LINEAGE-BASELINE** — continuation baseline 被后一次采集覆盖，
   当前文件（67/450/65/199/319，events `895982…`）既不匹配验证段 preflight
   （67/441/65/199/319，`a3446acc…`），也不匹配长段 preflight（445，`3b366cf…`）；
   验证段 `segment.json.db_baseline` 仍指向原 `db-baseline.json`（66/436），长段
   `segment.json.db_baseline` 指向 450-event 文件。RFC §7 要求的“前段 manifest/终止 DB
   identity 链接”无法仅凭留档 baseline 复现。该问题不改变 raw observation 的
   `872,313ms` 复算，但阻断 lineage 完整性的验收。要求：为每个段保留采集时的 baseline
   快照（含 sha256）并在 `segment.json` 记录实际使用的 baseline hash；或在文档中明确
   当前 baseline 是“下一段”锚点且历史段以各自 `resume-preflight.json` identity 为准，
   并补一段不可覆盖的 lineage 证据。同时修正当前 baseline 的 `delta_from_original`
   （写 `events: 5`，实际 450−436=14）。
   证据：`handoff-consistency-round17.json` 的两个 fail、各段 `resume-preflight.json`、
   `capture_continuation_baseline.py`。

**Non-blocking（需在最终 handoff 前修正/披露）**

3. **P6-R17-EVIDENCE-INDEX-STALE** — `engineer-evidence-index.json` 的
   `segment_continuation` 仍为旧 qualified 口径：`original_qualified_seconds=32622.1`、
   `remaining_qualified_seconds=53777.9`、`status=rfc_pending_independent_tester_and_fresh_reviewer`；
   canonical 为 19,874.125s / 65,653.562s，status 应为已实现/验证段失败待恢复。
4. **P6-R17-HANDOFF-STALE-HASHES** — handoff 的 `checks.resume_wp2.supervisor_sha256`
   与 `guard.supervisor_sha256` 为 `41fc283e…`，evidence-index 的 supervisor 为
   `ea0ab9fd…`，当前 manifest/磁盘为 `b137c656…`；handoff/evidence-index 的
   `frozen_image` 为历史 `aea741d2…`，当前 `image` 为 `b4cd10c9…`。字段含义需分列
   （历史冻结 vs 当前镜像），避免误读。
5. **P6-R17-COMPOSE-HASH-MISSING** — RFC §7 要求 segment manifest/aggregate 带
   source/image fingerprint 与 compose config hash；`segment.json`/`window.json` 未记录
   compose hash。独立只读 docker labels 显示 postgres `bbdc531c…`/alert `fc7fa351…`
   与工作包一致，control `6991731f…`/execution `60c7dbfe…` 因 resume 环境不同；
   应把四个服务的实际 hash 与“允许 control/execution 因 observation_only 环境变化”的
   说明写入 segment 证据。
6. **P6-R17-WP3-DOCS-MISSING** — `docs/INTERFACE_CONTRACTS.md` 13.17、
   `docs/PROJECT_ARCHITECTURE.md` 8.5、`docs/UNIFIED_TRADING_RUNBOOK.md` §5.2 仍只描述
   单次连续 ≥24h 窗口；未记录 `--resume-from-run`/`--segment-index`、continuation
   baseline/lease/observation-only、qualified-v3 分段累计口径。WP3 的
   acceptance/interface/architecture/runbook 交付未落地，需补文档（不影响本轮算术）。
7. **P6-R17-LONG-SEGMENT-TAIL-CLASSIFICATION** — 工程师将 `36448c34` 记为“内部 DR 违约、
   零计入”；独立复算显示 43 samples、active 1,263,890ms、qualified **1,143,515ms**、
   excluded 120,375ms，4 个 dr_stale 区间全部在尾部（seq 39–43，最后成功 backup
   `2026-11-10T04:11:57.675Z`，最后样本 `04:18:50.472Z`，7 次 SSH_TUNNEL_NOT_READY），
   `dr_internal_breach=false`、`run_valid=true`。按 RFC §5 的 tail breach 规则，此前合格
   区间可计；零计入是更保守的选择，但与 “internal breach” 的描述不符。若按 RFC tail 规则
   计入，累计应为 21,889,953ms（仍 <24h）；当前 canonical 不包含该段，故不影响
   20,746,438ms，但需在报告中明确该保守处置。
8. **P6-R17-STRESS-SUPERVISOR-STALE** — 40 对压力证据的 supervisor 输入 hash 为
   `ea0ab9fd…`，不覆盖当前 `b137c656…`；guard 模块未变，本轮 6 对当前代码复跑通过，
   故不构成安全失败，但旧证据的“输入前后 hash 与当前磁盘一致”声明已过期。
9. **P6-R17-VALIDATION-SEGMENT-PRE-EDIT-CODE** — 唯一合格验证段运行于 11:05 编辑前的
   supervisor；当前代码的 resume-baseline 强制与 segment-metadata 修复仅由单测/full suite
   覆盖，未在该段的真实端口/卷/lease 路径上端到端执行。建议下一段用当前冻结代码运行。
10. **P6-R17-595fbb5b-PROFILE（advisory）** — `595fbb5b` 是预实现失败段，既有
    `window.json` + posthoc、无 `segment.json`，不符合 RFC §11 的异常段
    `segment.json`+posthoc profile；它不是 aggregate 输入且已披露，仅作为历史证据保留。

### 未执行项与限制

- 未启动任何窗口/分段/沙箱 compose（遵守工作单）；未操作真实账户/真实资金；未改主机
  WiFi/路由/代理/防火墙；未 commit/push/merge/deploy。
- 未直接运行 `artifacts/foundation/phase6/run_concurrency_stress.py`：其硬编码 40 对并
  写入工程师 canonical 证据路径，运行会覆盖；改用同入口的 tester 小规模脚本 6 对。
- GitHub Actions 本身未在本地执行；本轮执行的是同一 pytest 命令的本地复跑。
- 未重新跑前端测试（156）；本轮范围是 phase 6 后端/证据，full suite 不含 Frontend。
- 未对 `docs/PHASE6_INDEPENDENT_TEST_REPORT.md` 自身做 black 检查（Markdown）；
  tester 两个 Python 测试文件未改动。
- 报告顶部 JSON 与全局各节为 round16 历史；本轮结论以本节为准。报告 sha256 由工程师
  在最终定稿后回填；本轮测试者可在最终回复给出当前文件哈希。

### 本轮证据文件（均位于 `artifacts/foundation/phase6/tester-round17/`）

`aggregate-continuation-recomputed.json`、`aggregate-recompute.stdout.txt`、
`full-suite.{xml,stdout.txt,exitcode.txt}`、`guard-run{1,2,3}.{xml,stdout.txt}`、
`tester-offline.{xml,stdout.txt}`、`tester-postgres.{xml,stdout.txt}`、
`obs-aggregate.{xml,stdout.txt}`、`check-{architecture,p0,control}.{xml,stdout.txt}`、
`check-observer-help.stdout.txt`、`manifest-verification.json`、
`manifest-regen-impl-current.json`、`manifest-regen-current.json`、
`stress-verification-round17.json`、`handoff-consistency-round17.json`、
`long-segment-recomputed.json`、`verify_manifests.py`、`verify_stress_round17.py`、
`verify_handoff_round17.py`、`tester-round17-manifest.json`（逐文件 sha256 清单）。

### 冻结与归属

- `authored_by=tester`、`executed_by=tester`、`analyzed_by=tester`；本轮全部命令由测试者
  会话执行，无工程师代跑。
- 仅追加本报告与 `artifacts/foundation/phase6/tester-round17/**`；未修改生产实现、
  既有测试断言、全局 fixture、两份 manifest、handoff/evidence-index、窗口证据、
  CI 或业务契约。
- `workspace_frozen=true` 仅指本轮测试者写入范围冻结；**overall=blocked**，6.1/6.2
  仍 pending，不声明阶段 6 最终交付通过。

## round18 复验：manifest/lineage 修复与 final16（P6-TESTER-ROUND18-CONTINUATION）

```json
{
  "report_id": "P6-TESTER-ROUND18-CONTINUATION",
  "workflow_version": 3,
  "phase": 6,
  "role": "tester",
  "agent_id": "tester-round18 session (child of engineer 231a51ed-bcba-4dd2-afe6-271e45f72164)",
  "round": 18,
  "base_commit": "c6fe450260eeb59d46c9caee591fb1625510b443",
  "branch": "feat/foundation-refactor",
  "source_digest": {
    "implementation_digest": "fd8a4df74550b971cbd608ec48d4d5a89eea4c3b6565362978d0a46730e97180",
    "implementation_files": 166,
    "complete_phase_digest": "17b8aeb27e7445009f21b9f3db687fccd1a15c2645e6b10ab78a988703884307",
    "complete_phase_files": 168,
    "manifest_checks": "per-file sha256 all match; mismatch=[]; null_but_exists=[]; missing=[]; file set equals git change set",
    "fire_test_sha256": "32cc7bcef236318c2e7990a876553f54676bd23ade851615aaa6806d1716fd74",
    "strategies_init_sha256": "9764ae81bb5c2fc2de6eadada07cb795c58fc8a553e35cf8acb74b76024718d0",
    "supervisor_sha256": "b137c65688801e866def52f1994cd445c3bbd33f876404dda42f071aa837f5fe"
  },
  "overall": "pass_for_6.3-6.12; pending_6.1-6.2",
  "overall_note": "round17 blocking findings P6-R17-MANIFEST-INCOMPLETE and P6-R17-SEGMENT-LINEAGE-BASELINE are closed by independent recomputation. 6.1/6.2 remain pending and are not claimed.",
  "authored_by": "tester (round18 session)",
  "executed_by": "tester (all commands in this round; no engineer proxy run)",
  "analyzed_by": "tester",
  "workspace_frozen": true
}
```

### 结论摘要

- **两个 round17 blocking findings 均已关闭**：
  - **P6-R17-MANIFEST-INCOMPLETE**：impl 166/`fd8a4df7…`、complete 168/`17b8aeb2…`；
    逐文件 sha256 全部匹配，`mismatch=[]`、`null_but_exists=[]`、`missing=[]`，文件集与
    `git diff --name-status <base>` + untracked（减去排除项）完全一致；
    `trader/strategies/fire_test.py`（`32cc7bce…`）与
    `trader/strategies/__init__.py`（`9764ae81…`）已在集合内；supervisor `b137c656…`；
    handoff/index 的 source_digest/file_count/frozen_* 已同步为上述值；
    `regenerate_manifests.py` 已委托 canonical `scripts/phase_manifest.py`。
  - **P6-R17-SEGMENT-LINEAGE-BASELINE**：不可覆盖 lineage 文件
    `segment-lineage-20261110.json`（sha256 `f07174a5…`）记录四态；
    当前 baseline `db-baseline-continuation-20261110.json`（sha256 `46297bd6…`）已修正
    `delta_from_original.events=14` 并加 role/`next_segment_index=4`/lineage 字段；
    四态 identity 由测试者按事件时间戳独立重构并全部匹配（见下）；
    历史 `segment.json`/`window.json` 未改写（aggregate 证据 digest 与 canonical 一致）；
    `capture_continuation_baseline.py` 已从 `db-baseline.json` 动态计算 delta。
- **final16 与独立全量**：工程师 final16 = **3491 passed / 0 failed / 0 errors / 0 skipped**、
  exit 0、JUnit timestamp `2026-11-10T15:12:06.909370+08:00`；测试者独立复跑
  **3491 passed / 0 failed / 0 errors / 0 skipped**、exit 0、JUnit timestamp
  `2026-11-10T15:18:57.623345+08:00`（写 `tester-round18/full-suite.xml`，未覆盖 final16）。
- **聚合算术不变**：original qualified `19,874,125ms`、active `39,598,016ms`、
  excluded `19,723,891ms`、evidence_digest `6498dad2…`；验证段 `872,313ms`、
  digest `2c3693b7…`；累计 `20,746,438ms`、剩余 `65,653,562ms`、`all_runs_valid=true`、
  `dr_internal_breach=false`、`acceptance_satisfied=false`；与 canonical 逐字段一致。
- **guard/observation/resume**：guard 18 例×3 通过；stress round3 **40/40 `[0,3]`**、
  0 failed、inputs unchanged（supervisor `b137c656…`/guard `50e680f2…`）；测试者当前代码
  6/6 对真实入口竞争通过；observation-only 镜像/行为与验证段 67/65 不变继续成立。
- **6.1/6.2 仍 pending**：累计 `20,746,438ms` < 86,400,000ms，剩余 `65,653,562ms`
  （18h14m）；本轮未启动任何窗口/分段/沙箱 compose，不声明 6.1/6.2 通过。

### 本轮独立执行的命令与结果

| # | 命令（DSN 以 `<task-db>` 代替，不写入报告） | exit | tests / failures / errors / skipped | 证据 |
| --- | --- | --- | --- | --- |
| 1 | `POSTGRES_CONNECTION_STRING=<task-db> python -m pytest -o addopts='' -q -p no:cacheprovider trader/tests --junitxml=artifacts/foundation/phase6/tester-round18/full-suite.xml --tb=short`（task DB `qts_p6_suite_948e94bb`） | 0 | 3491 / 0 / 0 / 0（`3491 passed, 24 warnings in 293.29s`） | `tester-round18/full-suite.{xml,stdout.txt,exitcode.txt}`；JUnit timestamp `2026-11-10T15:18:57.623345+08:00` |
| 2 | 交叉核对工程师 final16：`engineer-full-suite-final16.{xml,stdout.txt,exitcode.txt}` | 0 | 3491 / 0 / 0 / 0 | JUnit timestamp `2026-11-10T15:12:06.909370+08:00`；stdout `3491 passed, 24 warnings in 260.97s` |
| 3 | `python -m pytest ... trader/tests/test_phase6_startup_guard.py` ×3 | 0 / 0 / 0 | 18 / 0 / 0 / 0 ×3（24.79s / 25.52s / 25.53s） | `tester-round18/guard-run{1,2,3}.{xml,stdout.txt}` |
| 4 | `python -m pytest ... trader/tests/test_phase6_independent_offline.py` | 0 | 66 / 0 / 0 / 0 | `tester-round18/tester-offline.xml`；文件 sha256 `eb643f0a…` 未变 |
| 5 | `POSTGRES_CONNECTION_STRING=<task-db> python -m pytest ... trader/tests/test_phase6_independent_postgres.py` | 0 | 4 / 0 / 0 / 0 | `tester-round18/tester-postgres.xml`；文件 sha256 `0f7e030f…` 未变 |
| 6 | `python -m pytest ... trader/tests/test_phase6_observation_only_strategy.py trader/tests/test_phase6_segment_aggregate.py` | 0 | 17 / 0 / 0 / 0（5 + 12） | `tester-round18/obs-aggregate.{xml,stdout.txt}` |
| 7 | `python scripts/phase6_segment_aggregate.py --original .../windows-aborted/20261109T011441Z-efbba899 --segment .../windows/20261110T030232Z-7a911829 --out .../tester-round18/aggregate-continuation-recomputed.json` | 0 | n/a | `aggregate-continuation-recomputed.json`、`aggregate-recompute.stdout.txt` |
| 8 | `python scripts/phase6_segment_aggregate.py --original .../windows-aborted/20261110T035649Z-36448c34 --out .../tester-round18/long-segment-recomputed.json` | 0 | n/a | `long-segment-recomputed.json` |
| 9 | `python -I .../tester-round18/verify_manifests.py` | 0 | impl/final 全匹配、`ok=true` | `manifest-verification.json` |
| 10 | `python -I .../tester-round18/verify_stress_round18.py`（核对 round3 40 对 + 当前代码 6 对） | 0 | 40/40 + 6/6 均 `[0,3]` | `stress-verification-round18.json` |
| 11 | `python -I .../tester-round18/verify_lineage_handoff_round18.py` | 0 | 20 项检查：19 pass、1 fail（docs baseline sha 声明） | `lineage-handoff-verification.json` |
| 12 | `docker image inspect qts-runtime:local`；四个停止容器 `docker inspect` compose config-hash；`docker ps` 核对无运行中的 `qts_p6_sandbox_*` | 0 | 镜像 `b4cd10c9…`；labels 与 compose 文件一致 | 见正文 |

### 1) P6-R17-MANIFEST-INCOMPLETE 关闭证据

- impl 166 文件 / `fd8a4df74550b971cbd608ec48d4d5a89eea4c3b6565362978d0a46730e97180`；
  complete 168 文件 / `17b8aeb27e7445009f21b9f3db687fccd1a15c2645e6b10ab78a988703884307`。
- 逐文件复算：`mismatch=[]`、`null_but_exists=[]`、`missing=[]`；
  manifest 文件集与 `git diff --name-status` + untracked（剔除 `.workbuddy/` 与 manifest
  `excluded`）完全一致（`manifest_not_in_git_change_set=[]`、
  `git_change_not_in_manifest=[]`）。
- 关键文件：`trader/strategies/fire_test.py` `32cc7bce…`、
  `trader/strategies/__init__.py` `9764ae81…`、`scripts/sandbox_supervisor.py`
  `b137c656…`。
- handoff 的 `source_digest`/`source_file_count`/`frozen_*` 与 index 的
  `frozen_source_digest`/`frozen_source_files` 均等于上述值（manifest verifier
  `ok=true`）。
- 与 round17 测试者重算的 166/`2d2334f2…` 相比，文件集完全相同；只有三个 WP3 文档
  （`docs/INTERFACE_CONTRACTS.md`、`docs/PROJECT_ARCHITECTURE.md`、
  `docs/UNIFIED_TRADING_RUNBOOK.md`）内容变化，生产代码无未披露变化。
- `regenerate_manifests.py` 已改为调用 `python -I scripts/phase_manifest.py --base …`
  （impl 另加两个 tester 文件 `--exclude`），不再使用旧路径列表。

### 2) P6-R17-SEGMENT-LINEAGE-BASELINE 关闭证据

- lineage 文件：`artifacts/foundation/phase6/segment-continuation-20261109/segment-lineage-20261110.json`，
  sha256 `f07174a5573ff269604339f3ce3ac67dffae56ada4ed9a0ccf72a3250d4742c5`；
  当前 baseline sha256 `46297bd6556c478c41beb05081b83d1d27afbf0e4f914e5988965016cd63f915`。
- 四态由测试者独立按事件 ID 内嵌时间戳重构（排序后 canonical JSON 的 sha256）：

| state | counts（events） | identity（events） | 测试者复算 |
| --- | --- | --- | --- |
| original_stop | 436 | `99cf3e35902cd6d1c646434ffa66f72bfcf17646ec86c65fef6a72662d6a277e` | match |
| pre_validation_segment_7a911829 | 441 | `a3446acce82ec2a2d83b63ec87e7a972e18f71079052310885d14e0f53892367` | match |
| pre_long_segment_36448c34 | 445 | `3b366cf46f24e53493530f27e6a2559e2f60ec6cb4711b2602b6d86e5ab65aa2` | match |
| pre_next_segment_4 | 450 | `89598282182934074a82162ac016f6d750c9223ea12ca6339904a6009992031c` | match |

- `436→441` 的 +5：`order.queued`/`order.accepted`/`execution.booked`
  （`qts-83facbd2aaa50265fea81071`）+ 2 条 `account.reconciled`；
  `441→445` 的 +4：4 条 `account.reconciled`（验证段）；
  `445→450` 的 +5：5 条 `account.reconciled`（长段）。合计 +14，与
  `delta_from_original.events=14` 一致。
- lineage 四态的 `source_sha256` 与实际文件逐一匹配：`db-baseline.json`
  `37ab9209…`、7a911829 `resume-preflight.json` `cb9ab7b4…`、36448c34
  `resume-preflight.json` `fddc0223…`、当前 continuation baseline `46297bd6…`。
- 历史证据未改写：7a911829 aggregate evidence_digest 仍为 `2c3693b7…`；
  36448c34 仍为 `5bc4661a…`；原始窗口仍为 `6498dad2…`；段 2/3 的 `segment.json`
  `db_baseline` 路径字段按旧行为保留（段 2 指向 `db-baseline.json`，段 3 指向
  continuation baseline 路径），未回写；lineage 文件明确记录实际 preflight identity。
- 长段分类：4 个 dr_stale 全在尾部（seq 39–43，最后成功 backup
  `2026-11-10T04:11:57.675Z`，最后样本 `04:18:50.472Z`，7 次 SSH_TUNNEL_NOT_READY），
  `dr_internal_breach=false`、`run_valid=true`、tail 规则 qualified `1,143,515ms`；
  canonical 零计入（保守），若按 tail 规则累计 `21,889,953ms`（仍 <24h）。
  handoff 的 `failed_long_reason` 已改为 “tail DR breach … dr_internal_breach=false”，
  不再称 internal breach；lineage/docs 同步记录两种口径。

### 3) 非阻断项修复核对

- evidence-index `segment_continuation`：`original_qualified_seconds=19874.125`、
  `remaining_qualified_seconds=65653.562`、`original_not_ready_seconds=9741.442`、
  `original_excluded_seconds=19723.891`、status 已更新为 round17 修复/round18 pending；
  与 canonical aggregate 一致。
- handoff/index：supervisor `b137c656…`、guard `50e680f2…`；`frozen_image` 为历史
  `aea741d2…`（带 “historical original-window build; not current”），`image` 为当前
  `b4cd10c9…`；`image_note` 说明 11:05 supervisor 编辑是 host-side source，不在镜像内。
- compose：`compose-hashes-resume-20261110.json` sha256 `01f7a329…`；
  docker labels 独立复核：postgres `bbdc531c…`、alert `fc7fa351…` 与 work package 一致；
  control `6991731f…`、execution `60c7dbfe…` 因 resume env（project/ports/secrets/
  observation-only）不同，文件附有说明；未重启容器、未 re-render compose。
- WP3 文档：13.17.1、8.5 末段、5.2.1 均已存在，覆盖分段累计、resume、
  observation-only、qualified-v3、lineage、当前数值与 “6.1/6.2 仍未通过”；
  `start_resume_segment.ps1` 存在且使用 `--segment-index 4`。
- stress round3：40/40 `[0,3]`、0 failed、40 owner/40 loser、无孤儿、PID 唯一、
  inputs unchanged；supervisor/guard 输入 hash 与当前磁盘一致；测试者当前代码 6/6 对
  真实入口竞争通过。

### Findings

**Blocking：无。** round17 的两个 blocking findings 已关闭。

**Non-blocking（建议最终 handoff 前修正/披露）**

1. **P6-R18-DOCS-BASELINE-SHA-CLAIM** — `docs/INTERFACE_CONTRACTS.md` 13.17.1 写
   “每段 `segment.json` 至少记录 … `db_baseline` 与实际使用的 baseline sha256”，但
   `scripts/sandbox_supervisor.py::_write_segment_metadata` 只写 `db_baseline` 路径，
   未写 `db_baseline_sha256`；7a911829/36448c34 的 `segment.json` 也没有该字段。
   lineage 文件已经记录状态 source sha256，因此不影响 provenance 结论；建议二选一：
   给未来段的 `_write_segment_metadata` 增加实际 baseline sha256，或把文档改为
   “实际 baseline sha256 记录在 lineage 文件”。证据：
   `lineage-handoff-verification.json` 的唯一 fail。
2. **P6-R18-GUARD-METADATA-RACE（advisory）** — stress round3 offset 0 / pair 0 的
   loser 详情显示上一 owner PID `38040`，而 winner 为 `38816`；退出码仍为 `[0,3]`、
   无副作用，属于 guard 释放后/新 owner 写 metadata 前的读取窗口（诊断信息陈旧）。
   建议 loser 在锁失败后短暂重读 metadata，或在文档注明该字段仅为 best-effort。
   证据：`stress-verification-round18.json` 的
   `loser_detail_owner_pid_mismatch`（仅 1/40 对）。
3. **P6-R18-VALIDATION-PRE-EDIT（carried）** — 唯一合格验证段 7a911829 运行于
   11:05 supervisor 编辑前；当前 resume-baseline 强制/segment-metadata 逻辑仅由
   full suite/单测覆盖。handoff/lineage/docs 已披露 11:05 行为；下一段必须用当前
   冻结代码端到端执行。
4. **P6-R18-RFC-BASELINE-HASH-STALE（advisory）** — `RFC-v3.md` §11 仍写
   `db-baseline.json` sha256 `f7215cda…`；实际文件与 work-package/lineage 均为
   `37ab9209…`。建议把 RFC 的该值标注为历史或更正，避免 reviewer 混淆。

### 未执行项与限制

- 未启动任何窗口/分段/沙箱 compose（遵守工作单）；未操作真实账户/真实资金；未改主机
  WiFi/路由/代理/防火墙；未 commit/push/merge/deploy。
- 未直接运行 `artifacts/foundation/phase6/run_concurrency_stress.py`：其硬编码 40 对并
  写入工程师 canonical 压力证据路径，运行会覆盖；改用同入口的 tester 脚本 6 对。
- GitHub Actions 未在本地执行；未重跑前端 156 项（本轮范围为 phase 6 后端/证据）。
- 未对 Markdown 报告做 black 检查；tester 两个 Python 测试文件未改动
  （`eb643f0a…`/`0f7e030f…`）。
- 报告顶部 JSON 与全局各节为 round16 历史；round17/round18 结论以文末各自段落为准。
  本轮报告 sha256 由工程师在最终定稿后回填；测试者可在最终回复给出当前文件哈希。

### 本轮证据文件（均位于 `artifacts/foundation/phase6/tester-round18/`）

`full-suite.{xml,stdout.txt,exitcode.txt}`、`guard-run{1,2,3}.{xml,stdout.txt}`、
`tester-offline.{xml,stdout.txt}`、`tester-postgres.{xml,stdout.txt}`、
`obs-aggregate.{xml,stdout.txt}`、`aggregate-continuation-recomputed.json`、
`aggregate-recompute.stdout.txt`、`long-segment-recomputed.json`、
`long-segment-recompute.stdout.txt`、`manifest-verification.json`、
`manifest-verification.stdout.txt`、`stress-verification-round18.json`、
`stress-verification-round18.stdout.txt`、`lineage-handoff-verification.json`、
`lineage-handoff-verification.stdout.txt`、`verify_manifests.py`、
`verify_stress_round18.py`、`verify_lineage_handoff_round18.py`、
`tester-round18-manifest.json`（逐文件 sha256 清单）。

### 冻结与归属

- `authored_by=tester`、`executed_by=tester`、`analyzed_by=tester`；本轮全部命令由
  测试者会话执行，无工程师代跑。
- 仅追加本报告与 `artifacts/foundation/phase6/tester-round18/**`；未修改生产实现、
  既有测试断言、全局 fixture、两份 manifest、handoff/evidence-index、窗口证据、
  CI 或业务契约。
- `workspace_frozen=true`；**overall=pass_for_6.3-6.12; pending_6.1-6.2**；
  6.1/6.2 仍 pending，不声明阶段 6 最终完成。

## round19 复验：文档口径与最终指纹（P6-TESTER-ROUND19-CONTINUATION）

```json
{
  "report_id": "P6-TESTER-ROUND19-CONTINUATION",
  "workflow_version": 3,
  "phase": 6,
  "role": "tester",
  "agent_id": "tester-round19 session (child of engineer 231a51ed-bcba-4dd2-afe6-271e45f72164)",
  "round": 19,
  "base_commit": "c6fe450260eeb59d46c9caee591fb1625510b443",
  "branch": "feat/foundation-refactor",
  "source_digest": {
    "implementation_digest": "0935a8affca7c9fd9d1fe7d6c5c99ed6bcad3bbcf05edab209b5b7972529c47a",
    "implementation_files": 166,
    "complete_phase_digest": "519c2d10855b6621311cabe68395f7b1688598ad11a760e7ac41981a1f0e8662",
    "complete_phase_files": 168,
    "manifest_checks": "per-file sha256 all match; mismatch=[]; null_but_exists=[]; missing=[]; file set equals git change set",
    "fire_test_sha256": "32cc7bcef236318c2e7990a876553f54676bd23ade851615aaa6806d1716fd74",
    "strategies_init_sha256": "9764ae81bb5c2fc2de6eadada07cb795c58fc8a553e35cf8acb74b76024718d0",
    "supervisor_sha256": "b137c65688801e866def52f1994cd445c3bbd33f876404dda42f071aa837f5fe"
  },
  "overall": "pass_for_6.3-6.12; pending_6.1-6.2",
  "overall_note": "docs-only wording/fingerprint round; both round17 blocking findings remain closed; no blocking findings. 6.1/6.2 remain pending and are not claimed.",
  "authored_by": "tester (round19 session)",
  "executed_by": "tester (all commands in this round; no engineer proxy run)",
  "analyzed_by": "tester",
  "workspace_frozen": true
}
```

### 结论摘要

- **本轮仅文档/指纹变更，生产代码未改**：166 个 manifest 文件中，只有
  `docs/INTERFACE_CONTRACTS.md` 的 mtime 晚于 round18（15:26）；生产代码
  `scripts/sandbox_supervisor.py` `b137c656…`、`trader/ops/startup_guard.py` `50e680f2…`、
  `trader/strategies/fire_test.py` `32cc7bce…`、`trader/strategies/__init__.py`
  `9764ae81…` 与 round18 一致；tester 两个测试文件仍为 `eb643f0a…`/`0f7e030f…`。
- **13.17.1 / RFC-v3 §11 已修正**：不再声称每段 `segment.json` 已记录 baseline sha256；
  明确当前 supervisor 只写 `db_baseline` 路径，实际 baseline sha256 与每段 preflight
  identity 在不可覆盖的 `segment-lineage-20261110.json`；`db_baseline_sha256` 是后续版本
  的增强项。RFC §11 的 `db-baseline.json` sha 已改为实际值 `37ab9209…`，并把
  `f7215cda…` 标注为历史。guard 元数据竞争已作为 advisory 写入 13.17.1。
- **manifest 精确复算**：impl **166**/`0935a8af…`、complete **168**/`519c2d10…`；
  逐文件 sha256 全匹配，`mismatch=[]`、`null_but_exists=[]`、`missing=[]`，文件集与
  `git diff --name-status <base>` + untracked（减排除项）一致；策略文件仍在集合内。
- **final17**：3491 passed / 0 failed / 0 errors / 0 skipped，exit 0，JUnit timestamp
  `2026-11-10T15:28:27.626056+08:00`；测试者独立全量套件见下（写
  `tester-round19/full-suite.xml`，未覆盖 final17）。
- **handoff/index**：source/complete digests、final17 指针、tester round18 pass 记录
  （report sha `69f5d973…`）均一致。本轮报告追加后该 sha 会变化，最终 handoff 由工程师
  回填。
- **6.1/6.2 仍 pending**：累计 20,746,438ms、剩余 65,653,562ms；本轮未启动任何窗口/分段/
  沙箱 compose，不声明通过。

### 本轮独立执行的命令与结果

| # | 命令（DSN 以 `<task-db>` 代替，不写入报告） | exit | tests / failures / errors / skipped | 证据 |
| --- | --- | --- | --- | --- |
| 1 | `POSTGRES_CONNECTION_STRING=<task-db> python -m pytest -o addopts='' -q -p no:cacheprovider trader/tests --junitxml=artifacts/foundation/phase6/tester-round19/full-suite.xml --tb=short`（task DB `qts_p6_suite_948e94bb`） | 0 | 3491 / 0 / 0 / 0（`3491 passed, 24 warnings in 273.36s`） | `tester-round19/full-suite.{xml,stdout.txt,exitcode.txt}`；JUnit timestamp `2026-11-10T15:33:58.803898+08:00` |
| 2 | 交叉核对工程师 final17：`engineer-full-suite-final17.{xml,stdout.txt,exitcode.txt}` | 0 | 3491 / 0 / 0 / 0 | JUnit timestamp `2026-11-10T15:28:27.626056+08:00`；stdout `3491 passed, 24 warnings in 276.86s` |
| 3 | `python -I scripts/phase_manifest.py --base c6fe450… --exclude <offline> --exclude <postgres> --out .../manifest-regen-impl.json`；同命令不加 exclude 生成 complete | 0 | impl 166/`0935a8af…`；complete 168/`519c2d10…` | `manifest-regen-impl.json`、`manifest-regen-complete.json` |
| 4 | `python -I .../tester-round19/verify_round19.py` | 0 | 12/12 checks pass | `round19-verification.json` |
| 5 | `docker image inspect qts-runtime:local`；四个停止容器 compose config-hash 只读核对 | 0 | 镜像 `b4cd10c9…`；labels 与 compose 文件一致 | 见 round18/本轮正文 |

### 1) 文档修正核对

- `docs/INTERFACE_CONTRACTS.md` 13.17.1：
  - 当前 supervisor “在 `segment.json` 写 `db_baseline` 路径”；实际使用的 baseline
    sha256 与每段 preflight identity 记录在 `segment-lineage-20261110.json`；历史段 2/3
    的 `segment.json` 无 sha 字段属 11:05 前行为；后续版本应在 `_write_segment_metadata`
    增加 `db_baseline_sha256`，届时以 `segment.json` 字段为准。该表述与实现
    （`_write_segment_metadata` 当前只写路径）和 lineage 文件一致。
  - guard 元数据仅作诊断：竞争失败者可能读到上一 owner PID，归属由 OS 锁裁决，退出码
    保持 `[0,3]` 且无副作用（round18 advisory 已写入）。
  - 未发现残留的“每段 segment.json 已记录 baseline sha256”虚假声明；6.1/6.2 仍明确
    “聚合工具本身不推进 6.1/6.2”。
- `RFC-v3.md` §11：`db-baseline.json` sha256 已改为
  `37ab9209eb9740b3afd920f13080c9469136f6eaaf6ec8f5ee9242455fc72dbe`，并注明早期
  `f7215cda…` 为历史、以 lineage 为准；实际文件 hash 与 work-package/lineage 一致。
- 文档-only 证据：manifest 文件中只有 `docs/INTERFACE_CONTRACTS.md` 的 mtime 晚于
  round18（15:26）；`PROJECT_ARCHITECTURE.md`/`UNIFIED_TRADING_RUNBOOK.md` 与 round18
  相同（`3361d912…`/`a1dc8d9a…`）；生产代码 hash 未变。

### 2) 最终指纹（canonical 重算）

- impl 166 文件 / `0935a8affca7c9fd9d1fe7d6c5c99ed6bcad3bbcf05edab209b5b7972529c47a`
- complete 168 文件 / `519c2d10855b6621311cabe68395f7b1688598ad11a760e7ac41981a1f0e8662`
- 逐文件：`mismatch=[]`、`null_but_exists=[]`、`missing=[]`；文件集与 git 变更集一致；
  `trader/strategies/fire_test.py` `32cc7bce…`、`trader/strategies/__init__.py`
  `9764ae81…`、supervisor `b137c656…` 均在集合内。
- handoff `source_digest`/`source_file_count`/`frozen_*` 与 index
  `frozen_source_digest`/`frozen_source_files` 均等于上述值。

### 3) final17 与 handoff/index

- final17 JUnit：3491 tests、0 failures、0 errors、0 skipped、exit 0、timestamp
  `2026-11-10T15:28:27.626056+08:00`；stdout `3491 passed, 24 warnings in 276.86s`。
- handoff/index 的 `complete_suite` 指针均指向 `engineer-full-suite-final17.*`；
  `tester`/`tester_report` 记录 round18 overall
  `pass_for_6.3-6.12; pending_6.1-6.2`、report sha `69f5d973…`，与本报告 round18 段一致。
- 本轮报告追加后 report sha 将变化（见最终回复）；按工作流由工程师回填 handoff/index。

### Findings

**Blocking：无。** round17 的两个 blocking findings 保持关闭；round18 的非阻断项
（docs baseline sha 声明、guard metadata race、RFC baseline hash）均已修复/披露。

**Non-blocking：无新增。** 仅保留一个已知限制：历史段 2/3 实际使用的 441/445-event
baseline 文件未保留，其 baseline 文件 sha 不可恢复；lineage 以各自 `resume-preflight.json`
的 identity 与 source sha 锚定，当前 450-event baseline 的 sha `46297bd6…` 已记录。
这不影响 qualified 复算。

### 未执行项与限制

- 未启动任何窗口/分段/沙箱 compose；未操作真实账户/真实资金；未改主机 WiFi/路由/代理/
  防火墙；未 commit/push/merge/deploy。
- 未重跑 guard ×3/定向 tester 套件；本轮 full suite 已覆盖这些用例（final17 与独立全量
  均 3491/0/0/0）。
- GitHub Actions 未在本地执行；未重跑前端 156 项。
- 报告顶部 JSON 与全局各节为 round16 历史；round17/18/19 结论以文末各自段落为准。
  本轮报告 sha256 由工程师在最终定稿后回填；测试者可给出当前文件哈希。

### 本轮证据文件（均位于 `artifacts/foundation/phase6/tester-round19/`）

`full-suite.{xml,stdout.txt,exitcode.txt}`、`manifest-regen-impl.json`、
`manifest-regen-complete.json`、`round19-verification.json`、
`round19-verification.stdout.txt`、`verify_round19.py`、
`tester-round19-manifest.json`（逐文件 sha256 清单）。

### 冻结与归属

- `authored_by=tester`、`executed_by=tester`、`analyzed_by=tester`；本轮全部命令由
  测试者会话执行，无工程师代跑。
- 仅追加本报告与 `artifacts/foundation/phase6/tester-round19/**`；未修改生产实现、
  既有测试断言、全局 fixture、两份 manifest、handoff/evidence-index、窗口证据、
  CI 或业务契约。
- `workspace_frozen=true`；**overall=pass_for_6.3-6.12; pending_6.1-6.2**；
  6.1/6.2 仍 pending，不声明阶段 6 最终完成。

## round20 最终审计：6.1/6.2 通过（P6-TESTER-ROUND20-FINAL）

```json
{
  "report_id": "P6-TESTER-ROUND20-FINAL",
  "workflow_version": 3,
  "phase": 6,
  "role": "tester",
  "agent_id": "tester-round20 session (child of engineer 231a51ed-bcba-4dd2-afe6-271e45f72164)",
  "round": 20,
  "base_commit": "c6fe450260eeb59d46c9caee591fb1625510b443",
  "branch": "feat/foundation-refactor",
  "source_digest": {
    "implementation_digest": "0935a8affca7c9fd9d1fe7d6c5c99ed6bcad3bbcf05edab209b5b7972529c47a",
    "implementation_files": 166,
    "complete_phase_digest": "519c2d10855b6621311cabe68395f7b1688598ad11a760e7ac41981a1f0e8662",
    "complete_phase_files": 168,
    "final17": "3491 passed / 0 failed / 0 errors / 0 skipped, exit 0, JUnit timestamp 2026-11-10T15:28:27.626056+08:00",
    "manifest_checks": "per-file sha256 all match; mismatch=[]; null_but_exists=[]; missing=[]; file set equals git change set"
  },
  "window": {
    "run_id": "20261110T075417Z-cc17f251",
    "window_sha256": "263f70bbf339b513cdf926a47ffb47b4c87d6bf5ece06264c4b94763f65cf468",
    "passed": true,
    "criteria_true": 20,
    "samples": 2872,
    "duration_seconds": 86400,
    "ready_samples": 2510,
    "dr_cycles": 712,
    "dr_failures": 0
  },
  "final_aggregate": {
    "path": "artifacts/foundation/phase6/segment-continuation-20261109/aggregate-final-20261111.json",
    "sha256": "a1c135b7ac99cc4021d792ad12e8b0171a5e4e179d918756d6c311e58e603c4c",
    "original_qualified_ms": 19874125,
    "validation_qualified_ms": 872313,
    "segment5_qualified_ms": 73664222,
    "cumulative_qualified_ms": 94410660,
    "remaining_ms": 0,
    "qualified_satisfied": true,
    "all_runs_valid": true,
    "dr_internal_breach": false,
    "acceptance_satisfied": true
  },
  "baseline": {
    "path": "artifacts/foundation/phase6/segment-continuation-20261109/db-baseline-continuation-20261110.json",
    "sha256": "e63105b5f72a9a0621bc029f18302bcefac6ca5f48030ec7b1968bdf3e08d4a0",
    "counts": {"accounts": 1, "commands": 67, "events": 721, "executions": 65, "ledger": 199, "outbox": 319},
    "events_identity": "e7916be1fb7ecb131a318f4ed92ecf2c517116f5316c54404347cdd07c588965",
    "executions_identity": "4cacdd5b437bccb191ba7c777f5da2477e538eac9cb29d1532154680a7175d63",
    "ledger_identity": "6f15c98697d47752146cfaff66a43f276af34617322f5e37381aa2f2912258c2"
  },
  "lineage": {
    "path": "artifacts/foundation/phase6/segment-continuation-20261109/segment-lineage-20261110.json",
    "sha256": "dda0bceabf6b2cb089dfba3afd0064ab280b7565dfe8bcedb508c70381896b84",
    "segment4": "20261110T074521Z-703b71af, zero-credit, incident P6-INC-20261110-01",
    "segment5": "20261110T075417Z-cc17f251, validated_window_passed"
  },
  "overall": "pass_for_6.1-6.12",
  "authored_by": "tester (round20 session)",
  "executed_by": "tester (all commands in this round; no engineer proxy run)",
  "analyzed_by": "tester",
  "workspace_frozen": true
}
```

### 结论

- **6.1 通过**：segment 5 `20261110T075417Z-cc17f251` 的 observer marker 单调时长
  `330,414,171 → 416,814,171ms = 86,400,000ms`（`elapsed_monotonic_ms=86400000`；
  墙钟 07:55:07Z → 07:55:05.630Z 相差 86,398.306s，门禁按 round13 起的 monotonic 口径）；
  `window.json` **passed=true**，20/20 criteria 全 true；2,872 samples、0 metrics missing、
  0 unresolved、0 repeated sends、执行计数恒 65、无容器重启/监督中止/错误；DR
  712 cycles / 0 failures、max gap 151,939ms <300s、head 14,289ms、tail 29,751ms。
- **6.2 通过**：observation/window/supervisor 中 `PRIVATE_STREAM_COVERAGE_UNPROVEN`
  出现 **0** 次；2,872/2,872 样本均有完整 coverage proof（source/contiguous/generation/
  connected_from/recorded_at_ms/covered_through/buffered_through）；ready 样本
  coverage 不合格 **0**；最后 ready 样本 coverage age 4,519ms ≤780,000ms。
- **最终 aggregate**：独立复跑与 canonical 逐字段一致；original 19,874,125ms、
  validation 872,313ms、segment5 73,664,222ms、cumulative **94,410,660ms**、remaining 0、
  qualified_satisfied=true、all_runs_valid=true、dr_internal_breach=false、
  **acceptance_satisfied=true**。
- **6.3–6.12** 继续由 round19 的 final17/manifests 结论支撑：final17 3491/0/0/0、exit 0；
  impl 166/`0935a8af…`、complete 168/`519c2d10…`；handoff/index 与 tester round19
  pass 记录一致。
- **overall = `pass_for_6.1-6.12`**。无 blocking findings；有 4 项非阻断的
  lineage/文档元数据问题（见下），不影响 6.1/6.2 的窗口/覆盖/累计判定。fresh reviewer
  仍 pending（流程要求，非本轮测试阻断项）。

### 6.1 / 6.2 逐项证据

#### 6.1 窗口 20 项 criteria（全部 true，独立复核）

| # | criterion | 独立证据 |
| --- | --- | --- |
| 1 | `duration_at_least_24h` | 首/末 marker `monotonic_ms` 330,414,171 → 416,814,171，差 86,400,000ms；末 marker `elapsed_monotonic_ms=86400000`；`duration_seconds=86400` |
| 2 | `samples_present` | `observation.jsonl` 2,872 个 `kind=sample` |
| 3 | `samples_cover_window` | 2,872 ≥ min_samples 2,592（expected 2,880） |
| 4 | `sample_gap_within_bound` | `sample_gap_max_ms=32,219` ≤180,000 |
| 5 | `metrics_samples_present` | `metrics_samples=2,872` ≥2,592；逐样本 metrics 存在 |
| 6 | `metrics_gap_within_bound` | `metrics_gap_max_ms=32,218` ≤180,000；`metrics_missing=0` |
| 7 | `no_private_stream_coverage_unproven` | 0 次 `PRIVATE_STREAM_COVERAGE_UNPROVEN`（observation/window/supervisor） |
| 8 | `coverage_proof_recorded` | 2,872/2,872 样本 coverage proof 字段完整；ready 样本不合格 0 |
| 9 | `coverage_fresh_at_end` | 最后 ready 样本 seq 2872，coverage age 4,519ms ≤780,000；末样本 metrics 存在 |
| 10 | `no_persistent_unresolved` | `unresolved_seen=[]`、`unresolved_max_duration_ms={}`；逐样本 unresolved 空 |
| 11 | `unresolved_cleared_at_end` | 末样本 seq 2872 metrics 存在，`unresolved_entries=[]`、`unresolved=[]` |
| 12 | `no_repeated_sends` | `repeated_send_commands=[]`；逐样本 `send_outcomes.repeated_send_commands` 空 |
| 13 | `execution_count_monotonic` | 2,872 个样本 `execution_counts` 全部为 65；无回退 |
| 14 | `dr_backups_fresh` | RPO max gap 151,939ms <300,000；head age 14,289ms；tail age 29,751ms；head/tail covered |
| 15 | `dr_cycles_succeeded` | 712 cycles / 0 failures（`dr.successes=712`） |
| 16 | `no_container_restart` | supervisor journal 1,434 个 `stack` 行，`restart_counts` 全 0 |
| 17 | `no_supervisor_abort` | `aborted_reason=""`；journal 无 `abort` kind |
| 18 | `no_supervisor_error` | journal 无 error；`abort_error` 空 |
| 19 | `no_persistent_reasons` | 最长 reason `PRIVATE_STREAM_UNHEALTHY=1,025,195ms` <30min；2 分钟类 `PRIVATE_RECOVERY_ACCOUNT_BLOCKED…=30,103ms` <120s；其余均在界内 |
| 20 | `reasons_cleared_at_end` | 末样本 seq 2872 `ready=true`、`reasons=[]` |

补充：`ready_ratio=0.873955…`（2,510/2,872），362 个非 ready 样本均为有界恢复原因
（private stream/reconciliation/account snapshot），无未解释原因、无 unresolved、无重复发送；
aggregate 将 12,733,075ms 作为 not_ready 排除，仍得到 cumulative 94,410,660ms。

#### 6.2 私有 WS 覆盖

- `PRIVATE_STREAM_COVERAGE_UNPROVEN` 在 observation.jsonl、window.json、supervisor.jsonl
  中均为 0 次；`no_private_stream_coverage_unproven=true`。
- 2,872/2,872 样本均带 coverage proof；`ready=true` 样本 coverage 不合格 0；
  最后 ready 样本 coverage age 4,519ms。
- aggregate segment 5 的 `strong_invariants.coverage_proof_missing=false`；
  `primary_bucket.coverage=0`；coverage overlap 1,265,156ms 全部位于 not_ready 区间。

### 最终 aggregate 与 baseline

- 独立复跑 `phase6_segment_aggregate.py --original .../efbba899 --segment .../7a911829 --segment .../cc17f251`
  输出 `tester-round20/aggregate-final-recomputed.json`；与
  `aggregate-final-20261111.json` 逐字段一致（含 original/validation/segment5 的
  qualified/active/excluded/primary/overlap/strong invariants/evidence digest）。
- segment 5：qualified 73,664,222ms、active 86,397,297ms、excluded 12,733,075ms
  （全部 not_ready）；evidence digest `98f18087…`。original/validation digest 仍为
  `6498dad2…`/`2c3693b7…`。
- baseline：67 commands（2 ALLOCATE + 65 PLACE，全 CONFIRMED）、65 executions、
  199 ledger、319 outbox（全 delivered）、65 FILLED、holds 空、events 721
  （identity `e7916be1…`）；executions/ledger identity 未变。
- `delta_from_original`：commands +1、events **+285**、executions +1、ledger +3、
  outbox +3。事件归因独立复算：436→441 +5（queued fill/booking）、441→445 +4
  （validation reconciliation）、445→450 +5（long reconciliation）、450→451 +1
  （segment 4 reconciliation）、451→721 +270（segment 5 reconciliation，全部为
  `account.reconciled`）；无新订单/新 execution/新 ledger/新 outbox。
- segment 4 `20261110T074521Z-703b71af`：zero-credit、非 aggregate 输入；
  incident `P6-INC-20261110-01` 根因为并发 engineer/operator lease recovery 的
  stale read + race（ON CONFLICT 覆盖 active lease）+ postgres stop；reviewer 不是根因；
  DB delta 仅 events +1（commands/executions/ledger/outbox 0）。
- lineage `dda0bcea…`：states 含 pre-segment5 451/`48c7e429…` 与 post-segment5
  721/`e7916be1…`；segment5 validated_window_passed、qualified 73,664,222ms；
  canonical aggregate sha `a1c135b7…`、acceptance_satisfied=true、segment4_zero_credit=true。

### 独立执行的检查

| # | 检查 | exit | 结果 | 证据 |
| --- | --- | --- | --- | --- |
| 1 | 独立全量：`python -m pytest -o addopts='' -q -p no:cacheprovider trader/tests --junitxml=.../tester-round20/full-suite.xml --tb=short`（task DB `qts_p6_suite_948e94bb`） | 0 | 3491 / 0 / 0 / 0；`3491 passed, 24 warnings in 260.70s`；JUnit timestamp `2026-11-11T16:02:56.636873+08:00` | `tester-round20/full-suite.{xml,stdout.txt,exitcode.txt}` |
| 2 | 工程师 final17 交叉核对 | 0 | 3491 / 0 / 0 / 0；timestamp `2026-11-10T15:28:27.626056+08:00` | `engineer-full-suite-final17.*` |
| 3 | guard `test_phase6_startup_guard.py` ×3 | 0 / 0 / 0 | 18 / 0 / 0 / 0 ×3（21.72s / 21.91s / 21.76s） | `tester-round20/guard-run{1,2,3}.{xml,stdout.txt}` |
| 4 | tester offline `test_phase6_independent_offline.py` | 0 | 66 / 0 / 0 / 0 | `tester-round20/tester-offline.xml` |
| 5 | tester postgres `test_phase6_independent_postgres.py` | 0 | 4 / 0 / 0 / 0 | `tester-round20/tester-postgres.xml` |
| 6 | 最终 aggregate 复跑 | 0 | 与 canonical 一致；cumulative 94,410,660 / acceptance true | `tester-round20/aggregate-final-recomputed.json` |
| 7 | 最终审计聚合器 `verify_final_round20.py` | 0 | 25/25 checks pass；4 项非阻断 findings | `tester-round20/final-audit-round20.json` |

### Findings

**Blocking：无。**

**Non-blocking（不影响 6.1/6.2 判定，建议最终 handoff 前修正/标注）**

1. **P6-R20-SEG4-POSTHOC-REVIEWER-CAUSE-STALE** — segment 4 的
   `window-summary-engineer-posthoc.json` 仍写 “postgres was stopped … by a reviewer-side
   capture/lease check script during reviewer round13”；incident/lineage/handoff 已更正为
   并发 engineer/operator lease recovery + postgres stop，reviewer 不是根因。该 posthoc
   是历史证据不能改写，建议在 incident 或独立 `CORRECTION` 文件中显式写“supersedes the
   posthoc reviewer-cause sentence”，避免读者误读。
2. **P6-R20-LINEAGE-DUPLICATE-SEG4** — lineage `segment_map` 同时存在一个
   `segment_index=4, run_id=null, status=pending_backup_host_recovery` 的占位项和实际的
   `segment_index=4, run_id=703b71af` 中止项；建议删除/标记占位项，确保
   segment_index/run_id 唯一性无歧义。
3. **P6-R20-LINEAGE-DELTA-TEXT** — lineage `delta_explanation` 仍只解释 436→450 的 +14；
   最终 baseline `delta_from_original.events=285`，还包含 segment 4 的 +1 与 segment 5 的
   +270（全部 account.reconciled）。建议把最终 +285 的逐段归因补入 lineage。
4. **P6-R20-BASELINE-ROLE-MISMATCH** — 最终 baseline 文件 `role=next_segment_anchor`、
   `next_segment_index=null`，而 lineage `current_baseline.role=post_acceptance_record`、
   `acceptance_met=true`；建议把 baseline 的 role 元数据对齐为 post-acceptance 记录。

### 未执行项与限制

- 未启动任何新的窗口/分段/沙箱 compose；未操作真实账户/真实资金；未改主机
  WiFi/路由/代理/防火墙；未 commit/push/merge/deploy。
- fresh reviewer 仍 pending（handoff `delivery_status` 明确 `reviewer_pending;
  final_handoff_pending`）；本轮是 tester 最终审计，不替代 fresh reviewer。
- GitHub Actions 未在本地执行；未重跑前端 156 项（本轮范围为 phase 6 窗口/证据/后端）。
- 未直接运行 `run_concurrency_stress.py`（最终轮的 guard 18×3 与全量已覆盖；运行会写入
  工程师 canonical 压力证据路径）。
- 报告顶部 JSON 与全局各节为 round16 历史；round17–20 结论以文末各自段落为准。
  本轮报告追加后 report sha 会变化，最终 handoff 由工程师回填。

### 本轮证据文件（均位于 `artifacts/foundation/phase6/tester-round20/`）

`full-suite.{xml,stdout.txt,exitcode.txt}`、`guard-run{1,2,3}.{xml,stdout.txt}`、
`tester-offline.{xml,stdout.txt}`、`tester-postgres.{xml,stdout.txt}`、
`aggregate-final-recomputed.json`、`aggregate-recompute.stdout.txt`、
`final-audit-round20.json`、`final-audit-round20.stdout.txt`、
`verify_final_round20.py`、`tester-round20-manifest.json`（逐文件 sha256 清单）。

### 冻结与归属

- `authored_by=tester`、`executed_by=tester`、`analyzed_by=tester`；本轮全部命令由
  测试者会话执行，无工程师代跑。
- 仅追加本报告与 `artifacts/foundation/phase6/tester-round20/**`；未修改生产实现、
  既有测试断言、全局 fixture、manifests、handoff/evidence-index、窗口证据、CI 或业务
  契约。
- `workspace_frozen=true`；**overall=pass_for_6.1-6.12**。6.1/6.2 已通过；6.3–6.12
  继续由 round19 final17/manifests 支撑；fresh reviewer 与 final handoff 仍 pending。

## round21 复验：reviewer round14 七项阻断修复（P6-TESTER-ROUND21-FIXES）

```json
{
  "report_id": "P6-TESTER-ROUND21-FIXES",
  "workflow_version": 3,
  "phase": 6,
  "role": "tester",
  "agent_id": "tester-round21 session (child of engineer 231a51ed-bcba-4dd2-afe6-271e45f72164)",
  "round": 21,
  "base_commit": "c6fe450260eeb59d46c9caee591fb1625510b443",
  "branch": "feat/foundation-refactor",
  "source_digest": {
    "implementation_digest": "8c6f86452428c0c6d545283940a24f8bffcb8599906ad55826fb26da2194b5f6",
    "implementation_files": 166,
    "complete_digest_before_tester_tests": "4dec27d885fae2f1a0be8ce4edf7cce76f0615e0d7464fef1e221a02740d16ea",
    "complete_files": 168,
    "complete_digest_after_tester_tests": "a6c493ece2d9fbe687a70550b67dfebe9a71c8ccbbeb3f973949b5e99f2b2a29",
    "tester_offline_sha256": "925078c83514ce90e6cac5499ee9c0cbe09fb3febaf3b3ba977a94ca21c7fbbc",
    "final19": "3501 passed / 0 failed / 0 errors / 0 skipped, exit 0, JUnit timestamp 2026-11-11T17:01:46.340865+08:00"
  },
  "independent_full_suite": {
    "baseline_before_tester_tests": {"tests": 3501, "failures": 0, "errors": 0, "skipped": 0, "exit": 0, "timestamp": "2026-11-11T17:06:48.123737+08:00"},
    "final_with_tester_tests": {"tests": 3503, "failures": 0, "errors": 0, "skipped": 0, "exit": 0, "timestamp": "2026-11-11T17:12:29.124454+08:00"}
  },
  "overall": "blocked",
  "authored_by": "tester (round21 session)",
  "executed_by": "tester (all commands in this round; no engineer proxy run)",
  "analyzed_by": "tester",
  "workspace_frozen": true
}
```

### 结论

**overall = blocked。** 七项 reviewer round14 findings 中六项已验证修复；但
`P6-R14-AGGREGATE-IDENTITY-INPUTS` 的修复引入了新的 blocking 回归：
**新的 fail-closed aggregate 无法复现 canonical final aggregate**。标准命令对真实
canonical 三段返回 `SEGMENT_IDENTITY_MISMATCH:events`，因此无法用修复后的代码得到
94,410,660ms / acceptance_satisfied=true。旧的 `aggregate-final-20261111.json`
（sha `a1c135b7…`，mtime 16:01）是修复前生成的 artifact；新代码未生成新 canonical。
在修复前不能交付/声明通过。

### 七项 reviewer findings 的独立复验

| # | finding | 状态 | 证据 |
| --- | --- | --- | --- |
| 1 | **P6-R14-MONITOR-SYMBOL-EXPOSURE** | **fixed_verified** | 新增真实 service 成交回归（`test_control_views_use_booked_fills_for_avg_price_and_exposure`）：LIMIT 1@200 + 0.25@180 后 `/v1/monitor/snapshot` 返回 `symbol=BTCUSDT`、`quantity=0.25`、`current_price=200`、`exposure=50.00`、`total_exposure=50.00`；未知 mark 时 `current_price/exposure/total_exposure=null`（`test_monitor_snapshot_reports_unknown_mark_as_null_exposure`）。2 passed。 |
| 2 | **P6-R14-ORDER-AVERAGE-FILL-PRICE** | **fixed_verified** | 同上测试：无成交时 `avg_price=null`；0.25@180 后 `avg_price=180`；再成交 0.25@220 后加权 `(45+55)/0.5=200`。 |
| 3 | **P6-R14-AGGREGATE-IDENTITY-INPUTS** | **not closed / blocking** | 重复段、缺失目录在隔离副本上被拒（`DUPLICATE_RUN_ID` / `RUN_DIR_MISSING`），但**真实 canonical 三段**被新 identity 校验误拒：`SEGMENT_IDENTITY_MISMATCH:events`。详见下方 blocking finding。 |
| 4 | **P6-R14-AGGREGATE-DR-INTERNAL-GAP** | **fixed_verified** | 合成 300001ms verified backup gap（无样本命中）→ `dr_internal_breach=true`、`run_valid=false`；工程师 300000ms 边界测试通过。 |
| 5 | **P6-R14-AGGREGATE-STRONG-INVARIANTS** | **fixed_verified** | 隔离副本删除 seq100 `monotonic_ms` → `run_valid=false`、`clock_invalid=true`；seq31 清空 coverage + `PRIVATE_STREAM_COVERAGE_UNPROVEN` → `run_valid=false`、`coverage_proof_missing=true`。 |
| 6 | **P6-R14-PYTHON-FORMAT-GATE** | **fixed_verified** | `black --check --diff --line-length 100 trader/ scripts/phase6_segment_aggregate.py` exit 0（463 files unchanged）；`isort --check-only --diff --profile black trader/ scripts/phase6_segment_aggregate.py` exit 0。 |
| 7 | **P6-R14-CI-ENTRY-COVERAGE** | **fixed_verified** | `.github/workflows/ci-gate.yml` trading-core-gate 已加入 aggregate/observation_only（249）与 observer_relogin/window_gates（250）；`phase6-guard-gate`（Windows）加入 startup_guard（252–270）。五个套件均有显式入口。 |

### Blocking finding（新）

**P6-R21-AGGREGATE-CANONICAL-IDENTITY** — 新 aggregate 的 `_validate_identity` 用
`segment.json.db_identity_hashes`（段前 identity）对比**首个样本**的
`metrics.evidence`，对真实段必然失败：

- validation `7a911829`：segment.json events = `a3446acc…`（441，等于
  `resume-preflight.json`）；首样本 events = `c6ac4685…`（442）。
- segment5 `cc17f251`：segment.json events = `48c7e429…`（451，等于
  `resume-preflight.json`）；首样本 events = `715c39e0…`（452）。
- 原因：resume 后、首个观察样本前发生了一次合法的 `account.reconciled` 事件；段前
  identity 与首样本 identity 因此相差 1。executions/ledger 仍完全一致。
- 标准命令（输出到 tester-round21，不覆盖 canonical）：
  `python scripts/phase6_segment_aggregate.py --original .../efbba899 --segment .../7a911829 --segment .../cc17f251 --out .../tester-round21/aggregate-final-recomputed.json`
  → **exit 2，`{"error": "SEGMENT_IDENTITY_MISMATCH:events"}`，无输出文件**。
- 影响：修复后的 fail-closed aggregate 不能复现 canonical 94,410,660ms /
  acceptance_satisfied=true；reviewer round14 的 G6 identity 门禁仍未真正关闭。
- 修复建议：identity 校验应以 `resume-preflight.json` 的段前 identity 为锚（或允许
  events 为段前 identity 的超集，同时 executions/ledger 精确相等），不要用首样本
  evidence 直接比较；补一个使用真实 `efbba899 + 7a911829 + cc17f251` 三段的 canonical
  回归（调用 CLI/aggregate，断言 94,410,660/acceptance true）。修复后重生成
  `aggregate-final-20261111.json` 并刷新指纹。
- 证据：`tester-round21/aggregate-counterexamples-round21.json` 的
  `canonical_final_aggregate`、`aggregate-recompute.stdout.txt`。

### 五类 reviewer aggregate 反例（隔离副本）

为避免 canonical identity 误拒掩盖规则本身，测试者把 7a911829/cc17f251 复制到
`tester-round21/ce-*`，仅在副本把 segment events identity 对齐到首样本后运行；canonical
文件未改。结果：

| 反例 | 结果 |
| --- | --- |
| duplicate segment | `AggregateInputError: DUPLICATE_RUN_ID` |
| nonexistent required segment | `AggregateInputError: RUN_DIR_MISSING` |
| missing monotonic (seq100) | `run_valid=false`, `clock_invalid=true`（qualified 73,604,050） |
| coverage unproven during not-ready (seq31) | `run_valid=false`, `coverage_proof_missing=true` |
| DR verified gap 300001ms (no sample hit) | `dr_internal_breach=true`, `run_valid=false` |

五类规则全部被拒绝；但真实 canonical 段在未调整的 identity 校验下被误拒（blocking）。

### 全量 / guard / 格式 / 测试

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 工程师 final19 | 3501 / 0 / 0 / 0，exit 0；timestamp `2026-11-11T17:01:46.340865+08:00` | `engineer-full-suite-final19.*` |
| 独立基线全量（测试者新增用例前） | 3501 / 0 / 0 / 0，exit 0；timestamp `2026-11-11T17:06:48.123737+08:00` | `tester-round21/full-suite-baseline.*` |
| 独立最终全量（含新增 2 例） | **3503 / 0 / 0 / 0**，exit 0；timestamp `2026-11-11T17:12:29.124454+08:00` | `tester-round21/full-suite-final.*` |
| guard 18 例 ×3 | 18/18 ×3，exit 0（21.85s / 21.68s / 21.78s） | `tester-round21/guard-run{1,2,3}.*` |
| tester offline | 68 / 0 / 0 / 0（原 66 + 2 个 control-view 回归） | `tester-round21/tester-offline-68.*` |
| tester postgres | 4 / 0 / 0 / 0（task DB） | `tester-round21/tester-postgres.*` |
| aggregate tests | 22 / 0 / 0 / 0 | `tester-round21/aggregate-tests.*` |
| black / isort | black exit 0（463 files unchanged）；isort exit 0 | `tester-round21/black-final.stdout.txt`、`isort-final.stdout.txt` |

### 指纹 / 文档 / 非阻断项

- final19 manifests：impl 166/`8c6f8645…`、complete 168/`4dec27d8…`，逐文件与
  handoff/index 一致。
- 测试者新增 2 个测试后：impl 仍为 166/`8c6f8645…`；complete 重算为
  168/`a6c493ec…`（唯一差异 `trader/tests/test_phase6_independent_offline.py`
  `eb643f0a…` → `925078c8…`）。**工程师必须重新生成 complete manifest 并回填
  handoff/index。**
- 文档 13.17.1/8.5/5.2.1 已更新为 94,410,660ms/acceptance true/segment5/segment4
  incident；baseline/lineage 当前 sha `ac406c0c…`/`9be523c0…` 与文档一致。
- **P6-R21-RUNBOOK-FINAL18-STALE（non-blocking）**：runbook §5.2.1 仍写 “跑 final18
  （3501/0/0/0）”，当前门禁是 final19（同为 3501）；建议更新指针。
- **P6-R21-COMPLETE-MANIFEST-STALE（non-blocking，需工程师更新）**：如上述 complete
  digest 因测试者新增用例变化；impl 不受影响。
- round20 的四项非阻断项已复核修复：baseline role=`post_acceptance_record`、lineage
  segment4 重复占位已移除、delta_explanation 已含 +285、POSTHOC-CORRECTION.md 已加。

### 未执行项与限制

- 未启动任何窗口/分段/沙箱 compose；未操作真实账户/真实资金；未改主机 WiFi/路由/
  代理/防火墙；未 commit/push/merge/deploy。
- fresh reviewer 仍 pending（reviewer round15 需在修复 blocking finding 后创建）；
  本轮 tester 不替代 reviewer。
- GitHub Actions 远程工作流未执行；只本地跑相同命令并核对 CI 入口。
- 未重跑前端 156 项；未直接运行 `run_concurrency_stress.py`（会覆盖工程师 canonical
  压力证据；guard 18×3 与全量已覆盖）。
- 报告顶部 JSON 与全局各节为 round16 历史；round17–21 结论以文末各自段落为准。
  本轮报告追加后 report sha 变化，最终 handoff 由工程师回填。

### 本轮证据文件（均位于 `artifacts/foundation/phase6/tester-round21/`）

`full-suite-baseline.{xml,stdout.txt,exitcode.txt}`、
`full-suite-final.{xml,stdout.txt,exitcode.txt}`、`guard-run{1,2,3}.{xml,stdout.txt}`、
`tester-offline-68.{xml,stdout.txt}`、`tester-postgres.{xml,stdout.txt}`、
`aggregate-tests.{xml,stdout.txt}`、`control-view-regression.{xml,stdout.txt}`、
`aggregate-final-recomputed.json`（失败，无输出）、`aggregate-recompute.stdout.txt`、
`aggregate-counterexamples-round21.{json,stdout.txt}`、
`verify_aggregate_counterexamples.py`、`manifest-regen-{impl,complete}-after-tester-tests.json`、
`black-final.stdout.txt`、`isort-final.stdout.txt`、`tester-round21-manifest.json`。

### 冻结与归属

- `authored_by=tester`、`executed_by=tester`、`analyzed_by=tester`；本轮全部命令由
  测试者会话执行，无工程师代跑。
- 仅修改 `trader/tests/test_phase6_independent_offline.py`（新增 2 个真实 service
  control-view 回归）并追加本报告与 `artifacts/foundation/phase6/tester-round21/**`；
  未修改生产实现、既有测试断言、全局 fixture、manifests、handoff/evidence-index、
  窗口证据、CI 或业务契约。
- `workspace_frozen=true`；**overall=blocked**，待 aggregate identity 修复、canonical
  aggregate 重新生成并通过测试者复验后，才能进入 fresh reviewer/final handoff。

## round22 复验：aggregate identity 修复与 canonical 复现（P6-TESTER-ROUND22-IDENTITY-FIX）

```json
{
  "report_id": "P6-TESTER-ROUND22-IDENTITY-FIX",
  "workflow_version": 3,
  "phase": 6,
  "role": "tester",
  "agent_id": "tester-round22 session (child of engineer 231a51ed-bcba-4dd2-afe6-271e45f72164)",
  "round": 22,
  "base_commit": "c6fe450260eeb59d46c9caee591fb1625510b443",
  "branch": "feat/foundation-refactor",
  "source_digest": {
    "implementation_digest": "fd1d8b32b85d40bf7e7377d51c292ec8a5f2798dafe403956aa24f8b3c1b5d78",
    "implementation_files": 166,
    "complete_phase_digest": "f2d69dd72fb541228e23ba2199220d3d23c7f52090fa8f925a69038c1836a8fd",
    "complete_phase_files": 168,
    "final20": "3504 passed / 0 failed / 0 errors / 0 skipped, exit 0, JUnit timestamp 2026-11-11T17:19:21.171438+08:00"
  },
  "independent_full_suite": {
    "tests": 3504, "failures": 0, "errors": 0, "skipped": 0, "exit": 0,
    "timestamp": "2026-11-11T17:24:10.031626+08:00"
  },
  "canonical_aggregate": {
    "standard_command_exit": 0,
    "cumulative_qualified_ms": 94410660,
    "remaining_ms": 0,
    "acceptance_satisfied": true,
    "dr_internal_breach": false,
    "dr_head_breach": false,
    "dr_tail_breach": true,
    "canonical_file_sha256": "987a983c9b57c5a07da47ad522553ee3e7eb8250012a971877d1ce6f61a23520",
    "recomputed_file_sha256": "5fe022724d9a20e506ce2b40dbaee0325c40249a4f264cf75c1f501e06b0c872",
    "semantic_equality": true,
    "byte_difference": "only overlap_ms dict key order"
  },
  "overall": "pass_for_6.1-6.12",
  "authored_by": "tester (round22 session)",
  "executed_by": "tester (all commands in this round; no engineer proxy run)",
  "analyzed_by": "tester",
  "workspace_frozen": true
}
```

### 结论

**overall = `pass_for_6.1-6.12`。** round21 的 blocking
`P6-R21-AGGREGATE-CANONICAL-IDENTITY` 已修复并通过独立复验：

- 标准命令（original + validation + segment5，三段 `--expect-digest`）**exit 0**；
  cumulative **94,410,660ms**、remaining 0、`qualified_satisfied=true`、
  `all_runs_valid=true`、`dr_internal_breach=false`、`acceptance_satisfied=true`。
- `dr_tail_breach=true` 来自原始窗口尾部；按 RFC tail 规则不使整段 invalid，canonical
  采用保守零计入，符合既有口径。
- 五类 reviewer 反例仍全部被拒；canonical 三段不再被误拒。
- final20 与独立全量均 **3504 / 0 / 0 / 0，exit 0**；guard 18×3、black/isort 均通过。
- 无 blocking findings；两项非阻断（aggregate 输出键顺序、runbook final19 指针）见下。

### 1) canonical identity 修复复验

| 段 | 段前 identity（= resume-preflight） | 首样本 events | executions/ledger | 结论 |
| --- | --- | --- | --- | --- |
| validation `7a911829` | events `a3446acc…`，count 441 | 442（`c6ac4685…`） | 精确相等（`4cacdd5b…`/`6f15c986…`） | append-only +1，合法 |
| segment5 `cc17f251` | events `48c7e429…`，count 451 | 452（`715c39e0…`） | 精确相等（`4cacdd5b…`/`6f15c986…`） | append-only +1，合法 |

- `segment.json.db_identity_hashes` 与 `resume-preflight.json.identity_hashes` 完全相等；
- 首样本 executions/ledger hash 与段前 identity 精确相等；events 只比较 count（首样本
  ≥ 段前），不再比较 events hash；
- fail-closed 边界独立验证：缺 `resume-preflight.json` → `RESUME_PREFLIGHT_MISSING`；
  缺 `counts.events` → `SEGMENT_EVENT_COUNT_MISSING`；首样本 events 回退
  （451→450）→ `SEGMENT_EVENT_COUNT_REGRESSION:451->450`；segment identity 与
  preflight 不一致 → `SEGMENT_IDENTITY_PREFLIGHT_MISMATCH`。

### 2) canonical aggregate 标准命令

```text
python scripts/phase6_segment_aggregate.py \
  --original artifacts/foundation/phase6/windows-aborted/20261109T011441Z-efbba899 \
  --segment artifacts/foundation/phase6/windows/20261110T030232Z-7a911829 \
  --segment artifacts/foundation/phase6/windows/20261110T075417Z-cc17f251 \
  --expect-digest 20261109T011441Z-efbba899=6498dad2… \
  --expect-digest 20261110T030232Z-7a911829=2c3693b7… \
  --expect-digest 20261110T075417Z-cc17f251=98f18087… \
  --out artifacts/foundation/phase6/tester-round22/aggregate-final-recomputed.json
```

exit 0；逐字段与 canonical `aggregate-final-20261111.json`（sha `987a983c…`）一致：
original 19,874,125ms + validation 872,313ms + segment5 73,664,222ms = **94,410,660ms**，
remaining 0，acceptance true；original `dr_tail_breach=true`、internal/head false；
validation/segment5 valid、internal false。

### 3) 五类 reviewer 反例仍被拒绝

| 反例 | 独立结果 |
| --- | --- |
| duplicate segment | `DUPLICATE_RUN_ID` |
| nonexistent required segment | `RUN_DIR_MISSING` |
| missing monotonic（seq100） | `run_valid=false`、`clock_invalid=true` |
| coverage unproven during not-ready（seq31） | `run_valid=false`、`coverage_proof_missing=true` |
| DR verified gap 300001ms（无样本命中） | `dr_internal_breach=true`、`run_valid=false` |

canonical 三段不再被 identity 检查误拒；上述反例均使用隔离副本/合成目录，canonical
证据未改写。

### 4) 全量 / guard / 格式 / 指纹

| 检查 | 结果 |
| --- | --- |
| final20 | 3504 / 0 / 0 / 0，exit 0，timestamp `2026-11-11T17:19:21.171438+08:00` |
| 测试者独立全量 | **3504 / 0 / 0 / 0，exit 0**，timestamp `2026-11-11T17:24:10.031626+08:00` |
| guard 18 例 ×3 | 18/18 ×3，exit 0（21.64s / 21.80s / 21.76s） |
| tester postgres | 4 / 0 / 0 / 0 |
| black / isort | black exit 0（463 files unchanged）；isort exit 0 |
| manifests | impl 166/`fd1d8b32…`、complete 168/`f2d69dd7…`；handoff/index 一致 |
| CI | trading-core-gate 仍含 aggregate/observation_only/observer_relogin/window_gates；Windows `phase6-guard-gate` 含 startup_guard |

round21 其余六项 finding（monitor symbol、avg_price、DR internal gap、strong invariants、
black/isort、CI 入口）在本轮全量/定向中保持 fixed_verified；round20 四项非阻断项
（baseline role、lineage 重复段 4、+285 delta、POSTHOC-CORRECTION）已复核为已修复。

### Findings

**Blocking：无。**

**Non-blocking**

1. **P6-R22-AGGREGATE-OUTPUT-ORDER**：canonical 文件 sha `987a983c…` 本身有效，且
   测试者复跑输出与其 JSON 语义完全一致（含 cumulative/acceptance/segment digests）；
   但复跑文件 sha 为 `5fe0227…`，差异仅是 `overlap_ms` 字典的键顺序（集合迭代顺序）。
   同一输入可能得到不同文件哈希，削弱 canonical 文件 sha 的可复现性。建议在
   `aggregate()` 输出前对 `overlap_ms` 键排序或 `json.dumps(..., sort_keys=True)`，
   使 canonical sha 与命令输出可字节复现。
2. **P6-R22-RUNBOOK-FINAL19-STALE**：runbook §5.2.1 当前写 “final19（3501/0/0/0）”，
   最终门禁已是 final20（3504/0/0/0）；建议更新指针。

### 未执行项与限制

- 未启动任何窗口/分段/沙箱 compose；未操作真实账户/真实资金；未改主机 WiFi/路由/
  代理/防火墙；未 commit/push/merge/deploy。
- fresh reviewer（round15）仍 pending；final handoff 仍 pending。
- GitHub Actions 远程工作流未执行；未重跑前端 156 项；未直接运行
  `run_concurrency_stress.py`（guard 18×3 与全量已覆盖，且会覆盖工程师 canonical 压力证据）。
- 报告顶部 JSON 与全局各节为 round16 历史；round17–22 结论以文末各自段落为准。
  本轮报告追加后 report sha 会变化，最终 handoff 由工程师回填。

### 本轮证据文件（均位于 `artifacts/foundation/phase6/tester-round22/`）

`full-suite.{xml,stdout.txt,exitcode.txt}`、`guard-run{1,2,3}.{xml,stdout.txt}`、
`tester-postgres.{xml,stdout.txt}`、`aggregate-final-recomputed.json`、
`aggregate-recompute.stdout.txt`、`round22-verification.{json,stdout.txt}`、
`verify_round22.py`、`black.stdout.txt`、`isort.stdout.txt`、`tester-round22-manifest.json`。

### 冻结与归属

- `authored_by=tester`、`executed_by=tester`、`analyzed_by=tester`；本轮全部命令由
  测试者会话执行，无工程师代跑。
- 仅追加本报告与 `artifacts/foundation/phase6/tester-round22/**`；未修改生产实现、
  既有测试断言、全局 fixture、manifests、handoff/evidence-index、窗口证据、CI 或业务
  契约。
- `workspace_frozen=true`；**overall=pass_for_6.1-6.12**。fresh reviewer 与 final
  handoff 仍 pending。

## round23 最终复验：aggregate 输出确定性与 runbook 指针（P6-TESTER-ROUND23-OUTPUT-DETERMINISM）

```json
{
  "report_id": "P6-TESTER-ROUND23-OUTPUT-DETERMINISM",
  "workflow_version": 3,
  "phase": 6,
  "role": "tester",
  "agent_id": "tester-round23 session (child of engineer 231a51ed-bcba-4dd2-afe6-271e45f72164)",
  "round": 23,
  "base_commit": "c6fe450260eeb59d46c9caee591fb1625510b443",
  "branch": "feat/foundation-refactor",
  "source_digest": {
    "implementation_digest": "9fe09e9b9d8f7787b130e89dceba5e3787a345f314f9a780c062d49556d70c99",
    "implementation_files": 166,
    "complete_phase_digest": "350649a9d3b7fee7c3ffa890d425d64f96f8a60eea028919e2a133945e003c07",
    "complete_phase_files": 168,
    "final21": "3504 passed / 0 failed / 0 errors / 0 skipped, exit 0, JUnit timestamp 2026-11-11T17:31:18.292209+08:00"
  },
  "independent_full_suite": {
    "tests": 3504, "failures": 0, "errors": 0, "skipped": 0, "exit": 0,
    "timestamp": "2026-11-11T17:35:41.915985+08:00"
  },
  "canonical_aggregate": {
    "canonical_sha256": "f146b9c36dee26b03b290a71307f9277982b5f3462a5afebc359f11641bfbf05",
    "repro_1_sha256": "f146b9c36dee26b03b290a71307f9277982b5f3462a5afebc359f11641bfbf05",
    "repro_2_sha256": "f146b9c36dee26b03b290a71307f9277982b5f3462a5afebc359f11641bfbf05",
    "byte_identical": true,
    "cumulative_qualified_ms": 94410660,
    "remaining_ms": 0,
    "acceptance_satisfied": true,
    "dr_internal_breach": false,
    "dr_tail_breach": true
  },
  "overall": "pass_for_6.1-6.12",
  "authored_by": "tester (round23 session)",
  "executed_by": "tester (all commands in this round; no engineer proxy run)",
  "analyzed_by": "tester",
  "workspace_frozen": true
}
```

### 结论

**overall = `pass_for_6.1-6.12`。** round22 的两个非阻断项均已修复并独立复验：

- **P6-R22-AGGREGATE-OUTPUT-ORDER → fixed_verified**：标准三段命令（带三段
  `--expect-digest`）连续运行两次，输出与 canonical
  `aggregate-final-20261111.json` **字节一致**，三者 sha256 均为
  `f146b9c36dee26b03b290a71307f9277982b5f3462a5afebc359f11641bfbf05`；
  `cmp` 两次运行与 canonical 均为 0；`overlap_ms` 键已排序
  （`balance_unproven, dr_stale, not_ready, reconciliation_stale, risk_snapshot_stale,
  unresolved`）。语义仍为 **94,410,660ms**、remaining 0、`acceptance_satisfied=true`，
  `dr_internal_breach=false`、`dr_tail_breach=true`（原窗口尾部，tail 规则不 invalid）。
- **P6-R22-RUNBOOK-FINAL19-STALE → fixed_verified**：runbook §5.2.1 已写
  `final20（3504/0/0/0）`，不再引用 final19。

### 全量 / guard / 格式 / 指纹

| 检查 | 结果 |
| --- | --- |
| final21 | 3504 / 0 / 0 / 0，exit 0，timestamp `2026-11-11T17:31:18.292209+08:00` |
| 测试者独立全量 | **3504 / 0 / 0 / 0，exit 0**，timestamp `2026-11-11T17:35:41.915985+08:00` |
| guard 18 例 ×3 | 18/18 ×3，exit 0（21.88s / 21.70s / 22.13s） |
| tester postgres | 4 / 0 / 0 / 0 |
| black / isort | black exit 0（463 files unchanged）；isort exit 0 |
| manifests | impl 166/`9fe09e9b…`、complete 168/`350649a9…`；handoff/index 一致 |
| final21 指针 | handoff/index 的 `complete_suite` 均指向 `engineer-full-suite-final21.*`、3504/0/0/0、timestamp `2026-11-11T17:31:18.292209+08:00` |
| CI | trading-core-gate 仍含 aggregate/observation_only/observer_relogin/window_gates；Windows `phase6-guard-gate` 含 startup_guard |

round21 六项、round22 identity 修复、round20 四项非阻断项均保持已修复/已验证状态；
本轮未发现新的 blocking 或 non-blocking findings。

### 未执行项与限制

- 未启动任何窗口/分段/沙箱 compose；未操作真实账户/真实资金；未改主机 WiFi/路由/
  代理/防火墙；未 commit/push/merge/deploy。
- fresh reviewer（round15）仍 pending；final handoff 仍 pending。
- GitHub Actions 远程工作流未执行；未重跑前端 156 项；未直接运行
  `run_concurrency_stress.py`（guard 18×3 与全量已覆盖，且会覆盖工程师 canonical 压力证据）。
- 报告顶部 JSON 与全局各节为 round16 历史；round17–23 结论以文末各自段落为准。
  本轮报告追加后 report sha 会变化，最终 handoff 由工程师回填。

### 本轮证据文件（均位于 `artifacts/foundation/phase6/tester-round23/`）

`full-suite.{xml,stdout.txt,exitcode.txt}`、`guard-run{1,2,3}.{xml,stdout.txt}`、
`tester-postgres.{xml,stdout.txt}`、`aggregate-repro-{1,2}.{json,stdout.txt}`、
`black.stdout.txt`、`isort.stdout.txt`、`tester-round23-manifest.json`。

### 冻结与归属

- `authored_by=tester`、`executed_by=tester`、`analyzed_by=tester`；本轮全部命令由
  测试者会话执行，无工程师代跑。
- 仅追加本报告与 `artifacts/foundation/phase6/tester-round23/**`；未修改生产实现、
  既有测试断言、全局 fixture、manifests、handoff/evidence-index、窗口证据、CI 或业务
  契约。
- `workspace_frozen=true`；**overall=pass_for_6.1-6.12**。fresh reviewer 与 final
  handoff 仍 pending。
