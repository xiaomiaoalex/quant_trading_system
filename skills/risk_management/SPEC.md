# Risk Management Skill
# 版本: v1.0.0

## Metadata

- **Skill Name**: risk_management
- **触发场景**: 风控引擎开发、RiskEngine调试、KillSwitch配置、风险阈值调整
- **Token成本**: ~180 tokens（核心指令）

---

## 核心指令

### 系统架构

风控系统采用**多层防护+Fail-Closed**架构：

```
Signal → RiskEngine.check_pre_trade() → RiskCheckResult(passed, details)
                                    ↓
                        KillSwitch级别判断
                                    ↓
                        APPROVED / CLIPPED / REJECTED
```

**关键约束**：
- 日亏损限制: 默认-5%
- 最大回撤限制: 默认-10%
- 持仓数限制: 默认5个
- 订单频率限制: 10秒最多3单

### KillSwitch级别

| 级别 | 名称 | 行为 |
|------|------|------|
| L0 | NORMAL | 正常交易 |
| L1 | NO_NEW_POSITIONS | 禁止新开仓，允许平仓/撤单 |
| L2 | CANCEL_ALL_AND_HALT | 撤所有挂单，禁止交易 |
| L3 | LIQUIDATE_AND_DISCONNECT | 强平所有持仓，断开Broker |

**重要**: 所有KillSwitch升级在session内不可逆（Fail-Closed）

---

## AI编程注意事项

### ✅ 应该做的

1. **检查KillSwitch状态**: 执行任何订单前先检查当前KillSwitch级别
2. **记录风险事件**: 所有RiskEngine判断必须记录到事件日志
3. **Fail-Closed**: 无法确认一致性时，触发KillSwitch升级
4. **幂等去重**: 基于cl_ord_id + exec_id进行去重

### ❌ 不要做的

1. **不要Fail-Open**: 禁止裸`except: pass`
2. **不要绕过RiskEngine**: 所有订单必须通过风控
3. **不要假设执行顺序**: 并发控制必须用hashed lock

---

## 常见Bug模式

### Bug 1: KillSwitch未检查

```python
# 错误示例
async def submit_order(order):
    return await broker.create_order(order)

# 正确示例
async def submit_order(order):
    if killswitch.current_level > KillSwitchLevel.NORMAL:
        return OrderResult(status=REJECTED, reason="KillSwitch Active")
    return await broker.create_order(order)
```

### Bug 2: 风险阈值超限未升级KillSwitch

```python
# 错误示例
if daily_loss > MAX_DAILY_LOSS:
    log_warning("Loss exceeded")
    return REJECTED

# 正确示例
if daily_loss > MAX_DAILY_LOSS:
    log_critical("Loss exceeded, upgrading KillSwitch")
    await killswitch.upgrade_to(KillSwitchLevel.L1)
    return REJECTED
```

---

## 接口契约

参考: `docs/INTERFACE_CONTRACTS.md` 风控相关契约

关键类型：
- `RiskCheckResult`: {passed: bool, details: dict}
- `RiskEngine`: check_pre_trade(signal) -> RiskCheckResult
- `KillSwitch`: current_level, upgrade_to(), is_active()