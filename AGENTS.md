# quant_trading_system

## 工作入口

默认中文。先定位当前任务涉及的实现、调用点和测试，再修改。完成已授权工作；常规选择自行处理，缺失信息仅在会改变结果时澄清。保留已有未提交修改。

- 接口、DTO、事件、跨层调用或字段改动：查 `docs/INTERFACE_CONTRACTS.md`，实现与契约在同次交付中保持一致。
- 层级、职责、数据流或部署拓扑改动：查并同步 `docs/PROJECT_ARCHITECTURE.md`。
- Python 工程约定：按需读 `rules/L1_TECH_STACK.md`；交易业务改动读 `rules/L2_BUSINESS.md`。
- 回测、风控、Binance、OMS：从 `skills/_meta/index.yaml` 选相关 SPEC；这些是仓库参考文件，不要求预加载全部。
- 多阶段或跨模块工作：按需读 `rules/L3_WORKFLOW.md`；RFC 用于需要记录的设计决策，已有明确授权时不设额外签字阶段。
- 前端修改另读 `Frontend/AGENTS.md`。`.claude/get-shit-done/` 是 Claude 专用可选工作流，仅在用户选择该流程时加载；嵌套 worktree 不是当前仓库指令来源。

## 交易正确性

- Core 保持无 IO、确定性；外部字段在 Adapter/API 边界转换。通过既有跨层接口协作，不绕过领域层改状态。
- 订单状态通过统一 CAS 入口前进，终态不可回退；按 `cl_ord_id`、`exec_id` 去重，WS/REST 并发不得重复记账。
- 同订单事件按 hashed lock 或 actor 分区串行化，不依赖 asyncio 调度顺序。
- Public/Private Stream 状态隔离；重连先 REST Alignment 再恢复业务。交易所用于外部对账，append-only Event Log 用于本地回放；风险事件保留 PG 持久化要求。
- 无法确认一致性时 DEGRADED_MODE 并经 Control Plane 升级 KillSwitch；session 内升级不可逆。不能吞错或 fail-open。
- AI 只做建议、诊断和分析；未通过离线评测/影子运行门禁不得进入执行链路。实际高风险操作保留人工确认，不能绕过 Risk/Policy/Core。

## 验证与交付

行为变更增加或调整有意义的测试；缺陷和状态机修改优先复现失败。使用现有真实接口及 fakes，单测不访问网络，时间逻辑使用 fake clock。文案或规则修改检查链接、一致性和差异即可。

定向测试：`python -m pytest -q trader/tests/<相关测试文件> --tb=short`。
共享交易核心、适配或风控变更运行 P0：

```text
python -m pytest -q trader/tests/test_binance_connector.py trader/tests/test_binance_private_stream.py trader/tests/test_binance_degraded_cascade.py trader/tests/test_deterministic_layer.py trader/tests/test_hard_properties.py --tb=short
```

持久化变更补相关 Postgres 集成测试；合并前以 `.github/workflows/ci-gate.yml` 的实际门禁为准。未运行或环境阻塞的检查如实报告，不标为通过。

仅更新被本次改动影响的契约、架构、状态或计划。可复用经验才写 `docs/EXPERIENCE_SUMMARY.md`；开发日志追加重要决策与验证，不改写历史。无需为每次小改动回写所有文档。

`main` 通过 PR 合并；沿用已有 Task ID 与 Conventional Commits，无已有编号时不凭空制造。只暂存本任务文件。实际生产部署、真实账户操作、外发、不可逆数据/历史修改须有明确授权；已授权的本地修复、迁移草案和 CI 编辑不重复请示。
