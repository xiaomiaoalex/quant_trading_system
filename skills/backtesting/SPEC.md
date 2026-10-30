# 回测开发参考

用于 EventDrivenRiskReplay、BacktestRiskIntegration、vectorbt 适配和模拟执行。先查 `docs/INTERFACE_CONTRACTS.md` 的回放契约及当前实现。

信号按事件时间回放，经 BacktestRiskIntegration 和 RiskEngine 后形成 APPROVED/CLIPPED/REJECTED；有效数量为 None 或非正数时拒绝，REJECTED 保留原因，风控异常 fail-closed。保留 KillSwitch，持仓通过既有订单/成交事件更新。

权益按现金、持仓市值和费用的实际口径计算，不能把每笔买卖成交额都加到权益。验证买卖方向、部分成交、手续费、拒绝单、行情重估及无未来数据泄漏；拒绝计数 Counter 本身不能证明幂等。

字段映射以当前契约为准：signal_id 与 cl_ord_id、quantity 与 qty、effective_quantity 与 normalized_qty。配置模板见 `resources/config_template.yaml`，使用说明见 `resources/README.md`。
