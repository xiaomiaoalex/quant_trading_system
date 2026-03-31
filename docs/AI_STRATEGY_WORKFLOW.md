# AI策略共创工作流

> 本文档描述AI辅助策略开发的完整工作流程，包括自然语言交互、代码生成、安全验证、审批部署等环节。

## 目录

1. [概述](#1-概述)
2. [工作流架构](#2-工作流架构)
3. [自然语言交互](#3-自然语言交互)
4. [代码生成](#4-代码生成)
5. [安全验证](#5-安全验证)
6. [HITL审批](#6-hitl审批)
7. [部署执行](#7-部署执行)
8. [审计追溯](#8-审计追溯)

---

## 1. 概述

### 1.1 设计原则

AI策略共创遵循以下原则：

1. **AI-clean边界**：AI只能在Insight Plane活动，不可直接下单或修改Core状态
2. **HITL审批**：AI生成的策略必须经过人工审批才能部署
3. **安全验证**：所有AI生成的代码必须通过安全检查
4. **审计追溯**：完整的生成记录、审批记录、部署记录

### 1.2 适用场景

| 场景 | 说明 |
|------|------|
| 新策略开发 | Trader描述需求，AI生成初始代码 |
| 策略优化 | Trader描述优化方向，AI修改代码 |
| 参数调优 | AI根据回测结果建议参数调整 |
| 策略解释 | AI解释策略逻辑和风险点 |

---

## 2. 工作流架构

### 2.1 整体流程

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Human (Trader/Researcher)                    │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼ 自然语言描述
┌─────────────────────────────────────────────────────────────────────┐
│                     Insight Plane (AI Zone)                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │ StrategyChat │  │ AIStrategy   │  │ HITLGovernance            │  │
│  │ Interface    │──▶│ Generator    │──▶│ (Human-in-the-Loop)       │  │
│  │ (聊天界面)    │  │ (策略生成)    │  │ 审批AI生成的策略            │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
│         │                  │                      │                │
│         │                  ▼                      ▼                │
│         │         ┌──────────────┐        ┌────────────────┐       │
│         │         │ CodeSandbox  │        │ AISuggestion    │       │
│         │         │ (代码沙箱)    │        │ + Approval      │       │
│         │         │ 安全验证      │        │ Record          │       │
│         │         └──────────────┘        └────────────────┘       │
└─────────┼───────────────────────────────────────────────────────────┘
          │                           ▲
          ▼                           │ (Approved Strategy Code)
┌──────────────────────────────────┴──────────────────────────────────┐
│                      Adapter Plane (IO Zone)                        │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  StrategyRegistryAdapter                                      │   │
│  │  - 将AI生成的代码注册到策略管理模块                            │   │
│  │  - 触发热加载到策略执行器                                      │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 组件职责

| 组件 | Plane | 职责 |
|------|-------|------|
| StrategyChatInterface | Insight | 自然语言交互、意图识别 |
| AIStrategyGenerator | Insight | LLM调用、代码生成 |
| CodeSandbox | Insight | 代码安全验证 |
| HITLGovernance | Insight | 人工审批流程 |
| AIAuditLog | Insight | 审计日志记录 |
| StrategyRegistryAdapter | Adapter | 策略注册与部署 |

---

## 3. 自然语言交互

### 3.1 意图识别

系统支持以下意图类型：

| 意图 | 说明 | 示例 |
|------|------|------|
| GENERATE_STRATEGY | 生成新策略 | "帮我写一个EMA交叉策略" |
| MODIFY_PARAMS | 修改参数 | "把EMA周期改成20和50" |
| CHECK_STATUS | 检查状态 | "策略运行情况怎么样" |
| REQUEST_BACKTEST | 请求回测 | "回测一下最近3个月的表现" |
| EXPLAIN_STRATEGY | 解释策略 | "解释一下这个策略的逻辑" |

### 3.2 多轮对话

系统支持多轮对话，维护上下文：

```
用户: 我想开发一个趋势跟踪策略
AI:   好的，请问您希望使用哪些技术指标？例如EMA、MACD、布林带等？

用户: 使用EMA交叉，快线10，慢线20
AI:   明白了。请问您希望如何设置止损和止盈？

用户: 固定止损2%，止盈4%
AI:   好的，我将生成一个EMA交叉策略，参数如下：
      - 快线周期: 10
      - 慢线周期: 20
      - 止损: 2%
      - 止盈: 4%
      
      策略代码已生成，是否提交审批？
```

### 3.3 上下文管理

```python
@dataclass(slots=True)
class ChatSession:
    session_id: str
    trader_id: str
    context: Dict[str, Any]  # 当前讨论的策略、参数等
    history: List[ChatMessage]
    created_at: datetime
    status: SessionStatus  # ACTIVE, WAITING_APPROVAL, APPROVED, REJECTED
```

---

## 4. 代码生成

### 4.1 LLM后端

系统支持多LLM后端：

| 后端 | 说明 | 配置 |
|------|------|------|
| OpenAI | GPT-4/GPT-4-turbo | `LLMBackend.OPENAI` |
| Anthropic | Claude-3 | `LLMBackend.ANTHROPIC` |
| Local | 本地模型(vLLM) | `LLMBackend.LOCAL` |
| Mock | 测试用 | `LLMBackend.MOCK` |

### 4.2 Prompt模板

```python
STRATEGY_GENERATION_PROMPT = """
你是一个量化策略开发专家。基于以下需求生成Python策略代码：

需求：{requirements}

可用特征（从Feature Store读取）：
{available_features}

架构约束：
1. 策略必须实现StrategyPlugin协议
2. 只使用available_features中的特征
3. 包含完整的类型注解
4. 风险等级必须标注（LOW/MEDIUM/HIGH）
5. 禁止导入os, sys, subprocess, requests等危险模块
6. 禁止网络调用和文件操作

输出格式：
```json
{{
    "description": "策略描述",
    "risk_level": "MEDIUM",
    "param_schema": {{}},
    "code": "完整的Python代码"
}}
```
"""
```

### 4.3 代码提取

```python
def extract_metadata(code: str) -> Dict[str, Any]:
    """从生成的代码中提取元数据"""
    return {
        "description": extract_description(code),
        "risk_level": extract_risk_level(code),
        "param_schema": extract_param_schema(code),
        "plugin_id": extract_plugin_id(code),
        "version": extract_version(code),
    }
```

---

## 5. 安全验证

### 5.1 禁止模式

AI生成的代码必须通过以下安全检查：

```python
FORBIDDEN_IMPORTS = {
    # 系统操作
    "os", "sys", "subprocess", "shutil", "tempfile",
    # 网络调用
    "requests", "aiohttp", "urllib", "urllib3", "http", 
    "socket", "ftplib", "smtplib", "poplib", "imaplib",
    "websockets", "httpx",
    # 动态代码
    "eval", "exec", "compile", "importlib",
    # 文件操作
    "open", "file", "pathlib",
    # 序列化
    "pickle", "marshal", "shelve",
}

DANGEROUS_PATTERNS = [
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\bcompile\s*\(",
    r"\bopen\s*\(",
    r"\b__import__\s*\(",
    r"\bimportlib\.",
    r"\bsubprocess\.",
    r"\bos\.system",
    r"\bos\.popen",
    r"\bsocket\.",
    r"\brequests\.",
    r"\baiohttp\.",
    r"\burllib\.",
]
```

### 5.2 验证流程

```
AI生成代码
    │
    ▼
┌─────────────────┐
│ AST语法分析     │ ──▶ 语法错误 → 拒绝
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 导入检查        │ ──▶ 危险导入 → 拒绝
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 正则模式扫描    │ ──▶ 危险模式 → 拒绝
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 协议兼容检查    │ ──▶ 不兼容 → 拒绝
└────────┬────────┘
         │
         ▼
    验证通过
```

### 5.3 沙箱执行

```python
@dataclass(slots=True)
class SandboxConfig:
    max_memory_mb: int = 512
    timeout_seconds: int = 30
    allowed_imports: set[str] = field(default_factory=lambda: {
        "typing", "dataclasses", "decimal", "datetime",
        "collections", "itertools", "functools", "enum",
    })

class SafeCodeExecutor:
    async def execute(self, code: str, config: SandboxConfig) -> SandboxResult:
        # 1. AST分析
        self._analyze_ast(code)
        
        # 2. 设置资源限制
        self._set_limits(config)
        
        # 3. 执行代码
        with timeout(config.timeout_seconds):
            result = await self._run_in_sandbox(code)
        
        return result
```

---

## 6. HITL审批

### 6.1 审批流程

```
AI生成策略
    │
    ▼
┌─────────────────┐
│ 提交审批请求    │
│ status=PENDING  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Trader审核      │
│ 查看代码/指标   │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
 APPROVED   REJECTED
    │         │
    ▼         ▼
 注册部署   返回修改
```

### 6.2 审批记录

```python
@dataclass(slots=True)
class ApprovalRecord:
    suggestion_id: str
    strategy_id: str
    generated_code: str
    validation_result: ValidationResult
    submitted_at: datetime
    submitted_by: str
    
    decision: Literal["PENDING", "APPROVED", "REJECTED"]
    decided_at: datetime | None
    decided_by: str | None
    reason: str | None
```

### 6.3 审批API

```bash
# 提交审批
POST /api/chat/sessions/{session_id}/approve
{
    "approver": "trader_001",
    "reason": "策略逻辑清晰，风险可控"
}

# 拒绝策略
POST /api/chat/sessions/{session_id}/reject
{
    "reason": "风险等级过高，需要调整仓位控制"
}
```

---

## 7. 部署执行

### 7.1 部署流程

```
审批通过
    │
    ▼
┌─────────────────┐
│ 注册策略        │
│ status=DRAFT    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 代码验证        │
│ status=VALIDATED│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 回测验证        │
│ status=BACKTESTED│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 加载策略        │
│ 加载到Runner    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 启动策略        │
│ status=RUNNING  │
└─────────────────┘
```

### 7.2 部署API

```bash
# 注册策略
POST /api/v1/strategies/registry
{
    "strategy_id": "ai_generated_ema_v1",
    "name": "AI Generated EMA Strategy",
    "entrypoint": "strategies.ai_generated_ema:get_plugin",
    "language": "python"
}

# 加载并启动
POST /api/v1/strategies/ai_generated_ema_v1/load
{
    "version": 1,
    "module_path": "strategies.ai_generated_ema:get_plugin",
    "config": {...}
}

POST /api/v1/strategies/ai_generated_ema_v1/start
```

---

## 8. 审计追溯

### 8.1 审计日志

```python
@dataclass(slots=True)
class AuditEntry:
    entry_id: str
    trace_id: str
    
    # 输入
    input_requirements: str
    input_features: list[str]
    input_context: dict[str, Any]
    
    # 输出
    generated_code: str
    extracted_metadata: dict[str, Any]
    
    # 验证
    validation_passed: bool
    validation_errors: list[str]
    
    # 审批
    approval_status: AuditStatus
    approver: str | None
    approval_reason: str | None
    
    # 时间戳
    created_at: datetime
    validated_at: datetime | None
    submitted_at: datetime | None
    approved_at: datetime | None
    deployed_at: datetime | None
```

### 8.2 审计查询

```bash
# 查询审计日志
GET /api/audit/entries?strategy_id=ema_v1&status=APPROVED

# 查询单条记录
GET /api/audit/entries/{entry_id}
```

### 8.3 统计报表

```python
@dataclass(slots=True)
class AuditStatistics:
    total_generated: int
    total_approved: int
    total_rejected: int
    approval_rate: float
    avg_validation_time_ms: float
    avg_approval_time_ms: float
    top_rejection_reasons: list[tuple[str, int]]
```

---

## 附录

### A. 完整示例

```
# 1. 创建会话
POST /api/chat/sessions
Response: {"session_id": "sess_001"}

# 2. 发送需求
POST /api/chat/sessions/sess_001/messages
{
    "message": "帮我写一个基于布林带和RSI的组合策略"
}
Response: {
    "message": "策略已生成，等待审批",
    "attachments": [{
        "type": "code",
        "content": "class BollingerRSIStrategy: ..."
    }]
}

# 3. 审批
POST /api/chat/sessions/sess_001/approve
{
    "approver": "trader_001",
    "reason": "策略逻辑合理"
}
Response: {
    "strategy_id": "bollinger_rsi_v1",
    "status": "APPROVED"
}

# 4. 部署
POST /api/v1/strategies/bollinger_rsi_v1/start
Response: {
    "status": "RUNNING"
}
```

### B. 错误处理

| 错误 | 处理方式 |
|------|----------|
| LLM调用失败 | 重试3次，返回错误信息 |
| 代码验证失败 | 返回具体错误位置和建议 |
| 审批超时 | 自动标记为REJECTED |
| 部署失败 | 回滚到上一个版本 |

### C. 最佳实践

1. **明确需求**：提供详细的策略需求描述
2. **验证代码**：仔细审查AI生成的代码
3. **回测验证**：部署前必须通过回测
4. **小仓位测试**：首次部署使用小仓位
5. **监控告警**：配置策略监控规则
6. **保留记录**：保存所有审计日志
