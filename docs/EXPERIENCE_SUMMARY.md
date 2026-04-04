# 经验总结文档

> 本文档记录项目开发过程中遇到的典型问题和解决方案，供团队成员参考。

---

## 一、代码质量经验

### 1.1 全局可变单例在异步上下文中的线程安全问题

**问题描述**：
在 `trader/api/routes/reconciler.py` 中使用了模块级全局变量管理单例：
```python
_app_reconciler_service: Optional[ReconcilerService] = None

def get_reconciler_service() -> ReconcilerService:
    global _app_reconciler_service
    if _app_reconciler_service is None:
        _app_reconciler_service = ReconcilerService()
    return _app_reconciler_service
```

**问题**：
- 非原子操作，存在竞态条件
- FastAPI 异步上下文中，高并发请求可能导致多个实例被创建

**解决方案**：
使用 `functools.lru_cache()` 替代：
```python
from functools import lru_cache

@lru_cache()
def get_reconciler_service() -> ReconcilerService:
    return ReconcilerService()
```

**经验**：
- 异步上下文中的单例模式应避免全局可变状态
- 优先使用标准库提供的线程安全工具

---

### 1.2 重复类定义导致的后续定义覆盖问题

**问题描述**：
在 `orderbook.py` 中定义了两次 `OrderBook` 类：
```python
class OrderBook:
    ...

class OrderBook:
    ...  # 第二个定义覆盖了第一个
```

**问题**：
Python 中类定义可重复，后面的会覆盖前面的，但 IDE 和类型检查器不会报错，导致难以调试的问题。

**解决方案**：
- 使用 IDE 的类型检查和 lint 工具
- 代码审查时注意检查重复定义

**经验**：
- Python 不阻止重复类定义，但后定义的会覆盖前面的
- 使用 pylint、mypy 等工具可检测此类问题

---

### 1.3 类型注解的一致性

**问题描述**：
在 `trader/services/reconciler_service.py` 中同时使用 `Optional` 和 `|` 语法：
```python
from typing import Optional  # 导入了但未直接使用

def __init__(self, storage: Optional[InMemoryStorage] = None, ...):
```

**问题**：
- `Optional[X]` 等价于 `X | None`
- 混用会导致代码风格不一致

**解决方案**：
- 统一使用 Python 3.10+ 的 `X | None` 语法，或
- 统一使用 `Optional[X]` 并保持导入

**经验**：
- 建议在项目中统一类型注解风格
- 配置 ruff/black 等工具强制格式化

---

## 二、测试经验

### 2.1 单元测试覆盖状态机转换

**经验**：
- 每个状态转换路径都需要测试
- 边界条件测试（0值、空输入、极限值）

### 2.2 异步代码测试

**经验**：
- 使用 `pytest-asyncio` 处理异步测试
- 确保所有 async 函数正确 await

### 2.3 测试隔离

**经验**：
- 每个测试应独立运行，不依赖其他测试的状态
- 使用 fixture 管理测试数据

---

## 三、工程实践

### 3.1 Core Plane 禁止 IO

**规则**：
- `core/` 目录下所有代码不得有网络、数据库、文件 IO
- 不得读取环境变量

**经验**：
- 将 IO 操作限制在 adapter 层和 service 层
- Core 层只负责业务逻辑计算

### 3.2 Fail-Closed 异常处理

**规则**：
- 异常处理必须 Fail-Closed
- 禁止裸 `except: pass`

**经验**：
- 所有异常路径必须记录日志
- 关键操作需要降级策略

### 3.3 幂等性设计

**规则**：
- 所有写操作必须幂等
- 重复调用结果一致

**经验**：
- 使用唯一键（cl_ord_id、event_id）确保幂等
- 设计接口时考虑重复调用的可能性

---

## 四、工具使用

### 4.1 虚拟环境

**经验**：
```powershell
# Windows 下使用虚拟环境
.venv/Scripts/python.exe -m pytest tests/ -v
```

### 4.2 Git 操作

**经验**：
```bash
# 查看当前分支状态
git status

# 查看未提交的更改
git diff HEAD

# 查看已暂存的更改
git diff --cached
```

### 4.3 GitHub CLI

**经验**：
```bash
# 创建 PR
gh pr create --title "feat: description" --body "..." --base main --head branch

# 合并 PR
gh pr merge #36 --squash --delete-branch
```

---

## 五、调试技巧

### 5.1 导入问题排查

**经验**：
- 检查 `__init__.py` 是否正确导出
- 使用 `python -c "import module; print(module.__file__)"` 验证路径

### 5.2 类型检查

**经验**：
```bash
mypy trader/core/domain/services/depth_checker.py
```

### 5.3 测试调试

**经验**：
```bash
# 运行单个测试
pytest trader/tests/test_depth_checker.py::TestDepthCheckerBasic -v

# 显示 print 输出
pytest -s trader/tests/test_depth_checker.py
```

### 5.4 Python命令行执行在PowerShell中输出为空的问题

**问题描述**：
使用 `python -c "..."` 在PowerShell中执行多行Python代码或复杂字符串时，输出经常为空，且无任何错误信息。

**问题原因**：
1. PowerShell字符串转义问题 - 反斜杠`\`、引号`"`等被PowerShell解释
2. 多行字符串在 `-c` 参数中处理复杂
3. stdout缓冲问题
4. Windows和Unix路径/换行符差异

**解决方案**：
**优先使用写文件再执行的方式**：
```powershell
# 错误方式 - 输出为空
python -c "import asyncio
async def test():
    print('hello')
asyncio.run(test())"

# 正确方式 - 可靠执行
# 1. 写测试脚本到文件
# 2. 执行脚本
python test_debug.py
```

**经验总结**：
1. **不要用 `-c` 执行多行Python代码** - 在PowerShell中容易出现转义和缓冲问题
2. **使用写文件再执行的方式** - 更可靠，可调试
3. **对于async代码，确保正确import asyncio并调用**
4. **输出为空可能是stdout缓冲问题** - 添加 `sys.stdout.flush()`
5. **调试脚本命名规范** - 使用 `test_debug.py` 或类似名称，完成后删除

**调试流程推荐**：
```powershell
# 1. 写调试脚本
Write-Content -Path "test_debug.py" -Value @"
import asyncio
async def test():
    print('hello')
asyncio.run(test())
"@

# 2. 执行
python test_debug.py

# 3. 检查结果后删除
Remove-Item test_debug.py
```

---

## 六、OnChain 适配器开发经验

### 6.1 STUB 实现标注规范

**经验**：
- STUB 实现必须在函数 docstring 首行标注 `[STUB IMPLEMENTATION]`
- 在代码中添加 TODO 注释说明需要接入的真实数据源
- 使用 `logger.debug` 而非 `logger.warning` 记录 STUB 状态，避免生产环境告警噪音
- 考虑是否应使用特性开关控制 STUB 代码的加载

### 6.2 外部 API 降级保护

**经验**：
- 所有外部 API 调用必须使用 try-except 包裹
- 限流 (429) 应使用指数退避重试
- 重试次数应有上限，超过后应优雅降级
- 降级时应记录有意义的日志，便于排查

### 6.3 CoinGecko API 使用注意

**经验**：
- 免费 API 有严格限流，测试环境中容易触发
- `total_supply` 字段可能返回 None，需使用 `or 0` 处理
- CoinGecko coin ID 与交易所 symbol 映射需要额外维护映射表

---

## 七、变更记录

| 日期 | 作者 | 描述 |
|------|------|------|
| 2026-03-21 | Kilo Code | 初始版本，记录 Reconciler 和 DepthChecker 开发经验 |
| 2026-03-22 | Kilo Code | 添加PowerShell中Python命令行执行输出为空问题的经验总结 |
| 2026-03-23 | Kilo Code | 添加OnChain适配器开发经验：STUB实现标注、外部API限流处理、降级保护设计 |
| 2026-03-25 | Kilo Code | 添加 PostgreSQL 投影读模型优化经验：索引查询优化、EventType 枚举设计、重构模式 |
| 2026-04-04 | Kilo Code | 添加 Phase 7 风控穿透验证与策略正期望证明经验：从"功能齐全"到"可信赖系统"的认知转变 |

## 八、PostgreSQL 投影读模型优化经验

### 8.1 索引查询优化

**场景**：`get_order_by_client_order_id` 需要 O(1) 查询性能

**经验**：
- 为 `client_order_id` 字段创建唯一索引
- 使用 `SELECT ... WHERE client_order_id = $1` 而非模糊查询
- 确保查询计划走索引扫描（使用 `EXPLAIN ANALYZE` 验证）

### 8.2 EventType 枚举设计

**场景**：避免字符串硬编码，提高类型安全性

**经验**：
- 在 projector 中定义 `EventType` 枚举，统一事件类型定义
- 使用枚举替代字符串比较，避免拼写错误
- 枚举值与数据库中的 event_type 字符串保持一致

### 8.3 重构 `_apply_position_increased` 方法

**经验**：
- 重构时保持方法签名不变，确保调用方兼容
- 添加清晰的日志记录重构前的行为差异
- 单元测试覆盖重构后的所有分支路径

---

## 七、架构约束经验

### 7.1 Core Plane 无 IO 约束的实现

**场景**：OMS 中的重试逻辑违反了 Core Plane 无 IO 约束

**问题描述**：
在 `trader/core/application/oms.py` 中，订单提交逻辑包含了 `asyncio.sleep()` 重试机制：
```python
for attempt in range(self._max_retries):
    try:
        broker_order = await self._broker.place_order(...)
    except BrokerNetworkError as e:
        await asyncio.sleep(2 ** attempt)  # 违反无 IO 约束
```

**问题**：
- Core Plane 应该是无 IO、完全确定性的
- `asyncio.sleep()` 是 IO 操作，违反了架构约束
- 重试逻辑应该由 Adapter 层处理

**解决方案**：
移除 OMS 中的重试逻辑，简化为单次调用：
```python
try:
    broker_order = await self._broker.place_order(...)
except BrokerNetworkError as e:
    order.reject(f"网络错误: {e}")
    # 不再重试，由 Adapter 层处理
```

**经验**：
- Core Plane 必须保持无 IO、完全确定性
- 重试、超时等 IO 相关逻辑应放在 Adapter 层
- 架构约束需要在代码审查时重点关注

---

### 7.2 FastAPI Lifespan 管理服务生命周期

**场景**：需要在应用启动/关闭时管理服务生命周期

**问题描述**：
`ReconcilerService` 需要在应用启动时启动后台任务，在应用关闭时清理资源。

**解决方案**：
使用 FastAPI 的 `lifespan` 上下文管理器：
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    reconciler_service = reconciler.get_reconciler_service()
    await reconciler_service.start()
    yield
    await reconciler_service.stop()

app = FastAPI(lifespan=lifespan)
```

**经验**：
- FastAPI 推荐使用 `lifespan` 管理应用生命周期
- 避免使用已弃用的 `@app.on_event("startup")` 装饰器
- 确保所有后台任务在应用关闭时正确停止

---

## 八、策略参数动态调整实现（Phase 4 Task 4.7）

### 8.1 动态参数调整的协议设计

**场景**：需要在策略运行期间动态调整参数，无需停止或重载策略

**设计方案**：
在 `StrategyPlugin` 协议中添加 `update_config()` 方法：
```python
async def update_config(self, config: Dict[str, Any]) -> ValidationResult:
    """
    更新策略配置参数
    
    允许在策略运行期间动态调整参数，无需停止策略。
    参数变更后应调用 validate() 确保配置有效。
    """
    ...
```

**实现经验**：
- `update_config()` 返回 `ValidationResult`，允许验证参数有效性
- 支持部分更新（增量更新），只传入需要修改的参数
- 如果插件不支持 `update_config()`，使用 `initialize()` 作为后备方案

### 8.2 策略运行器的参数更新

**场景**：`StrategyRunner` 需要提供统一的参数更新入口

**设计方案**：
```python
async def update_strategy_config(
    self,
    strategy_id: str,
    config: Dict[str, Any],
) -> StrategyRuntimeInfo:
    """更新策略配置参数"""
    plugin = self._plugins[strategy_id]
    
    # 调用策略的 update_config 方法
    validation_result = await plugin.update_config(config)
    if not validation_result.is_valid:
        raise ValueError(f"参数验证失败")
    
    # 更新存储的配置
    info.config = {**info.config, **config}
    return info
```

**实现经验**：
- 配置存储在 `StrategyRuntimeInfo.config` 中
- 合并策略配置时使用 `{**old_config, **new_config}` 实现部分更新
- 需要处理插件可能使用同步 `update_config()` 的情况

### 8.3 生命周期管理器的参数更新

**场景**：`StrategyLifecycleManager` 需要记录参数变更事件

**设计方案**：
新增 `PARAMS_UPDATED` 生命周期事件类型：
```python
class LifecycleEventType(Enum):
    ...
    PARAMS_UPDATED = "PARAMS_UPDATED"
```

记录参数变更到生命周期历史：
```python
lifecycle._transition_to(
    lifecycle.status,  # 状态不变
    LifecycleEventType.PARAMS_UPDATED,
    metadata={
        "updated_keys": list(new_config.keys()),
        "new_config": new_config,
    },
)
```

**实现经验**：
- 参数更新不改变生命周期状态
- 事件记录包含更新的参数键和新值，便于审计追溯
- 使用哈希锁保证并发安全

### 8.4 API 端点设计

**场景**：提供 HTTP API 允许外部系统动态调整策略参数

**设计方案**：
```python
@router.put("/v1/strategies/{strategy_id}/params")
async def update_strategy_params(
    strategy_id: str,
    request: UpdateStrategyParamsRequest,
):
    """
    Update strategy parameters.
    
    Supports partial updates (incremental updates).
    """
    runner = get_strategy_runner()
    info = await runner.update_strategy_config(strategy_id, request.config)
    return UpdateStrategyParamsResponse(
        success=True,
        strategy_id=strategy_id,
        updated_config=info.config,
    )
```

**实现经验**：
- `PUT` 方法表示完整的替换语义，但实现为部分更新
- 支持 `validate_only` 模式，仅验证参数不实际更新
- 错误时返回 400 或 500 状态码

---

## 九、待补充

- [ ] 添加更多典型 bug 案例
- [ ] 补充性能优化经验
- [ ] 添加安全相关经验
- [ ] 完善调试工具清单

---

## 十、文档真相源收敛经验（Phase 6 启动）

### 10.1 文档漂移比代码缺功能更容易误导排期

**场景**：
`PROJECT_STATUS.md` 已写明 Phase 5 完成，但同文件后部仍保留 Task 5.7 / 5.9 “待开始”；`PLAN.md` 顶部状态快照仍把多个已完成模块列为未开始。

**问题**：
- 工程输入不一致，导致后续任务规划偏离真实现状
- 容易重复排期、重复评估，浪费上下文切换成本
- 个人项目中，文档漂移会直接变成方向漂移

**经验**：
- 阶段切换时，先修正文档状态，再讨论新功能
- `PROJECT_STATUS.md` 负责“当前真实状态”
- `PLAN.md` 负责“当前执行主线”
- 单独的 phase 计划文件负责“可执行拆解”，避免把所有历史计划堆在一个文件里

### 10.2 当系统已具备分散风控能力时，应优先做收敛层

**场景**：
仓库已具备时间窗口、最大暴露、策略级限额、KillSwitch、回撤检查等能力，但仍缺少统一 sizing 决策。

**问题**：
- 规则分散在 `risk_engine`、`position_risk_constructor`、`strategy_runner` 等多个位置
- 只有 pass/reject，没有统一的"缩仓"语义
- 多策略并发时无法形成一致裁决

**经验**：
- 在继续扩功能前，优先收敛成 `risk_sizer` 和 `capital_allocator`
- "个人版生存风控"比"机构级组合风控"更符合单账户项目约束
- 风控演进顺序应是：硬限额 → 统一缩放 → 分配裁决 → 数据可靠性联动

---

## 十一、风控穿透验证与策略正期望证明经验（Phase 7 启动）

### 11.1 功能齐全 ≠ 可信赖，系统需要"可证伪"验证

**场景**：
各风控模块单元测试均通过（深度检查 19 tests、时间窗口 29 tests、RiskSizer 52 tests），但没有人验证过"风控真的改变了多少下单结果"。

**问题**：
- 代码里有 `if depth_check` 不等于风控真的生效
- 没有量化指标衡量风控实际拦截率
- 没有对照组证明风控在起作用（深度充足时通过 vs 深度不足时拒绝）

**经验**：
- 从"写更多分析报告"开始是错的，从"可证伪"开始才是对的
- 两个最小实验目标：
  1. 证明"风控真的改变下单结果"（Risk Intervention Rate）
  2. 证明"策略扣掉真实成本后仍有正期望"（成本压测 + 样本外验证）
- 不要先看 Sharpe 多高、收益多漂亮，先看"能不能被证伪"

### 11.2 风控验证的本质是"对照组实验"

**场景**：
需要验证深度检查、风控时间窗口、KillSwitch、日亏损阈值等规则是否真的改变订单命运。

**问题**：
- 单元测试只验证"代码逻辑正确"，不验证"实际生效"
- 需要证明"同一个信号，进入风控前后输出真的变了"

**经验**：
- 风控穿透测试矩阵：每条规则对应 4 类场景（通过/缩单/拒单/停机）
- 必须有反例对照组：深度充足时同一信号通过，深度不足时同一信号拒绝
- 验证标准只有三个：
  1. 结果必须改变订单命运（不是打印日志，是 OMS 行为真的变了）
  2. 结果必须可回放（trace_id + signal_id + rule_name + action）
  3. 必须有反例（没有对照组，就证明不了风控在起作用）

### 11.3 策略"正期望"不看什么

**场景**：
Phase 5 完成了回测框架升级（QuantConnect Lean、无前瞻偏差、方向感知滑点、止盈止损支持），但还需要验证"扣成本后样本外是否仍为正期望"。

**问题**：
- 不要先看年化收益多高、Sharpe 多漂亮、单段行情多惊艳
- 这些都是"自证预言"式的验证方式

**经验**：
- 先看四个更本质的东西：
  1. 成本后期望（Expectancy = avg_win × win_rate - avg_loss × loss_rate - avg_cost > 0）
  2. 最大回撤是否在生存边界内
  3. 样本外是否还活着（Walk-Forward Sharpe 衰减 < 20%）
  4. 参数是否脆弱（稍微一动就从赚钱变亏钱）
- 5 层验证门控：
  - L1: 机制假设（为什么会赚钱？什么情况下失效？）
  - L2: 回测合规（下一 bar 执行、方向感知滑点、止盈止损）
  - L3: 样本外验证（Walk-Forward + K-Fold）
  - L4: 成本压测（1x/1.5x/2x 成本）
  - L5: 影子模式（回测信号 vs 实盘信号 vs 成交偏差）

### 11.4 审计骨架 ≠ 强审计

**场景**：
系统有 Event Sourcing 到 PG、OMS 事件持久化、幂等订单、AIAuditLog 字段设计完整，但 AIAuditLog 当前是 InMemoryAuditLogStorage，控制面快照仍是内存读模型。

**问题**：
- 有审计骨架不等于"监管/机构级可举证"
- 内存存储在进程重启后丢失
- 缺少统一 decision_id 串起完整证据链

**经验**：
- 强审计需要两个条件：
  1. 持久化存储（PG，而非内存）
  2. 统一关联键（decision_id 贯穿 signal → risk → order_intent → exchange_order → reconcile）
- 最该补的一刀：强制每次策略决策产生 `market_state_ref + feature_version + signal_id + decision_id + risk_action + order_intent_id + exchange_order_id + reconcile_result`
- 这条链在 PG 里稳定落下，系统才算真正"强可审计"
