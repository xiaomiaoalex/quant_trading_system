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

## 5. 测试环境观察窗口（需人工执行）

1. 至少运行 24 小时，期间执行一次主动断线重连和一次执行进程重启。
2. 检查 `/v1/trading/metrics`：`unknown_commands`、`unbooked_fills`、`balance_anomalies`、`reconciliation_age_ms`、`outbox_backlog`、`unresolved`。
3. 出现 `BALANCE_MISMATCH`、`EXECUTION_KEY_CONFLICT`、`UNKNOWN_EXECUTION`、`UNKNOWN_ORDER` 时保持交易阻断，人工核对后再 `POST /v1/trading/resolve/{code}`。
4. 记录观察窗口、重连/重启时间、指标和所有人工处置。
