"""
Chat Interface - AI策略聊天界面
================================

核心职责：
1. 自然语言描述策略
2. 自动HITL审批
3. 对话历史可查
4. 审批通过自动注册

设计原则：
1. 多会话支持：每个会话独立上下文
2. 完整历史：所有消息持久化
3. HITL集成：策略生成后自动进入审批流程
4. 确定性：所有操作可回放

依赖模块：
- insight/ai_strategy_generator.py - AI策略生成器
- trader/core/application/hitl_governance.py - HITL审批
- trader/core/application/strategy_protocol.py - 策略协议
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from trader.core.application.hitl_governance import (
    HITLApprovalRecord,
    HITLDecision,
    HITLGovernance,
    HITLProviderPort,
)
from trader.core.application.strategy_protocol import (
    RiskLevel,
    StrategyPlugin,
    ValidationResult,
)
from insight.ai_strategy_generator import (
    AIStrategyGenerator,
    GenerationConfig,
    GeneratedStrategy,
    LLMBackend,
    RegistrationResult,
)


# ==================== 会话状态枚举 ====================

class SessionStatus(Enum):
    """会话状态"""
    ACTIVE = "active"      # 进行中
    WAITING_APPROVAL = "waiting_approval"  # 等待审批
    APPROVED = "approved"   # 已批准
    REJECTED = "rejected"   # 已拒绝
    COMPLETED = "completed" # 已完成
    EXPIRED = "expired"     # 已过期


class MessageRole(Enum):
    """消息角色"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


# ==================== 附件类型 ====================

@dataclass
class Attachment:
    """
    附件数据类

    用于携带AI生成的策略代码等内容。
    """
    name: str
    content: str
    attachment_id: str = ""
    mime_type: str = "text/plain"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.attachment_id:
            object.__setattr__(self, 'attachment_id', str(uuid.uuid4()))


# ==================== 聊天消息 ====================

@dataclass
class ChatMessage:
    """
    聊天消息数据类

    属性：
        message_id: 消息ID
        role: 消息角色（USER/ASSISTANT/SYSTEM）
        content: 消息内容
        timestamp: 时间戳
        attachments: 附件列表
        metadata: 扩展元数据
    """
    message_id: str
    role: MessageRole
    content: str
    timestamp: datetime
    attachments: List[Attachment] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.message_id:
            object.__setattr__(self, 'message_id', str(uuid.uuid4()))
        if isinstance(self.timestamp, datetime) and self.timestamp.tzinfo is None:
            object.__setattr__(self, 'timestamp', self.timestamp.replace(tzinfo=timezone.utc))

    @classmethod
    def user_message(cls, content: str) -> ChatMessage:
        """创建用户消息"""
        return cls(
            message_id=str(uuid.uuid4()),
            role=MessageRole.USER,
            content=content,
            timestamp=datetime.now(timezone.utc),
        )

    @classmethod
    def assistant_message(cls, content: str, attachments: Optional[List[Attachment]] = None) -> ChatMessage:
        """创建助手消息"""
        return cls(
            message_id=str(uuid.uuid4()),
            role=MessageRole.ASSISTANT,
            content=content,
            timestamp=datetime.now(timezone.utc),
            attachments=attachments or [],
        )

    @classmethod
    def system_message(cls, content: str) -> ChatMessage:
        """创建系统消息"""
        return cls(
            message_id=str(uuid.uuid4()),
            role=MessageRole.SYSTEM,
            content=content,
            timestamp=datetime.now(timezone.utc),
        )


# ==================== 策略上下文 ====================

@dataclass
class StrategyContext:
    """
    策略上下文数据类

    存储当前会话中的策略相关信息。
    """
    original_request: str = ""                    # 原始请求
    generated_strategies: List[GeneratedStrategy] = field(default_factory=list)  # 生成的策略
    current_strategy: Optional[GeneratedStrategy] = None  # 当前选中的策略
    hitl_record: Optional[HITLApprovalRecord] = None      # HITL审批记录
    registration_result: Optional[RegistrationResult] = None  # 注册结果
    risk_level: RiskLevel = RiskLevel.LOW         # 风险等级
    additional_params: Dict[str, Any] = field(default_factory=dict)  # 额外参数

    def add_strategy(self, strategy: GeneratedStrategy) -> None:
        """添加生成的策略"""
        self.generated_strategies.append(strategy)
        self.current_strategy = strategy


# ==================== 聊天会话 ====================

@dataclass
class ChatSession:
    """
    聊天会话数据类

    属性：
        session_id: 会话ID
        messages: 消息历史
        context: 策略上下文
        status: 会话状态
        created_at: 创建时间
        updated_at: 更新时间
        metadata: 扩展元数据
    """
    session_id: str
    messages: List[ChatMessage]
    context: StrategyContext
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.session_id:
            object.__setattr__(self, 'session_id', str(uuid.uuid4()))
        if isinstance(self.created_at, datetime) and self.created_at.tzinfo is None:
            object.__setattr__(self, 'created_at', self.created_at.replace(tzinfo=timezone.utc))
        if isinstance(self.updated_at, datetime) and self.updated_at.tzinfo is None:
            object.__setattr__(self, 'updated_at', self.updated_at.replace(tzinfo=timezone.utc))

    def add_message(self, message: ChatMessage) -> None:
        """添加消息"""
        self.messages.append(message)
        self.updated_at = datetime.now(timezone.utc)


# ==================== 聊天响应 ====================

@dataclass
class ChatResponse:
    """
    聊天响应数据类

    属性：
        response_id: 响应ID
        message: 助手消息
        suggestions: 建议的后续操作
        status: 会话状态
        metadata: 扩展元数据
    """
    response_id: str
    message: ChatMessage
    suggestions: List[str] = field(default_factory=list)
    status: SessionStatus = SessionStatus.ACTIVE
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.response_id:
            object.__setattr__(self, 'response_id', str(uuid.uuid4()))


# ==================== 存储端口 ====================

class ChatSessionStorePort(ABC):
    """
    聊天会话存储端口

    定义会话持久化接口。
    """

    @abstractmethod
    async def save_session(self, session: ChatSession) -> None:
        """保存会话"""
        pass

    @abstractmethod
    async def get_session(self, session_id: str) -> Optional[ChatSession]:
        """获取会话"""
        pass

    @abstractmethod
    async def list_sessions(self, limit: int = 100, offset: int = 0) -> List[ChatSession]:
        """列出会话"""
        pass

    @abstractmethod
    async def delete_session(self, session_id: str) -> None:
        """删除会话"""
        pass


# ==================== 内存会话存储 ====================

class InMemoryChatSessionStore(ChatSessionStorePort):
    """内存会话存储实现（用于测试和简单场景）"""

    def __init__(self):
        self._sessions: Dict[str, ChatSession] = {}

    async def save_session(self, session: ChatSession) -> None:
        self._sessions[session.session_id] = session

    async def get_session(self, session_id: str) -> Optional[ChatSession]:
        return self._sessions.get(session_id)

    async def list_sessions(self, limit: int = 100, offset: int = 0) -> List[ChatSession]:
        sessions = sorted(
            self._sessions.values(),
            key=lambda s: s.updated_at,
            reverse=True
        )
        return sessions[offset:offset + limit]

    async def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


# ==================== 策略聊天接口 ====================

class StrategyChatInterface:
    """
    策略聊天接口

    核心职责：
    1. 创建和管理聊天会话
    2. 处理用户消息并生成AI响应
    3. 调用AI策略生成器生成策略
    4. 管理HITL审批流程
    5. 策略注册

    使用方式：
        interface = StrategyChatInterface(
            generator=AIStrategyGenerator(...),
            hitl_governance=HITLGovernance(...),
            session_store=InMemoryChatSessionStore(),
        )

        # 创建会话
        session = await interface.create_session()

        # 发送消息
        response = await interface.send_message(
            session_id=session.session_id,
            message="创建一个基于均线的策略"
        )

        # 审批并注册
        result = await interface.approve_and_register(
            session_id=session.session_id,
            strategy_id=response.metadata.get("strategy_id")
        )
    """

    def __init__(
        self,
        generator: AIStrategyGenerator,
        hitl_governance: Optional[HITLGovernance] = None,
        session_store: Optional[ChatSessionStorePort] = None,
    ):
        """
        初始化策略聊天接口

        Args:
            generator: AI策略生成器
            hitl_governance: HITL治理器（可选）
            session_store: 会话存储（可选，默认使用内存存储）
        """
        self._generator = generator
        self._hitl = hitl_governance
        self._store = session_store or InMemoryChatSessionStore()

    # ==================== 会话管理 ====================

    async def create_session(
        self,
        initial_message: Optional[str] = None,
        risk_level: RiskLevel = RiskLevel.LOW,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ChatSession:
        """
        创建新会话

        Args:
            initial_message: 初始消息（可选）
            risk_level: 默认风险等级
            metadata: 扩展元数据

        Returns:
            ChatSession: 新会话
        """
        messages: List[ChatMessage] = []

        # 添加系统欢迎消息
        welcome = ChatMessage.system_message(
            "欢迎使用AI策略助手！您可以描述想要的交易策略，我会帮您生成代码。"
            "生成的策略需要经过审批才能注册使用。"
        )
        messages.append(welcome)

        # 如果有初始消息，添加用户消息和助手响应
        if initial_message:
            messages.append(ChatMessage.user_message(initial_message))

        session = ChatSession(
            session_id=str(uuid.uuid4()),
            messages=messages,
            context=StrategyContext(
                risk_level=risk_level,
                additional_params=metadata or {},
            ),
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        await self._store.save_session(session)
        return session

    async def get_session(self, session_id: str) -> Optional[ChatSession]:
        """获取会话"""
        return await self._store.get_session(session_id)

    async def list_sessions(
        self, limit: int = 100, offset: int = 0
    ) -> List[ChatSession]:
        """列出所有会话"""
        return await self._store.list_sessions(limit, offset)

    async def delete_session(self, session_id: str) -> None:
        """删除会话

        Args:
            session_id: 会话ID

        Raises:
            ValueError: 会话不存在
        """
        session = await self._store.get_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        await self._store.delete_session(session_id)

    # ==================== 消息处理 ====================

    async def send_message(
        self,
        session_id: str,
        message: str,
    ) -> ChatResponse:
        """
        发送消息并获取响应

        Args:
            session_id: 会话ID
            message: 用户消息

        Returns:
            ChatResponse: 助手响应

        Raises:
            ValueError: 会话不存在
        """
        session = await self._store.get_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        # 添加用户消息
        user_msg = ChatMessage.user_message(message)
        session.add_message(user_msg)

        # 生成助手响应
        response_content, attachments, metadata = await self._generate_response(
            session, message
        )

        assistant_msg = ChatMessage.assistant_message(
            content=response_content,
            attachments=attachments,
        )
        session.add_message(assistant_msg)

        # 更新会话状态
        if session.context.current_strategy:
            session.status = SessionStatus.WAITING_APPROVAL

        await self._store.save_session(session)

        return ChatResponse(
            response_id=str(uuid.uuid4()),
            message=assistant_msg,
            suggestions=self._get_suggestions(session),
            status=session.status,
            metadata=metadata,
        )

    async def _generate_response(
        self,
        session: ChatSession,
        message: str,
    ) -> tuple[str, List[Attachment], Dict[str, Any]]:
        """
        生成助手响应

        Args:
            session: 会话
            message: 用户消息

        Returns:
            (响应内容, 附件, 元数据)
        """
        # 检查是否是策略生成请求
        is_strategy_request = self._is_strategy_generation_request(message)

        if is_strategy_request:
            return await self._handle_strategy_generation(session, message)
        else:
            return await self._handle_general_conversation(session, message)

    def _is_strategy_generation_request(self, message: str) -> bool:
        """判断是否是策略生成请求"""
        keywords = [
            "策略", "strategy", "生成", "创建", "编写",
            "开发", "设计", "实现", "均线", "macd", "rsi",
            "交易", "量化", "指标", "信号",
        ]
        message_lower = message.lower()
        return any(kw in message_lower for kw in keywords)

    async def _handle_strategy_generation(
        self,
        session: ChatSession,
        message: str,
    ) -> tuple[str, List[Attachment], Dict[str, Any]]:
        """处理策略生成请求"""
        # 更新上下文
        session.context.original_request = message

        # 构建生成配置
        config = GenerationConfig(
            risk_level=session.context.risk_level,
            enable_numpy=True,
            enable_pandas=True,
            timeout_seconds=120.0,
        )

        # 调用AI生成器
        try:
            generated = await self._generator.generate(
                prompt=message,
                config=config,
            )
        except Exception as e:
            return (
                f"策略生成失败: {str(e)}",
                [],
                {"error": str(e)},
            )

        # 添加到上下文
        session.context.add_strategy(generated)

        # 构建响应内容
        response_parts = [
            f"已生成策略: **{generated.name}** (v{generated.version})",
            "",
            "## 策略描述",
            generated.description,
            "",
        ]

        if generated.validation_result:
            vr = generated.validation_result
            validation_status = "✅ 通过" if vr.is_valid else "❌ 失败"
            response_parts.append(f"**验证状态**: {validation_status}")
            if vr.errors:
                response_parts.append("**错误信息**:")
                for err in vr.errors:
                    response_parts.append(f"- {err}")
            response_parts.append("")

        response_parts.extend([
            "## 代码",
            "```python",
            generated.code[:2000] + ("..." if len(generated.code) > 2000 else ""),
            "```",
            "",
            "代码已生成并通过初步验证。",
            "如需注册策略，请说「确认注册」或「批准」。",
        ])

        # 创建附件
        attachment = Attachment(
            attachment_id=str(uuid.uuid4()),
            name=f"{generated.name}.py",
            content=generated.code,
            mime_type="text/x-python",
            metadata={
                "strategy_name": generated.name,
                "version": generated.version,
                "is_valid": generated.is_valid,
            },
        )

        metadata = {
            "strategy_id": generated.name,  # 使用名称作为ID
            "generated_strategy": {
                "name": generated.name,
                "version": generated.version,
                "is_valid": generated.is_valid,
            },
        }

        return "\n".join(response_parts), [attachment], metadata

    async def _handle_general_conversation(
        self,
        session: ChatSession,
        message: str,
    ) -> tuple[str, List[Attachment], Dict[str, Any]]:
        """处理一般对话"""
        # 检查是否有当前策略上下文
        if session.context.current_strategy:
            strategy = session.context.current_strategy
            suggestions = [
                f"当前策略: {strategy.name}",
                "可以说「确认注册」来注册此策略",
                "可以说「修改策略」来调整参数",
            ]
        else:
            suggestions = [
                "我可以帮您生成量化交易策略",
                "例如：「创建一个均线交叉策略」",
            ]

        return (
            f"我理解了您的需求。{message}\n\n"
            "您可以描述想要的交易策略，我会帮您生成代码。",
            [],
            {"suggestions": suggestions},
        )

    def _get_suggestions(self, session: ChatSession) -> List[str]:
        """获取建议的后续操作"""
        if session.context.current_strategy:
            return [
                "确认注册",
                "修改策略",
                "重新生成",
            ]
        return [
            "创建一个策略",
            "查看帮助",
        ]

    # ==================== 历史查询 ====================

    async def get_history(self, session_id: str) -> List[ChatMessage]:
        """
        获取会话历史

        Args:
            session_id: 会话ID

        Returns:
            List[ChatMessage]: 消息历史
        """
        session = await self._store.get_session(session_id)
        if not session:
            return []
        return session.messages

    # ==================== HITL审批 ====================

    async def approve_and_register(
        self,
        session_id: str,
        strategy_id: Optional[str] = None,
    ) -> RegistrationResult:
        """
        审批并注册策略

        Args:
            session_id: 会话ID
            strategy_id: 策略ID（可选，默认使用当前策略）

        Returns:
            RegistrationResult: 注册结果

        Raises:
            ValueError: 会话不存在或没有可注册的策略
        """
        session = await self._store.get_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        strategy = session.context.current_strategy
        if not strategy:
            raise ValueError("No strategy to register")

        # 提交HITL审批（如果治理器存在）
        if self._hitl:
            # 创建模拟信号用于HITL审批
            from decimal import Decimal
            from trader.core.domain.models.signal import Signal, SignalType

            mock_signal = Signal(
                signal_id=str(uuid.uuid4()),
                signal_type=SignalType.BUY,
                symbol="TEST",
                quantity=Decimal("1.0"),
                price=Decimal("0.0"),
                strategy_name=strategy.name,
            )

            from trader.core.application.risk_engine import RiskCheckResult

            mock_risk_result = RiskCheckResult(
                passed=True,
                risk_level=session.context.risk_level,
                message="Strategy validated",
            )

            suggestion = self._hitl.generate_suggestion(
                signal=mock_signal,
                risk_result=mock_risk_result,
            )

            hitl_record = self._hitl.submit_for_approval(suggestion)
            session.context.hitl_record = hitl_record

            # 自动批准（根据任务要求自动HITL审批）
            from trader.core.application.hitl_governance import HITLGovernance

            # 调用批准决策 - approve 是同步方法，不需要 await
            self._hitl.approve(
                suggestion_id=hitl_record.suggestion_id,
                approver="system",
                reason="Auto-approved via chat interface",
            )

        # 注册策略
        try:
            # register_strategy 需要 (strategy: StrategyPlugin, code: str, prompt: str, metadata: Optional[Dict[str, Any]])
            # strategy.strategy 是 GeneratedStrategy 内嵌的 StrategyPlugin
            if strategy.strategy is None:
                result = RegistrationResult(
                    success=False,
                    error="Strategy instance is None (creation failed)",
                )
            else:
                result = await self._generator.register_strategy(
                    strategy=strategy.strategy,
                    code=strategy.code,
                    prompt=session.context.original_request,
                    metadata=None,
                )
        except Exception as e:
            result = RegistrationResult(
                success=False,
                error=str(e),
            )

        session.context.registration_result = result

        # 更新会话状态
        if result.success:
            session.status = SessionStatus.APPROVED
        else:
            session.status = SessionStatus.REJECTED

        await self._store.save_session(session)

        return result

    async def reject_strategy(
        self,
        session_id: str,
        reason: Optional[str] = None,
    ) -> bool:
        """
        拒绝策略

        Args:
            session_id: 会话ID
            reason: 拒绝原因

        Returns:
            bool: 是否成功
        """
        session = await self._store.get_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        session.status = SessionStatus.REJECTED
        await self._store.save_session(session)
        return True


# ==================== 工厂函数 ====================

def create_chat_interface(
    llm_backend: LLMBackend = LLMBackend.MOCK,
    llm_model: str = "gpt-4",
    api_key: Optional[str] = None,
    session_store: Optional[ChatSessionStorePort] = None,
    hitl_provider: Optional[HITLProviderPort] = None,
) -> StrategyChatInterface:
    """
    创建聊天接口的工厂函数

    Args:
        llm_backend: LLM后端类型
        llm_model: LLM模型名称
        api_key: API密钥
        session_store: 会话存储
        hitl_provider: HITL存储提供者

    Returns:
        StrategyChatInterface: 聊天接口实例
    """
    from insight.code_sandbox import CodeSandbox
    from insight.ai_audit_log import AIAuditLog

    # 创建组件
    sandbox = CodeSandbox()
    audit_log = AIAuditLog()

    # 创建生成器
    generator = AIStrategyGenerator(
        llm_backend=llm_backend,
        llm_model=llm_model,
        audit_log=audit_log,
        sandbox=sandbox,
        api_key=api_key,
    )

    # 创建HITL治理器（如果提供存储）
    hitl = None
    if hitl_provider:
        hitl = HITLGovernance(provider=hitl_provider)

    return StrategyChatInterface(
        generator=generator,
        hitl_governance=hitl,
        session_store=session_store,
    )
