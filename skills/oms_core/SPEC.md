# OMS Core Skill
# 版本: v1.0.0

## Metadata

- **Skill Name**: oms_core
- **触发场景**: OMS开发、订单状态机调试、幂等性修复、cl_ord_id/exec_id去重
- **Token成本**: ~180 tokens（核心指令）

---

## 核心指令

### 系统架构

OMS采用**单调状态机+幂等性保证**架构：

```
Order Request → OMS.check_order() → CAS Update → Order Event Log
                                         ↓
                                   Position Update
```

### 状态机定义

订单状态只能前进，禁止回退：

```
NEW → PARTIALLY_FILLED → FILLED
  ↓         ↓            ↓
CANCELLED  CANCELLED   (终态)
  ↓         ↓
REJECTED  (终态)
```

### 幂等性保证

- **cl_ord_id**: 客户端订单ID，全局唯一
- **exec_id**: 执行ID，Binance生成，用于去重
- **去重逻辑**: 基于 (cl_ord_id, exec_id) Tuple去重

---

## AI编程注意事项

### ✅ 应该做的

1. **CAS更新**: 使用Compare-And-Swap保证并发安全
2. **终态检查**: 操作前检查是否已是终态
3. **幂等去重**: WS和REST并发到达时不重复记账
4. **事件溯源**: 所有状态变更必须记录到Event Log

### ❌ 不要做的

1. **不要直接覆盖状态**: 必须通过CAS更新
2. **不要跳过幂等检查**: 必须去重
3. **不要回退终态**: FILLED/CANCELLED/REJECTED不可改

---

## 常见Bug模式

### Bug 1: 缺少终态检查

```python
# 错误示例
async def update_order(order_id, status):
    order.status = status  # 可能从FILLED回退

# 正确示例
async def update_order(order_id, new_status):
    order = await self._get_order(order_id)
    if order.status.is_terminal:
        raise InvalidStateTransition(order_id, order.status, new_status)
    await self._cas_update(order_id, new_status)
```

### Bug 2: 缺少幂等检查

```python
# 错误示例
async def on_fill(exec_id, qty, price):
    self._positions[symbol] += qty  # 可能重复记账

# 正确示例
async def on_fill(exec_id, qty, price):
    if (symbol, exec_id) in self._processed_fills:
        return  # 幂等去重
    self._processed_fills.add((symbol, exec_id))
    self._positions[symbol] += qty
```

---

## 接口契约

参考: `docs/INTERFACE_CONTRACTS.md` OMS契约

关键类型：
- `Order`: {cl_ord_id, status, symbol, side, qty, ...}
- `OrderStatus`: NEW, PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED
- `OrderEvent`: {cl_ord_id, exec_id, event_type, timestamp}