"""
In-Memory Store - 内存存储实现
==============================

此模块实现基于 Python 内存字典的 PortfolioProposalStore。

设计原则：
1. 简单直接，用于开发/测试
2. 行为语义必须与 Postgres 实现一致
3. 不包含任何数据库细节

语义约束：
- save() 是 upsert：存在则更新，不存在则创建
- delete() 对不存在的 id 静默成功
- list_*() 按 created_at DESC 排序
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

from trader.adapters.persistence.portfolio_proposals.models import (
    ProposalModel,
    ProposalStatus,
    ProposalType,
)
from trader.adapters.persistence.portfolio_proposals.store_protocol import (
    PortfolioProposalStore,
)


class InMemoryPortfolioProposalStore:
    """
    内存存储实现
    
    使用线程安全的字典存储提案数据。
    主要用于开发/测试环境。
    
    特性：
    - 线程安全（使用 threading.RLock）
    - 异步友好的锁（实际同步锁，在 async 上下文中会阻塞）
    - 无需初始化（initialize 是 no-op）
    """
    
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._proposals: Dict[str, ProposalModel] = {}
        self._initialized = False
    
    async def initialize(self) -> None:
        """初始化存储（no-op）"""
        with self._lock:
            self._initialized = True
    
    @property
    def is_initialized(self) -> bool:
        """检查是否已初始化"""
        with self._lock:
            return self._initialized
    
    def _check_initialized(self) -> None:
        """检查是否已初始化"""
        if not self._initialized:
            raise RuntimeError("Store not initialized. Call initialize() first.")
    
    async def save(self, proposal: ProposalModel) -> str:
        """
        保存提案（Upsert）
        
        如果 proposal_id 已存在，则更新；
        否则创建新记录。
        
        Args:
            proposal: 提案模型
            
        Returns:
            proposal_id
        """
        self._check_initialized()
        
        proposal.save()  # 更新 updated_at
        
        with self._lock:
            self._proposals[proposal.proposal_id] = proposal
        
        return proposal.proposal_id
    
    async def get_by_id(self, proposal_id: str) -> Optional[ProposalModel]:
        """
        按 ID 获取提案
        
        Args:
            proposal_id: 提案 ID
            
        Returns:
            提案模型，如果不存在则返回 None
        """
        self._check_initialized()
        
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if proposal is None:
                return None
            # 返回副本，避免外部修改
            return proposal
    
    async def list_by_status(
        self,
        status: ProposalStatus,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ProposalModel]:
        """
        按状态列出提案
        
        Args:
            status: 提案状态
            limit: 返回数量限制
            offset: 偏移量
            
        Returns:
            提案列表，按 created_at DESC 排序
        """
        self._check_initialized()
        
        with self._lock:
            filtered = [
                p for p in self._proposals.values()
                if p.status == status
            ]
            # 按 created_at DESC 排序
            filtered.sort(key=lambda p: p.created_at, reverse=True)
            return filtered[offset:offset + limit]
    
    async def list_by_type(
        self,
        proposal_type: ProposalType,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ProposalModel]:
        """
        按类型列出提案
        
        Args:
            proposal_type: 提案类型
            limit: 返回数量限制
            offset: 偏移量
            
        Returns:
            提案列表，按 created_at DESC 排序
        """
        self._check_initialized()
        
        with self._lock:
            filtered = [
                p for p in self._proposals.values()
                if p.proposal_type == proposal_type
            ]
            # 按 created_at DESC 排序
            filtered.sort(key=lambda p: p.created_at, reverse=True)
            return filtered[offset:offset + limit]
    
    async def list_by_specialist(
        self,
        specialist_type: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ProposalModel]:
        """
        按 Specialist 类型列出提案（仅 SLEEVE 类型）
        
        Args:
            specialist_type: Specialist 类型
            limit: 返回数量限制
            offset: 偏移量
            
        Returns:
            提案列表，按 created_at DESC 排序
        """
        self._check_initialized()
        
        with self._lock:
            filtered = [
                p for p in self._proposals.values()
                if p.proposal_type == ProposalType.SLEEVE
                and p.specialist_type == specialist_type
            ]
            # 按 created_at DESC 排序
            filtered.sort(key=lambda p: p.created_at, reverse=True)
            return filtered[offset:offset + limit]
    
    async def delete(self, proposal_id: str) -> None:
        """
        删除提案
        
        对不存在的 proposal_id 静默成功（幂等）。
        
        Args:
            proposal_id: 提案 ID
        """
        self._check_initialized()
        
        with self._lock:
            self._proposals.pop(proposal_id, None)
    
    async def exists(self, proposal_id: str) -> bool:
        """
        检查提案是否存在
        
        Args:
            proposal_id: 提案 ID
            
        Returns:
            True 如果存在，否则 False
        """
        self._check_initialized()
        
        with self._lock:
            return proposal_id in self._proposals
    
    async def count(self) -> int:
        """
        统计提案总数
        
        Returns:
            提案总数
        """
        self._check_initialized()
        
        with self._lock:
            return len(self._proposals)
    
    async def health_check(self) -> bool:
        """
        健康检查
        
        内存存储总是健康的。
        """
        return True
    
    # =========================================================================
    # 测试辅助方法
    # =========================================================================
    
    def _clear_all(self) -> None:
        """清除所有数据（仅用于测试）"""
        with self._lock:
            self._proposals.clear()
    
    def _get_all(self) -> List[ProposalModel]:
        """获取所有提案（仅用于测试）"""
        with self._lock:
            return list(self._proposals.values())
    
    def __repr__(self) -> str:
        with self._lock:
            return f"InMemoryPortfolioProposalStore(count={len(self._proposals)})"