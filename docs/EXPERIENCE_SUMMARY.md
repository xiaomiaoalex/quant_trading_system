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

---

## 六、变更记录

| 日期 | 作者 | 描述 |
|------|------|------|
| 2026-03-21 | Kilo Code | 初始版本，记录 Reconciler 和 DepthChecker 开发经验 |

---

## 七、待补充

- [ ] 添加更多典型 bug 案例
- [ ] 补充性能优化经验
- [ ] 添加安全相关经验
- [ ] 完善调试工具清单
