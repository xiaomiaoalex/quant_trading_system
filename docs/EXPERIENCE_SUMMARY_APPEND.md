---

## 十七、Phase A-F 脚本实现踩坑记录 (2026-04-16)

### 17.1 dataclass 字段顺序与默认值约束

**问题描述**：
在 `model_drift_detector.py` 的 `DriftMetrics` 中，字段顺序导致 LSP 报错。

**根因**：
Python dataclass 要求：有默认值的字段必须在无默认值字段之后。

**解决方案**：
将无默认值字段放在前面，有默认值字段放在后面。

---

### 17.2 dataclass 中 Literal 类型赋值问题

**问题描述**：
在 `__post_init__` 中，直接赋值 `self.drift_severity = severity` 报错。

**解决方案**：
使用 `object.__setattr__()` 绕过 dataclass 的类型检查。

---

### 17.3 Qlib/Hermes 边界设计原则

**核心约束**：
1. Hermes 只调用研究脚本，不直接调用下单接口
2. Qlib 只进入 Insight/Research 路径，不直接触发下单
3. Core Plane 保持 IO-clean / AI-clean，执行依旧走 StrategyRunner + RiskEngine + OMS

**架构边界**：
Hermes -> Qlib -> qlib_to_strategy_bridge -> StrategyRunner + RiskEngine + OMS -> Broker Adapter

---

## 十八、fire_test 信号方向错误与异步 Handler 修复 (2026-04-20)

### 18.1 Python Enum 字符串转换陷阱

**问题描述**：
fire_test 策略启动后第一次信号总是 SELL 而不是 BUY，导致订单被 Binance 拒绝。

**根因**：
`str(SignalType.BUY)` 返回 `"SignalType.BUY"` 而非 `"BUY"`。

**错误代码**：
```python
side = OrderSide.BUY if str(signal.signal_type).upper() in ("BUY", "LONG") else OrderSide.SELL
# "SIGNALTYPE.BUY" 不在 ("BUY", "LONG") 中，总是返回 SELL
```

**正确代码**：
```python
side = OrderSide.BUY if signal.signal_type.value.upper() in ("BUY", "LONG") else OrderSide.SELL
# 使用 .value 获取枚举的实际值
```

**教训**：获取枚举值时应使用 `.value` 属性，而不是 `str()` 转换。

---

### 18.2 异步 Handler 未 Await

**问题描述**：
RuntimeWarning: coroutine 'fill_handler' was never awaited

**根因**：
`_on_fill_update()` 调用 `handler(update)` 时未 await 异步 handler

**修复**：
在 `connector.py` 和 `private_stream.py` 中添加 `_dispatch_handler()` 方法自动检测并处理：
```python
def _dispatch_handler(self, handler: Callable, *args) -> None:
    try:
        if asyncio.iscoroutinefunction(handler):
            loop = asyncio.get_event_loop()
            loop.create_task(handler(*args))  # 创建 task 异步执行
        else:
            handler(*args)
    except Exception as e:
        logger.error(f"Handler error: {e}")
```

**教训**：异步编程中，调用异步函数必须 await 或创建 task，避免 coroutine 被遗漏。

---

### 18.3 策略状态生命周期管理

**问题描述**：
fire_test 使用单例模式 (`_plugin_instance`)，stop/start 后状态未重置。

**表现**：
第一次启动后信号方向正确，stop 后再 start，第一次信号变成 SELL。

**修复**：
- `strategy_runner.stop()` 调用 `plugin.shutdown()` 重置状态
- `strategy_runner.start()` 调用 `plugin.initialize()` 确保状态干净

**教训**：单例策略需要显式管理状态生命周期，stop/start 不应保留上次运行状态。

---

### 18.4 余额预检查逻辑不完整

**问题描述**：
BUY 成功后，SELL 订单因"BTC 余额不足"被拒。

**根因**：
余额预检查只检查 USDT 余额，未检查 BTC 余额。

**修复**：
根据交易方向检查对应资产：
```python
if side == OrderSide.BUY:
    # 检查 quote asset (USDT)
elif side == OrderSide.SELL:
    # 检查 base asset (BTC)
```

**教训**：通用交易逻辑中，余额检查应根据交易方向检查正确的资产。

---

### 18.5 前端状态映射错误

**问题描述**：
前端把 `stopped` 状态当作"未加载"处理，stop 后显示 Load 按钮而不是 Start 按钮。

**修复**：
```tsx
// 修复前
{status === 'stopped' && <button>Load</button>}

// 修复后
{status === null && <button>Load</button>}
{(status === 'loaded' || status === 'stopped') && <button>Start</button>}
```

**教训**：后端状态与前端展示状态需要明确映射关系，避免语义混淆。

---

### 19. VectorBT 回测重构踩坑记录 (2026-04-22)

#### 19.1 重复代码目录清理

**问题描述**：
存在两个相同的 `backtesting/` 目录：
- `services/backtesting/` (根目录，14个文件，10520行)
- `trader/services/backtesting/` (正确的路径)

**根因**：
早期开发时创建的副本，长期未清理。

**解决方案**：
```bash
rm -rf services/backtesting/
rm trader/services/backtesting/quantconnect_adapter.py
```

**教训**：重复代码会误导开发、维护成本高，定期用 `grep` 或工具检查无引用文件。

#### 19.2 test_backtesting_adapters.py 残留导入

**问题描述**：
删除 `quantconnect_adapter.py` 后，测试文件仍有残留导入，导致 `NameError` 类未定义。

**根因**：
删除文件时未同步更新引用该文件的测试。

**解决方案**：
删除依赖已删除模块的测试类（`TestQuantConnectDataAdapter`、`TestTimeFrame` 等），保留 `execution_simulator`、`strategy_adapter` 等仍存在模块的测试。

**教训**：删除任何文件前，先 `grep` 检查引用，删除后同步更新引用方。

#### 19.3 Binance Demo URL 错误

**问题描述**：
`BinanceDataProvider` 初始使用 `https://testnet.binance.vision/api`，但用户实际用的是 `https://demo.binance.com`。

**解决方案**：
```python
# 修改前
base_url: str = "https://testnet.binance.vision/api"
# 修改后
base_url: str = "https://demo.binance.com"
```

**教训**：Binance 有多个 demo/test 端点，集成前需和用户确认实际使用的 URL。

#### 19.4 Port/Adapter 架构的价值

**设计验证**：
VectorBT 重构成功验证了 Port/Adapter 架构的实用性：
- `BacktestEnginePort` 接口不变 → 切换引擎实现只需换 `BinanceExecutionAdapter` 内部的 `_vectorbt`
- 新增 `BinanceDataProvider` 实现 `DataProviderPort` → 数据源可替换
- 定制逻辑（KillSwitch/Risk/OMS）通过 callback 注入，不污染引擎代码

**关键代码**：
```python
class BinanceExecutionAdapter:
    def __init__(self, killswitch_callback=None, risk_callback=None, oms_callback=None):
        self._killswitch_callback = killswitch_callback
        self._risk_callback = risk_callback
        self._oms_callback = oms_callback
        self._vectorbt = VectorBTAdapter()  # 可替换的引擎

    async def run_backtest(self, config, strategy):
        if self._killswitch_callback and self._killswitch_callback() >= 2:
            return BacktestResult(..., metrics={"blocked_by": "KillSwitch"})
        result = await self._vectorbt.run_backtest(config, strategy)
        if self._oms_callback:
            for trade in result.trades:
                self._oms_callback({"type": "backtest_fill", "trade": trade})
        return result
```

**架构意义**：业务逻辑（KillSwitch/Risk/OMS）与撮合引擎（VectorBT）解耦，两者独立演进。
