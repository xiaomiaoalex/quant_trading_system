import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Callable, Awaitable, Optional

from trader.core.application.reconciler import (
    Reconciler,
    ReconcileReport,
    OrderDrift,
    DriftType,
    LocalOrderSnapshot,
    ExchangeOrderSnapshot,
)
from trader.storage.in_memory import get_storage, InMemoryStorage

logger = logging.getLogger(__name__)


class ReconcilerService:
    DEFAULT_INTERVAL_SEC = 30.0

    def __init__(
        self,
        storage: Optional[InMemoryStorage] = None,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
        grace_period_sec: float = Reconciler.DEFAULT_GRACE_PERIOD_SEC,
    ):
        self._storage = storage or get_storage()
        self._reconciler = Reconciler(grace_period_sec=grace_period_sec)
        self._interval_sec = interval_sec
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_report: Optional[ReconcileReport] = None
        self._drift_handlers: List[Callable[[OrderDrift], Awaitable[None]]] = []
        self._local_orders_getter: Optional[Callable[[], Awaitable[List[Dict[str, Any]]]]] = None
        self._exchange_orders_getter: Optional[Callable[[], Awaitable[List[Dict[str, Any]]]]] = None
        self._external_order_ids_getter: Optional[Callable[[], set[str]]] = None

    async def trigger_reconcile(
        self,
        local_orders_getter: Callable[[], Awaitable[List[Dict[str, Any]]]],
        exchange_orders_getter: Callable[[], Awaitable[List[Dict[str, Any]]]],
        external_order_ids: Optional[set[str]] = None,
    ) -> ReconcileReport:
        local_raw = await local_orders_getter()
        exchange_raw = await exchange_orders_getter()

        local_orders = [
            LocalOrderSnapshot(
                cl_ord_id=o["client_order_id"],
                status=o["status"],
                symbol=o.get("symbol", ""),
                quantity=str(o.get("quantity", "0")),
                filled_quantity=str(o.get("filled_quantity", "0")),
                created_at=o.get("created_at", datetime.now(timezone.utc)),
                updated_at=o.get("updated_at", datetime.now(timezone.utc)),
            )
            for o in local_raw
        ]

        exchange_orders = [
            ExchangeOrderSnapshot(
                cl_ord_id=o["client_order_id"],
                status=o["status"],
                symbol=o.get("symbol", ""),
                quantity=str(o.get("quantity", "0")),
                filled_quantity=str(o.get("filled_quantity", "0")),
                updated_at=o.get("updated_at", datetime.now(timezone.utc)),
            )
            for o in exchange_raw
        ]

        report = self._reconciler.reconcile(local_orders, exchange_orders, external_order_ids)
        self._last_report = report

        # Task 9.11: Broadcast SSE update for real-time frontend updates
        try:
            from trader.api.routes.sse import broadcast_reconciliation_update
            asyncio.create_task(broadcast_reconciliation_update({
                "type": "reconciliation_complete",
                "ghost_count": report.ghost_count,
                "phantom_count": report.phantom_count,
                "diverged_count": report.diverged_count,
                "drift_count": len(report.drifts),
            }))
        except Exception:
            pass  # SSE broadcast is non-critical

        for drift in report.drifts:
            # PHANTOM orders (exists on exchange but not locally) have no created_at timestamp
            # to calculate grace period, so they are always handled immediately.
            # GHOST and DIVERGED orders respect grace period to avoid false alarms
            # during normal order propagation delays.
            # Skip EXTERNAL orders - they don't trigger PHANTOM noise
            if drift.drift_type == DriftType.PHANTOM:
                # Check if this is an EXTERNAL order - skip noise for external orders
                if external_order_ids is not None and drift.cl_ord_id in external_order_ids:
                    logger.debug(
                        f"[Reconciler] skipping PHANTOM handler for external order: {drift.cl_ord_id}"
                    )
                    continue
            if drift.grace_period_remaining_sec is None or drift.grace_period_remaining_sec <= 0:
                await self._handle_drift(drift)

        return report

    async def _handle_drift(self, drift: OrderDrift) -> None:
        logger.warning(
            f"[Reconciler] 检测到订单漂移: {drift.cl_ord_id} - {drift.drift_type.value} "
            f"(本地: {drift.local_status} vs 交易所: {drift.exchange_status})"
        )

        for handler in self._drift_handlers:
            try:
                await handler(drift)
            except Exception as e:
                logger.error(f"[Reconciler] 漂移处理器执行失败: {e}")

        await self._publish_drift_event(drift)

    async def _publish_drift_event(self, drift: OrderDrift) -> None:
        event = {
            "stream_key": "order_drifts",
            "event_type": "ORDER_DRIFT_DETECTED",
            "aggregate_type": "Reconciler",
            "aggregate_id": drift.cl_ord_id,
            "data": {
                "cl_ord_id": drift.cl_ord_id,
                "drift_type": drift.drift_type.value,
                "local_status": drift.local_status,
                "exchange_status": drift.exchange_status,
                "symbol": drift.symbol,
                "quantity": drift.quantity,
                "filled_quantity": drift.filled_quantity,
                "exchange_filled_quantity": drift.exchange_filled_quantity,
                "detected_at": drift.detected_at.isoformat(),
            },
        }
        self._storage.append_event(event)

    def register_drift_handler(self, handler: Callable[[OrderDrift], Awaitable[None]]) -> None:
        if not callable(handler):
            raise TypeError(f"handler must be callable, got {type(handler).__name__}")
        self._drift_handlers.append(handler)

    def get_last_report(self) -> Optional[ReconcileReport]:
        return self._last_report

    def set_last_report(self, report: ReconcileReport) -> None:
        self._last_report = report

    async def start(self) -> None:
        if self._running:
            logger.warning("[Reconciler] 服务已在运行中")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"[Reconciler] 服务已启动，间隔 {self._interval_sec}s")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[Reconciler] 服务已停止")

    def configure_periodic_reconciliation(
        self,
        local_orders_getter: Callable[[], Awaitable[List[Dict[str, Any]]]],
        exchange_orders_getter: Callable[[], Awaitable[List[Dict[str, Any]]]],
        external_order_ids_getter: Optional[Callable[[], set[str]]] = None,
    ) -> None:
        """Configure the callback functions for periodic reconciliation."""
        self._local_orders_getter = local_orders_getter
        self._exchange_orders_getter = exchange_orders_getter
        self._external_order_ids_getter = external_order_ids_getter

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._interval_sec)
                if self._local_orders_getter is None or self._exchange_orders_getter is None:
                    logger.warning("[Reconciler] 周期性对账未配置local_orders_getter和exchange_orders_getter，跳过本次执行")
                    continue
                
                external_ids = self._external_order_ids_getter() if self._external_order_ids_getter else None
                await self.trigger_reconcile(self._local_orders_getter, self._exchange_orders_getter, external_ids)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Reconciler] 周期性对账执行失败: {e}")
