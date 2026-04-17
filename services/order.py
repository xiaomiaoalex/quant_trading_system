from typing import List, Optional
from datetime import datetime, timezone

from trader.storage.in_memory import get_storage, InMemoryStorage
from trader.api.models.schemas import (
    OrderView, ExecutionView,
    ActionResult,
)


class OrderService:
    """Service for querying orders and executions"""

    def __init__(self, storage: Optional[InMemoryStorage] = None):
        self._storage = storage or get_storage()

    def get_order(self, cl_ord_id: str) -> Optional[OrderView]:
        """Get order by client order ID"""
        order = self._storage.get_order(cl_ord_id)
        if order:
            return OrderView(**order)
        return None

    def list_orders(
        self,
        account_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
        venue: Optional[str] = None,
        status: Optional[str] = None,
        instrument: Optional[str] = None,
        since_ts_ms: Optional[int] = None,
        limit: int = 200,
    ) -> List[OrderView]:
        """Query orders with filters"""
        orders = self._storage.list_orders(
            account_id, strategy_id, deployment_id, venue, status, instrument, since_ts_ms, limit
        )
        return [OrderView(**o) for o in orders]

    def cancel_order(self, cl_ord_id: str) -> ActionResult:
        """Cancel an order"""
        order = self._storage.get_order(cl_ord_id)
        if order:
            order["status"] = "CANCELLED"
            order["updated_ts_ms"] = int(datetime.now(timezone.utc).timestamp() * 1000)
            return ActionResult(ok=True, message=f"Order {cl_ord_id} cancellation requested")
        return ActionResult(ok=False, message=f"Order {cl_ord_id} not found")

    def list_executions(
        self,
        cl_ord_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
        since_ts_ms: Optional[int] = None,
        limit: int = 500,
    ) -> List[ExecutionView]:
        """Query executions"""
        executions = self._storage.list_executions(cl_ord_id, deployment_id, since_ts_ms, limit)
        return [ExecutionView(**e) for e in executions]
