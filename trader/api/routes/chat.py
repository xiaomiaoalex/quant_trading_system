"""
Chat Routes - AI策略聊天API
===========================

API端点：
- POST /api/chat/sessions - 创建会话
- POST /api/chat/sessions/{id}/messages - 发送消息
- GET /api/chat/sessions/{id}/history - 获取历史
- POST /api/chat/sessions/{id}/approve - 审批并注册
- DELETE /api/chat/sessions/{id} - 删除会话
- GET /api/chat/sessions - 列出所有会话
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from insight.chat_interface import (
    Attachment,
    ChatMessage,
    ChatResponse,
    ChatSession,
    SessionStatus,
    StrategyChatInterface,
    StrategyContext,
    MessageRole,
)
from trader.core.application.strategy_protocol import RiskLevel

# ==================== 请求模型 ====================

class CreateSessionRequest(BaseModel):
    """创建会话请求"""
    initial_message: Optional[str] = Field(None, description="初始消息")
    risk_level: str = Field("LOW", description="风险等级: LOW, MEDIUM, HIGH, CRITICAL")


class SendMessageRequest(BaseModel):
    """发送消息请求"""
    message: str = Field(..., description="用户消息")
    session_id: str = Field(..., description="会话ID")


class ApproveRequest(BaseModel):
    """审批请求"""
    strategy_id: Optional[str] = Field(None, description="策略ID（可选）")
    approved: bool = Field(True, description="是否批准")


class RejectRequest(BaseModel):
    """拒绝请求"""
    reason: Optional[str] = Field(None, description="拒绝原因")


# ==================== 响应模型 ====================

class AttachmentResponse(BaseModel):
    """附件响应"""
    attachment_id: str
    name: str
    content: str
    mime_type: str


class ChatMessageResponse(BaseModel):
    """聊天消息响应"""
    message_id: str
    role: str
    content: str
    timestamp: datetime
    attachments: List[AttachmentResponse] = []

    class Config:
        from_attributes = True


class SessionResponse(BaseModel):
    """会话响应"""
    session_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    has_strategy: bool = False
    metadata: Dict[str, Any] = {}

    class Config:
        from_attributes = True


class SendMessageResponse(BaseModel):
    """发送消息响应"""
    response_id: str
    message: ChatMessageResponse
    suggestions: List[str]
    status: str
    metadata: Dict[str, Any] = {}


class RegistrationResultResponse(BaseModel):
    """注册结果响应"""
    success: bool
    strategy_id: Optional[str] = None
    entry_id: Optional[str] = None
    error: Optional[str] = None


class ErrorResponse(BaseModel):
    """错误响应"""
    error: str
    detail: Optional[str] = None


# ==================== 辅助函数 ====================

def _attachment_to_response(attachment: Attachment) -> AttachmentResponse:
    """将附件转换为响应模型"""
    return AttachmentResponse(
        attachment_id=attachment.attachment_id,
        name=attachment.name,
        content=attachment.content,
        mime_type=attachment.mime_type,
    )


def _message_to_response(message: ChatMessage) -> ChatMessageResponse:
    """将消息转换为响应模型"""
    return ChatMessageResponse(
        message_id=message.message_id,
        role=message.role.value,
        content=message.content,
        timestamp=message.timestamp,
        attachments=[_attachment_to_response(a) for a in message.attachments],
    )


def _session_to_response(session: ChatSession) -> SessionResponse:
    """将会话转换为响应模型"""
    return SessionResponse(
        session_id=session.session_id,
        status=session.status.value,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=len(session.messages),
        has_strategy=session.context.current_strategy is not None,
        metadata=session.metadata,
    )


def _risk_level_from_string(level: str) -> RiskLevel:
    """从字符串获取风险等级"""
    try:
        return RiskLevel[level.upper()]
    except KeyError:
        return RiskLevel.LOW


# ==================== 全局接口实例 ====================

# 全局聊天接口实例（通过依赖注入或应用启动时设置）
_chat_interface: Optional[StrategyChatInterface] = None


def set_chat_interface(interface: StrategyChatInterface) -> None:
    """设置全局聊天接口实例"""
    global _chat_interface
    _chat_interface = interface


def get_chat_interface() -> StrategyChatInterface:
    """获取全局聊天接口实例"""
    global _chat_interface
    if _chat_interface is None:
        from insight.chat_interface import create_chat_interface
        _chat_interface = create_chat_interface()
    return _chat_interface


# ==================== API路由 ====================

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post(
    "/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建新会话",
    description="创建一个新的聊天会话，用于AI策略开发对话。",
)
async def create_session(
    request: CreateSessionRequest,
) -> SessionResponse:
    """
    创建新会话

    - **initial_message**: 初始消息（可选）
    - **risk_level**: 风险等级（LOW, MEDIUM, HIGH, CRITICAL）
    """
    interface = get_chat_interface()
    risk_level = _risk_level_from_string(request.risk_level)

    session = await interface.create_session(
        initial_message=request.initial_message,
        risk_level=risk_level,
    )

    return _session_to_response(session)


@router.post(
    "/sessions/{session_id}/messages",
    response_model=SendMessageResponse,
    summary="发送消息",
    description="向指定会话发送消息并获取AI响应。",
)
async def send_message(
    session_id: str,
    message: str = Query(..., description="用户消息"),
) -> SendMessageResponse:
    """
    发送消息

    - **session_id**: 会话ID
    - **message**: 用户消息内容
    """
    interface = get_chat_interface()

    try:
        response = await interface.send_message(
            session_id=session_id,
            message=message,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    return SendMessageResponse(
        response_id=response.response_id,
        message=_message_to_response(response.message),
        suggestions=response.suggestions,
        status=response.status.value,
        metadata=response.metadata,
    )


@router.get(
    "/sessions/{session_id}/history",
    response_model=List[ChatMessageResponse],
    summary="获取消息历史",
    description="获取指定会话的所有消息历史。",
)
async def get_history(
    session_id: str,
) -> List[ChatMessageResponse]:
    """
    获取消息历史

    - **session_id**: 会话ID
    """
    interface = get_chat_interface()
    messages = await interface.get_history(session_id)
    return [_message_to_response(m) for m in messages]


@router.post(
    "/sessions/{session_id}/approve",
    response_model=RegistrationResultResponse,
    summary="审批并注册策略",
    description="批准当前会话中的策略并将其注册到系统。",
)
async def approve_strategy(
    session_id: str,
    strategy_id: Optional[str] = None,
) -> RegistrationResultResponse:
    """
    审批并注册策略

    - **session_id**: 会话ID
    - **strategy_id**: 策略ID（可选）
    """
    interface = get_chat_interface()

    try:
        result = await interface.approve_and_register(
            session_id=session_id,
            strategy_id=strategy_id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    return RegistrationResultResponse(
        success=result.success,
        strategy_id=result.strategy_id,
        entry_id=result.entry_id,
        error=result.error,
    )


@router.post(
    "/sessions/{session_id}/reject",
    response_model=dict,
    summary="拒绝策略",
    description="拒绝当前会话中的策略。",
)
async def reject_strategy(
    session_id: str,
    reason: Optional[str] = None,
) -> dict:
    """
    拒绝策略

    - **session_id**: 会话ID
    - **reason**: 拒绝原因（可选）
    """
    interface = get_chat_interface()

    try:
        success = await interface.reject_strategy(
            session_id=session_id,
            reason=reason,
        )
        return {"success": success}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除会话",
    description="删除指定的聊天会话。",
)
async def delete_session(
    session_id: str,
) -> None:
    """
    删除会话

    - **session_id**: 会话ID
    """
    interface = get_chat_interface()

    try:
        await interface.delete_session(session_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.get(
    "/sessions",
    response_model=List[SessionResponse],
    summary="列出所有会话",
    description="列出所有聊天会话。",
)
async def list_sessions(
    limit: int = Query(100, ge=1, le=1000, description="返回数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
) -> List[SessionResponse]:
    """
    列出所有会话

    - **limit**: 返回数量（默认100）
    - **offset**: 偏移量（默认0）
    """
    interface = get_chat_interface()
    sessions = await interface.list_sessions(limit=limit, offset=offset)
    return [_session_to_response(s) for s in sessions]


@router.get(
    "/sessions/{session_id}",
    response_model=SessionResponse,
    summary="获取会话详情",
    description="获取指定会话的详细信息。",
)
async def get_session(
    session_id: str,
) -> SessionResponse:
    """
    获取会话详情

    - **session_id**: 会话ID
    """
    interface = get_chat_interface()
    session = await interface.get_session(session_id)

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    return _session_to_response(session)
