# CI/CD 改进 RFC

日期：2026-11-04（Asia/Shanghai）。审查对象：本地 `feat/foundation-refactor`
的 `c6fe450260eeb59d46c9caee591fb1625510b443`。这是 CI/CD 设计与实施清单，
不代表已有远端验收，不授权生产部署、真实资金、对现有主机/数据库/卷的写操作，
也不改变基础重构的阶段状态。

## 1. 结论与范围

当前仓库已有较完整的 **CI + 本地演练基础**，但尚未形成可审计的持续交付：
后端没有版本化镜像/发布产物，GitHub Actions 没有发布、部署、回滚工作流，Compose
启动仍属人工操作。现有强项是 P0 回归、真实 PG 集成测试、凭据隔离、执行租约、
`/health/trading`、异机备份/恢复和沙箱演练；应先复用这些安全边界，而不是把“全自动
部署到生产”当成第一步。

目标按顺序为：

1. 让合并门禁与实际执行项一致，交易核心不变量及关键持久化测试不漏跑；
2. 一次构建不可变、可追踪、可启动验证的后端镜像，随后部署同一产物；
3. 在独立 Compose Demo 环境中完成预检、发布、观察、回滚闭环；Demo 是本项目
   最高验收环境，不要求真实 testnet；
4. 真实生产/真实资金仅作为后续单独授权项目，保留人工批准，不放松现有
   `BINANCE_ENV_NOT_SANDBOX` 防线，也不以 CI 绿灯或旧演练产物代替上线验收。

不在本次改动中替换 Docker Compose、引入 Kubernetes、设计实盘多活、放宽 KillSwitch、
自动修改事件历史/成交/账本、自动批准 AI 策略或自动决定实盘启停。前端已有构建但
尚无运行时服务；P2 应先确定静态托管/反向代理与新旧 API 路由，再纳入同一发布。

## 2. 审查依据与当前状态

### 2.1 当前各环节

| 环节 | 已有实现 | 边界 |
| --- | --- | --- |
| 触发 | PR 到 `main`、push 到 `main`，同 ref 并发取消旧运行，`.github/workflows/ci-gate.yml:3` | 不覆盖发布/部署，也没有定期故障演练 |
| 后端静态检查 | 3 个关键测试模块导入、Black/isort、Core 无 IO AST 检查，`.github/workflows/ci-gate.yml:18` | 无 Python 类型检查，架构检查不能代替行为测试 |
| 后端测试 | P0 与 control 选择集、交易核心/研究测试，`.github/workflows/ci-gate.yml:75` | 静态清单选择，非全量发现 |
| PG 测试 | PostgreSQL 16 service + 显式 DSN + 17 个文件，`.github/workflows/ci-gate.yml:123` | 未检查 skip 数量；PG 初始化/迁移非完整发布验证 |
| 前端 | `npm ci`、typecheck、Vitest、Vite build，`.github/workflows/ci-gate.yml:291` | 不执行 lint、`test:coverage`；`dist` 不作为发布产物；无 Compose 前端服务 |
| 后端构建 | `Dockerfile` 使用 `python:3.12-slim`，安装 runtime requirements、复制仓库并以非 root 运行，`Dockerfile:1` | CI 不构建镜像，无 registry、digest、版本元数据、SBOM/镜像扫描 |
| 部署 | Compose 含 postgres/control/execution/research/history/backup/alert-sink，`compose.yml:1`；隔离启动 smoke 可实际 build/up，`scripts/compose_smoke.py:198` | 该 smoke 不在 Actions；启动手册仍是人工 `up -d`，`docs/PHASE5_OPERATIONS_RUNBOOK.md:45` |
| 运维安全 | `/health/trading` 聚合租约/账户/DR；非沙箱 Execution 拒绝启动，`trader/runtime/credentials.py:103`；备份 RPO<=300s、恢复 RTO<=3600s，`docs/PHASE5_OPERATIONS_RUNBOOK.md:86` | 这些强门禁尚未串成 release gate；不可把健康或历史证据当作发布成功 |

静态统计：`trader/tests` 有 244 个 `test_*.py` 文件，而 CI 显式引用 72 个去重
文件。该数字说明 **测试文件选择不完整，不是 29.5% 的代码覆盖率，也不能据此断言
每个未列文件都完全没有被间接覆盖**。OMS 幂等、订单域、事件回放、候选晋级/影子
验证、风险干预、WS/REST 对账、前端浏览器 E2E 等仍有补强空间。

GitHub 查询（2026-11-04）显示：当前远端最新 `main` run 为 #336，提交
`a1319881`，2026-11-02 10:36–10:40（北京时间）成功；其中只有 8 个 Job，无
Compose Configuration Job，故它与当前本地流水线版本不完全相同。不能把此成功结论
套用到上述本地 commit。远端规则 <https://github.com/xiaomiaoalex/quant_trading_system/rules/13369928>
目前仅要求 `Import Gate`、`P0 Regression Tests`、`Control Gate`，要求 0 个审批，
且未启用 strict up-to-date checks。普通 branch protection 查询权限不足，本方案不声称
没有另外的保护规则。

文档间存在时效差：`docs/UNIFIED_TRADING_RUNBOOK.md:209` 仍称 24h 窗口不能
重跑验收，而 `docs/UNIFIED_TRADING_ACCEPTANCE.md:3` 记录 2026-11-01 至 11-02
的 25.18h Demo 窗口 `ok=true`、`ready_ratio=0.6252`。按 2026-11-04 更新后的验收
口径，Demo E2E 与观察窗口满足本阶段外部验收，不再要求真实 testnet。原 artifacts
不在仓库，本 RFC 不复验历史结果，也不将历史 Demo 记录自动视为后续版本验收。

### 2.2 主要薄弱点与优先级

| 优先级 | 薄弱点 | 针对性改进 |
| --- | --- | --- |
| P0 | 交易安全相关文件可不在 CI 中执行；API 测试只用 `-k` 选择集 | 标记/清单驱动的完整发现 + CI 零选择遗漏校验；优先纳入 OMS、风险、恢复、回放、AI 门禁 |
| P0 | PG 不可用/准备失败可转为 skip，形成假绿 | 必跑任务使用 `REQUIRE_POSTGRES=1`，连接/schema/驱动失败即 fail，JUnit 强制 PG skipped=0 |
| P0 | 部分高风险 Job 不是远端必需检查，缺少独立汇总 gate | 新增 `CI Required` Job 覆盖所有 PR 必需 Job，ruleset 只依赖汇总 gate；strict 同步分支、至少 1 名非作者审核 |
| P0 | 无后端镜像构建/启动测试，前端产物没有部署路径 | 按 SHA 构建一次镜像、推送 digest；隔离 Compose 实际启动并验证健康/负向凭据/告警 |
| P0 | `docker compose up -d` 与 `qts-runtime:local` 不能证明线上版本可复现/回退 | 部署固定镜像 digest，生成 release manifest；记录上一 digest 和 schema 版本，禁止 mutable `latest` |
| P1 | CI/镜像的 Python 与 apt/PyPI 依赖大量浮动，重复安装耗时且环境可漂移 | 生成 runtime/CI constraints 或 lock（含哈希）；缓存 pip/npm；Python minor 保留既有补丁策略，使用镜像 digest 固定发布基础层 |
| P1 | 配置散落环境变量；有 DB/alert 默认口令，非敏感配置与 secrets 边界需发布级校验 | 非敏感 typed config + env-specific Compose overlay；secret 仅文件/管理器注入；启动前 fail-closed 校验 |
| P1 | 旧版→新版→旧版迁移/事件兼容和恢复没有统一发布验收 | 发布副本上执行 expand-only 迁移、旧版重启兼容、回放/守恒/迁移校验和测试；不可逆迁移走专项方案 |
| P1 | 沙箱 E2E、24h 观察、租约/崩溃/DR 演练未成为发布门禁 | 定时/候选发布手动触发，产物与 commit/digest/环境绑定；失败阻断晋级，旧证据不可复用 |
| P1 | 单测只清理部分环境变量，没有强制禁网 | 普通单测/P0 禁止公网 socket，PG/本地 HTTP 独立放行 loopback，外部网络仅显式授权任务 |
| P2 | 无 Python 覆盖率、前端 lint/coverage 未进 CI、无 JUnit/安全供应链报告 | 先报告后设基线，覆盖关键风险路径；补充 lint、超时、JUnit、SBOM、扫描与权限加固 |

另有两个应立即修复的验证盲点：`scripts/summarize_observation.py:94` 只记录
`ready_ratio`，`:128` 的 blocker 没有将其纳入失败条件，也未为 outbox 积压、未入账
成交、对账年龄设统一阈值。`scripts/compose_smoke.py:293` 断言 HTTP 语义，但
`:376` 的 passed 没有要求每个容器 health 均为 `healthy`；隔离启动不等于健康启动。
修复时先补失败测试，不能把阈值偷偷改成 fail-open。`rules/L1_TECH_STACK.md:1`
仍写固定 Python 3.12.5，与当前 3.12 minor 策略不一致，应同步规则事实。

## 3. 目标流水线与设计决策

### 3.1 分层而非一条长串

```text
PR: format/type/lint + 架构/契约 + P0/OMS/风控单元测试
                 + 交易核心/研究测试 + PG + 前端 ──> CI Required ──> 审核合并

main: 上述测试 + 构建一次后端镜像 + 隔离 Compose smoke
      ──> 推送 digest / SBOM / manifest ──> 可发布候选（不自动部署）

Demo 候选或定时任务: 独立沙箱 E2E、故障/恢复演练、24h 观察
      ──> 环境绑定证据 ──> 人工批准发布窗口

受保护环境: 部署前检查 -> 新风险关闭 -> 迁移/兼容验证 -> 同 digest 启动
      -> 对账/恢复/健康观察 -> 提交发布；失败则保持关闭新增风险并执行回滚手册
```

- **测试分层**：保留快速 P0 先行反馈，但不以 P0 取代全域离线测试。将测试标记为
  `unit`、`postgres`、`sandbox`、`ops`、`slow`、`network`；普通 `unit` 必须无网络。
  使用集中 marker 清单和文件清单校验脚本，保证 `pytest --collect-only` 的离线集合
  与 CI 选择一致；禁止通过新增排除目录或宽泛 `-k` 悄悄缩小范围。PG 独立 job 使用
  显式 DSN，并将 `skipped>0` 视为失败。网络/沙箱标记默认拒绝，需显式 workflow opt-in。
  已有真实 OMS 幂等、终态、风险拒绝测试（如
  `trader/tests/test_oms_callback_fill_idempotency.py:21`、
  `trader/tests/test_order_domain.py:69`、`trader/tests/test_oms_pretrade_risk_gate.py:79`）
  应直接加入 P0，而非仅依赖同领域 P0 测试间接覆盖；API 应运行完整风险状态用例，
  不只选择参数校验。单元/P0 使用 socket 禁网，PG/本地 HTTP 单独管理 loopback；
  可选 VectorBT 等研究依赖单独分组，不允许必跑用例静默 skip。
- **质量门禁**：P0 覆盖重复/乱序成交、CAS 合法/非法迁移、终态不可回退、同订单并发、
  WS/REST 对账、降级/KillSwitch、余额/预约守恒、重启不重复发送；交易核心与 Research
  覆盖回测/实盘风险口径、影子运行、候选晋级、HITL/AI 禁止直接执行。新增风险行为
  必须增加正反向和故障路径测试。前端增加关键页面 blocked/degraded/确认失败回滚及
  API 契约测试，浏览器 E2E 以 staging 的脱敏假数据运行。
- **覆盖率**：CI 先产出行/分支报告与 JUnit，确认稳定基线后再设阈值；优先对
  `trader/core`、`trader/trading`、OMS/风控/恢复设置关键模块门槛，并对变更代码设
  覆盖率门禁。不能用一个全仓数字掩盖核心缺口。前端已有 80% 配置，改用
  `npm run test:coverage` 后才实际执行，随后纳入质量检查。
- **不可变产物**：镜像标签为 commit SHA，部署使用 `ghcr.io/<owner>/<image>@sha256:<digest>`；
  manifest 记录 commit、tree dirty=false、workflow run、镜像 digest、runtime/CI lock hash、
  schema/checksum、前端 artifact hash、测试结果。不得在生产机上重新 checkout/build。
  `QTS_DEPLOYMENT_ID` 保持策略实例语义，另增只读 `QTS_RELEASE_ID` 用于日志、证据、
  健康信息关联；不得把它当作新的执行 owner 或改变租约协议。
- **部署方式**：沿用单机 Compose，不引入第二套调度系统。基础 Compose 保留本地 build；
  `deploy/compose.demo.yml` 只引用已验证 digest，不提供仓库源码 build。每次发布
  使用独立、已批准项目/卷/端口；绝不在 CI 使用 `down -v`，不接触既有 PG。对现有环境
  的切换与历史数据处理必须单独演练并授权。
- **配置**：提交 `deploy/demo.env.example`、非敏感 typed config schema 和渲染后脱敏
  快照；实际 secrets 仅通过 Docker secrets/受控 secret manager 注入。Control、Research、
  History 禁止交易所写凭据；Execution 独立最小权限密钥。启动前检查
  `QTS_AUTH_REQUIRED=1`、非空密码哈希、非默认 PG/告警密钥、专用沙箱账户、symbol/
  instrument allowlist、策略 warmup、外部告警、有效 DR 证据、RPO/RTO 与备份异机证明。
  环境配置与 release digest 绑定；配置变更走同一 PR、测试和审计路径。
- **最小权限**：CI 默认 `contents: read`，发布单独 `packages: write`，部署仅受保护
  environment；PR/fork 不得继承部署 secrets。优先 OIDC/短期凭证或受控 self-hosted
  runner，避免长期 SSH 私钥进入 Actions secrets；部署 runner 不共享开发数据库/卷。
  Actions/基础镜像逐步 pin 到已验证 commit/digest，并保留升级审计。

### 3.2 发布、停止与回滚

当前 Execution 要求 sandbox，因此第一版只打通 **隔离 Demo 发布**，不伪装成生产方案。
真实生产的启用需要单独的环境拓扑、人工权限、资金风险预算和实盘演练验收。

1. 选择已构建并验证的 digest，人工填写变更、风险、schema 兼容性、批准人及回滚目标；
2. 检查目标 compose project/DB/卷身份、当前执行 owner、待处理命令/未知成交、外部告警、
   备份和 DR 证据。目标/证据不匹配则拒绝发布；
3. 先通过既有控制入口禁止新增风险，保留查询、撤单和按策略允许的风险降低动作；
   **不得直接写 KillSwitch 投影或绕过 Risk/Policy/Core**。等待允许的命令收敛，不能
   假定挂单/未知结果已经安全消失；
4. 在隔离数据副本执行迁移、旧版重启读取、事件回放/投影重建和六表守恒校验。普通发布
   仅允许向后兼容 expand-only 迁移；旧版不兼容时停止自动回滚，转人工向前修复或专项
   恢复。`trader/trading/migrations.py` 已提供校验和/过新版本拒绝，应纳入 gate；
5. 以固定 digest 启动同一套服务，恢复前重新 REST 对账并处理 WS/REST 并发；只有
   `/health/trading=200`、单一有效 owner/fencing token、无未知命令/差异、账务守恒、
   无重复发送、告警/心跳/备份正常，才可提交发布。启动 readiness 前不得打开新风险；
6. 观察窗口沿用 24h，并补齐 ready_ratio/outbox/未入账成交/对账年龄阈值；初始阈值由
   历史沙箱基线审批确定，不允许以“暂时没有异常”自动放行。测试候选的 E2E、主动重连、
   runtime 重启、崩溃/租约 fencing、恢复证据须绑定当前 digest 和窗口；
7. 回滚分为：**发布前**保持原版本并关闭新增风险；**启动失败且 schema 兼容**固定旧
   digest 重启，再对账验证，绝不重复发送；**事件/账务/风控语义异常**立即禁止新增风险，
   人工核对交易所与 append-only 事实，不通过删事件、改余额或撤销历史“回滚”；**数据
   灾难**按已验证 DR 恢复到独立环境，经 RPO/RTO、事实摘要和守恒校验后人工切换。

模型/策略回滚是独立于代码镜像的业务变更：`scripts/model_rollback_manager.py` 不应
被当作运行系统回滚器。复用候选/影子/人工审批/暂停接口，固定已验证策略 artifact 和
配置版本；不能以改 registry 文件替代停止信号、预算重估、在途订单处理及审计。

## 4. 实施清单与验收

| 阶段 | 交付物/修改 | 可验收条件 |
| --- | --- | --- |
| P0 合并门禁 | 更新 `ci-gate.yml`；新增离线测试清单校验、`CI Required`、PG 严格模式/JUnit/Job 超时；更新 ruleset；补 OMS/风控/回放/AI 关键测试 | 故意新增未分类测试会失败；关键负向测试可复现失败；PG skip=0；ruleset 强制完整 gate，strict/1 review 生效 |
| P0 产物与隔离启动 | `Dockerfile`/lock、release build workflow、digest manifest、Demo Compose overlay；修复 smoke health 汇总 | 同一 commit 只构建一次；干净容器实际启动且全服务 healthy；缺凭据/非 sandbox 被拒绝；Control 无交易密钥；只清理任务资源 |
| P1 发布预检与回滚 | `docs/CI_CD_RELEASE_RUNBOOK.md`、配置校验、迁移/回放/兼容 gate、deploy dry-run workflow | 默认 dry-run 不连接主机；错误 digest/默认口令/DR 失效/schema 不兼容均 fail-closed；旧 digest 在副本完成恢复/不重复发送演练 |
| P1 沙箱证据 | nightly/候选发布 workflow，在 `BINANCE_ENV=demo` 下运行 E2E、24h 观察、故障与 DR；修复 observation gate | 证据关联 commit+digest+专用 Demo 环境；缺失/过期证据、ready_ratio 低于批准阈值、任何账务异常均阻断；无真实生产/testnet 凭据或 Testnet 必跑任务 |
| P2 效率与供应链 | pip 缓存、拆分测试、类型/lint/coverage/JUnit、SBOM/漏洞扫描、Actions 权限/pinning | PR 关键检查有明确超时；历史耗时基线后不增加无必要串行；安全报告可追踪，阈值例外有到期日 |

建议将 P0 与 P1 的每一步独立 PR 交付；先运行现有 CI 和新增门禁，再允许任何环境
接入。代码合并通过 ≠ 当前版本 Demo 验收通过 ≠ 获批准发布。优先完成前两项，比直接添加
自动 SSH 部署或提高全仓覆盖率更能降低当前交易系统的实际风险。

## 5. 验证范围

本次仅进行静态配置、代码与远端只读规则/Actions 记录审查，未运行本地测试、Docker、
沙箱网络、部署或数据迁移，也未读取 `.env`、实际 secrets 或私人证据文件。后续每个实施
PR 应附定向失败复现、现有 CI 通过记录及未执行项；生产与不可逆操作继续单独授权。

## 6. 实施状态（2026-11-04，独立分支 `feature/ci-cd-hardening`）

本节只记录本次实施的真实状态，不修改前文的审查时点结论。状态分为 **已实现**、
**已实测**、**未执行** 三类；未执行项不得按通过处理。

### 6.1 已实现并已实测

| 验收 | 实现 | 本地实测证据 |
| --- | --- | --- |
| C1 | `ci/test_manifest.json` 唯一分类 256 个测试文件（含本专项新增测试与测试员预分类文件）；`scripts/verify_test_manifest.py` 校验未分类/失效/重复/工作流缩小及 `CI Required` needs 完整性；`QTS_TEST_GROUP` 分组选择；`QTS_TEST_NETWORK_POLICY` 禁网；`REQUIRE_POSTGRES=1` 将 PG skip 转失败；`research_ai` 审计子集独立 gate；新增 WS/REST 同成交并发 OMS 测试 | P0 23 文件 367 passed / 0 skipped（offline 网络策略）；offline 202 文件全量通过 / 0 skipped；Research/AI 子集 252 passed / 0 skipped；Postgres 组 246 passed / 0 skipped；组合 3606 passed / 0 skipped；新增反例：未分类文件收集失败（exit 4） |
| C2 | `ci-gate.yml` 最小权限、pip 缓存、Job 超时、JUnit/coverage artifact、`CI Required` 聚合、前端 lint/coverage、类型检查限定 CI/ops 模块；`trader/requirements-ci.constraints.txt` 与 runtime constraints 含哈希；coverage 基线门槛；coverage Job 同时运行 P0+offline 以对齐基线，P0 快速 Job 保留 | 本地组合运行 3606 passed / 0 skipped；行覆盖 79.44%、分支 54.58%；关键模块门槛见 `ci/coverage_thresholds.json`；前端 24 文件 156 tests，lint/typecheck/build 通过 |
| C3 | Dockerfile 双基础 digest pin（python/postgres 多阶段复制 pg_dump）、revision/version 元数据、hashed runtime 依赖；`scripts/compose_smoke.py` 支持固定 digest、全容器 health、清理与既有资源身份检查；`scripts/release_manifest.py`；`release-build.yml` + SBOM/扫描 | smoke 回归测试通过；Docker/Compose 可用；**实际隔离 Compose 启动见 6.2** |
| C4 | `deploy/compose.demo.yml`、`deploy/demo.env.example`、`scripts/deploy_preflight.py`、`deploy-demo.yml`（默认 dry-run） | 合并 Compose config 渲染通过（Demo project/端口/卷隔离、`BINANCE_ENV=demo`、无 build）；preflight 正/负例测试通过 |
| C5 | `docs/CI_CD_RELEASE_RUNBOOK.md`；扩展 `summarize_observation.py` 阈值、重复/重发/覆盖证据与 strict release 绑定；迁移 expand-only/旧版兼容/守恒测试；`model_rollback_manager.py` 边界文档 | 观察门禁正/负例通过；迁移静态 expand-only 测试通过；真实 PG 迁移/守恒见 6.2 |
| C6 | RFC 状态、`PROJECT_ARCHITECTURE.md` 8.5、`INTERFACE_CONTRACTS.md` 13.17、Demo 验收/runbook 更新、PR 模板绑定 digest | 文档与实现键名/命令一致；未修改历史验收事实 |

### 6.2 已执行但范围受限

- 真实 PostgreSQL 测试：使用任务自有临时 schema/容器，`REQUIRE_POSTGRES=1` 且
  `QTS_TEST_NETWORK_POLICY=loopback`；结果见独立测试报告。未连接、未迁移、未触碰现有
  `qts-postgres` 容器或 `quant_trading_system_qts_pgdata` 卷。
- 发布预检：远端 dry-run（ubuntu-latest）使用 `--no-host-evidence`，只校验 release
  绑定与非主机配置，并把 DR/schema/rollback 主机证据标为 `HOST_EVIDENCE_DEFERRED`；
  self-hosted Demo runner 上的实际部署 job 在启动任何容器前再次执行完整预检。dry-run
  的通过不构成部署批准。
- 隔离 Compose smoke：实际构建 `qts-runtime:local` 后，分别以基础 `compose.yml` 和
  `compose.yml + deploy/compose.demo.yml` 启动。两次均使用任务自有 project、空闲端口、
  空凭据文件；结果 `passed=true`、所有启动容器 healthy、Control 无交易凭据、Execution
  空凭据与 `BINANCE_ENV=production` 均以退出码 2 拒绝、`/health/trading` 负向语义成立、
  告警 ack 成功、既有 `qts-postgres`/`qts_pgdata` 身份不变、清理后无残留容器/卷。
  原始证据在 `artifacts/cicd/raw/compose-smoke-{base,demo}/compose-smoke.json`（不提交）。
  Demo overlay 的启动使用本地镜像标签而非最终 GHCR digest，因此只证明配置与启动行为，
  不证明当前未构建的远端 digest。
- GitHub Actions 工作流只做本地 YAML/静态检查；**没有**在 GitHub 远端执行。

### 6.3 未执行（不得声称完成）

| 项目 | 原因/边界 |
| --- | --- |
| 远端 ruleset 改为 `CI Required`、strict、1 名非作者审核 | 需要仓库管理员权限；本专项只提供迁移文件与操作说明 |
| GHCR 推送、SBOM/Trivy 远端扫描 | 需要 GitHub Actions 与 packages 权限；本地只验证脚本/配置 |
| 真实 Demo 部署 | 本专项不连接主机、不部署 |
| 当前 digest 的 Demo E2E、主动重连、24h 观察 | 需要受保护 Demo 环境和真实观察时间；历史 `ready_ratio=0.6252` 不自动适用于新 digest |
| 生产/真实资金 | 独立项目，需单独授权 |
| 真实 testnet | 用户已明确不要求；`testnet_e2e.py` 不作为普通 CI 或 Testnet 必跑项 |

### 6.4 已知限制

- 6.1 C1/C2 表中的 367/246/3606/79.44% 是测试员文件加入前的工程师批次；纳入测试员
  73 个 offline + 8 个 PG 用例后的最终批次为组合 3682、offline 3315、postgres 254、
  覆盖 79.5%。两组都是真实运行结果，不合并成同一批次，也不以旧批次替代最终验收。

- 本地 `pytest 9.0.2` 仅用于开发验证；CI 使用 `trader/requirements-ci.constraints.txt`
  锁定的版本。哈希锁按 `x86_64-unknown-linux-gnu` / CPython 3.12 生成，Windows 开发机
  不保证可直接 `--require-hashes` 安装。
- 前端 coverage 原 80% 阈值从未实际执行；本次实测为 statements/lines 31.71%、
  branches 73.79%、functions 37.8%，因此把门槛调整为实测基线下限并在代码注释中记录，
  不把 80% 当作已达成。
- `trader/core/application/oms.py` 离线覆盖率 37.33%，`migrations.py` 20%；门槛只防
  回归，不表示这些模块已充分覆盖。迁移真实覆盖依赖 Postgres Job。
- 类型检查仅覆盖本专项 CI/ops 模块，未做全仓 mypy 清理。

### 6.5 父级最终核验 F-P1..F-P8 的修复（2026-11-05）

父级核验拒绝了上一版冻结源，指出 8 项真实的 workflow/runtime 质量门禁缺口。本节逐条
记录修复点与可执行证据（新一轮独立测试与只读审核结果见后续报告；旧的 6.1/6.2 数字是
上一批次运行结果，不冒充本次修复后的验收）。

| 编号 | 缺口 | 修复 |
| --- | --- | --- |
| F-P1 | strict 观察未强制 24h | `summarize_observation.py` 新增 `--strict-min-hours`（下限 24）与 `effective_min_hours`；`--strict-release` 下窗口不足 24h 记 `STRICT_WINDOW_SHORTER_THAN_24H` 并退出 1；`demo-evidence.yml` 增加 24h 输入前置校验 |
| F-P2 | release binding 自声明 | 新增 `scripts/collect_runtime_release_binding.py`：在目标主机 inspect 容器/镜像/label/health，输出 `runtime_inspected=true` 与 `checks.*`；strict 模式要求该绑定并逐项校验（`RELEASE_RUNTIME_*` blockers），手写 binding 被拒 |
| F-P3 | 部署无健康/并发保护 | 新增 `scripts/deploy_gate.py`（pre/post）：pre 要求新增风险已关闭、命令收敛、单一 owner/有效 fencing；post 要求全部容器 healthy、固定 digest/label、`/health/trading` ready、无未入账成交；`deploy-demo.yml` 接入 pre/post 门禁并与 `demo-evidence.yml` 共用 `qts-demo-<project>` concurrency 组 |
| F-P4 | 发布缺启动证明 | `release-build.yml` 在 main 发布后用 `${IMAGE}@${DIGEST}` 执行 `compose_smoke.py --require-digest` 隔离启动，证据随 release 上传并写入 manifest |
| F-P5 | 受保护工作流任意 checkout | 新增 `scripts/verify_release_provenance.py`：要求 Release Build 为 `workflow_run` 触发、`main`、成功、head SHA 与请求 commit 一致且 manifest 绑定一致；两个受保护工作流在 checkout `inputs.commit` 或使用 secrets 前先过该门禁 |
| F-P6 | 外部告警未接线 | demo overlay 将 control/execution 的 `QTS_ALERT_WEBHOOK_URL`/`QTS_ALERT_HEARTBEAT_URL` 绑定到 `QTS_EXTERNAL_ALERT_WEBHOOK_URL`/`QTS_EXTERNAL_ALERT_HEARTBEAT_URL`；预检新增 `EXTERNAL_ALERT_HEARTBEAT_MISSING`；alert-sink 仅保留为本地演练路径 |
| F-P7 | 安全指标可静默消失 | strict 模式要求每个样本含 `reconciliation_age_ms`/`outbox_backlog`/`unbooked_fills`/`unknown_commands`/`balance_anomalies`，缺失或类型非法记 `STRICT_METRICS_MISSING:*`/`STRICT_METRICS_INVALID:*`；兼容模式保持历史行为 |
| F-P8 | manifest 缺证据不拒绝 | `release_manifest.py` 默认要求 JUnit/coverage/frontend/SBOM/scan/启动 smoke 全部存在且启动 smoke 通过、digest 一致，否则直接失败；`deploy_preflight.py` 复核 `RELEASE_MANIFEST_EVIDENCE_MISSING:<key>`/`RELEASE_MANIFEST_STARTUP_SMOKE_FAILED` |

同时：`compose.yml` 的 backup 服务补 healthcheck（部署后“全部容器 healthy”才有意义），
`ci-gate.yml` 的格式与类型检查范围纳入三个新脚本。

### 6.6 父级第 2 次核验的修复（2026-11-05，沿用原 finding id）

父级对 `1f62fa71…` 的核验再次未通过，指出 5 项仍然不完整的门禁。本节记录同一批
finding id 的第二轮修复与可执行证据；修复次数按父级裁决累计，不另起新 id。

| 编号 | 父级指出的问题 | 本轮修复 |
| --- | --- | --- |
| F-P1 | strict 24h 下限有效，但真实采集入口在同一 duration 下只得到 23.98h | 观察器在 deadline 时刻补一次样本（首样本计入窗口），请求 24h 即得到 ≥24h 跨度；strict 下限不变；`test_cicd_observation_gate.py::test_observer_records_the_full_requested_duration` 用假时钟+假 HTTP 复现父级场景并断言跨度 ≥24h |
| F-P2 | binding 只验布尔 flags，不验容器事实/错误/时间戳，也不验样本归属 | 新增 `scripts/release_binding.py`：核对 binding 的实际事实（容器 present/running/healthy、镜像 digest、release label、采集 `errors` 为空、`generated_at` 为真实时间戳、健康响应就绪、`runtime` 身份块）；观察器把 `/health/trading` 的运行时身份写进每个样本；strict 比对每个样本的身份与时间戳，并要求 binding 的采集时间落在样本窗口内 |
| F-P3 | `psql -tAc` 布尔是 `t/f` 而实现只认 `true`；pre 不采集 QUEUED/SENDING、忽略未入账成交；post 单次判定 | 布尔按 `t/true/1/yes/on` 与 `f/false/0/no/off` 解析，空值视为未知；新增 `queued_commands`/`sending_commands` 事实，pre/post 都要求命令收敛且无未入账成交；缺失或不可解析的事实一律 `FACTS_INVALID:<key>`；post 门禁在 `--wait-timeout` 内轮询后再判定。新增 `scripts/verify_deploy_gate_live.py`：在任务自有 Compose project 中通过真实 `docker compose exec … psql` 采集并验证，证据见 `artifacts/cicd/raw/deploy-gate-live.json` |
| F-P5 | provenance 步骤把 `${{ inputs.* }}` 直接展开进 shell | 两个受保护工作流新增“Validate the operator inputs”步骤，输入只经 step `env` 传入并用 `[[ ... =~ ... ]]` 校验后以 `"$VAR"` 引用；`run:` 中不再出现 `${{ inputs.* }}`；Demo secrets 改为步骤级注入（provenance 步骤无 Demo 密钥）；观察时长同样改为 env 传值 |
| F-P8 | upload 多路径使共同祖先变成仓库根；第二次 checkout 清掉 artifacts | Release Build 只上传 `reports/` 一个根（manifest 顶层、smoke 在 `compose-smoke/`）；消费工作流把证据下载到 `${{ runner.temp }}/qts-release`，checkout 清理不影响证据；`demo-evidence.yml` 在观察窗口之后采集绑定，使窗口与身份可对照；删除不存在的 `runtime-release-binding.json` 上传项 |

同时：`deploy_preflight.py` 在未传 `--release-manifest` 时给出警告
`RELEASE_MANIFEST_NOT_PROVIDED`（工作流始终传入）；`ci-gate.yml` 的格式与类型检查范围
纳入本轮新增脚本（`release_binding.py`、`collect_runtime_release_binding.py`、
`deploy_gate.py`、`observe_trading_runtime.py`、`verify_release_provenance.py`、
`verify_deploy_gate_live.py`）。
