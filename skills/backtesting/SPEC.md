# Backtesting Skill
# 版本: v1.0.0

## Metadata

- **Skill Name**: backtesting
- **触发场景**: 回测系统开发、EventDrivenRiskReplay调试、vectorbt适配、风险回放
- **Token成本**: ~200 tokens（核心指令）

---

## 核心指令

### 系统架构

回测系统采用**事件驱动+风控集成**架构：

```
Signal/Bar Events → EventDrivenRiskReplay → BacktestRiskIntegration → RiskEngine → 模拟执行
```

**关键组件**：
- `event_driven_risk_replay.py`: 回放编排器，按时间顺序处理signal/bar events
- `backtest_risk_integration.py`: 风控集成，调用RiskEngine.check_pre_trade()
- `vectorbt_adapter.py`: 向量回测引擎适配器
- `execution_simulator.py`: 模拟执行器，处理成交、滑点、手续费

### 数据流

1. **输入**: Signal对象列表（包含symbol, quantity, price, timestamp, signal_id）
2. **处理**: EventDrivenRiskReplay.replay() 按时间顺序回放
3. **风控**: BacktestRiskIntegration.evaluate_signal() 调用完整风控
4. **决策**: APPROVED/CLIPPED/REJECTED
5. **输出**: EventDrivenRiskReplayResult（包含orders, fills, equity_curve, max_drawdown）

### 状态枚举

```python
class OrderDecision(str, Enum):
    APPROVED = "APPROVED"  # 订单通过风控，正常执行
    CLIPPED = "CLIPPED"   # 数量被裁剪，使用effective_quantity执行
    REJECTED = "REJECTED"  # 订单被拒，记录但不执行
```

### 关键数据结构

```python
@dataclass(frozen=True, slots=True)
class ReplayOrder:
    symbol: str
    side: OrderSide
    qty: Decimal              # 原始数量
    price: Decimal            # 原始价格
    timestamp_ms: int
    decision: OrderDecision   # APPROVED/CLIPPED/REJECTED
    normalized_qty: Decimal   # 风控后数量（CLIPPED时可能小于qty）
    normalized_price: Decimal
    rejection_reason: str | None
    fills: list[ReplayFill]

@dataclass(frozen=True, slots=True)
class ReplayFill:
    symbol: str
    side: OrderSide
    qty: Decimal
    price: Decimal
    timestamp_ms: int
    commission: Decimal = Decimal("0")
```

---

## AI编程注意事项

### ✅ 应该做的

1. **保持幂等性**: rejection_counter是Counter类型，用于统计拒绝原因
2. **处理边界**: effective_qty为None或<=0时，生成REJECTED而非CLIPPED
3. **Fail-Closed**: 风控异常时生成REJECTED结果，记录错误到errors列表
4. **更新权益曲线**: 每笔订单后计算current_equity，记录到equity_curve

### ❌ 不要做的

1. **不要绕过RiskEngine**: 所有订单必须通过BacktestRiskIntegration.evaluate_signal()
2. **不要直接修改positions**: 必须通过order流程更新持仓
3. **不要遗漏rejection_reason**: REJECTED订单必须填充rejection_reason字段
4. **不要在回测中忽略KillSwitch**: 检查KillSwitch级别决定是否执行

---

## 常见Bug模式

### Bug 1: effective_qty未检查

```python
# 错误示例
normalized_qty = effective_qty

# 正确示例
if effective_qty is None or effective_qty <= 0:
    # 生成REJECTED
else:
    normalized_qty = effective_qty
```

### Bug 2: rejection_reason遗漏

```python
# 错误示例
rejection_reason = None  # 不应该为None

# 正确示例
reason = signal_result.rejection_reason or "UNKNOWN"
```

### Bug 3: 权益曲线未更新

每笔订单执行后必须更新equity_curve：

```python
fill_value = fill.qty * fill.price - fill.commission
current_equity += fill_value if side == OrderSide.BUY else fill_value
result.equity_curve.append(current_equity)
```

---

## 接口契约

参考: `docs/INTERFACE_CONTRACTS.md` 8.11.6 EventDrivenRiskReplay v1契约

关键字段映射：
- Signal.signal_id → Order.cl_ord_id
- signal.quantity → Order.qty
- signal.price → Order.price
- signal_result.effective_quantity → Order.normalized_qty

---

## Resources

按需加载以下资源：

- `resources/risk_thresholds.yaml`: 风控阈值配置示例
- `resources/backtest_config.yaml`: 回测配置模板