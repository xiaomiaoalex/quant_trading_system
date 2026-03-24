"""
Reconciler API Routes
=====================
Order reconciliation endpoints.
"""
from functools import lru_cache
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Query, HTTPException, Depends
from pydantic import BaseModel

from trader.core.application.reconciler import (
    Reconciler,
    ReconcileReport,
    OrderDrift,
    DriftType,
    LocalOrderSnapshot,
    ExchangeOrderSnapshot,
)
from trader.services.reconciler_service import ReconcilerService

router = APIRouter(tags=["Reconciler"])


@lru_cache()
def get_reconciler_service() -> ReconcilerService:
    """Thread-safe singleton for ReconcilerService using lru_cache."""
    return ReconcilerService()


@lru_cache()
def get_reconciler() -> Reconciler:
    """Thread-safe singleton for Reconciler core using lru_cache."""
    return Reconciler()


class TriggerReconcileRequest(BaseModel):
    local_orders: List[Dict[str, Any]]
    exchange_orders: List[Dict[str, Any]]


class DriftResponse(BaseModel):
    cl_ord_id: str
    drift_type: str
    local_status: Optional[str]
    exchange_status: Optional[str]
    detected_at: str
    symbol: Optional[str]
    quantity: Optional[str]
    filled_quantity: Optional[str]
    exchange_filled_quantity: Optional[str]
    grace_period_remaining_sec: Optional[float]


class ReconcileReportResponse(BaseModel):
    timestamp: str
    total_orders_checked: int
    drifts: List[DriftResponse]
    ghost_count: int
    phantom_count: int
    diverged_count: int
    within_grace_period_count: int


def _drift_to_response(drift: OrderDrift) -> DriftResponse:
    return DriftResponse(
        cl_ord_id=drift.cl_ord_id,
        drift_type=drift.drift_type.value,
        local_status=drift.local_status,
        exchange_status=drift.exchange_status,
        detected_at=drift.detected_at.isoformat(),
        symbol=drift.symbol,
        quantity=drift.quantity,
        filled_quantity=drift.filled_quantity,
        exchange_filled_quantity=drift.exchange_filled_quantity,
        grace_period_remaining_sec=drift.grace_period_remaining_sec,
    )


def _report_to_response(report: ReconcileReport) -> ReconcileReportResponse:
    return ReconcileReportResponse(
        timestamp=report.timestamp.isoformat(),
        total_orders_checked=report.total_orders_checked,
        drifts=[_drift_to_response(d) for d in report.drifts],
        ghost_count=report.ghost_count,
        phantom_count=report.phantom_count,
        diverged_count=report.diverged_count,
        within_grace_period_count=report.within_grace_period_count,
    )


@router.get("/v1/reconciler/report", response_model=ReconcileReportResponse)
async def get_reconciler_report(
    service: ReconcilerService = Depends(get_reconciler_service),
):
    """
    Get latest reconciliation report.

    Returns the most recent reconciliation report if available.
    """
    last_report = service.get_last_report()
    if last_report is None:
        raise HTTPException(status_code=404, detail="No reconciliation report available")
    return _report_to_response(last_report)


@router.post("/v1/reconciler/trigger", response_model=ReconcileReportResponse)
async def trigger_reconciliation(
    request: TriggerReconcileRequest,
    reconciler: Reconciler = Depends(get_reconciler),
    service: ReconcilerService = Depends(get_reconciler_service),
):
    """
    Trigger a reconciliation check.

    Compares local orders with exchange orders and reports any drifts.
    """
    local_orders = [
        LocalOrderSnapshot(
            cl_ord_id=o["client_order_id"],
            status=o["status"],
            symbol=o.get("symbol", ""),
            quantity=str(o.get("quantity", "0")),
            filled_quantity=str(o.get("filled_quantity", "0")),
            created_at=_parse_datetime(o.get("created_at")),
            updated_at=_parse_datetime(o.get("updated_at")),
        )
        for o in request.local_orders
    ]

    exchange_orders = [
        ExchangeOrderSnapshot(
            cl_ord_id=o["client_order_id"],
            status=o["status"],
            symbol=o.get("symbol", ""),
            quantity=str(o.get("quantity", "0")),
            filled_quantity=str(o.get("filled_quantity", "0")),
            updated_at=_parse_datetime(o.get("updated_at")),
        )
        for o in request.exchange_orders
    ]

    report = reconciler.reconcile(local_orders, exchange_orders)
    service.set_last_report(report)
    return _report_to_response(report)


def _parse_datetime(value) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return datetime.now(timezone.utc)
    return datetime.now(timezone.utc)
