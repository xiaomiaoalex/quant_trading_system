"""
Position Projector - 持仓投影
==============================
将持仓相关事件投影为 PostgreSQL 读模型。

事件类型：
- POSITION_OPENED: 开仓
- POSITION_INCREASED: 加仓
- POSITION_DECREASED: 减仓
- POSITION_CLOSED: 平仓
- POSITION_UPDATED: 持仓更新

投影表结构：
- aggregate_id: 持仓 ID (position_id)
- state: JSONB 存储完整持仓状态
- version: 版本号（乐观锁）
- last_event_seq: 最后处理的事件序列号
- updated_at: 更新时间

索引：
- 主键: aggregate_id
- symbol 索引（用于按标的查询）
- updated_at 索引（用于时间排序）
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Any, Optional

from trader.adapters.persistence.postgres.projectors.base import (
    Projectable,
    ProjectorSnapshot,
)
from trader.core.domain.models.events import EventType


# ==================== 数据类型 ====================

@dataclass
class PositionProjection:
    """
    持仓投影数据类
    
    用于类型化的投影结果访问。
    """
    position_id: str
    symbol: str
    quantity: Decimal
    avg_price: Decimal
    current_price: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    market_value: Decimal
    cost_basis: Decimal
    is_long: bool
    is_empty: bool
    opened_at: Optional[datetime]
    updated_at: datetime
    version: int
    last_event_seq: int
    
    @classmethod
    def from_state(cls, position_id: str, state: Dict[str, Any]) -> "PositionProjection":
        """从状态字典创建 PositionProjection"""
        return cls(
            position_id=position_id,
            symbol=state.get("symbol", ""),
            quantity=Decimal(str(state.get("quantity", "0"))),
            avg_price=Decimal(str(state.get("avg_price", "0"))),
            current_price=Decimal(str(state.get("current_price", "0"))),
            realized_pnl=Decimal(str(state.get("realized_pnl", "0"))),
            unrealized_pnl=Decimal(str(state.get("unrealized_pnl", "0"))),
            market_value=Decimal(str(state.get("market_value", "0"))),
            cost_basis=Decimal(str(state.get("cost_basis", "0"))),
            is_long=state.get("is_long", True),
            is_empty=state.get("is_empty", True),
            opened_at=state.get("opened_at"),
            updated_at=state.get("updated_at", datetime.now(timezone.utc)),
            version=state.get("_version", 1),
            last_event_seq=state.get("_last_event_seq", 0),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "quantity": str(self.quantity),
            "avg_price": str(self.avg_price),
            "current_price": str(self.current_price),
            "realized_pnl": str(self.realized_pnl),
            "unrealized_pnl": str(self.unrealized_pnl),
            "market_value": str(self.market_value),
            "cost_basis": str(self.cost_basis),
            "is_long": self.is_long,
            "is_empty": self.is_empty,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else self.updated_at,
            "version": self.version,
            "last_event_seq": self.last_event_seq,
        }


# ==================== PositionProjector ====================

class PositionProjector(Projectable):
    """
    持仓投影
    
    将持仓事件投影为可查询的读模型。
    """
    
    # 该投影处理的事件类型
    EVENT_TYPES = {
        EventType.POSITION_OPENED.value,
        EventType.POSITION_INCREASED.value,
        EventType.POSITION_DECREASED.value,
        EventType.POSITION_CLOSED.value,
        EventType.POSITION_UPDATED.value,
    }
    
    def __init__(self, pool: "asyncpg.Pool"):
        super().__init__(
            pool=pool,
            table_name="positions_proj",
            snapshot_table_name="positions_snapshots",
            event_types=[et.value for et in self.EVENT_TYPES],
        )
    
    def get_projection_id_field(self) -> str:
        """主键字段名"""
        return "aggregate_id"
    
    def extract_aggregate_id(self, event: "StreamEvent") -> str:
        """从事件中提取持仓 ID"""
        # 持仓事件使用 aggregate_id 作为 position_id
        return event.aggregate_id
    
    def _init_position_state(self) -> Dict[str, Any]:
        """初始化持仓状态"""
        return {
            "position_id": "",
            "symbol": "",
            "quantity": "0",
            "avg_price": "0",
            "current_price": "0",
            "realized_pnl": "0",
            "unrealized_pnl": "0",
            "market_value": "0",
            "cost_basis": "0",
            "is_long": True,
            "is_empty": True,
            "opened_at": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    
    def _apply_position_opened(self, state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """应用开仓事件"""
        state["position_id"] = data.get("position_id", "")
        state["symbol"] = data.get("symbol", "")
        state["quantity"] = str(data.get("quantity", "0"))
        state["avg_price"] = str(data.get("avg_price", "0"))
        state["current_price"] = str(data.get("current_price", state["avg_price"]))
        state["realized_pnl"] = "0"
        state["unrealized_pnl"] = "0"
        state["opened_at"] = data.get("opened_at") or datetime.now(timezone.utc).isoformat()
        state["is_long"] = Decimal(state["quantity"]) > 0
        state["is_empty"] = Decimal(state["quantity"]) == 0
        
        # 计算市值和成本
        qty = Decimal(state["quantity"])
        cur = Decimal(state["current_price"])
        avg = Decimal(state["avg_price"])
        state["market_value"] = str(qty * cur)
        state["cost_basis"] = str(qty * avg)
        
        return state
    
    def _apply_position_increased(self, state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """应用加仓事件"""
        add_qty = Decimal(str(data.get("quantity", "0")))
        add_price = Decimal(str(data.get("avg_price", "0")))
        
        current_qty = Decimal(state["quantity"])
        current_avg = Decimal(state["avg_price"])
        
        # 计算新的加权平均价
        if current_qty + add_qty > 0:
            total_cost = (current_qty * current_avg) + (add_qty * add_price)
            new_avg = total_cost / (current_qty + add_qty)
        else:
            new_avg = Decimal("0")
        
        state["quantity"] = str(current_qty + add_qty)
        state["avg_price"] = str(new_avg)
        
        # 更新市值和成本
        cur = Decimal(state["current_price"])
        state["market_value"] = str(Decimal(state["quantity"]) * cur)
        state["cost_basis"] = str(Decimal(state["quantity"]) * Decimal(state["avg_price"]))
        
        state["is_long"] = Decimal(state["quantity"]) > 0
        state["is_empty"] = Decimal(state["quantity"]) == 0
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        return state
    
    def _apply_position_decreased(self, state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """应用减仓事件"""
        reduce_qty = Decimal(str(data.get("quantity", "0")))
        reduce_price = Decimal(str(data.get("price", "0")))
        
        current_qty = Decimal(state["quantity"])
        current_avg = Decimal(state["avg_price"])
        
        # 限制减仓数量
        actual_reduce = min(reduce_qty, current_qty)
        
        # 计算实现的盈亏
        cost = actual_reduce * current_avg
        proceeds = actual_reduce * reduce_price
        realized = proceeds - cost
        
        # 更新已实现盈亏
        current_realized = Decimal(state["realized_pnl"])
        state["realized_pnl"] = str(current_realized + realized)
        
        # 更新数量
        state["quantity"] = str(current_qty - actual_reduce)
        
        # 更新市值和成本
        cur = Decimal(state["current_price"])
        state["market_value"] = str(Decimal(state["quantity"]) * cur)
        state["cost_basis"] = str(Decimal(state["quantity"]) * Decimal(state["avg_price"]))
        
        state["is_long"] = Decimal(state["quantity"]) > 0
        state["is_empty"] = Decimal(state["quantity"]) == 0
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        return state
    
    def _apply_position_closed(self, state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """应用平仓事件"""
        close_price = Decimal(str(data.get("price", state["current_price"])))
        
        current_qty = Decimal(state["quantity"])
        current_avg = Decimal(state["avg_price"])
        
        # 计算实现的盈亏
        cost = current_qty * current_avg
        proceeds = current_qty * close_price
        realized = proceeds - cost
        
        # 更新已实现盈亏
        current_realized = Decimal(state["realized_pnl"])
        state["realized_pnl"] = str(current_realized + realized)
        
        # 清空持仓
        state["quantity"] = "0"
        state["avg_price"] = "0"
        state["current_price"] = str(close_price)
        state["market_value"] = "0"
        state["cost_basis"] = "0"
        state["unrealized_pnl"] = "0"
        state["is_long"] = False
        state["is_empty"] = True
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        return state
    
    def _apply_position_updated(self, state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """应用持仓更新事件"""
        if "quantity" in data:
            state["quantity"] = str(data["quantity"])
        if "avg_price" in data:
            state["avg_price"] = str(data["avg_price"])
        if "current_price" in data:
            state["current_price"] = str(data["current_price"])
        if "realized_pnl" in data:
            state["realized_pnl"] = str(data["realized_pnl"])
        if "unrealized_pnl" in data:
            state["unrealized_pnl"] = str(data["unrealized_pnl"])
        if "opened_at" in data:
            state["opened_at"] = data["opened_at"]
        
        # 重新计算衍生字段
        qty = Decimal(state["quantity"])
        cur = Decimal(state["current_price"])
        avg = Decimal(state["avg_price"])
        state["market_value"] = str(qty * cur)
        state["cost_basis"] = str(qty * avg)
        state["is_long"] = qty > 0
        state["is_empty"] = qty == 0
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        return state
    
    def compute_projection(
        self,
        aggregate_id: str,
        events: List["StreamEvent"],
    ) -> Dict[str, Any]:
        """
        计算持仓投影
        
        通过重放事件流来计算当前持仓状态。
        
        Args:
            aggregate_id: 持仓 ID
            events: 按时间顺序排列的事件列表
            
        Returns:
            持仓投影状态
        """
        state = self._init_position_state()
        state["position_id"] = aggregate_id
        
        for event in events:
            data = event.data if isinstance(event.data, dict) else {}
            
            if event.event_type == EventType.POSITION_OPENED.value:
                state = self._apply_position_opened(state, data)
            elif event.event_type == EventType.POSITION_INCREASED.value:
                state = self._apply_position_increased(state, data)
            elif event.event_type == EventType.POSITION_DECREASED.value:
                state = self._apply_position_decreased(state, data)
            elif event.event_type == EventType.POSITION_CLOSED.value:
                state = self._apply_position_closed(state, data)
            elif event.event_type == EventType.POSITION_UPDATED.value:
                state = self._apply_position_updated(state, data)
        
        return state
    
    async def get_position(
        self,
        position_id: str,
    ) -> Optional[PositionProjection]:
        """
        获取持仓投影
        
        Args:
            position_id: 持仓 ID
            
        Returns:
            PositionProjection 或 None
        """
        projection = await self.get_projection(position_id)
        if projection is None:
            return None
        
        state = projection["state"]
        return PositionProjection.from_state(position_id, state)
    
    async def list_positions(
        self,
        symbol: Optional[str] = None,
        is_long: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[PositionProjection]:
        """
        列出持仓投影
        
        支持按标的和方向过滤。
        
        Args:
            symbol: 按标的过滤
            is_long: 按多空过滤
            limit: 最大返回数量
            offset: 偏移量
            
        Returns:
            PositionProjection 列表
        """
        # 首先获取所有投影
        projections = await self.list_projections(limit=limit * 2, offset=offset)
        
        results = []
        for proj in projections:
            pos = PositionProjection.from_state(proj["aggregate_id"], proj["state"])
            
            # 应用过滤器
            if symbol and pos.symbol != symbol:
                continue
            if is_long is not None and pos.is_long != is_long:
                continue
            
            results.append(pos)
            
            if len(results) >= limit:
                break
        
        return results
    
    async def get_positions_summary(self) -> Dict[str, Any]:
        """
        获取持仓汇总
        
        Returns:
            持仓汇总信息
        """
        projections = await self.list_projections(limit=10000)
        
        total_realized_pnl = Decimal("0")
        total_unrealized_pnl = Decimal("0")
        total_market_value = Decimal("0")
        position_count = 0
        non_empty_positions = []
        
        for proj in projections:
            pos = PositionProjection.from_state(proj["aggregate_id"], proj["state"])
            if not pos.is_empty:
                position_count += 1
                non_empty_positions.append(pos)
                total_realized_pnl += pos.realized_pnl
                total_unrealized_pnl += pos.unrealized_pnl
                total_market_value += pos.market_value
        
        return {
            "total_positions": position_count,
            "total_realized_pnl": str(total_realized_pnl),
            "total_unrealized_pnl": str(total_unrealized_pnl),
            "total_market_value": str(total_market_value),
            "total_pnl": str(total_realized_pnl + total_unrealized_pnl),
            "positions": [p.to_dict() for p in non_empty_positions],
        }


# 类型注解循环依赖处理
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trader.adapters.persistence.postgres.event_store import StreamEvent
