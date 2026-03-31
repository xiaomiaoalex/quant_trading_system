"""
AIAuditLog - AI审计日志
========================

核心职责：
1. 记录所有AI生成的代码
2. 版本历史
3. 审批状态
4. 使用统计分析

设计原则：
1. 不可变性：审计日志一旦写入不可修改
2. 完整性：记录完整的操作上下文
3. 可追溯性：支持按策略ID、时间、审批状态查询
4. 统计能力：支持使用分析

数据结构：
- AuditEntry: 单条审计记录
- AuditStatus: 审计状态枚举
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol
from collections import defaultdict


class AuditStatus(Enum):
    """审计状态"""
    DRAFT = "draft"           # 草稿（未提交审批）
    PENDING = "pending"       # 待审批
    APPROVED = "approved"     # 已批准
    REJECTED = "rejected"     # 已拒绝
    REVISION = "revision"     # 需要修订
    ACTIVE = "active"         # 激活使用中
    ARCHIVED = "archived"     # 已归档
    DELETED = "deleted"       # 已删除


class AuditEventType(Enum):
    """审计事件类型"""
    GENERATED = "generated"           # 代码生成
    VALIDATED = "validated"           # 代码验证
    SUBMITTED = "submitted"           # 提交审批
    APPROVED = "approved"             # 审批通过
    REJECTED = "rejected"             # 审批拒绝
    REVISION_REQUESTED = "revision"   # 要求修订
    DEPLOYED = "deployed"             # 部署
    UNDEPLOYED = "undeployed"         # 取消部署
    ARCHIVED = "archived"             # 归档
    MODIFIED = "modified"             # 修改
    EXECUTED = "executed"             # 执行
    ERROR = "error"                   # 错误


@dataclass(slots=True)
class AuditEntry:
    """
    审计日志条目
    
    属性：
        entry_id: 条目ID (UUID)
        strategy_id: 策略ID
        strategy_name: 策略名称
        version: 策略版本
        event_type: 事件类型
        status: 当前状态
        prompt: 用户输入的提示词
        generated_code: 生成的代码
        code_hash: 代码哈希值（用于完整性校验）
        llm_backend: 使用的LLM后端
        llm_model: LLM模型
        execution_result: 执行结果（如果执行过）
       审批人: approver: 审批人（如果已审批）
        approval_comment: 审批意见
        metadata: 扩展元数据
        created_at: 创建时间
        updated_at: 更新时间
    """
    entry_id: str
    strategy_id: str
    strategy_name: str
    version: str
    event_type: AuditEventType
    status: AuditStatus
    prompt: str
    generated_code: str
    code_hash: str
    llm_backend: str
    llm_model: str
    created_at: datetime
    updated_at: datetime
    
    # 可选字段
    execution_result: Optional[Dict[str, Any]] = None
    approver: Optional[str] = None
    approval_comment: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """初始化默认值"""
        if not self.entry_id:
            object.__setattr__(self, 'entry_id', str(uuid.uuid4()))
        if isinstance(self.created_at, datetime) and self.created_at.tzinfo is None:
            object.__setattr__(self, 'created_at', self.created_at.replace(tzinfo=timezone.utc))
        if isinstance(self.updated_at, datetime) and self.updated_at.tzinfo is None:
            object.__setattr__(self, 'updated_at', self.updated_at.replace(tzinfo=timezone.utc))
    
    def update_status(self, new_status: AuditStatus, event_type: AuditEventType, 
                     approver: Optional[str] = None, comment: Optional[str] = None) -> None:
        """更新状态"""
        object.__setattr__(self, 'status', new_status)
        object.__setattr__(self, 'event_type', event_type)
        object.__setattr__(self, 'updated_at', datetime.now(timezone.utc))
        if approver:
            object.__setattr__(self, 'approver', approver)
        if comment:
            object.__setattr__(self, 'approval_comment', comment)
        object.__setattr__(self, 'metadata', {
            **self.metadata,
            f'last_event_{event_type.value}_at': datetime.now(timezone.utc).isoformat(),
        })
    
    def set_execution_result(self, result: Dict[str, Any]) -> None:
        """设置执行结果"""
        object.__setattr__(self, 'execution_result', result)
        object.__setattr__(self, 'updated_at', datetime.now(timezone.utc))
    
    @property
    def is_active(self) -> bool:
        """是否激活"""
        return self.status == AuditStatus.ACTIVE
    
    @property
    def is_pending(self) -> bool:
        """是否待审批"""
        return self.status == AuditStatus.PENDING
    
    @property
    def is_approved(self) -> bool:
        """是否已批准"""
        return self.status == AuditStatus.APPROVED


class AuditLogStorage(Protocol):
    """审计日志存储接口"""
    
    async def save(self, entry: AuditEntry) -> None:
        """保存审计条目"""
        ...
    
    async def get(self, entry_id: str) -> Optional[AuditEntry]:
        """获取审计条目"""
        ...
    
    async def get_by_strategy(self, strategy_id: str) -> List[AuditEntry]:
        """获取策略的所有版本"""
        ...
    
    async def list_by_status(self, status: AuditStatus, limit: int = 100) -> List[AuditEntry]:
        """按状态查询"""
        ...
    
    async def list_by_time_range(
        self, 
        start: datetime, 
        end: datetime, 
        limit: int = 100
    ) -> List[AuditEntry]:
        """按时间范围查询"""
        ...
    
    async def list_recent(self, limit: int = 100) -> List[AuditEntry]:
        """获取最近的审计记录"""
        ...


class InMemoryAuditLogStorage:
    """内存审计日志存储（用于测试）"""
    
    def __init__(self):
        self._entries: Dict[str, AuditEntry] = {}
    
    async def save(self, entry: AuditEntry) -> None:
        self._entries[entry.entry_id] = entry
    
    async def get(self, entry_id: str) -> Optional[AuditEntry]:
        return self._entries.get(entry_id)
    
    async def get_by_strategy(self, strategy_id: str) -> List[AuditEntry]:
        return [e for e in self._entries.values() if e.strategy_id == strategy_id]
    
    async def list_by_status(self, status: AuditStatus, limit: int = 100) -> List[AuditEntry]:
        return sorted(
            [e for e in self._entries.values() if e.status == status],
            key=lambda x: x.created_at,
            reverse=True
        )[:limit]
    
    async def list_by_time_range(
        self, 
        start: datetime, 
        end: datetime, 
        limit: int = 100
    ) -> List[AuditEntry]:
        return sorted(
            [e for e in self._entries.values() if start <= e.created_at <= end],
            key=lambda x: x.created_at,
            reverse=True
        )[:limit]
    
    async def list_recent(self, limit: int = 100) -> List[AuditEntry]:
        return sorted(
            self._entries.values(),
            key=lambda x: x.created_at,
            reverse=True
        )[:limit]


@dataclass
class AuditStatistics:
    """审计统计数据"""
    total_generations: int = 0
    total_approved: int = 0
    total_rejected: int = 0
    total_deployed: int = 0
    by_backend: Dict[str, int] = field(default_factory=dict)
    by_model: Dict[str, int] = field(default_factory=dict)
    by_status: Dict[str, int] = field(default_factory=dict)
    average_generation_time: float = 0.0
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None


class AIAuditLog:
    """
    AI审计日志管理器
    
    核心功能：
    1. 记录AI生成的代码
    2. 管理版本历史
    3. 跟踪审批状态
    4. 提供统计分析
    
    使用方式：
        audit_log = AIAuditLog(storage=InMemoryAuditLogStorage())
        
        # 记录生成
        entry = audit_log.log_generation(
            prompt="...",
            generated_code="...",
            llm_backend="openai",
            llm_model="gpt-4"
        )
        
        # 提交审批
        audit_log.submit_for_approval(entry.entry_id)
        
        # 审批
        audit_log.approve(entry.entry_id, approver="admin", comment="LGTM")
    """
    
    def __init__(self, storage: Optional[AuditLogStorage] = None):
        self._storage = storage or InMemoryAuditLogStorage()
        # 内存缓存用于快速查询
        self._entries: Dict[str, AuditEntry] = {}
        self._strategy_versions: Dict[str, List[str]] = defaultdict(list)
    
    async def initialize(self) -> None:
        """初始化（从存储加载已有数据）"""
        recent = await self._storage.list_recent(limit=10000)
        for entry in recent:
            self._entries[entry.entry_id] = entry
            self._strategy_versions[entry.strategy_id].append(entry.entry_id)
    
    def _compute_code_hash(self, code: str) -> str:
        """计算代码哈希值"""
        import hashlib
        return hashlib.sha256(code.encode('utf-8')).hexdigest()[:16]
    
    def _generate_strategy_id(self, name: str) -> str:
        """生成策略ID"""
        return f"strategy_{uuid.uuid4().hex[:12]}"
    
    def _generate_version(self, strategy_id: str) -> str:
        """生成版本号"""
        existing = self._strategy_versions.get(strategy_id, [])
        if not existing:
            return "1.0.0"
        # 获取最新版本
        latest = max(
            [self._entries[eid].version for eid in existing],
            key=lambda v: [int(x) for x in v.split('.')]
        )
        parts = latest.split('.')
        patch = int(parts[2]) + 1
        return f"{parts[0]}.{parts[1]}.{patch}"
    
    async def log_generation(
        self,
        prompt: str,
        generated_code: str,
        llm_backend: str,
        llm_model: str,
        strategy_name: str,
        strategy_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        """
        记录代码生成
        
        Args:
            prompt: 用户提示词
            generated_code: 生成的代码
            llm_backend: LLM后端
            llm_model: LLM模型
            strategy_name: 策略名称
            strategy_id: 策略ID（可选，用于更新现有策略）
            metadata: 扩展元数据
            
        Returns:
            AuditEntry: 审计条目
        """
        now = datetime.now(timezone.utc)
        
        # 生成或使用现有策略ID
        if strategy_id and strategy_id in self._strategy_versions:
            sid = strategy_id
            version = self._generate_version(sid)
        else:
            sid = strategy_id or self._generate_strategy_id(strategy_name)
            version = "1.0.0"
        
        entry = AuditEntry(
            entry_id=str(uuid.uuid4()),
            strategy_id=sid,
            strategy_name=strategy_name,
            version=version,
            event_type=AuditEventType.GENERATED,
            status=AuditStatus.DRAFT,
            prompt=prompt,
            generated_code=generated_code,
            code_hash=self._compute_code_hash(generated_code),
            llm_backend=llm_backend,
            llm_model=llm_model,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        
        # 保存到内存缓存
        self._entries[entry.entry_id] = entry
        self._strategy_versions[sid].append(entry.entry_id)
        
        # 持久化
        await self._storage.save(entry)
        
        return entry
    
    async def log_validation(
        self,
        entry_id: str,
        validation_result: Dict[str, Any],
    ) -> AuditEntry:
        """记录验证结果"""
        entry = self._entries.get(entry_id)
        if not entry:
            raise ValueError(f"审计条目不存在: {entry_id}")
        
        entry.event_type = AuditEventType.VALIDATED
        entry.metadata['validation'] = validation_result
        entry.updated_at = datetime.now(timezone.utc)
        
        await self._storage.save(entry)
        return entry
    
    async def submit_for_approval(self, entry_id: str) -> AuditEntry:
        """提交审批"""
        entry = self._entries.get(entry_id)
        if not entry:
            raise ValueError(f"审计条目不存在: {entry_id}")
        
        entry.update_status(
            new_status=AuditStatus.PENDING,
            event_type=AuditEventType.SUBMITTED,
        )
        
        await self._storage.save(entry)
        return entry
    
    async def approve(
        self,
        entry_id: str,
        approver: str,
        comment: Optional[str] = None,
    ) -> AuditEntry:
        """批准"""
        entry = self._entries.get(entry_id)
        if not entry:
            raise ValueError(f"审计条目不存在: {entry_id}")
        
        entry.update_status(
            new_status=AuditStatus.APPROVED,
            event_type=AuditEventType.APPROVED,
            approver=approver,
            comment=comment,
        )
        
        await self._storage.save(entry)
        return entry
    
    async def reject(
        self,
        entry_id: str,
        approver: str,
        comment: Optional[str] = None,
    ) -> AuditEntry:
        """拒绝"""
        entry = self._entries.get(entry_id)
        if not entry:
            raise ValueError(f"审计条目不存在: {entry_id}")
        
        entry.update_status(
            new_status=AuditStatus.REJECTED,
            event_type=AuditEventType.REJECTED,
            approver=approver,
            comment=comment,
        )
        
        await self._storage.save(entry)
        return entry
    
    async def request_revision(
        self,
        entry_id: str,
        approver: str,
        comment: Optional[str] = None,
    ) -> AuditEntry:
        """要求修订"""
        entry = self._entries.get(entry_id)
        if not entry:
            raise ValueError(f"审计条目不存在: {entry_id}")
        
        entry.update_status(
            new_status=AuditStatus.REVISION,
            event_type=AuditEventType.REVISION_REQUESTED,
            approver=approver,
            comment=comment,
        )
        
        await self._storage.save(entry)
        return entry
    
    async def deploy(self, entry_id: str) -> AuditEntry:
        """部署"""
        entry = self._entries.get(entry_id)
        if not entry:
            raise ValueError(f"审计条目不存在: {entry_id}")
        
        if entry.status != AuditStatus.APPROVED:
            raise ValueError(f"只能部署已批准的策略，当前状态: {entry.status.value}")
        
        entry.update_status(
            new_status=AuditStatus.ACTIVE,
            event_type=AuditEventType.DEPLOYED,
        )
        
        await self._storage.save(entry)
        return entry
    
    async def archive(self, entry_id: str) -> AuditEntry:
        """归档"""
        entry = self._entries.get(entry_id)
        if not entry:
            raise ValueError(f"审计条目不存在: {entry_id}")
        
        entry.update_status(
            new_status=AuditStatus.ARCHIVED,
            event_type=AuditEventType.ARCHIVED,
        )
        
        await self._storage.save(entry)
        return entry
    
    async def log_execution(
        self,
        entry_id: str,
        execution_result: Dict[str, Any],
    ) -> AuditEntry:
        """记录执行结果"""
        entry = self._entries.get(entry_id)
        if not entry:
            raise ValueError(f"审计条目不存在: {entry_id}")
        
        entry.set_execution_result(execution_result)
        entry.metadata['execution_count'] = entry.metadata.get('execution_count', 0) + 1
        entry.metadata['last_execution_at'] = datetime.now(timezone.utc).isoformat()
        
        await self._storage.save(entry)
        return entry
    
    async def get_entry(self, entry_id: str) -> Optional[AuditEntry]:
        """获取审计条目"""
        return self._entries.get(entry_id)
    
    async def get_strategy_versions(self, strategy_id: str) -> List[AuditEntry]:
        """获取策略的所有版本"""
        entry_ids = self._strategy_versions.get(strategy_id, [])
        return [self._entries[eid] for eid in entry_ids if eid in self._entries]
    
    async def get_latest_version(self, strategy_id: str) -> Optional[AuditEntry]:
        """获取策略的最新版本"""
        versions = await self.get_strategy_versions(strategy_id)
        if not versions:
            return None
        return max(versions, key=lambda v: [int(x) for x in v.version.split('.')])
    
    async def get_pending_approvals(self) -> List[AuditEntry]:
        """获取待审批列表"""
        return [e for e in self._entries.values() if e.status == AuditStatus.PENDING]
    
    async def get_active_strategies(self) -> List[AuditEntry]:
        """获取激活的策略"""
        return [e for e in self._entries.values() if e.status == AuditStatus.ACTIVE]
    
    async def get_statistics(
        self,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> AuditStatistics:
        """获取统计数据"""
        stats = AuditStatistics()
        stats.period_start = period_start
        stats.period_end = period_end
        
        entries = list(self._entries.values())
        
        # 按时间过滤
        if period_start:
            entries = [e for e in entries if e.created_at >= period_start]
        if period_end:
            entries = [e for e in entries if e.created_at <= period_end]
        
        stats.total_generations = len(entries)
        stats.total_approved = len([e for e in entries if e.status == AuditStatus.APPROVED])
        stats.total_rejected = len([e for e in entries if e.status == AuditStatus.REJECTED])
        stats.total_deployed = len([e for e in entries if e.status == AuditStatus.ACTIVE])
        
        # 按后端统计
        for entry in entries:
            stats.by_backend[entry.llm_backend] = stats.by_backend.get(entry.llm_backend, 0) + 1
            stats.by_model[entry.llm_model] = stats.by_model.get(entry.llm_model, 0) + 1
            stats.by_status[entry.status.value] = stats.by_status.get(entry.status.value, 0) + 1
        
        return stats
    
    async def search(
        self,
        query: Optional[str] = None,
        status: Optional[AuditStatus] = None,
        llm_backend: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditEntry]:
        """搜索审计记录"""
        results = list(self._entries.values())
        
        if status:
            results = [e for e in results if e.status == status]
        
        if llm_backend:
            results = [e for e in results if e.llm_backend == llm_backend]
        
        if query:
            query_lower = query.lower()
            results = [
                e for e in results 
                if query_lower in e.prompt.lower() 
                or query_lower in e.generated_code.lower()
                or query_lower in e.strategy_name.lower()
            ]
        
        # 按时间倒序
        results.sort(key=lambda x: x.created_at, reverse=True)
        
        return results[:limit]
