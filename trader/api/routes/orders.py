"""
Order API Routes
================
Order and execution query endpoints.
"""
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Path, Query

from trader.api.models.schemas import OrderView, ExecutionView, ActionResult
from trader.services import OrderService

router = APIRouter(tags=["Orders"])


@router.get("/v1/orders", response_model=List[OrderView])
async def list_orders(
    account_id: Optional[str] = Query(None, description="Filter by account ID"),
    strategy_id: Optional[str] = Query(None, description="Filter by strategy ID"),
    deployment_id: Optional[str] = Query(None, description="Filter by deployment ID"),
    venue: Optional[str] = Query(None, description="Filter by venue"),
    status: Optional[str] = Query(None, description="Filter by order status"),
    instrument: Optional[str] = Query(None, description="Filter by instrument"),
    since_ts_ms: Optional[int] = Query(None, description="Filter by timestamp (ms)"),
    limit: int = Query(200, description="Max results", le=2000),
):
    """
    Query orders.

    Returns a list of orders with optional filters.
    """
    service = OrderService()
    return service.list_orders(
        account_id, strategy_id, deployment_id, venue, status, instrument, since_ts_ms, limit
    )


@router.get("/v1/orders/{cl_ord_id}", response_model=OrderView)
async def get_order(cl_ord_id: str = Path(..., description="Client order ID")):
    """
    Get order by client order ID.

    Returns a specific order.
    """
    service = OrderService()
    order = service.get_order(cl_ord_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {cl_ord_id} not found")
    return order


@router.post("/v1/orders/{cl_ord_id}/cancel", response_model=ActionResult)
async def cancel_order(cl_ord_id: str = Path(..., description="Client order ID")):
    """
    Cancel order.

    Requests cancellation of an order.
    """
    service = OrderService()
    return service.cancel_order(cl_ord_id)


@router.get("/v1/executions", response_model=List[ExecutionView])
async def list_executions(
    cl_ord_id: Optional[str] = Query(None, description="Filter by client order ID"),
    deployment_id: Optional[str] = Query(None, description="Filter by deployment ID"),
    since_ts_ms: Optional[int] = Query(None, description="Filter by timestamp (ms)"),
    limit: int = Query(500, description="Max results", le=5000),
):
    """
    Query executions.

    Returns a list of executions with optional filters.
    """
    service = OrderService()
    return service.list_executions(cl_ord_id, deployment_id, since_ts_ms, limit)
