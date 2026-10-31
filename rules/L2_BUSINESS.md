# 交易业务参考

涉及 Core、Adapter、Persistence、Policy 和 Control 的实现时读取。共享不变量见 `../AGENTS.md`，完整边界见 `../docs/PROJECT_ARCHITECTURE.md`。

| 平面 | 职责与限制 |
| --- | --- |
| Core：trader/core/ | 确定性领域计算、OMS；不引入网络、DB、环境读取或 sleep |
| Adapter：trader/adapters/ | IO、清洗、流隔离、REST 对齐；不直接修改核心状态 |
| Persistence：trader/adapters/persistence/ | append-only 事件与投影，保留 PG 风险事件持久化，不覆盖事件真相源 |
| Policy：trader/core/application/、trader/services/risk.py | 风险决策与 KillSwitch，不 fail-open |
| Control：trader/api/、trader/services/ | 生命周期及编排，不绕过风控直接下单 |

KillSwitch：L0 NORMAL；L1 NO_NEW_POSITIONS（允许符合策略的平仓/撤单）；L2 CANCEL_ALL_AND_HALT；L3 LIQUIDATE_AND_DISCONNECT。session 内升级不可逆；Adapter 降级通过 Control Plane 升级全局 KillSwitch。

Position、Exposure、Daily PnL、Unrealized PnL 分别说明输入、单位、公式、更新时点及估算/真实口径。成交数量乘价格表示成交名义金额，不通用于所有 PnL；行情重估、成本和费用按实际资产与合约定义计算。total_positions 的数量含义与 total_exposure 的净额/总额口径查当前接口契约，不从字段名猜测。

PnL 变化验证相关空仓/多仓、部分成交、费用和行情变化场景；风险规则变化检查拒绝、裁剪、升级及持久化。实际高风险动作保留人工确认，AI 建议不自动执行。
