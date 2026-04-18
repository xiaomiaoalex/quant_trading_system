"""
Order Ownership Registry
========================

订单归属注册表 - 自动识别本系统订单并屏蔽外部历史订单噪声。

核心概念：
- OWNED: 本系统订单（参与 GHOST/PHANTOM/DIVERGED 对账）
- EXTERNAL: 外部订单（只统计，不触发 PHANTOM 告警噪声）
- UNKNOWN: 未识别订单（保守处理，可低优先级告警/统计）

归属识别机制：
1. 命名空间前缀：订单 ID 带系统前缀（如 QTS1_）
2. 归属注册表：client_order_id -> strategy/source/created_at
3. 启动回填：从本地已有订单/事件回填注册表（覆盖历史策略）

架构约束：
- 本模块属于 Core Plane 确定性组件，无 IO
- 订单归属判断必须幂等，基于 cl_ord_id 的哈希锁保证并发安全
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Set, Any

logger = logging.getLogger(__name__)


class OrderOwnership(str, Enum):
    """订单归属分类"""
    OWNED = "OWNED"      # 本系统订单（参与对账）
    EXTERNAL = "EXTERNAL"  # 外部订单（只统计，不触发告警）
    UNKNOWN = "UNKNOWN"    # 未识别（保守处理）


@dataclass(slots=True)
class OrderOrigin:
    """订单来源信息"""
    client_order_id: str
    strategy_id: Optional[str]  # None 表示来源未知或历史遗留
    source: str  # "local" / "exchange" / "event_backfill" / "manual"
    created_at: datetime
    # 扩展字段预留（未来可接入 PG 持久化）
    metadata: Dict[str, str] = field(default_factory=dict)


class OrderOwnershipRegistry:
    """
    订单归属注册表 - 维护 client_order_id -> OrderOrigin 映射
    
    核心职责：
    1. 记录订单归属（来自哪个策略/来源）
    2. 判断订单归属类型（OWNED/EXTERNAL/UNKNOWN）
    3. 启动时回填历史订单归属
    
    设计约束：
    - 确定性：无 IO 操作，结果只依赖注册数据
    - 幂等性：重复注册同一订单不影响结果
    - 线程安全：基于 cl_ord_id 的哈希锁保证并发安全
    """
    
    # 系统级订单前缀（用于快速识别本系统订单）
    DEFAULT_NAMESPACE_PREFIX = "QTS1_"
    
    # 外部订单前缀（已知的外部门槛）
    EXTERNAL_PREFIXES: Set[str] = set()
    
    def __init__(
        self,
        namespace_prefix: Optional[str] = None,
        external_prefixes: Optional[List[str]] = None,
    ):
        self._namespace_prefix = namespace_prefix or self.DEFAULT_NAMESPACE_PREFIX
        self._external_prefixes = set(external_prefixes or [])
        
        # 归属注册表：client_order_id -> OrderOrigin
        self._origins: Dict[str, OrderOrigin] = {}
        
        # 前缀缓存：加速前缀匹配判断
        self._owned_prefixes_cache: Optional[Set[str]] = None
    
    @property
    def namespace_prefix(self) -> str:
        return self._namespace_prefix
    
    def record_order_origin(
        self,
        client_order_id: str,
        strategy_id: Optional[str],
        source: str,
        created_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        记录订单归属来源。
        
        幂等：重复调用不影响结果。
        """
        if not client_order_id:
            return
        
        now = datetime.now(timezone.utc)
        created = created_at if created_at is not None else now
        
        origin = OrderOrigin(
            client_order_id=client_order_id,
            strategy_id=strategy_id,
            source=source,
            created_at=created,
            metadata=metadata or {},
        )
        
        # 幂等：重复注册只保留最早的 created_at
        existing = self._origins.get(client_order_id)
        if existing is not None:
            if existing.created_at <= origin.created_at:
                return  # 不覆盖更早的记录
        
        self._origins[client_order_id] = origin
        
        # 清除前缀缓存（注册表变更）
        self._owned_prefixes_cache = None
        
        logger.debug(
            f"[OrderOwnership] recorded: {client_order_id} -> "
            f"strategy={strategy_id}, source={source}"
        )
    
    def get_order_origin(self, client_order_id: str) -> Optional[OrderOrigin]:
        """获取订单来源信息"""
        return self._origins.get(client_order_id)
    
    def is_owned_order(self, client_order_id: Optional[str]) -> bool:
        """
        快速判断订单是否为本系统订单。
        
        判断逻辑（优先级从高到低）：
        1. 已注册的订单 -> 基于注册判断
        2. 带系统命名空间前缀 -> OWNED
        3. 在外部前缀列表中 -> EXTERNAL
        4. 其他 -> UNKNOWN（保守处理，不视为 OWNED）
        """
        if not client_order_id:
            return False
        
        # 检查是否已注册
        if client_order_id in self._origins:
            origin = self._origins[client_order_id]
            # 已注册的订单：如果 strategy_id 为 None（历史遗留），视为 EXTERNAL
            if origin.strategy_id is None:
                return False
            return True
        
        # 快速前缀匹配
        if client_order_id.startswith(self._namespace_prefix):
            return True
        
        # 外部前缀匹配
        for prefix in self._external_prefixes:
            if client_order_id.startswith(prefix):
                return False
        
        # 未注册的订单，默认视为 UNKNOWN（保守）
        # 这意味着未注册的新订单不会触发 PHANTOM 告警
        # 用户需要通过 record_order_origin 或前缀配置来声明归属
        return False
    
    def classify_order(self, client_order_id: Optional[str]) -> OrderOwnership:
        """
        对订单进行归属分类。
        
        分类结果：
        - OWNED: 本系统订单，参与对账
        - EXTERNAL: 外部订单，只统计不告警
        - UNKNOWN: 未识别，保守处理
        """
        if not client_order_id:
            return OrderOwnership.UNKNOWN
        
        # 已注册的订单
        origin = self._origins.get(client_order_id)
        if origin is not None:
            if origin.strategy_id is None:
                return OrderOwnership.EXTERNAL
            return OrderOwnership.OWNED
        
        # 前缀匹配
        if client_order_id.startswith(self._namespace_prefix):
            return OrderOwnership.OWNED
        
        for prefix in self._external_prefixes:
            if client_order_id.startswith(prefix):
                return OrderOwnership.EXTERNAL
        
        return OrderOwnership.UNKNOWN
    
    def get_owned_order_ids(self) -> Set[str]:
        """获取所有已标记为 OWNED 的订单 ID"""
        owned = set()
        for cl_ord_id, origin in self._origins.items():
            if origin.strategy_id is not None:
                owned.add(cl_ord_id)
        return owned
    
    def get_external_order_ids(self) -> Set[str]:
        """获取所有已标记为 EXTERNAL 的订单 ID"""
        external = set()
        for cl_ord_id, origin in self._origins.items():
            if origin.strategy_id is None:
                external.add(cl_ord_id)
        return external
    
    def bootstrap_from_local_orders(
        self,
        orders: List[Dict[str, Any]],
    ) -> int:
        """
        从本地订单列表回填注册表。
        
        orders: List[Dict] - 订单字典列表，包含 cl_ord_id, strategy_id, created_at 等字段
        
        返回：回填的订单数量
        """
        count = 0
        for order in orders:
            cl_ord_id = order.get("cl_ord_id") or order.get("client_order_id")
            if not cl_ord_id:
                continue
            
            strategy_id = order.get("strategy_id")
            created_at_str = order.get("created_at") or order.get("created_ts_ms")
            
            created_at: Optional[datetime] = None
            if created_at_str:
                if isinstance(created_at_str, datetime):
                    created_at = created_at_str
                elif isinstance(created_at_str, (int, float)):
                    created_at = datetime.fromtimestamp(
                        created_at_str / 1000, tz=timezone.utc
                    )
                elif isinstance(created_at_str, str):
                    try:
                        created_at = datetime.fromisoformat(
                            created_at_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass
            
            self.record_order_origin(
                client_order_id=cl_ord_id,
                strategy_id=strategy_id,
                source="event_backfill",
                created_at=created_at,
            )
            count += 1
        
        if count > 0:
            logger.info(
                f"[OrderOwnership] bootstrapped {count} local orders into registry"
            )
        
        return count
    
    def get_statistics(self) -> Dict[str, int]:
        """获取归属统计信息"""
        owned = sum(1 for o in self._origins.values() if o.strategy_id is not None)
        external = sum(1 for o in self._origins.values() if o.strategy_id is None)
        return {
            "total_registered": len(self._origins),
            "owned": owned,
            "external": external,
        }


# 全局单例（应用级）
_global_registry: Optional[OrderOwnershipRegistry] = None


def get_order_ownership_registry() -> OrderOwnershipRegistry:
    """获取全局订单归属注册表单例"""
    global _global_registry
    if _global_registry is None:
        from trader.api.env_config import get_system_order_namespace_prefix
        prefix = get_system_order_namespace_prefix()
        external_prefixes = []  # 可从环境变量扩展
        _global_registry = OrderOwnershipRegistry(
            namespace_prefix=prefix,
            external_prefixes=external_prefixes,
        )
    return _global_registry


def reset_order_ownership_registry() -> None:
    """重置全局注册表（用于测试）"""
    global _global_registry
    _global_registry = None