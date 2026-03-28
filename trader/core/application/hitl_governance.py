"""
HITL Governance - AI治理接口 (Human-in-the-Loop)
=================================================

核心职责：
1. AI建议生成 - 基于RiskCheckResult生成交易建议
2. Human审批队列 - 管理待审批的交易建议
3. 审批决策 - 支持批准/拒绝/修改参数
4. 审计日志 - 记录所有审批决策

关键设计原则：
1. 所有需要人工介入的场景（CRITICAL风险、大额交易、KillSwitch降级）都必须经过HITL
2. 审批决策必须包含完整的审计信息
3. 状态转换必须是原子性的
4. Core Plane禁止IO，纯计算逻辑和状态管理
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Any
import uuid

from trader.core.application.risk_engine import RiskCheckResult, RiskLevel
from trader.core.domain.models.signal import Signal


# ==================== 常量定义 ====================

HITL_TIMEOUT_SECONDS = 300  # 5分钟默认超时


# ==================== HITL决策枚举 ====================

class HITLDecision(Enum):
    """HITL决策类型"""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"


# ==================== 数据类 ====================

@dataclass
class AISuggestion:
    """
    AI建议数据类

    基于风控检查结果生成的交易建议。
    包含建议ID、信号、风控结果、推荐操作、置信度等信息。
    """
    suggestion_id: str
    signal: Signal
    risk_check_result: RiskCheckResult
    recommended_action: str  # BUY, SELL, HOLD, MODIFY
    confidence: float  # 0.0-1.0
    created_at: datetime
    suggested_params: Dict[str, Any] = field(default_factory=dict)  # 建议的参数
    reason: str = ""  # 建议原因
    requires_human_review: bool = False  # 是否需要人工审核

    def __post_init__(self):
        if not self.suggestion_id:
            object.__setattr__(self, 'suggestion_id', str(uuid.uuid4()))
        if isinstance(self.created_at, datetime) and self.created_at.tzinfo is None:
            object.__setattr__(self, 'created_at', self.created_at.replace(tzinfo=timezone.utc))


@dataclass
class HITLApprovalRecord:
    """
    审批记录数据类

    记录每次审批决策的完整信息，用于审计追溯。
    """
    record_id: str
    suggestion_id: str
    decision: HITLDecision
    approver: Optional[str]  # 审批人，None表示系统
    reason: Optional[str]  # 审批理由
    modified_params: Optional[Dict[str, Any]]  # 修改后的参数（MODIFIED时使用）
    created_at: datetime  # 建议创建时间
    decided_at: Optional[datetime]  # 审批时间
    timeout_seconds: int = HITL_TIMEOUT_SECONDS  # 超时时间
    metadata: Dict[str, Any] = field(default_factory=dict)  # 额外元数据

    def __post_init__(self):
        if not self.record_id:
            object.__setattr__(self, 'record_id', str(uuid.uuid4()))
        if isinstance(self.created_at, datetime) and self.created_at.tzinfo is None:
            object.__setattr__(self, 'created_at', self.created_at.replace(tzinfo=timezone.utc))
        if isinstance(self.decided_at, datetime) and self.decided_at.tzinfo is None:
            object.__setattr__(self, 'decided_at', self.decided_at.replace(tzinfo=timezone.utc))

    def is_approved(self) -> bool:
        """是否已批准"""
        return self.decision == HITLDecision.APPROVED

    def is_rejected(self) -> bool:
        """是否已拒绝"""
        return self.decision == HITLDecision.REJECTED

    def is_modified(self) -> bool:
        """是否已修改"""
        return self.decision == HITLDecision.MODIFIED

    def is_pending(self) -> bool:
        """是否待审批"""
        return self.decision == HITLDecision.PENDING

    def is_expired(self, current_time: datetime) -> bool:
        """是否已超时"""
        if self.decided_at is not None:
            return False
        elapsed = (current_time - self.created_at).total_seconds()
        return elapsed > self.timeout_seconds


# ==================== 端口协议 ====================

class HITLProviderPort(ABC):
    """
    HITL存储端口

    定义审批记录的持久化接口。
    可以实现为内存存储、数据库等。
    """

    @abstractmethod
    async def save_approval_record(self, record: HITLApprovalRecord) -> None:
        """保存审批记录"""
        pass

    @abstractmethod
    async def get_approval_record(self, record_id: str) -> Optional[HITLApprovalRecord]:
        """获取审批记录"""
        pass

    @abstractmethod
    async def get_approval_records_by_suggestion(
        self, suggestion_id: str
    ) -> List[HITLApprovalRecord]:
        """获取某个建议的所有审批记录"""
        pass

    @abstractmethod
    async def get_pending_approvals(self) -> List[HITLApprovalRecord]:
        """获取所有待审批记录"""
        pass

    @abstractmethod
    async def get_approval_history(
        self, limit: int = 100, offset: int = 0
    ) -> List[HITLApprovalRecord]:
        """获取审批历史"""
        pass


# ==================== 异常定义 ====================

class HITLGovernanceError(Exception):
    """HITL治理异常基类"""
    pass


class SuggestionNotFoundError(HITLGovernanceError):
    """建议不存在"""
    pass


class InvalidDecisionError(HITLGovernanceError):
    """无效决策"""
    pass


class SuggestionExpiredError(HITLGovernanceError):
    """建议已超时"""
    pass


# ==================== 治理器类 ====================

class HITLGovernance:
    """
    HITL治理器

    核心职责：
    1. 基于风控结果生成AI建议
    2. 管理待审批队列
    3. 处理审批决策（批准/拒绝/修改）
    4. 提供审计日志查询

    设计原则：
    - Core Plane纯计算逻辑，无IO
    - 状态转换原子性
    - 完整审计追溯
    """

    # 需要人工介入的风险等级阈值
    HUMAN_REVIEW_RISK_LEVEL = RiskLevel.HIGH

    # 大额交易阈值（USD）
    LARGE_TRADE_THRESHOLD_USD = Decimal("10000")

    def __init__(
        self,
        provider: Optional[HITLProviderPort] = None,
        timeout_seconds: int = HITL_TIMEOUT_SECONDS,
        large_trade_threshold: Optional[Decimal] = None,
    ):
        """
        初始化HITL治理器

        Args:
            provider: 存储端口实现（可选，用于审计记录持久化）
            timeout_seconds: 审批超时时间（秒）
            large_trade_threshold: 大额交易阈值
        """
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._large_trade_threshold = large_trade_threshold or self.LARGE_TRADE_THRESHOLD_USD

        # 内存中的待审批队列（Core Plane状态管理）
        self._pending_suggestions: Dict[str, AISuggestion] = {}
        self._approval_records: Dict[str, HITLApprovalRecord] = {}

    # ==================== 建议生成 ====================

    def generate_suggestion(
        self,
        signal: Signal,
        risk_result: RiskCheckResult,
    ) -> AISuggestion:
        """
        基于风控结果生成AI建议

        Args:
            signal: 交易信号
            risk_result: 风控检查结果

        Returns:
            AISuggestion: AI建议
        """
        # 确定推荐操作
        if risk_result.passed:
            recommended_action = self._get_action_from_signal(signal)
            confidence = float(signal.confidence) if signal.confidence else 0.8
        else:
            recommended_action = "HOLD"
            confidence = 0.0

        # 判断是否需要人工审核
        requires_human_review = self._requires_human_review(signal, risk_result)

        # 构建建议参数
        suggested_params = {
            "symbol": signal.symbol,
            "quantity": str(signal.quantity),
            "price": str(signal.price),
            "side": signal.signal_type.value,
            "stop_loss": str(signal.stop_loss) if signal.stop_loss else None,
            "take_profit": str(signal.take_profit) if signal.take_profit else None,
        }

        # 生成建议原因
        reason = self._generate_suggestion_reason(signal, risk_result, requires_human_review)

        suggestion = AISuggestion(
            suggestion_id=str(uuid.uuid4()),
            signal=signal,
            risk_check_result=risk_result,
            recommended_action=recommended_action,
            confidence=confidence,
            created_at=datetime.now(timezone.utc),
            suggested_params=suggested_params,
            reason=reason,
            requires_human_review=requires_human_review,
        )

        return suggestion

    def _get_action_from_signal(self, signal: Signal) -> str:
        """从信号类型获取推荐操作"""
        action_mapping = {
            "BUY": "BUY",
            "LONG": "BUY",
            "SELL": "SELL",
            "CLOSE_LONG": "SELL",
            "CLOSE_SHORT": "SELL",
            "SHORT": "SELL",
        }
        return action_mapping.get(signal.signal_type.value, "HOLD")

    def _get_risk_level_ordinal(self, risk_level: RiskLevel) -> int:
        """获取风险等级的ordinal（用于比较）"""
        return list(RiskLevel).index(risk_level)

    def _requires_human_review(
        self, signal: Signal, risk_result: RiskCheckResult
    ) -> bool:
        """
        判断是否需要人工审核

        需要人工介入的场景：
        1. RiskLevel >= HUMAN_REVIEW_RISK_LEVEL (HIGH或CRITICAL)
        2. 大额交易（超出阈值）
        3. 风控未通过但AI建议执行
        """
        # 风险等级触发（使用ordinal进行比较）
        if self._get_risk_level_ordinal(risk_result.risk_level) >= self._get_risk_level_ordinal(self.HUMAN_REVIEW_RISK_LEVEL):
            return True

        # 大额交易触发
        try:
            order_value = signal.price * signal.quantity
            if order_value > self._large_trade_threshold:
                return True
        except (TypeError, AttributeError):
            pass

        # 风控未通过但建议执行（特殊情况）
        if not risk_result.passed and risk_result.message:
            return True

        return False

    def _generate_suggestion_reason(
        self, signal: Signal, risk_result: RiskCheckResult, requires_review: bool
    ) -> str:
        """生成建议原因描述"""
        reasons = []

        # 风控状态
        if risk_result.passed:
            reasons.append(f"风控通过（{risk_result.risk_level.value}级）")
        else:
            reasons.append(f"风控未通过：{risk_result.rejection_reason.value if risk_result.rejection_reason else '未知原因'}")

        # 风险等级
        if risk_result.risk_level == RiskLevel.CRITICAL:
            reasons.append("CRITICAL风险需要人工确认")
        elif risk_result.risk_level == RiskLevel.HIGH:
            reasons.append("HIGH风险建议人工确认")

        # 大额交易
        try:
            order_value = signal.price * signal.quantity
            if order_value > self._large_trade_threshold:
                reasons.append(f"大额交易（价值${order_value}）")
        except (TypeError, AttributeError):
            pass

        # 审核状态
        if requires_review:
            reasons.append("需要人工审核")

        return "; ".join(reasons)

    # ==================== 审批队列管理 ====================

    def submit_for_approval(self, suggestion: AISuggestion) -> HITLApprovalRecord:
        """
        提交建议到审批队列

        Args:
            suggestion: AI建议

        Returns:
            HITLApprovalRecord: 审批记录
        """
        # 如果需要审核，加入待审批队列
        if suggestion.requires_human_review:
            self._pending_suggestions[suggestion.suggestion_id] = suggestion

        # 创建审批记录（初始状态为PENDING）
        record = HITLApprovalRecord(
            record_id=str(uuid.uuid4()),
            suggestion_id=suggestion.suggestion_id,
            decision=HITLDecision.PENDING,
            approver=None,
            reason=None,
            modified_params=None,
            created_at=suggestion.created_at,
            decided_at=None,
            timeout_seconds=self._timeout_seconds,
            metadata={
                "signal_id": suggestion.signal.signal_id,
                "strategy_name": suggestion.signal.strategy_name,
                "requires_human_review": suggestion.requires_human_review,
            },
        )

        self._approval_records[record.record_id] = record

        return record

    async def persist_approval_record_async(self, record_id: str) -> None:
        """
        异步保存审批记录到持久化层（由调用方在合适的async上下文中调用）
        
        注意：Core Plane不直接处理IO，此方法供服务层调用以持久化审批记录。
        
        Args:
            record_id: 审批记录ID
        """
        if self._provider is None:
            return
        
        record = self._approval_records.get(record_id)
        if record is None:
            return
        
        await self._provider.save_approval_record(record)

    def get_pending_suggestions(self) -> List[AISuggestion]:
        """
        获取待审批建议列表

        Returns:
            List[AISuggestion]: 待审批建议列表
        """
        return list(self._pending_suggestions.values())

    def get_pending_approvals(self) -> List[HITLApprovalRecord]:
        """
        获取待审批记录列表（排除已超时的）

        Args:
            current_time: 当前时间（用于判断超时）

        Returns:
            List[HITLApprovalRecord]: 待审批记录列表
        """
        current_time = datetime.now(timezone.utc)
        pending = []

        for record in self._approval_records.values():
            if record.is_pending() and not record.is_expired(current_time):
                pending.append(record)

        return pending

    def get_pending_approvals_with_timeout(
        self, current_time: datetime
    ) -> List[HITLApprovalRecord]:
        """
        获取待审批记录列表（包含超时信息）

        Args:
            current_time: 当前时间（用于判断超时）

        Returns:
            List[HITLApprovalRecord]: 待审批记录列表
        """
        pending = []
        for record in self._approval_records.values():
            if record.is_pending():
                pending.append(record)
        return pending

    # ==================== 审批决策 ====================

    def approve(
        self,
        suggestion_id: str,
        approver: str,
        reason: Optional[str] = None,
    ) -> HITLApprovalRecord:
        """
        批准建议

        Args:
            suggestion_id: 建议ID
            approver: 审批人
            reason: 审批理由

        Returns:
            HITLApprovalRecord: 更新后的审批记录

        Raises:
            SuggestionNotFoundError: 建议不存在
            InvalidDecisionError: 建议已审批
        """
        record = self._find_pending_record(suggestion_id)
        if record is None:
            raise SuggestionNotFoundError(f"建议不存在或已审批: {suggestion_id}")

        # 更新记录
        record.decision = HITLDecision.APPROVED
        record.approver = approver
        record.reason = reason
        record.decided_at = datetime.now(timezone.utc)

        # 从待审批队列移除
        self._pending_suggestions.pop(suggestion_id, None)

        return record

    def reject(
        self,
        suggestion_id: str,
        approver: str,
        reason: str,
    ) -> HITLApprovalRecord:
        """
        拒绝建议

        Args:
            suggestion_id: 建议ID
            approver: 审批人
            reason: 拒绝理由（必填）

        Returns:
            HITLApprovalRecord: 更新后的审批记录

        Raises:
            SuggestionNotFoundError: 建议不存在
            InvalidDecisionError: 建议已审批或未提供拒绝理由
        """
        if not reason:
            raise InvalidDecisionError("拒绝建议必须提供理由")

        record = self._find_pending_record(suggestion_id)
        if record is None:
            raise SuggestionNotFoundError(f"建议不存在或已审批: {suggestion_id}")

        # 更新记录
        record.decision = HITLDecision.REJECTED
        record.approver = approver
        record.reason = reason
        record.decided_at = datetime.now(timezone.utc)

        # 从待审批队列移除
        self._pending_suggestions.pop(suggestion_id, None)

        return record

    def modify_and_approve(
        self,
        suggestion_id: str,
        approver: str,
        new_params: Dict[str, Any],
        reason: Optional[str] = None,
    ) -> HITLApprovalRecord:
        """
        修改参数后批准建议

        Args:
            suggestion_id: 建议ID
            approver: 审批人
            new_params: 修改后的参数
            reason: 修改理由

        Returns:
            HITLApprovalRecord: 更新后的审批记录

        Raises:
            SuggestionNotFoundError: 建议不存在
            InvalidDecisionError: 建议已审批或未提供修改参数
        """
        if not new_params:
            raise InvalidDecisionError("修改审批必须提供新参数")

        record = self._find_pending_record(suggestion_id)
        if record is None:
            raise SuggestionNotFoundError(f"建议不存在或已审批: {suggestion_id}")

        # 更新记录
        record.decision = HITLDecision.MODIFIED
        record.approver = approver
        record.reason = reason
        record.modified_params = new_params
        record.decided_at = datetime.now(timezone.utc)

        # 从待审批队列移除
        self._pending_suggestions.pop(suggestion_id, None)

        return record

    def _find_pending_record(self, suggestion_id: str) -> Optional[HITLApprovalRecord]:
        """查找待审批记录"""
        for record in self._approval_records.values():
            if record.suggestion_id == suggestion_id and record.is_pending():
                return record
        return None

    def get_suggestion(self, suggestion_id: str) -> Optional[AISuggestion]:
        """获取建议"""
        return self._pending_suggestions.get(suggestion_id)

    def get_record(self, record_id: str) -> Optional[HITLApprovalRecord]:
        """获取审批记录"""
        return self._approval_records.get(record_id)

    def get_record_by_suggestion(self, suggestion_id: str) -> List[HITLApprovalRecord]:
        """获取某个建议的所有审批记录"""
        return [
            r for r in self._approval_records.values()
            if r.suggestion_id == suggestion_id
        ]

    # ==================== 审计日志 ====================

    def get_approval_history(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> List[HITLApprovalRecord]:
        """
        获取审批历史（审计日志）

        Args:
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            List[HITLApprovalRecord]: 审批历史记录
        """
        # 按创建时间降序排序
        sorted_records = sorted(
            self._approval_records.values(),
            key=lambda r: r.created_at,
            reverse=True,
        )

        # 应用分页
        return sorted_records[offset:offset + limit]

    def get_approval_stats(self) -> Dict[str, Any]:
        """
        获取审批统计信息

        Returns:
            Dict: 统计信息
        """
        total = len(self._approval_records)
        pending = sum(1 for r in self._approval_records.values() if r.is_pending())
        approved = sum(1 for r in self._approval_records.values() if r.is_approved())
        rejected = sum(1 for r in self._approval_records.values() if r.is_rejected())
        modified = sum(1 for r in self._approval_records.values() if r.is_modified())

        return {
            "total": total,
            "pending": pending,
            "approved": approved,
            "rejected": rejected,
            "modified": modified,
            "approval_rate": approved / total if total > 0 else 0.0,
        }

    # ==================== 辅助方法 ====================

    def is_high_value_trade(self, signal: Signal) -> bool:
        """判断是否为大额交易"""
        try:
            order_value = signal.price * signal.quantity
            return order_value > self._large_trade_threshold
        except (TypeError, AttributeError):
            return False

    def needs_human_review_for_risk_level(self, risk_level: RiskLevel) -> bool:
        """判断风险等级是否需要人工审核"""
        return self._get_risk_level_ordinal(risk_level) >= self._get_risk_level_ordinal(self.HUMAN_REVIEW_RISK_LEVEL)

    def cleanup_expired_suggestions(self, current_time: datetime) -> List[str]:
        """
        清理超时的建议

        注意：超时记录会保留在 _approval_records 中（标记为REJECTED）用于审计追溯。
        如需完全删除，请使用 purge_expired_records() 方法。

        Args:
            current_time: 当前时间

        Returns:
            List[str]: 被清理的建议ID列表
        """
        expired_ids = []

        for suggestion_id, suggestion in list(self._pending_suggestions.items()):
            # 查找对应的记录
            for record in self._approval_records.values():
                if record.suggestion_id == suggestion_id and record.is_expired(current_time):
                    expired_ids.append(suggestion_id)
                    # 将记录标记为拒绝（超时）- 保留用于审计
                    record.decision = HITLDecision.REJECTED
                    record.reason = f"审批超时（超过{self._timeout_seconds}秒）"
                    record.decided_at = current_time
                    break

        # 从待审批队列移除
        for suggestion_id in expired_ids:
            self._pending_suggestions.pop(suggestion_id, None)

        return expired_ids

    def purge_expired_records(self, current_time: datetime) -> List[str]:
        """
        完全清除超时的审批记录（从_approval_records中删除）

        与 cleanup_expired_suggestions() 的区别：
        - cleanup_expired_suggestions: 仅从待审批队列移除，记录保留在审计历史
        - purge_expired_records: 完全删除记录（用于GDPR等场景）

        Args:
            current_time: 当前时间

        Returns:
            List[str]: 被删除的记录ID列表
        """
        purged_ids = []

        for record_id, record in list(self._approval_records.items()):
            if record.is_pending() and record.is_expired(current_time):
                purged_ids.append(record_id)
                self._approval_records.pop(record_id, None)

        # 同时从待审批队列移除
        for suggestion_id in list(self._pending_suggestions.keys()):
            for record_id in purged_ids:
                record = self._approval_records.get(record_id)
                if record and record.suggestion_id == suggestion_id:
                    self._pending_suggestions.pop(suggestion_id, None)
                    break

        return purged_ids
