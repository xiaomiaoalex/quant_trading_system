# Binance Adapter Skill
# 版本: v1.0.0

## Metadata

- **Skill Name**: binance_adapter
- **触发场景**: Binance连接器开发、WebSocket流调试、REST对齐、限流退避、DegradedCascade
- **Token成本**: ~200 tokens（核心指令）

---

## 核心指令

### 系统架构

Binance适配器采用**物理隔离+级联保护**架构：

```
Public Stream (市场数据) ─┬─→ Connector ─→ Adapter Plane
                          │
Private Stream (账户数据) ─┘
        ↓
REST Alignment (重连纠偏)
        ↓
DegradedCascadeController (级联保护)
```

### 关键约束

1. **Public/Private Stream物理隔离**: 禁止共享状态
2. **REST对齐**: 重连后必须先REST Alignment再恢复业务
3. **限流退避**: 使用全抖动退避算法
4. **DegradedCascade**: 逐级降级保护 → 上报KillSwitch

---

## AI编程注意事项

### ✅ 应该做的

1. **检查WS连接状态**: 发送消息前验证连接是否活跃
2. **处理乱序**: 使用sequence number检测乱序和丢包
3. **限流控制**: 使用令牌桶算法控制请求频率
4. **重连退避**: 使用全抖动退避避免雪崩

### ❌ 不要做的

1. **不要共享Stream状态**: Public和Private Stream独立
2. **不要绕过REST Alignment**: 重连后必须对齐
3. **不要忽略错误码**: Binance错误码必须正确处理

---

## 常见Bug模式

### Bug 1: 忽略Stream断开

```python
# 错误示例
async def on_message(msg):
    process(msg)

# 正确示例
async def on_message(msg):
    if not self._connected:
        await self._reconnect()
    process(msg)
```

### Bug 2: 缺少REST Alignment

```python
# 错误示例
async def on_reconnect():
    await self._subscribe()

# 正确示例
async def on_reconnect():
    await self._rest_alignment()  # 必须先对齐
    await self._subscribe()
```

---

## 接口契约

参考: `docs/INTERFACE_CONTRACTS.md` Binance Adapter契约