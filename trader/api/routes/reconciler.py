"""
Reconciler API Routes
=====================
Order reconciliation endpoints.
"""
import logging
from functools import lru_cache
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Query, HTTPException, Depends
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from trader.core.application.reconciler import (
    Reconciler,
    ReconcileReport,
    OrderDrift,
    DriftType,
    LocalOrderSnapshot,
    ExchangeOrderSnapshot,
)
from trader.services.reconciler_service import ReconcilerService
from trader.services.order import OrderService
from trader.api.models.schemas import OrderView

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
    """对账请求（可选 - 不传则后端自动拉取）"""
    local_orders: Optional[List[Dict[str, Any]]] = None
    exchange_orders: Optional[List[Dict[str, Any]]] = None


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


async def _fetch_exchange_orders() -> List[ExchangeOrderSnapshot]:
    """
    从交易所 adapter 获取当前交易所订单快照。
    
    使用 BinanceSpotDemoBroker 从真实交易所获取未结订单。
    配置从环境变量读取: BINANCE_API_KEY, BINANCE_SECRET_KEY
    """
    try:
        import os
        from trader.adapters.broker.binance_spot_demo_broker import (
            BinanceSpotDemoBroker,
            BinanceSpotDemoBrokerConfig,
        )
        
        api_key = os.environ.get("BINANCE_API_KEY")
        secret_key = os.environ.get("BINANCE_SECRET_KEY")
        
        if not api_key or not secret_key:
            logger.warning(
                "EXCHANGE_ORDERS_CONFIG_MISSING",
                extra={"message": "BINANCE_API_KEY or BINANCE_SECRET_KEY not set, returning empty list"}
            )
            return []
        
        # 创建真实的 Binance broker
        config = BinanceSpotDemoBrokerConfig.create(api_key, secret_key)
        broker = BinanceSpotDemoBroker(config)
        
        # 连接并获取开放订单
        await broker.connect()
        try:
            broker_orders = await broker.get_open_orders()
            
            exchange_orders: List[ExchangeOrderSnapshot] = [
                ExchangeOrderSnapshot(
                    cl_ord_id=order.client_order_id,
                    status=order.status.value,
                    symbol=order.symbol,
                    quantity=str(order.quantity),
                    filled_quantity=str(order.filled_quantity),
                    updated_at=order.created_at,
                )
                for order in broker_orders
            ]
            
            logger.info(
                "EXCHANGE_ORDERS_FETCHED",
                extra={"exchange_orders_count": len(exchange_orders)}
            )
            
            return exchange_orders
        finally:
            await broker.disconnect()
        
    except Exception as e:
        # Fail-closed: 获取失败时记录日志并返回空列表
        logger.error(
            "EXCHANGE_ORDERS_FETCH_FAILED",
            extra={"error": str(e), "error_type": type(e).__name__}
        )
        return []


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
    request: Optional[TriggerReconcileRequest] = None,
    reconciler: Reconciler = Depends(get_reconciler),
    service: ReconcilerService = Depends(get_reconciler_service),
):
    """
    Trigger a reconciliation check (Task 9.3 - 无参触发模式)。
    
    支持两种模式：
    1. 无参模式（request=None）：后端自动拉取本地与交易所订单快照
    2. 带参模式：前端提交 local_orders + exchange_orders（用于测试）
    """
    # 无参触发模式：后端自动聚合数据
    if request is None or (request.local_orders is None and request.exchange_orders is None):
        order_svc = OrderService()
        
        # 从 OrderService 拉取本地订单（OMS 订单）
        local_orders_raw: List[OrderView] = order_svc.list_orders(limit=10000)
        local_orders = [
            LocalOrderSnapshot(
                cl_ord_id=o.cl_ord_id,
                status=o.status,
                symbol=o.instrument,
                quantity=o.qty,
                filled_quantity=o.filled_qty,
                created_at=datetime.fromtimestamp(o.created_ts_ms / 1000, tz=timezone.utc) if o.created_ts_ms else datetime.now(timezone.utc),
                updated_at=datetime.fromtimestamp(o.updated_ts_ms / 1000, tz=timezone.utc) if o.updated_ts_ms else datetime.now(timezone.utc),
            )
            for o in local_orders_raw
        ]
        
        # 从交易所 adapter 拉取订单（真实交易所状态）
        # 注意：这需要 LiveExchangeAdapter 支持，目前使用模拟方式
        exchange_orders: List[ExchangeOrderSnapshot] = await _fetch_exchange_orders()
        
        logger.info(
            "RECONCILER_AUTO_TRIGGER",
            extra={"local_orders_count": len(local_orders), "exchange_orders_count": len(exchange_orders)}
        )
    else:
        # 带参模式：使用前端提交的数据
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
            for o in (request.local_orders or [])
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
            for o in (request.exchange_orders or [])
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
