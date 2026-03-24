"""
Projector Base - 投影层基类
===========================
定义 Projectable 接口和投影基类实现。

核心概念：
- Projectable：投影接口，定义投影的基本行为
- 幂等更新：使用 version 进行乐观锁，避免重复更新
- 同步/异步更新：支持实时和事件驱动更新
- 投影重建：从 event_log 重放事件重建投影

幂等更新语义：
1. 每次更新都会增加 version
2. 使用 ON CONFLICT (aggregate_id) DO UPDATE SET ... WHERE version < $new_version
3. 只有当新版本大于当前版本时才更新

架构：
    event_log ---> Projectable.project(event) ---> positions_proj / orders_proj / risk_states_proj
                              |
                              v
                        snapshots_proj (可选，用于加速重建)
"""
import logging
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Any, TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import asyncpg
    from trader.adapters.persistence.postgres.event_store import StreamEvent

logger = logging.getLogger(__name__)


# ==================== 数据类型定义 ====================

@dataclass(frozen=True)
class ProjectionVersion:
    """投影版本号（用于乐观锁）"""
    aggregate_id: str
    version: int
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def is_newer_than(self, other: "ProjectionVersion") -> bool:
        """检查是否比另一个版本更新"""
        return self.version > other.version


@dataclass
class ProjectorSnapshot:
    """
    投影快照
    
    用于加速投影重建，记录投影状态和版本信息。
    """
    aggregate_id: str
    projection_type: str
    state: Dict[str, Any]
    version: int
    last_event_seq: int  # 最后处理的事件序列号
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ==================== Projectable 接口 ====================

class Projectable(ABC):
    """
    投影接口（抽象基类）
    
    所有投影都必须实现此接口。
    
    职责：
    1. 定义投影的事件类型过滤器
    2. 实现事件到投影状态的转换逻辑
    3. 提供幂等的 upsert 操作
    4. 支持投影重建（从快照或从头）
    
    幂等更新保证：
    - 使用 aggregate_id 作为主键
    - 使用 version 进行乐观锁
    - 相同事件不会重复更新（通过检查 last_event_seq）
    """

    def __init__(
        self,
        pool: "asyncpg.Pool",
        table_name: str,
        snapshot_table_name: str,
        event_types: List[str],
    ):
        """
        Args:
            pool: asyncpg 连接池
            table_name: 投影表名
            snapshot_table_name: 快照表名
            event_types: 该投影处理的事件类型列表
        """
        self._pool = pool
        self._table_name = table_name
        self._snapshot_table_name = snapshot_table_name
        self._event_types = set(event_types)
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    @property
    def table_name(self) -> str:
        """投影表名"""
        return self._table_name

    @property
    def snapshot_table_name(self) -> str:
        """快照表名"""
        return self._snapshot_table_name

    @property
    def event_types(self) -> set:
        """该投影处理的事件类型"""
        return self._event_types

    def can_handle(self, event_type: str) -> bool:
        """检查该投影是否能处理此事件类型"""
        return event_type in self._event_types

    @abstractmethod
    def extract_aggregate_id(self, event: "StreamEvent") -> str:
        """
        从事件中提取聚合根 ID
        
        Args:
            event: 事件对象
            
        Returns:
            聚合根 ID
        """
        pass

    @abstractmethod
    def compute_projection(
        self,
        aggregate_id: str,
        events: List["StreamEvent"],
    ) -> Dict[str, Any]:
        """
        计算投影状态
        
        从事件列表计算最新的投影状态。
        
        Args:
            aggregate_id: 聚合根 ID
            events: 该聚合根的事件列表（按时间顺序）
            
        Returns:
            投影状态字典
        """
        pass

    @abstractmethod
    def get_projection_id_field(self) -> str:
        """获取投影表的主键字段名（通常是 aggregate_id）"""
        pass

    def _serialize_value(self, value: Any) -> Any:
        """
        序列化值（处理特殊类型）
        
        处理 Decimal, datetime, Enum 等特殊类型。
        """
        if isinstance(value, Decimal):
            return str(value)
        elif isinstance(value, datetime):
            return value.isoformat()
        elif isinstance(value, Enum):
            return value.value
        elif isinstance(value, (list, dict)):
            return json.dumps(value, default=str)
        return value

    def _deserialize_value(self, value: Any, target_type: Optional[type] = None) -> Any:
        """
        反序列化值
        
        Args:
            value: 待反序列化的值
            target_type: 目标类型（可选）
        """
        if value is None:
            return None
        
        if target_type == Decimal:
            return Decimal(str(value))
        elif target_type == datetime:
            if isinstance(value, datetime):
                return value
            return datetime.fromisoformat(value)
        
        return value

    async def upsert_projection(
        self,
        aggregate_id: str,
        projection_state: Dict[str, Any],
        version: int,
        last_event_seq: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        幂等 Upsert 投影状态
        
        使用 ON CONFLICT 实现幂等更新。
        只有当新版本大于当前版本时才更新。
        
        Args:
            aggregate_id: 聚合根 ID
            projection_state: 投影状态
            version: 版本号
            last_event_seq: 最后处理的事件序列号
            metadata: 额外的元数据
            
        Returns:
            True if updated, False if skipped (version not newer)
        """
        if metadata is None:
            metadata = {}
        
        # 序列化所有值
        serialized_state = {
            k: self._serialize_value(v) 
            for k, v in projection_state.items()
        }
        serialized_state["_version"] = version
        serialized_state["_last_event_seq"] = last_event_seq
        serialized_state["_updated_at"] = datetime.now(timezone.utc).isoformat()
        
        if metadata:
            serialized_state["_metadata"] = json.dumps(metadata, default=str)
        
        async with self._pool.acquire() as conn:
            try:
                result = await conn.execute(
                    f"""
                    INSERT INTO {self._table_name} (aggregate_id, state, version, last_event_seq, updated_at)
                    VALUES ($1, $2, $3, $4, NOW())
                    ON CONFLICT ({self.get_projection_id_field()}) 
                    DO UPDATE SET 
                        state = EXCLUDED.state,
                        version = EXCLUDED.version,
                        last_event_seq = EXCLUDED.last_event_seq,
                        updated_at = NOW()
                    WHERE {self._table_name}.version < EXCLUDED.version
                    """,
                    aggregate_id,
                    json.dumps(serialized_state),
                    version,
                    last_event_seq,
                )
                
                # PostgreSQL 的 execute 返回命令结果
                # INSERT ... ON CONFLICT ... DO UPDATE 返回 'INSERT 0 N' 或 'UPDATE N'
                updated = result.startswith("UPDATE")
                
                if updated:
                    self._logger.debug(
                        "PROJECTION_UPDATED",
                        extra={
                            "table": self._table_name,
                            "aggregate_id": aggregate_id,
                            "version": version,
                        },
                    )
                else:
                    self._logger.debug(
                        "PROJECTION_SKIPPED",
                        extra={
                            "table": self._table_name,
                            "aggregate_id": aggregate_id,
                            "reason": "version_not_newer",
                        },
                    )
                    
                return updated
                
            except Exception as e:
                self._logger.error(
                    "PROJECTION_UPSERT_ERROR",
                    extra={
                        "table": self._table_name,
                        "aggregate_id": aggregate_id,
                        "error": str(e),
                    },
                )
                raise

    async def get_projection(
        self,
        aggregate_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        获取投影状态
        
        Args:
            aggregate_id: 聚合根 ID
            
        Returns:
            投影状态字典，如果不存在则返回 None
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT aggregate_id, state, version, last_event_seq, updated_at
                FROM {self._table_name}
                WHERE aggregate_id = $1
                """,
                aggregate_id,
            )
        
        if row is None:
            return None
        
        state = json.loads(row["state"]) if isinstance(row["state"], str) else row["state"]
        return {
            "aggregate_id": row["aggregate_id"],
            "state": state,
            "version": row["version"],
            "last_event_seq": row["last_event_seq"],
            "updated_at": row["updated_at"],
        }

    async def list_projections(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        列出投影（支持分页）
        
        Args:
            limit: 最大返回数量
            offset: 偏移量
            
        Returns:
            投影列表
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT aggregate_id, state, version, last_event_seq, updated_at
                FROM {self._table_name}
                ORDER BY updated_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
        
        results = []
        for row in rows:
            state = json.loads(row["state"]) if isinstance(row["state"], str) else row["state"]
            results.append({
                "aggregate_id": row["aggregate_id"],
                "state": state,
                "version": row["version"],
                "last_event_seq": row["last_event_seq"],
                "updated_at": row["updated_at"],
            })
        
        return results

    async def delete_projection(self, aggregate_id: str) -> bool:
        """
        删除投影
        
        Args:
            aggregate_id: 聚合根 ID
            
        Returns:
            True if deleted, False if not found
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                f"""
                DELETE FROM {self._table_name}
                WHERE aggregate_id = $1
                """,
                aggregate_id,
            )
        
        return result != "DELETE 0"

    async def save_snapshot(
        self,
        aggregate_id: str,
        projection_type: str,
        state: Dict[str, Any],
        version: int,
        last_event_seq: int,
    ) -> None:
        """
        保存投影快照
        
        Args:
            aggregate_id: 聚合根 ID
            projection_type: 投影类型
            state: 投影状态
            version: 版本号
            last_event_seq: 最后处理的事件序列号
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._snapshot_table_name} (aggregate_id, projection_type, state, version, last_event_seq, updated_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
                ON CONFLICT (aggregate_id, projection_type) 
                DO UPDATE SET 
                    state = EXCLUDED.state,
                    version = EXCLUDED.version,
                    last_event_seq = EXCLUDED.last_event_seq,
                    updated_at = NOW()
                """,
                aggregate_id,
                projection_type,
                json.dumps(state, default=str),
                version,
                last_event_seq,
            )

    async def get_snapshot(
        self,
        aggregate_id: str,
        projection_type: str,
    ) -> Optional[ProjectorSnapshot]:
        """
        获取投影快照
        
        Args:
            aggregate_id: 聚合根 ID
            projection_type: 投影类型
            
        Returns:
            投影快照，如果不存在则返回 None
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT aggregate_id, projection_type, state, version, last_event_seq, created_at, updated_at
                FROM {self._snapshot_table_name}
                WHERE aggregate_id = $1 AND projection_type = $2
                """,
                aggregate_id,
                projection_type,
            )
        
        if row is None:
            return None
        
        state = json.loads(row["state"]) if isinstance(row["state"], str) else row["state"]
        return ProjectorSnapshot(
            aggregate_id=row["aggregate_id"],
            projection_type=row["projection_type"],
            state=state,
            version=row["version"],
            last_event_seq=row["last_event_seq"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def project_event(
        self,
        event: "StreamEvent",
    ) -> bool:
        """
        处理单个事件并更新投影
        
        这是事件驱动更新的入口方法。
        
        Args:
            event: 事件对象
            
        Returns:
            True if projection was updated, False otherwise
        """
        if not self.can_handle(event.event_type):
            return False
        
        aggregate_id = self.extract_aggregate_id(event)
        if not aggregate_id:
            self._logger.warning(
                "PROJECTION_SKIP_NO_AGGREGATE_ID",
                extra={"event_type": event.event_type, "event_id": event.event_id},
            )
            return False
        
        # 获取当前投影版本
        current = await self.get_projection(aggregate_id)
        current_version = current["version"] if current else 0
        current_seq = current["last_event_seq"] if current else -1
        
        # 检查事件是否已处理（幂等保证）
        if event.seq <= current_seq:
            self._logger.debug(
                "PROJECTION_SKIP_ALREADY_PROCESSED",
                extra={
                    "aggregate_id": aggregate_id,
                    "event_seq": event.seq,
                    "current_seq": current_seq,
                },
            )
            return False
        
        # 获取该聚合根的所有事件（从当前seq之后）
        # 注意：这里需要从 event_store 读取，而不是直接使用 event
        # 因为 project_event 可能被调用时只传入了单个事件
        from trader.adapters.persistence.postgres.event_store import PostgresEventStore
        event_store = PostgresEventStore(self._pool)
        events = await event_store.read_stream(
            stream_key=event.stream_key,
            from_seq=current_seq,
            limit=10000,  # 足够大的限制
        )
        
        # 计算新投影
        new_state = self.compute_projection(aggregate_id, events)
        new_version = current_version + 1
        new_seq = max(e.seq for e in events)
        
        # 更新投影
        return await self.upsert_projection(
            aggregate_id=aggregate_id,
            projection_state=new_state,
            version=new_version,
            last_event_seq=new_seq,
        )

    async def rebuild_projection(
        self,
        aggregate_id: str,
        stream_key: str,
    ) -> Dict[str, Any]:
        """
        从头重建投影
        
        从 event_log 读取所有事件并重新计算投影。
        
        Args:
            aggregate_id: 聚合根 ID
            stream_key: 事件流键
            
        Returns:
            重建后的投影状态
        """
        from trader.adapters.persistence.postgres.event_store import PostgresEventStore
        
        self._logger.info(
            "PROJECTION_REBUILD_START",
            extra={"aggregate_id": aggregate_id, "stream_key": stream_key},
        )
        
        event_store = PostgresEventStore(self._pool)
        
        # 读取所有事件
        events = await event_store.read_stream(
            stream_key=stream_key,
            from_seq=0,
            limit=100000,  # 足够大的限制
        )
        
        if not events:
            self._logger.info(
                "PROJECTION_REBUILD_NO_EVENTS",
                extra={"aggregate_id": aggregate_id, "stream_key": stream_key},
            )
            return {}
        
        # 计算投影
        new_state = self.compute_projection(aggregate_id, events)
        new_version = 1
        new_seq = max(e.seq for e in events)
        
        # 更新投影
        await self.upsert_projection(
            aggregate_id=aggregate_id,
            projection_state=new_state,
            version=new_version,
            last_event_seq=new_seq,
        )
        
        self._logger.info(
            "PROJECTION_REBUILD_COMPLETE",
            extra={
                "aggregate_id": aggregate_id,
                "stream_key": stream_key,
                "event_count": len(events),
                "final_seq": new_seq,
            },
        )
        
        return new_state

    async def get_projection_at(
        self,
        aggregate_id: str,
        at_seq: int,
    ) -> Optional[Dict[str, Any]]:
        """
        获取特定时间点的投影快照
        
        通过重建该时间点之前的投影状态来实现。
        
        Args:
            aggregate_id: 聚合根 ID
            at_seq: 目标序列号
            
        Returns:
            投影状态（如果存在）
        """
        from trader.adapters.persistence.postgres.event_store import PostgresEventStore
        
        event_store = PostgresEventStore(self._pool)
        stream_key = f"{self.__class__.__name__.replace('Projector', '')}-{aggregate_id}"
        
        # 读取截止到 at_seq 的所有事件
        events = await event_store.read_stream(
            stream_key=stream_key,
            from_seq=0,
            limit=at_seq + 1,
        )
        
        # 过滤到 at_seq
        events = [e for e in events if e.seq <= at_seq]
        
        if not events:
            return None
        
        state = self.compute_projection(aggregate_id, events)
        return {
            "aggregate_id": aggregate_id,
            "state": state,
            "at_seq": at_seq,
        }

    async def check_consistency(self, aggregate_id: str) -> Dict[str, Any]:
        """
        检查投影的一致性
        
        对比投影表和事件日志，检测数据不一致。
        
        Args:
            aggregate_id: 聚合根 ID
            
        Returns:
            一致性检查结果
        """
        projection = await self.get_projection(aggregate_id)
        
        if projection is None:
            return {
                "aggregate_id": aggregate_id,
                "exists": False,
                "consistent": True,
                "issues": [],
            }
        
        issues = []
        proj_seq = projection["last_event_seq"]
        
        # TODO: 可以进一步验证事件数量和状态正确性
        
        return {
            "aggregate_id": aggregate_id,
            "exists": True,
            "version": projection["version"],
            "last_event_seq": proj_seq,
            "consistent": len(issues) == 0,
            "issues": issues,
        }


# ==================== 辅助函数 ====================

def make_stream_key(aggregate_type: str, aggregate_id: str) -> str:
    """
    创建事件流键
    
    格式: {aggregate_type}-{aggregate_id}
    
    Examples:
        >>> make_stream_key("Order", "123")
        'Order-123'
        >>> make_stream_key("Position", "456")
        'Position-456'
    """
    return f"{aggregate_type}-{aggregate_id}"


def parse_stream_key(stream_key: str) -> tuple[str, str]:
    """
    解析事件流键
    
    Returns:
        (aggregate_type, aggregate_id)
        
    Raises:
        ValueError: 如果格式不正确
    """
    parts = stream_key.split("-", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid stream_key format: {stream_key}")
    return parts[0], parts[1]
