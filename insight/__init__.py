"""
Insight Package - AI策略洞察模块
================================

此模块提供AI策略生成、代码沙箱和审计日志功能。

核心组件：
- AIStrategyGenerator: AI策略生成器
- CodeSandbox: 安全代码执行沙箱
- AIAuditLog: AI审计日志

设计原则：
1. 所有AI生成的代码必须经过沙箱验证
2. 危险代码拦截
3. 完整审计追溯
4. 多LLM后端支持
"""

from insight.ai_strategy_generator import (
    AIStrategyGenerator,
    GenerationConfig,
    GeneratedStrategy,
    LLMBackend,
    RegistrationResult,
)
from insight.code_sandbox import (
    CodeSandbox,
    SandboxConfig,
    SandboxResult,
    DangerousCodeError,
)
from insight.ai_audit_log import (
    AIAuditLog,
    AuditEntry,
    AuditStatus,
)
from insight.chat_interface import (
    ChatSession,
    ChatMessage,
    ChatResponse,
    StrategyContext,
    StrategyChatInterface,
    SessionStatus,
    MessageRole,
    Attachment,
    ChatSessionStorePort,
    InMemoryChatSessionStore,
    create_chat_interface,
)

__all__ = [
    # AI Strategy Generator
    "AIStrategyGenerator",
    "GenerationConfig",
    "GeneratedStrategy",
    "LLMBackend",
    "RegistrationResult",
    # Code Sandbox
    "CodeSandbox",
    "SandboxConfig",
    "SandboxResult",
    "DangerousCodeError",
    # Audit Log
    "AIAuditLog",
    "AuditEntry",
    "AuditStatus",
    # Chat Interface
    "ChatSession",
    "ChatMessage",
    "ChatResponse",
    "StrategyContext",
    "StrategyChatInterface",
    "SessionStatus",
    "MessageRole",
    "Attachment",
    "ChatSessionStorePort",
    "InMemoryChatSessionStore",
    "create_chat_interface",
]
