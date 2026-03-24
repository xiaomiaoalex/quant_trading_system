"""
Order Projector - 订单投影
==========================
将订单相关事件投影为 PostgreSQL 读模型。

事件类型：
- ORDER_CREATED: 订单创建
- ORDER_SUBMITTED: 订单提交
- ORDER_PARTIALLY_FILLED: 部分成交
- ORDER_FILLED: 完全成交
- ORDER_CANCELLED: 订单撤销
- ORDER_REJECTED: 订单拒绝

投影表结构：
- aggregate_id: 订单 ID (order_id)
- state: JSONB 存储完整订单状态
- version: 版本号（乐观锁）
- last_event_seq: 最后处理的事件序列号
- updated_at: 更新时间

索引：
- 主键: aggregate_id
- client_order_id 索引（用于幂等查询）
- status 索引（用于按状态查询）
- symbol 索引（用于按标的查询）
- created_at 索引（用于时间排序）
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Any, Optional

from trader.adapters.persistence.postgres.projectors.base import (
    Projectable,
)
from trader.core.domain.models.events import EventType


# ==================== 数据类型 ====================

@dataclass
class OrderProjection:
    """
    订单投影数据类
    
    用于类型化的投影结果访问。
    """
    order_id: str
    client_order_id: str
    broker_order_id: Optional[str]
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    price: Optional[Decimal]
    filled_quantity: Decimal
    average_price: Decimal
    status: str
    strategy_name: str
    stop_loss: Optional[Decimal]
    take_profit: Optional[Decimal]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime
    submitted_at: Optional[datetime]
    filled_at: Optional[datetime]
    version: int
    last_event_seq: int
    
    @classmethod
    def from_state(cls, order_id: str, state: Dict[str, Any]) -> "OrderProjection":
        """从状态字典创建 OrderProjection"""
        created_at = state.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        
        updated_at = state.get("updated_at")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)
        elif updated_at is None:
            updated_at = datetime.now(timezone.utc)
        
        submitted_at = state.get("submitted_at")
        if isinstance(submitted_at, str):
            submitted_at = datetime.fromisoformat(submitted_at)
        
        filled_at = state.get("filled_at")
        if isinstance(filled_at, str):
            filled_at = datetime.fromisoformat(filled_at)
        
        return cls(
            order_id=order_id,
            client_order_id=state.get("client_order_id", ""),
            broker_order_id=state.get("broker_order_id"),
            symbol=state.get("symbol", ""),
            side=state.get("side", "BUY"),
            order_type=state.get("order_type", "MARKET"),
            quantity=Decimal(str(state.get("quantity", "0"))),
            price=Decimal(str(state["price"])) if state.get("price") else None,
            filled_quantity=Decimal(str(state.get("filled_quantity", "0"))),
            average_price=Decimal(str(state.get("average_price", "0"))),
            status=state.get("status", "PENDING"),
            strategy_name=state.get("strategy_name", ""),
            stop_loss=Decimal(str(state["stop_loss"])) if state.get("stop_loss") else None,
            take_profit=Decimal(str(state["take_profit"])) if state.get("take_profit") else None,
            error_message=state.get("error_message"),
            created_at=created_at or datetime.now(timezone.utc),
            updated_at=updated_at,
            submitted_at=submitted_at,
            filled_at=filled_at,
            version=state.get("_version", 1),
            last_event_seq=state.get("_last_event_seq", 0),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "broker_order_id": self.broker_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "quantity": str(self.quantity),
            "price": str(self.price) if self.price else None,
            "filled_quantity": str(self.filled_quantity),
            "average_price": str(self.average_price),
            "status": self.status,
            "strategy_name": self.strategy_name,
            "stop_loss": str(self.stop_loss) if self.stop_loss else None,
            "take_profit": str(self.take_profit) if self.take_profit else None,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
            "updated_at": self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else self.updated_at,
            "submitted_at": self.submitted_at.isoformat() if isinstance(self.submitted_at, datetime) else self.submitted_at,
            "filled_at": self.filled_at.isoformat() if isinstance(self.filled_at, datetime) else self.filled_at,
            "version": self.version,
            "last_event_seq": self.last_event_seq,
        }
    
    @property
    def remaining_quantity(self) -> Decimal:
        """剩余未成交数量"""
        return self.quantity - self.filled_quantity
    
    @property
    def is_terminal(self) -> bool:
        """是否为终态"""
        return self.status in ("FILLED", "CANCELLED", "REJECTED")
    
    @property
    def order_value(self) -> Decimal:
        """订单名义金额"""
        price = self.average_price if self.average_price > 0 else (self.price or Decimal("0"))
        return self.filled_quantity * price


# ==================== OrderProjector ====================

class OrderProjector(Projectable):
    """
    订单投影
    
    将订单事件投影为可查询的读模型。
    """
    
    # 该投影处理的事件类型
    EVENT_TYPES = {
        EventType.ORDER_CREATED.value,
        EventType.ORDER_SUBMITTED.value,
        EventType.ORDER_PARTIALLY_FILLED.value,
        EventType.ORDER_FILLED.value,
        EventType.ORDER_CANCELLED.value,
        EventType.ORDER_REJECTED.value,
    }
    
    def __init__(self, pool: "asyncpg.Pool"):
        super().__init__(
            pool=pool,
            table_name="orders_proj",
            snapshot_table_name="orders_snapshots",
            event_types=[et.value for et in self.EVENT_TYPES],
        )
    
    def get_projection_id_field(self) -> str:
        """主键字段名"""
        return "aggregate_id"
    
    def extract_aggregate_id(self, event: "StreamEvent") -> str:
        """从事件中提取订单 ID"""
        return event.aggregate_id
    
    def _init_order_state(self) -> Dict[str, Any]:
        """初始化订单状态"""
        now = datetime.now(timezone.utc)
        return {
            "order_id": "",
            "client_order_id": "",
            "broker_order_id": None,
            "symbol": "",
            "side": "BUY",
            "order_type": "MARKET",
            "time_in_force": "GTC",
            "quantity": "0",
            "price": None,
            "filled_quantity": "0",
            "average_price": "0",
            "status": "PENDING",
            "strategy_name": "",
            "stop_loss": None,
            "take_profit": None,
            "error_message": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "submitted_at": None,
            "filled_at": None,
        }
    
    def _apply_order_created(self, state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """应用订单创建事件"""
        state["order_id"] = data.get("order_id", "")
        state["client_order_id"] = data.get("client_order_id", "")
        state["symbol"] = data.get("symbol", "")
        state["side"] = data.get("side", "BUY")
        state["order_type"] = data.get("order_type", "MARKET")
        state["time_in_force"] = data.get("time_in_force", "GTC")
        state["quantity"] = str(data.get("quantity", "0"))
        state["price"] = str(data["price"]) if data.get("price") else None
        state["strategy_name"] = data.get("strategy_name", "")
        state["stop_loss"] = str(data["stop_loss"]) if data.get("stop_loss") else None
        state["take_profit"] = str(data["take_profit"]) if data.get("take_profit") else None
        state["status"] = "PENDING"
        state["filled_quantity"] = "0"
        state["average_price"] = "0"
        state["created_at"] = data.get("created_at") or datetime.now(timezone.utc).isoformat()
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        return state
    
    def _apply_order_submitted(self, state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """应用订单提交事件"""
        state["status"] = "SUBMITTED"
        if data.get("broker_order_id"):
            state["broker_order_id"] = data["broker_order_id"]
        state["submitted_at"] = data.get("submitted_at") or datetime.now(timezone.utc).isoformat()
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        return state
    
    def _apply_order_partially_filled(self, state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """应用部分成交事件"""
        fill_qty = Decimal(str(data.get("filled_quantity", "0")))
        fill_price = Decimal(str(data.get("average_price", "0")))
        
        current_filled = Decimal(state["filled_quantity"])
        current_avg = Decimal(state["average_price"])
        
        # 计算新的加权平均价格
        if current_filled + fill_qty > 0:
            total_value = (current_avg * current_filled) + (fill_price * fill_qty)
            new_avg = total_value / (current_filled + fill_qty)
        else:
            new_avg = fill_price
        
        state["filled_quantity"] = str(current_filled + fill_qty)
        state["average_price"] = str(new_avg)
        state["status"] = "PARTIALLY_FILLED"
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        return state
    
    def _apply_order_filled(self, state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """应用完全成交事件"""
        fill_qty = Decimal(str(data.get("filled_quantity", state["quantity"])))
        fill_price = Decimal(str(data.get("average_price", "0")))
        
        state["filled_quantity"] = str(fill_qty)
        state["average_price"] = str(fill_price)
        state["status"] = "FILLED"
        state["filled_at"] = data.get("filled_at") or datetime.now(timezone.utc).isoformat()
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        return state
    
    def _apply_order_cancelled(self, state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """应用订单撤销事件"""
        state["status"] = "CANCELLED"
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        return state
    
    def _apply_order_rejected(self, state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """应用订单拒绝事件"""
        state["status"] = "REJECTED"
        state["error_message"] = data.get("reason", data.get("error_message", "Unknown"))
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        return state
    
    def compute_projection(
        self,
        aggregate_id: str,
        events: List["StreamEvent"],
    ) -> Dict[str, Any]:
        """
        计算订单投影
        
        通过重放事件流来计算当前订单状态。
        
        Args:
            aggregate_id: 订单 ID
            events: 按时间顺序排列的事件列表
            
        Returns:
            订单投影状态
        """
        state = self._init_order_state()
        state["order_id"] = aggregate_id
        
        for event in events:
            data = event.data if isinstance(event.data, dict) else {}
            
            if event.event_type == EventType.ORDER_CREATED.value:
                state = self._apply_order_created(state, data)
            elif event.event_type == EventType.ORDER_SUBMITTED.value:
                state = self._apply_order_submitted(state, data)
            elif event.event_type == EventType.ORDER_PARTIALLY_FILLED.value:
                state = self._apply_order_partially_filled(state, data)
            elif event.event_type == EventType.ORDER_FILLED.value:
                state = self._apply_order_filled(state, data)
            elif event.event_type == EventType.ORDER_CANCELLED.value:
                state = self._apply_order_cancelled(state, data)
            elif event.event_type == EventType.ORDER_REJECTED.value:
                state = self._apply_order_rejected(state, data)
        
        return state
    
    async def get_order(
        self,
        order_id: str,
    ) -> Optional[OrderProjection]:
        """
        获取订单投影
        
        Args:
            order_id: 订单 ID
            
        Returns:
            OrderProjection 或 None
        """
        projection = await self.get_projection(order_id)
        if projection is None:
            return None
        
        state = projection["state"]
        return OrderProjection.from_state(order_id, state)
    
    async def get_order_by_client_id(
        self,
        client_order_id: str,
    ) -> Optional[OrderProjection]:
        """
        通过客户端订单 ID 获取订单
        
        需要扫描查找，但通过索引可以加速。
        
        Args:
            client_order_id: 客户端订单 ID
            
        Returns:
            OrderProjection 或 None
        """
        # 扫描查找（需要索引优化）
        projections = await self.list_projections(limit=1000)
        for proj in projections:
            state = proj["state"]
            if state.get("client_order_id") == client_order_id:
                return OrderProjection.from_state(proj["aggregate_id"], state)
        return None
    
    async def list_orders(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        side: Optional[str] = None,
        strategy_name: Optional[str] = None,
        is_terminal: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[OrderProjection]:
        """
        列出订单投影
        
        支持多种过滤条件。
        
        Args:
            symbol: 按标的过滤
            status: 按状态过滤
            side: 按方向过滤
            strategy_name: 按策略名过滤
            is_terminal: 按是否终态过滤
            limit: 最大返回数量
            offset: 偏移量
            
        Returns:
            OrderProjection 列表
        """
        projections = await self.list_projections(limit=limit * 2, offset=offset)
        
        results = []
        for proj in projections:
            order = OrderProjection.from_state(proj["aggregate_id"], proj["state"])
            
            # 应用过滤器
            if symbol and order.symbol != symbol:
                continue
            if status and order.status != status:
                continue
            if side and order.side != side:
                continue
            if strategy_name and order.strategy_name != strategy_name:
                continue
            if is_terminal is not None and order.is_terminal != is_terminal:
                continue
            
            results.append(order)
            
            if len(results) >= limit:
                break
        
        return results
    
    async def get_orders_summary(self) -> Dict[str, Any]:
        """
        获取订单汇总
        
        Returns:
            订单汇总信息
        """
        projections = await self.list_projections(limit=10000)
        
        by_status: Dict[str, int] = {}
        total_order_value = Decimal("0")
        filled_order_value = Decimal("0")
        order_count = 0
        
        for proj in projections:
            order = OrderProjection.from_state(proj["aggregate_id"], proj["state"])
            order_count += 1
            
            # 按状态统计
            status = order.status
            by_status[status] = by_status.get(status, 0) + 1
            
            # 计算金额
            total_order_value += order.quantity * (order.price or Decimal("0"))
            filled_order_value += order.filled_quantity * order.average_price
        
        return {
            "total_orders": order_count,
            "by_status": by_status,
            "total_order_value": str(total_order_value),
            "filled_order_value": str(filled_order_value),
            "fill_rate": str(filled_order_value / total_order_value) if total_order_value > 0 else "0",
        }


# 类型注解循环依赖处理
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trader.adapters.persistence.postgres.event_store import StreamEvent
