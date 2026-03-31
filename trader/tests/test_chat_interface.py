"""
Test Chat Interface - AI策略聊天界面单元测试
=============================================

测试覆盖：
1. ChatSession创建和管理
2. ChatMessage消息处理
3. StrategyChatInterface会话管理
4. 策略生成请求处理
5. 消息历史查询
6. 审批流程
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from insight.chat_interface import (
    Attachment,
    ChatMessage,
    ChatResponse,
    ChatSession,
    ChatSessionStorePort,
    InMemoryChatSessionStore,
    MessageRole,
    SessionStatus,
    StrategyChatInterface,
    StrategyContext,
    create_chat_interface,
)
from insight.ai_strategy_generator import (
    AIStrategyGenerator,
    GeneratedStrategy,
    GenerationConfig,
    LLMBackend,
    RegistrationResult,
)
from trader.core.application.strategy_protocol import (
    RiskLevel,
    StrategyResourceLimits,
    ValidationResult,
    ValidationStatus,
)


# ==================== 测试辅助 ====================

class FakeChatSessionStore(ChatSessionStorePort):
    """假会话存储"""

    def __init__(self):
        self._sessions = {}

    async def save_session(self, session: ChatSession) -> None:
        self._sessions[session.session_id] = session

    async def get_session(self, session_id: str) -> ChatSession | None:
        return self._sessions.get(session_id)

    async def list_sessions(
        self, limit: int = 100, offset: int = 0
    ) -> list[ChatSession]:
        sessions = sorted(
            self._sessions.values(),
            key=lambda s: s.updated_at,
            reverse=True,
        )
        return sessions[offset : offset + limit]

    async def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


class FakeStrategyForTest:
    """用于测试的假策略类"""
    name = "FakeTestStrategy"
    version = "1.0.0"
    risk_level = RiskLevel.LOW
    resource_limits = StrategyResourceLimits()
    
    def validate(self):
        return ValidationResult.valid()
    
    async def initialize(self, config):
        pass
    
    async def on_market_data(self, data):
        return None
    
    async def on_fill(self, order_id, symbol, side, quantity, price):
        pass
    
    async def on_cancel(self, order_id, reason):
        pass
    
    async def shutdown(self):
        pass


class FakeGenerator(AIStrategyGenerator):
    """假策略生成器"""

    def __init__(self):
        # 跳过父类初始化
        self._mock_mode = True

    async def generate(
        self,
        prompt: str,
        config: GenerationConfig | None = None,
    ) -> GeneratedStrategy:
        # 创建一个假的策略实例
        fake_strategy = FakeStrategyForTest()
        return GeneratedStrategy(
            code=f"# Strategy for: {prompt}\nprint('hello')",
            name="TestStrategy",
            version="1.0.0",
            description=f"Generated strategy for: {prompt}",
            strategy=fake_strategy,  # 添加策略实例
            validation_result=ValidationResult.valid(),
        )

    async def register_strategy(
        self,
        strategy: GeneratedStrategy = None,
        code: str = "",
        prompt: str = "",
        metadata: dict = None,
    ) -> RegistrationResult:
        # 兼容多种调用方式
        if strategy is not None and hasattr(strategy, 'name'):
            strategy_id = strategy.name
        else:
            strategy_id = "TestStrategy"
        return RegistrationResult(
            success=True,
            strategy_id=strategy_id,
            entry_id="test-entry-id",
        )


# ==================== 测试用例 ====================

class TestChatMessage:
    """ChatMessage单元测试"""

    def test_user_message_creation(self):
        """测试用户消息创建"""
        msg = ChatMessage.user_message("Hello, AI!")
        assert msg.role == MessageRole.USER
        assert msg.content == "Hello, AI!"
        assert msg.message_id is not None
        assert msg.attachments == []

    def test_assistant_message_creation(self):
        """测试助手消息创建"""
        attachments = [
            Attachment(
                attachment_id="att1",
                name="strategy.py",
                content="print('hello')",
            )
        ]
        msg = ChatMessage.assistant_message("Here's the strategy", attachments)
        assert msg.role == MessageRole.ASSISTANT
        assert msg.content == "Here's the strategy"
        assert len(msg.attachments) == 1
        assert msg.attachments[0].name == "strategy.py"

    def test_system_message_creation(self):
        """测试系统消息创建"""
        msg = ChatMessage.system_message("Welcome to AI Strategy Chat")
        assert msg.role == MessageRole.SYSTEM
        assert msg.content == "Welcome to AI Strategy Chat"

    def test_message_timestamp_auto_fill(self):
        """测试时间戳自动填充"""
        msg = ChatMessage.user_message("test")
        assert msg.timestamp.tzinfo is not None


class TestAttachment:
    """Attachment单元测试"""

    def test_attachment_creation(self):
        """测试附件创建"""
        att = Attachment(
            attachment_id="att1",
            name="test.py",
            content="print('hello')",
            mime_type="text/x-python",
        )
        assert att.attachment_id == "att1"
        assert att.name == "test.py"
        assert att.content == "print('hello')"
        assert att.mime_type == "text/x-python"

    def test_attachment_auto_id(self):
        """测试附件ID自动生成"""
        att = Attachment(
            name="test.py",
            content="print('hello')",
        )
        assert att.attachment_id is not None
        assert len(att.attachment_id) > 0


class TestStrategyContext:
    """StrategyContext单元测试"""

    def test_context_creation(self):
        """测试上下文创建"""
        ctx = StrategyContext(
            original_request="Create a MA strategy",
            risk_level=RiskLevel.MEDIUM,
        )
        assert ctx.original_request == "Create a MA strategy"
        assert ctx.risk_level == RiskLevel.MEDIUM
        assert ctx.generated_strategies == []
        assert ctx.current_strategy is None

    def test_add_strategy(self):
        """测试添加策略"""
        ctx = StrategyContext()
        strategy = GeneratedStrategy(
            code="# code",
            name="MA_Strategy",
            version="1.0.0",
            description="MA strategy",
        )
        ctx.add_strategy(strategy)
        assert len(ctx.generated_strategies) == 1
        assert ctx.current_strategy == strategy


class TestChatSession:
    """ChatSession单元测试"""

    def test_session_creation(self):
        """测试会话创建"""
        session = ChatSession(
            session_id="test-session-1",
            messages=[],
            context=StrategyContext(),
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        assert session.session_id == "test-session-1"
        assert session.status == SessionStatus.ACTIVE
        assert len(session.messages) == 0

    def test_add_message(self):
        """测试添加消息"""
        created_time = datetime.now(timezone.utc)
        session = ChatSession(
            session_id="test-session-1",
            messages=[],
            context=StrategyContext(),
            status=SessionStatus.ACTIVE,
            created_at=created_time,
            updated_at=created_time,
        )
        # 确保有时间差异
        import time
        time.sleep(0.01)
        msg = ChatMessage.user_message("Hello")
        session.add_message(msg)
        assert len(session.messages) == 1
        assert session.updated_at >= session.created_at


class TestInMemoryChatSessionStore:
    """内存会话存储测试"""

    @pytest.fixture
    def store(self):
        return InMemoryChatSessionStore()

    @pytest.fixture
    def session(self):
        return ChatSession(
            session_id="test-1",
            messages=[],
            context=StrategyContext(),
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    @pytest.mark.asyncio
    async def test_save_and_get(self, store, session):
        """测试保存和获取"""
        await store.save_session(session)
        retrieved = await store.get_session("test-1")
        assert retrieved is not None
        assert retrieved.session_id == "test-1"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store):
        """测试获取不存在的会话"""
        result = await store.get_session("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_sessions(self, store):
        """测试列出会话"""
        for i in range(5):
            session = ChatSession(
                session_id=f"test-{i}",
                messages=[],
                context=StrategyContext(),
                status=SessionStatus.ACTIVE,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            await store.save_session(session)

        sessions = await store.list_sessions(limit=3, offset=0)
        assert len(sessions) == 3

    @pytest.mark.asyncio
    async def test_delete_session(self, store, session):
        """测试删除会话"""
        await store.save_session(session)
        await store.delete_session("test-1")
        result = await store.get_session("test-1")
        assert result is None


class TestStrategyChatInterface:
    """策略聊天接口测试"""

    @pytest.fixture
    def interface(self):
        store = FakeChatSessionStore()
        generator = FakeGenerator()
        return StrategyChatInterface(
            generator=generator,
            hitl_governance=None,
            session_store=store,
        )

    @pytest.mark.asyncio
    async def test_create_session(self, interface):
        """测试创建会话"""
        session = await interface.create_session(
            initial_message="Hello",
            risk_level=RiskLevel.HIGH,
        )
        assert session is not None
        assert session.status == SessionStatus.ACTIVE
        assert len(session.messages) >= 1  # 至少有欢迎消息

    @pytest.mark.asyncio
    async def test_create_session_without_initial_message(self, interface):
        """测试创建空会话"""
        session = await interface.create_session()
        assert session is not None
        assert len(session.messages) == 1  # 只有欢迎消息

    @pytest.mark.asyncio
    async def test_send_general_message(self, interface):
        """测试发送一般消息"""
        session = await interface.create_session()
        response = await interface.send_message(
            session_id=session.session_id,
            message="Hello, how are you?",
        )
        assert response is not None
        assert response.message.role == MessageRole.ASSISTANT
        assert len(response.suggestions) > 0

    @pytest.mark.asyncio
    async def test_send_strategy_request(self, interface):
        """测试发送策略生成请求"""
        session = await interface.create_session()
        response = await interface.send_message(
            session_id=session.session_id,
            message="创建一个均线交叉策略",
        )
        assert response is not None
        assert response.status == SessionStatus.WAITING_APPROVAL
        assert response.metadata.get("generated_strategy") is not None

    @pytest.mark.asyncio
    async def test_get_history(self, interface):
        """测试获取历史"""
        session = await interface.create_session()
        await interface.send_message(
            session_id=session.session_id,
            message="Hello",
        )
        history = await interface.get_history(session.session_id)
        assert len(history) >= 2  # 欢迎消息 + 用户消息 + 助手消息

    @pytest.mark.asyncio
    async def test_get_history_empty(self, interface):
        """测试获取空历史"""
        session = await interface.create_session()
        history = await interface.get_history(session.session_id)
        assert len(history) >= 1  # 至少有欢迎消息

    @pytest.mark.asyncio
    async def test_get_history_nonexistent_session(self, interface):
        """测试获取不存在会话的历史"""
        history = await interface.get_history("nonexistent")
        assert history == []

    @pytest.mark.asyncio
    async def test_approve_and_register(self, interface):
        """测试审批并注册"""
        session = await interface.create_session()
        # 先生成策略
        await interface.send_message(
            session_id=session.session_id,
            message="创建一个RSI策略",
        )
        # 审批并注册
        result = await interface.approve_and_register(
            session_id=session.session_id,
        )
        assert result.success is True
        assert result.strategy_id is not None

    @pytest.mark.asyncio
    async def test_approve_nonexistent_session(self, interface):
        """测试审批不存在会话"""
        with pytest.raises(ValueError):
            await interface.approve_and_register(
                session_id="nonexistent",
            )

    @pytest.mark.asyncio
    async def test_reject_strategy(self, interface):
        """测试拒绝策略"""
        session = await interface.create_session()
        await interface.send_message(
            session_id=session.session_id,
            message="创建一个策略",
        )
        success = await interface.reject_strategy(
            session_id=session.session_id,
            reason="Too risky",
        )
        assert success is True

        # 验证状态已更新
        updated = await interface.get_session(session.session_id)
        assert updated.status == SessionStatus.REJECTED

    @pytest.mark.asyncio
    async def test_list_sessions(self, interface):
        """测试列出会话"""
        # 创建多个会话
        for i in range(3):
            await interface.create_session()

        sessions = await interface.list_sessions(limit=10, offset=0)
        assert len(sessions) == 3

    @pytest.mark.asyncio
    async def test_get_session(self, interface):
        """测试获取会话"""
        session = await interface.create_session()
        retrieved = await interface.get_session(session.session_id)
        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    @pytest.mark.asyncio
    async def test_get_session_nonexistent(self, interface):
        """测试获取不存在的会话"""
        result = await interface.get_session("nonexistent")
        assert result is None


class TestCreateChatInterface:
    """工厂函数测试"""

    def test_create_with_defaults(self):
        """测试使用默认参数创建"""
        interface = create_chat_interface()
        assert interface is not None
        assert isinstance(interface, StrategyChatInterface)

    def test_create_with_custom_store(self):
        """测试使用自定义存储创建"""
        store = InMemoryChatSessionStore()
        interface = create_chat_interface(session_store=store)
        assert interface is not None

    def test_create_with_mock_llm(self):
        """测试使用MOCK LLM创建"""
        interface = create_chat_interface(
            llm_backend=LLMBackend.MOCK,
        )
        assert interface is not None


# ==================== 边界测试 ====================

class TestBoundaryConditions:
    """边界条件测试"""

    @pytest.fixture
    def interface(self):
        store = FakeChatSessionStore()
        generator = FakeGenerator()
        return StrategyChatInterface(
            generator=generator,
            hitl_governance=None,
            session_store=store,
        )

    @pytest.mark.asyncio
    async def test_empty_message(self, interface):
        """测试空消息"""
        session = await interface.create_session()
        # 空消息不应该抛出异常
        response = await interface.send_message(
            session_id=session.session_id,
            message="",
        )
        assert response is not None

    @pytest.mark.asyncio
    async def test_very_long_message(self, interface):
        """测试超长消息"""
        session = await interface.create_session()
        long_message = "a" * 10000  # 10KB消息
        response = await interface.send_message(
            session_id=session.session_id,
            message=long_message,
        )
        assert response is not None

    @pytest.mark.asyncio
    async def test_unicode_message(self, interface):
        """测试Unicode消息"""
        session = await interface.create_session()
        response = await interface.send_message(
            session_id=session.session_id,
            message="你好，世界！🌍 🎉",
        )
        assert response is not None

    @pytest.mark.asyncio
    async def test_special_characters_in_message(self, interface):
        """测试特殊字符消息"""
        session = await interface.create_session()
        response = await interface.send_message(
            session_id=session.session_id,
            message="!@#$%^&*()_+-=[]{}|;':\",./<>?",
        )
        assert response is not None


# ==================== 错误路径测试 ====================

class TestErrorPaths:
    """错误路径测试"""

    @pytest.fixture
    def interface(self):
        store = FakeChatSessionStore()
        generator = FakeGenerator()
        return StrategyChatInterface(
            generator=generator,
            hitl_governance=None,
            session_store=store,
        )

    @pytest.mark.asyncio
    async def test_send_message_to_nonexistent_session(self, interface):
        """测试向不存在的会话发送消息"""
        with pytest.raises(ValueError):
            await interface.send_message(
                session_id="nonexistent",
                message="Hello",
            )

    @pytest.mark.asyncio
    async def test_approve_without_strategy(self, interface):
        """测试在没有生成策略时审批"""
        session = await interface.create_session()
        with pytest.raises(ValueError, match="No strategy to register"):
            await interface.approve_and_register(
                session_id=session.session_id,
            )

    @pytest.mark.asyncio
    async def test_reject_nonexistent_session(self, interface):
        """测试拒绝不存在的会话"""
        with pytest.raises(ValueError):
            await interface.reject_strategy(
                session_id="nonexistent",
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
