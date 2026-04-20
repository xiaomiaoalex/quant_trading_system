# Task 11-15 实施提示词（交付给 AI 工程师）

你要在 `quant_trading_system` 仓库中完成“策略自动交易闭环”落地，目标是**真实可运行**，不是前端假按钮或后端占位逻辑。

## 0. 背景与当前断点（必须先理解）

当前系统已经有：
1. 策略代码新建/调试/回测/注册/加载/启动接口。
2. `StrategyRunner.tick()` 能处理信号，且支持 `oms_callback` 执行订单。
3. 前端 Backtests 页已有“Debug -> Register -> Backtest -> Load -> Run”按钮链路。

当前缺口：
1. `/start` 仅切状态为 RUNNING，没有启动真实行情订阅与 tick 调度。
2. `StrategyRunner` 在 API 路由里未注入 `oms_callback`，信号不会走真实下单。
3. 前端调用了策略事件接口，但后端未完整提供 `/v1/strategies/{id}/events*` 查询接口。
4. 缺少自动交易安全闸门（开关、交易规则校验、KillSwitch 联动、失败 fail-closed）。
5. 缺少“自动买卖成功”的端到端验收与文档闭环。

---

## 1. 架构与硬约束（严格遵守）

1. 遵守五层架构，不允许跨层污染。
2. 不允许 `except: pass`。
3. 失败必须 fail-closed，不能 silent fallback 到“假成功”。
4. 幂等性与状态单调必须保留，尤其订单与成交处理。
5. 代码改动后必须更新文档：
   - `PROJECT_STATUS.md`
   - `docs/EXPERIENCE_SUMMARY.md`
   - 如果任务优先级/计划变化，更新 `PLAN.md`
6. 每个 Task 单独提交，提交信息格式：
   - `feat(task-11): ...`
   - `feat(task-12): ...`
   - `feat(task-13): ...`
   - `feat(task-14): ...`
   - `feat(task-15): ...`

---

## 2. 任务拆分与交付要求

## Task 11：接入实时行情订阅 + `runner.tick()` 调度到 `/load` `/start` 流程

目标：
1. `POST /v1/strategies/{id}/start` 后，策略会持续收到实时行情并触发 `runner.tick()`。
2. `stop/unload` 会正确回收调度任务和订阅资源。
3. 多策略并发时互不影响。

实施要求：
1. 新增“策略运行时编排服务”（建议文件：`trader/services/strategy_runtime_orchestrator.py`）。
2. 编排服务负责：
   - 管理每个 `strategy_id` 的 runtime context（task/queue/symbol/status）。
   - 接收行情事件并转换为 `MarketData`。
   - 按顺序调用 `await runner.tick(strategy_id, market_data)`。
3. 与现有 Binance 公有流组件对接。
4. `/start` 时启动调度，`/stop` 时停止，`/unload` 时清理。
5. 必须记录运行事件（至少：tick、调度异常、停止原因）。

验收标准：
1. 启动后 `tick_count` 持续增长。
2. 停止后 `tick_count` 不再增长。
3. 任一策略异常不会拖垮其他策略。

---

## Task 12：接入 OMS 下单回调（真实下单 + 成交回调策略）

目标：
1. 策略信号触发后走 OMS/券商，产生真实订单结果。
2. 成交后回调 `runner.on_fill()`，策略可感知成交。
3. 订单与成交写入控制面存储，前端可查询。

实施要求：
1. 在 `get_strategy_runner()` 创建 Runner 时注入：
   - `oms_callback`
   - `event_callback`
   - `killswitch_callback`
2. `oms_callback` 逻辑：
   - 将 `Signal` 映射为下单参数。
   - 使用 `BinanceSpotDemoBroker` 执行真实单。
   - 使用 `get_symbol_step_size()` 与 `quantize_by_step_size()`做数量合法化。
   - 处理 BUY/SELL 与数量精度、余额不足、最小名义金额等错误。
3. 订单与成交要写入现有 storage（orders/executions）。
4. 下单成功、拒单、成交都要发布策略事件。

验收标准：
1. 策略触发信号后，`/v1/orders` 可看到订单。
2. 有成交时，策略收到 `on_fill` 回调。
3. 拒单原因可追踪，不允许吞错。

---

## Task 13：补齐策略事件查询 API + 前端页对接

目标：
1. 后端提供事件查询接口，前端能看到实时信号/订单/错误。
2. 前端 Backtests/Strategy 详情能展示运行证据链。

后端要求：
1. 新增接口：
   - `GET /v1/strategies/{strategy_id}/events`
   - `GET /v1/strategies/{strategy_id}/events/signals`
   - `GET /v1/strategies/{strategy_id}/events/errors`
2. 支持 `limit`、`event_type` 等基础过滤。
3. 返回结构与前端 `StrategyEventEnvelope` 对齐。

前端要求：
1. 使用现有 `strategiesAPI.getStrategyEvents/getStrategySignals/getStrategyErrors`。
2. 在 Backtests 页 Strategy Lab 增加运行事件面板。
3. 展示字段至少包括：event_type、symbol、side、qty、price、reason、ts。

验收标准：
1. 触发策略后前端可看到信号事件。
2. 下单成功/拒单会出现在事件面板。
3. 前端无类型错误，接口字段一致。

---

## Task 14：自动交易安全闸门（必须）

目标：
1. 默认安全关闭，显式开启后才能真实下单。
2. 缺少安全前置条件时直接拒绝下单（fail-closed）。
3. KillSwitch 生效时阻断新仓并可停止策略。

必须实现：
1. 环境开关：
   - `LIVE_TRADING_ENABLED=true` 才允许真实下单。
2. 交易规则校验：
   - stepSize
   - minQty / minNotional（从交易所规则获取并校验）
3. KillSwitch 联动：
   - L1 禁新仓
   - L2/L3 停止策略或更强阻断
4. 错误可观测：
   - 拒单必须带明确 reason。
   - 发布 `strategy.error` 或 `strategy.order.rejected` 事件。

验收标准：
1. 开关关闭时任何信号都不会发实单。
2. 开关开启且条件满足时可正常下单。
3. 触发 KillSwitch 后行为符合级别定义。

---

## Task 15：联调验收 + 回归测试 + 文档闭环

目标：
1. 给出“自动买卖真实可见”的可重复验证路径。
2. 保证已有功能不回归。

测试要求：
1. 后端最少通过：
   - `trader/tests/test_api_endpoints.py`
   - `trader/tests/test_api_services.py`
   - 新增自动交易链路测试文件（你来命名）
2. 新增至少一个集成测试场景：
   - 注册策略代码 -> load -> start -> 收到行情 -> 产生信号 -> 真实下单回执 -> stop
3. 补充 smoke 脚本或复用现有脚本，验证 demo 环境自动买卖流程。

文档要求：
1. 在 `PROJECT_STATUS.md` 记录：
   - 开发前后状态
   - 验证步骤
   - 测试结果
2. 在 `docs/EXPERIENCE_SUMMARY.md` 记录：
   - 本次踩坑
   - 可复用模式
3. 若阶段优先级变化，更新 `PLAN.md`。

验收标准：
1. 自动交易链路完整演示可复现。
2. 文档与仓库实现一致，无“已完成/待开始”冲突状态。

---

## 3. 关键实现建议（防止低水平实现跑偏）

1. 不要把“定时伪造行情”当成实时订阅完成标准。
2. 不要只在前端显示“RUNNING”而没有真实 tick 增长与订单产生。
3. 不要把下单写成 mock 成功返回。
4. 不要把异常吃掉后返回 200。
5. 优先复用现有模块，不要新建并行重复栈。

---

## 4. 输出格式要求（你交付时必须包含）

1. 变更文件清单（按 Task 分组）。
2. 每个 Task 的测试命令与结果摘要。
3. 风险点与回滚方式。
4. 未完成项与下一步建议（如果有）。
5. Git 提交列表（按 task-11 到 task-15 顺序）。

---

## 5. 最终 DoD（全部勾选才算完成）

- [ ] `/start` 后策略自动消费实时行情并触发 `tick()`
- [ ] 信号可触发真实 OMS 下单
- [ ] 成交能回调策略并落地订单/成交记录
- [ ] 后端事件查询接口可用且前端可见
- [ ] 安全闸门（开关+规则+KillSwitch）生效
- [ ] 自动交易 E2E 测试通过
- [ ] `PROJECT_STATUS.md`、`docs/EXPERIENCE_SUMMARY.md`、`PLAN.md` 已同步
- [ ] 提交信息严格为 `feat(task-XX): ...` 且编号递增
