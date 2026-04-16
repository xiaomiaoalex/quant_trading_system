# 经验总结文档

> 本文档记录项目开发过程中遇到的典型问题和解决方案，供团队成员参考。

---

## 一、代码质量经验

### 1.1 PostgreSQLStorage API 契约与调用方不匹配问题

**问题描述**：
在 `PortfolioProposalStore` 实现中，调用了 `PostgreSQLStorage` 不存在的方法：
```python
# 错误代码
self._postgres = PostgreSQLStorage()
await self._postgres.initialize()  # 不存在，应该是 connect()
await self._postgres.execute(...)  # 不存在，应该用 acquire()
await self._postgres.fetchone(...)  # 不存在，应该用 acquire()
```

**问题**：
- `PostgreSQLStorage` 使用 `acquire()` 获取连接，然后调用 `conn.execute()` / `conn.fetch()` / `conn.fetchrow()`
- `initialize()` 方法不存在，应该用 `connect()`
- `is_postgres_available()` 是同步函数，却被 `await` 调用

**解决方案**：
```python
# 正确代码
self._postgres = PostgreSQLStorage()
await self._postgres.connect()  # 正确

async with self._postgres.acquire() as conn:
    await conn.execute(query, *params)  # 正确
    rows = await conn.fetch(query, *params)  # 正确
    row = await conn.fetchrow(query, *params)  # 正确

if is_postgres_available():  # 同步函数，不需要 await
    ...
```

**经验**：
- 使用第三方存储类时，必须先阅读其 API 契约
- 不要假设方法存在，要基于实际实现的接口编程
- LSP/类型检查错误通常反映真实的 API 不匹配问题

---

### 1.2 全局可变单例在异步上下文中的线程安全问题

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

### 1.3 重复类定义导致的后续定义覆盖问题

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

### 1.4 类型注解的一致性

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

## 七、PostgreSQL 投影读模型优化经验

### 7.1 索引查询优化

**场景**：`get_order_by_client_order_id` 需要 O(1) 查询性能

**经验**：
- 为 `client_order_id` 字段创建唯一索引
- 使用 `SELECT ... WHERE client_order_id = $1` 而非模糊查询
- 确保查询计划走索引扫描（使用 `EXPLAIN ANALYZE` 验证）

### 7.2 EventType 枚举设计

**场景**：避免字符串硬编码，提高类型安全性

**经验**：
- 在 projector 中定义 `EventType` 枚举，统一事件类型定义
- 使用枚举替代字符串比较，避免拼写错误
- 枚举值与数据库中的 event_type 字符串保持一致

### 7.3 重构 `_apply_position_increased` 方法

**经验**：
- 重构时保持方法签名不变，确保调用方兼容
- 添加清晰的日志记录重构前的行为差异
- 单元测试覆盖重构后的所有分支路径

---

## 八、架构约束经验

### 8.1 Core Plane 无 IO 约束的实现

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

### 8.2 FastAPI Lifespan 管理服务生命周期

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

## 九、策略参数动态调整实现（Phase 4 Task 4.7）

### 9.1 动态参数调整的协议设计

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

### 9.2 策略运行器的参数更新

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

### 9.3 生命周期管理器的参数更新

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

### 9.4 API 端点设计

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

## 十、待补充

- [ ] 添加更多典型 bug 案例
- [ ] 补充性能优化经验
- [ ] 添加安全相关经验
- [ ] 完善调试工具清单

---

## 十一、文档真相源收敛经验（Phase 6 启动）

### 11.1 文档漂移比代码缺功能更容易误导排期

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

### 11.2 当系统已具备分散风控能力时，应优先做收敛层

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

## 十二、风控穿透验证与策略正期望证明经验（Phase 7 启动）

### 12.1 功能齐全 ≠ 可信赖，系统需要"可证伪"验证

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

### 12.2 风控验证的本质是"对照组实验"

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

### 12.3 策略"正期望"不看什么

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

### 12.4 审计骨架 ≠ 强审计

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

---

## 十三、FastAPI 测试覆盖经验（Phase 8 Task 8 补充）

### 13.1 新增 API 路由必须在 main.py 中注册

**场景**：
新增了 `chat.py` 和 `portfolio_research.py` 两个 API 路由文件，但忘记在 `main.py` 中注册。

**问题**：
- 测试时所有端点返回 404 Not Found
- 路由文件存在但未生效

**解决方案**：
```python
# trader/api/main.py
from trader.api.routes import chat, portfolio_research

app.include_router(chat.router)
app.include_router(portfolio_research.router)
```

**经验**：
- 新增路由文件后，必须在 `main.py` 中 `include_router`
- 测试前先验证路由是否正确注册

### 13.2 Pydantic 响应模型必须继承 BaseModel

**场景**：
在 `portfolio_research.py` 中定义了响应模型但未继承 `pydantic.BaseModel`。

**问题**：
```python
# 错误代码
class ApprovalResponse:
    success: bool
    run_id: str
```

FastAPI 无法正确处理响应序列化。

**解决方案**：
```python
# 正确代码
from pydantic import BaseModel

class ApprovalResponse(BaseModel):
    success: bool
    run_id: str
```

**经验**：
- FastAPI 响应模型必须继承 `pydantic.BaseModel`
- 否则 OpenAPI 文档生成和响应验证都会出问题

### 13.3 Mock 枚举类型需要实现 value 属性

**场景**：
测试中使用 Mock 对象模拟枚举类型，但 API 代码中访问了 `enum.value`。

**问题**：
```python
# 错误代码
mock_session.status = "active"  # 字符串没有 .value 属性

# API 代码
return SessionResponse(status=session.status.value)  # AttributeError
```

**解决方案**：
```python
# 正确代码
class MockSessionStatus:
    def __init__(self, value: str = "active"):
        self.value = value

mock_session.status = MockSessionStatus("active")
```

**经验**：
- 当 API 代码访问枚举的 `.value` 属性时，Mock 对象也需要实现该属性
- 可以创建简单的 Mock 类来模拟枚举行为

### 13.4 测试中 Mock 路径必须匹配实际导入路径

**场景**：
在 `portfolio_research.py` 中导入了 `PortfolioProposalStore`，测试时 Mock 路径写错。

**问题**：
```python
# 错误代码
with patch("trader.api.routes.portfolio_research.PortfolioProposalStore", ...):
```

**解决方案**：
```python
# 正确代码
with patch("trader.adapters.persistence.portfolio_proposal_store.PortfolioProposalStore", ...):
```

**经验**：
- Mock 路径应该是模块定义的位置，而不是使用它的位置
- 查看 `from ... import ...` 语句确定正确的 Mock 路径

---

## 十四、变更记录

| 日期 | 作者 | 描述 |
|------|------|------|
| 2026-03-21 | Kilo Code | 初始版本，记录 Reconciler 和 DepthChecker 开发经验 |
| 2026-03-22 | Kilo Code | 添加PowerShell中Python命令行执行输出为空问题的经验总结 |
| 2026-03-23 | Kilo Code | 添加OnChain适配器开发经验：STUB实现标注、外部API限流处理、降级保护设计 |
| 2026-03-25 | Kilo Code | 添加 PostgreSQL 投影读模型优化经验：索引查询优化、EventType 枚举设计、重构模式 |
| 2026-04-04 | Kilo Code | 添加 Phase 7 风控穿透验证与策略正期望证明经验：从"功能齐全"到"可信赖系统"的认知转变 |
| 2026-04-05 | Kilo Code | 添加 FastAPI 测试覆盖经验：路由注册、Pydantic 模型、Mock 枚举、Mock 路径 |
| 2026-04-10 | Kilo Code | 添加 Truth Gap 后端修复经验：API 契约优先、后端聚合模式、Bug 审计与修复方法论 |
| 2026-04-16 | Kilo Code | 添加 v3.4.0 文档升级与 Qlib/Hermes 集成规划经验：研究编排与执行链路隔离原则 |
| 2026-04-16 | Codex | 添加内置策略误删恢复经验：插件协议对齐、单测护栏与入口一致性检查 |

---

## 十五、Truth Gap 修复经验

### 15.1 API 契约优先原则

**问题**：
Truth Gap 产生的原因往往是前端基于"期望"而非"实际实现的 API"进行开发。

**经验**：
- 开发新 API 时，先写 OpenAPI 文档或契约测试
- 前端不允许基于 TODO 或猜测调用 API，必须有实际端点支撑
- 缺失能力必须标注 `// TODO: BLOCKED BY BACKEND API`，而不是脑补实现

### 15.2 后端真聚合 vs 前端聚合

**问题**：
`GET /v1/monitor/snapshot` 使用 query 参数传递数据，前端需要自己聚合多个 API 调用结果。

**修复方案**：
- 后端内部聚合 orders/pnl/killswitch/adapters 数据
- 前端只需调用一个端点即可获得完整快照
- 添加 `snapshot_source` 和 `freshness` 元信息

**经验**：
- 控制面 API 应提供"聚合视图"，而不是让前端做数据组合
- 元信息（来源、新鲜度）对于前端判断数据可用性至关重要

### 15.3 无参触发模式设计

**问题**：
`POST /v1/reconciler/trigger` 要求前端提交完整的 local_orders 和 exchange_orders，导致联调困难。

**修复方案**：
- 支持无参触发：后端自动从 OrderService 和 BinanceSpotDemoBroker 获取数据
- 保留带参模式用于测试场景
- 使用 Optional 请求体设计
- 交易所 broker 配置从环境变量读取 (BINANCE_API_KEY, BINANCE_SECRET_KEY)

**经验**：
- "一键触发"类操作应默认自动聚合，后端自行获取所需数据
- 前端只需关心操作结果，不应要求其理解内部数据来源
- 使用真实 broker 而非 fake broker，确保联调时数据真实

### 15.4 Bug 审计方法论

**审计发现**：
- Critical: exchange_orders 始终为空（对账无法工作）
- Critical: reports 详情全为 null（曲线无法展示）
- High: daily_pnl_pct 计算错误（百分比计算缺少分母）
- High: adapters 健康状态未获取

**经验**：
- 代码修改后必须进行"完整性检查"：不仅验证修改本身，还要验证相关数据流
- 特别注意"临时实现"（TODO comment）是否会导致功能不可用
- 使用 stub 数据时，至少要保证数据结构完整性和类型正确性

### 15.5 代码审计检查清单

```
1. 数据流完整性
   - 数据从哪里来？（OrderService/BrokerAdapter/Storage）
   - 数据如何传递？（函数调用/事件/存储）
   - 是否有缺失环节？（TODO BLOCKED BY INFRA）

2. 计算正确性
   - 百分比计算：分子/分母是否正确？
   - 单位转换：时间戳/币种精度是否一致？
   - 空值处理：None/空列表/空字符串是否区分？

3. 状态同步
   - 单例状态是否在多请求间正确共享？
   - 异步操作是否正确等待？
   - 错误是否被正确捕获并降级？

4. API 契约
   - 请求可选字段是否真的可选？
   - 响应字段是否有稳定的默认值？
   - 错误码是否符合 HTTP 语义？
```

### 15.6 后台任务实现模式

**问题**：
同步执行后台任务会导致 HTTP 请求阻塞，且无法追踪任务状态。

**解决方案**：
使用 FastAPI BackgroundTasks + 状态存储：
```python
from fastapi import BackgroundTasks

@router.post("/v1/replay", response_model=ReplayJob)
async def trigger_replay(request: ReplayRequest, background_tasks: BackgroundTasks):
    job = ReplayJob(job_id=job_id, status="PENDING", ...)
    _replay_jobs[job_id] = job
    background_tasks.add_task(_run_replay_task, job_id, request)
    return job

async def _run_replay_task(job_id: str, request: ReplayRequest) -> None:
    # 更新状态为 RUNNING
    # 执行任务
    # 更新状态为 COMPLETED/FAILED
```

**经验**：
- BackgroundTasks 适用于轻量级后台任务
- 状态存储使用 Dict + asyncio.Lock 保证线程安全
- 前端通过 job_id 轮询获取状态

### 15.7 存储分层降级设计

**问题**：
PostgreSQL 存储不可用时需要优雅降级。

**解决方案**：
```python
def get_storage():
    if _postgres_storage is None:
        # 尝试初始化 PG
        try:
            _postgres_storage = create_postgres_storage()
        except Exception:
            _postgres_storage = None
    
    if _postgres_storage is not None:
        return _postgres_storage
    return _in_memory_storage  # 降级方案
```

**经验**：
- 存储层应该有统一的接口
- 降级时要有清晰的数据一致性语义
- 生产环境优先使用持久化存储

### 15.8 快照历史存储设计

**问题**：
InMemory 存储使用 Dict[str, Dict] 只保留最新值，不支持历史查询。

**解决方案**：
```python
# 改为 List 结构支持历史
self.snapshots: Dict[str, List[Dict[str, Any]]] = {}

def save_snapshot(self, snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
    snapshot["snapshot_id"] = self._snapshot_counter
    stream_key = snapshot_data.get("stream_key")
    if stream_key not in self.snapshots:
        self.snapshots[stream_key] = []
    self.snapshots[stream_key].append(snapshot)
    return snapshot

def list_snapshots(self, stream_key: str, since_ts_ms: int = None, limit: int = 100):
    history = self.snapshots.get(stream_key, [])
    if since_ts_ms:
        history = [s for s in history if s.get("ts_ms", 0) >= since_ts_ms]
    return history[-limit:]
```

**经验**：
- 需要历史查询的存储应该用 List 而不是 Dict
- 时间范围过滤在存储层实现，减少应用层过滤

---

## 十六、v3.4.0 文档升级与 Qlib/Hermes 集成规划经验

### 16.1 新能力接入优先做“边界设计”，再做“功能实现”

**场景**：
准备引入 Qlib 与 Hermes 时，最容易犯的错误是直接把“模型预测”连到“执行下单”。

**经验**：
- Qlib 只做离线研究输出（因子、模型、预测）
- Hermes 只做研发流程编排（数据、训练、评估、报告）
- 下单路径继续走 `StrategyRunner -> RiskEngine -> OMS`
- 任何 AI 输出都必须经过既有门控和 HITL

### 16.2 版本升级不只是替换字符串，必须同步主线计划

**场景**：
仅把 `v3.3.0` 替换成 `v3.4.0`，会导致“版本号变了，但执行主线还是旧的”。

**经验**：
- 版本升级时至少同步三类文档：
  1. `docs/` 主文档（定位、架构、优先级）
  2. `PLAN.md`（当前执行主线）
  3. `PROJECT_STATUS.md`（最近开发记录与下次计划）
- 必须保证计划入口唯一，避免多个文档出现冲突状态

### 16.3 AI 研究闭环的最小可落地顺序

**经验**：
1. 先冻结研究数据契约（时间戳、缺失值、对齐规则）
2. 再做 Qlib 数据转换和训练流水线
3. 再做预测到标准 Signal 的桥接
4. 最后做 Hermes 编排自动化和上线门控联调

这个顺序的价值是：先保证可复现，再追求自动化；先保证可治理，再追求“看起来智能”。

---

## 十七、内置策略误删恢复经验（策略插件重建）

### 17.1 插件入口可注册不代表插件可加载

**场景**：
`trader/api/main.py` 中已注册默认策略 entrypoint，但 `trader/strategies/` 目录下对应模块被误删。

**问题**：
- 控制面“注册成功”不等于运行时可加载
- 真正失败点发生在 `StrategyRunner.load_strategy()` 的动态导入阶段

**经验**：
- 每次维护默认策略时，必须同时检查：
  1. `strategy_id` 与 entrypoint 是否存在
  2. 对应模块是否暴露 `get_plugin()`
  3. `validate_strategy_plugin()` 是否通过
- “元数据存在 + 模块缺失”属于高风险隐藏故障，必须用测试兜底

### 17.2 策略重建必须优先满足协议闭环

**场景**：
重建 `ema_cross_btc` / `rsi_grid` / `dca_btc` 时，需要快速恢复运行能力并保持与 Runner 协议兼容。

**设计模式**：
- 每个策略均实现统一结构：
  - `initialize(config)`：清理状态并接收配置
  - `on_market_data(data)`：纯计算生成 `Signal | None`
  - `update_config(config)`：支持热更新并带回滚
  - `validate()`：参数边界校验
  - `get_plugin()`：模块级单例工厂

**经验**：
- `update_config()` 推荐“先快照、后应用、失败回滚”，防止运行时落入半更新状态
- 所有策略都应只依赖 `market_data.timestamp`，避免引入 `datetime.now()` 导致回放不可重复

### 17.3 给默认策略加“存在性测试”比事后排查更便宜

**场景**：
策略模块缺失在常规 API 单测中不一定暴露，因为 API 注册不触发动态导入。

**解决方案**：
新增 `trader/tests/test_builtin_strategies.py`，覆盖：
- 三个默认模块可导入
- `get_plugin()` 存在且协议校验通过
- 关键信号路径（EMA 交叉、RSI 超买超卖、DCA 定时/回撤买入）

**经验**：
- 入口存在性测试应作为默认策略的最小护栏
- 这类测试运行快、收益高，能在“误删类故障”发生时第一时间报警
