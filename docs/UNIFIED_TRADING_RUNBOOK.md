# Unified Spot Trading Runbook

本 runbook 覆盖本地/测试环境的统一现货闭环：Control API + Trading Runtime、Research Worker、数据库和故障演练。禁止用于生产或真实资金。

## 1. 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `POSTGRES_CONNECTION_STRING` | compose 默认 | PostgreSQL 连接 |
| `BINANCE_ENV` | `demo` | `demo` / `testnet` |
| `BINANCE_API_KEY` / `BINANCE_SECRET_KEY` | 空 | 缺失时交易 runtime 启动但保持未就绪 |
| `QTS_ACCOUNT_ID` | `binance_spot` | 账户 ID |
| `QTS_SYMBOLS` | `BTCUSDT` | 对账/估值 symbol 列表 |
| `QTS_RUNTIME_OWNER` | host:pid | 执行租约持有者 |
| `QTS_RUNTIME_LEASE_SECONDS` | 30 | 执行租约秒数 |
| `QTS_RECONCILE_LOOKBACK_MS` | 86400000 | REST 成交补查窗口 |
| `QTS_COVERAGE_LOOKBACK_MS` | 86400000 | 覆盖证明回填窗口；必须不短于最长订单生命周期 |
| `QTS_COVERAGE_SETTLE_MS` | 250 | 两遍完整回填之间的静置毫秒数，用于暴露交易所迟到写入 |
| `QTS_RECONCILE_INTERVAL_SECONDS` | 30 | 运行期周期对账间隔；撤单/成交终态由轮询观测 |
| `QTS_TRADING_API_PORT` | 8090 | 新 Control API 端口 |
| `QTS_USERNAME` / `QTS_PASSWORD_HASH` | owner / 空 | 登录；空且 `QTS_AUTH_REQUIRED` 未设置时为开发模式 |
| `QTS_AUTH_REQUIRED` | false | 无密码时强制鉴权（fail-closed） |
| `QTS_ORIGIN` | `http://127.0.0.1:5173` | 允许的前端 Origin |
| `QTS_WORKER_LEASE_SECONDS` | 30 | 研究 worker 租约 |
| `QTS_WORKER_POLL_SECONDS` | 2 | 空队列轮询间隔 |
| `QTS_STRATEGY_MODULE` | 空 | 可选已安装 `trader.strategies.*` 插件；空时只提供人工命令执行能力 |
| `QTS_STRATEGY_CONFIG` | `{}` | 插件初始化配置 JSON |
| `QTS_DEPLOYMENT_ID` | 空 | 启用策略时必填，稳定策略实例 ID |
| `QTS_INSTRUMENTS` | `{}` | 显式 symbol→`[base_asset, quote_asset]` JSON；启用策略时必填 |
| `QTS_STRATEGY_WARMUP_EVENTS` | 0 | 部署侧预热长度下界；与插件声明 `warmup_events` 取较大者。0 表示“不额外加长”，不表示可跳过预热 |
| `QTS_STRATEGY_WARMUP_INTERVAL` | 1m | 重新采集预热窗口所用的 Binance K 线周期 |
| `QTS_MARKET_POLL_SECONDS` | 1 | 策略 ticker 轮询间隔，只有恢复就绪后才调用插件 |

生成密码哈希：

```powershell
.venv\Scripts\python.exe -m trader.api.auth
```

## 2. 启动顺序

```powershell
# 1) 显式开发目标；仅首次或明确重建时加 --drop
.venv\Scripts\python.exe scripts\bootstrap_dev_schema.py --target dev

# 2) Control API：独立进程，仅接收持久命令与查询，不构建交易所发送器
# 先在该进程环境配置 QTS_PASSWORD_HASH；独立控制面始终强制鉴权
.venv\Scripts\python.exe -m trader.runtime.control_main

# 3) Trading Runtime：另一终端；独立执行进程、单账户租约
.venv\Scripts\python.exe -m trader.runtime.trading_worker_main

# 4) Research Worker：第三终端
.venv\Scripts\python.exe -m trader.runtime.research_worker_main
```

启动日志必须出现 `storage -> ledger_risk_oms -> adapter -> reconcile -> ready`；未出现 `ready` 时 `/health/trading` 返回 503 且策略不得启动。

独立执行器现已装配 `PrivateTradingSession` → `RecoveryCoordinator` → 统一交易服务：
拿账户租约后才启动 private stream，双快照/分页完整性/代际缓冲/可信覆盖证明均通过后
才发布 READY。未装配会话返回 `PRIVATE_SESSION_NOT_ASSEMBLED`。
真实 Binance 适配器使用 `BinanceSpotCoverageProver`：以交易所服务器时间界定窗口，
完整分页回填订单与成交，逐笔映射本系统 `qts-` 订单，连续两遍回填必须完全一致，
快照内成交必须全部出现在回填中；任一条件失败即无证明并保持
`PRIVATE_STREAM_COVERAGE_UNPROVEN` 阻断。不得通过环境开关伪造证明，也不得以
REST 接收时间或 socket 已连接代替覆盖证明。`QTS_COVERAGE_SETTLE_MS=0` 仅用于
本地测试；真实环境应保留非零静置以暴露交易所迟到写入。
`QTS_COVERAGE_LOOKBACK_MS` 应不小于 `QTS_RECONCILE_LOOKBACK_MS`，也不得短于
最长订单存活时间；窗口内出现任何非本系统 `qts-` 订单的成交都会使证明失败，
因此运行账户必须是专用隔离账户。`QTS_INSTRUMENTS` 的每个 symbol 必须包含在
`QTS_SYMBOLS` 中，否则执行入口在启动时直接拒绝；`QTS_SYMBOLS` 同时是运行时
下单 allowlist，手动命令和持久 QUEUED 命令在发送边界都会拒绝范围外 symbol，
撤单/查询不受限。
旧合并启动器仍将仅 REST 快照的 `stream_healthy` 标为 false。
自动化测试注入明确的 fixture coverage，验证新策略 runner→订单→WS 成交→账本→断线阻断闭环。
执行器租约冲突时独立进程直接失败退出，不在后台等待另一个实例失效后接管。

Control API 下单/撤单返回 `202 + QUEUED`；执行器周期消费后才更新命令。
Control API 通过 PG 账户门禁与有效 owner/token 租约查询交易就绪，自己不持执行租约。
旧 `/v1/orders/{id}/cancel` 返回 503 `LEGACY_CANCEL_RETIRED`，撤单必须提交
`/v1/trading/orders/{cl_ord_id}/cancel` 持久命令。`/v1/killswitch` 与
`/v1/safety-gate/enable` 映射为 `BLOCK_NEW_RISK` / `RESUME_NEW_RISK` 持久命令；
`/v1/reconciler/trigger` 返回 503 `RECONCILIATION_IS_EXECUTION_OWNED`。
各独立终端需显式配置环境变量（PG、账户、控制面密码、执行器 sandbox 凭据），
不依赖另一个进程已加载 `.env`。

策略可选配置（仅配置示例；恢复未通过时不会运行策略）：

```powershell
$env:QTS_STRATEGY_MODULE = "trader.strategies.ema_cross_btc"
$env:QTS_DEPLOYMENT_ID = "ema-paper-1"
$env:QTS_STRATEGY_CONFIG = '{"fast_period":12,"slow_period":26,"order_size":"0.01"}'
$env:QTS_INSTRUMENTS = '{"BTCUSDT":["BTC","USDT"]}'
$env:QTS_STRATEGY_WARMUP_EVENTS = "26"   # 至少覆盖最慢指标回看窗口
```

新 `UnifiedStrategyRunner` 接受显式市场事件 ID，经相同账户风控/订单入口执行；
不维护旧 Runner 的累计 committed exposure。重复已受理事件复用持久命令，
策略数量保持原值，现货 SHORT/CLOSE_SHORT 与复杂条件意图明确拒绝。
插件每次进程启动都从空指标状态开始：前 `QTS_STRATEGY_WARMUP_EVENTS` 个观察只
喂给插件并返回无信号，预热完成后才允许下单；该门禁不绕过账户恢复，重启后重新预热。

第四轮起预热是可审计的状态机，不再只是计数：

- 插件用 `stateful_indicators` / `warmup_events` 声明自己的指标需求；未声明的插件按
  有状态处理，此时 `QTS_STRATEGY_WARMUP_EVENTS=0` 会以 `STRATEGY_WARMUP_NOT_CONFIGURED`
  阻断并让 Worker 返回未就绪，而不再只是告警。无状态插件显式声明
  `stateful_indicators=False` 后可保持0。
- 预热窗口**重新采集**，不持久化插件内部指标状态：Worker 在恢复通过后按
  `QTS_STRATEGY_WARMUP_INTERVAL` 回放已收盘 K 线（未完成 K 线不可见），预热不足、
  行情缺口或采集失败分别以 `STRATEGY_WARMUP_HISTORY_INCOMPLETE` /
  `STRATEGY_WARMUP_HISTORY_FAILED` 阻断。
- 就绪响应与 `/health/trading` 暴露 `strategy_warmup_state`、
  `strategy_warmup_reason`、`strategy_warmup_remaining`、`strategy_ready`；
  预热失败即关闭新增风险。

## 3. 健康与登录

阶段 6 起 Control API 是唯一 HTTP 进程，前端不再按路径拆分旧 API：

```powershell
$env:QTS_CONTROL_API_TARGET = "http://127.0.0.1:8090"   # 可选，默认已是该端口
cd Frontend
npm run dev
```

旧 `trader.api.main`（8080）已删除；原只读/安全视图（monitor、orders、killswitch、
safety gate、brokers、portfolio、reconciler report、strategies 读取、deployment
events、events、allocations 读取）由 `trader/api/routes/control_views.py` 从统一账户
聚合提供。旧策略动态生命周期（load/start/stop/pause/resume）、replay/snapshot 和
legacy backtest/candidate 页面随旧 Runner 退休；策略通过 `QTS_STRATEGY_MODULE` /
`QTS_STRATEGY_CONFIG` 配置并随执行进程启动，研究回测使用 `/v1/research/backtests`。

历史归档导入已改为独立进程：`python -m trader.runtime.history_worker_main`
（同一数据库只会有一个 owner，靠 PostgreSQL session advisory lock 保证）。

阶段 6 明确退休的旧页面/接口（随旧 Runner/旧进程删除，数据文件与 PG 历史保留）：

- 旧 `/v1/backtests*`、`/v1/strategy-candidates*`、`/v1/deployments` 创建/启停、
  `/v1/portfolio-autopilot*`、`/v1/allocations` 写入：由 `/v1/research/backtests` 与
  `/v1/trading/allocations`（book 显式划拨）替代；旧回测报告文件不删除。
- `/v1/strategies/*` 动态 load/start/stop/pause/resume、`/v1/safety-gate` 旧进程内开关：
  返回 410 `LEGACY_STRATEGY_LIFECYCLE_RETIRED`；策略由 `QTS_STRATEGY_MODULE` /
  `QTS_STRATEGY_CONFIG` 配置并随执行进程启动，安全闸门映射为持久
  `BLOCK_NEW_RISK` / `RESUME_NEW_RISK` 命令。
- `/v1/replay*`、`/v1/snapshots*`：返回 410 `LEGACY_REPLAY_RETIRED`；研究回放使用新研究
  runtime，操作员对账使用 `/v1/reconciler/report` 与 `/v1/trading/metrics`。
- 保留并迁移：monitor/orders/executions/brokers/portfolio 读取、killswitch/safety-gate、
  reconciler report、strategies 读取、deployment status/events、events、allocations 读取、
  performance 读取、portfolio research、audit、chat、data catalog/history、research。

登录后的页面刷新通过 `/v1/auth/me` 恢复 CSRF token，无需重新登录。

```powershell
curl http://127.0.0.1:8090/health/live
curl http://127.0.0.1:8090/health/ready
curl http://127.0.0.1:8090/health/trading
```

登录后使用返回的 `csrf_token` 和 Cookie：

```powershell
curl -c cookies.txt -X POST http://127.0.0.1:8090/v1/auth/login `
  -H "Content-Type: application/json" `
  -d '{\"username\":\"owner\",\"password\":\"...\"}'
```

写操作必须带 `-b cookies.txt -H "x-csrf-token: <token>"`。

## 4. 验收与演练命令

```powershell
.venv\Scripts\python.exe -m pytest -q trader/tests/test_trading_service.py --tb=short
.venv\Scripts\python.exe -m pytest -q trader/tests/test_trading_runtime.py --tb=short
.venv\Scripts\python.exe -m pytest -q trader/tests/test_trading_api.py --tb=short
.venv\Scripts\python.exe -m pytest -q trader/tests/test_trading_ingress.py --tb=short
.venv\Scripts\python.exe -m pytest -q trader/tests/test_spot_execution_adapter.py --tb=short
.venv\Scripts\python.exe -m pytest -q trader/tests/test_research_runtime.py --tb=short
.venv\Scripts\python.exe -m pytest -q trader/tests/test_trading_performance.py --tb=short
.venv\Scripts\python.exe -m pytest -q trader/tests/test_trading_postgres.py --tb=short
.venv\Scripts\python.exe -m pytest -q trader/tests/test_split_control_runtime.py trader/tests/test_split_control_postgres.py --tb=short
.venv\Scripts\python.exe -m pytest -q trader/tests/test_research_postgres.py --tb=short
.venv\Scripts\python.exe scripts\rehearse_trading_runtime.py
```

## 5. 沙箱 E2E 与测试环境观察窗口

本阶段最高验收环境固定为 `BINANCE_ENV=demo`。完成 Demo E2E、主动重连/执行进程
重启和 24h 观察门禁即为外部验收通过，不要求真实 testnet 覆盖证明或 Testnet E2E。
下文 `testnet_e2e.py` 只是历史脚本文件名，执行目标必须由 `BINANCE_ENV=demo` 限定；
不得据此连接生产或真实资金账户。

### 5.1 沙箱 E2E

本地启动（自动加载 `.env`，未配置密码时生成临时本地密码并写入系统临时目录，不打印明文）：

```powershell
.venv\Scripts\python.exe scripts\run_local_trading_runtime.py
```

如账户仍有非本系统遗留挂单，runtime 会按设计保持 `UNKNOWN_ORDER` 阻断。只能在确认这些挂单确属遗留后，用显式 allowlist 清理（拒绝 `qts-` 前缀订单）：

```powershell
.venv\Scripts\python.exe scripts\cancel_sandbox_orders.py --cl-ord-id <id1> --cl-ord-id <id2>
```

启动 runtime 前确认 `BINANCE_ENV=demo`，启动后执行（脚本不会读取或请求生产凭据）：

```powershell
.venv\Scripts\python.exe scripts\testnet_e2e.py --reconnect --out artifacts\e2e_evidence_final.json
```

脚本断言链路：

1. 登录 + `/health/trading` 必须 `200`。
2. LIMIT 挂单 → `OPEN`；撤单命令 `CONFIRMED` 但订单仍 `OPEN`；REST 周期对账观测到 `CANCELLED`。
3. MARKET 买入 → 订单 `FILLED`；等待 `unbooked_fills=0` 且 `active_holds=0`。
4. 读取 `trading_ledger`，费用按原币种记账。
5. `POST /v1/trading/reconnect` 后重新 `READY`。
6. `unresolved` 必须为空，`balance_anomalies` 必须为 0。

重连接口提交 `{command_id, account_id}` 并返回命令回执；轮询命令 `CONFIRMED`
以及交易就绪均成功后才算恢复。执行进程先持久关闭 stream/ready 门禁，再断开会话，
重新对账；API 接受重连意图不等同于会话已经恢复。
当前验收记录见 `docs/UNIFIED_TRADING_ACCEPTANCE.md`：第五轮 Demo E2E 与 24h
观察窗口已通过。该历史结果不等于每个后续 commit 都自动通过；新版本验收须将证据
关联到对应版本。历史脚本名中的 testnet 不增加额外 Testnet 验收要求。

### 5.2 观察窗口（阶段 6 起）

阶段 6 的 ≥24h 窗口由 `scripts/sandbox_supervisor.py` 编排：任务自有 Compose 项目/卷/动态端口、
持续 off-host 备份+恢复 DR 证据、执行前首次 DR 门禁、显式 book 划拨、观察 JSONL 与最终
`window.json` 判定。冻结镜像后：

```powershell
# 先在冻结源码上构建镜像并记录 image ID
docker compose -p qts_p6_build build
docker image inspect --format "{{.Id}} {{.Created}}" qts-runtime:local

# 真实 >=24h Demo 窗口（动态空闲端口；--no-build 使用刚构建的镜像）
# 异机地址必须显式给出：命令行优先于 $QTS_OFFHOST_HOST，两者都没有时 fail-closed。
# --host-key-alias 指定 known_hosts 中已有的身份（历史地址 192.168.3.4），
# 不关闭 StrictHostKeyChecking。
python scripts/sandbox_supervisor.py --duration-hours 24 \
  --lookback-ms 3600000 --runtime-lookback-ms 3600000 \
  --coverage-lookback-ms 86400000 --no-build \
  --remote-host 192.168.0.105 --host-key-alias 192.168.3.4 \
  --ssh-user alex --expected-hostname my-server
# 证据：artifacts/foundation/phase6/windows/<run_id>/{window.json,observation.jsonl,dr/}
```

每次启动由 supervisor 自己取得**本机级非阻塞独占 guard**（默认
`%LOCALAPPDATA%\qts\phase6-sandbox-start.lock`，身份跨 run ID/入口稳定，手动/包装/直接执行共用），
在产生任何 Demo/账户/Compose/DR 副作用之前取得，并持有到安全清理结束：
并发启动者以 `STARTUP_GUARD_HELD` 退出且零副作用；锁空闲但发现存活的 observer/DR 隧道
进程或运行中的 `qts_p6_sandbox_*` 容器时，以 `STARTUP_ORPHAN_DETECTED` fail-closed；
supervisor 归属由 OS 锁裁决，仍在启动或正在退出的非 owner supervisor 是竞争者/收尾者，
不作为孤儿；Docker 不可用时 `STARTUP_ORPHAN_CHECK_UNAVAILABLE`。窗口结束或中止后 supervisor 停止本窗口容器（只 stop、
保留容器与日志，不删除证据）；崩溃路径由下一次启动的孤儿检测拦截，自动释放锁不等于允许并发。
preflight 还要求 venue 无挂单（`SANDBOX_ACCOUNT_HAS_OPEN_ORDERS`）且完整 runtime lookback 内
无 qts- 成交（`SANDBOX_LOOKBACK_NOT_CLEAN`），必须按实时 venue 事实复核，不得靠等待时间戳或
清空本地账本掩盖孤儿订单/命令/占用。

独立观察脚本仍可单独使用（密码文件，不把口令放进 argv）：

```powershell
python scripts/observe_trading_runtime.py --duration 86400 --password-file <secret-file> --out artifacts/observation.jsonl
```

`window.json` 以观察器的真实起止 marker 计算时长（起止 marker 均带 `monotonic_ms` 时以
monotonic 差值计，墙钟仅用于旧 marker 回退；整除截断只会低估）；`PRIVATE_STREAM_ALIGNING` 是每
`QTS_RECONCILE_INTERVAL_SECONDS` 次覆盖证明的有限转换，单独归类，不计为未解释故障。
对账/余额影响类未决码（BALANCE_MISMATCH、UNBOOKED_EXECUTIONS、UNKNOWN_EXECUTION、
FILL_OBSERVATION_MISMATCH、TERMINAL_EXECUTION_MISMATCH、INCOMPLETE_SNAPSHOT、
STALE_SNAPSHOT、UNKNOWN_ORDER、UNKNOWN_COMMAND、RECOVERY_GENERATION_OR_OVERFLOW、
RECOVERY_SNAPSHOT_UNSTABLE）允许最长 30 分钟的真实恢复时间；
其他未决码最长 2 分钟。判定以 `window.json` 的 `unresolved_max_duration_ms` 为准，
`unresolved_max_streak` 仅作证据，不参与通过判定。私有 WS/行情/对账类非就绪原因允许
最长 30 分钟的外部网络恢复（`PRIVATE_RECOVERY_*` 动态原因同属此类），其他原因最长 2 分钟；
沙箱出口按运行位置分别验证：宿主 preflight 可用其显式应用代理，容器 Demo REST/行情/private WS
按容器实际配置验证（允许直连），离机 DR 走专属 LAN/strict-key SSH/S3 且与 Demo 出口隔离；
所有检查绑定同一 Demo 环境与账户，不得静默代理回退。窗口 preflight 的 clean 检查覆盖最近
1 小时（`--lookback-ms 3600000`），比旧版 5 分钟更严格。
证据密度同样 fail-closed：带完整 metrics 体的采样数需达到预期采样的 90%，证据采样之间的
最大间隔（含末端到最后一个证据采样）不超过采样间隔上限，覆盖证明须在窗口末端新鲜（上界由 reconcile 节奏派生，默认 780s；`coverage.recorded_at_ms` 必填、缺失 fail-closed），缺证据的采样不得清空未决状态；
窗口结束时未决码与非就绪原因都必须已清空，否则判定失败。容器重启、主机崩溃、监督进程重启写入 `window_aborted`，不得追溯称为连续。
手动重连/执行进程重启不是本阶段 24h 窗口的必需动作；如需演练，应在窗口之外单独执行并保留证据。

Demo/Testnet 的 legacy `userDataStream` listenKey REST 端点可能返回 `410 Gone`；沙箱执行进程
显式使用 `QTS_PRIVATE_STREAM_MODE=ws_api_signature`（官方 ws-api 签名订阅），不落入已废弃的
listenKey 回退。若 ws-api 启动失败，会话按运行时对账周期重连；窗口证据记录
`PRIVATE_STREAM_RECOVERING/UNHEALTHY` 转换，`PRIVATE_STREAM_COVERAGE_UNPROVEN` 仍会使 6.2 失败。

阶段 6 的 24h 窗口不要求手动重连或执行进程重启；这类演练应在窗口之外单独执行并保留证据。窗口判定以 supervisor 的 `window.json` 判据为准（真实起止、采样覆盖与最大间隔、覆盖证明、未决清退、重复发送、DR、容器重启）；历史 `summarize_observation.py` 的 `runtime_restart`/`active_reconnect` marker 门禁仅适用于阶段 5 及更早窗口，不是阶段 6 的必跑项。出现 `BALANCE_MISMATCH`、`EXECUTION_KEY_CONFLICT`、`UNKNOWN_EXECUTION`、`UNKNOWN_ORDER` 时保持交易阻断；人工核对后通过 `POST /v1/trading/control` 提交 `RESOLVE` 命令（`code` + 证据 `evidence`），由 Execution 进程校验并应用，Control 进程不直接写账户事实。余额差异的证据必须逐资产给出与观测一致的 `internal` / `venue` / `difference`，例如：

**CI/CD 发布严格观察（历史兼容读取与新 digest 证据）**

```powershell
# 人工动作时追加标记
.venv\Scripts\python.exe scripts\mark_observation.py --out artifacts\observation.jsonl --mark runtime_restart --detail "killed pid ..."
.venv\Scripts\python.exe scripts\mark_observation.py --out artifacts\observation.jsonl --mark active_reconnect --detail "reconnect response and recovery evidence path ..."

# 汇总与门禁（历史兼容读取）
.venv\Scripts\python.exe scripts\summarize_observation.py --input artifacts\observation.jsonl --min-hours 24 --json-out artifacts\observation_summary.json

# 新 digest 的发布验收：严格绑定 release evidence，并显式使用发布阈值
.venv\Scripts\python.exe scripts\summarize_observation.py --input artifacts\observation.jsonl `
  --min-hours 24 --strict-release `
  --release-evidence artifacts\demo\release-binding.json `
  --expected-release-id <release-id> `
  --expected-commit <40hex> --expected-digest sha256:<64hex> `
  --json-out artifacts\observation_summary_strict.json
```

严格模式的 ready_ratio、对账年龄、末样本 outbox/未入账成交、重复/重发/覆盖证据与
release 绑定规则见 `docs/CI_CD_RELEASE_RUNBOOK.md` 第 7 节。历史窗口通过不等于当前
digest 通过。阶段 6 的窗口由 supervisor 的 window.json 判定，不要求窗口内主动重连/
重启；marker 门禁仅适用于阶段 5 及更早窗口。

```json
{
  "command_id": "resolve-2026-11-02-1",
  "account_id": "binance_spot",
  "action": "RESOLVE",
  "code": "BALANCE_MISMATCH",
  "evidence": {
    "kind": "external_withdrawal",
    "reference": "venue-statement-2026-11-01",
    "reason": "operator verified the venue statement",
    "assets": {"USDT": {"internal": "1000", "venue": "990", "difference": "-10"}}
  }
}
```

命令 `CONFIRMED` 表示证据已被 Execution 校验并记账；`FAILED` 时查看 `error`
（如 `RESOLVE_EVIDENCE_AMOUNT_MISMATCH`）并保持阻断。`UNKNOWN_COMMAND`
只能由交易所证据（完整快照）解除，不接受操作员命令清除。

运行期注意：

- **观察窗口进行中禁止重建开发库 schema。** `bootstrap_dev_schema.py --drop` 会删除
  `trading_accounts`，运行中的 runtime 会把窗口内全部历史成交当作新事实重放；脚本现在会在
  发现未过期 `trading_runtime_leases` 租约时拒绝执行。需要重建时先停 runtime，再执行 bootstrap，
  最后重新开始窗口计时。
- 新 runtime 每 `QTS_RECONCILE_INTERVAL_SECONDS` 秒拉取完整账户快照；撤单/成交终态来自快照中的本系统订单观测，不靠猜测。
- 私有流可能先于本地订单聚合到达；这类成交先以未入账状态保留，订单被 REST 领养后自动补记，
  不会因重复投递变成 `EXECUTION_KEY_CONFLICT`。内容真正不同的同 `exec_id` 成交仍然阻断。
- 交易所规则（step/tick/min notional/quote precision）在 Adapter 边界归一化；MARKET BUY 以 quote 金额结算，成交基础数量可能与请求数量不同，只要成交事实可对上账即视为完整。

#### 5.2.1 分段续跑（resume segment，2026-11-10）

原始连续窗口 `20261109T011441Z-efbba899` 已按 not-acceptance 归档（DR RPO 违约），
不再要求连续 24h；按用户/父级授权在同一原始窗口/数据库身份上分段累计。

**启动前检查（全部满足才启动）**

- 备份机 `192.168.0.105`（known_hosts 身份别名为历史地址 `192.168.3.4`）的
  SSH/S3 可达，且 supervisor 完成 3 个连续 verified off-host DR cycle；
  备份机不可达时 fail-closed，不得启动。
- 无 observer、DR 隧道、运行中的 `qts_p6_sandbox_*` 容器；startup guard 锁空闲；
  当前 continuation baseline `db-baseline-continuation-20261110.json`（67/450/65/199/319）
  与段 preflight 一致；lease 已过期/可递增；无挂单；runtime lookback 内无 qts- 成交。
- 原项目/卷/密钥仍在；镜像为当前 continuation 版本（`qts-runtime:local b4cd10c9…`，
  含 observation-only）；不得重新分配账户/新建数据库/更换凭据。

**启动**

```powershell
powershell -File artifacts/foundation/phase6/start_resume_segment.ps1
# 内部：scripts/sandbox_supervisor.py --resume-from-run 20261109T011441Z-efbba899 `
#         --segment-index 4 --duration-hours 24 --no-build
```

同一时刻只允许一个 supervisor；并发启动者以 `STARTUP_GUARD_HELD` 零副作用退出；
孤儿检测 `STARTUP_ORPHAN_DETECTED`、Docker 不可用 `STARTUP_ORPHAN_CHECK_UNAVAILABLE`。
不得启动新的独立 24h 窗口；不得手工启动第二套 Compose/observer。

**运行中核对**

- `/health/trading` 200 且 `ready=true`、reasons 为空；DR 周期持续成功（RPO 300s）。
- observation-only：commands/executions 不增加；如出现新订单/成交立即停段并按 finding 披露。
- lease/fencing token 只递增；outbox 不重复投递；dual snapshot 稳定；`segment.json` 写入
  `segment_index/resume_from/observation_only/dr_ready_cycles/lease/db_identity_hashes`。
- 出现 DR 内部违约、重复发送、执行回归、未解释未决、覆盖证明缺失或容器重启时按规则停段。

**停止/失败**

- 停段后 supervisor 只 stop 本段容器（保留容器/日志）；失败段归档到
  `windows-aborted/<run_id>/`，保留 `window.json`/posthoc summary，不得改写。
- DR 内部违约使整段失效；tail 违约只排除尾部。长段 `36448c34` 的 4 个 dr_stale 全在尾部，
  按 RFC tail 规则其此前 1,143,515ms 可计；canonical 采用零计入的保守口径，最终 handoff
  必须明确。两种口径当前均 <24h。
- 主机 WiFi/路由/代理/防火墙不由自动化修改。

**段后**

- 运行 `scripts/phase6_segment_aggregate.py` 更新 aggregate；用
  `capture_continuation_baseline.py` 采集下一段 baseline（delta 由原始 baseline 计算，
  per-segment lineage 写入 `segment-lineage-20261110.json`）；更新 handoff/evidence-index；
  交独立 tester 复验、再交全新只读 reviewer。
- 当前状态（2026-11-11）：原窗口 19,874,125ms；验证段 872,313ms；segment 5
  `20261110T075417Z-cc17f251` 73,664,222ms（24h `window.json` passed=true）；累计
  94,410,660ms，remaining 0，`acceptance_satisfied=true`。长段 36448c34 与 segment 4
  703b71af 零计入（前者 tail 规则 1,143,515ms 未采用，后者 incident
  `P6-INC-20261110-01`）。tester round20 已 pass 6.1/6.2；reviewer round14 的
  监控读视图/avg_price/聚合器 fail-closed/black-isort/CI 入口 findings 已修复并跑
  final20（3504/0/0/0），最终 fresh reviewer 签核后交父 agent handoff。最终 baseline
  67/721/65/199/319。

### 5.3 本地验证产物（`artifacts/` 不提交）

| 文件 | 内容 |
|------|------|
| `artifacts/e2e_evidence_final.json` | 首次完整沙箱链路：登录/健康/LIMIT 挂单/撤单/订单转 `CANCELLED`/MARKET 成交/账务结算/账本/主动重连 |
| `artifacts/e2e_evidence_window.json` | 观察窗口内、执行进程重启后的同一链路复验 |
| `artifacts/observation.jsonl` | 24h 观察采样（每 20s 一条；含 2 条重启停机 `ConnectError`、READY 切换和 markers） |
| `artifacts/observation_summary_partial.json` | 8.8h 部分窗口、以 `--min-hours 8` 生成的中间汇总；不是 24h 门禁结论，不能用作验收证据 |
| `artifacts/observation_attempt1.jsonl` | 第一次尝试（本地密码未跨重启复用导致 observer 401），保留用于审计 |

（历史：以下 `summarize_observation.py` 门禁仅适用于阶段 5 及更早的观察格式；阶段 6 的最终判定以
`artifacts/foundation/phase6/windows/<run_id>/window.json` 为准，不要求手工 marker。）

```powershell
# 历史命令，阶段 6 不再使用
.venv\Scripts\python.exe scripts\summarize_observation.py --input artifacts\observation.jsonl --min-hours 24 --json-out artifacts\observation_summary_final.json
```

本地临时密码文件位于系统临时目录 `qts_operator_password.txt`；验证结束后可删除。生产/真实资金环境必须使用显式 `QTS_PASSWORD_HASH`，不得依赖该文件。
