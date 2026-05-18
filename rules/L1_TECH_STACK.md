# L1 技术栈/工程规范（按文件生效）
# 版本: v1.0.0
# 按文件类型选择性加载

---

## Python 技术栈

### 语言版本
- Python 3.12.5
- async-first（全称 `asyncio`，无阻塞网络调用）

### 类型注解
- 所有函数签名和类属性必须有严格类型注解
- 使用 `str | None` 风格（联合类型）
- 禁止 `Optional[T]`（旧风格）

### Classes
- 优先使用 `@dataclass(slots=True)`
- 需要不可变性时使用 `@dataclass(frozen=True, slots=True)`

### Async-First
- 统一 `asyncio`
- 禁止在异步上下文使用阻塞网络库
- 使用 `aiohttp` / `httpx` / `aiofiles` 等异步库
- 锁用 `asyncio.Lock`

### 并发控制
- 仅使用 `asyncio.Lock`
- 使用 hashed lock / actor / queue 模式
- 不依赖 asyncio 调度顺序
- 同一 `cl_ord_id` 的并发处理必须加锁

## 代码格式

- **格式化工具**：Black（line-length=100）
- **导入排序**：isort（profile=black）
- 执行命令：
  ```bash
  black trader/ --line-length 100
  isort trader/ --profile black
  ```

## 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 类名 | PascalCase | `EventDrivenRiskReplay` |
| 函数/方法 | snake_case | `evaluate_signal()` |
| 常量 | UPPER_SNAKE_CASE | `MAX_DAILY_LOSS` |
| 私有成员 | `_prefix` | `_risk_integration` |
| 类型别名 | PascalCase + Alias | `OrderId: TypeAlias[str]` |

## 日志规范

关键链路必须可观测：
- 信号生成 → 下单请求 → 订单回报 → 成交回报 → 持仓更新 → PnL 更新 → Monitor 聚合

报错时尽量带：输入参数、关键中间状态、异常位置、影响对象。

每个外部消息必须携带元数据：
- `local_receive_ts_ms` / `exchange_event_ts_ms`
- `seq/update_id` / `source` / `out_of_order` / `gap_detected`

## 可观测性

关键状态转换必须打印结构化日志，包含：
- `trace_id` / `stream_key` / `schema_version`

## 类型检查

执行命令：
```bash
mypy trader/
```

## 测试规范

- 测试必须密集
- AI生成的代码若无法被测试验证，不得合并
- 使用 `trader/tests/fakes/` 里的 `fake_clock` / `fake_http` / `fake_websocket`
- 禁止在单测中发起真实网络请求

---

**适用文件类型**：Python文件
**生效方式**：按文件类型生效
**维护者**：项目所有者
**版本**：v1.0.0