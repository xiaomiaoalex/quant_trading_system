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
`trader.api.main` 旧入口保留只读 connector/账户快照，停用旧自动恢复/OMS sender，旧查询与研究保留；
旧 `/v1/orders/{id}/cancel` 返回 503，禁止直接改查询投影伪称撤单完成。
旧 main 退休运行期间 `/v1/killswitch` 写操作同样返回 `503 LEGACY_EXECUTION_RETIRED`，
读取仍可用；风险控制必须提交统一 Control API 的持久控制命令。
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

本地前端指向统一 Runtime（默认 Vite 仍使用旧控制面 8080）：

```powershell
$env:QTS_CONTROL_API_TARGET = "http://127.0.0.1:8090"
cd Frontend
npm run dev
```

只把新接口切到 Control API、旧页面继续走旧 API 时，再加一个旧目标：

```powershell
$env:QTS_CONTROL_API_TARGET = "http://127.0.0.1:8090"   # /v1/research/*、/v1/data/history/*
$env:QTS_LEGACY_API_TARGET  = "http://127.0.0.1:8080"   # 其余 /v1/*、/api、/health
```

未设置 `QTS_LEGACY_API_TARGET` 时两者指向同一目标，保持原有整体切换行为。

历史归档导入已改为独立进程：`python -m trader.runtime.history_worker_main`
（同一数据库只会有一个 owner，靠 PostgreSQL session advisory lock 保证）。
旧 `trader.api.main` 的内嵌导入 worker 默认关闭，需要时用
`QTS_ENABLE_EMBEDDED_HISTORY_WORKER=1` 显式打开。

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

### 5.1 沙箱 E2E

本地启动（自动加载 `.env`，未配置密码时生成临时本地密码并写入系统临时目录，不打印明文）：

```powershell
.venv\Scripts\python.exe scripts\run_local_trading_runtime.py
```

如账户仍有非本系统遗留挂单，runtime 会按设计保持 `UNKNOWN_ORDER` 阻断。只能在确认这些挂单确属遗留后，用显式 allowlist 清理（拒绝 `qts-` 前缀订单）：

```powershell
.venv\Scripts\python.exe scripts\cancel_sandbox_orders.py --cl-ord-id <id1> --cl-ord-id <id2>
```

启动 runtime 后执行（只连接 `BINANCE_ENV` 指定的 sandbox；脚本不会读取或请求生产凭据）：

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
上述外部沙箱演练与 24h 窗口因 private WS 恢复证明缺口尚不能重跑验收，
历史产物不代表当前代码已通过最终门禁。

### 5.2 观察窗口

```powershell
# 24h 观察（默认 60s 采样）
.venv\Scripts\python.exe scripts\observe_trading_runtime.py --duration 86400 --out artifacts\observation.jsonl

# 人工动作时追加标记
.venv\Scripts\python.exe scripts\mark_observation.py --out artifacts\observation.jsonl --mark runtime_restart --detail "killed pid ..."
.venv\Scripts\python.exe scripts\mark_observation.py --out artifacts\observation.jsonl --mark active_reconnect --detail "reconnect response and recovery evidence path ..."

# 汇总与门禁
.venv\Scripts\python.exe scripts\summarize_observation.py --input artifacts\observation.jsonl --min-hours 24 --json-out artifacts\observation_summary.json
```

窗口内必须完成一次主动重连和一次执行进程重启；`summarize_observation.py` 会输出 `ready_ratio`、读数切换、最大对账年龄、outbox 积压、未补齐成交、未知命令、账务异常、未解决差异和 markers。出现 `BALANCE_MISMATCH`、`EXECUTION_KEY_CONFLICT`、`UNKNOWN_EXECUTION`、`UNKNOWN_ORDER` 时保持交易阻断，人工核对后再 `POST /v1/trading/resolve/{code}`。

最终汇总门禁要求 `runtime_restart` 和 `active_reconnect` 两个 marker 的时间戳均在首末采样之间；
marker 的 `detail` 应指向可复查的动作/恢复证据，不能仅凭标记声称动作已完成。
损坏 JSON、无效行或乱序/重复采样时间戳使门禁失败。相邻采样最大间隔默认 180 秒，
可按实际采样计划显式使用 `--max-gap-seconds` 调整，输出同时记录实际和允许间隔；
不得用仅有首末两个样本的时间跨度冒充连续观察。`parse_errors > 0` 不可通过。

运行期注意：

- 新 runtime 每 `QTS_RECONCILE_INTERVAL_SECONDS` 秒拉取完整账户快照；撤单/成交终态来自快照中的本系统订单观测，不靠猜测。
- 交易所规则（step/tick/min notional/quote precision）在 Adapter 边界归一化；MARKET BUY 以 quote 金额结算，成交基础数量可能与请求数量不同，只要成交事实可对上账即视为完整。

### 5.3 本地验证产物（`artifacts/` 不提交）

| 文件 | 内容 |
|------|------|
| `artifacts/e2e_evidence_final.json` | 首次完整沙箱链路：登录/健康/LIMIT 挂单/撤单/订单转 `CANCELLED`/MARKET 成交/账务结算/账本/主动重连 |
| `artifacts/e2e_evidence_window.json` | 观察窗口内、执行进程重启后的同一链路复验 |
| `artifacts/observation.jsonl` | 24h 观察采样（每 20s 一条；含 2 条重启停机 `ConnectError`、READY 切换和 markers） |
| `artifacts/observation_summary_partial.json` | 当前部分窗口汇总；`blockers=[]` |
| `artifacts/observation_attempt1.jsonl` | 第一次尝试（本地密码未跨重启复用导致 observer 401），保留用于审计 |

24h 结束后运行最终门禁：

```powershell
.venv\Scripts\python.exe scripts\summarize_observation.py --input artifacts\observation.jsonl --min-hours 24 --json-out artifacts\observation_summary_final.json
```

本地临时密码文件位于系统临时目录 `qts_operator_password.txt`；验证结束后可删除。生产/真实资金环境必须使用显式 `QTS_PASSWORD_HASH`，不得依赖该文件。
