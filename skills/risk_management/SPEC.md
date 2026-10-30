# 风控开发参考

用于 RiskEngine、KillSwitch、风险阈值及拒绝/裁剪逻辑。查当前接口契约、配置和测试，不把文档示例数字视为生产默认值。

所有订单走既有风控；判断与风险事件可审计。风险不一致时 fail-closed，不能吞错。KillSwitch session 内升级不可逆：L0 NORMAL；L1 NO_NEW_POSITIONS，允许符合策略的平仓/撤单；L2 CANCEL_ALL_AND_HALT；L3 LIQUIDATE_AND_DISCONNECT。不要用“所有非零等级拒绝所有动作”的伪代码替代这些语义。

验证所改阈值的单位、正负号、边界、裁剪与拒绝；验证各等级对开仓、平仓和撤单的实际契约。并发、幂等与持久化要求见仓库业务规则。修改运行中的风控参数属于高风险操作，保留人工确认。
