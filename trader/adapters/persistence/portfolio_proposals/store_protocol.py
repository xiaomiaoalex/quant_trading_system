"""
Store Protocol - 业务存储接口
============================

此模块定义 PortfolioProposalStore 协议（Protocol），
表达业务层存储语义，不暴露底层数据库操作。

设计原则：
1. 面向业务语义设计接口
2. 不包含 fetch/execute/fetchone 等数据库方法
3. 所有方法都是异步的（async/await）
4. 使用 Python Protocol 定义结构化子类型（static duck typing）

接口能力：
- initialize(): 初始化存储
- save(): Upsert 提案
- get_by_id(): 按 ID 获取提案
- list_by_status(): 按状态列出提案
- list_by_type(): 按类型列出提案
- list_by_specialist(): 按 specialist 类型列出提案（仅 SLEEVE）
- delete(): 删除提案（不存在时静默成功）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, List, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from trader.adapters.persistence.portfolio_proposals.models import (
        ProposalModel,
        ProposalStatus,
        ProposalType,
    )


@runtime_checkable
class PortfolioProposalStore(Protocol):
    """
    业务存储接口协议
    
    定义组合提案存储的业务语义，
    具体实现（内存/PostgreSQL）必须实现所有方法。
    
    语义约束：
    - save() 是 upsert：存在则更新，不存在则创建
    - get_by_id() 查不到时返回 None
    - delete() 对不存在的 id 静默成功（幂等）
    - list_*() 方法返回列表总是有序的（按 created_at DESC）
    """
    
    async def initialize(self) -> None:
        """
        初始化存储
        
        对于 InMemory 实现：no-op
        对于 Postgres 实现：建立连接/创建表
        """
        ...
    
    async def save(self, proposal: ProposalModel) -> str:
        """
        保存提案（Upsert）
        
        Args:
            proposal: 提案模型
            
        Returns:
            proposal_id
        """
        ...
    
    async def get_by_id(self, proposal_id: str) -> Optional[ProposalModel]:
        """
        按 ID 获取提案
        
        Args:
            proposal_id: 提案 ID
            
        Returns:
            提案模型，如果不存在则返回 None
        """
        ...
    
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
        ...
    
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
        ...
    
    async def list_by_specialist(
        self,
        specialist_type: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ProposalModel]:
        """
        按 Specialist 类型列出提案（仅 SLEEVE 类型）
        
        Args:
            specialist_type: Specialist 类型（如 "trend", "price_volume"）
            limit: 返回数量限制
            offset: 偏移量
            
        Returns:
            提案列表，按 created_at DESC 排序
        """
        ...
    
    async def delete(self, proposal_id: str) -> None:
        """
        删除提案
        
        对不存在的 proposal_id 静默成功（幂等）。
        
        Args:
            proposal_id: 提案 ID
        """
        ...
    
    # =========================================================================
    # 可选方法（提供默认实现或 NotImplemented）
    # =========================================================================
    
    async def exists(self, proposal_id: str) -> bool:
        """
        检查提案是否存在
        
        默认实现：调用 get_by_id 并检查结果
        
        Args:
            proposal_id: 提案 ID
            
        Returns:
            True 如果存在，否则 False
        """
        result = await self.get_by_id(proposal_id)
        return result is not None
    
    async def count(self) -> int:
        """
        统计提案总数
        
        默认实现：调用 list_by_type 获取所有并计数（低效，override 推荐）
        
        Returns:
            提案总数
        """
        from trader.adapters.persistence.portfolio_proposals.models import ProposalType
        results = await self.list_by_type(
            ProposalType.SLEEVE,
            limit=0,
            offset=0,
        )
        return len(results)
    
    async def health_check(self) -> bool:
        """
        健康检查
        
        检查存储是否可用。
        InMemory 实现：总是 True
        Postgres 实现：检查连接
        """
        ...


class StoreError(Exception):
    """存储操作错误"""
    pass


class NotFoundError(StoreError):
    """资源不存在错误"""
    pass


class IntegrityError(StoreError):
    """数据完整性错误"""
    pass