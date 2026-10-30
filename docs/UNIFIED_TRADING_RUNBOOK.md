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
| `QTS_RECONCILE_INTERVAL_SECONDS` | 30 | 运行期周期对账间隔；撤单/成交终态由轮询观测 |
| `QTS_TRADING_API_PORT` | 8090 | 新 Control API 端口 |
| `QTS_USERNAME` / `QTS_PASSWORD_HASH` | owner / 空 | 登录；空且 `QTS_AUTH_REQUIRED` 未设置时为开发模式 |
| `QTS_AUTH_REQUIRED` | false | 无密码时强制鉴权（fail-closed） |
| `QTS_ORIGIN` | `http://127.0.0.1:5173` | 允许的前端 Origin |
| `QTS_WORKER_LEASE_SECONDS` | 30 | 研究 worker 租约 |
| `QTS_WORKER_POLL_SECONDS` | 2 | 空队列轮询间隔 |

生成密码哈希：

```powershell
.venv\Scripts\python.exe -m trader.api.auth
```

## 2. 启动顺序

```powershell
# 1) 显式开发目标；仅首次或明确重建时加 --drop
.venv\Scripts\python.exe scripts\bootstrap_dev_schema.py --target dev

# 2) Trading Runtime + Control API（同一进程，生产应拆进程并只保留一个执行租约持有者）
.venv\Scripts\python.exe -m trader.runtime.trading_main

# 3) Research Worker（独立进程）
.venv\Scripts\python.exe -m trader.runtime.research_worker_main
```

启动日志必须出现 `storage -> ledger_risk_oms -> adapter -> reconcile -> ready`；未出现 `ready` 时 `/health/trading` 返回 503 且策略不得启动。

## 3. 健康与登录

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

### 5.2 观察窗口

```powershell
# 24h 观察（默认 60s 采样）
.venv\Scripts\python.exe scripts\observe_trading_runtime.py --duration 86400 --out artifacts\observation.jsonl

# 人工动作时追加标记
.venv\Scripts\python.exe scripts\mark_observation.py --out artifacts\observation.jsonl --mark runtime_restart --detail "killed pid ..."

# 汇总与门禁
.venv\Scripts\python.exe scripts\summarize_observation.py --input artifacts\observation.jsonl --min-hours 24 --json-out artifacts\observation_summary.json
```

窗口内必须完成一次主动重连和一次执行进程重启；`summarize_observation.py` 会输出 `ready_ratio`、读数切换、最大对账年龄、outbox 积压、未补齐成交、未知命令、账务异常、未解决差异和 markers。出现 `BALANCE_MISMATCH`、`EXECUTION_KEY_CONFLICT`、`UNKNOWN_EXECUTION`、`UNKNOWN_ORDER` 时保持交易阻断，人工核对后再 `POST /v1/trading/resolve/{code}`。

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
