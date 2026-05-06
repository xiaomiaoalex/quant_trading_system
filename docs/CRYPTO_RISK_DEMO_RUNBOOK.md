# Crypto Risk Demo Runbook

> 目标：在 Binance demo 执行环境下，验证数字货币独立风控 runtime、只读 USD-M 风控 source、预算热更新、readiness probe 和审计事件。本文只覆盖只读联调，不覆盖 Futures 下单。

## 1. 边界

- `BINANCE_ENV=demo` 表示当前执行适配器连接 Binance Spot Demo。
- `CRYPTO_RISK_FUTURES_BASE_URL` 只表示 USD-M 风控 source 的只读数据源 URL，不代表系统具备 Futures 下单能力。
- `POST /v1/risk/crypto/probe` 只能读取 venue health、mark price、instrument specs、leverage brackets、account、positions、open orders；不得下单、撤单或调整杠杆。
- `scripts/test_binance_demo_connection.py` 和 `scripts/smoke_trade_roundtrip.py` 会触发订单生命周期测试，不属于本 runbook 的只读 probe 流程。

## 2. 环境变量

从 `.env.example` 复制到 `.env` 后，至少确认：

```text
BINANCE_ENV=demo
BINANCE_API_KEY=<real demo key>
BINANCE_SECRET_KEY=<real demo secret>
LIVE_TRADING_ENABLED=false

CRYPTO_RISK_ENABLED=true
CRYPTO_RISK_FUTURES_BASE_URL=https://demo-fapi.binance.com
CRYPTO_RISK_BASE_SYMBOLS=BTCUSDT,ETHUSDT
CRYPTO_RISK_TOTAL_NOTIONAL_CAP=10000
CRYPTO_RISK_SYMBOL_CLUSTERS=BTCUSDT=BTC_BETA,ETHUSDT=ETH_BETA
CRYPTO_RISK_CLUSTER_NOTIONAL_CAPS=BTC_BETA=8000,ETH_BETA=4000
CRYPTO_RISK_MAX_MARGIN_RATIO=0.60
CRYPTO_RISK_MIN_LIQUIDATION_BUFFER_RATIO=0.08
```

如果使用自定义 demo 兼容 USD-M 风控 source，必须显式设置 `CRYPTO_RISK_FUTURES_BASE_URL`，并在 probe 前确认该 source 支持当前 demo 凭证的 signed account endpoints。

## 3. 联调前自检

运行：

```powershell
python scripts/check_crypto_risk_demo_env.py --env-file .env --strict
```

通过条件：

- `status: PASS`
- `execution_env: demo`
- `risk_source_mode: demo`，或在明确确认后接受 `custom` warning
- `symbols` 不为空
- 没有 `errors`

常见阻断：

- `BINANCE_ENV_NOT_DEMO`: 当前不是 demo 执行环境
- `CRYPTO_RISK_FUTURES_URL_MISSING`: 未显式配置 USD-M 风控 source
- `CRYPTO_RISK_FUTURES_URL_TESTNET`: source 指向 testnet
- `CRYPTO_RISK_FUTURES_URL_LIVE`: source 指向 live USD-M，不适合 demo rehearsal
- `CRYPTO_RISK_FUTURES_URL_SPOT_DEMO`: source 错用了 Spot Demo URL；USD-M demo source 应使用 `https://demo-fapi.binance.com`
- `CRYPTO_RISK_BUDGET_MISSING`: 未配置 symbol/cluster/total 任一风险预算
- `CRYPTO_RISK_CLUSTER_SYMBOL_UNMAPPED`: cluster cap 开启但 base symbol 未映射 cluster

脚本只读取环境变量，不访问网络，不打印 API key 或 secret。

## 4. 启动后端

```powershell
python -m uvicorn trader.api.main:app --host 127.0.0.1 --port 8080
```

启动后先查 runtime：

```powershell
Invoke-RestMethod http://127.0.0.1:8080/v1/risk/crypto/runtime
```

期望：

- `enabled=true`
- `wired=true`
- `fail_closed=false`
- `execution_env=demo`
- `base_symbols` 与 `.env` 一致

如果 `enabled=true` 但凭证缺失、source 初始化失败或 OMS broker 未初始化，runtime 必须进入 fail-closed，不允许静默绕过 pre-trade 风控。

## 5. 只读 Probe

PowerShell 示例：

```powershell
$body = @{
  symbols = @("BTCUSDT", "ETHUSDT")
  requested_by = "ops-demo-runbook"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8080/v1/risk/crypto/probe `
  -ContentType "application/json" `
  -Body $body
```

通过条件：

- `read_only=true`
- `execution_env=demo`
- `ok=true`
- `checks` 中 `venue_health`、`mark_prices`、`instrument_specs`、`leverage_brackets`、`account`、`positions`、`open_orders` 均为 `passed`

如果某项失败，记录失败项和响应 payload；不要通过手工填默认值继续开仓。

## 6. 审计确认

Probe 成功或失败后，查询事件流：

```powershell
Invoke-RestMethod "http://127.0.0.1:8080/v1/events?stream_key=risk:crypto&limit=20"
```

期望出现：

- `event_type=crypto_risk.probe_run`
- payload 包含 `ok`、`read_only`、`mode`、`execution_env`、`symbols`、`requested_by`、`duration_ms`、`checks`

预算热更新后还应出现：

- `event_type=crypto_risk.budget_updated`
- payload 包含 `previous_budget`、`new_budget`、`runtime_before`、`runtime_after`

## 7. Fail-Closed 演练

每次 demo 联调至少做一轮负向演练：

- 将 `CRYPTO_RISK_FUTURES_BASE_URL` 临时改为 testnet，确认自检失败。
- 临时移除 `BINANCE_SECRET_KEY`，确认 runtime fail-closed。
- 临时移除一个 base symbol 的 cluster 映射，确认自检失败。
- 使用不存在 symbol 发起 probe，确认该 symbol 的 mark/spec/bracket 检查失败且无订单动作。

只读负向 probe 可以用脚本自动验证：

```powershell
python scripts/rehearse_crypto_risk_demo_fail_closed.py `
  --base-url http://127.0.0.1:8080 `
  --symbol QTSFAILCLOSEDUSDT `
  --requested-by ops-fail-closed-rehearsal
```

通过条件：

- runtime 为 `enabled=true`、`wired=true`、`fail_closed=false`
- probe 返回 `ok=false`、`read_only=true`
- 至少一个检查项失败，通常是 `mark_prices`、`instrument_specs` 或 `leverage_brackets`
- `risk:crypto / crypto_risk.probe_run` 写入匹配的失败审计事件
- `/v1/orders` 演练前后返回内容一致

演练结束后恢复 `.env`，重新运行自检和 probe。

## 8. 前端入口

启动 Frontend 后访问 `/crypto-risk`：

- 查看 runtime 状态
- 触发只读 probe
- 热更新预算
- 查看 `risk:crypto` 审计事件

前端按钮的确认框不替代本 runbook；真实联调前仍先跑自检脚本。
