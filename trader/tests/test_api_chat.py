"""
Unit Tests - Chat API Endpoints
===============================
Tests for Chat API endpoints using TestClient.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from trader.api.main import app


class MockSessionStatus:
    """Mock SessionStatus for testing"""
    def __init__(self, value: str = "active"):
        self.value = value


class MockMessageRole:
    """Mock MessageRole for testing"""
    def __init__(self, value: str = "user"):
        self.value = value


class MockChatSession:
    """Mock ChatSession for testing"""
    def __init__(
        self,
        session_id: str = "test-session-001",
        status: str = "active",
        created_at: datetime = None,
        updated_at: datetime = None,
        message_count: int = 0,
        has_strategy: bool = False,
        metadata: dict = None,
    ):
        self.session_id = session_id
        self.status = MockSessionStatus(status)
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or datetime.now(timezone.utc)
        self.messages = []
        self.context = MagicMock()
        self.context.current_strategy = None
        self.metadata = metadata or {}


class MockChatMessage:
    """Mock ChatMessage for testing"""
    def __init__(
        self,
        message_id: str = "msg-001",
        role: str = "user",
        content: str = "Test message",
        timestamp: datetime = None,
        attachments: list = None,
    ):
        self.message_id = message_id
        self.role = MockMessageRole(role)
        self.content = content
        self.timestamp = timestamp or datetime.now(timezone.utc)
        self.attachments = attachments or []


class MockChatResponse:
    """Mock ChatResponse for testing"""
    def __init__(
        self,
        response_id: str = "resp-001",
        message: MockChatMessage = None,
        suggestions: list = None,
        status: str = "success",
        metadata: dict = None,
    ):
        self.response_id = response_id
        self.message = message or MockChatMessage(role="assistant")
        self.suggestions = suggestions or []
        self.status = MockSessionStatus(status)
        self.metadata = metadata or {}


class MockRegistrationResult:
    """Mock RegistrationResult for testing"""
    def __init__(
        self,
        success: bool = True,
        strategy_id: str = "strategy-001",
        entry_id: str = "entry-001",
        error: str = None,
    ):
        self.success = success
        self.strategy_id = strategy_id
        self.entry_id = entry_id
        self.error = error


class TestChatEndpoints:
    """Test chat API endpoints"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)
        self.mock_interface = MagicMock()
        self.mock_interface.create_session = AsyncMock()
        self.mock_interface.send_message = AsyncMock()
        self.mock_interface.get_history = AsyncMock()
        self.mock_interface.get_session = AsyncMock()
        self.mock_interface.list_sessions = AsyncMock()
        self.mock_interface.delete_session = AsyncMock()
        self.mock_interface.approve_and_register = AsyncMock()
        self.mock_interface.reject_strategy = AsyncMock()

    def test_create_session_success(self):
        """Test creating a new chat session"""
        mock_session = MockChatSession()
        self.mock_interface.create_session.return_value = mock_session

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=self.mock_interface
        ):
            response = self.client.post(
                "/api/chat/sessions",
                json={"initial_message": "Hello", "risk_level": "LOW"}
            )

        assert response.status_code == 201
        data = response.json()
        assert data["session_id"] == "test-session-001"
        assert data["status"] == "active"

    def test_create_session_without_initial_message(self):
        """Test creating session without initial message"""
        mock_session = MockChatSession()
        self.mock_interface.create_session.return_value = mock_session

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=self.mock_interface
        ):
            response = self.client.post(
                "/api/chat/sessions",
                json={"risk_level": "MEDIUM"}
            )

        assert response.status_code == 201
        data = response.json()
        assert data["session_id"] == "test-session-001"

    def test_send_message_success(self):
        """Test sending a message to a session"""
        mock_response = MockChatResponse()
        self.mock_interface.send_message.return_value = mock_response

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=self.mock_interface
        ):
            response = self.client.post(
                "/api/chat/sessions/test-session-001/messages",
                params={"message": "Hello AI"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["response_id"] == "resp-001"
        assert "message" in data

    def test_send_message_session_not_found(self):
        """Test sending message to non-existent session"""
        self.mock_interface.send_message.side_effect = ValueError("Session not found")

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=self.mock_interface
        ):
            response = self.client.post(
                "/api/chat/sessions/nonexistent/messages",
                params={"message": "Hello"}
            )

        assert response.status_code == 404

    def test_get_history_success(self):
        """Test getting message history"""
        mock_messages = [
            MockChatMessage(message_id="msg-001", role="user", content="Hello"),
            MockChatMessage(message_id="msg-002", role="assistant", content="Hi there!"),
        ]
        self.mock_interface.get_history.return_value = mock_messages

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=self.mock_interface
        ):
            response = self.client.get("/api/chat/sessions/test-session-001/history")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["role"] == "user"
        assert data[1]["role"] == "assistant"

    def test_approve_strategy_success(self):
        """Test approving and registering a strategy"""
        mock_result = MockRegistrationResult()
        self.mock_interface.approve_and_register.return_value = mock_result

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=self.mock_interface
        ):
            response = self.client.post(
                "/api/chat/sessions/test-session-001/approve",
                params={"strategy_id": "strategy-001"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["strategy_id"] == "strategy-001"

    def test_approve_strategy_session_not_found(self):
        """Test approving strategy for non-existent session"""
        self.mock_interface.approve_and_register.side_effect = ValueError("Session not found")

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=self.mock_interface
        ):
            response = self.client.post(
                "/api/chat/sessions/nonexistent/approve"
            )

        assert response.status_code == 404

    def test_reject_strategy_success(self):
        """Test rejecting a strategy"""
        self.mock_interface.reject_strategy.return_value = True

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=self.mock_interface
        ):
            response = self.client.post(
                "/api/chat/sessions/test-session-001/reject",
                params={"reason": "Not suitable"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_delete_session_success(self):
        """Test deleting a session"""
        self.mock_interface.delete_session.return_value = None

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=self.mock_interface
        ):
            response = self.client.delete("/api/chat/sessions/test-session-001")

        assert response.status_code == 204

    def test_delete_session_not_found(self):
        """Test deleting non-existent session"""
        self.mock_interface.delete_session.side_effect = ValueError("Session not found")

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=self.mock_interface
        ):
            response = self.client.delete("/api/chat/sessions/nonexistent")

        assert response.status_code == 404

    def test_list_sessions_success(self):
        """Test listing all sessions"""
        mock_sessions = [
            MockChatSession(session_id="session-001"),
            MockChatSession(session_id="session-002"),
        ]
        self.mock_interface.list_sessions.return_value = mock_sessions

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=self.mock_interface
        ):
            response = self.client.get("/api/chat/sessions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["session_id"] == "session-001"
        assert data[1]["session_id"] == "session-002"

    def test_list_sessions_with_pagination(self):
        """Test listing sessions with pagination"""
        mock_sessions = [MockChatSession(session_id=f"session-{i}") for i in range(5)]
        self.mock_interface.list_sessions.return_value = mock_sessions

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=self.mock_interface
        ):
            response = self.client.get(
                "/api/chat/sessions",
                params={"limit": 5, "offset": 10}
            )

        assert response.status_code == 200
        self.mock_interface.list_sessions.assert_called_once_with(limit=5, offset=10)

    def test_get_session_success(self):
        """Test getting a specific session"""
        mock_session = MockChatSession(session_id="session-001")
        self.mock_interface.get_session.return_value = mock_session

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=self.mock_interface
        ):
            response = self.client.get("/api/chat/sessions/session-001")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "session-001"

    def test_get_session_not_found(self):
        """Test getting non-existent session"""
        self.mock_interface.get_session.return_value = None

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=self.mock_interface
        ):
            response = self.client.get("/api/chat/sessions/nonexistent")

        assert response.status_code == 404


class TestChatEndpointsValidation:
    """Test chat API input validation"""

    def setup_method(self):
        """Setup for each test"""
        self.client = TestClient(app)

    def test_create_session_invalid_risk_level(self):
        """Test creating session with invalid risk level - defaults to LOW"""
        mock_interface = MagicMock()
        mock_session = MockChatSession()
        mock_interface.create_session = AsyncMock(return_value=mock_session)

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=mock_interface
        ):
            response = self.client.post(
                "/api/chat/sessions",
                json={"risk_level": "INVALID"}
            )

        assert response.status_code == 201

    def test_send_message_missing_message_param(self):
        """Test sending message without message parameter"""
        mock_interface = MagicMock()

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=mock_interface
        ):
            response = self.client.post(
                "/api/chat/sessions/test-session/messages"
            )

        assert response.status_code == 422

    def test_list_sessions_invalid_limit(self):
        """Test listing sessions with invalid limit"""
        mock_interface = MagicMock()

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=mock_interface
        ):
            response = self.client.get(
                "/api/chat/sessions",
                params={"limit": 10000}
            )

        assert response.status_code == 422

    def test_list_sessions_negative_offset(self):
        """Test listing sessions with negative offset"""
        mock_interface = MagicMock()

        with patch(
            "trader.api.routes.chat.get_chat_interface",
            return_value=mock_interface
        ):
            response = self.client.get(
                "/api/chat/sessions",
                params={"offset": -1}
            )

        assert response.status_code == 422
